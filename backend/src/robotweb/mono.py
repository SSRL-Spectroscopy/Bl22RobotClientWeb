"""The monochromator energy, worked out from its encoder.

    Bragg angle [deg] = REP x (UREV / EREV) + offset
    energy [eV]       = h c / (2 d sin(angle))

REP (raw encoder counts), UREV and EREV are fields of the mono motor record;
the offset is BL22:STORE:CRYSTAL_ENCODER_OFFSET, which changes at every energy
calibration and is therefore monitored. d is the crystal's lattice spacing in
angstrom (crystal.py: read at start, on Reload and on Apply).

The result is published like a PV, with a history for the plot. It is for
display only: sequences ask the control server for the mono energy.
"""

import math
import time
from collections.abc import Callable

from .pvs import MONO_ENERGY_KEY, MONO_MOTOR
from .state import StateStore

HC_EV_ANGSTROM = 12398.4198
REP = f"{MONO_MOTOR}.REP"
UREV = f"{MONO_MOTOR}.UREV"
EREV = f"{MONO_MOTOR}.EREV"
OFFSET = "BL22:STORE:CRYSTAL_ENCODER_OFFSET"
INPUTS = (REP, UREV, EREV, OFFSET)


def bragg_energy(rep: float, urev: float, erev: float, offset: float, d: float) -> float | None:
    """The energy in eV, or None where the numbers make no sense."""
    if erev == 0 or d <= 0:
        return None
    sine = math.sin(math.radians(rep * (urev / erev) + offset))
    if sine <= 0:
        return None
    return HC_EV_ANGSTROM / (2.0 * d * sine)


class MonoEnergy:
    def __init__(self, store: StateStore, lattice_spacing: Callable[[], float | None]) -> None:
        self._store = store
        self._d = lattice_spacing
        self._store.publish(MONO_ENERGY_KEY, {"connected": False, "value": None, "severity": 0, "ts": time.time()})

    def on_pv_update(self, name: str) -> None:
        """Call after a monitored PV has been published."""
        if name in INPUTS:
            self.update()

    def update(self) -> None:
        values = [self._number(name) for name in INPUTS]
        d = self._d()
        energy = None
        if d is not None and all(value is not None for value in values):
            energy = bragg_energy(*values, d)
        ts = float((self._store.get(f"pv.{REP}") or {}).get("ts") or time.time())
        previous = self._store.get(MONO_ENERGY_KEY) or {}
        if energy is None:
            # keep the last value so the page can show it as stale
            self._store.publish(
                MONO_ENERGY_KEY, {"connected": False, "value": previous.get("value"), "severity": 0, "ts": ts}
            )
            return
        self._store.publish(MONO_ENERGY_KEY, {"connected": True, "value": energy, "severity": 0, "ts": ts})
        self._store.add_sample(MONO_ENERGY_KEY, ts, energy)

    def _number(self, name: str) -> float | None:
        entry = self._store.get(f"pv.{name}")
        if not entry or not entry.get("connected"):
            return None
        value = entry.get("value")
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
