import asyncio

from robotweb.control import ControlClient
from robotweb.control.fake_server import FakeControlServer
from robotweb.control.poller import StatusPoller
from robotweb.state import StateStore


def make_poller(server_port: int, store: StateStore) -> StatusPoller:
    client = ControlClient("127.0.0.1", server_port, connect_timeout_s=1.0, response_timeout_s=1.0)
    return StatusPoller(client, store, idle_interval_s=1.0, busy_interval_s=0.25, reconnect_delay_s=2.0)


async def test_publishes_every_subsystem():
    async with FakeControlServer() as server:
        store = StateStore()
        poller = make_poller(server.port, store)
        delay = await poller.poll_once()

        assert delay == 1.0  # everything idle
        assert store.get("control")["connected"] is True
        for name in ("MOTOR", "SAMPLE", "BEAMLINE", "SCAN"):
            entry = store.get(f"subsystem.{name}")
            assert entry["connected"] and entry["state"] == "IDLE" and not entry["failed"]
        assert server.requests == [
            "MOTOR GET_STATUS",
            "SAMPLE GET_STATUS",
            "BEAMLINE GET_STATUS",
            "SCAN GET_STATUS",
        ]


async def test_polls_faster_while_busy_and_shows_the_step_name():
    async with FakeControlServer(busy_s=0.2) as server:
        store = StateStore()
        poller = make_poller(server.port, store)
        command = ControlClient("127.0.0.1", server.port)
        try:
            assert (await command.request("SAMPLE GET_FRAME 0 0")).ok
            assert await poller.poll_once() == 0.25
            assert store.get("subsystem.SAMPLE")["state"] == "GetFrame"
            assert store.get("subsystem.MOTOR")["state"] == "IDLE"

            await asyncio.sleep(0.3)
            assert await poller.poll_once() == 1.0
            assert store.get("subsystem.SAMPLE")["state"] == "IDLE"
        finally:
            await command.close()


async def test_reports_a_subsystem_error():
    async with FakeControlServer() as server:
        server.fail("SCAN", "DETECTOR_FAULT")
        store = StateStore()
        await make_poller(server.port, store).poll_once()
        scan = store.get("subsystem.SCAN")
        assert scan["failed"] and scan["error"] == "DETECTOR_FAULT"
        assert not store.get("subsystem.MOTOR")["failed"]


async def test_lost_connection_keeps_last_values_but_marks_them_disconnected():
    server = FakeControlServer()
    await server.start()
    store = StateStore()
    poller = make_poller(server.port, store)
    await poller.poll_once()
    last_ts = store.get("subsystem.SAMPLE")["ts"]

    await server.stop()
    assert await poller.poll_once() == 2.0  # reconnect delay

    assert store.get("control")["connected"] is False
    sample = store.get("subsystem.SAMPLE")
    assert sample["connected"] is False
    assert sample["state"] == "IDLE" and sample["ts"] == last_ts


async def test_recovers_when_the_server_comes_back():
    server = FakeControlServer()
    await server.start()
    port = server.port
    store = StateStore()
    poller = make_poller(port, store)
    await poller.poll_once()
    await server.stop()
    await poller.poll_once()
    assert store.get("control")["connected"] is False

    server = FakeControlServer(port=port)
    await server.start()
    try:
        await poller.poll_once()
        assert store.get("control")["connected"] is True
        assert store.get("subsystem.SAMPLE")["connected"] is True
    finally:
        await server.stop()


async def test_unreachable_server_from_the_start():
    store = StateStore()
    poller = make_poller(1, store)
    assert await poller.poll_once() == 2.0
    assert store.get("control")["connected"] is False
    assert store.get("subsystem.MOTOR") == {
        "connected": False,
        "state": None,
        "error": None,
        "failed": False,
        "ts": store.get("subsystem.MOTOR")["ts"],
    }
