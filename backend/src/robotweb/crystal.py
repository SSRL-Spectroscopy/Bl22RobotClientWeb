"""The monochromator crystal, as stored in two EPICS PVs.

    BL22:STORE:CRYSTAL_D    lattice spacing in angstrom (about 3.13 = Si-111,
                            about 1.92 = Si-220)
    BL22:STORE:CRYSTAL_PHI  degrees, 0 or 90

The crystal is changed by hand and rarely, so the PVs are not monitored. The
values are read when the backend starts and when an operator presses Reload,
and written when the operator at the helm presses Apply. Sequences use the
values last read.
"""

import logging
import time
from collections.abc import Callable
from typing import Protocol

from .audit import AuditLog
from .science import crystal_cut
from .state import StateStore

log = logging.getLogger(__name__)

CRYSTAL_KEY = "crystal"
PV_D = "BL22:STORE:CRYSTAL_D"
PV_PHI = "BL22:STORE:CRYSTAL_PHI"
PHI_VALUES = (0.0, 90.0)
D_RANGE = (1.0, 4.0)


class CrystalError(Exception):
    """The crystal values could not be read or written, or are not valid."""


class PvAccess(Protocol):
    async def get(self, name: str) -> float: ...
    async def put(self, name: str, value: float) -> None: ...


class ChannelAccess:
    """Reads and writes through EPICS Channel Access (aioca, imported lazily)."""

    def __init__(self, timeout_s: float = 5.0) -> None:
        self._timeout_s = timeout_s

    async def get(self, name: str) -> float:
        from aioca import caget

        return float(await caget(name, timeout=self._timeout_s))

    async def put(self, name: str, value: float) -> None:
        from aioca import caput

        await caput(name, value, wait=True, timeout=self._timeout_s)


class Memory:
    """Stands in for the PVs when EPICS is off (tests and the simulator)."""

    def __init__(self, d: float = 3.1356, phi: float = 0.0) -> None:
        self.values = {PV_D: d, PV_PHI: phi}

    async def get(self, name: str) -> float:
        return self.values[name]

    async def put(self, name: str, value: float) -> None:
        self.values[name] = value


class Crystal:
    def __init__(self, pvs: PvAccess, store: StateStore, audit: AuditLog, *, simulated: bool = False) -> None:
        self._pvs = pvs
        # Called whenever the values were read or written, for what depends
        # on the lattice spacing (the mono energy).
        self.on_change: Callable[[], None] | None = None
        self._store = store
        self._audit = audit
        self._simulated = simulated
        self.d: float | None = None
        self.phi: float | None = None
        self._read: float | None = None
        self._error: str | None = "not read yet"
        self._publish()

    @property
    def cut(self) -> str | None:
        """ "Si-111", "Si-220", or None if the values are unknown."""
        return crystal_cut(self.d)

    async def reload(self) -> None:
        """Read both PVs. Raises CrystalError if that fails; the values last
        read are then no longer trusted."""
        try:
            d, phi = await self._pvs.get(PV_D), await self._pvs.get(PV_PHI)
        except Exception as exc:  # aioca raises its own error types
            self.d = self.phi = None
            self._error = f"the crystal PVs could not be read: {type(exc).__name__}"
            self._publish()
            raise CrystalError(f"The crystal PVs could not be read: {type(exc).__name__}: {exc}") from exc
        self.d, self.phi = d, phi
        self._read = time.time()
        self._error = None
        self._publish()

    async def apply(self, d: float, phi: float, *, user: str) -> None:
        """Write both PVs, then read them back."""
        if not (D_RANGE[0] <= d <= D_RANGE[1]):
            raise CrystalError(f"The lattice spacing must be between {D_RANGE[0]} and {D_RANGE[1]} angstrom.")
        if crystal_cut(d) is None:
            raise CrystalError(
                "The lattice spacing matches neither Si-111 (about 3.13) nor Si-220 (about 1.92)."
            )
        if phi not in PHI_VALUES:
            raise CrystalError("PHI must be 0 or 90 degrees.")
        try:
            await self._pvs.put(PV_D, d)
            await self._pvs.put(PV_PHI, phi)
        except Exception as exc:
            self._audit.write("crystal_write_failed", user=user, d=d, phi=phi, error=type(exc).__name__)
            # One of the two may have been written: read back what is there.
            try:
                await self.reload()
            except CrystalError:
                pass
            raise CrystalError(f"The crystal PVs could not be written: {type(exc).__name__}: {exc}") from exc
        self._audit.write("crystal_written", user=user, d=d, phi=phi)
        await self.reload()

    def _publish(self) -> None:
        if self.on_change is not None:
            self.on_change()
        self._store.publish(
            CRYSTAL_KEY,
            {
                "d": self.d,
                "phi": self.phi,
                "cut": self.cut,
                "read": self._read,
                "error": self._error,
                "simulated": self._simulated,
                "ts": time.time(),
            },
        )
