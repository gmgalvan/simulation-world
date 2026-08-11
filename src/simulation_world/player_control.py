"""Optional direct control of the rifleman selected by the unit inspector."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .battle import Battle
    from .entities import Unit


class RiflemanController:
    """Translate held input into orders without owning combat rules."""

    def __init__(self) -> None:
        self.unit: Unit | None = None
        self.firing = False

    @property
    def active(self) -> bool:
        return self.unit is not None

    @staticmethod
    def can_take(unit: Unit | None) -> bool:
        return bool(unit is not None and unit.alive and unit.kind == "rifleman")

    def take(self, unit: Unit | None) -> bool:
        if not self.can_take(unit):
            return False
        self.release()
        self.unit = unit
        unit.manual_controlled = True
        unit.target = None
        return True

    def release(self) -> bool:
        if self.unit is None:
            return False
        self.unit.manual_controlled = False
        self.unit = None
        self.firing = False
        return True

    def update(self, keys: set[str], battle: Battle) -> bool:
        """Drive and fire once per frame; return False if control was lost."""
        unit = self.unit
        if not self.can_take(unit):
            self.release()
            return False

        throttle = float("w" in keys) - float("s" in keys)
        turn = float("a" in keys) - float("d" in keys)
        unit.update_manual_rifleman(throttle, turn)
        if self.firing:
            battle.manual_rifle_fire(unit)
        return True
