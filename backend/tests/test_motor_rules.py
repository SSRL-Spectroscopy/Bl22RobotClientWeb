"""Checks the rule engine against the real rule files in config/motor_rules."""

from pathlib import Path

import pytest

from robotweb.motor_rules import MotorRules, RuleFileError

RULES_DIR = Path(__file__).resolve().parents[2] / "config" / "motor_rules"

# Everything parked: detector retracted and to the side, robot away.
PARKED = {
    "DET_X": 0.0,
    "DET_Y": 130.0,
    "SOL_VERT": 25.0,
    "PIPS_VERT": 25.0,
    "OPTIC_VERT": 5.0,
    "BOT_VERT": 215.0,
    "JOINT_1": -60.0,
    "JOINT_2": 30.0,
}


@pytest.fixture(scope="module")
def rules() -> MotorRules:
    return MotorRules.load(RULES_DIR)


def at(**changes) -> dict[str, float]:
    return {**PARKED, **changes}


def test_all_rule_files_load(rules):
    assert rules.motors == [
        "BOT_VERT", "DET_X", "DET_Y", "OPTIC_VERT", "PIPS_VERT", "REF_PIVOT", "SOL_HOR", "SOL_VERT",
    ]


def test_the_c_sharp_detector_sequence_is_allowed(rules):
    # in: DET_X 0 -> 200, then DET_Y 130 -> 10
    assert rules.check_move("DET_X", 200, at()) == []
    assert rules.check_move("DET_Y", 10, at(DET_X=200)) == []
    # out: DET_Y 10 -> 130, then DET_X 200 -> 0
    assert rules.check_move("DET_Y", 130, at(DET_X=200, DET_Y=10)) == []
    assert rules.check_move("DET_X", 0, at(DET_X=200, DET_Y=130)) == []


def test_detector_cannot_move_sideways_while_it_is_in(rules):
    # The move the added DET_X rule now covers: leaving 170..230 with DET_Y in.
    problems = rules.check_move("DET_X", 0, at(DET_X=200, DET_Y=10))
    assert len(problems) == 1 and "retract detector (DET_Y) first" in problems[0]
    assert rules.check_move("DET_X", 250, at(DET_X=200, DET_Y=10)) != []
    # small sideways moves inside 170..230 are allowed with the detector in
    assert rules.check_move("DET_X", 210, at(DET_X=200, DET_Y=10)) == []


def test_detector_cannot_go_in_unless_everything_is_clear(rules):
    assert "DET_X is at 0" in rules.check_move("DET_Y", 10, at())[0]
    assert "PIPS too high" in rules.check_move("DET_Y", 10, at(DET_X=200, PIPS_VERT=132))[0]
    assert "Soller too high" in rules.check_move("DET_Y", 10, at(DET_X=200, SOL_VERT=132))[0]
    assert "BOT_VERT too low" in rules.check_move("DET_Y", 10, at(DET_X=200, BOT_VERT=100))[0]
    robot = rules.check_move("DET_Y", 10, at(DET_X=200, JOINT_1=40))
    assert len(robot) == 1 and "Robot in the wrong hemispehre" in robot[0]


def test_every_failed_condition_is_reported(rules):
    problems = rules.check_move("DET_Y", 10, at(PIPS_VERT=132, SOL_VERT=132))
    assert len(problems) == 3  # DET_X, PIPS_VERT, SOL_VERT


def test_direction_limits_a_rule_to_one_way(rules):
    detector_in = at(DET_X=200, DET_Y=10)
    # SOL_VERT rule has direction 1: moving up needs the detector parked...
    assert rules.check_move("SOL_VERT", 132, at()) == []
    assert rules.check_move("SOL_VERT", 132, detector_in) != []
    # ...moving down matches no rule and is allowed
    assert rules.check_move("SOL_VERT", 25, at(SOL_VERT=132, DET_X=200, DET_Y=10)) == []
    assert rules.check_move("PIPS_VERT", 132, detector_in) != []


