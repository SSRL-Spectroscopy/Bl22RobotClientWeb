"""The measurement sequence, run against the fake control server and the fake portal."""

import asyncio
import time
from pathlib import Path

import pytest

from robotweb import measure
from robotweb.audit import AuditLog
from robotweb.beam import BeamWatch
from robotweb.commands import CommandRefused
from robotweb.control import ControlClient
from robotweb.control.fake_server import FakeControlServer
from robotweb.crystal import Crystal, Memory
from robotweb.equipment import FILTER, FRAME, REFERENCE, EquipmentState
from robotweb.motor_rules import MotorRules
from robotweb.mqueue import MeasurementQueue
from robotweb.notify import Notifier, NotifyError, parse_addresses
from robotweb.runner import CommandRunner
from robotweb.samples import normalise_frame
from robotweb.science import Science
from robotweb.sequence import SequenceEngine, SequenceError
from robotweb.state import StateStore

from fake_portal import FakePortal, frame, measurement

CONFIG = Path(__file__).resolve().parents[2] / "config"
M1, M2, M3, M4 = "1" * 24, "2" * 24, "3" * 24, "4" * 24


def second_frame() -> dict:
    """A frame with one Fe K transmission measurement and its own id."""
    raw = frame("ABC-DEF", 1, 0)
    raw["positions"] = [
        {
            "position": "Left-B",
            "plate_type": "8-circle",
            "sample_id": "c" * 24,
            "sample_name": "Fe foil",
            "measurements": [measurement(M4, 1, detection="transmission", technique="xanes")],
        }
    ]
    return raw


class Rig:
    def __init__(self, tmp_path, server: FakeControlServer, *, beam: bool = False, emails: str = "") -> None:
        self.server = server
        self.current: float | None = 500.0  # the SPEAR current the beam watch reads
        self.watch = BeamWatch(lambda: self.current, settle_s=0.05, unknown_grace_s=0.05) if beam else None
        self.store = StateStore()
        audit = AuditLog(tmp_path / "audit.log")
        self.equipment = EquipmentState(tmp_path / "equipment.json", self.store, audit)
        self.runner = CommandRunner(
            ControlClient("127.0.0.1", server.port, connect_timeout_s=1, response_timeout_s=1),
            self.store,
            self.equipment,
            audit,
            motion_enabled=True,
            rules=MotorRules.load(CONFIG / "motor_rules"),
            settle_s=0.005,
            poll_s=0.005,
            max_poll_failures=3,
        )
        self.queue = MeasurementQueue(tmp_path / "queue.json", self.store, audit)
        self.portal = FakePortal()
        self.portal.frames = {"KQZ-TBW": frame("KQZ-TBW", 0, 9), "ABC-DEF": second_frame()}
        self.crystal = Crystal(Memory(), self.store, audit, simulated=True)
        self.client = self.portal.client()
        self.engine = SequenceEngine(
            self.runner,
            self.equipment,
            self.queue,
            self.store,
            audit,
            tmp_path / "runs",
            portal=self.client,
            science=Science(CONFIG),
            crystal=self.crystal,
            simulation=True,
            beam=self.watch,
            notifier=Notifier(self.runner, parse_addresses(emails)),
            beam_poll_s=0.005,
        )
        for item in (FRAME, REFERENCE, FILTER):
            self.equipment.declare(item, None, user="alice")
        server.barcodes[(0, 9)] = "KQZ-TBW"
        server.barcodes[(1, 0)] = "ABC-DEF"

    def add(self, barcode: str, ids: list[str]) -> None:
        self.queue.set_frame(normalise_frame(self.portal.frames[barcode]), ids, user="alice")

    @property
    def status(self) -> dict:
        return self.store.get("sequence")

    async def run(self, name: str = "measure") -> dict:
        self.engine.start(name, user="alice")
        return await self.finish()

    async def finish(self) -> dict:
        await self.until(lambda: self.status["state"] in ("finished", "failed", "aborted"))
        return self.status

    async def until(self, condition) -> None:
        # By the clock, not by counting: on Windows a short sleep can return at once.
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if condition():
                return
            await asyncio.sleep(0.005)
        raise AssertionError(f"timed out; the sequence is {self.status['state']}: {self.status.get('step')}")

    def step(self) -> str:
        return self.status.get("step") or ""

    def mails(self) -> list[str]:
        return [r for r in self.server.requests if r.startswith("SCHEDULER SEND_MESSAGE")]

    def sent(self) -> list[str]:
        skip = ("GET_STATUS", "GET_SAMPLE_STORAGE_SLOTS", "GET_JOINTS")
        return [r for r in self.server.requests if not r.endswith(skip) and " GET_POSITION " not in r]

    def log(self) -> list[str]:
        return [entry["text"] for entry in self.status["log"]]

    def portal_writes(self) -> list[tuple[str, str]]:
        """(what, new value) for every state or status the portal was sent."""
        return [(path.split("/")[-2], body.get("state") or body.get("status"))
                for method, path, body in self.portal.requests if method in ("POST", "PUT")]

    def status_in_portal(self, barcode: str, measurement_id: str) -> str:
        for position in self.portal.frames[barcode]["positions"]:
            for m in position["measurements"]:
                if m["id"] == measurement_id:
                    return m["status"]
        raise KeyError(measurement_id)


