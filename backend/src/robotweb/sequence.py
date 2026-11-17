"""Runs sequences: series of commands that work through the queue.

A sequence runs in the backend, so it continues when browsers close or the
helm changes hands. Rules (see docs/ARCHITECTURE.md):

- One sequence at a time. Commands sent by hand are refused while it runs and
  allowed while it is paused.
- Pause and abort take effect between two commands. A command that is already
  running always finishes.
- After an abort or a failure everything stays where it is. Nothing is
  cleaned up automatically; the operator starts the cleanup sequence.
- Every step goes to the run log: shown on the page and written to a file.
- If the backend stops during a sequence, the sequence is lost.

A sequence is a coroutine that takes a Context. It must go through the
Context for everything it does, so that pause, abort, the equipment record,
the log and the step tree all stay correct.

The step tree (outline.py). A sequence groups what it does with
`async with ctx.section(key, title)`; every command and request inside is a
line. Before the start the sequence is run once "dry": the Context then sends
nothing and hands out stand-ins for the queue, the equipment record and the
portal, so the same code that will run yields the plan. A sequence must
therefore reach the outside world only through its Context, and must give
requests whose answer decides what happens next a `dry=` answer.
"""

import asyncio
import copy
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import measure
from .audit import AuditLog
from .commands import COMMANDS, CommandRefused
from .control.client import ControlConnectionError
from .control.protocol import ProtocolError
from .equipment import FILTER, FRAME, ITEMS, REFERENCE, EquipmentState
from .mqueue import MeasurementQueue
from .outline import Outline, flat, public
from .portal import PortalError
from .runner import CommandFailed, CommandRunner
from .science import ScienceError
from .state import StateStore

log = logging.getLogger(__name__)

SEQUENCE_KEY = "sequence"
OUTLINE_KEY = "sequence_outline"
LOG_LINES_SHOWN = 200
NO_TAG = {"0000-0000", "000-000", ""}


class SequenceError(Exception):
    """The requested sequence action is not possible right now."""


class _Aborted(Exception):
    """Raised inside a sequence at the checkpoint after an abort request."""


# ---- stand-ins for a dry run ------------------------------------------------------


class _DryQueue:
    """A private copy of the queue: marks change the copy only."""

    def __init__(self, items: list[dict]) -> None:
        self.items = copy.deepcopy(items)

    def mark(self, barcode: str, state: str, note: str | None = None) -> None:
        for item in self.items:
            if item["barcode"] == barcode:
                item["state"] = state

    def mark_measurement(self, barcode: str, measurement_id: str, state: str) -> None:
        for item in self.items:
            if item["barcode"] == barcode:
                for measurement in item["measurements"]:
                    if measurement["id"] == measurement_id:
                        measurement["state"] = state


class _DryEquipment:
    """What would be loaded as the planned commands succeed one by one."""

    def __init__(self, real: EquipmentState) -> None:
        self._known = {item: real.is_known(item) for item in ITEMS}
        self._values = {item: real.value(item) if real.is_known(item) else None for item in ITEMS}

    def is_known(self, item: str) -> bool:
        return self._known[item]

    def value(self, item: str) -> Any:
        return self._values[item]

    def set_known(self, item: str, value: Any) -> None:
        self._known[item], self._values[item] = True, value


class _DryPortal:
    """Assumes every frame is in internal storage, as the queue requires."""

    def __init__(self) -> None:
        self._states: dict[str, str] = {}

    async def frame(self, barcode: str) -> dict:
        return {"barcode": barcode, "state": self._states.get(barcode, "internal_storage")}

    async def set_frame_state(self, barcode: str, state: str) -> dict:
        self._states[barcode] = state
        return {"barcode": barcode, "state": state}

    async def set_measurement_status(self, measurement_id: str, status: str) -> dict:
        return {}


