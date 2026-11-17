import json

import pytest

from robotweb.audit import AuditLog
from robotweb.helm import HelmError, HelmManager
from robotweb.state import StateStore


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Setup:
    def __init__(self, tmp_path) -> None:
        self.clock = Clock()
        self.store = StateStore()
        self.audit_path = tmp_path / "audit.log"
        self.helm = HelmManager(
            self.store,
            AuditLog(self.audit_path),
            request_timeout_s=60,
            absence_grace_s=30,
            clock=self.clock,
        )
        self.present = {"alice", "bob", "carol"}

    def tick(self, seconds: float = 0) -> None:
        self.clock.now += seconds
        self.helm.tick(lambda user: user in self.present)

    @property
    def published(self) -> dict:
        return self.store.get("helm")

    def actions(self) -> list[str]:
        return [json.loads(line)["action"] for line in self.audit_path.read_text().splitlines()]


@pytest.fixture
def s(tmp_path) -> Setup:
    return Setup(tmp_path)


def test_nobody_holds_the_helm_at_start(s):
    assert s.helm.holder is None
    assert s.published["holder"] is None and s.published["request"] is None


def test_free_helm_is_granted_at_once(s):
    s.helm.request("alice")
    assert s.helm.is_holder("alice")
    assert s.published["holder"] == "alice"
    assert s.actions() == ["helm_taken"]


def test_request_then_transfer(s):
    s.helm.request("alice")
    s.helm.request("bob")
    assert s.helm.is_holder("alice")  # nothing changes until alice answers
    assert s.published["request"] == {"user": "bob", "deadline": 1060.0}
    s.helm.respond("alice", accept=True)
    assert s.helm.is_holder("bob")
    assert s.published["request"] is None
    assert s.actions() == ["helm_taken", "helm_requested", "helm_transferred"]


def test_request_then_deny(s):
    s.helm.request("alice")
    s.helm.request("bob")
    s.helm.respond("alice", accept=False)
    assert s.helm.is_holder("alice")
    assert s.published["request"] is None
    assert s.actions()[-1] == "helm_denied"
    # denied requests may be repeated
    s.helm.request("bob")
    assert s.helm.requester == "bob"


def test_no_answer_transfers_after_the_deadline(s):
    s.helm.request("alice")
    s.helm.request("bob")
    s.tick(59)
    assert s.helm.is_holder("alice")
    s.tick(1)
    assert s.helm.is_holder("bob")
    assert s.actions()[-1] == "helm_auto_transferred"


def test_only_one_request_can_wait(s):
    s.helm.request("alice")
    s.helm.request("bob")
    with pytest.raises(HelmError, match="bob is already waiting"):
        s.helm.request("carol")
    with pytest.raises(HelmError, match="already waiting"):
        s.helm.request("bob")


def test_wrong_person_cannot_act(s):
    s.helm.request("alice")
    with pytest.raises(HelmError):
        s.helm.request("alice")  # already has control
    with pytest.raises(HelmError):
        s.helm.respond("alice", accept=True)  # nobody waiting
    s.helm.request("bob")
    with pytest.raises(HelmError):
        s.helm.respond("bob", accept=True)  # the requester cannot accept for the holder
    with pytest.raises(HelmError):
        s.helm.release("bob")
    with pytest.raises(HelmError):
        s.helm.cancel("carol")
    assert s.helm.is_holder("alice") and s.helm.requester == "bob"


def test_requester_can_cancel(s):
    s.helm.request("alice")
    s.helm.request("bob")
    s.helm.cancel("bob")
    assert s.published["request"] is None
    s.tick(120)
    assert s.helm.is_holder("alice")


def test_release_frees_the_helm(s):
    s.helm.request("alice")
    s.helm.release("alice")
    assert s.helm.holder is None
    assert s.published["holder"] is None


def test_release_with_a_waiting_request_hands_over(s):
    s.helm.request("alice")
    s.helm.request("bob")
    s.helm.release("alice")
    assert s.helm.is_holder("bob")
    assert s.published["request"] is None


def test_logout_releases_and_cancels(s):
    s.helm.request("alice")
    s.helm.request("bob")
    s.helm.user_left("bob")
    assert s.helm.requester is None
    s.helm.user_left("alice")
    assert s.helm.holder is None
    s.helm.user_left("carol")  # no part in the helm: nothing happens


def test_absent_holder_loses_the_helm_after_the_grace_period(s):
    s.helm.request("alice")
    s.present.discard("alice")
    s.tick(0)
    s.tick(29)
    assert s.helm.is_holder("alice")
    s.tick(1)
    assert s.helm.holder is None
    assert s.actions()[-1] == "helm_freed_holder_absent"


def test_holder_who_returns_in_time_keeps_the_helm(s):
    s.helm.request("alice")
    s.present.discard("alice")
    s.tick(0)
    s.tick(20)
    s.present.add("alice")
    s.tick(1)
    s.present.discard("alice")
    s.tick(0)
    s.tick(20)  # the grace period started again
    assert s.helm.is_holder("alice")


def test_absent_holder_with_a_waiting_request_hands_over(s):
    s.helm.request("alice")
    s.helm.request("bob")
    s.present.discard("alice")
    s.tick(0)
    s.tick(30)
    assert s.helm.is_holder("bob")


def test_control_never_goes_to_a_requester_who_left(s):
    s.helm.request("alice")
    s.helm.request("bob")
    s.present.discard("bob")
    s.tick(1)
    assert s.helm.requester is None
    s.tick(120)
    assert s.helm.is_holder("alice")
