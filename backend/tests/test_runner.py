import asyncio
import json

import pytest

from robotweb.audit import AuditLog
from robotweb.commands import CommandRefused
from robotweb.control import ControlClient, ControlConnectionError
from robotweb.control.fake_server import FakeControlServer
from robotweb.equipment import FILTER, FRAME, REFERENCE, EquipmentState
from robotweb.runner import CommandRunner
from robotweb.state import StateStore


class Rig:
    def __init__(self, tmp_path, server: FakeControlServer, *, motion_enabled: bool = True) -> None:
        self.server = server
        self.store = StateStore()
        self.audit_path = tmp_path / "audit.log"
        audit = AuditLog(self.audit_path)
        self.equipment = EquipmentState(tmp_path / "equipment.json", self.store, audit)
        self.runner = CommandRunner(
            ControlClient("127.0.0.1", server.port, connect_timeout_s=1, response_timeout_s=1),
            self.store,
            self.equipment,
            audit,
            motion_enabled=motion_enabled,
            settle_s=0.02,
            poll_s=0.03,
            max_poll_failures=3,
        )
        for item in (FRAME, REFERENCE, FILTER):
            self.equipment.declare(item, None, user="alice")

    @property
    def command(self) -> dict:
        return self.store.get("command")

    async def finished(self) -> dict:
        for _ in range(200):
            if not self.runner.busy:
                return self.command
            await asyncio.sleep(0.02)
        raise AssertionError("command did not finish")

    def actions(self) -> list[str]:
        return [json.loads(line)["action"] for line in self.audit_path.read_text().splitlines()]

    def sent(self) -> list[str]:
        """Everything sent except the read-only status and slot requests."""
        return [
            r for r in self.server.requests if not r.endswith(("GET_STATUS", "GET_SAMPLE_STORAGE_SLOTS"))
        ]


@pytest.fixture
async def rig(tmp_path):
    async with FakeControlServer(busy_s=0.15) as server:
        rig = Rig(tmp_path, server)
        yield rig
        await rig.runner.stop()


async def test_successful_command_updates_the_equipment_state(rig):
    await rig.runner.start("get_frame", {"container": 0, "slot": 9}, user="alice")
    assert rig.runner.busy
    assert rig.command["phase"] == "running" and rig.command["text"] == "SAMPLE GET_FRAME 0 9"
    assert not rig.equipment.is_known(FRAME)  # changing while the robot moves

    done = await rig.finished()
    assert done["phase"] == "succeeded"
    assert rig.equipment.value(FRAME) == {"container": 0, "slot": 9}
    assert rig.sent() == ["SAMPLE GET_FRAME 0 9"]
    assert rig.actions()[-2:] == ["command_started", "command_succeeded"]


async def test_step_name_is_shown_while_running(rig):
    await rig.runner.start("get_frame", {"container": 0, "slot": 9}, user="alice")
    await asyncio.sleep(0.08)
    assert rig.command["step"] == "GetFrame"
    await rig.finished()
    assert rig.command["step"] is None


async def test_full_round_trip_in_the_c_sharp_order(rig):
    for name, args in [
        ("get_reference", {"name": "Co"}),
        ("get_filter", {"name": "Z-1"}),
        ("get_frame", {"container": 0, "slot": 9}),
        ("return_frame", {}),
        ("return_reference", {}),
        ("return_filter", {}),
    ]:
        await rig.runner.start(name, args, user="alice")
        assert (await rig.finished())["phase"] == "succeeded"
    assert rig.sent() == [
        "SAMPLE GET_REF Co",
        "SAMPLE GET_FILTER Z-1",
        "SAMPLE GET_FRAME 0 9",
        "SAMPLE RETURN_FRAME 0 9",
        "SAMPLE RETURN_REF Co",
        "SAMPLE RETURN_FILTER",
    ]
    assert all(rig.equipment.value(item) is None for item in (FRAME, REFERENCE, FILTER))