class _Scope:
    """`async with` around a section or a line of the step tree."""

    def __init__(self, enter: Callable[[], Any], leave: Callable[[Any, str], None]) -> None:
        self._enter, self._leave = enter, leave
        self._token: Any = None

    async def __aenter__(self) -> None:
        self._token = self._enter()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            state = "done"
        elif issubclass(exc_type, (_Aborted, asyncio.CancelledError)):
            state = "stopped"
        else:
            state = "failed"
        self._leave(self._token, state)


class Context:
    """What a sequence may do."""

    def __init__(self, engine: "SequenceEngine", user: str, *, dry: bool = False) -> None:
        self._engine = engine
        self.user = user
        # True while the plan is worked out: nothing is sent or changed.
        self.dry = dry
        if dry:
            self.queue: Any = _DryQueue(engine.queue.items)
            self.equipment: Any = _DryEquipment(engine.equipment)
            self.portal: Any = _DryPortal() if engine.portal is not None else None
            self._outline = Outline()
        else:
            self.queue = engine.queue
            self.equipment = engine.equipment
            self.portal = engine.portal
            self._outline = engine._outline
        self.science = engine.science
        self.crystal = engine.crystal
        self.simulation = engine.simulation
        # Set by a sequence that needs X-rays: every checkpoint then also
        # waits while the beam is lost (beam.py).
        self.needs_beam = False

    @property
    def beam_losses(self) -> int:
        """How often the beam has been lost so far (0 without a beam watch)."""
        if self.dry or self._engine.beam is None:
            return 0
        return self._engine.beam.losses

    async def notify(self, subject: str, text: str) -> None:
        """E-mail the operators. Never raises: a message that cannot be sent
        is written to the run log."""
        if not self.dry:
            await self._engine._notify(subject, text)

    def log(self, text: str, level: str = "info") -> None:
        if not self.dry:
            self._engine._log(text, level)

    async def checkpoint(self) -> None:
        """Honour a pause or abort request, and wait for beam if the
        sequence needs it. Called before every command."""
        if self.dry:
            return
        await self._engine._checkpoint()
        if self.needs_beam:
            await self._engine._wait_for_beam()

    def section(self, key: str, title: str) -> _Scope:
        """`async with ctx.section(key, title):` groups the steps inside it
        under a heading in the step tree. `key` tells sections of one parent
        apart; `title` is what the operator reads."""
        outline = self._outline
        return _Scope(lambda: outline.enter(key, title), lambda _, state: outline.leave(state))

    def step(self, title: str) -> _Scope:
        """`async with ctx.step(title):` shows what happens inside as one line
        of the step tree. Commands and requests are lines by themselves; this
        is for the rest, such as a report to the portal."""
        outline = self._outline
        return _Scope(lambda: outline.begin(title), outline.end)

    async def command(
        self, name: str, args: dict | None = None, *, label: str, dry: list[str] | None = None
    ) -> list[str]:
        """Run one of the commands in commands.py and wait for it to finish.
        The command's own preconditions and equipment updates apply. Returns
        the data lines of the control server's first answer; in a dry run,
        `dry`."""
        await self.checkpoint()
        async with self.step(label):
            if self.dry:
                spec = COMMANDS[name]
                cleaned = spec.clean_args(args or {})
                spec.check(self.equipment, cleaned)
                for item, value in spec.result(self.equipment, cleaned).items():
                    self.equipment.set_known(item, value)
                return list(dry or [])
            self._engine._set_step(label)
            self.log(label)
            return await self._engine.runner.run(name, args or {}, user=self.user)

    async def read_positions(self, names: list[str], *, dry: dict[str, float] | None = None) -> dict[str, float]:
        if self.dry:
            return dict(dry or {})
        return await self._engine.runner.read_positions(names)

    async def ask(self, text: str, *, label: str, dry: list[str] | None = None, line: bool = True) -> list[str]:
        """Send a read-only request and return its data lines; in a dry run,
        `dry`. With line=False it gets no line of its own in the step tree."""
        await self.checkpoint()
        if not line:
            if self.dry:
                return list(dry or [])
            self._engine._set_step(label)
            return await self._engine.runner.ask(text)
        async with self.step(label):
            if self.dry:
                return list(dry or [])
            self._engine._set_step(label)
            return await self._engine.runner.ask(text)


