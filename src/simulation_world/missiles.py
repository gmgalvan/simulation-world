"""Guided missiles.

Separated from the dumb rounds in ``effects`` because a missile is not a
thrown rock with extra fields: it has a motor, a seeker with a field of view,
a limited airframe, and a guidance law.

The guidance law is **proportional navigation**, which is what real homing
missiles use. Instead of pointing at the target — pure pursuit, which trails
into a tail chase and loses anything fast that crosses the nose — it commands
lateral acceleration proportional to the *rotation rate of the line of sight*.
Driving that rate to zero is exactly the collision-course condition: if the
bearing to something stops changing while the range closes, you are going to
hit it. That is also why it leads the target without ever computing an
intercept point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from panda3d.core import NodePath, Point3, Vec3

GRAVITY = 9.81


@dataclass(frozen=True)
class MissileSpec:
    """Airframe and seeker of one class of missile."""

    launch_speed: float = 45.0
    burn_speed: float = 130.0
    burn_time: float = 1.1
    # Navigation constant. Real seekers use 3-5; below ~2 the missile lags the
    # target, much above 5 it wastes energy thrashing at seeker noise.
    nav_constant: float = 4.0
    max_lateral_g: float = 12.0
    # Seeker field of view, as the cosine of the half-angle off the nose.
    # Outside it the missile has lost lock and flies on ballistically.
    seeker_cos: float = 0.15
    proximity: float = 5.0      # proximity fuze radius, metres
    lifetime: float = 9.0
    damage: float = 100.0
    trail_rate: float = 26.0    # smoke puffs per second


AIR_TO_GROUND = MissileSpec(
    burn_speed=145.0,
    nav_constant=3.5,
    max_lateral_g=8.0,
    proximity=5.5,
    damage=105.0,
)

SURFACE_TO_AIR = MissileSpec(
    launch_speed=38.0,
    burn_speed=170.0,
    burn_time=1.4,
    # A SAM has to catch something crossing fast, so it navigates harder and
    # pulls more g than an air-to-ground round ever needs to. 11 g is a
    # gameplay choice, not a physical one: measured against a jet holding a
    # hard turn it lands roughly three shots in four, where 18 g never missed
    # and made the air war a formality.
    nav_constant=4.5,
    max_lateral_g=11.0,
    proximity=7.0,
    lifetime=11.0,
    damage=85.0,
)


class Missile:
    """A single guided round in flight."""

    __slots__ = (
        "np", "spec", "shooter", "target",
        "age", "speed", "heading", "prev_pos", "_trail_debt", "lost_lock",
    )

    def __init__(
        self,
        np: NodePath,
        spec: MissileSpec,
        shooter,
        target,
        direction: Vec3,
    ) -> None:
        self.np = np
        self.spec = spec
        self.shooter = shooter
        self.target = target
        self.age = 0.0
        self.speed = spec.launch_speed
        self.heading = Vec3(direction)
        if self.heading.length_squared() > 1e-9:
            self.heading.normalize()
        self.prev_pos = Point3(np.get_pos())
        self._trail_debt = 0.0
        self.lost_lock = False

    # ------------------------------------------------------------------
    @property
    def position(self) -> Point3:
        return self.np.get_pos()

    @property
    def velocity(self) -> Vec3:
        return self.heading * self.speed

    @property
    def expired(self) -> bool:
        return self.age > self.spec.lifetime

    def tracking(self) -> bool:
        return (
            not self.lost_lock
            and self.target is not None
            and getattr(self.target, "alive", False)
        )

    # ------------------------------------------------------------------
    def update(self, dt: float) -> None:
        self.age += dt
        self._accelerate(dt)
        if self.tracking():
            self._guide(dt)
        # Integrated by hand rather than by Bullet. A projectile that is a
        # rigid body in the world gets its contacts resolved by the solver and
        # visibly bounces off whatever it was supposed to destroy.
        self.np.set_pos(self.np.get_pos() + self.heading * (self.speed * dt))
        self._point_along_velocity()

    def _accelerate(self, dt: float) -> None:
        """Motor burn, then coast with a little drag."""
        if self.age < self.spec.burn_time:
            gain = min(1.0, dt / max(self.spec.burn_time, 1e-3) * 2.2)
            self.speed += (self.spec.burn_speed - self.speed) * gain
        else:
            # Coast drag, with a floor. This is compounded every frame, so a
            # value that looks small still bleeds the round below target speed
            # in seconds — at which point it can never catch anything again.
            self.speed = max(self.spec.burn_speed * 0.55, self.speed * (1.0 - 0.035 * dt))

    def _guide(self, dt: float) -> None:
        """Proportional navigation: null the line-of-sight rotation rate."""
        missile_v = self.velocity
        los = Vec3(self.target.position) - self.position
        range_sq = los.length_squared()
        if range_sq < 1e-6 or missile_v.length_squared() < 1e-6:
            return

        heading = Vec3(missile_v)
        heading.normalize()
        bearing = Vec3(los)
        bearing.normalize()

        # Seeker gimbal limit: once the target slides outside the cone the
        # missile is blind and simply carries on.
        if bearing.dot(heading) < self.spec.seeker_cos:
            self.lost_lock = True
            return

        relative_v = Vec3(self.target.velocity) - missile_v
        # Line-of-sight rotation rate, as a vector: omega = (r x v) / (r . r)
        omega = los.cross(relative_v) / range_sq
        closing = -bearing.dot(relative_v)
        if closing <= 0.0:
            # Range is opening, so the line-of-sight rate carries no useful
            # intercept information. Falling through to pure pursuit at least
            # turns the missile onto the target; doing nothing here left it
            # flying dead straight while the target ran away.
            self._pursue(dt, bearing)
            return

        # True proportional navigation: accelerate perpendicular to the flight
        # path, proportionally to how fast the bearing is sweeping.
        command = omega.cross(heading) * (self.spec.nav_constant * closing)

        limit = self.spec.max_lateral_g * GRAVITY
        magnitude = command.length()
        if magnitude > limit:
            command *= limit / magnitude

        steered = missile_v + command * dt
        if steered.length_squared() < 1e-9:
            return
        steered.normalize()
        self.heading = steered

    def _pursue(self, dt: float, bearing: Vec3) -> None:
        """Fallback: swing the nose onto the target."""
        velocity = self.velocity
        steered = velocity + (bearing * self.speed - velocity) * min(1.0, 3.5 * dt)
        if steered.length_squared() < 1e-9:
            return
        steered.normalize()
        self.heading = steered

    def _point_along_velocity(self) -> None:
        if self.heading.length_squared() > 1e-6:
            self.np.look_at(self.position + self.heading)

    # ------------------------------------------------------------------
    def fuzed(self) -> bool:
        """Proximity fuze: real SAMs never need a direct hit."""
        if self.target is None or not getattr(self.target, "alive", False):
            return False
        return (Vec3(self.target.position) - self.position).length() <= self.spec.proximity

    def trail_puffs(self, dt: float) -> int:
        """How many smoke puffs to lay down this frame."""
        self._trail_debt += self.spec.trail_rate * dt * (
            1.0 if self.age < self.spec.burn_time else 0.35
        )
        count = int(self._trail_debt)
        self._trail_debt -= count
        return count


def intercept_direction(start: Point3, target, speed: float) -> Vec3:
    """Launch heading: a first-order lead so the missile starts out pointed
    roughly where the target will be, instead of where it is."""
    delta = Vec3(target.position) - Vec3(start)
    distance = delta.length()
    if distance < 1e-6:
        return Vec3(0, 1, 0)
    flight = distance / max(speed, 1e-3)
    aim = Vec3(target.position) + Vec3(target.velocity) * flight - Vec3(start)
    if aim.length_squared() < 1e-9:
        aim = delta
    aim.normalize()
    return aim
