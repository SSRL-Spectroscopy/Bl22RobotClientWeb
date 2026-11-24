"""What a measurement needs: energies, scan settings, slits, detune, gases,
reference foil and FY filter.

Written from the older C# client (reference/csharp/MasterScan.cs and
helpers.cs). Everything here is plain calculation and table lookup; nothing
talks to the control server.

Tables and mappings are files in config/, read when the backend starts:
  xray_edges.csv               absorption edge energies (X-Ray Data Booklet)
  xray_fluorescence_lines.csv  fluorescence lines per edge
  reference_foils.json         measured element/edge -> reference foil
  fy_filters.json              measured element -> filter for the PIPS detector

Edges are written as the portal writes them (K, L1, L2, L3) everywhere in the
backend. The tables and the control server write L-3; see table_edge().
"""

import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

# The control software decides the number of sweeps (instrument-api.md 4.2).
SWEEPS = 4

SI_111 = "Si-111"
SI_220 = "Si-220"
# Lattice spacings in angstrom; BL22:STORE:CRYSTAL_D holds one of them.
_CRYSTAL_D = {SI_111: 3.1356, SI_220: 1.9201}
_CRYSTAL_D_TOLERANCE = 0.05

# Slits while the mono is detuned (vertical, horizontal; mm).
DETUNE_SLITS = (4.0, 10.0)

# Motor positions the sequence uses (mm).
SOL_VERT_FY = 132.0
SOL_VERT_PARKED = 25.0
PIPS_VERT_FY = 132.0
PIPS_VERT_PARKED = 25.0
OPTIC_VERT_PARKED = 5.0
OPTIC_VERT_PIPE = 73.0  # unfocused beam
OPTIC_VERT_CAP = 148.0  # focused beam
DET_X_IN, DET_X_PARKED = 200.0, 0.0
DET_Y_IN, DET_Y_PARKED = 10.0, 130.0

# Above this mono energy the frame is too transparent for its edges to be
# found, so the frame is aligned at FRAME_ALIGN_ENERGY instead.
FRAME_ALIGN_ABOVE = 13000.0
FRAME_ALIGN_ENERGY = 10000.0

# Order of measurements within one element and edge.
_DETECTION_ORDER = {"transmission": 0, "transmission_tfy": 1, "transmission_sdd": 2}
_BEAM_ORDER = {"unfocused": 0, "focused": 1}

_EDGE = re.compile(r"^([KLMNOP])-?(\d)?$")


class ScienceError(Exception):
    """A measurement cannot be prepared: something is missing from a table."""


def table_edge(edge: str) -> str:
    """K -> K, L3 -> L-3: the notation of the tables and the control server."""
    match = _EDGE.fullmatch(edge.strip().upper())
    if match is None:
        raise ScienceError(f"unknown edge {edge!r}")
    shell, number = match.groups()
    if shell == "K":
        return "K"
    if number is None:
        raise ScienceError(f"unknown edge {edge!r}")
    return f"{shell}-{number}"


def crystal_cut(d_spacing: float | None) -> str | None:
    """The crystal cut for a lattice spacing in angstrom, or None if unknown."""
    if d_spacing is None:
        return None
    for cut, value in _CRYSTAL_D.items():
        if abs(d_spacing - value) <= _CRYSTAL_D_TOLERANCE:
            return cut
    return None