async def _rig(tmp_path, **options):
    async with FakeControlServer(busy_s=0.01) as server:
        rig = Rig(tmp_path, server, **options)
        await rig.crystal.reload()
        ticker = asyncio.create_task(rig.watch.run(0.005)) if rig.watch else None
        yield rig
        if ticker:
            ticker.cancel()
        await rig.engine.stop()
        await rig.runner.stop()
        await rig.client.close()


@pytest.fixture
async def rig(tmp_path):
    async for rig in _rig(tmp_path):
        yield rig


@pytest.fixture
async def beam_rig(tmp_path):
    """With a beam watch (10 minutes shortened to 0.05 s) and two operator addresses."""
    async for rig in _rig(tmp_path, beam=True, emails="op1@example.org, op2@example.org"):
        yield rig


def in_order(haystack: list[str], needles: list[str]) -> bool:
    position = 0
    for needle in needles:
        try:
            position = haystack.index(needle, position) + 1
        except ValueError:
            return False
    return True


async def test_plan_shows_foil_energies_and_order(rig):
    rig.add("KQZ-TBW", [M3, M1])
    steps = rig.engine.plan("measure")
    text = "\n".join(steps)
    assert "Crystal Si-111" in steps[0]
    # Fe K (7112 eV) comes before Ni K (8333 eV), whatever the portal's order
    assert text.index("Fe K: reference foil Fe") < text.index("Ni K: reference foil Ni")
    assert "Left-A EXAFS transmission + sdd, unfocused: 6912 to 8112 eV, 8 sweeps of 240 s" in text
    assert steps[-1] == "Return the reference foil and the FY filter"


