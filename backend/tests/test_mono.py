"""The mono energy worked out from the encoder PVs."""

import pytest

from robotweb.mono import EREV, OFFSET, REP, UREV, MonoEnergy, bragg_energy
from robotweb.pvs import MONO_ENERGY_KEY, page_list
from robotweb.state import StateStore


def put(store: StateStore, name: str, value, connected: bool = True, ts: float = 100.0) -> None:
    store.publish(f"pv.{name}", {"connected": connected, "value": value, "severity": 0, "ts": ts})


def test_bragg_energy():
    # values read from the beamline on 2026-11-17: 19.3529 degrees on Si-111
    energy = bragg_energy(752519, 360.0, 31488000, 10.74943090784472, 3.1356)
    assert energy == pytest.approx(5965.975, abs=0.01)
    assert bragg_energy(0, 360.0, 31488000, 0.0, 3.1356) is None  # angle 0
    assert bragg_energy(1, 360.0, 0, 10.0, 3.1356) is None
    assert bragg_energy(1, 360.0, 31488000, 10.0, 0.0) is None


def test_energy_follows_encoder_offset_and_crystal():
    store = StateStore()
    d = [3.1356]
    mono = MonoEnergy(store, lambda: d[0])
    assert store.get(MONO_ENERGY_KEY)["connected"] is False

    put(store, UREV, 360.0)
    put(store, EREV, 31488000)
    put(store, OFFSET, 10.74943090784472)
    mono.on_pv_update(OFFSET)
    assert store.get(MONO_ENERGY_KEY)["value"] is None  # no encoder reading yet

    put(store, REP, 752519, ts=123.0)
    mono.on_pv_update(REP)
    entry = store.get(MONO_ENERGY_KEY)
    assert entry["connected"] and entry["value"] == pytest.approx(5965.975, abs=0.01) and entry["ts"] == 123.0

    put(store, OFFSET, 10.8)  # a new energy calibration
    mono.on_pv_update(OFFSET)
    assert store.get(MONO_ENERGY_KEY)["value"] == pytest.approx(5950.95, abs=0.5)

    mono.on_pv_update("SPEAR:BeamCurrAvg")  # not an input: nothing happens
    d[0] = None  # crystal values unknown
    mono.update()
    stale = store.get(MONO_ENERGY_KEY)
    assert stale["connected"] is False and stale["value"] == pytest.approx(5950.95, abs=0.5)

    d[0] = 3.1356
    put(store, REP, 752519, connected=False)
    mono.on_pv_update(REP)
    assert store.get(MONO_ENERGY_KEY)["connected"] is False


def test_page_list_hides_the_inputs():
    names = [entry["name"] for entry in page_list()]
    assert "mono.energy" in names and REP not in names and OFFSET not in names
    groups = {entry["label"]: entry["group"] for entry in page_list() if entry["plot"]}
    assert groups == {"I_0": "adc", "I_1": "adc", "I_2": "adc", "PIPS": "adc", "Mono energy": "mono"}