@dataclass(frozen=True)
class Sequence:
    name: str
    label: str
    description: str
    # Raises SequenceError if the sequence cannot start in the current situation.
    check: Callable[["SequenceEngine"], None]
    run: Callable[[Context], Awaitable[None]]


# ---- sequence: verify the queued frames ---------------------------------------


def _check_verify(engine: "SequenceEngine") -> None:
    _need_empty_robot(engine.equipment)
    if not engine.queue.items:
        raise SequenceError("The queue is empty.")


async def _run_verify(ctx: Context) -> None:
    mismatches = 0
    for item in ctx.queue.items:
        ctx.queue.mark(item["barcode"], "queued")
    while True:
        # Pick the next frame only after the checkpoint: while the sequence is
        # paused, the operator may remove or reorder frames not yet started.
        await ctx.checkpoint()
        item = next((i for i in ctx.queue.items if i["state"] == "queued"), None)
        if item is None:
            break
        barcode, container, slot = item["barcode"], item["container"], item["slot"]
        where = f"container {container}, slot {slot}"
        async with ctx.section(f"frame:{barcode}", f"Frame {barcode} in {where}"):
            await ctx.command(
                "get_frame", {"container": container, "slot": slot}, label=f"Get the frame from {where}"
            )
            lines = await ctx.ask(
                "SCHEDULER GET_BARCODE", label=f"Read its barcode and compare it with {barcode}", dry=[barcode]
            )
            found = lines[0].strip().upper() if lines else ""
            if found in NO_TAG:
                mismatches += 1
                ctx.log(f"No barcode could be read on the frame from {where} (expected {barcode})", "error")
                ctx.queue.mark(barcode, "mismatch", "no barcode could be read")
            elif found != barcode:
                mismatches += 1
                ctx.log(f"The frame from {where} carries {found}, but the portal lists {barcode} there", "error")
                ctx.queue.mark(barcode, "mismatch", f"the frame in this slot carries {found}")
            else:
                ctx.log(f"Barcode {found} matches the portal")
                ctx.queue.mark(barcode, "verified")
            await ctx.command("return_frame", label=f"Return the frame to {where}")
    if mismatches:
        ctx.log(f"{mismatches} frame(s) did not match the portal. Remove them from the queue.", "error")


# ---- sequence: cleanup ----------------------------------------------------------


def _cleanup_steps(equipment) -> list[tuple[str, str]]:
    """(command, label) pairs that bring the robot back to holding nothing,
    with no reference and no filter loaded. The frame goes first: while the
    robot holds it, it cannot move anything else."""
    unknown = [item for item in ITEMS if not equipment.is_known(item)]
    if unknown:
        raise SequenceError(
            "Cleanup needs to know what is loaded. Declare the state of: " + ", ".join(unknown) + "."
        )
    steps = []
    frame = equipment.value(FRAME)
    if frame is not None:
        steps.append(("park_detector", "Retract and park the SDD detector, if it is not parked"))
        steps.append(("return_frame", f"Return the frame to container {frame['container']}, slot {frame['slot']}"))
    if equipment.value(REFERENCE) is not None:
        steps.append(("return_reference", f"Return the reference foil {equipment.value(REFERENCE)}"))
    if equipment.value(FILTER) is not None:
        steps.append(("return_filter", f"Return the FY filter {equipment.value(FILTER)}"))
    return steps


def _check_cleanup(engine: "SequenceEngine") -> None:
    if not _cleanup_steps(engine.equipment):
        raise SequenceError("Nothing to clean up: no frame, reference or filter is loaded.")


async def _run_cleanup(ctx: Context) -> None:
    for command, label in _cleanup_steps(ctx.equipment):
        if command == "park_detector":
            async with ctx.section("park", label):
                await _park_detector(ctx)
            continue
        await ctx.command(command, label=label)
        if command == "return_frame":
            await _report_frames_returned(ctx)