async def test_one_frame_two_edges(rig):
    rig.add("KQZ-TBW", [M1, M3])
    done = await rig.run()
    assert done["state"] == "finished", done["detail"]
    sent = rig.sent()

    assert sent[0] == "MOTOR MOVE OPTIC_VERT 73"
    assert in_order(sent, [
        "SAMPLE GET_FRAME 0 9",
        "SCHEDULER GET_BARCODE",
        # Fe K first: nothing loaded yet, so the frame goes back for the foil
        "SAMPLE RETURN_FRAME 0 9",
        "SAMPLE GET_REF Fe",
        "SAMPLE GET_FRAME 0 9",
        "BEAMLINE AUTO_GAS_FILL NITROGEN 1.5 NITROGEN 0 NITROGEN 2.5 NITROGEN 0 NITROGEN 3.0 NITROGEN 0",
        "BEAMLINE SET_SLIT_GAP 4 10",
        "BEAMLINE AUTO_DETUNE 7512 0.27416",
        "BEAMLINE ALIGN_TABLE",
        "BEAMLINE AUTO_GAIN 6912 7127 8112 0 1 2 3",
        "SCAN CALIBRATE_ENERGY Fe K Si-111",
        # align the frame once
        "MOTOR MOVE SOL_VERT 25",
        "MOTOR MOVE PIPS_VERT 25",
        "MOTOR MOVE OPTIC_VERT 5",
        "SCAN GET_MONO_ENERGY",
        "SAMPLE CALIBRATE_FRAME",
        # the measurement
        "SAMPLE PLACE 8-CIRCLE LEFT 1 TRUE",
        "SCHEDULER GET_PHOTO KQZ-TBW_LEFT-A",
        "MOTOR MOVE OPTIC_VERT 73",
        "BEAMLINE COLLECT_OFFSETS",
        "MOTOR MOVE DET_X 200",
        "MOTOR MOVE DET_Y 10",
        "SCAN START_XAS_FY_SCAN KQZ-TBW_LEFT-A 6912 8112 240 2400 8 619 40 599 80",
        f"SCAN POST_XAS_FY_SCAN KQZ-TBW_LEFT-A_001 1 {M1} 1 1 EXAFS Fe K TRANSMISSION_SDD UNFOCUSED",
        f"SCAN POST_XAS_FY_SCAN KQZ-TBW_LEFT-A_001 8 {M1} 8 1 EXAFS Fe K TRANSMISSION_SDD UNFOCUSED",
        "MOTOR MOVE DET_Y 130",
        "MOTOR MOVE DET_X 0",
        # Ni K: exchange the foil, optimise again, no second frame alignment
        "SAMPLE RETURN_FRAME 0 9",
        "SAMPLE RETURN_REF Fe",
        "SAMPLE GET_REF Ni",
        "SAMPLE GET_FRAME 0 9",
        "SCAN CALIBRATE_ENERGY Ni K Si-111",
        "SAMPLE PLACE 8-SLOT RIGHT 3 FALSE",
        "SCAN START_XAS_SCAN KQZ-TBW_RIGHT-C 8133 9333 160 4800 8",
        f"SCAN POST_XAS_SCAN KQZ-TBW_RIGHT-C_002 8 {M3} 8 1 EXAFS Ni K TRANSMISSION UNFOCUSED",
        "SAMPLE RETURN_FRAME 0 9",
        "SAMPLE RETURN_REF Ni",
    ])
    assert sent[-1] == "SAMPLE RETURN_REF Ni"
    assert sent.count("SAMPLE CALIBRATE_FRAME") == 1
    assert sum("POST_XAS" in r for r in sent) == 16

    assert rig.portal_writes() == [
        ("KQZ-TBW", "scheduled"),
        ("KQZ-TBW", "loaded"),
        (M1, "done"),
        (M3, "done"),
        ("KQZ-TBW", "internal_storage"),
    ]
    item = rig.queue.items[0]
    assert item["state"] == "done" and [m["state"] for m in item["measurements"]] == ["done", "done"]
    assert rig.equipment.value(FRAME) is None and rig.equipment.value(REFERENCE) is None

    # done is reported after the upload and before the SDD detector moves back
    log = rig.log()
    assert in_order(log, [
        "Uploaded 8 sweeps of Left-A Fe K EXAFS as batch 1",
        "Portal: Left-A Fe K EXAFS is done",
        "Move DET_Y to 130",
    ])


async def test_all_frames_are_scheduled_before_the_first_one_moves(rig):
    rig.add("KQZ-TBW", [M3])
    rig.add("ABC-DEF", [M4])
    assert (await rig.run())["state"] == "finished"
    writes = rig.portal_writes()
    assert writes[:3] == [("KQZ-TBW", "scheduled"), ("ABC-DEF", "scheduled"), ("KQZ-TBW", "loaded")]
    assert writes[-3:] == [("ABC-DEF", "loaded"), (M4, "done"), ("ABC-DEF", "internal_storage")]
    assert [item["state"] for item in rig.queue.items] == ["done", "done"]
    # Ni foil for the first frame, exchanged for Fe for the second, returned at the end
    refs = [r for r in rig.sent() if "_REF" in r]
    assert refs == ["SAMPLE GET_REF Ni", "SAMPLE RETURN_REF Ni", "SAMPLE GET_REF Fe", "SAMPLE RETURN_REF Fe"]


async def test_same_foil_stays_loaded_for_the_next_frame(rig):
    rig.add("KQZ-TBW", [M1])
    rig.add("ABC-DEF", [M4])
    assert (await rig.run())["state"] == "finished"
    refs = [r for r in rig.sent() if "_REF" in r]
    assert refs == ["SAMPLE GET_REF Fe", "SAMPLE RETURN_REF Fe"]
    # the beamline is still optimised again for the new frame
    assert rig.sent().count("SCAN CALIBRATE_ENERGY Fe K Si-111") == 2


