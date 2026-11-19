import asyncio
import json
import secrets
import threading
import time

import pytest
from fastapi.testclient import TestClient

from robotweb.auth import UserStore
from robotweb.config import get_settings
from robotweb.control.fake_server import FakeControlServer

from test_api_auth import Browser


class ServerThread:
    """Runs a FakeControlServer on its own event loop, for the HTTP-level tests."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.server = FakeControlServer(busy_s=0.15)
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self.server.start(), self.loop).result(5)

    def stop(self) -> None:
        asyncio.run_coroutine_threadsafe(self.server.stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)


@pytest.fixture
def control(monkeypatch):
    thread = ServerThread()
    monkeypatch.setenv("ROBOTWEB_CONTROL_PORT", str(thread.server.port))
    monkeypatch.setenv("ROBOTWEB_MOTION_COMMANDS_ENABLED", "1")
    monkeypatch.setenv("ROBOTWEB_STATUS_POLL_IDLE_S", "0.1")
    monkeypatch.setenv("ROBOTWEB_STATUS_POLL_BUSY_S", "0.05")
    yield thread.server
    thread.stop()


@pytest.fixture
def passwords() -> dict[str, str]:
    return {name: secrets.token_urlsafe(12) for name in ("alice", "bob")}


@pytest.fixture
def http(control, passwords):
    users = UserStore(get_settings().users_file)
    for name, password in passwords.items():
        users.set_password(name, password, create=True)
    from robotweb.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def alice(http, passwords) -> Browser:
    browser = Browser(http)
    browser.login("alice", passwords["alice"])
    browser.post("/api/helm/request")
    return browser


def declare_all_empty(browser: Browser) -> None:
    for item in ("frame", "reference", "filter"):
        assert browser.post("/api/equipment/declare", {"item": item, "value": None}).status_code == 200


def wait_idle(http, seconds: float = 5) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not http.app.state.runner.busy:
            return http.app.state.store.get("command")
        time.sleep(0.02)
    raise AssertionError("command did not finish")


def sent(control) -> list[str]:
    return [r for r in control.requests if not r.endswith(("GET_STATUS", "GET_SAMPLE_STORAGE_SLOTS", "LIST_REF"))]


def test_command_list_is_public(http):
    body = Browser(http).get("/api/commands").json()
    assert body["motion_enabled"] is True
    names = [c["name"] for c in body["commands"]]
    assert names[:2] == ["get_frame", "return_frame"]
    assert body["commands"][0]["params"][1] == {"name": "slot", "label": "Slot", "kind": "int", "min": 0, "max": 11}
    assert "storage_slots" in [q["name"] for q in body["queries"]]


def test_observer_and_operator_without_helm_cannot_command(http, control, passwords, alice):
    observer, bob = Browser(http), Browser(http)
    bob.login("bob", passwords["bob"])
    body = {"args": {"container": 0, "slot": 9}}

    assert observer.post("/api/commands/get_frame", body).status_code == 401
    assert bob.post("/api/commands/get_frame", body).status_code == 403
    assert observer.post("/api/equipment/declare", {"item": "frame", "value": None}).status_code == 401
    assert bob.post("/api/equipment/declare", {"item": "frame", "value": None}).status_code == 403
    assert observer.post("/api/queries/mono_energy").status_code == 401
    assert sent(control) == []


def test_operator_without_helm_may_query(http, passwords, alice):
    bob = Browser(http)
    bob.login("bob", passwords["bob"])
    assert bob.post("/api/queries/mono_energy").json() == {"lines": ["5966.002295"]}


def test_unknown_state_blocks_until_declared(http, control, alice):
    body = {"args": {"container": 0, "slot": 9}}
    response = alice.post("/api/commands/get_frame", body)
    assert response.status_code == 409
    assert "Declare what is actually loaded" in response.json()["detail"]
    assert sent(control) == []

    declare_all_empty(alice)
    assert alice.post("/api/commands/get_frame", body).status_code == 200
    assert wait_idle(http)["phase"] == "succeeded"
    assert sent(control) == ["SAMPLE GET_FRAME 0 9"]
    assert http.app.state.equipment.value("frame") == {"container": 0, "slot": 9}


def test_command_progress_reaches_observers(http, alice):
    declare_all_empty(alice)
    with Browser(http).websocket() as ws:
        ws.receive_json()
        alice.post("/api/commands/get_reference", {"args": {"name": "Co"}})
        phases = []
        while "succeeded" not in phases:
            update = ws.receive_json()
            if "command" in update["changes"]:
                phases.append(update["changes"]["command"]["phase"])
        assert phases[0] == "running"
    assert http.app.state.store.get("equipment")["items"]["reference"]["value"] == "Co"


def test_refusals_are_explained_and_audited(http, control, alice):
    declare_all_empty(alice)
    response = alice.post("/api/commands/return_frame")
    assert response.status_code == 409 and "no frame on the beam" in response.json()["detail"]
    assert alice.post("/api/commands/get_frame", {"args": {"container": 7, "slot": 0}}).status_code == 409
    assert alice.post("/api/commands/self_destruct").status_code == 404
    assert sent(control) == []
    lines = [json.loads(line) for line in get_settings().audit_file.read_text().splitlines()]
    refused = [line for line in lines if line["action"] == "command_refused"]
    assert len(refused) == 2 and refused[0]["user"] == "alice"


def test_declare_is_refused_while_a_command_runs(http, alice):
    declare_all_empty(alice)
    alice.post("/api/commands/get_frame", {"args": {"container": 0, "slot": 9}})
    assert alice.post("/api/equipment/declare", {"item": "frame", "value": None}).status_code == 409
    wait_idle(http)
    assert alice.post("/api/equipment/declare", {"item": "frame", "value": None}).status_code == 200


def test_losing_the_helm_stops_further_commands(http, control, passwords, alice):
    declare_all_empty(alice)
    alice.post("/api/helm/release")
    assert alice.post("/api/commands/get_frame", {"args": {"container": 0, "slot": 9}}).status_code == 403
    assert sent(control) == []


def test_motor_positions_endpoint(http, passwords, alice):
    observer, bob = Browser(http), Browser(http)
    bob.login("bob", passwords["bob"])
    assert observer.post("/api/motors/read").status_code == 401
    body = bob.post("/api/motors/read").json()  # read-only: no helm needed
    assert body["positions"]["PIPS_VERT"] == 132.0
    assert body["positions"]["JOINT_2"] == pytest.approx(25.74494)
    assert set(body["positions"]) >= {"DET_X", "DET_Y", "SOL_VERT", "BOT_VERT", "OPTIC_VERT", "JOINT_6"}
    assert http.app.state.store.get("motors")["positions"]["DET_Y"] == 130.0
    assert observer.get("/api/health").json()["simulation"] is False


def test_sequence_endpoints(http, control, passwords, alice):
    from robotweb.samples import normalise_frame

    from fake_portal import frame

    observer, bob = Browser(http), Browser(http)
    bob.login("bob", passwords["bob"])
    names = [s["name"] for s in observer.get("/api/sequences").json()]
    assert names == ["verify_frames", "measure", "measure_quick", "cleanup"]

    # who may do what
    assert observer.get("/api/sequences/cleanup/plan").status_code == 401
    assert observer.post("/api/sequences/cleanup/start").status_code == 401
    assert bob.post("/api/sequences/cleanup/start").status_code == 403
    for action in ("pause", "resume", "abort"):
        assert bob.post(f"/api/sequence/{action}").status_code == 403
        assert alice.post(f"/api/sequence/{action}").status_code == 409  # nothing is running
    assert alice.post("/api/sequences/launch_rocket/start").status_code == 404

    # a plan that cannot be made explains why
    response = bob.get("/api/sequences/verify_frames/plan")
    assert response.status_code == 409 and "Declare it first" in response.json()["detail"]

    declare_all_empty(alice)
    control.barcodes[(0, 9)] = "KQZ-TBW"
    http.app.state.queue.set_frame(normalise_frame(frame("KQZ-TBW", 0, 9)), ["1" * 24], user="alice")
    plan = bob.get("/api/sequences/verify_frames/plan").json()
    assert len(plan["steps"]) == 4  # the frame, and three lines under it
    assert [len(section["children"]) for section in plan["outline"]] == [3]

    with observer.websocket() as ws:
        ws.receive_json()
        assert alice.post("/api/sequences/verify_frames/start").status_code == 200
        states = []
        while "finished" not in states:
            update = ws.receive_json()
            if "sequence" in update["changes"]:
                states.append(update["changes"]["sequence"]["state"])
        assert states[0] == "running"
    assert sent(control) == ["SAMPLE GET_FRAME 0 9", "SCHEDULER GET_BARCODE", "SAMPLE RETURN_FRAME 0 9"]
    assert http.app.state.queue.items[0]["state"] == "verified"


def test_motion_is_off_by_default(monkeypatch, passwords):
    monkeypatch.delenv("ROBOTWEB_MOTION_COMMANDS_ENABLED", raising=False)
    users = UserStore(get_settings().users_file)
    users.set_password("alice", passwords["alice"], create=True)
    from robotweb.main import app

    with TestClient(app) as http:
        assert http.get("/api/commands").json()["motion_enabled"] is False
        browser = Browser(http)
        browser.login("alice", passwords["alice"])
        browser.post("/api/helm/request")
        declare_all_empty(browser)
        response = browser.post("/api/commands/get_frame", {"args": {"container": 0, "slot": 9}})
        assert response.status_code == 409 and "switched off" in response.json()["detail"]
