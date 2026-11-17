import json

import pytest

from robotweb.audit import AuditLog
from robotweb.commands import COMMANDS, CommandRefused, clean_declaration
from robotweb.equipment import FILTER, FRAME, REFERENCE, EquipmentState
from robotweb.state import StateStore


def make(tmp_path) -> EquipmentState:
    return EquipmentState(tmp_path / "equipment.json", StateStore(), AuditLog(tmp_path / "audit.log"))


def all_empty(tmp_path) -> EquipmentState:
    eq = make(tmp_path)
    for item in (FRAME, REFERENCE, FILTER):
        eq.declare(item, None, user="alice")
    return eq


# ---- the record ------------------------------------------------------------


def test_everything_is_unknown_until_declared(tmp_path):
    eq = make(tmp_path)
    assert not any(eq.is_known(item) for item in (FRAME, REFERENCE, FILTER))


def test_declared_state_survives_a_restart(tmp_path):
    eq = make(tmp_path)
    eq.declare(FRAME, {"container": 0, "slot": 9}, user="alice")
    eq.declare(REFERENCE, None, user="alice")

    again = make(tmp_path)
    assert again.is_known(FRAME) and again.value(FRAME) == {"container": 0, "slot": 9}
    assert again.is_known(REFERENCE) and again.value(REFERENCE) is None
    assert not again.is_known(FILTER)


def test_restart_during_a_command_leaves_the_item_unknown(tmp_path):
    eq = all_empty(tmp_path)
    eq.begin_change(FRAME)  # the backend dies here, mid-command

    again = make(tmp_path)
    assert not again.is_known(FRAME)
    assert "stopped while a command" in again.describe(FRAME)["reason"]
    assert again.is_known(REFERENCE)  # untouched items keep their state


def test_refused_command_restores_the_previous_state(tmp_path):
    eq = all_empty(tmp_path)
    previous = eq.begin_change(FRAME)
    assert not eq.is_known(FRAME)
    eq.restore(FRAME, previous)
    assert eq.is_known(FRAME) and eq.value(FRAME) is None


def test_corrupt_file_is_not_trusted(tmp_path):
    (tmp_path / "equipment.json").write_text("{ not json")
    eq = make(tmp_path)
    assert not eq.is_known(FRAME)


def test_changes_are_published_and_audited(tmp_path):
    store = StateStore()
    eq = EquipmentState(tmp_path / "equipment.json", store, AuditLog(tmp_path / "audit.log"))
    eq.declare(REFERENCE, "Co", user="alice")
    assert store.get("equipment")["items"]["reference"] == {"status": "known", "value": "Co", "reason": None}
    eq.set_unknown(REFERENCE, "test reason")
    actions = [json.loads(line)["action"] for line in (tmp_path / "audit.log").read_text().splitlines()]
    assert actions == ["equipment_declared", "equipment_unknown"]


# ---- the rules -------------------------------------------------------------


def refused(eq, command_name, /, **args) -> str:
    command = COMMANDS[command_name]
    with pytest.raises(CommandRefused) as info:
        command.check(eq, command.clean_args(args))
    return str(info.value)


def allowed(eq, command_name, /, **args) -> str:
    command = COMMANDS[command_name]
    clean = command.clean_args(args)
    command.check(eq, clean)
    return command.build(eq, clean)


def test_nothing_moves_while_the_state_is_unknown(tmp_path):
    eq = make(tmp_path)
    for name, args in [
        ("get_frame", {"container": 0, "slot": 0}),
        ("return_frame", {}),
        ("get_reference", {"name": "Co"}),
        ("return_reference", {}),
        ("get_filter", {"name": "Z-1"}),
        ("return_filter", {}),
    ]:
        assert "Declare what is actually loaded" in refused(eq, name, **args)


def test_frame_rules(tmp_path):
    eq = all_empty(tmp_path)
    assert allowed(eq, "get_frame", container=0, slot=9) == "SAMPLE GET_FRAME 0 9"
    assert "no frame on the beam" in refused(eq, "return_frame")

    eq.set_known(FRAME, {"container": 0, "slot": 9})
    assert "already on the beam" in refused(eq, "get_frame", container=1, slot=2)
    # the frame goes back to the slot it came from
    assert allowed(eq, "return_frame") == "SAMPLE RETURN_FRAME 0 9"


def test_reference_and_filter_are_only_exchanged_without_a_frame(tmp_path):
    eq = all_empty(tmp_path)
    assert allowed(eq, "get_reference", name="Co") == "SAMPLE GET_REF Co"
    assert allowed(eq, "get_filter", name="Z-1") == "SAMPLE GET_FILTER Z-1"

    eq.set_known(REFERENCE, "Co")
    eq.set_known(FILTER, "Z-1")
    assert "already loaded" in refused(eq, "get_reference", name="Ni")
    assert allowed(eq, "return_reference") == "SAMPLE RETURN_REF Co"
    assert allowed(eq, "return_filter") == "SAMPLE RETURN_FILTER"

    eq.set_known(FRAME, {"container": 0, "slot": 9})
    for name, args in [
        ("return_reference", {}),
        ("return_filter", {}),
        ("get_reference", {"name": "Ni"}),
        ("get_filter", {"name": "Z-2"}),
    ]:
        assert "Return the frame to storage first" in refused(eq, name, **args)


def test_unknown_frame_blocks_reference_changes(tmp_path):
    eq = all_empty(tmp_path)
    eq.set_unknown(FRAME, "test")
    assert "Declare" in refused(eq, "get_reference", name="Co")


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("get_frame", {"container": 3, "slot": 0}),
        ("get_frame", {"container": 0, "slot": 12}),
        ("get_frame", {"container": -1, "slot": 0}),
        ("get_frame", {"container": "0", "slot": 0}),
        ("get_frame", {"container": True, "slot": 0}),
        ("get_frame", {"container": 0}),
        ("get_frame", {"container": 0, "slot": 0, "extra": 1}),
        ("get_reference", {"name": "Co\r\nSAMPLE GET_FRAME 0 0"}),
        ("get_reference", {"name": "Co Ni"}),
        ("get_reference", {"name": ""}),
        ("return_frame", {"container": 0, "slot": 0}),
    ],
)
def test_bad_parameters_are_rejected(name, args):
    with pytest.raises(CommandRefused):
        COMMANDS[name].clean_args(args)


def test_declarations_are_validated():
    assert clean_declaration(FRAME, None) is None
    assert clean_declaration(FRAME, {"container": 1, "slot": 4}) == {"container": 1, "slot": 4}
    assert clean_declaration(REFERENCE, "Co") == "Co"
    for item, value in [(FRAME, {"container": 9, "slot": 0}), (FRAME, "0 9"), (REFERENCE, "a b"), ("motor", 1)]:
        with pytest.raises(CommandRefused):
            clean_declaration(item, value)