async def test_a_done_measurement_is_set_pending_and_gets_a_new_batch(rig):
    rig.add("KQZ-TBW", [M2])  # done, last batch 1, last sweep 8
    assert (await rig.run())["state"] == "finished"
    assert rig.portal_writes()[2:4] == [(M2, "pending"), (M2, "done")]
    posts = [r for r in rig.sent() if "POST_XAS" in r]
    assert posts[0].split()[3:7] == ["1", M2, "9", "2"]  # sweep 1 of the scan is sweep 9, batch 2
    assert posts[-1].split()[3:7] == ["8", M2, "16", "2"]


async def test_tfy_measurement_gets_its_filter_and_raises_soller_and_pips(rig):
    raw = rig.portal.frames["KQZ-TBW"]
    raw["positions"][0]["measurements"][0]["detection"] = "transmission_tfy"
    rig.add("KQZ-TBW", [M1])
    assert (await rig.run())["state"] == "finished"
    sent = rig.sent()
    assert in_order(sent, [
        "SAMPLE GET_REF Fe",
        "SAMPLE GET_FILTER MN-3",
        "SAMPLE GET_FRAME 0 9",
        "SAMPLE PLACE 8-CIRCLE LEFT 1 TRUE",
        "MOTOR MOVE SOL_VERT 132",
        "MOTOR MOVE PIPS_VERT 132",
        "SCAN START_XAS_SCAN KQZ-TBW_LEFT-A 6912 8112 160 4800 8",
        "SAMPLE RETURN_REF Fe",
        "SAMPLE RETURN_FILTER",
    ])
    assert not any("DET_X" in r for r in sent)


async def test_a_failed_scan_stops_the_run_and_cleanup_parks_the_detector(rig):
    rig.add("KQZ-TBW", [M1])
    rig.add("ABC-DEF", [M4])
    rig.server.refuse.add(("SCAN", "START_XAS_FY_SCAN"))
    done = await rig.run()
    assert done["state"] == "failed" and "Everything was left where it is" in done["detail"]
    assert any("Operator attention needed" in line for line in rig.log())
    assert any("No e-mail sent: no operator address is configured" in line for line in rig.log())

    # everything stays in place: frame held, detector in, foil loaded
    assert rig.equipment.value(FRAME) == {"container": 0, "slot": 9}
    assert rig.equipment.value(REFERENCE) == "Fe"
    assert rig.server.positions["DET_Y"] == 10.0
    # the portal: the measurement is untouched, the frame in hand stays loaded,
    # the frame that was never started is released
    assert rig.status_in_portal("KQZ-TBW", M1) == "pending"
    assert rig.portal.frames["KQZ-TBW"]["state"] == "loaded"
    assert rig.portal.frames["ABC-DEF"]["state"] == "internal_storage"
    states = {item["barcode"]: item["state"] for item in rig.queue.items}
    assert states == {"KQZ-TBW": "loaded", "ABC-DEF": "queued"}
    assert rig.queue.items[0]["measurements"][0]["state"] == "failed"

    before = len(rig.sent())
    assert rig.engine.plan("cleanup")[0] == "Retract and park the SDD detector, if it is not parked"
    assert (await rig.run("cleanup"))["state"] == "finished"
    assert rig.sent()[before:] == [
        "MOTOR MOVE DET_Y 130",
        "MOTOR MOVE DET_X 0",
        "SAMPLE RETURN_FRAME 0 9",
        "SAMPLE RETURN_REF Fe",
    ]
    assert rig.portal.frames["KQZ-TBW"]["state"] == "internal_storage"


async def test_wrong_barcode_skips_the_frame(rig):
    rig.add("KQZ-TBW", [M3])
    rig.server.barcodes[(0, 9)] = "XXX-YYY"
    assert (await rig.run())["state"] == "finished"
    assert rig.queue.items[0]["state"] == "mismatch"
    assert rig.portal.frames["KQZ-TBW"]["state"] == "internal_storage"
    assert not any("START_XAS" in r or "GET_REF" in r for r in rig.sent())


