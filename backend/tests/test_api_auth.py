import json
import secrets

import pytest
from fastapi.testclient import TestClient

from robotweb.api_auth import COOKIE_NAME
from robotweb.auth import UserStore
from robotweb.config import get_settings


class Browser:
    """One browser's view of the app: its own cookie, shared backend."""

    def __init__(self, http: TestClient) -> None:
        self._http = http
        self.cookie: str | None = None

    def _headers(self) -> dict:
        return {"Cookie": f"{COOKIE_NAME}={self.cookie}"} if self.cookie else {}

    def get(self, path: str):
        return self._http.get(path, headers=self._headers())

    def post(self, path: str, body: dict | None = None, **headers):
        response = self._http.post(path, json=body or {}, headers={**self._headers(), **headers})
        self._http.cookies.clear()  # keep the shared client free of anyone's cookie
        return response

    def login(self, username: str, password: str):
        response = self.post("/api/login", {"username": username, "password": password})
        if response.status_code == 200:
            self.cookie = response.cookies.get(COOKIE_NAME) or _cookie_from(response)
        return response

    def websocket(self):
        return self._http.websocket_connect("/api/ws", headers=self._headers())


def _cookie_from(response) -> str:
    return response.headers["set-cookie"].split(";")[0].split("=", 1)[1]


@pytest.fixture
def passwords() -> dict[str, str]:
    return {name: secrets.token_urlsafe(12) for name in ("alice", "bob")}


@pytest.fixture
def http(passwords):
    users = UserStore(get_settings().users_file)
    for name, password in passwords.items():
        users.set_password(name, password, create=True)
    from robotweb.main import app

    with TestClient(app) as client:
        yield client


def audit_actions() -> list[str]:
    lines = get_settings().audit_file.read_text().splitlines()
    return [json.loads(line)["action"] for line in lines]


def test_observer_needs_no_login(http):
    observer = Browser(http)
    assert observer.get("/api/me").json() == {"user": None, "role": "observer"}
    assert observer.get("/api/health").status_code == 200
    with observer.websocket() as ws:
        assert ws.receive_json()["state"]["helm"]["holder"] is None


def test_observer_cannot_touch_the_helm(http):
    observer = Browser(http)
    for path in ("/api/helm/request", "/api/helm/release", "/api/helm/cancel"):
        assert observer.post(path).status_code == 401
    assert observer.post("/api/helm/respond", {"accept": True}).status_code == 401


def test_login_sets_a_protected_cookie(http, passwords):
    alice = Browser(http)
    response = alice.login("alice", passwords["alice"])
    assert response.status_code == 200
    assert response.json() == {"user": "alice", "role": "operator"}
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert alice.get("/api/me").json()["user"] == "alice"


def test_wrong_password_and_unknown_user_look_the_same(http, passwords):
    browser = Browser(http)
    wrong = browser.login("alice", "not-the-password")
    unknown = browser.login("mallory", "not-the-password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert browser.get("/api/me").json()["user"] is None


def test_repeated_failures_lock_the_account(http, passwords):
    browser = Browser(http)
    for _ in range(5):
        assert browser.login("alice", "wrong-password").status_code == 401
    # even the right password is refused while locked
    assert browser.login("alice", passwords["alice"]).status_code == 429
    assert "login_refused_locked" in audit_actions()


def test_logout_ends_the_session(http, passwords):
    alice = Browser(http)
    alice.login("alice", passwords["alice"])
    assert alice.post("/api/logout").status_code == 200
    assert alice.get("/api/me").json()["user"] is None  # the old cookie is dead
    assert alice.post("/api/helm/request").status_code == 401


def test_cross_site_post_is_refused(http, passwords):
    browser = Browser(http)
    response = browser.post(
        "/api/login",
        {"username": "alice", "password": passwords["alice"]},
        Origin="http://evil.example",
    )
    assert response.status_code == 403


def test_helm_flow_between_two_operators(http, passwords):
    alice, bob = Browser(http), Browser(http)
    alice.login("alice", passwords["alice"])
    bob.login("bob", passwords["bob"])
    helm = http.app.state.helm

    assert alice.post("/api/helm/request").status_code == 200
    assert helm.holder == "alice"

    assert bob.post("/api/helm/request").status_code == 200
    assert helm.holder == "alice" and helm.requester == "bob"

    # only the holder can answer
    assert bob.post("/api/helm/respond", {"accept": True}).status_code == 409
    assert alice.post("/api/helm/respond", {"accept": False}).status_code == 200
    assert helm.holder == "alice" and helm.requester is None

    assert bob.post("/api/helm/request").status_code == 200
    assert alice.post("/api/helm/respond", {"accept": True}).status_code == 200
    assert helm.holder == "bob"

    assert alice.post("/api/helm/release").status_code == 409
    assert bob.post("/api/helm/release").status_code == 200
    assert helm.holder is None

    assert audit_actions()[-6:] == [
        "helm_taken",
        "helm_requested",
        "helm_denied",
        "helm_requested",
        "helm_transferred",
        "helm_released",
    ]


def test_helm_state_is_pushed_to_observers(http, passwords):
    alice, observer = Browser(http), Browser(http)
    alice.login("alice", passwords["alice"])
    with observer.websocket() as ws:
        ws.receive_json()  # snapshot
        alice.post("/api/helm/request")
        while True:
            update = ws.receive_json()
            if "helm" in update["changes"]:
                break
        assert update["changes"]["helm"]["holder"] == "alice"


def test_logout_releases_the_helm(http, passwords):
    alice = Browser(http)
    alice.login("alice", passwords["alice"])
    alice.post("/api/helm/request")
    alice.post("/api/logout")
    assert http.app.state.helm.holder is None


def test_logout_in_one_browser_keeps_the_helm_if_logged_in_elsewhere(http, passwords):
    desk, laptop = Browser(http), Browser(http)
    desk.login("alice", passwords["alice"])
    laptop.login("alice", passwords["alice"])
    desk.post("/api/helm/request")
    laptop.post("/api/logout")
    assert http.app.state.helm.holder == "alice"


def test_open_page_of_a_logged_in_operator_counts_as_present(http, passwords):
    alice, observer = Browser(http), Browser(http)
    alice.login("alice", passwords["alice"])
    presence = http.app.state.presence
    assert not presence.has_page("alice")
    with alice.websocket() as ws:
        ws.receive_json()
        assert presence.has_page("alice")
        with observer.websocket() as other:
            other.receive_json()
            assert presence.has_page("alice")
    # leaving is registered once the server notices the closed socket
    import time

    deadline = time.time() + 5
    while presence.has_page("alice") and time.time() < deadline:
        time.sleep(0.1)
    assert not presence.has_page("alice")


def test_require_helm_guard(http, passwords):
    """The check that step 4's command endpoints will use."""
    from fastapi import HTTPException

    from robotweb.api_auth import require_helm

    class FakeRequest:
        def __init__(self, cookie):
            self.app = http.app
            self.cookies = {COOKIE_NAME: cookie} if cookie else {}

    alice, bob = Browser(http), Browser(http)
    alice.login("alice", passwords["alice"])
    bob.login("bob", passwords["bob"])
    alice.post("/api/helm/request")

    assert require_helm(FakeRequest(alice.cookie)).user == "alice"
    with pytest.raises(HTTPException) as not_holder:
        require_helm(FakeRequest(bob.cookie))
    assert not_holder.value.status_code == 403
    with pytest.raises(HTTPException) as not_logged_in:
        require_helm(FakeRequest(None))
    assert not_logged_in.value.status_code == 401