async def _park_detector(ctx: Context) -> None:
    """After a run that stopped during an SDD measurement the detector is
    still in front of the sample. It can only move while the robot holds the
    frame in the beam (motor rules), so it is parked before the frame goes."""
    # The plan assumes it is parked; the moves show up when they are needed.
    positions = await ctx.read_positions(["DET_X", "DET_Y"], dry={"DET_X": 0.0, "DET_Y": 130.0})
    if positions["DET_Y"] < 128.0:
        await ctx.command(
            "motor_move", {"motor": "DET_Y", "position": 130.0}, label="Retract the detector: move DET_Y to 130"
        )
    if positions["DET_X"] > 10.0:
        await ctx.command(
            "motor_move", {"motor": "DET_X", "position": 0.0}, label="Park the detector: move DET_X to 0"
        )


async def _report_frames_returned(ctx: Context) -> None:
    """A frame a stopped run left "loaded" is back in its slot now: tell the
    portal. A portal problem is logged and does not stop the cleanup."""
    if ctx.portal is None:
        return
    for item in ctx.queue.items:
        if item["state"] != "loaded":
            continue
        barcode = item["barcode"]
        try:
            if (await ctx.portal.frame(barcode)).get("state") == "loaded":
                await ctx.portal.set_frame_state(barcode, "internal_storage")
                ctx.log(f"Portal: frame {barcode} is back in storage")
        except PortalError as exc:
            ctx.log(f"Portal: frame {barcode} could not be reported as returned: {exc}", "error")
            continue
        ctx.queue.mark(barcode, "queued")


def _check_measure(engine: "SequenceEngine") -> None:
    _need_empty_robot(engine.equipment)
    try:
        measure.check(engine)
    except measure.MeasureError as exc:
        raise SequenceError(str(exc)) from exc


def _need_empty_robot(equipment: EquipmentState) -> None:
    if not equipment.is_known(FRAME):
        raise SequenceError("The backend does not know whether the robot holds a frame. Declare it first.")
    if equipment.value(FRAME) is not None:
        raise SequenceError("The robot is holding a frame. Return it first (the cleanup sequence does that).")


SEQUENCES: dict[str, Sequence] = {
    s.name: s
    for s in (
        Sequence(
            "verify_frames",
            "Verify the queued frames",
            "Fetches each queued frame, reads its barcode, compares it with the portal and returns the frame.",
            _check_verify,
            _run_verify,
        ),
        Sequence(
            "measure",
            "Measure the queue",
            "Runs every queued measurement, frame by frame, and reports progress to the portal.",
            _check_measure,
            measure.run,
        ),
        Sequence(
            "cleanup",
            "Clean up",
            "Returns the frame, the reference foil and the FY filter, as far as they are loaded.",
            _check_cleanup,
            _run_cleanup,
        ),
    )
}


# ---- the engine -------------------------------------------------------------------