async def test_only_one_command_at_a_time(rig):
    await rig.runner.start("get_reference", {"name": "Co"}, user="alice")
    with pytest.raises(CommandRefused, match="still running"):
        await rig.runner.start("get_filter", {"name": "Z-1"}, user="alice")
    await rig.finished()
    assert rig.sent() == ["SAMPLE GET_REF Co"]


async def test_two_simultaneous_requests_send_only_one_command(rig):
    results = await asyncio.gather(
        rig.runner.start("get_frame", {"container": 0, "slot": 1}, user="alice"),
        rig.runner.start("get_frame", {"container": 0, "slot": 2}, user="alice"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, CommandRefused) for r in results) == 1
    await rig.finished()
    assert len(rig.sent()) == 1


async def test_precondition_failure_sends_nothing(rig):
    with pytest.raises(CommandRefused, match="no frame on the beam"):
        await rig.runner.start("return_frame", {}, user="alice")
    assert rig.sent() == []
    assert not rig.runner.busy
    assert rig.equipment.is_known(FRAME)


async def test_frame_is_not_fetched_from_an_empty_slot(rig):
    # the fake server reports container 0 as "1 1 1 1 0 0 0 1 1 1 1 0"
    with pytest.raises(CommandRefused, match="slot 4 is empty"):
        await rig.runner.start("get_frame", {"container": 0, "slot": 4}, user="alice")
    assert rig.sent() == []
    assert not rig.runner.busy
    assert rig.equipment.is_known(FRAME) and rig.equipment.value(FRAME) is None


async def test_busy_subsystem_refuses_the_command(rig):
    other = ControlClient("127.0.0.1", rig.server.port)
    try:
        await other.request("SAMPLE PLACE A1")  # someone else's process
        with pytest.raises(CommandRefused, match="SAMPLE is busy"):
            await rig.runner.start("get_frame", {"container": 0, "slot": 9}, user="alice")
        assert rig.sent() == ["SAMPLE PLACE A1"]
    finally:
        await other.close()


async def test_command_refused_by_the_server_keeps_the_old_state(rig):
    rig.server.refuse.add(("SAMPLE", "GET_FRAME"))
    with pytest.raises(CommandRefused, match="control server refused"):
        await rig.runner.start("get_frame", {"container": 0, "slot": 9}, user="alice")
    assert not rig.runner.busy
    assert rig.equipment.is_known(FRAME) and rig.equipment.value(FRAME) is None
    assert rig.command["phase"] == "failed"
    assert rig.actions()[-1] == "command_failed"


async def test_error_during_the_process_makes_the_item_unknown(rig):
    await rig.runner.start("get_frame", {"container": 0, "slot": 9}, user="alice")
    rig.server.fail("SAMPLE", "GRIPPER_FAULT")
    done = await rig.finished()
    assert done["phase"] == "failed" and "GRIPPER_FAULT" in done["detail"]
    assert not rig.equipment.is_known(FRAME)
    assert rig.equipment.is_known(REFERENCE)  # only the affected item is doubted
    # and nothing more can be done with the frame until an operator declares it
    with pytest.raises(CommandRefused, match="Declare"):
        await rig.runner.start("get_frame", {"container": 0, "slot": 9}, user="alice")


async def test_losing_the_server_mid_command_makes_the_item_unknown(rig):
    await rig.runner.start("get_frame", {"container": 0, "slot": 9}, user="alice")
    await rig.server.stop()
    done = await rig.finished()
    assert done["phase"] == "failed" and "Lost contact" in done["detail"]
    assert not rig.equipment.is_known(FRAME)


async def test_unreachable_server_sends_nothing_and_keeps_the_state(rig):
    await rig.server.stop()
    with pytest.raises(ControlConnectionError):
        await rig.runner.start("get_frame", {"container": 0, "slot": 9}, user="alice")
    assert not rig.runner.busy
    assert rig.equipment.is_known(FRAME)


