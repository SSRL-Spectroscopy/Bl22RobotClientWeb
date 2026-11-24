"""The science helpers, checked against the tables and mappings in config/."""

import json
from pathlib import Path

import pytest

from robotweb.science import (
    Science,
    ScienceError,
    crystal_cut,
    detune_factor,
    gas_mix,
    num,
    table_edge,
)

CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture(scope="module")
def science() -> Science:
    return Science(CONFIG)


def measurement(**changes) -> dict:
    base = {
        "element": "Cu", "edge": "K", "technique": "xanes", "detection": "transmission",
        "beam": "unfocused", "plate_type": "8-circle", "position": "Left-A",
    }
    return {**base, **changes}


def test_edge_notation():
    assert [table_edge(e) for e in ("K", "L3", "l-3", "L1")] == ["K", "L-3", "L-3", "L-1"]
    for bad in ("", "L", "X3", "L33"):
        with pytest.raises(ScienceError):
            table_edge(bad)


def test_table_lookups(science):
    assert science.edge_energy("Fe", "K") == 7112
    assert science.edge_energy("Pt", "L3") == 11564
    assert science.emission_energy("Fe", "K") == pytest.approx(6399.47, abs=0.01)
    with pytest.raises(ScienceError, match="no edge energy"):
        science.edge_energy("Xx", "K")


def test_reference_foil_mapping(science):
    assert science.reference_foil("Cu", "K") == ("Cu", "K")  # not listed: its own foil
    assert science.reference_foil("Pt", "L3") == ("Pt", "L-3")
    assert science.reference_foil("Gd", "L3") == ("Fe", "K")
    assert science.reference_foil("Gd", "L1") == ("Ni", "K")
    assert science.reference_foil("Gd", "L2") == ("Co", "K")
    assert science.reference_foil("Yb", "L2") == ("Ta", "L-3")
    assert science.reference_foil("Sr", "K") == ("Zr", "K")


def test_fy_filter_only_for_tfy(science):
    assert science.setup(measurement(element="Fe", detection="transmission_tfy"), "Si-111").fy_filter == "MN-3"
    assert science.setup(measurement(element="Fe"), "Si-111").fy_filter == ""
    assert science.setup(measurement(element="Mn", detection="transmission_tfy"), "Si-111").fy_filter == ""
    assert science.setup(measurement(element="Pt", edge="L3", detection="transmission_tfy"), "Si-111").fy_filter == ""


def test_scan_settings(science):
    xanes = science.setup(measurement(), "Si-111")
    assert (xanes.e1, xanes.e2, xanes.scan_points, xanes.scan_time) == (8779, 9279, 2500, 50)
    exafs = science.setup(measurement(technique="exafs"), "Si-111")
    assert (exafs.e1, exafs.e2, exafs.scan_points, exafs.scan_time) == (8779, 9979, 4800, 160)
    sdd = science.setup(measurement(detection="transmission_sdd"), "Si-111")
    assert (sdd.scan_points, sdd.scan_time) == (2000, 100)
    assert (sdd.roi1_min, sdd.roi1_size, sdd.roi2_min, sdd.roi2_size) == (784, 40, 764, 80)
    sdd_exafs = science.setup(measurement(detection="transmission_sdd", technique="exafs"), "Si-111")
    assert (sdd_exafs.scan_points, sdd_exafs.scan_time) == (2400, 240)
    assert xanes.sweeps == 8


def test_slits(science):
    assert science.setup(measurement(), "Si-111").slit_h == 6.0
    assert science.setup(measurement(plate_type="8-slot"), "Si-111").slit_h == 10.0
    assert science.setup(measurement(plate_type="8-circle-gas"), "Si-111").slit_h == 6.0
    assert science.setup(measurement(detection="transmission_sdd"), "Si-111").slit_h == pytest.approx(4.2426, abs=1e-4)
    assert science.setup(measurement(detection="transmission_sdd", beam="focused"), "Si-111").slit_h == 10.0
    assert science.setup(measurement(), "Si-111").slit_v == pytest.approx(-4.7e-5 * 9029 + 1.43)
    assert science.setup(measurement(element="Mo"), "Si-111").slit_v == pytest.approx(0.4877, abs=1e-3)
    assert science.setup(measurement(element="Sn"), "Si-111").slit_v == 0.4  # never below 0.4 mm


def test_detune_keeps_the_low_energy_cap():
    assert detune_factor(4000, "Si-111") == 0.45
    assert detune_factor(5000, "Si-111") == pytest.approx(0.45)
    assert detune_factor(7500, "Si-111") == pytest.approx(0.275)
    assert detune_factor(12000, "Si-111") == 0.1
    assert detune_factor(10000, "Si-220") == 0.45
    assert detune_factor(16000, "Si-220") == pytest.approx(0.325)
    assert detune_factor(25000, "Si-220") == 0.2
    assert detune_factor(9000, "something else") == 0.2


def test_gas_mix():
    assert gas_mix(6000) == "NITROGEN 1.1 NITROGEN 0 NITROGEN 1.6 NITROGEN 0 NITROGEN 2.1 NITROGEN 0"
    assert gas_mix(7000).startswith("NITROGEN 1.5")
    assert gas_mix(15000) == "NITROGEN 3.0 NITROGEN 0 ARGON 1.0 NITROGEN 2.0 ARGON 1.0 NITROGEN 2.0"
    assert gas_mix(20000).startswith("ARGON 1.0 NITROGEN 2.0 NITROGEN 1.0 ARGON 2.0")
    assert gas_mix(40000) == " ".join(["NITROGEN 1.0 ARGON 2.0"] * 3)
    assert all(len(gas_mix(e).split()) == 12 for e in (5000, 9000, 15000, 25000, 35000))


def test_order_of_measurements(science):
    ms = [
        measurement(id="a", element="Ni", detection="transmission_sdd"),
        measurement(id="b", element="Fe", detection="transmission_sdd", beam="focused"),
        measurement(id="c", element="Fe", detection="transmission_sdd"),
        measurement(id="d", element="Fe", detection="transmission_tfy"),
        measurement(id="e", element="Ni"),
        measurement(id="f", element="Fe"),
    ]
    assert [m["id"] for m in science.ordered(ms)] == ["f", "d", "c", "b", "e", "a"]


def test_crystal_cut_and_numbers():
    assert crystal_cut(3.13) == "Si-111" and crystal_cut(1.92) == "Si-220"
    assert crystal_cut(2.5) is None and crystal_cut(None) is None
    assert [num(v) for v in (8779.0, 1.0156, 0.0, 4.242640687, 0.27416)] == ["8779", "1.0156", "0", "4.242641", "0.27416"]


def test_broken_mapping_file_is_reported(tmp_path):
    for name in ("xray_edges.csv", "xray_fluorescence_lines.csv", "fy_filters.json"):
        (tmp_path / name).write_bytes((CONFIG / name).read_bytes())
    (tmp_path / "reference_foils.json").write_text(json.dumps({"Ba": {"L1": "Cr K"}}))
    with pytest.raises(ScienceError, match="needs a 'default' entry"):
        Science(tmp_path)
    (tmp_path / "reference_foils.json").write_text(json.dumps({"Ba": {"default": "Vanadium"}}))
    with pytest.raises(ScienceError, match="is not '<element> <edge>'"):
        Science(tmp_path)
