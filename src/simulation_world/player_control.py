"""Optional direct control of units selected by the unit inspector."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .battle import Battle
    from .entities import Unit


CONTROLLABLE_KINDS = frozenset({"rifleman", "jet"})


class PlayerController:
    """Translate held input into orders without owning combat rules."""

    def __init__(self) -> None:
        self.unit: Unit | None = None
        self.firing = False
        self.throttle = 1.0
        self.locked_target = None

    @property
    def active(self) -> bool:
        return self.unit is not None

    @staticmethod
    def can_take(unit: Unit | None) -> bool:
        return bool(
            unit is not None and unit.alive and unit.kind in CONTROLLABLE_KINDS
        )

    def take(self, unit: Unit | None) -> bool:
        if not self.can_take(unit):
            return False
        self.release()
        self.unit = unit
        unit.manual_controlled = True
        unit.target = None
        self.throttle = 1.0
        self.locked_target = None
        return True

    def release(self) -> bool:
        if self.unit is None:
            return False
        self.unit.manual_controlled = False
        self.unit = None
        self.firing = False
        self.locked_target = None
        return True

    def update(self, dt: float, keys: set[str], battle: Battle, terrain) -> bool:
        """Drive and fire once per frame; return False if control was lost."""
        unit = self.unit
        if not self.can_take(unit):
            self.release()
            return False

        turn = float("a" in keys) - float("d" in keys)
        if unit.kind == "rifleman":
            throttle = float("w" in keys) - float("s" in keys)
            unit.update_manual_rifleman(throttle, turn)
            if self.firing:
                battle.manual_rifle_fire(unit)
        elif unit.kind == "jet":
            throttle_step = float("w" in keys) - float("s" in keys)
            self.throttle = max(
                0.62,
                min(1.25, self.throttle + throttle_step * 0.42 * dt),
            )
            climb = float("e" in keys or "arrow_up" in keys) - float(
                "q" in keys or "arrow_down" in keys
            )
            unit.update_manual_jet(terrain, self.throttle, turn, climb)
            self.locked_target = battle.manual_jet_target(unit)
            if self.firing:
                battle.manual_jet_fire(unit, self.locked_target)
        return True
