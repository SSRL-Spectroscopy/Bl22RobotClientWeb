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
Context for everything it does, so that pause, abort, the equipment record and
the log all stay correct.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .audit import AuditLog
from .commands import CommandRefused
from .control.client import ControlConnectionError
from .control.protocol import ProtocolError
from .equipment import FILTER, FRAME, ITEMS, REFERENCE, EquipmentState
from .mqueue import MeasurementQueue
from .runner import CommandFailed, CommandRunner
from .state import StateStore

log = logging.getLogger(__name__)

SEQUENCE_KEY = "sequence"
LOG_LINES_SHOWN = 200
NO_TAG = {"0000-0000", "000-000", ""}


class SequenceError(Exception):
    """The requested sequence action is not possible right now."""


class _Aborted(Exception):
    """Raised inside a sequence at the checkpoint after an abort request."""


class Context:
    """What a sequence may do."""

    def __init__(self, engine: "SequenceEngine", user: str) -> None:
        self._engine = engine
        self.user = user
        self.queue = engine.queue
        self.equipment = engine.equipment

    def log(self, text: str, level: str = "info") -> None:
        self._engine._log(text, level)

    async def checkpoint(self) -> None:
        """Honour a pause or abort request. Called before every command."""
        await self._engine._checkpoint()

    async def command(self, name: str, args: dict | None = None, *, label: str) -> None:
        """Run one of the commands in commands.py and wait for it to finish.
        The command's own preconditions and equipment updates apply."""
        await self.checkpoint()
        self._engine._set_step(label)
        self.log(label)
        await self._engine.runner.run(name, args or {}, user=self.user)

    async def ask(self, text: str, *, label: str) -> list[str]:
        """Send a read-only request and return its data lines."""
        await self.checkpoint()
        self._engine._set_step(label)
        return await self._engine.runner.ask(text)


@dataclass(frozen=True)
class Sequence:
    name: str
    label: str
    description: str
    plan: Callable[["SequenceEngine"], list[str]]
    run: Callable[[Context], Awaitable[None]]


# ---- sequence: verify the queued frames ---------------------------------------


def _plan_verify(engine: "SequenceEngine") -> list[str]:
    _need_empty_robot(engine.equipment)
    if not engine.queue.items:
        raise SequenceError("The queue is empty.")
    steps = []
    for item in engine.queue.items:
        where = f"container {item['container']}, slot {item['slot']}"
        steps += [
            f"Get the frame from {where}",
            f"Read its barcode and compare it with {item['barcode']}",
            f"Return the frame to {where}",
        ]
    return steps


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
        await ctx.command("get_frame", {"container": container, "slot": slot}, label=f"Get the frame from {where}")
        lines = await ctx.ask("SCHEDULER GET_BARCODE", label="Read the barcode")
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


def _cleanup_steps(equipment: EquipmentState) -> list[tuple[str, str]]:
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
        steps.append(("return_frame", f"Return the frame to container {frame['container']}, slot {frame['slot']}"))
    if equipment.value(REFERENCE) is not None:
        steps.append(("return_reference", f"Return the reference foil {equipment.value(REFERENCE)}"))
    if equipment.value(FILTER) is not None:
        steps.append(("return_filter", f"Return the FY filter {equipment.value(FILTER)}"))
    return steps


def _plan_cleanup(engine: "SequenceEngine") -> list[str]:
    steps = [label for _, label in _cleanup_steps(engine.equipment)]
    if not steps:
        raise SequenceError("Nothing to clean up: no frame, reference or filter is loaded.")
    return steps


async def _run_cleanup(ctx: Context) -> None:
    for command, label in _cleanup_steps(ctx.equipment):
        await ctx.command(command, label=label)


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
            _plan_verify,
            _run_verify,
        ),
        Sequence(
            "cleanup",
            "Clean up",
            "Returns the frame, the reference foil and the FY filter, as far as they are loaded.",
            _plan_cleanup,
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
    ) -> None:
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
        self._publish()

    @property
    def active(self) -> bool:
        return self._status["state"] in ("running", "paused")

    # ---- operator actions -------------------------------------------------

    def plan(self, name: str) -> list[str]:
        """The steps a sequence would take now. Raises SequenceError if it
        cannot be started in the current situation."""
        return self._sequence(name).plan(self)

    def start(self, name: str, *, user: str) -> None:
        sequence = self._sequence(name)
        if self.active:
            raise SequenceError(f"A sequence is already running: {self._status['label']}.")
        if self.runner.busy:
            raise SequenceError("Wait until the running command has finished.")
        if not self.runner.motion_enabled:
            raise SequenceError("Commands that move hardware are switched off in the backend configuration.")
        steps = sequence.plan(self)

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
        }
        self.runner.sequence_running = True
        self._audit.write("sequence_started", user=user, sequence=sequence.name)
        self._log(f"{sequence.label}: started by {user}, {len(steps)} steps planned")
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
        except (CommandRefused, CommandFailed, ControlConnectionError, ProtocolError) as exc:
            self._end("failed", f"{exc} Everything was left where it is.")
        except asyncio.CancelledError:
            self._end("failed", "The backend was stopped during the sequence.")
            raise
        except Exception as exc:  # a bug in a sequence must not kill the backend
            log.exception("sequence crashed")
            self._end("failed", f"Internal error: {type(exc).__name__}: {exc}. Everything was left where it is.")
        else:
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

    def _end(self, state: str, detail: str | None) -> None:
        self.runner.sequence_running = False
        self._status.update(state=state, detail=detail, step=None, finished=time.time())
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

    @staticmethod
    def _sequence(name: str) -> Sequence:
        sequence = SEQUENCES.get(name)
        if sequence is None:
            raise SequenceError(f"unknown sequence {name!r}")
        return sequence