async def test_when_the_run_cannot_start(rig, tmp_path):
    with pytest.raises(SequenceError, match="no measurements to run"):
        rig.engine.plan("measure")
    rig.add("KQZ-TBW", [M1])
    rig.equipment.set_unknown(REFERENCE, "test")
    with pytest.raises(SequenceError, match="which reference foil is loaded"):
        rig.engine.plan("measure")
    rig.equipment.declare(REFERENCE, None, user="alice")

    await rig.crystal.apply(1.9201, 90.0, user="alice")
    assert "Crystal Si-220" in rig.engine.plan("measure")[0]
    rig.crystal.d = None
    with pytest.raises(SequenceError, match="crystal values are not known"):
        rig.engine.plan("measure")


def test_place_argument():
    def place(plate_type, position, detection):
        return measure.place_argument({"plate_type": plate_type, "position": position, "detection": detection})

    assert place("8-circle", "Left-A", "transmission") == "8-CIRCLE LEFT 1 FALSE"
    assert place("8-slot", "Right-H", "transmission_tfy") == "8-SLOT RIGHT 8 TRUE"
    assert place("8-circle", "right-c", "transmission_sdd") == "8-CIRCLE RIGHT 3 TRUE"
    for bad in (("8-circle-gas", "Left-A", "transmission"), ("8-circle", "Left-J", "transmission"),
                ("8-circle", "Middle-A", "transmission"), ("8-circle", "Left-A", "xes")):
        with pytest.raises(measure.MeasureError):
            place(*bad)


async def test_a_plate_that_cannot_be_placed_stops_the_start(rig):
    rig.portal.frames["KQZ-TBW"]["positions"][0]["plate_type"] = "8-circle-gas"
    rig.add("KQZ-TBW", [M1])
    with pytest.raises(SequenceError, match="'8-circle-gas' plate .* has not been defined"):
        rig.engine.plan("measure")
    with pytest.raises(SequenceError):
        rig.engine.start("measure", user="alice")
    assert rig.sent() == [] and rig.portal_writes() == []


# ---- beam watch and e-mails -----------------------------------------------------


def test_beam_watch_rules():
    now, current = [0.0], [500.0]
    watch = BeamWatch(lambda: current[0], clock=lambda: now[0])

    def at(t, value):
        now[0], current[0] = t, value
        watch.tick()
        return watch.held

    assert not at(0, 500) and not at(1, 400)  # 400 is not below 400
    assert at(2, 399.9) and watch.losses == 1
    assert at(3, 470)  # between the two limits: still waiting
    assert at(10, 481) and at(609, 481)  # 10 minutes above 480 mA
    assert "in about 1 min" in watch.describe()
    assert at(609.5, 480)  # not above 480: the 10 minutes start again
    assert at(700, 490) and at(1299, 490)
    assert not at(1300, 490) and watch.losses == 1
    assert not at(1301, 450)  # only below 400 mA counts as lost
    # no reading: lost after 60 s, not before
    assert not at(1400, None) and not at(1459, None)
    assert at(1460, None) and watch.losses == 2
    assert "cannot be read" in watch.describe()
    assert at(1500, 500) and not at(2100, 500)


async def test_the_run_waits_for_beam_and_mails_the_operators(beam_rig):
    rig = beam_rig
    rig.current = 36.0
    await rig.until(lambda: rig.watch.held)
    rig.add("KQZ-TBW", [M3])
    rig.engine.start("measure", user="alice")
    await rig.until(lambda: rig.status.get("waiting"))
    await asyncio.sleep(0.1)
    assert "below 400 mA (now 36 mA)" in rig.status["waiting"] and rig.status["state"] == "running"
    assert rig.sent() == rig.mails() and len(rig.mails()) == 2  # nothing moved; one mail per operator
    assert rig.mails()[0].startswith("SCHEDULER SEND_MESSAGE op1@example.org BL22HT_Run_waiting_for_beam The run")

    rig.current = 500.0
    assert (await rig.finish())["state"] == "finished"
    assert [m.split()[3] for m in rig.mails()] == ["BL22HT_Run_waiting_for_beam"] * 2 + ["BL22HT_Run_resumed"] * 2
    assert in_order(rig.log(), ["E-mail sent to the operators (BL22HT_Run_waiting_for_beam)",
                                "The beam is back: the run goes on.", "Move OPTIC_VERT to 73"])
    assert not any("example.org" in line for line in rig.log())  # addresses stay out of the log
    assert rig.status_in_portal("KQZ-TBW", M3) == "done"


