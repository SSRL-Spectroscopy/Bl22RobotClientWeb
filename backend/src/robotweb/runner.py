"""Sends operator commands to the control server and follows them to the end.

One command runs at a time. For each command the runner:
  1. checks the preconditions against the equipment state,
  2. marks the affected items as "changing" (saved to disk before sending),
  3. sends the start command on the command connection,
  4. polls "<SUBSYSTEM> GET_STATUS" until the subsystem is idle again,
  5. records the new equipment state on success, or "unknown" on any doubt.

The control server is not polled: it is only asked when a command or a
read-out needs it. What those requests show on the way (the state of a
subsystem, and when the server last answered) is published for the page.
Activity the backend did not start (for example the older C# client driving
the robot) is therefore not noticed.
"""

import asyncio
import itertools
import logging
import time
from collections.abc import Iterable

from .audit import AuditLog
from .commands import COMMANDS, QUERIES, Command, CommandRefused, slot_is_occupied
from .control.client import ControlClient, ControlConnectionError
from .control.protocol import ProtocolError, Response, SubsystemStatus, parse_status
from .equipment import EquipmentState
from .motor_rules import MotorRules
from .state import StateStore

log = logging.getLogger(__name__)

COMMAND_KEY = "command"
CONTROL_KEY = "control"


def subsystem_key(name: str) -> str:
    return f"subsystem.{name}"


class CommandFailed(Exception):
    """A command or request made by a sequence did not succeed."""


