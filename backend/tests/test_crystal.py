"""The crystal values: reading, writing, and who may do what."""

import pytest

from robotweb.audit import AuditLog
from robotweb.crystal import PV_D, PV_PHI, Crystal, CrystalError, Memory
from robotweb.state import StateStore

from test_api_auth import Browser
from test_api_samples import alice, http, passwords, portal  # noqa: F401  (fixtures)


class Broken(Memory):
    async def get(self, name: str) -> float:
        raise TimeoutError("no answer")


def make(tmp_path, pvs=None) -> tuple[Crystal, StateStore, Memory]:
    store = StateStore()
    pvs = pvs or Memory()
    return Crystal(pvs, store, AuditLog(tmp_path / "audit.log")), store, pvs


async def test_values_are_unknown_until_read(tmp_path):
    crystal, store, _ = make(tmp_path)
    assert crystal.cut is None and store.get("crystal")["error"] == "not read yet"
    await crystal.reload()
    published = store.get("crystal")
    assert (published["d"], published["phi"], published["cut"], published["error"]) == (3.1356, 0.0, "Si-111", None)


async def test_apply_writes_both_pvs_and_reads_back(tmp_path):
    crystal, store, pvs = make(tmp_path)
    await crystal.apply(1.92, 90.0, user="alice")
    assert pvs.values == {PV_D: 1.92, PV_PHI: 90.0}
    assert store.get("crystal")["cut"] == "Si-220"
    assert '"crystal_written"' in (tmp_path / "audit.log").read_text()


@pytest.mark.parametrize(
    ("d", "phi", "message"),
    [(2.5, 0.0, "neither Si-111"), (0.2, 0.0, "between"), (3.13, 45.0, "0 or 90")],
)
async def test_invalid_values_are_not_written(tmp_path, d, phi, message):
    crystal, _, pvs = make(tmp_path)
    with pytest.raises(CrystalError, match=message):
        await crystal.apply(d, phi, user="alice")
    assert pvs.values == {PV_D: 3.1356, PV_PHI: 0.0}


async def test_a_failed_read_forgets_the_old_values(tmp_path):
    crystal, store, _ = make(tmp_path, Broken())
    crystal.d, crystal.phi = 3.1356, 0.0
    with pytest.raises(CrystalError, match="could not be read"):
        await crystal.reload()
    assert crystal.cut is None and "could not be read" in store.get("crystal")["error"]


def test_who_may_reload_and_apply(http, passwords, alice):  # noqa: F811
    body = {"d": 1.9201, "phi": 90}
    observer, bob = Browser(http), Browser(http)
    assert observer.post("/api/crystal/reload").status_code == 401
    bob.login("bob", passwords["bob"])  # an operator without the helm
    assert bob.post("/api/crystal/reload").status_code == 200
    assert bob.post("/api/crystal/apply", body).status_code == 403
    assert alice.post("/api/crystal/apply", {"d": 2.5, "phi": 0}).status_code == 409
    assert alice.post("/api/crystal/apply", body).status_code == 200
    assert http.app.state.crystal.cut == "Si-220"