async def test_a_run_waiting_for_beam_can_be_aborted(beam_rig):
    rig = beam_rig
    rig.current = None  # the current cannot be read
    await rig.until(lambda: rig.watch.held)
    rig.add("KQZ-TBW", [M3])
    rig.engine.start("measure", user="alice")
    await rig.until(lambda: rig.status.get("waiting"))
    assert "cannot be read" in rig.status["waiting"]
    rig.engine.abort(user="alice")
    done = await rig.finish()
    assert done["state"] == "aborted" and done["waiting"] is None
    assert rig.portal.frames["KQZ-TBW"]["state"] == "internal_storage"


async def test_a_scan_without_beam_is_not_uploaded_but_repeated(beam_rig):
    rig = beam_rig
    rig.add("KQZ-TBW", [M3])
    rig.server.slow[("SCAN", "START_XAS_SCAN")] = 0.4  # long enough for the beam to drop in it
    rig.engine.start("measure", user="alice")
    await rig.until(lambda: rig.step().startswith("Scan"))
    rig.current = 100.0
    await rig.until(lambda: rig.watch.held)
    rig.current = 500.0
    rig.server.slow.clear()
    assert (await rig.finish())["state"] == "finished"

    sent = rig.sent()
    assert sum("START_XAS_SCAN" in r for r in sent) == 2
    assert sum("POST_XAS_SCAN" in r for r in sent) == 8  # only the second scan was uploaded
    assert all("RIGHT-C_002" in r for r in sent if "POST_XAS_SCAN" in r)
    assert sent.count("SCAN CALIBRATE_ENERGY Ni K Si-111") == 2  # optimised again after the beam came back
    assert sent.count("SAMPLE CALIBRATE_FRAME") == 1
    assert in_order(rig.log(), [
        "The beam was lost during the scan of Right-C Ni K EXAFS: nothing is uploaded, the scan will be repeated",
        "The beam is back: the run goes on.",
        "The beam was lost since the beamline was optimised: optimising again",
        "Uploaded 8 sweeps of Right-C Ni K EXAFS as batch 1",
    ])
    assert [w for w in rig.portal_writes() if w[0] == M3] == [(M3, "done")]


async def test_a_stopped_run_mails_the_operators(beam_rig):
    rig = beam_rig
    rig.add("KQZ-TBW", [M3])
    rig.server.refuse.add(("SCAN", "START_XAS_SCAN"))
    assert (await rig.run())["state"] == "failed"
    assert len(rig.mails()) == 2 and "BL22HT_Run_stopped The measurement run stopped" in rig.mails()[0]


def test_operator_addresses_are_checked():
    assert parse_addresses("") == [] and parse_addresses(" a@b.org ,c@d.org") == ["a@b.org", "c@d.org"]
    for bad in ("someone", "a@b.org; rm", "a@b.org c@d.org"):
        with pytest.raises(NotifyError):
            parse_addresses(bad)


async def test_motor_moves_are_checked_against_the_rules(rig):
    # by hand: not offered at all
    with pytest.raises(CommandRefused, match="only be sent by a sequence"):
        await rig.runner.start("motor_move", {"motor": "DET_Y", "position": 10.0}, user="alice")
    # by a sequence: DET_Y may not go in while DET_X is parked and the robot is away
    with pytest.raises(CommandRefused, match="DET_Y may not move to 10") as refusal:
        await rig.runner.run("motor_move", {"motor": "DET_Y", "position": 10.0}, user="alice")
    assert "DET_X is at 0" in str(refusal.value) and "Robot in the wrong hemispehre" in str(refusal.value)
    with pytest.raises(CommandRefused, match="outside of defined rules"):
        await rig.runner.run("motor_move", {"motor": "OPTIC_VERT", "position": 250.0}, user="alice")
    assert not any(r.startswith("MOTOR MOVE") for r in rig.server.requests)
    # lowering PIPS_VERT is allowed
    await rig.runner.run("motor_move", {"motor": "PIPS_VERT", "position": 25.0}, user="alice")
    assert rig.server.positions["PIPS_VERT"] == 25.0
