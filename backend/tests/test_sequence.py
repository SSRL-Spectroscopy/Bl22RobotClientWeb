import asyncio
import json

import pytest

from robotweb.audit import AuditLog
from robotweb.commands import CommandRefused
from robotweb.control import ControlClient
from robotweb.control.fake_server import FakeControlServer
from robotweb.equipment import FILTER, FRAME, REFERENCE, EquipmentState
from robotweb.mqueue import MeasurementQueue
from robotweb.runner import CommandRunner
from robotweb.samples import normalise_frame
from robotweb.sequence import SequenceEngine, SequenceError
from robotweb.state import StateStore

from fake_portal import frame

M1 = "1" * 24
# Two frames in slots the fake control server reports as occupied.
FRAMES = [("KQZ-TBW", 0, 9), ("ABC-DEF", 1, 0)]


class Rig:
    def __init__(self, tmp_path, server: FakeControlServer, *, motion_enabled: bool = True) -> None:
        self.server = server
        self.tmp_path = tmp_path
        self.store = StateStore()
        audit = AuditLog(tmp_path / "audit.log")
        self.equipment = EquipmentState(tmp_path / "equipment.json", self.store, audit)
        self.runner = CommandRunner(
            ControlClient("127.0.0.1", server.port, connect_timeout_s=1, response_timeout_s=1),
            self.store,
            self.equipment,
            audit,
            motion_enabled=motion_enabled,
            settle_s=0.02,
            poll_s=0.02,
            max_poll_failures=3,
        )
        self.queue = MeasurementQueue(tmp_path / "queue.json", self.store, audit)
        self.engine = SequenceEngine(self.runner, self.equipment, self.queue, self.store, audit, tmp_path / "runs")
        for item in (FRAME, REFERENCE, FILTER):
            self.equipment.declare(item, None, user="alice")
        for barcode, container, slot in FRAMES:
            self.queue.set_frame(normalise_frame(frame(barcode, container, slot)), [M1], user="alice")
            server.barcodes[(container, slot)] = barcode

    @property
    def status(self) -> dict:
        return self.store.get("sequence")

    async def until(self, *states: str) -> dict:
        for _ in range(500):
            if self.status["state"] in states:
                return self.status
            await asyncio.sleep(0.01)
        raise AssertionError(f"sequence stayed in {self.status['state']}")

    async def until_sent(self, text: str) -> None:
        for _ in range(500):
            if text in self.server.requests:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"{text!r} was never sent")

    def sent(self) -> list[str]:
        return [
            r for r in self.server.requests if not r.endswith(("GET_STATUS", "GET_SAMPLE_STORAGE_SLOTS"))
        ]

    def log_text(self) -> str:
        return "\n".join(entry["text"] for entry in self.status["log"])

    def queue_states(self) -> list[str]:
        return [item["state"] for item in self.queue.items]


@pytest.fixture
async def rig(tmp_path):
    async with FakeControlServer(busy_s=0.1) as server:
        rig = Rig(tmp_path, server)
        yield rig
        await rig.engine.stop()
        await rig.runner.stop()


VERIFY_BOTH = [
    "SAMPLE GET_FRAME 0 9",
    "SCHEDULER GET_BARCODE",
    "SAMPLE RETURN_FRAME 0 9",
    "SAMPLE GET_FRAME 1 0",
    "SCHEDULER GET_BARCODE",
    "SAMPLE RETURN_FRAME 1 0",
]


async def test_plan_lists_the_steps_before_anything_moves(rig):
    steps = rig.engine.plan("verify_frames")
    assert len(steps) == 8  # per frame: a heading and three lines
    assert steps[0] == "Frame KQZ-TBW in container 0, slot 9"
    assert steps[1] == "  Get the frame from container 0, slot 9"
    assert steps[2] == "  Read its barcode and compare it with KQZ-TBW"
    assert rig.sent() == []


