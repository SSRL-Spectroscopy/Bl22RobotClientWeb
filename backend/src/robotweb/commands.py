"""The commands an operator can send, and the rules for when each is allowed.

The browser never sends protocol strings. It names a command from this file
and supplies its parameters; the backend validates them, checks the
preconditions against the equipment state, and builds the string.

To add a command: write a Command (or Query) below and add it to the table at
the end of the file.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .equipment import FILTER, FRAME, REFERENCE, EquipmentState
from .science import num

CONTAINERS = 3
SLOTS_PER_CONTAINER = 12
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
_WORDS_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+( [A-Za-z0-9_.-]+){0,15}$")


class CommandRefused(Exception):
    """The command is not allowed right now, or its parameters are invalid."""


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    kind: str  # "int", "number", "name" or "words"
    min: float | None = None
    max: float | None = None

    def clean(self, raw: Any) -> Any:
        if self.kind == "number":
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != raw or abs(raw) == float("inf"):
                raise CommandRefused(f"{self.label} must be a number")
            if (self.min is not None and raw < self.min) or (self.max is not None and raw > self.max):
                raise CommandRefused(f"{self.label} must be between {self.min} and {self.max}")
            return float(raw)
        if self.kind == "words":
            if not isinstance(raw, str) or not _WORDS_PATTERN.fullmatch(raw):
                raise CommandRefused(f"{self.label}: up to 16 words from A-Z a-z 0-9 _ . -")
            return raw
        if self.kind == "int":
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise CommandRefused(f"{self.label} must be a whole number")
            if (self.min is not None and raw < self.min) or (self.max is not None and raw > self.max):
                raise CommandRefused(f"{self.label} must be between {self.min} and {self.max}")
            return raw
        if not isinstance(raw, str) or not _NAME.fullmatch(raw):
            raise CommandRefused(f"{self.label}: 1 to 24 characters from A-Z a-z 0-9 _ -")
        return raw


@dataclass(frozen=True)
class Command:
    """A command that starts a process on the control server.

    check    raises CommandRefused unless the equipment state allows it
    build    returns the protocol string
    changes  the equipment items the process changes
    result   the new values of those items once the process has succeeded
    """

    name: str
    label: str
    subsystem: str
    check: Callable[[EquipmentState, dict], None]
    build: Callable[[EquipmentState, dict], str]
    result: Callable[[EquipmentState, dict], dict[str, Any]]
    changes: tuple[str, ...]
    params: tuple[Param, ...] = ()
    group: str = "Sample handling"
    # The command takes a frame out of storage: the slot named by the
    # "container" and "slot" parameters must be occupied.
    needs_occupied_slot: bool = False
    # False for commands that only sequences send: they are not offered on
    # the page and are refused when sent by hand.
    manual: bool = True
    # The command moves the motor named by the "motor" parameter to
    # "position": the motor interlock rules are checked first.
    motor_move: bool = False

    def clean_args(self, raw: dict) -> dict:
        unexpected = set(raw) - {p.name for p in self.params}
        if unexpected:
            raise CommandRefused(f"unexpected parameter: {', '.join(sorted(unexpected))}")
        missing = [p.label for p in self.params if p.name not in raw]
        if missing:
            raise CommandRefused(f"missing: {', '.join(missing)}")
        return {p.name: p.clean(raw[p.name]) for p in self.params}

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "group": self.group,
            "subsystem": self.subsystem,
            "params": [
                {"name": p.name, "label": p.label, "kind": p.kind, "min": p.min, "max": p.max}
                for p in self.params
            ],
        }


@dataclass(frozen=True)
class Query:
    """A read-only request that answers at once and changes nothing."""

    name: str
    label: str
    text: str
    publish_as: str | None = None  # state key to publish the answer under
    params: tuple[Param, ...] = field(default_factory=tuple)

    def describe(self) -> dict:
        return {"name": self.name, "label": self.label}


# ---- precondition helpers --------------------------------------------------

_WORDS = {FRAME: "the frame on the beam", REFERENCE: "the reference foil", FILTER: "the FY filter"}


def _need_known(equipment: EquipmentState, *items: str) -> None:
    for item in items:
        if not equipment.is_known(item):
            raise CommandRefused(
                f"The backend does not know the state of {_WORDS[item]}. Declare what is actually loaded first."
            )


def _need_empty(equipment: EquipmentState, item: str, why: str) -> None:
    _need_known(equipment, item)
    if equipment.value(item) is not None:
        raise CommandRefused(why)


def _need_loaded(equipment: EquipmentState, item: str, why: str) -> None:
    _need_known(equipment, item)
    if equipment.value(item) is None:
        raise CommandRefused(why)


# RULE (confirmed by the beamline scientist, 2026-11-16): there is one robot.
# It moves frames, reference foils and FY filters, and it also holds the frame
# in the beam during a measurement. While it holds a frame it cannot move
# anything else, so the frame must go back to storage before a reference or a
# filter is fetched or returned.
_FRAME_IN_THE_WAY = (
    "Return the frame to storage first: the robot is holding it and cannot move anything else."
)


# ---- sample handling ---------------------------------------------------------


def _check_get_frame(eq: EquipmentState, args: dict) -> None:
    _need_empty(eq, FRAME, "A frame is already on the beam. Return it first.")


def _check_return_frame(eq: EquipmentState, args: dict) -> None:
    _need_loaded(eq, FRAME, "There is no frame on the beam to return.")


def _frame_text(verb: str) -> Callable[[EquipmentState, dict], str]:
    def build(eq: EquipmentState, args: dict) -> str:
        frame = args if verb == "GET_FRAME" else eq.value(FRAME)
        return f"SAMPLE {verb} {frame['container']} {frame['slot']}"

    return build


def _check_get_reference(eq: EquipmentState, args: dict) -> None:
    _need_empty(eq, FRAME, _FRAME_IN_THE_WAY)
    _need_empty(eq, REFERENCE, "A reference foil is already loaded. Return it first.")


def _check_return_reference(eq: EquipmentState, args: dict) -> None:
    _need_empty(eq, FRAME, _FRAME_IN_THE_WAY)
    _need_loaded(eq, REFERENCE, "There is no reference foil to return.")


def _check_get_filter(eq: EquipmentState, args: dict) -> None:
    _need_empty(eq, FRAME, _FRAME_IN_THE_WAY)
    _need_empty(eq, FILTER, "An FY filter is already loaded. Return it first.")


def _check_return_filter(eq: EquipmentState, args: dict) -> None:
    _need_empty(eq, FRAME, _FRAME_IN_THE_WAY)
    _need_loaded(eq, FILTER, "There is no FY filter to return.")


# ---- commands that only sequences send -------------------------------------------


def _nothing(eq: EquipmentState, args: dict) -> None:
    return None


def _check_frame_held(eq: EquipmentState, args: dict) -> None:
    _need_loaded(eq, FRAME, "The robot is not holding a frame.")


def _step(name: str, label: str, subsystem: str, text: str, *params: Param, **more) -> Command:
    """A process that changes none of the tracked equipment items. `text` is
    the protocol string with {parameter} placeholders; numbers are written
    without exponent or trailing zeros."""

    def build(eq: EquipmentState, args: dict) -> str:
        shown = {k: num(v) if isinstance(v, float) else v for k, v in args.items()}
        return text.format(**shown)

    fields = dict(check=_nothing, changes=(), result=lambda eq, a: {})
    fields.update(more)
    return Command(
        name=name, label=label, subsystem=subsystem, params=params, build=build,
        manual=False, group="Sequence steps", **fields,
    )


def _with_frame(name: str, label: str, text: str, *params: Param) -> Command:
    """A robot process on the frame it holds. If it fails, the backend no
    longer knows where the frame is."""
    return _step(
        name, label, "SAMPLE", text, *params,
        check=_check_frame_held, changes=(FRAME,), result=lambda eq, a: {FRAME: eq.value(FRAME)},
    )


_ENERGY = dict(min=1000.0, max=40000.0)

_SEQUENCE_STEPS = (
    _step(
        "motor_move", "Move motor", "MOTOR", "MOTOR MOVE {motor} {position}",
        Param("motor", "Motor", "name"), Param("position", "Position", "number", -1000.0, 1000.0),
        motor_move=True,
    ),
    _with_frame("place_sample", "Place sample", "SAMPLE PLACE {where}", Param("where", "Position", "words")),
    _with_frame("align_frame", "Align frame", "SAMPLE CALIBRATE_FRAME"),
    _step("gas_fill", "Fill ion chambers", "BEAMLINE", "BEAMLINE AUTO_GAS_FILL {mix}", Param("mix", "Gas mix", "words")),
    _step(
        "set_slits", "Set slits", "BEAMLINE", "BEAMLINE SET_SLIT_GAP {vertical} {horizontal}",
        Param("vertical", "Vertical gap", "number", 0.0, 20.0),
        Param("horizontal", "Horizontal gap", "number", 0.0, 30.0),
    ),
    _step(
        "detune", "Detune mono", "BEAMLINE", "BEAMLINE AUTO_DETUNE {energy} {factor}",
        Param("energy", "Energy", "number", **_ENERGY), Param("factor", "Detune factor", "number", 0.0, 1.0),
    ),
    _step("align_table", "Align table", "BEAMLINE", "BEAMLINE ALIGN_TABLE"),
    _step(
        "auto_gain", "Set amplifier gain", "BEAMLINE", "BEAMLINE AUTO_GAIN {e1} {e2} {e3} 0 1 2 3",
        Param("e1", "Energy 1", "number", **_ENERGY), Param("e2", "Energy 2", "number", **_ENERGY),
        Param("e3", "Energy 3", "number", **_ENERGY),
    ),
    _step("collect_offsets", "Collect offsets", "BEAMLINE", "BEAMLINE COLLECT_OFFSETS"),
    _step("move_mono", "Move mono", "BEAMLINE", "BEAMLINE MOVE_MONO {energy}", Param("energy", "Energy", "number", **_ENERGY)),
    _step(
        "calibrate_energy", "Calibrate mono", "SCAN", "SCAN CALIBRATE_ENERGY {element} {edge} {crystal}",
        Param("element", "Foil element", "name"), Param("edge", "Foil edge", "name"),
        Param("crystal", "Crystal", "name"),
    ),
    _step(
        "xas_scan", "XAS scan", "SCAN", "SCAN START_XAS_SCAN {scan} {e1} {e2} {time} {points} {sweeps}",
        Param("scan", "Scan name", "name"),
        Param("e1", "Start energy", "number", **_ENERGY), Param("e2", "End energy", "number", **_ENERGY),
        Param("time", "Time per sweep", "number", 1.0, 3600.0),
        Param("points", "Points", "int", 10, 100000), Param("sweeps", "Sweeps", "int", 1, 100),
    ),
    _step(
        "xas_fy_scan", "XAS scan with SDD", "SCAN",
        "SCAN START_XAS_FY_SCAN {scan} {e1} {e2} {time} {points} {sweeps} "
        "{roi1_min} {roi1_size} {roi2_min} {roi2_size}",
        Param("scan", "Scan name", "name"),
        Param("e1", "Start energy", "number", **_ENERGY), Param("e2", "End energy", "number", **_ENERGY),
        Param("time", "Time per sweep", "number", 1.0, 3600.0),
        Param("points", "Points", "int", 10, 100000), Param("sweeps", "Sweeps", "int", 1, 100),
        Param("roi1_min", "ROI 1 start", "int", 0, 100000), Param("roi1_size", "ROI 1 size", "int", 1, 100000),
        Param("roi2_min", "ROI 2 start", "int", 0, 100000), Param("roi2_size", "ROI 2 size", "int", 1, 100000),
    ),
)


COMMANDS: dict[str, Command] = {
    c.name: c
    for c in (
        Command(
            name="get_frame",
            label="Get frame",
            subsystem="SAMPLE",
            params=(
                Param("container", "Container", "int", 0, CONTAINERS - 1),
                Param("slot", "Slot", "int", 0, SLOTS_PER_CONTAINER - 1),
            ),
            check=_check_get_frame,
            build=_frame_text("GET_FRAME"),
            needs_occupied_slot=True,
            changes=(FRAME,),
            result=lambda eq, a: {FRAME: {"container": a["container"], "slot": a["slot"]}},
        ),
        Command(
            name="return_frame",
            label="Return frame",
            subsystem="SAMPLE",
            check=_check_return_frame,
            build=_frame_text("RETURN_FRAME"),
            changes=(FRAME,),
            result=lambda eq, a: {FRAME: None},
        ),
        Command(
            name="get_reference",
            label="Get reference",
            subsystem="SAMPLE",
            params=(Param("name", "Reference", "name"),),
            check=_check_get_reference,
            build=lambda eq, a: f"SAMPLE GET_REF {a['name']}",
            changes=(REFERENCE,),
            result=lambda eq, a: {REFERENCE: a["name"]},
        ),
        Command(
            name="return_reference",
            label="Return reference",
            subsystem="SAMPLE",
            check=_check_return_reference,
            build=lambda eq, a: f"SAMPLE RETURN_REF {eq.value(REFERENCE)}",
            changes=(REFERENCE,),
            result=lambda eq, a: {REFERENCE: None},
        ),
        Command(
            name="get_filter",
            label="Get FY filter",
            subsystem="SAMPLE",
            params=(Param("name", "Filter", "name"),),
            check=_check_get_filter,
            build=lambda eq, a: f"SAMPLE GET_FILTER {a['name']}",
            changes=(FILTER,),
            result=lambda eq, a: {FILTER: a["name"]},
        ),
        Command(
            name="return_filter",
            label="Return FY filter",
            subsystem="SAMPLE",
            check=_check_return_filter,
            build=lambda eq, a: "SAMPLE RETURN_FILTER",
            changes=(FILTER,),
            result=lambda eq, a: {FILTER: None},
        ),
        *_SEQUENCE_STEPS,
    )
}

QUERIES: dict[str, Query] = {
    q.name: q
    for q in (
        Query("storage_slots", "Storage slots", "SAMPLE GET_SAMPLE_STORAGE_SLOTS", publish_as="storage_slots"),
        Query("mono_energy", "Mono energy", "SCAN GET_MONO_ENERGY"),
        Query("gain", "Amplifier gain", "BEAMLINE GET_GAIN"),
        Query("slit_gap", "Slit gap", "BEAMLINE GET_SLIT_GAP"),
        Query("slit_center", "Slit centre", "BEAMLINE GET_SLIT_CENTER"),
    )
}


def slot_is_occupied(lines: list[str], container: int, slot: int) -> bool:
    """Read the answer to SAMPLE GET_SAMPLE_STORAGE_SLOTS: one line per
    container, one flag per slot, "1" meaning occupied.

    Raises CommandRefused if the answer does not have the expected shape.
    """
    try:
        flag = lines[container].split()[slot]
    except IndexError:
        raise CommandRefused("The storage slot list from the control server could not be read.") from None
    if flag not in ("0", "1"):
        raise CommandRefused("The storage slot list from the control server could not be read.")
    return flag == "1"


def clean_declaration(item: str, value: Any) -> Any:
    """Validate what an operator declares to be loaded. None means nothing."""
    if value is None:
        return None
    if item == FRAME:
        if not isinstance(value, dict) or set(value) != {"container", "slot"}:
            raise CommandRefused("a frame is declared by its container and slot")
        return {
            "container": Param("container", "Container", "int", 0, CONTAINERS - 1).clean(value["container"]),
            "slot": Param("slot", "Slot", "int", 0, SLOTS_PER_CONTAINER - 1).clean(value["slot"]),
        }
    if item in (REFERENCE, FILTER):
        return Param("name", "Name", "name").clean(value)
    raise CommandRefused(f"unknown item {item!r}")
