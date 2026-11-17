"""The measurement sequence: works through the queue, frame by frame.

Written from RunFrame_2 in the older C# client (reference/csharp/MasterScan.cs)
with the changes agreed on 2026-11-17:

- When the run starts, every queued frame is reported to the portal as
  "scheduled". A frame is reported "loaded" when the robot holds it and
  "internal_storage" when it is back in its slot.
- A frame's measurements run grouped by element and edge, then by detection,
  then by beam (science.py: Science.ordered).
- After the control server has uploaded the sweeps, the measurement is
  reported "done" in a step of its own (_conclude), before the SDD detector
  moves back. A later version may evaluate the data there and decide to run
  more sweeps.
- A failure stops the whole run and leaves everything in place, and the
  operators get an e-mail.
- No scan-info file: metadata belongs to the control software's upload.
- The run waits while the beam is lost (beam.py). A scan during which the
  beam was lost is not uploaded; it is repeated after the beamline has been
  optimised again.

The reference foil stays loaded from one frame to the next if the next frame
needs the same one, but the beamline is optimised at least once for every
frame, against drift. The foil and the FY filter are returned when the queue
is done.
"""

from typing import TYPE_CHECKING

from . import science as sci
from .equipment import FILTER, REFERENCE
from .portal import PortalError
from .runner import CommandFailed
from .samples import describe, normalise_frame
from .science import ScienceError, Setup, num

if TYPE_CHECKING:
    from .sequence import Context, SequenceEngine

NO_TAG = {"0000-0000", "000-000", ""}
# Queue states of a frame that the sequence will not touch.
_SKIPPED_FRAME_STATES = {"mismatch"}


class MeasureError(Exception):
    """The sequence cannot start or cannot go on."""


def _open_measurements(item: dict) -> list[dict]:
    return [m for m in item["measurements"] if m.get("state") != "done"]


def _frames_to_run(queue_items: list[dict]) -> list[dict]:
    return [i for i in queue_items if i["state"] not in _SKIPPED_FRAME_STATES and _open_measurements(i)]


# Plate types SAMPLE PLACE is known to understand, as the control server
# writes them.
_PLATES = {"8-circle": "8-CIRCLE", "8-slot": "8-SLOT"}
_SIDES = {"left": "LEFT", "right": "RIGHT"}
_FY_DETECTION = {"transmission": "FALSE", "transmission_tfy": "TRUE", "transmission_sdd": "TRUE"}


def place_argument(measurement: dict) -> str:
    """The argument of SAMPLE PLACE: "<plate> <side> <number> <fy>", for
    example "8-CIRCLE LEFT 1 TRUE" (ParseApiSamplePosition_2 in helpers.cs).
    Sample A is number 1, H is 8. <fy> is TRUE for TFY and SDD detection.

    Refuses anything it does not know for certain: a wrong argument could
    drive the sample into something.
    """
    plate_type = str(measurement.get("plate_type", ""))
    position = str(measurement.get("position", ""))
    detection = str(measurement.get("detection", "")).lower()
    plate = _PLATES.get(plate_type.lower())
    if plate is None:
        raise MeasureError(
            f"How a sample on a '{plate_type}' plate is placed in the beam has not been defined, "
            f"so {position} cannot be measured."
        )
    side, _, label = position.partition("-")
    if side.lower() not in _SIDES or len(label) != 1 or label.upper() not in "ABCDEFGH":
        raise MeasureError(f"'{position}' is not a sample position such as Left-A.")
    if detection not in _FY_DETECTION:
        raise MeasureError(f"'{detection}' is not a known detection, so {position} cannot be placed.")
    number = "ABCDEFGH".index(label.upper()) + 1
    return f"{plate} {_SIDES[side.lower()]} {number} {_FY_DETECTION[detection]}"


# ---- plan ---------------------------------------------------------------------


