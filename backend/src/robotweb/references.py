"""Which reference foils the instrument has: the answer to SAMPLE LIST_REF.

Read when the backend starts, whenever a frame is added to the queue, before
the measurement sequence is planned or started, and when an operator asks.
The page compares it with the foil each queued measurement needs and marks
the ones that cannot run; the measurement sequence refuses to start while one
is missing.

The foil that is loaded at the moment counts as available whether or not the
control server lists it.

ASSUMPTION (2026-11-17, not yet checked against the control server): the
answer's data lines hold the names, separated by spaces, commas or line
breaks, written as SAMPLE GET_REF takes them (for example "Fe"). Names are
compared without regard to case.
"""

import re
import time

from .commands import CommandRefused
from .control.client import ControlConnectionError
from .control.protocol import ProtocolError
from .equipment import REFERENCE, EquipmentState
from .runner import CommandFailed, CommandRunner
from .state import StateStore

REFERENCES_KEY = "references"
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,24}$")


def parse(lines: list[str]) -> list[str]:
    """The names in a LIST_REF answer, in the order given, without repeats."""
    names: list[str] = []
    for token in re.split(r"[\s,;]+", " ".join(lines)):
        if _NAME.fullmatch(token) and token.upper() not in {n.upper() for n in names}:
            names.append(token)
    return names


class References:
    def __init__(self, runner: CommandRunner, store: StateStore, equipment: EquipmentState) -> None:
        self._runner = runner
        self._store = store
        self._equipment = equipment
        self.available: list[str] | None = None  # None: not known
        self.error: str | None = "not read yet"
        self._publish()

    async def refresh(self) -> None:
        """Ask the control server. Never raises: if the list cannot be read
        it is unknown, and nothing is marked as missing."""
        control = self._store.get("control")
        if control is not None and not control.get("connected"):
            # The status poll already knows the control server is away: do not
            # make the caller wait for another connection attempt.
            self.available = None
            self.error = "the list of references could not be read: the control server is not connected"
            self._publish()
            return
        try:
            self.available = parse(await self._runner.ask("SAMPLE LIST_REF"))
            self.error = None
        except (CommandFailed, CommandRefused, ControlConnectionError, ProtocolError) as exc:
            self.available = None
            self.error = f"the list of references could not be read: {exc}"
        self._publish()

    def is_missing(self, name: str) -> bool:
        """True only if the list is known and the foil is neither in it nor loaded."""
        if self.available is None:
            return False
        loaded = self._equipment.value(REFERENCE) if self._equipment.is_known(REFERENCE) else None
        wanted = name.upper()
        if isinstance(loaded, str) and loaded.upper() == wanted:
            return False
        return wanted not in {n.upper() for n in self.available}

    def _publish(self) -> None:
        self._store.publish(REFERENCES_KEY, {"available": self.available, "error": self.error, "ts": time.time()})
