"""The list of reference foils, the queue's use of it, and removing single measurements."""

import pytest

from robotweb.equipment import REFERENCE
from robotweb.mqueue import QueueError
from robotweb.references import References, parse
from robotweb.sequence import SequenceError

from test_api_samples import alice, delete, http, passwords, portal, put  # noqa: F401  (fixtures)
from test_measure import M1, M3, M4, rig  # noqa: F401  (fixture)


def test_parse_accepts_the_likely_layouts():
    assert parse(["Fe Cu Zn"]) == ["Fe", "Cu", "Zn"]
    assert parse(["Fe", "Cu", "Zn"]) == ["Fe", "Cu", "Zn"]
    assert parse(["Fe, Cu;  fe ,Zn"]) == ["Fe", "Cu", "Zn"]  # no repeats, whatever the case
    assert parse([]) == [] and parse([""]) == []


async def test_missing_only_when_the_list_is_known(rig):  # noqa: F811
    refs = References(rig.runner, rig.store, rig.equipment)
    assert refs.available is None and not refs.is_missing("Gd")  # unknown: nothing is marked
    rig.server.references = ["Fe", "Cu"]
    await refs.refresh()
    assert rig.store.get("references")["available"] == ["Fe", "Cu"]
    assert refs.is_missing("Ni") and not refs.is_missing("fe")
    rig.equipment.declare(REFERENCE, "Ni", user="alice")  # the loaded foil counts as available
    assert not refs.is_missing("Ni")
    rig.equipment.declare(REFERENCE, None, user="alice")

    rig.server.refuse.add(("SAMPLE", "LIST_REF"))  # for example a control server without the command
    await refs.refresh()
    published = rig.store.get("references")
    assert published["available"] is None and "could not be read" in published["error"]
    assert not refs.is_missing("Ni")


async def test_the_run_does_not_start_without_a_needed_foil(rig):  # noqa: F811
    refs = References(rig.runner, rig.store, rig.equipment)
    rig.engine.references = refs
    rig.server.references = ["Fe", "Cu"]
    await refs.refresh()
    rig.add("KQZ-TBW", [M1, M3])  # Fe K and Ni K
    with pytest.raises(SequenceError, match="no reference foil for: Ni"):
        rig.engine.plan("measure")
    with pytest.raises(SequenceError):
        rig.engine.start("measure", user="alice")
    rig.queue.remove_measurement("KQZ-TBW", M3, user="alice")
    assert "Fe K: reference foil Fe" in "\n".join(rig.engine.plan("measure"))


async def test_the_queue_names_the_foil_each_measurement_needs(rig):  # noqa: F811
    rig.add("KQZ-TBW", [M1, M3])
    # the rig's queue has no science; the backend's has (main.py), see the API test below
    assert [m["reference"] for m in rig.store.get("queue")["items"][0]["measurements"]] == [None, None]


async def test_remove_single_measurements(rig):  # noqa: F811
    rig.add("KQZ-TBW", [M1, M3])
    rig.add("ABC-DEF", [M4])
    with pytest.raises(QueueError, match="no queued measurement"):
        rig.queue.remove_measurement("KQZ-TBW", M4, user="alice")
    with pytest.raises(QueueError, match="not in the queue"):
        rig.queue.remove_measurement("XXX-YYY", M1, user="alice")
    rig.queue.mark_measurement("KQZ-TBW", M1, "running")
    with pytest.raises(QueueError, match="being measured"):
        rig.queue.remove_measurement("KQZ-TBW", M1, user="alice")
    rig.queue.remove_measurement("kqz-tbw", M3, user="alice")
    assert [m["id"] for m in rig.queue.items[0]["measurements"]] == [M1]
    rig.queue.remove_measurement("ABC-DEF", M4, user="alice")  # the last one takes the frame with it
    assert [item["barcode"] for item in rig.queue.items] == ["KQZ-TBW"]


def test_api_reference_foil_in_the_queue_and_removal(http, alice):  # noqa: F811
    assert put(alice, "/api/queue/frames/KQZ-TBW", {"measurement_ids": [M1, M3]}).status_code == 200
    queued = http.app.state.store.get("queue")["items"][0]["measurements"]
    assert [(m["element"], m["reference"]) for m in queued] == [("Fe", "Fe"), ("Ni", "Ni")]

    assert alice.post("/api/references/read").status_code == 200
    assert delete(alice, f"/api/queue/frames/KQZ-TBW/measurements/{M3}").status_code == 200
    assert [m["id"] for m in http.app.state.store.get("queue")["items"][0]["measurements"]] == [M1]
    assert delete(alice, f"/api/queue/frames/KQZ-TBW/measurements/{M3}").status_code == 409
    assert delete(alice, f"/api/queue/frames/KQZ-TBW/measurements/{M1}").status_code == 200
    assert http.app.state.store.get("queue")["items"] == []