def plan(engine: "SequenceEngine") -> list[str]:
    """The steps the run would take now, for the operator to read before
    starting. Raises MeasureError if the run cannot start."""
    equipment = engine.equipment
    for item, word in ((REFERENCE, "reference foil"), (FILTER, "FY filter")):
        if not equipment.is_known(item):
            raise MeasureError(f"The backend does not know which {word} is loaded. Declare it first.")
    if engine.science is None or engine.portal is None or engine.crystal is None:
        raise MeasureError("The measurement sequence is not available in this backend.")
    cut = engine.crystal.cut
    if cut is None:
        raise MeasureError("The crystal values are not known. Press Reload in the crystal panel first.")
    frames = _frames_to_run(engine.queue.items)
    if not frames:
        raise MeasureError("The queue holds no measurements to run.")

    steps = [f"Crystal {cut}. Report {len(frames)} frame(s) to the portal as scheduled."]
    if engine.beam is not None:
        watch = engine.beam
        steps.append(
            f"Throughout: wait while the SPEAR current is below {watch.low_ma:g} mA, and go on "
            f"{watch.settle_s / 60:g} min after it is above {watch.ok_ma:g} mA"
        )
    for item in frames:
        steps.append(
            f"Frame {item['barcode']} from container {item['container']}, slot {item['slot']}: "
            "get it, check its barcode, align it once"
        )
        edge = None
        for m in _ordered(engine, item):
            setup = _setup(engine, m, cut)
            place_argument(m)
            if setup.edge_key != edge:
                edge = setup.edge_key
                steps.append(
                    f"  {setup.element} {setup.edge}: reference foil {setup.foil_element}, "
                    f"optimise the beamline (detune {setup.detune_factor:.2f} at {setup.mid_energy:.0f} eV)"
                )
            extra = f", FY filter {setup.fy_filter}" if setup.fy_filter else ""
            again = " (measure again)" if m.get("status") not in (None, "pending") else ""
            steps.append(
                f"    {m['position']} {setup.technique.upper()} {setup.detection.replace('_', ' + ')}, "
                f"{setup.beam}{extra}: {setup.e1:.0f} to {setup.e2:.0f} eV, "
                f"{setup.sweeps} sweeps of {setup.scan_time:.0f} s{again}"
            )
        steps.append(f"  Return frame {item['barcode']} and report it as back in storage")
    steps.append("Return the reference foil and the FY filter")
    return steps


def _ordered(engine: "SequenceEngine", item: dict) -> list[dict]:
    try:
        return engine.science.ordered(_open_measurements(item))
    except ScienceError as exc:
        raise MeasureError(f"Frame {item['barcode']}: {exc}.") from exc


def _setup(engine: "SequenceEngine", measurement: dict, cut: str) -> Setup:
    try:
        return engine.science.setup(measurement, cut)
    except ScienceError as exc:
        raise MeasureError(f"{describe(measurement)}: {exc}.") from exc


# ---- run ----------------------------------------------------------------------


async def run(ctx: "Context") -> None:
    cut = ctx.crystal.cut
    if cut is None:
        raise MeasureError("The crystal values are not known.")
    ctx.needs_beam = True
    for item in _frames_to_run(ctx.queue.items):
        ctx.queue.mark(item["barcode"], "queued")
        for m in _open_measurements(item):
            ctx.queue.mark_measurement(item["barcode"], m["id"], "queued")

    scheduled: set[str] = set()  # frames this run reported as scheduled
    try:
        for item in _frames_to_run(ctx.queue.items):
            await _schedule(ctx, item["barcode"], scheduled)
        await _motor(ctx, "OPTIC_VERT", sci.OPTIC_VERT_PIPE)
        while True:
            # Pick the next frame only after the checkpoint: while the run is
            # paused, the operator may change the frames not yet started.
            await ctx.checkpoint()
            item = next(
                (i for i in _frames_to_run(ctx.queue.items) if i["state"] in ("queued", "scheduled")), None
            )
            if item is None:
                break
            await _schedule(ctx, item["barcode"], scheduled)
            await _run_frame(ctx, item, cut)
            scheduled.discard(item["barcode"])
        await _return_foil_and_filter(ctx)
    except Exception as exc:
        from .sequence import _Aborted  # here, because sequence.py imports this module

        if not isinstance(exc, _Aborted):  # an abort is the operator's own decision
            ctx.log("Operator attention needed: the run stopped.", "error")
            await ctx.notify("BL22HT_Run_stopped", f"The measurement run stopped and needs an operator: {exc}")
        raise
    finally:
        await _release(ctx, scheduled)


