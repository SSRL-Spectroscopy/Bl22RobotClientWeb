"""The EPICS PVs the backend monitors.

To add a PV, add a line here. "plot" keeps a history for it; "group" names
the plot it is drawn in ("adc"); "enum" asks the IOC for the state name
instead of the number; "show=False" is for PVs the backend needs for a value
it works out (the inputs of the mono energy, see mono.py): the page lists them
only in its Signals table; "exp" shows the value in exponential form (1e+9).
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
    exp: bool = False

    @property
    def key(self) -> str:
        return f"pv.{self.name}"

    def as_dict(self) -> dict:
        return {**asdict(self), "key": self.key}


PVS: tuple[PvDef, ...] = (
    PvDef("SPEAR:BeamCurrAvg", "SPEAR current", "mA"),
    PvDef("BL22:SCAN:MASTER:ADC1", "I_0", "V", plot=True, group="adc"),
    PvDef("BL22:SCAN:MASTER:ADC2", "I_1", "V", plot=True, group="adc"),
    PvDef("BL22:SCAN:MASTER:ADC3", "I_2", "V", plot=True, group="adc"),
    PvDef("BL22:SCAN:MASTER:ADC4", "PIPS", "V", plot=True, group="adc"),
    PvDef("BL22:SRS570_AMP1:GAIN", "I_0 amplifier gain", "V/A", exp=True),
    PvDef("BL22:SRS570_AMP2:GAIN", "I_1 amplifier gain", "V/A", exp=True),
    PvDef("BL22:SRS570_AMP3:GAIN", "I_2 amplifier gain", "V/A", exp=True),
    PvDef("BL22:SRS570_AMP4:GAIN", "PIPS amplifier gain", "V/A", exp=True),    
    PvDef("BL22:GAS.IC1P", "Ion chamber 1 gas pressure", "bar"),
    PvDef("BL22:GAS.IC2P", "Ion chamber 2 gas pressure", "bar"),
    PvDef("BL22:GAS.IC3P", "Ion chamber 3 gas pressure", "bar"),
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
    "plot": True, "enum": False, "group": "mono", "show": True, "exp": False,
}


def page_list() -> list[dict]:
    """Everything the page can show: the PVs and the mono energy."""
    shown = [pv.as_dict() for pv in PVS if pv.show]
    inputs = [pv.as_dict() for pv in PVS if not pv.show]
    return shown + [MONO_ENERGY] + inputs
