"""Waiting a long time for the control server (it may be at a breakpoint)."""

import asyncio
import time

import pytest

from robotweb.control import ControlClient, ControlConnectionError
from robotweb.control.fake_server import FakeControlServer
from robotweb.control.long_wait import KEY, LongWait
from robotweb.state import StateStore

from test_api_commands import alice, control, http, passwords  # noqa: F401  (fixtures)
from test_api_auth import Browser


class Told:
    def __init__(self) -> None:
        self.silent: list[str] = []
        self.answered: list[str] = []

    async def on_silent(self, command: str, seconds: float) -> None:
        self.silent.append(command)

    async def on_answer(self, command: str, seconds: float) -> None:
        self.answered.append(command)


@pytest.fixture
async def slow():
    """A server that takes 0.5 s to answer SCAN POST_XAS_SCAN."""
    async with FakeControlServer(busy_s=0.1) as server:
        server.answer_delay[("SCAN", "POST_XAS_SCAN")] = 0.5
        yield server


def patient(store, told, **limits) -> LongWait:
    return LongWait(store, on_silent=told.on_silent, on_answer=told.on_answer, **limits)


async def test_switched_off_the_usual_wait_applies_and_nobody_is_told(slow):
    store, told = StateStore(), Told()
    wait = patient(store, told, warn_after_s=0.1, max_wait_s=5.0)
    client = ControlClient(slow.host, slow.port, response_timeout_s=0.2, long_wait=wait)
    try:
        assert store.get(KEY)["enabled"] is False
        with pytest.raises(ControlConnectionError):
            await client.request("SCAN POST_XAS_SCAN abc 1 1")
        await asyncio.sleep(0.05)
    finally:
        await client.close()
    assert told.silent == [] and told.answered == []


async def test_switched_on_a_late_answer_is_waited_for_and_the_operators_are_told(slow):
    store, told = StateStore(), Told()
    wait = patient(store, told, warn_after_s=0.15, max_wait_s=5.0)
    wait.set(True)
    client = ControlClient(slow.host, slow.port, response_timeout_s=0.05, long_wait=wait)
    try:
        request = asyncio.ensure_future(client.request("SCAN POST_XAS_SCAN abc 1 1"))
        await asyncio.sleep(0.3)
        waiting = store.get(KEY)["waiting"]
        assert waiting["command"] == "SCAN POST_XAS_SCAN abc 1 1" and told.silent == [waiting["command"]]
        assert told.answered == []

        response = await request
        assert "POST_XAS_SCAN" in response.raw
        await asyncio.sleep(0.05)
        assert told.answered == ["SCAN POST_XAS_SCAN abc 1 1"] and len(told.silent) == 1
        assert store.get(KEY)["waiting"] is None
        # the same connection is still in step
        assert (await client.request("SAMPLE GET_STATUS")).ok and client.connected
        # an answer that comes in time is not reported
        assert len(told.silent) == 1 and len(told.answered) == 1
    finally:
        await client.close()


async def test_the_long_wait_ends_too(slow):
    store, told = StateStore(), Told()
    wait = patient(store, told, warn_after_s=0.1, max_wait_s=0.25)
    wait.set(True)
    client = ControlClient(slow.host, slow.port, response_timeout_s=0.05, long_wait=wait)
    try:
        with pytest.raises(ControlConnectionError, match="no answer in"):
            await client.request("SCAN POST_XAS_SCAN abc 1 1")
        await asyncio.sleep(0.05)
        assert len(told.silent) == 1 and told.answered == []
        assert store.get(KEY)["waiting"] is None
        # the late answer of the old connection does not confuse the next request
        await asyncio.sleep(0.4)
        assert "SAMPLE GET_STATUS" in (await client.request("SAMPLE GET_STATUS")).raw
    finally:
        await client.close()


async def test_a_closed_server_is_noticed_at_once():
    store, told = StateStore(), Told()
    wait = patient(store, told, warn_after_s=5.0, max_wait_s=30.0)
    wait.set(True)
    server = FakeControlServer()
    await server.start()
    server.answer_delay[("SCAN", "POST_XAS_SCAN")] = 20.0
    client = ControlClient(server.host, server.port, long_wait=wait)
    try:
        request = asyncio.ensure_future(client.request("SCAN POST_XAS_SCAN abc 1 1"))
        await asyncio.sleep(0.1)
        await server.stop()
        with pytest.raises(ControlConnectionError):
            await asyncio.wait_for(request, 2.0)
    finally:
        await client.close()
    assert told.silent == []


async def test_addresses_are_not_published():
    store, told = StateStore(), Told()
    wait = patient(store, told)
    wait.silent("SCHEDULER SEND_MESSAGE someone@example.org Subject text", 0.0)
    await asyncio.sleep(0)
    assert store.get(KEY)["waiting"]["command"] == "SCHEDULER SEND_MESSAGE <address> Subject text"
    assert told.silent == ["SCHEDULER SEND_MESSAGE <address> Subject text"]


def test_only_the_operator_at_the_helm_switches_it(http, passwords, alice):  # noqa: F811
    observer, bob = Browser(http), Browser(http)
    bob.login("bob", passwords["bob"])
    assert http.app.state.store.get(KEY)["enabled"] is False
    assert observer.post("/api/long-wait", {"enabled": True}).status_code == 401
    assert bob.post("/api/long-wait", {"enabled": True}).status_code == 403
    assert http.app.state.long_wait.enabled is False

    assert alice.post("/api/long-wait", {"enabled": True}).json() == {"enabled": True}
    entry = http.app.state.store.get(KEY)
    assert entry["enabled"] is True and entry["max_wait_s"] == 3600.0 and entry["warn_after_s"] == 60.0
    assert alice.post("/api/long-wait", {"enabled": False}).json() == {"enabled": False}
    assert http.app.state.store.get(KEY)["enabled"] is False


class Mail:
    """Stands in for the notifier, with a mail server."""

    addresses = ["someone@example.org"]
    smtp = object()
    route = "the mail server"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, subject: str, text: str) -> None:
        self.sent.append((subject, text))


def test_the_operators_get_an_e_mail_when_a_request_waits(http, control, alice):  # noqa: F811
    assert alice.post("/api/queries/mono_energy").status_code == 200
    request = control.requests[-1]
    mail = Mail()
    http.app.state.sequences.notifier = mail
    wait = http.app.state.long_wait
    wait.warn_after_s = 0.2
    control.answer_delay[tuple(request.split()[:2])] = 0.6
    assert alice.post("/api/long-wait", {"enabled": True}).status_code == 200

    assert alice.post("/api/queries/mono_energy").json() == {"lines": ["5966.002295"]}
    time.sleep(0.2)
    subjects = [subject for subject, _ in mail.sent]
    assert subjects == ["BL22HT_Control_server_not_answering", "BL22HT_Control_server_answering_again"]
    assert request in mail.sent[0][1] and "60 min" in mail.sent[0][1]
    assert http.app.state.store.get(KEY)["waiting"] is None