async def test_verify_all_frames(rig):
    rig.engine.start("verify_frames", user="alice")
    assert rig.status["state"] == "running" and rig.status["user"] == "alice"
    done = await rig.until("finished", "failed", "aborted")
    assert done["state"] == "finished"
    assert rig.sent() == VERIFY_BOTH
    assert rig.queue_states() == ["verified", "verified"]
    assert rig.equipment.value(FRAME) is None
    assert "Barcode KQZ-TBW matches the portal" in rig.log_text()

    # the run log is also a file
    files = list((rig.tmp_path / "runs").glob("*_verify_frames.log"))
    assert len(files) == 1
    assert "Barcode ABC-DEF matches the portal" in files[0].read_text()
    actions = [json.loads(line)["action"] for line in (rig.tmp_path / "audit.log").read_text().splitlines()]
    assert "sequence_started" in actions and actions[-1] == "sequence_finished"


async def test_wrong_frame_in_a_slot_is_reported_and_returned(rig):
    rig.server.barcodes[(0, 9)] = "XYZ-XYZ"
    del rig.server.barcodes[(1, 0)]  # no tag readable on the second frame
    rig.engine.start("verify_frames", user="alice")
    assert (await rig.until("finished", "failed"))["state"] == "finished"
    assert rig.sent() == VERIFY_BOTH  # both frames went back to their slots
    assert rig.queue_states() == ["mismatch", "mismatch"]
    assert "carries XYZ-XYZ, but the portal lists KQZ-TBW" in rig.log_text()
    assert "No barcode could be read" in rig.log_text()
    assert rig.store.get("queue")["items"][0]["note"] == "the frame in this slot carries XYZ-XYZ"


async def test_pause_takes_effect_between_commands(rig):
    rig.engine.start("verify_frames", user="alice")
    await rig.until_sent("SAMPLE GET_FRAME 0 9")
    with pytest.raises(CommandRefused, match="A sequence is running"):
        await rig.runner.start("get_reference", {"name": "Co"}, user="alice")

    rig.engine.pause(user="alice")
    assert rig.status["state"] == "running" and rig.status["pause_requested"]  # the command finishes first
    await rig.until("paused")
    assert rig.equipment.value(FRAME) == {"container": 0, "slot": 9}  # the running command completed
    assert rig.sent() == ["SAMPLE GET_FRAME 0 9"]
    assert not rig.runner.sequence_running  # commands by hand are possible now

    await asyncio.sleep(0.15)
    assert rig.sent() == ["SAMPLE GET_FRAME 0 9"]  # nothing happens while paused

    rig.engine.resume(user="alice")
    assert (await rig.until("finished", "failed"))["state"] == "finished"
    assert rig.sent() == VERIFY_BOTH


async def test_abort_leaves_everything_in_place_and_cleanup_is_separate(rig):
    rig.engine.start("verify_frames", user="alice")
    await rig.until_sent("SAMPLE GET_FRAME 0 9")
    rig.engine.abort(user="alice")
    done = await rig.until("aborted", "finished", "failed")
    assert done["state"] == "aborted"
    assert rig.sent() == ["SAMPLE GET_FRAME 0 9"]  # the command finished, nothing followed
    assert rig.equipment.value(FRAME) == {"container": 0, "slot": 9}  # the robot still holds the frame

    await asyncio.sleep(0.15)
    assert rig.sent() == ["SAMPLE GET_FRAME 0 9"]  # no automatic cleanup

    assert rig.engine.plan("cleanup") == [
        "Retract and park the SDD detector, if it is not parked",
        "Return the frame to container 0, slot 9",
    ]
    rig.engine.start("cleanup", user="alice")
    assert (await rig.until("finished", "failed"))["state"] == "finished"
    assert rig.sent()[-1] == "SAMPLE RETURN_FRAME 0 9"
    assert rig.equipment.value(FRAME) is None


async def test_abort_while_paused(rig):
    rig.engine.start("verify_frames", user="alice")
    await rig.until_sent("SAMPLE GET_FRAME 0 9")
    rig.engine.pause(user="alice")
    await rig.until("paused")
    rig.engine.abort(user="alice")
    assert (await rig.until("aborted"))["state"] == "aborted"
    assert rig.sent() == ["SAMPLE GET_FRAME 0 9"]