def num(value: float) -> str:
    """A number as sent to the control server: no exponent, no trailing zeros."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


@dataclass(frozen=True)
class Setup:
    """Everything one measurement needs, worked out before anything moves."""

    element: str
    edge: str  # portal notation
    technique: str
    detection: str
    beam: str
    edge_energy: float
    e1: float
    e2: float
    scan_time: float
    scan_points: int
    sweeps: int
    detune_factor: float
    slit_v: float
    slit_h: float
    gas_mix: str
    foil_element: str
    foil_edge: str  # table notation, as sent to the control server
    fy_filter: str  # "" for none
    crystal: str
    # SDD only: regions of interest in detector channels (10 eV each).
    roi1_min: int = 0
    roi1_size: int = 0
    roi2_min: int = 0
    roi2_size: int = 0

    @property
    def mid_energy(self) -> float:
        return (self.e1 + self.e2) / 2.0

    @property
    def is_tfy(self) -> bool:
        return self.detection == "transmission_tfy"

    @property
    def is_sdd(self) -> bool:
        return self.detection == "transmission_sdd"

    @property
    def is_focused(self) -> bool:
        return self.beam == "focused"

    @property
    def edge_key(self) -> tuple[str, str]:
        return (self.element, self.edge)

    @property
    def trajectory(self) -> str:
        return f"{self.element} {self.edge} {self.technique.upper()}"


class Science:
    def __init__(self, folder: Path) -> None:
        self._edges = _load_edges(folder / "xray_edges.csv")
        self._lines = _load_lines(folder / "xray_fluorescence_lines.csv")
        self._foils = _load_json(folder / "reference_foils.json")
        self._filters = _load_json(folder / "fy_filters.json")
        for element, entry in self._foils.items():
            if not isinstance(entry, dict) or "default" not in entry:
                raise ScienceError(f"reference_foils.json: {element} needs a 'default' entry")
            for value in entry.values():
                _split_foil(value)
        for element, value in self._filters.items():
            if not isinstance(value, str):
                raise ScienceError(f"fy_filters.json: the filter for {element} must be a text")

    # ---- lookups ----------------------------------------------------------

    def edge_energy(self, element: str, edge: str) -> float:
        try:
            return self._edges[(element.capitalize(), table_edge(edge))]
        except KeyError:
            raise ScienceError(f"no edge energy tabulated for {element} {edge}") from None

    def emission_energy(self, element: str, edge: str) -> float:
        """Intensity-weighted mean energy of the fluorescence lines of an edge."""
        lines = self._lines.get((element.capitalize(), table_edge(edge)))
        if not lines:
            raise ScienceError(f"no fluorescence lines tabulated for {element} {edge}")
        return sum(e * i for e, i in lines) / sum(i for _, i in lines)

    def reference_foil(self, element: str, edge: str) -> tuple[str, str]:
        """(foil element, foil edge in table notation) for a measured edge."""
        entry = self._foils.get(element)
        if entry is None:
            return element, table_edge(edge)
        foil_element, foil_edge = _split_foil(entry.get(edge.upper(), entry["default"]))
        return foil_element, table_edge(foil_edge)

    def fy_filter(self, element: str) -> str:
        return self._filters.get(element, "")

    # ---- one measurement ----------------------------------------------------

    def setup(self, measurement: dict, crystal: str) -> Setup:
        """Work out everything for one measurement (a queue entry)."""
        element, edge = measurement["element"], measurement["edge"]
        technique, detection = measurement["technique"], measurement["detection"]
        beam, plate_type = measurement["beam"], measurement["plate_type"]
        is_sdd = detection == "transmission_sdd"
        is_tfy = detection == "transmission_tfy"

        e0 = self.edge_energy(element, edge)
        if technique == "xanes":
            e1, e2 = e0 - 200.0, e0 + 300.0
            step, speed = (0.25, 5.0) if is_sdd else (0.20, 10.0)
        elif technique == "exafs":
            e1, e2 = e0 - 200.0, e0 + 1000.0
            step, speed = (0.5, 5.0) if is_sdd else (0.25, 7.5)
        else:
            raise ScienceError(f"unknown technique {technique!r}")
        span = e2 - e1
        mid = (e1 + e2) / 2.0

        slit_h = 6.0 if plate_type in ("8-circle", "8-circle-gas") else 10.0
        if is_sdd or is_tfy:
            slit_h *= math.sin(math.radians(45.0))  # the sample is turned 45 degrees
        if beam == "focused":
            slit_h = 10.0
        # 1.2 mm at 5 keV down to 0.5 mm at 20 keV, never below 0.4 mm
        slit_v = max(0.4, -4.7e-5 * mid + 1.43)

        foil_element, foil_edge = self.reference_foil(element, edge)
        roi = {}
        if is_sdd:
            centre = self.emission_energy(element, edge) / 10.0
            roi = {
                "roi1_min": math.floor(centre - 20),
                "roi1_size": 40,
                "roi2_min": math.floor(centre - 40),
                "roi2_size": 80,
            }
        return Setup(
            element=element,
            edge=edge,
            technique=technique,
            detection=detection,
            beam=beam,
            edge_energy=e0,
            e1=e1,
            e2=e2,
            scan_time=span / speed,
            scan_points=int(span / step),
            sweeps=SWEEPS,
            detune_factor=detune_factor(mid, crystal),
            slit_v=slit_v,
            slit_h=slit_h,
            gas_mix=gas_mix(mid),
            foil_element=foil_element,
            foil_edge=foil_edge,
            fy_filter=self.fy_filter(element) if is_tfy else "",
            crystal=crystal,
            **roi,
        )

    def ordered(self, measurements: list[dict]) -> list[dict]:
        """The order in which a frame's measurements are run: all measurements
        of one element and edge together (lowest edge energy first), within
        that by detection, then by beam. Otherwise the portal's order."""

        def key(m: dict) -> tuple:
            return (
                self.edge_energy(m["element"], m["edge"]),
                m["element"],
                m["edge"],
                _DETECTION_ORDER.get(m["detection"], 99),
                _BEAM_ORDER.get(m["beam"], 99),
            )

        return sorted(measurements, key=key)


