"""The backend's own check of the motor interlock rules.

The control server enforces these rules itself. The backend checks them too,
from the same rule files, before it sends a motor move: a second layer in case
the control server's implementation has a bug. This is not a safety system.

Rule files (one per motor, same format as the control server's):

    {"motor_type": "TRINAMIC", "motor_name": "DET_Y", "rules": [
        {"position_min": "-10.0", "position_max": "145.0",   current position of this motor
         "target_min": "-10.0",   "target_max": "145.0",     where the move wants to go
         "direction": "0",                                   -1 negative, 0 any, 1 positive
         "conditions": [                                     where other motors must be
            {"motor_type": "TRINAMIC", "motor_name": "DET_X",
             "position_min": "190.0", "position_max": "245.0", "message": "..."}]}]}

How a move is judged:
- A rule applies when the motor's current position and the target are inside
  the rule's ranges (limits included) and the move goes in the rule's direction.
- Every condition of every rule that applies must hold.
- If no rule covers the current position and the target at all (whatever the
  direction), the move is refused: the rules do not describe it. This also
  catches a reading that sits just outside a range, such as -0.001.
- A move that is covered, but only by rules for the other direction, is
  allowed (for example lowering PIPS_VERT). Both decided 2026-11-17.
- If a position needed for the check is not available, the move is refused.
"""

import json
from dataclasses import dataclass
from pathlib import Path


OUTSIDE_RULES = "current position or target outside of defined rules"


class RuleFileError(Exception):
    """A rule file is missing a field or has a value that is not a number."""


@dataclass(frozen=True)
class Condition:
    motor_type: str
    motor_name: str
    position_min: float
    position_max: float
    message: str


@dataclass(frozen=True)
class Rule:
    position_min: float
    position_max: float
    target_min: float
    target_max: float
    direction: int
    conditions: tuple[Condition, ...]

    def covers(self, current: float, target: float) -> bool:
        """Whether the position and the target are inside this rule's ranges."""
        return (
            self.position_min <= current <= self.position_max
            and self.target_min <= target <= self.target_max
        )

    def applies(self, current: float, target: float) -> bool:
        if not self.covers(current, target):
            return False
        if self.direction > 0:
            return target > current
        if self.direction < 0:
            return target < current
        return True


class MotorRules:
    def __init__(self, rules: dict[str, tuple[Rule, ...]]) -> None:
        self._rules = rules

    @classmethod
    def load(cls, folder: Path) -> "MotorRules":
        rules: dict[str, tuple[Rule, ...]] = {}
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                name = data["motor_name"]
                rules[name] = tuple(_rule(raw) for raw in data["rules"])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise RuleFileError(f"{path.name}: {type(exc).__name__}: {exc}") from exc
        return cls(rules)

    @property
    def motors(self) -> list[str]:
        return sorted(self._rules)

    def needed_for(self, motor: str) -> set[str]:
        """Every motor or joint whose position a check of this motor may need."""
        needed = {motor}
        for rule in self._rules.get(motor, ()):
            needed.update(condition.motor_name for condition in rule.conditions)
        return needed

    def check_move(self, motor: str, target: float, positions: dict[str, float]) -> list[str]:
        """Reasons why the move is not allowed; an empty list means allowed.

        positions maps motor and joint names (for example "DET_X", "JOINT_1")
        to their current positions.
        """
        if motor not in self._rules:
            return [f"there are no rules for motor {motor}"]
        if motor not in positions:
            return [f"the current position of {motor} is not known"]
        current = positions[motor]
        if not any(rule.covers(current, target) for rule in self._rules[motor]):
            return [f"{OUTSIDE_RULES} ({motor} is at {current:g}, target {target:g})"]
        problems: list[str] = []
        for rule in self._rules[motor]:
            if not rule.applies(current, target):
                continue
            for condition in rule.conditions:
                where = positions.get(condition.motor_name)
                if where is None:
                    problem = f"the position of {condition.motor_name} is not known"
                elif condition.position_min <= where <= condition.position_max:
                    continue
                else:
                    problem = (
                        f"{condition.motor_name} is at {where:g}, "
                        f"must be between {condition.position_min:g} and {condition.position_max:g}"
                    )
                    if condition.message:
                        problem += f" ({condition.message})"
                if problem not in problems:
                    problems.append(problem)
        return problems


def _rule(raw: dict) -> Rule:
    direction = int(raw["direction"])
    if direction not in (-1, 0, 1):
        raise ValueError(f"direction must be -1, 0 or 1, not {direction}")
    return Rule(
        position_min=float(raw["position_min"]),
        position_max=float(raw["position_max"]),
        target_min=float(raw["target_min"]),
        target_max=float(raw["target_max"]),
        direction=direction,
        conditions=tuple(
            Condition(
                motor_type=c["motor_type"],
                motor_name=c["motor_name"],
                position_min=float(c["position_min"]),
                position_max=float(c["position_max"]),
                message=c.get("message", ""),
            )
            for c in raw["conditions"]
        ),
    )
