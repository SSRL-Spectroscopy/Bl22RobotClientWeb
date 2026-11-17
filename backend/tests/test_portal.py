import json

import pytest
from pydantic import SecretStr

from robotweb.audit import AuditLog
from robotweb.mqueue import MeasurementQueue, QueueError
from robotweb.portal import PortalClient, PortalError, PortalNotConfigured
from robotweb.samples import normalise_frame
from robotweb.state import StateStore

from fake_portal import FakePortal, frame, measurement

M1, M2, M3 = "1" * 24, "2" * 24, "3" * 24


@pytest.fixture
def portal() -> FakePortal:
    return FakePortal()


# ---- the client ------------------------------------------------------------


async def test_reads_the_queue_and_one_frame(portal):
    client = portal.client()
    queue = await client.queue()
    assert [f["barcode"] for f in queue] == ["KQZ-TBW"]
    assert (await client.frame("kqz-tbw "))["barcode"] == "KQZ-TBW"  # case and spaces do not matter
    assert (await client.internal_storage())[0]["slot"] == 7
    assert (await client.health())["version"] == "2.7.1"
    await client.close()


async def test_portal_errors_are_reported_with_their_detail(portal):
    client = portal.client()
    with pytest.raises(PortalError, match="404: Unknown barcode") as info:
        await client.frame("ABC-DEF")
    assert info.value.status == 404
    with pytest.raises(PortalError, match="409: Cannot move from internal_storage to loaded"):
        await client.set_frame_state("KQZ-TBW", "loaded")
    await client.close()


async def test_wrong_key_is_refused_and_never_shown(portal):
    wrong = "htxas_" + "x" * 40
    client = portal.client(key=wrong)
    with pytest.raises(PortalError) as info:
        await client.queue()
    assert info.value.status == 401
    assert wrong not in str(info.value)
    await client.close()


async def test_unreachable_portal(portal):
    portal.down = True
    client = portal.client()
    with pytest.raises(PortalError, match="cannot be reached") as info:
        await client.queue()
    assert portal.key not in str(info.value)
    await client.close()


async def test_not_configured():
    for url, key in [("", None), ("http://portal.test/api", None), ("", SecretStr("k")), ("http://x", SecretStr(""))]:
        client = PortalClient(url, key)
        assert not client.configured
        with pytest.raises(PortalNotConfigured):
            await client.queue()
        await client.close()


async def test_bad_identifiers_never_reach_the_portal(portal):
    client = portal.client()
    for bad in ["../admin", "KQZ-TBW/state", "", "KQZTBW"]:
        with pytest.raises(PortalError):
            await client.frame(bad)
    with pytest.raises(PortalError):
        await client.set_measurement_status("../x", "done")
    with pytest.raises(PortalError):
        await client.set_frame_state("KQZ-TBW", "void")
    assert portal.requests == []
    await client.close()


async def test_reporting_progress(portal):
    client = portal.client()
    await client.set_frame_state("KQZ-TBW", "scheduled")
    await client.set_frame_state("KQZ-TBW", "loaded")
    done = await client.set_measurement_status(M1, "done")
    assert done["status"] == "done"
    failed = await client.set_measurement_status(M3, "error", "Edge step too small")
    assert failed["error_message"] == "Edge step too small"
    await client.set_frame_state("KQZ-TBW", "internal_storage")
    assert portal.requests[-1] == ("POST", "/instrument/frames/KQZ-TBW/state", {"state": "internal_storage"})
    await client.close()


# ---- the frame representation -----------------------------------------------


def test_frame_is_flattened_into_measurements():
    f = normalise_frame(frame())
    assert (f["barcode"], f["container"], f["slot"], f["owner"]) == ("KQZ-TBW", 2, 7, "Jane Doe")
    assert [m["id"] for m in f["measurements"]] == [M1, M2, M3]
    first = f["measurements"][0]
    assert first["position"] == "Left-A" and first["plate_type"] == "8-circle"
    assert first["sample_name"] == "Fe2O3 reference"
    assert first["measurable"] and first["problem"] is None
    assert f["measurements"][1]["last_sweep"] == 8


def test_unknown_values_do_not_fail_but_are_not_measurable():
    raw = frame()
    raw["positions"][0]["plate_type"] = "16-well"
    raw["positions"][1]["measurements"][0]["technique"] = "xes"
    f = normalise_frame(raw)
    assert not f["measurements"][0]["measurable"]
    assert "plate type '16-well'" in f["measurements"][0]["problem"]
    assert "technique 'xes'" in f["measurements"][2]["problem"]