class CommandRunner:
    def __init__(
        self,
        client: ControlClient,
        store: StateStore,
        equipment: EquipmentState,
        audit: AuditLog,
        *,
        motion_enabled: bool,
        rules: MotorRules | None = None,
        settle_s: float = 0.1,
        poll_s: float = 0.5,
        max_poll_failures: int = 10,
    ) -> None:
        self._client = client
        self._store = store
        self._equipment = equipment
        self._audit = audit
        self.motion_enabled = motion_enabled
        self._rules = rules
        self._settle_s = settle_s
        self._poll_s = poll_s
        self._max_poll_failures = max_poll_failures

        self._ids = itertools.count(1)
        self._active: dict | None = None
        self._task: asyncio.Task | None = None
        # Set by the sequence engine while a sequence is running and not
        # paused; commands sent by hand are refused meanwhile.
        self.sequence_running = False
        self._store.publish(COMMAND_KEY, {"phase": "none", "ts": time.time()})
        # connected: None until the control server has been asked something.
        self._last_answer: float | None = None
        self._store.publish(
            CONTROL_KEY, {"connected": None, "detail": "not asked yet", "last_answer": None, "ts": time.time()}
        )

    @property
    def busy(self) -> bool:
        return self._active is not None

    # ---- what the requests show ------------------------------------------------

    async def _request(self, text: str, *, timeout_s: float | None = None) -> Response:
        """Every request to the control server goes through here, so the page
        can show when it last answered."""
        try:
            response = await self._client.request(text, timeout_s=timeout_s)
        except ControlConnectionError as exc:
            self._store.publish(
                CONTROL_KEY,
                {"connected": False, "detail": str(exc), "last_answer": self._last_answer, "ts": time.time()},
            )
            raise
        self._last_answer = time.time()
        self._store.publish(
            CONTROL_KEY, {"connected": True, "detail": None, "last_answer": self._last_answer, "ts": self._last_answer}
        )
        return response

    async def _read_status(self, subsystem: str) -> SubsystemStatus:
        """Ask a subsystem for its status and publish it: `ts` is when it was read."""
        response = await self._request(f"{subsystem} GET_STATUS")
        try:
            status = parse_status(response)
        except ProtocolError as exc:
            # The server answered, but not with a usable status.
            self._store.publish(
                subsystem_key(subsystem), {"state": "UNKNOWN", "error": str(exc), "failed": True, "ts": time.time()}
            )
            raise
        self._store.publish(
            subsystem_key(subsystem),
            {"state": status.state, "error": status.error, "failed": status.failed, "ts": time.time()},
        )
        return status

    # ---- commands -----------------------------------------------------------

    async def run(self, name: str, raw_args: dict, *, user: str) -> list[str]:
        """Start a command for a sequence and wait until it has finished.
        Returns the data lines the control server sent when it accepted the
        command (a scan answers with the name of its data).

        Raises CommandRefused if it could not be started and CommandFailed if
        it started but did not succeed.
        """
        await self.start(name, raw_args, user=user, by_sequence=True)
        record, task = self._active, self._task
        assert record is not None and task is not None
        await asyncio.shield(task)
        if record["phase"] != "succeeded":
            raise CommandFailed(f"{record['label']} failed: {record['detail']}")
        return record["answer"]

    async def ask(self, text: str, *, timeout_s: float | None = None) -> list[str]:
        """Send a read-only request for a sequence and return its data lines.
        `timeout_s`: wait longer than usual for the answer."""
        response = await self._request(text, timeout_s=timeout_s)
        if not response.ok:
            raise CommandFailed(f"'{text}' was answered with: {response.error}")
        return list(response.lines)

    async def read_positions(self, names: Iterable[str]) -> dict[str, float]:
        """Read motor positions (MOTOR GET_POSITION, engineering units) and,
        if any JOINT_n is asked for, the robot joints (ROBOT GET_JOINTS)."""
        names = set(names)
        positions: dict[str, float] = {}
        try:
            for name in sorted(n for n in names if not n.startswith("JOINT_")):
                positions[name] = float((await self.ask(f"MOTOR GET_POSITION {name}"))[0])
            if any(n.startswith("JOINT_") for n in names):
                values = (await self.ask("ROBOT GET_JOINTS"))[0].split()
                if len(values) != 6:
                    raise ValueError(f"expected 6 joint positions, got {len(values)}")
                for index, value in enumerate(values, start=1):
                    positions[f"JOINT_{index}"] = float(value)
        except (ValueError, IndexError) as exc:
            raise CommandFailed(f"A position answer from the control server could not be read: {exc}") from exc
        return positions

    async def start(self, name: str, raw_args: dict, *, user: str, by_sequence: bool = False) -> None:
        """Validate and start a command. Returns once the server accepted it.

        Raises CommandRefused if it is not allowed or the server refuses it,
        and ControlConnectionError if the server cannot be reached.
        """
        spec = COMMANDS.get(name)
        if spec is None:
            raise CommandRefused(f"unknown command {name!r}")
        if not spec.manual and not by_sequence:
            raise CommandRefused(f"{spec.label} can only be sent by a sequence.")
        if self.sequence_running and not by_sequence:
            raise CommandRefused("A sequence is running. Pause it before sending commands by hand.")
        if not self.motion_enabled:
            raise CommandRefused("Commands that move hardware are switched off in the backend configuration.")
        args = spec.clean_args(raw_args)
        if self._active is not None:
            raise CommandRefused(f"Another command is still running: {self._active['label']}.")

        # Reserve the runner before the first await, so two requests arriving
        # together cannot both pass the check above.
        record = {
            "id": next(self._ids),
            "name": spec.name,
            "label": spec.label,
            "subsystem": spec.subsystem,
            "user": user,
            "phase": "starting",
            "text": None,
            "step": None,
            "detail": None,
            "answer": [],
            "started": time.time(),
            "finished": None,
        }
        self._active = record
        try:
            status = await self._read_status(spec.subsystem)
            if not status.idle:
                raise CommandRefused(f"{spec.subsystem} is busy ({status.state}). Wait until it is idle.")
            spec.check(self._equipment, args)
            if spec.needs_occupied_slot:
                slots = await self.query("storage_slots")
                if not slot_is_occupied(slots, args["container"], args["slot"]):
                    raise CommandRefused(
                        f"Container {args['container']}, slot {args['slot']} is empty "
                        "according to the control server."
                    )
            if spec.motor_move:
                await self._check_motor_rules(args["motor"], args["position"])
            text = spec.build(self._equipment, args)
            results = spec.result(self._equipment, args)
        except BaseException:
            self._active = None
            raise

        record["text"] = text
        previous = {item: self._equipment.begin_change(item) for item in spec.changes}
        try:
            response = await self._request(text)
        except BaseException as exc:
            # The command may or may not have reached the server.
            self._doubt(spec, f"lost contact with the control server while sending '{text}'")
            self._finish(record, "failed", f"No answer from the control server: {exc}")
            raise

        if not response.ok:
            # Refused outright: nothing started, so the old state still holds.
            for item, old in previous.items():
                self._equipment.restore(item, old)
            self._finish(record, "failed", f"Refused by the control server: {response.error}")
            raise CommandRefused(f"The control server refused the command: {response.error}")

        record["phase"] = "running"
        record["answer"] = list(response.lines)
        self._audit.write("command_started", user=user, command=spec.name, text=text)
        self._publish(record)
        self._task = asyncio.create_task(self._watch(spec, record, results), name=f"command-{spec.name}")

    async def _check_motor_rules(self, motor: str, target: float) -> None:
        """The backend's own check of the interlock rules (motor_rules.py),
        against positions read now. Raises CommandRefused if the move is not
        allowed or cannot be judged."""
        if self._rules is None:
            raise CommandRefused("The motor rules are not loaded, so no motor can be moved.")
        try:
            positions = await self.read_positions(self._rules.needed_for(motor))
        except CommandFailed as exc:
            raise CommandRefused(f"{motor} was not moved: {exc}") from exc
        problems = self._rules.check_move(motor, target, positions)
        if problems:
            raise CommandRefused(f"{motor} may not move to {target:g}: " + "; ".join(problems) + ".")

    async def _watch(self, spec: Command, record: dict, results: dict) -> None:
        """Poll the subsystem until the process ends, then record the outcome."""
        error: str | None = None
        failures = 0
        try:
            await asyncio.sleep(self._settle_s)  # let the server leave IDLE first
            while True:
                try:
                    status = await self._read_status(spec.subsystem)
                except (ControlConnectionError, ProtocolError) as exc:
                    failures += 1
                    if failures >= self._max_poll_failures:
                        self._doubt(spec, f"lost contact with the control server during '{record['text']}'")
                        self._finish(record, "failed", f"Lost contact with the control server: {exc}")
                        return
                    await asyncio.sleep(self._poll_s)
                    continue
                failures = 0
                if status.failed:
                    error = status.error
                if status.idle:
                    break
                if record["step"] != status.state:
                    record["step"] = status.state
                    self._publish(record)
                await asyncio.sleep(self._poll_s)

            if error is not None:
                self._doubt(spec, f"'{record['text']}' ended with error {error}")
                self._finish(record, "failed", f"{spec.subsystem} reported {error}")
            else:
                for item, value in results.items():
                    self._equipment.set_known(item, value)
                self._finish(record, "succeeded", None)
        except asyncio.CancelledError:
            # Backend shutting down. The items stay "changing" on disk and are
            # read back as unknown at the next start.
            raise
        except Exception as exc:  # never leave the runner stuck on a bug
            log.exception("command watcher failed")
            self._doubt(spec, f"internal error while following '{record['text']}'")
            self._finish(record, "failed", f"Internal error: {exc}")

    def _doubt(self, spec: Command, reason: str) -> None:
        for item in spec.changes:
            self._equipment.set_unknown(item, reason)

    def _finish(self, record: dict, phase: str, detail: str | None) -> None:
        record["phase"] = phase
        record["detail"] = detail
        record["step"] = None
        record["finished"] = time.time()
        self._audit.write(
            f"command_{phase}", user=record["user"], command=record["name"], text=record["text"], detail=detail
        )
        self._publish(record)
        self._active = None  # last, so "not busy" implies the outcome is published

    def _publish(self, record: dict) -> None:
        self._store.publish(COMMAND_KEY, {**record, "ts": time.time()})

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._client.close()

    # ---- queries --------------------------------------------------------------

    async def query(self, name: str) -> list[str]:
        spec = QUERIES.get(name)
        if spec is None:
            raise CommandRefused(f"unknown query {name!r}")
        response = await self._request(spec.text)
        if not response.ok:
            raise CommandRefused(f"The control server answered: {response.error}")
        lines = list(response.lines)
        if spec.publish_as:
            self._store.publish(spec.publish_as, {"lines": lines, "ts": time.time()})
        return lines