def detune_factor(mid_energy: float, crystal: str) -> float:
    """How far the mono is detuned to suppress harmonics. Capped at 0.45 at
    low energy (the cap is kept on purpose, decided 2026-11-16)."""
    if crystal == SI_111:
        if mid_energy < 5000:
            return 0.45
        if mid_energy > 10000:
            return 0.1
        return mid_energy * -7e-5 + 0.8
    if crystal == SI_220:
        if mid_energy < 12000:
            return 0.45
        if mid_energy > 20000:
            return 0.2
        return mid_energy * -3.125e-5 + 0.825
    return 0.2


def gas_mix(mid_energy: float) -> str:
    """Gases and pressures for the three ion chambers, as the arguments of
    BEAMLINE AUTO_GAS_FILL: gas 1, pressure 1, gas 2, pressure 2 per chamber."""
    n, a = "NITROGEN", "ARGON"
    if mid_energy < 7000:
        chambers = ((n, "1.1", n, "0"), (n, "1.6", n, "0"), (n, "2.1", n, "0"))
    elif mid_energy < 12000:
        chambers = ((n, "1.5", n, "0"), (n, "2.5", n, "0"), (n, "3.0", n, "0"))
    elif mid_energy < 18000:
        chambers = ((n, "3.0", n, "0"), (a, "1.0", n, "2.0"), (a, "1.0", n, "2.0"))
    elif mid_energy < 32000:
        chambers = ((a, "1.0", n, "2.0"), (n, "1.0", a, "2.0"), (n, "1.0", a, "2.0"))
    else:
        chambers = ((n, "1.0", a, "2.0"),) * 3
    return " ".join(" ".join(chamber) for chamber in chambers)


# ---- file readers -------------------------------------------------------------


def _rows(path: Path):
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            lines = [line for line in handle if line.strip() and not line.startswith(("#", "Z,"))]
    except OSError as exc:
        raise ScienceError(f"{path.name}: {exc}") from exc
    return csv.reader(lines)


def _load_edges(path: Path) -> dict[tuple[str, str], float]:
    table = {}
    for row in _rows(path):
        if len(row) >= 4:
            table[(row[1].strip(), row[2].strip())] = float(row[3])
    if not table:
        raise ScienceError(f"{path.name} contains no edges")
    return table


def _load_lines(path: Path) -> dict[tuple[str, str], list[tuple[float, float]]]:
    table: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for row in _rows(path):
        if len(row) >= 7:
            table.setdefault((row[1].strip(), row[2].strip()), []).append((float(row[5]), float(row[6])))
    if not table:
        raise ScienceError(f"{path.name} contains no lines")
    return table


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScienceError(f"{path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ScienceError(f"{path.name} must contain an object")
    return {key: value for key, value in data.items() if not key.startswith("_")}


def _split_foil(value) -> tuple[str, str]:
    parts = value.split() if isinstance(value, str) else []
    if len(parts) != 2:
        raise ScienceError(f"reference_foils.json: {value!r} is not '<element> <edge>'")
    table_edge(parts[1])
    return parts[0], parts[1]