async def _run_frame(ctx: "Context", item: dict, cut: str) -> None:
    barcode, container, slot = item["barcode"], item["container"], item["slot"]
    where = f"container {container}, slot {slot}"
    await ctx.command("get_frame", {"container": container, "slot": slot}, label=f"Get frame {barcode} from {where}")
    fresh = normalise_frame(await ctx.portal.set_frame_state(barcode, "loaded"))
    ctx.queue.mark(barcode, "loaded")
    ctx.log(f"Portal: frame {barcode} is loaded")

    lines = await ctx.ask("SCHEDULER GET_BARCODE", label="Read the barcode")
    found = lines[0].strip().upper() if lines else ""
    if found != barcode:
        note = "no barcode could be read" if found in NO_TAG else f"the frame in this slot carries {found}"
        ctx.log(f"Frame from {where}: {note}; the portal lists {barcode} there. Skipping it.", "error")
        await ctx.command("return_frame", label=f"Return the frame to {where}")
        await ctx.portal.set_frame_state(barcode, "internal_storage")
        ctx.queue.mark(barcode, "mismatch", note)
        return

    in_portal = {m["id"]: m for m in fresh["measurements"]}
    aligned = False
    edge = None  # None at the start of every frame: each frame is optimised at least once
    optimised_at: int | None = None  # the beam-loss count when the beamline was last optimised
    for measurement in ctx.science.ordered(_open_measurements(item)):
        measured = False
        while not measured:
            await ctx.checkpoint()
            setup = ctx.science.setup(measurement, cut)
            if setup.edge_key != edge:
                ctx.log(f"New element or edge: {setup.element} {setup.edge}")
                await _exchange(ctx, item, setup)
                edge, optimised_at = setup.edge_key, None
            elif setup.fy_filter and ctx.equipment.value(FILTER) != setup.fy_filter:
                await _exchange(ctx, item, setup)
            # Optimise for a new edge, and again whenever the beam was lost
            # since (or during) the last optimisation.
            while optimised_at != ctx.beam_losses:
                if optimised_at is not None:
                    ctx.log("The beam was lost since the beamline was optimised: optimising again")
                await ctx.checkpoint()  # waits until the beam is back
                optimised_at = ctx.beam_losses
                await _optimise(ctx, setup)
            if not aligned:
                await _align_frame(ctx)
                aligned = True
            try:
                measured = await _measure(ctx, item, measurement, setup, in_portal)
            except BaseException:
                ctx.queue.mark_measurement(barcode, measurement["id"], "failed")
                raise

    await ctx.command("return_frame", label=f"Return frame {barcode} to {where}")
    await ctx.portal.set_frame_state(barcode, "internal_storage")
    ctx.queue.mark(barcode, "done")
    ctx.log(f"Frame {barcode} is complete and back in storage")


async def _exchange(ctx: "Context", item: dict, setup: Setup) -> None:
    """Bring in the reference foil and the FY filter this measurement needs.
    The robot cannot move them while it holds the frame, so the frame goes
    back to its slot for the exchange."""
    equipment = ctx.equipment
    foil, fy_filter = equipment.value(REFERENCE), equipment.value(FILTER)
    wanted_filter = setup.fy_filter or None
    change_foil = foil != setup.foil_element
    change_filter = fy_filter != wanted_filter
    if not (change_foil or change_filter):
        return
    where = f"container {item['container']}, slot {item['slot']}"
    await ctx.command("return_frame", label=f"Return frame {item['barcode']} to {where} for the exchange")
    if change_foil and foil is not None:
        await ctx.command("return_reference", label=f"Return the reference foil {foil}")
    if change_filter and fy_filter is not None:
        await ctx.command("return_filter", label=f"Return the FY filter {fy_filter}")
    if change_foil:
        await ctx.command(
            "get_reference", {"name": setup.foil_element}, label=f"Get the reference foil {setup.foil_element}"
        )
    if change_filter and wanted_filter is not None:
        await ctx.command("get_filter", {"name": wanted_filter}, label=f"Get the FY filter {wanted_filter}")
    await ctx.command(
        "get_frame", {"container": item["container"], "slot": item["slot"]}, label=f"Get frame {item['barcode']} again"
    )


async def _optimise(ctx: "Context", s: Setup) -> None:
    """Full beamline optimisation for a new element or edge."""
    detune_v, detune_h = sci.DETUNE_SLITS
    await ctx.command("gas_fill", {"mix": s.gas_mix}, label="Fill the ion chambers")
    await _slits(ctx, detune_v, detune_h)
    await ctx.command(
        "detune",
        {"energy": s.mid_energy, "factor": s.detune_factor},
        label=f"Detune the mono: {s.detune_factor:.2f} at {s.mid_energy:.0f} eV",
    )
    await _slits(ctx, s.slit_v, s.slit_h)
    await ctx.command("align_table", label="Align the table")
    await _gain(ctx, s)
    await ctx.command(
        "calibrate_energy",
        {"element": s.foil_element, "edge": s.foil_edge, "crystal": s.crystal},
        label=f"Calibrate the mono on {s.foil_element} {s.foil_edge} ({s.crystal})",
    )


