"""Watches the SPEAR current and decides whether there is beam to measure with.

Rules (2026-11-17):

- Below `low_ma` (400 mA) the beam counts as lost: the measurement sequence
  holds before its next command.
- The beam counts as back once the current has stayed above `ok_ma` (480 mA)
  for `settle_s` (10 minutes). A reading at or below `ok_ma` in that time
  starts the 10 minutes again.
- If the current cannot be read for `unknown_grace_s` (60 s), that counts as
  lost beam too: better to wait than to measure without knowing.

`losses` counts how often the beam was lost. The measurement sequence compares
it before and after a scan to see whether the scan can be trusted.
"""

import asyncio
import time
from collections.abc import Callable

from .state import StateStore

BEAM_KEY = "beam"


class BeamWatch:
    def __init__(
        self,
        read: Callable[[], float | None],
        store: StateStore | None = None,
        *,
        low_ma: float = 400.0,
        ok_ma: float = 480.0,
        settle_s: float = 600.0,
        unknown_grace_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read = read
        self._store = store
        self.low_ma = low_ma
        self.ok_ma = ok_ma
        self.settle_s = settle_s
        self.unknown_grace_s = unknown_grace_s
        self._clock = clock

        self.held = False
        self.losses = 0
        self.current: float | None = None
        self._reason = ""
        self._good_since: float | None = None
        self._unknown_since: float | None = None
        self._publish()

    def tick(self) -> None:
        """Look at the current once. Call it about once a second."""
        now = self._clock()
        value = self._read()
        self.current = value
        if value is None:
            if self._unknown_since is None:
                self._unknown_since = now
            self._good_since = None
            if not self.held and now - self._unknown_since >= self.unknown_grace_s:
                self._hold("the SPEAR current cannot be read")
        else:
            self._unknown_since = None
            if not self.held:
                if value < self.low_ma:
                    self._hold(f"the SPEAR current fell below {self.low_ma:g} mA")
            elif value > self.ok_ma:
                if self._good_since is None:
                    self._good_since = now
                if now - self._good_since >= self.settle_s:
                    self.held = False
                    self._good_since = None
            else:
                self._good_since = None
        self._publish()

    async def run(self, interval_s: float = 1.0) -> None:
        while True:
            self.tick()
            await asyncio.sleep(interval_s)

    def describe(self) -> str:
        """One line for the page and the log: why the run waits, and for how long."""
        if not self.held:
            return "Beam available."
        now_ma = "not readable" if self.current is None else f"{self.current:.0f} mA"
        if self._good_since is not None:
            left = max(0.0, self.settle_s - (self._clock() - self._good_since))
            return (
                f"Waiting for beam: the SPEAR current is back above {self.ok_ma:g} mA ({now_ma}); "
                f"the run goes on in about {int(left // 60) + 1} min."
            )
        return (
            f"Waiting for beam: {self._reason} (now {now_ma}). The run goes on "
            f"{self.settle_s / 60:g} min after the current is above {self.ok_ma:g} mA."
        )

    def _hold(self, reason: str) -> None:
        self.held = True
        self.losses += 1
        self._reason = reason
        self._good_since = None

    def _publish(self) -> None:
        if self._store is not None:
            self._store.publish(
                BEAM_KEY,
                {
                    "held": self.held,
                    "losses": self.losses,
                    "text": self.describe(),
                    # for the page: below this the SPEAR tile turns amber
                    "low_ma": self.low_ma,
                    "ts": time.time(),
                },
            )


def pv_reader(store: StateStore, key: str) -> Callable[[], float | None]:
    """Reads a monitored PV from the state store; None while it is disconnected."""

    def read() -> float | None:
        entry = store.get(key)
        if not entry or not entry.get("connected"):
            return None
        value = entry.get("value")
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    return read
