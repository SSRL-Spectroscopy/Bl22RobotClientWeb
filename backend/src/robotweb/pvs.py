"""The EPICS PVs the backend monitors.

To add a PV, add a line here. "plot" keeps a history for it; "group" names
the plot it is drawn in ("adc"); "enum" asks the IOC for the state name
instead of the number; "show=False" is for PVs the backend needs but the page
does not list (the inputs of the mono energy, see mono.py).
"""

from dataclasses import asdict, dataclass

MONO_MOTOR = "BL22:SCAN:MASTER:MOTOR1"


@dataclass(frozen=True)
class PvDef:
    name: str
    label: str
    unit: str = ""
    plot: bool = False
    enum: bool = False
    group: str = ""
    show: bool = True

    @property
    def key(self) -> str:
        return f"pv.{self.name}"

    def as_dict(self) -> dict:
        return {**asdict(self), "key": self.key}


PVS: tuple[PvDef, ...] = (
    PvDef("BL22:SCAN:MASTER:ADC1", "I_0", "V", plot=True, group="adc"),
    PvDef("BL22:SCAN:MASTER:ADC2", "I_1", "V", plot=True, group="adc"),
    PvDef("BL22:SCAN:MASTER:ADC3", "I_2", "V", plot=True, group="adc"),
    PvDef("BL22:SCAN:MASTER:ADC4", "PIPS", "V", plot=True, group="adc"),
    PvDef("SPEAR:BeamCurrAvg", "SPEAR current", "mA"),
    PvDef("BL22:SCAN:MASTER.RTME", "Remaining time", "s"),
    # Inputs of the mono energy.
    PvDef(f"{MONO_MOTOR}.REP", "Mono encoder", "counts", show=False),
    PvDef(f"{MONO_MOTOR}.UREV", "Mono units per revolution", "deg", show=False),
    PvDef(f"{MONO_MOTOR}.EREV", "Mono encoder counts per revolution", "counts", show=False),
    PvDef("BL22:STORE:CRYSTAL_ENCODER_OFFSET", "Mono encoder offset", "deg", show=False),
)

# Worked out by the backend from the PVs above (mono.py); listed for the page
# like a PV.
MONO_ENERGY_KEY = "mono.energy"
MONO_ENERGY = {
    "name": "mono.energy", "key": MONO_ENERGY_KEY, "label": "Mono energy", "unit": "eV",
    "plot": True, "enum": False, "group": "mono", "show": True,
}


def page_list() -> list[dict]:
    """What the page shows: the listed PVs and the mono energy."""
    return [pv.as_dict() for pv in PVS if pv.show] + [MONO_ENERGY]