async def _align_frame(ctx: "Context") -> None:
    """Find the frame's edges with the beam. Once per frame."""
    await _motor(ctx, "SOL_VERT", sci.SOL_VERT_PARKED)
    await _motor(ctx, "PIPS_VERT", sci.PIPS_VERT_PARKED)
    await _motor(ctx, "OPTIC_VERT", sci.OPTIC_VERT_PARKED)
    lines = await ctx.ask("SCAN GET_MONO_ENERGY", label="Read the mono energy")
    try:
        energy = float(lines[0])
    except (IndexError, ValueError):
        raise CommandFailed("The mono energy could not be read.") from None
    too_high = energy > sci.FRAME_ALIGN_ABOVE
    if too_high:
        await ctx.command(
            "move_mono", {"energy": sci.FRAME_ALIGN_ENERGY}, label="Move the mono to 10 keV to align the frame"
        )
    await ctx.command("align_frame", label="Align the frame")
    if too_high:
        await ctx.command("move_mono", {"energy": energy}, label=f"Move the mono back to {energy:.0f} eV")


async def _measure(ctx: "Context", item: dict, m: dict, s: Setup, portal_measurements: dict) -> bool:
    """Measure once. False if the beam was lost during the scan: nothing was
    uploaded then and the measurement has to be repeated."""
    barcode = item["barcode"]
    scan = f"{barcode}_{m['position'].upper()}"
    what = f"{m['position']} {s.trajectory}"
    ctx.queue.mark_measurement(barcode, m["id"], "running")

    await _motor(ctx, "SOL_VERT", sci.SOL_VERT_PARKED)
    await ctx.command(
        "place_sample", {"where": place_argument(m)}, label=f"Place sample {m['position']} of {barcode}"
    )
    if s.is_tfy and s.fy_filter:
        await _motor(ctx, "SOL_VERT", sci.SOL_VERT_FY)
    if s.is_tfy:
        await _motor(ctx, "PIPS_VERT", sci.PIPS_VERT_FY)

    await _motor(ctx, "OPTIC_VERT", sci.OPTIC_VERT_PARKED)
    await ctx.ask(f"SCHEDULER GET_PHOTO {scan}", label="Take the sample photo")
    ctx.log("Sample photo taken")
    await _motor(ctx, "OPTIC_VERT", sci.OPTIC_VERT_CAP if s.is_focused else sci.OPTIC_VERT_PIPE)

    await _slits(ctx, s.slit_v, s.slit_h)
    await _gain(ctx, s)
    await ctx.command("collect_offsets", label="Collect offsets")

    if s.is_sdd:
        await _motor(ctx, "SOL_VERT", sci.SOL_VERT_PARKED)
        await _motor(ctx, "PIPS_VERT", sci.PIPS_VERT_PARKED)
        await _motor(ctx, "DET_X", sci.DET_X_IN)
        await _motor(ctx, "DET_Y", sci.DET_Y_IN)

    # A measurement that is already done or failed is measured again: the
    # portal starts a new batch once it is pending again.
    await ctx.checkpoint()
    in_portal = portal_measurements.get(m["id"])
    if in_portal is None or in_portal.get("status") != "pending":
        in_portal = await ctx.portal.set_measurement_status(m["id"], "pending")
        portal_measurements[m["id"]] = in_portal
        ctx.log(f"Portal: {what} set back to pending, to be measured again")
    batch = (in_portal.get("last_batch") or 0) + 1
    first_sweep = (in_portal.get("last_sweep") or 0) + 1

    args = {
        "scan": scan, "e1": s.e1, "e2": s.e2, "time": s.scan_time, "points": s.scan_points, "sweeps": s.sweeps,
    }
    if s.is_sdd:
        args.update(roi1_min=s.roi1_min, roi1_size=s.roi1_size, roi2_min=s.roi2_min, roi2_size=s.roi2_size)
    await ctx.checkpoint()  # waits here if the beam is lost right now
    losses = ctx.beam_losses
    answer = await ctx.command(
        "xas_fy_scan" if s.is_sdd else "xas_scan", args, label=f"Scan {what}: {s.sweeps} sweeps of {s.scan_time:.0f} s"
    )
    if ctx.beam_losses != losses:
        ctx.log(f"The beam was lost during the scan of {what}: nothing is uploaded, the scan will be repeated", "error")
        ctx.queue.mark_measurement(barcode, m["id"], "queued")
        await _after_scan(ctx, s)
        return False
    data_name = answer[0].strip() if answer else ""
    if not data_name:
        raise CommandFailed("The control server did not name the data of the scan, so it cannot be uploaded.")

    post = "POST_XAS_FY_SCAN" if s.is_sdd else "POST_XAS_SCAN"
    tail = f"{s.technique.upper()} {s.element} {s.edge} {s.detection.upper()} {s.beam.upper()}"
    for n in range(s.sweeps):
        await ctx.ask(
            f"SCAN {post} {data_name} {n + 1} {m['id']} {first_sweep + n} {batch} {tail}",
            label=f"Upload sweep {n + 1} of {s.sweeps}",
        )
    ctx.log(f"Uploaded {s.sweeps} sweeps of {what} as batch {batch}")
    ctx.queue.mark_measurement(barcode, m["id"], "uploaded")

    await _conclude(ctx, barcode, m, what)
    await _after_scan(ctx, s)
    return True