async def test_motion_can_be_switched_off(tmp_path):
    async with FakeControlServer() as server:
        rig = Rig(tmp_path, server, motion_enabled=False)
        with pytest.raises(CommandRefused, match="switched off"):
            await rig.runner.start("get_frame", {"container": 0, "slot": 9}, user="alice")
        assert server.requests == []  # refused before anything is sent
        # read-only queries still work
        assert len(await rig.runner.query("storage_slots")) == 3
        assert rig.store.get("storage_slots")["lines"][0].startswith("1 1 1 1")
        await rig.runner.stop()


async def test_queries(rig):
    assert await rig.runner.query("mono_energy") == ["5966.002295"]
    with pytest.raises(CommandRefused):
        await rig.runner.query("slit_gap")  # the fake server answers ERROR
    with pytest.raises(CommandRefused):
        await rig.runner.query("no_such_query")


async def test_read_motor_positions_and_robot_joints(rig):
    positions = await rig.runner.read_positions({"DET_X", "DET_Y", "JOINT_1", "JOINT_2"})
    # joints come as a set of six, whichever were asked for
    assert positions["DET_X"] == 0.0 and positions["DET_Y"] == 130.0
    assert positions["JOINT_1"] == 90.0 and positions["JOINT_6"] == 0.0
    assert "MOTOR GET_POSITION DET_X" in rig.server.requests
    assert rig.server.requests.count("ROBOT GET_JOINTS") == 1

    only_motor = await rig.runner.read_positions({"BOT_VERT"})
    assert only_motor == {"BOT_VERT": 209.0}

    from robotweb.runner import CommandFailed

    with pytest.raises(CommandFailed):
        await rig.runner.read_positions({"NO_SUCH_MOTOR"})


async def test_live_positions_feed_the_rule_check(rig):
    from pathlib import Path

    from robotweb.motor_rules import MotorRules

    rules = MotorRules.load(Path(__file__).resolve().parents[2] / "config" / "motor_rules")
    positions = await rig.runner.read_positions(rules.needed_for("DET_X"))
    # At rest (as read from the real system) the detector may not move
    # sideways: BOT_VERT is at 209 and the robot is parked at joint 1 = 90.
    problems = rules.check_move("DET_X", 200, positions)
    assert any("BOT_VERT is at 209" in p for p in problems)
    assert any("JOINT_1 is at 90" in p for p in problems)


async def test_the_requests_of_a_command_show_the_subsystem_and_the_last_answer(rig):
    control = rig.store.get("control")
    assert control["connected"] is None and control["last_answer"] is None
    assert rig.store.get("subsystem.SAMPLE") is None  # nothing is polled

    await rig.runner.start("get_reference", {"name": "Co"}, user="alice")
    assert rig.store.get("control")["connected"] is True
    seen = set()
    while rig.runner.busy:
        seen.add(rig.store.get("subsystem.SAMPLE")["state"])
        await asyncio.sleep(0.01)
    assert seen - {"IDLE"}  # the running step was shown
    sample = rig.store.get("subsystem.SAMPLE")
    assert sample["state"] == "IDLE" and not sample["failed"]
    assert sample["ts"] <= rig.store.get("control")["last_answer"]
    # only the subsystem of the command was asked
    assert rig.store.get("subsystem.MOTOR") is None
    assert all(r.startswith("SAMPLE") for r in rig.server.requests)


async def test_activity_started_elsewhere_is_not_noticed(rig):
    other = ControlClient("127.0.0.1", rig.server.port)
    try:
        await other.request("SAMPLE GET_FRAME 1 1")  # e.g. the older C# client
        await asyncio.sleep(0.1)
        assert rig.equipment.is_known(FRAME)
        assert "foreign_activity" not in rig.actions()
    finally:
        await other.close()


async def test_a_control_server_that_is_away_is_shown_with_the_last_answer(rig):
    await rig.runner.ask("SAMPLE LIST_REF")
    last = rig.store.get("control")["last_answer"]
    assert last is not None
    await rig.server.stop()
    with pytest.raises(ControlConnectionError):
        await rig.runner.ask("SAMPLE LIST_REF")
    control = rig.store.get("control")
    assert control["connected"] is False and control["last_answer"] == last and control["detail"]