async def test_frame_removed_while_paused_is_skipped(rig):
    rig.engine.start("verify_frames", user="alice")
    await rig.until_sent("SAMPLE GET_FRAME 0 9")
    rig.engine.pause(user="alice")
    await rig.until("paused")
    rig.queue.remove("ABC-DEF", user="alice")
    rig.engine.resume(user="alice")
    assert (await rig.until("finished", "failed"))["state"] == "finished"
    assert rig.sent() == VERIFY_BOTH[:3]


async def test_failure_stops_the_sequence_and_leaves_things_unknown(rig):
    rig.engine.start("verify_frames", user="alice")
    await rig.until_sent("SAMPLE GET_FRAME 0 9")
    rig.server.fail("SAMPLE", "GRIPPER_FAULT")
    done = await rig.until("failed", "finished")
    assert done["state"] == "failed" and "GRIPPER_FAULT" in done["detail"]
    assert rig.sent() == ["SAMPLE GET_FRAME 0 9"]
    assert not rig.equipment.is_known(FRAME)
    assert not rig.runner.sequence_running
    # cleanup cannot guess: the operator has to say what is actually loaded
    with pytest.raises(SequenceError, match="Declare the state of: frame"):
        rig.engine.plan("cleanup")


async def test_cleanup_returns_frame_first_then_reference_and_filter(rig):
    rig.equipment.set_known(REFERENCE, "Co")
    rig.equipment.set_known(FILTER, "Z-1")
    rig.equipment.set_known(FRAME, {"container": 0, "slot": 9})
    assert rig.engine.plan("cleanup") == [
        "Retract and park the SDD detector, if it is not parked",
        "Return the frame to container 0, slot 9",
        "Return the reference foil Co",
        "Return the FY filter Z-1",
    ]
    rig.engine.start("cleanup", user="alice")
    assert (await rig.until("finished", "failed"))["state"] == "finished"
    # the detector is parked already, so its positions are read and nothing moves
    assert rig.sent() == [
        "MOTOR GET_POSITION DET_X",
        "MOTOR GET_POSITION DET_Y",
        "SAMPLE RETURN_FRAME 0 9",
        "SAMPLE RETURN_REF Co",
        "SAMPLE RETURN_FILTER",
    ]


async def test_when_a_sequence_cannot_start(rig):
    with pytest.raises(SequenceError, match="Nothing to clean up"):
        rig.engine.start("cleanup", user="alice")
    with pytest.raises(SequenceError, match="unknown sequence"):
        rig.engine.start("measure_everything", user="alice")

    rig.equipment.set_known(FRAME, {"container": 0, "slot": 9})
    with pytest.raises(SequenceError, match="robot is holding a frame"):
        rig.engine.start("verify_frames", user="alice")
    rig.equipment.set_unknown(FRAME, "test")
    with pytest.raises(SequenceError, match="Declare it first"):
        rig.engine.start("verify_frames", user="alice")
    rig.equipment.set_known(FRAME, None)

    rig.queue.clear(user="alice")
    with pytest.raises(SequenceError, match="queue is empty"):
        rig.engine.start("verify_frames", user="alice")
    assert rig.status["state"] == "idle" and rig.sent() == []


async def test_only_one_sequence_and_wrong_moment_actions(rig):
    with pytest.raises(SequenceError):
        rig.engine.pause(user="alice")
    with pytest.raises(SequenceError):
        rig.engine.abort(user="alice")
    rig.engine.start("verify_frames", user="alice")
    with pytest.raises(SequenceError, match="already running"):
        rig.engine.start("verify_frames", user="alice")
    with pytest.raises(SequenceError, match="not paused"):
        rig.engine.resume(user="alice")
    await rig.until("finished", "failed")


async def test_sequences_need_motion_switched_on(tmp_path):
    async with FakeControlServer() as server:
        rig = Rig(tmp_path, server, motion_enabled=False)
        with pytest.raises(SequenceError, match="switched off"):
            rig.engine.start("verify_frames", user="alice")
        assert server.requests == []
        await rig.runner.stop()
