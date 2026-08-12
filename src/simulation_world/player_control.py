"""Optional direct control of units selected by the unit inspector."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .battle import Battle
    from .entities import Unit


CONTROLLABLE_KINDS = frozenset({"rifleman", "jet", "helicopter"})


class PlayerController:
    """Translate held input into orders without owning combat rules."""

    def __init__(self) -> None:
        self.unit: Unit | None = None
        self.firing = False
        self.gun_firing = False
        self.throttle = 1.0
        self.locked_target = None
        self.shot_fired = False

    @property
    def active(self) -> bool:
        return self.unit is not None

    def validate(self) -> bool:
        """Drop ownership immediately when the controlled unit is no longer valid."""
        if self.can_take(self.unit):
            if self.locked_target is not None and not getattr(
                self.locked_target, "alive", False
            ):
                self.locked_target = None
            return True
        self.release()
        return False

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
        self.shot_fired = False
        return True

    def release(self) -> bool:
        if self.unit is None:
            return False
        self.unit.manual_controlled = False
        self.unit = None
        self.firing = False
        self.gun_firing = False
        self.locked_target = None
        self.shot_fired = False
        return True

    def update(self, dt: float, keys: set[str], battle: Battle, terrain) -> bool:
        """Drive and fire once per frame; return False if control was lost."""
        unit = self.unit
        self.shot_fired = False
        if not self.validate():
            return False

        turn = float("a" in keys) - float("d" in keys)
        if unit.kind == "rifleman":
            throttle = float("w" in keys) - float("s" in keys)
            unit.update_manual_rifleman(throttle, turn, dt)
            if self.firing:
                battle.manual_rifle_fire(unit)
        elif unit.kind == "helicopter":
            # Same key layout as the jet so nothing has to be relearned, but
            # the stick means speed rather than throttle: releasing it stops
            # the aircraft in a hover instead of leaving it flying on.
            cyclic = float("w" in keys) - float("s" in keys)
            collective = float("e" in keys or "arrow_up" in keys) - float(
                "q" in keys or "arrow_down" in keys
            )
            strafe = float("arrow_right" in keys) - float("arrow_left" in keys)
            unit.update_manual_helicopter(
                terrain, collective, turn, cyclic, strafe, dt
            )
            self.throttle = cyclic
            self.locked_target = battle.manual_heli_target(unit)
            if self.firing:
                self.shot_fired = battle.manual_heli_fire(
                    unit, self.locked_target
                )
            if self.gun_firing:
                battle.manual_heli_gun(unit)
        elif unit.kind == "jet":
            throttle_step = float("w" in keys) - float("s" in keys)
            self.throttle = max(
                0.62,
                min(1.25, self.throttle + throttle_step * 0.42 * dt),
            )
            climb = float("e" in keys or "arrow_up" in keys) - float(
                "q" in keys or "arrow_down" in keys
            )
            unit.update_manual_jet(terrain, self.throttle, turn, climb, dt)
            self.locked_target = battle.manual_jet_target(unit)
            if self.firing:
                self.shot_fired = battle.manual_jet_fire(
                    unit, self.locked_target
                )
        return True
