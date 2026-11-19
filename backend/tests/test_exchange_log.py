"""The log of the raw exchange with the control server."""

import pytest

from robotweb.control import ControlClient, ControlConnectionError
from robotweb.control.exchange_log import KEY, ExchangeLog
from robotweb.control.fake_server import FakeControlServer
from robotweb.state import StateStore


@pytest.fixture
async def server():
    async with FakeControlServer(busy_s=0.2) as srv:
        yield srv


def lines(path):
    # without the date and time in front
    return [line.split(" ", 2)[2] for line in path.read_text(encoding="utf-8").splitlines()]


async def test_nothing_is_written_until_it_is_switched_on(tmp_path, server):
    store = StateStore()
    exchange = ExchangeLog(tmp_path / "exchange", store)
    client = ControlClient(server.host, server.port, name="command", exchange=exchange)
    try:
        await client.request("SAMPLE GET_STATUS")
        assert not (tmp_path / "exchange").exists() and store.get(KEY)["enabled"] is False

        path = exchange.start()
        assert exchange.start() == path  # on already: the same file
        await client.request("SAMPLE GET_STATUS")
        await client.request("SAMPLE NO_SUCH_COMMAND")
        entry = store.get(KEY)
        assert entry["enabled"] is True and entry["file"] == path.name

        exchange.stop()
        await client.request("MOTOR GET_STATUS")
    finally:
        await client.close()
    written = lines(path)
    assert written[:2] == ["command > SAMPLE GET_STATUS", "command < SUCCESS SAMPLE GET_STATUS | IDLE NO_ERROR"]
    assert written[2] == "command > SAMPLE NO_SUCH_COMMAND" and written[3].startswith("command < ERROR")
    assert len(written) == 4 and store.get(KEY)["enabled"] is False


async def test_a_request_without_an_answer_is_logged(tmp_path):
    exchange = ExchangeLog(tmp_path)
    path = exchange.start()
    server = FakeControlServer()
    await server.start()
    client = ControlClient(
        server.host, server.port, connect_timeout_s=1.0, response_timeout_s=1.0, name="status", exchange=exchange
    )
    try:
        assert (await client.request("SCAN GET_STATUS")).ok
        await server.stop()
        with pytest.raises(ControlConnectionError):
            await client.request("SCAN GET_STATUS")
    finally:
        await client.close()
    written = lines(path)
    assert written[2] == "status  > SCAN GET_STATUS" and written[3].startswith("status  ! ")


def test_addresses_are_masked_and_the_size_is_limited(tmp_path):
    store = StateStore()
    exchange = ExchangeLog(tmp_path, store, max_bytes=300)
    path = exchange.start()
    exchange.sent("command", "SCHEDULER SEND_MESSAGE someone@example.org Subject text")
    assert lines(path) == ["command > SCHEDULER SEND_MESSAGE <address> Subject text"]
    for _ in range(10):
        exchange.received("status", b"SUCCESS SAMPLE GET_STATUS \rIDLE NO_ERROR\r\n\r\n")
    assert not exchange.enabled
    assert "Switched off by itself" in store.get(KEY)["note"]
    assert path.stat().st_size < 600