class SequenceEngine:
    def __init__(
        self,
        runner: CommandRunner,
        equipment: EquipmentState,
        queue: MeasurementQueue,
        store: StateStore,
        audit: AuditLog,
        log_dir: Path,
        *,
        portal=None,
        science=None,
        crystal=None,
        simulation: bool = False,
        beam=None,
        notifier=None,
        beam_poll_s: float = 1.0,
    ) -> None:
        self.beam = beam
        self.notifier = notifier
        self._beam_poll_s = beam_poll_s
        self.portal = portal
        self.science = science
        self.crystal = crystal
        self.simulation = simulation
        self.runner = runner
        self.equipment = equipment
        self.queue = queue
        self._store = store
        self._audit = audit
        self._log_dir = log_dir

        self._task: asyncio.Task | None = None
        self._resume = asyncio.Event()
        self._pause_requested = False
        self._abort_requested = False
        self._log_file: Path | None = None
        self._lines: list[dict] = []
        self._status: dict = {"state": "idle"}
        self._outline = Outline(self._publish_outline)
        self._publish()
        self._publish_outline()

    @property
    def active(self) -> bool:
        return self._status["state"] in ("running", "paused")

    # ---- operator actions -------------------------------------------------

    def outline(self, name: str) -> list[dict]:
        """The step tree a sequence would go through if started now. Raises
        SequenceError if it cannot be started in the current situation."""
        return public(self._planned(self._sequence(name)))

    def plan(self, name: str) -> list[str]:
        """The same as text: one title per line, indented by level."""
        return flat(self._planned(self._sequence(name)))

    def _planned(self, sequence: Sequence) -> list[dict]:
        sequence.check(self)
        context = Context(self, "", dry=True)
        run = sequence.run(context)
        try:
            # Nothing in a dry run waits for anything, so one push runs it to the end.
            run.send(None)
        except StopIteration:
            return context._outline.planned()
        except (CommandRefused, CommandFailed, ScienceError, measure.MeasureError) as exc:
            raise SequenceError(str(exc)) from exc
        run.close()
        raise SequenceError("The steps of this sequence could not be worked out in advance.")

    def start(self, name: str, *, user: str) -> None:
        sequence = self._sequence(name)
        if self.active:
            raise SequenceError(f"A sequence is already running: {self._status['label']}.")
        if self.runner.busy:
            raise SequenceError("Wait until the running command has finished.")
        if not self.runner.motion_enabled:
            raise SequenceError("Commands that move hardware are switched off in the backend configuration.")
        planned = self._planned(sequence)

        self._pause_requested = self._abort_requested = False
        self._resume.clear()
        self._lines = []
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self._log_dir / f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{sequence.name}.log"
        self._status = {
            "state": "running",
            "name": sequence.name,
            "label": sequence.label,
            "user": user,
            "started": time.time(),
            "finished": None,
            "step": None,
            "detail": None,
            "waiting": None,
        }
        self._outline = Outline(self._publish_outline)
        self._outline.load(planned)
        self.runner.sequence_running = True
        self._audit.write("sequence_started", user=user, sequence=sequence.name)
        self._log(f"{sequence.label}: started by {user}, {len(flat(planned))} steps planned")
        self._task = asyncio.create_task(self._run(sequence, user), name=f"sequence-{sequence.name}")

    def pause(self, *, user: str) -> None:
        if self._status["state"] != "running":
            raise SequenceError("No sequence is running.")
        if not self._pause_requested:
            self._pause_requested = True
            self._audit.write("sequence_pause_requested", user=user)
            self._log(f"Pause requested by {user}; it takes effect when the current command has finished")

    def resume(self, *, user: str) -> None:
        if self._status["state"] != "paused":
            raise SequenceError("The sequence is not paused.")
        if self.runner.busy:
            raise SequenceError("Wait until the running command has finished.")
        self._pause_requested = False
        self._audit.write("sequence_resumed", user=user)
        self._log(f"Resumed by {user}")
        self._resume.set()

    def abort(self, *, user: str) -> None:
        if not self.active:
            raise SequenceError("No sequence is running.")
        if not self._abort_requested:
            self._abort_requested = True
            self._audit.write("sequence_abort_requested", user=user)
            self._log(f"Abort requested by {user}; it takes effect when the current command has finished")
            self._publish()
        self._resume.set()  # a paused sequence wakes up and stops

    async def stop(self) -> None:
        """Backend shutdown."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ---- running ----------------------------------------------------------

    async def _run(self, sequence: Sequence, user: str) -> None:
        try:
            await sequence.run(Context(self, user))
        except _Aborted:
            self._end("aborted", "Aborted. Everything was left where it is.")
        except (
            CommandRefused, CommandFailed, ControlConnectionError, ProtocolError,
            PortalError, ScienceError, measure.MeasureError,
        ) as exc:
            self._end("failed", f"{exc} Everything was left where it is.")
        except asyncio.CancelledError:
            self._end("failed", "The backend was stopped during the sequence.")
            raise
        except Exception as exc:  # a bug in a sequence must not kill the backend
            log.exception("sequence crashed")
            self._end("failed", f"Internal error: {type(exc).__name__}: {exc}. Everything was left where it is.")
        else:
            self._outline.finish()
            self._end("finished", None)

    async def _checkpoint(self) -> None:
        if self._abort_requested:
            raise _Aborted()
        if not self._pause_requested:
            return
        self._status["state"] = "paused"
        self.runner.sequence_running = False
        self._log("Paused. Commands can be sent by hand.")
        self._resume.clear()
        await self._resume.wait()
        if self._abort_requested:
            raise _Aborted()
        self._status["state"] = "running"
        self.runner.sequence_running = True
        self._publish()

    async def _wait_for_beam(self) -> None:
        """Hold the sequence while the beam is lost. Abort and pause still
        work; while paused by the operator, commands can be sent by hand."""
        watch = self.beam
        if watch is None or not watch.held:
            return
        label = self._status["label"]
        self._status["waiting"] = watch.describe()
        self._log(watch.describe(), "error")
        await self._notify("BL22HT_Run_waiting_for_beam", f"The run '{label}' is on hold. {watch.describe()}")
        try:
            while watch.held:
                await self._checkpoint()
                if self._status["waiting"] != watch.describe():
                    self._status["waiting"] = watch.describe()
                    self._publish()
                await asyncio.sleep(self._beam_poll_s)
        finally:
            self._status["waiting"] = None
        self._log("The beam is back: the run goes on.")
        await self._notify("BL22HT_Run_resumed", f"The beam is back. The run '{label}' goes on.")

    async def _notify(self, subject: str, text: str) -> None:
        if self.notifier is None or not self.notifier.addresses:
            self._log("No e-mail sent: no operator address is configured (ROBOTWEB_OPERATOR_EMAILS).")
            return
        try:
            await self.notifier.send(subject, text)
        except (CommandFailed, ControlConnectionError, ProtocolError) as exc:
            self._log(f"The e-mail to the operators could not be sent: {exc}", "error")
        else:
            self._log(f"E-mail sent to the operators ({subject})")

    def _end(self, state: str, detail: str | None) -> None:
        self.runner.sequence_running = False
        self._status.update(state=state, detail=detail, step=None, waiting=None, finished=time.time())
        self._audit.write(
            f"sequence_{state}", user=self._status["user"], sequence=self._status["name"], detail=detail
        )
        self._log(detail or "Finished.", "info" if state == "finished" else "error")

    def _set_step(self, label: str) -> None:
        self._status["step"] = label
        self._publish()

    def _log(self, text: str, level: str = "info") -> None:
        entry = {"ts": time.time(), "level": level, "text": text}
        self._lines.append(entry)
        if self._log_file is not None:
            stamp = datetime.fromtimestamp(entry["ts"]).strftime("%Y-%m-%d %H:%M:%S")
            try:
                with self._log_file.open("a", encoding="utf-8") as handle:
                    handle.write(f"{stamp} {level.upper():5} {text}\n")
            except OSError:
                log.exception("could not write the run log")
        self._publish()

    def _publish(self) -> None:
        self._store.publish(
            SEQUENCE_KEY,
            {
                **self._status,
                "pause_requested": self._pause_requested,
                "abort_requested": self._abort_requested,
                "log": self._lines[-LOG_LINES_SHOWN:],
                "ts": time.time(),
            },
        )

    def _publish_outline(self) -> None:
        # Under its own key: the tree is large and changes less often than the log.
        self._store.publish(OUTLINE_KEY, {"nodes": self._outline.public(), "ts": time.time()})

    @staticmethod
    def _sequence(name: str) -> Sequence:
        sequence = SEQUENCES.get(name)
        if sequence is None:
            raise SequenceError(f"unknown sequence {name!r}")
        return sequence
