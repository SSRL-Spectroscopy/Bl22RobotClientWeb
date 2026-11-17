import json
import secrets

import pytest

from robotweb.auth import LoginThrottle, Sessions, UserError, UserStore


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def password() -> str:
    return secrets.token_urlsafe(12)


@pytest.fixture
def users(tmp_path) -> UserStore:
    return UserStore(tmp_path / "users.json")


def test_add_and_verify(users, password):
    users.set_password("alice", password, create=True)
    assert users.names() == ["alice"]
    assert users.verify("alice", password)
    assert not users.verify("alice", password + "x")
    assert not users.verify("nobody", password)


def test_password_is_stored_only_as_a_hash(users, password, tmp_path):
    users.set_password("alice", password, create=True)
    text = (tmp_path / "users.json").read_text()
    assert password not in text
    assert json.loads(text)["users"]["alice"]["hash"].startswith("$argon2")


def test_rules(users, password):
    with pytest.raises(UserError):
        users.set_password("bad name", password, create=True)
    with pytest.raises(UserError):
        users.set_password("alice", "short", create=True)
    users.set_password("alice", password, create=True)
    with pytest.raises(UserError):
        users.set_password("alice", password, create=True)  # already exists
    with pytest.raises(UserError):
        users.set_password("bob", password, create=False)  # does not exist
    with pytest.raises(UserError):
        users.remove("bob")


def test_change_password_and_remove(users, password):
    users.set_password("alice", password, create=True)
    new = secrets.token_urlsafe(12)
    users.set_password("alice", new, create=False)
    assert users.verify("alice", new) and not users.verify("alice", password)
    users.remove("alice")
    assert not users.verify("alice", new)


def test_changes_by_another_process_are_picked_up(tmp_path, password):
    path = tmp_path / "users.json"
    backend_view = UserStore(path)
    assert not backend_view.exists("alice")
    UserStore(path).set_password("alice", password, create=True)  # the command-line tool
    assert backend_view.exists("alice")


def test_session_lifetime(users, password):
    users.set_password("alice", password, create=True)
    clock = Clock()
    sessions = Sessions(users, lifetime_s=100, clock=clock)
    session = sessions.create("alice")
    assert sessions.get(session.token).user == "alice"
    assert sessions.get("made-up-token") is None
    assert sessions.get(None) is None
    clock.now += 101
    assert sessions.get(session.token) is None
    assert not sessions.user_has_session("alice")


def test_session_ends_when_the_account_is_removed(users, password):
    users.set_password("alice", password, create=True)
    sessions = Sessions(users, lifetime_s=100)
    session = sessions.create("alice")
    users.remove("alice")
    assert sessions.get(session.token) is None


def test_logout_ends_only_that_session(users, password):
    users.set_password("alice", password, create=True)
    sessions = Sessions(users, lifetime_s=100)
    first, second = sessions.create("alice"), sessions.create("alice")
    sessions.end(first.token)
    assert sessions.get(first.token) is None
    assert sessions.get(second.token) is not None
    assert sessions.user_has_session("alice")


def test_throttle_locks_after_repeated_failures():
    clock = Clock()
    throttle = LoginThrottle(max_failures=3, window_s=300, lock_s=60, clock=clock)
    for _ in range(2):
        throttle.failed("Alice")
    assert throttle.locked_for("alice") == 0
    throttle.failed("alice")  # case-insensitive, third failure
    assert throttle.locked_for("ALICE") == 60
    assert throttle.locked_for("bob") == 0
    clock.now += 61
    assert throttle.locked_for("alice") == 0


def test_throttle_resets_on_success_and_forgets_old_failures():
    clock = Clock()
    throttle = LoginThrottle(max_failures=3, window_s=300, lock_s=60, clock=clock)
    throttle.failed("alice")
    throttle.failed("alice")
    throttle.succeeded("alice")
    throttle.failed("alice")
    throttle.failed("alice")
    assert throttle.locked_for("alice") == 0
    clock.now += 301  # the two failures age out
    throttle.failed("alice")
    assert throttle.locked_for("alice") == 0