def test_motor_without_conditions(rules):
    assert rules.check_move("OPTIC_VERT", 148, at()) == []
    assert rules.check_move("OPTIC_VERT", 5, at(OPTIC_VERT=148)) == []


def test_unknown_positions_refuse_the_move(rules):
    without_robot = {k: v for k, v in at(DET_X=200).items() if not k.startswith("JOINT")}
    problems = rules.check_move("DET_Y", 10, without_robot)
    assert "the position of JOINT_1 is not known" in problems
    assert rules.check_move("DET_Y", 10, {}) == ["the current position of DET_Y is not known"]
    assert rules.check_move("NO_SUCH", 1, at()) == ["there are no rules for motor NO_SUCH"]


def test_limits_are_inclusive(rules):
    assert rules.check_move("DET_Y", 10, at(DET_X=170)) == []
    assert rules.check_move("DET_Y", 10, at(DET_X=169.9)) != []
    assert rules.check_move("DET_Y", 10, at(DET_X=230)) == []
    assert rules.check_move("DET_Y", 10, at(DET_X=230.1)) != []


def test_position_or_target_outside_every_range_is_refused(rules):
    outside = "current position or target outside of defined rules"
    # a reading just under the lower end no longer switches the rule off
    assert outside in rules.check_move("PIPS_VERT", 100, at(PIPS_VERT=-0.001))[0]
    assert outside in rules.check_move("BOT_VERT", 100, at(BOT_VERT=220.001))[0]
    # a target beyond the declared travel
    assert outside in rules.check_move("DET_X", 261, at())[0]
    assert outside in rules.check_move("DET_Y", -11, at(DET_X=200))[0]
    # also when the move goes in a direction the rule does not restrict
    assert outside in rules.check_move("SOL_VERT", -1, at(SOL_VERT=132))[0]
    # motors without conditions move freely, but only inside their declared range
    assert rules.check_move("REF_PIVOT", 60, {"REF_PIVOT": 0.0}) == []
    assert outside in rules.check_move("REF_PIVOT", 66, {"REF_PIVOT": 0.0})[0]
    assert rules.check_move("SOL_HOR", 20, {"SOL_HOR": 0.0}) == []
    assert outside in rules.check_move("SOL_HOR", 21, {"SOL_HOR": 0.0})[0]
    assert outside in rules.check_move("OPTIC_VERT", 201, {"OPTIC_VERT": 73.0})[0]


def test_retracted_range_is_the_same_everywhere(rules):
    # DET_Y at 142 counts as retracted for every motor
    assert rules.check_move("DET_X", 200, at(DET_Y=142)) == []
    assert rules.check_move("PIPS_VERT", 132, at(DET_Y=142)) == []
    assert rules.check_move("SOL_VERT", 132, at(DET_Y=142)) == []
    assert rules.check_move("BOT_VERT", 209, at(DET_Y=142)) == []


def test_detector_cannot_move_sideways_with_the_soller_up(rules):
    problems = rules.check_move("DET_X", 200, at(SOL_VERT=132))
    assert len(problems) == 1 and "Soller too high" in problems[0]


def test_positions_a_check_needs(rules):
    assert rules.needed_for("DET_Y") == {"DET_Y", "DET_X", "PIPS_VERT", "SOL_VERT", "BOT_VERT", "JOINT_1", "JOINT_2"}
    assert rules.needed_for("OPTIC_VERT") == {"OPTIC_VERT"}


def test_broken_rule_file_is_reported(tmp_path):
    (tmp_path / "bad.json").write_text('{"motor_name": "X", "rules": [{"position_min": "abc"}]}')
    with pytest.raises(RuleFileError, match="bad.json"):
        MotorRules.load(tmp_path)
    (tmp_path / "bad.json").write_text(
        '{"motor_name": "X", "rules": [{"position_min": "0", "position_max": "1", "target_min": "0",'
        ' "target_max": "1", "direction": "2", "conditions": []}]}'
    )
    with pytest.raises(RuleFileError, match="direction"):
        MotorRules.load(tmp_path)