async def _after_scan(ctx: "Context", s: Setup) -> None:
    if s.is_sdd:
        await _motor(ctx, "DET_Y", sci.DET_Y_PARKED)
        await _motor(ctx, "DET_X", sci.DET_X_PARKED)
    if s.is_focused:
        await _motor(ctx, "OPTIC_VERT", sci.OPTIC_VERT_PIPE)


async def _conclude(ctx: "Context", barcode: str, m: dict, what: str) -> None:
    """Close a measurement after its data has been uploaded.

    A step of its own on purpose: a later version may fetch the data from the
    portal here, judge its quality and run more sweeps of the same measurement
    before it is reported done.
    """
    await ctx.checkpoint()
    await ctx.portal.set_measurement_status(m["id"], "done")
    ctx.queue.mark_measurement(barcode, m["id"], "done")
    ctx.log(f"Portal: {what} is done")


async def _return_foil_and_filter(ctx: "Context") -> None:
    foil, fy_filter = ctx.equipment.value(REFERENCE), ctx.equipment.value(FILTER)
    if foil is not None:
        await ctx.command("return_reference", label=f"Return the reference foil {foil}")
    if fy_filter is not None:
        await ctx.command("return_filter", label=f"Return the FY filter {fy_filter}")


# ---- portal bookkeeping -----------------------------------------------------------


async def _schedule(ctx: "Context", barcode: str, scheduled: set[str]) -> None:
    if barcode in scheduled:
        return
    state = (await ctx.portal.frame(barcode)).get("state")
    if state == "internal_storage":
        await ctx.portal.set_frame_state(barcode, "scheduled")
    elif state != "scheduled":
        raise MeasureError(
            f"The portal lists frame {barcode} as '{state}'. It must be in internal storage before it can be measured."
        )
    scheduled.add(barcode)
    ctx.queue.mark(barcode, "scheduled")
    ctx.log(f"Portal: frame {barcode} is scheduled")


async def _release(ctx: "Context", scheduled: set[str]) -> None:
    """When the run ends for any reason: frames it scheduled but never took
    out of storage go back to "internal_storage" in the portal. A frame the
    robot holds stays "loaded"; the cleanup sequence deals with it."""
    for barcode in sorted(scheduled):
        try:
            if (await ctx.portal.frame(barcode)).get("state") != "scheduled":
                continue
            await ctx.portal.set_frame_state(barcode, "internal_storage")
        except PortalError as exc:
            ctx.log(f"Portal: frame {barcode} could not be released: {exc}", "error")
            continue
        ctx.queue.mark(barcode, "queued")
        ctx.log(f"Portal: frame {barcode} released, it was not started")


# ---- small helpers ------------------------------------------------------------


async def _motor(ctx: "Context", motor: str, position: float) -> None:
    await ctx.command("motor_move", {"motor": motor, "position": position}, label=f"Move {motor} to {num(position)}")


async def _slits(ctx: "Context", vertical: float, horizontal: float) -> None:
    await ctx.command(
        "set_slits",
        {"vertical": vertical, "horizontal": horizontal},
        label=f"Set the slits to {vertical:.2f} x {horizontal:.2f} mm",
    )


async def _gain(ctx: "Context", s: Setup) -> None:
    await ctx.command(
        "auto_gain", {"e1": s.e1, "e2": s.e1 + 215.0, "e3": s.e2}, label="Set the amplifier gain"
    )