def test_frame_without_a_slot_or_owner():
    f = normalise_frame(frame(storage=None, owner=None))
    assert f["container"] is None and f["slot"] is None and f["owner"] is None


# ---- the queue ---------------------------------------------------------------


def make_queue(tmp_path) -> tuple[MeasurementQueue, StateStore]:
    store = StateStore()
    return MeasurementQueue(tmp_path / "queue.json", store, AuditLog(tmp_path / "audit.log")), store


def test_queue_a_frame_with_selected_measurements(tmp_path):
    queue, store = make_queue(tmp_path)
    queue.set_frame(normalise_frame(frame()), [M3, M1], user="alice")
    item = queue.items[0]
    assert (item["barcode"], item["container"], item["slot"]) == ("KQZ-TBW", 2, 7)
    assert [m["id"] for m in item["measurements"]] == [M1, M3]  # the portal's order, not the click order
    assert all(m["state"] == "queued" for m in item["measurements"])

    published = store.get("queue")["items"][0]
    assert published["measurements"][0]["element"] == "Fe"
    text = json.dumps(store.get("queue"))
    assert "Jane Doe" not in text and "Fe2O3 reference" not in text  # names stay with operators


def test_changing_the_selection_keeps_the_place_in_the_queue(tmp_path):
    queue, _ = make_queue(tmp_path)
    second = frame("ABC-DEF", 0, 3)
    second["positions"][0]["measurements"][0]["id"] = "4" * 24
    queue.set_frame(normalise_frame(frame()), [M1, M3], user="alice")
    queue.set_frame(normalise_frame(second), ["4" * 24], user="alice")
    queue.set_frame(normalise_frame(frame()), [M3], user="alice")
    assert [i["barcode"] for i in queue.items] == ["KQZ-TBW", "ABC-DEF"]
    assert [m["id"] for m in queue.items[0]["measurements"]] == [M3]


@pytest.mark.parametrize(
    ("changes", "ids", "message"),
    [
        ({}, [], "at least one"),
        ({"safe": False}, [M1], "not declared safe"),
        ({}, ["9" * 24], "has no measurement"),
        ({}, [M1, M1], "selected twice"),
        ({"state": "loaded"}, [M1], "not in internal storage"),
        ({"locked": False}, [M1], "not locked"),
        ({"storage": None}, [M1], "no storage slot"),
    ],
)
def test_what_cannot_be_queued(tmp_path, changes, ids, message):
    queue, _ = make_queue(tmp_path)
    with pytest.raises(QueueError, match=message):
        queue.set_frame(normalise_frame(frame(**changes)), ids, user="alice")
    assert queue.items == []


def test_unmeasurable_measurement_cannot_be_queued(tmp_path):
    queue, _ = make_queue(tmp_path)
    raw = frame()
    raw["positions"][0]["measurements"] = [measurement(M1, beam="microfocus")]
    with pytest.raises(QueueError, match="cannot be measured"):
        queue.set_frame(normalise_frame(raw), [M1], user="alice")


def test_remove_reorder_clear_and_restart(tmp_path):
    queue, _ = make_queue(tmp_path)
    for barcode, slot in [("KQZ-TBW", 7), ("ABC-DEF", 3), ("GHI-JKL", 5)]:
        queue.set_frame(normalise_frame(frame(barcode, 0, slot)), [M1], user="alice")

    queue.reorder(["GHI-JKL", "KQZ-TBW", "ABC-DEF"], user="alice")
    with pytest.raises(QueueError):
        queue.reorder(["GHI-JKL", "KQZ-TBW"], user="alice")  # a frame is missing
    queue.remove("kqz-tbw", user="alice")
    with pytest.raises(QueueError):
        queue.remove("KQZ-TBW", user="alice")
    assert [i["barcode"] for i in queue.items] == ["GHI-JKL", "ABC-DEF"]

    restarted, _ = make_queue(tmp_path)  # the queue survives a backend restart
    assert [i["barcode"] for i in restarted.items] == ["GHI-JKL", "ABC-DEF"]
    restarted.clear(user="alice")
    assert make_queue(tmp_path)[0].items == []
