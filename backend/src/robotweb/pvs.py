"""The EPICS PVs shown on the status page.

To add a PV, add a line here. "plot" puts it in the rolling plot and keeps a
history for it; "enum" asks the IOC for the state name instead of the number.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PvDef:
    name: str
    label: str
    unit: str = ""
    plot: bool = False
    enum: bool = False

    @property
    def key(self) -> str:
        return f"pv.{self.name}"

    def as_dict(self) -> dict:
        return {**asdict(self), "key": self.key}


PVS: tuple[PvDef, ...] = (
    PvDef("BL22:SCAN:MASTER:ADC1", "I_0", "V", plot=True),
    PvDef("BL22:SCAN:MASTER:ADC2", "I_1", "V", plot=True),
    PvDef("BL22:SCAN:MASTER:ADC3", "I_2", "V", plot=True),
    PvDef("BL22:SCAN:MASTER:ADC4", "PIPS", "V"),
    PvDef("SPEAR:BeamCurrAvg", "SPEAR current", "mA"),
    PvDef("BL22:SCAN:MASTER.TRIG", "DAQ trigger", enum=True),
    PvDef("BL22:SCAN:MASTER.RTME", "Remaining time", "s"),
)
