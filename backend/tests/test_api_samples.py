import secrets

import pytest
from fastapi.testclient import TestClient

from robotweb.auth import UserStore
from robotweb.config import get_settings

from fake_portal import FakePortal, frame
from test_api_auth import Browser

M1, M2, M3 = "1" * 24, "2" * 24, "3" * 24


@pytest.fixture
def passwords() -> dict[str, str]:
    return {name: secrets.token_urlsafe(12) for name in ("alice", "bob")}


@pytest.fixture
def portal() -> FakePortal:
    return FakePortal()


@pytest.fixture
def http(passwords, portal):
    users = UserStore(get_settings().users_file)
    for name, password in passwords.items():
        users.set_password(name, password, create=True)
    from robotweb.main import app

    with TestClient(app) as client:
        app.state.portal = portal.client()
        yield client


@pytest.fixture
def alice(http, passwords) -> Browser:
    browser = Browser(http)
    browser.login("alice", passwords["alice"])
    browser.post("/api/helm/request")
    return browser


def put(browser: Browser, path: str, body: dict):
    return browser._http.put(path, json=body, headers=browser._headers())


def delete(browser: Browser, path: str):
    return browser._http.delete(path, headers=browser._headers())


def test_sample_list_is_for_operators_only(http, passwords, alice):
    assert Browser(http).get("/api/samples").status_code == 401
    bob = Browser(http)
    bob.login("bob", passwords["bob"])  # logged in, without the helm
    body = bob.get("/api/samples").json()
    assert body["frames"][0]["owner"] == "Jane Doe"
    assert len(body["frames"][0]["measurements"]) == 3


def test_sample_list_shows_every_frame_in_internal_storage(http, alice, portal):
    # A frame with nothing pending and one that is not locked are not in the
    # portal's queue, but they are in a slot and so they are listed.
    finished = frame("ABC-DEF", 0, 3)
    for position in finished["positions"]:
        for m in position["measurements"]:
            m["status"] = "done"
    portal.frames["ABC-DEF"] = finished
    portal.frames["GHI-JKL"] = frame("GHI-JKL", 1, 0, locked=False)
    frames = alice.get("/api/samples").json()["frames"]
    assert [(f["barcode"], f["container"], f["slot"]) for f in frames] == [
        ("ABC-DEF", 0, 3), ("GHI-JKL", 1, 0), ("KQZ-TBW", 2, 7),
    ]
    assert frames[1]["locked"] is False and frames[0]["state"] == "internal_storage"


def test_queue_changes_need_the_helm(http, passwords, alice, portal):
    observer, bob = Browser(http), Browser(http)
    bob.login("bob", passwords["bob"])
    body = {"measurement_ids": [M1]}
    assert put(observer, "/api/queue/frames/KQZ-TBW", body).status_code == 401
    assert put(bob, "/api/queue/frames/KQZ-TBW", body).status_code == 403
    assert delete(bob, "/api/queue/frames/KQZ-TBW").status_code == 403
    assert bob.post("/api/queue/clear").status_code == 403
    assert bob.post("/api/queue/order", {"barcodes": []}).status_code == 403
    assert http.app.state.queue.items == []


def test_queue_a_frame_and_everyone_sees_it_without_names(http, alice):
    with Browser(http).websocket() as ws:
        assert ws.receive_json()["state"]["queue"]["items"] == []
        assert put(alice, "/api/queue/frames/kqz-tbw", {"measurement_ids": [M1, M3]}).status_code == 200
        while True:
            update = ws.receive_json()
            if "queue" in update["changes"]:
                break
    item = update["changes"]["queue"]["items"][0]
    assert item["barcode"] == "KQZ-TBW" and (item["container"], item["slot"]) == (2, 7)
    assert [m["position"] for m in item["measurements"]] == ["Left-A", "Right-C"]
    assert "owner" not in item and "sample_name" not in item["measurements"][0]


def test_the_frame_is_read_again_when_queued(http, alice, portal):
    # Between loading the list and queueing, the portal changed.
    portal.frames["KQZ-TBW"]["safe"] = False
    response = put(alice, "/api/queue/frames/KQZ-TBW", {"measurement_ids": [M1]})
    assert response.status_code == 409 and "not declared safe" in response.json()["detail"]
    portal.frames["KQZ-TBW"]["safe"] = True
    portal.frames["KQZ-TBW"]["locked"] = False
    response = put(alice, "/api/queue/frames/KQZ-TBW", {"measurement_ids": [M1]})
    assert response.status_code == 409 and "not locked" in response.json()["detail"]


def test_measurements_that_are_done_or_failed_can_be_queued_again(http, alice, portal):
    measurements = portal.frames["KQZ-TBW"]["positions"][0]["measurements"]
    measurements[0]["status"] = "done"
    assert put(alice, "/api/queue/frames/KQZ-TBW", {"measurement_ids": [M1]}).status_code == 200
    queued = http.app.state.queue.items[0]["measurements"]
    assert [m["status"] for m in queued] == ["done"]
    measurements[0]["status"] = "error"
    assert put(alice, "/api/queue/frames/KQZ-TBW", {"measurement_ids": [M1]}).status_code == 200
    measurements[0]["status"] = "something_new"
    response = put(alice, "/api/queue/frames/KQZ-TBW", {"measurement_ids": [M1]})
    assert response.status_code == 409 and "unknown status" in response.json()["detail"]


def test_refusals(http, alice, portal):
    assert put(alice, "/api/queue/frames/ABC-DEF", {"measurement_ids": [M1]}).status_code == 404
    assert put(alice, "/api/queue/frames/not-a-barcode!", {"measurement_ids": [M1]}).status_code == 422
    assert put(alice, "/api/queue/frames/KQZ-TBW", {"measurement_ids": []}).status_code == 409
    assert delete(alice, "/api/queue/frames/KQZ-TBW").status_code == 409  # not queued
    portal.down = True
    assert put(alice, "/api/queue/frames/KQZ-TBW", {"measurement_ids": [M1]}).status_code == 502
    assert alice.get("/api/samples").status_code == 502


def test_reorder_remove_clear(http, alice, portal):
    portal.frames["ABC-DEF"] = frame("ABC-DEF", 0, 3)
    for barcode in ("KQZ-TBW", "ABC-DEF"):
        assert put(alice, f"/api/queue/frames/{barcode}", {"measurement_ids": [M1]}).status_code == 200
    queue = http.app.state.queue
    assert alice.post("/api/queue/order", {"barcodes": ["ABC-DEF", "KQZ-TBW"]}).status_code == 200
    assert [i["barcode"] for i in queue.items] == ["ABC-DEF", "KQZ-TBW"]
    assert delete(alice, "/api/queue/frames/ABC-DEF").status_code == 200
    assert alice.post("/api/queue/clear").status_code == 200
    assert queue.items == []


def test_unconfigured_portal_is_explained(passwords):
    users = UserStore(get_settings().users_file)
    users.set_password("alice", passwords["alice"], create=True)
    from robotweb.main import app

    with TestClient(app) as http:  # no URL or key in the test environment
        browser = Browser(http)
        browser.login("alice", passwords["alice"])
        response = browser.get("/api/samples")
        assert response.status_code == 503
        assert "ROBOTWEB_BL22_API_KEY" in response.json()["detail"]
