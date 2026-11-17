import asyncio

import pytest

from robotweb.control import ControlClient, ControlConnectionError, parse_status
from robotweb.control.fake_server import FakeControlServer


@pytest.fixture
async def server():
    async with FakeControlServer(busy_s=0.2) as srv:
        yield srv


@pytest.fixture
async def client(server):
    c = ControlClient(server.host, server.port, response_timeout_s=2.0)
    yield c
    await c.close()


async def test_status_request(client):
    status = parse_status(await client.request("SAMPLE GET_STATUS"))
    assert status.idle and not status.failed


async def test_multi_line_response(client):
    response = await client.request("SAMPLE GET_SAMPLE_STORAGE_SLOTS")
    assert len(response.lines) == 3


async def test_error_response_is_not_an_exception(client):
    response = await client.request("SAMPLE NO_SUCH_COMMAND")
    assert not response.ok
    assert "unknown command" in response.error


async def test_start_then_poll_until_idle(client):
    assert (await client.request("SAMPLE GET_FRAME 0 0")).ok
    assert parse_status(await client.request("SAMPLE GET_STATUS")).state == "GetFrame"
    # other subsystems stay idle
    assert parse_status(await client.request("MOTOR GET_STATUS")).idle
    await asyncio.sleep(0.3)
    assert parse_status(await client.request("SAMPLE GET_STATUS")).idle


async def test_status_reports_failure(client, server):
    server.fail("SAMPLE", "GRIPPER_FAULT")
    assert parse_status(await client.request("SAMPLE GET_STATUS")).failed


async def test_concurrent_requests_do_not_interleave(client):
    commands = ["SAMPLE GET_STATUS", "SCAN GET_MONO_ENERGY"] * 10
    responses = await asyncio.gather(*(client.request(c) for c in commands))
    for command, response in zip(commands, responses):
        expected = "IDLE NO_ERROR" if command.endswith("GET_STATUS") else "5966.002295"
        assert response.first == expected


async def test_response_split_across_packets():
    # A multi-line response arriving in two pieces must not be cut short.
    async with FakeControlServer(split_delay_s=0.05) as srv:
        client = ControlClient(srv.host, srv.port)
        try:
            response = await client.request("SAMPLE GET_SAMPLE_STORAGE_SLOTS")
            assert len(response.lines) == 3
        finally:
            await client.close()


async def test_reconnects_after_server_restart():
    srv = FakeControlServer()
    await srv.start()
    port = srv.port
    client = ControlClient(srv.host, port, response_timeout_s=1.0)
    try:
        assert (await client.request("SCAN GET_STATUS")).ok
        await srv.stop()
        with pytest.raises(ControlConnectionError):
            await client.request("SCAN GET_STATUS")
        srv = FakeControlServer(port=port)
        await srv.start()
        assert (await client.request("SCAN GET_STATUS")).ok
    finally:
        await client.close()
        await srv.stop()


async def test_unreachable_server():
    client = ControlClient("127.0.0.1", 1, connect_timeout_s=1.0)
    with pytest.raises(ControlConnectionError):
        await client.request("SCAN GET_STATUS")
