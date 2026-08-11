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
    # Depth below the water line at which the weapon runs. Set for torpedoes;
    # None for anything that flies. A running weapon holds this depth and
    # steers only in the horizontal plane.
    run_depth: float | None = None
    # Positive only for long-range missiles. It is the extra height at the
    # apex of a guided parabolic arc: vertical boost, mid-course loft, then a
    # terminal dive using proportional navigation.
    loft_altitude: float = 0.0


AIR_TO_GROUND = MissileSpec(
    burn_speed=145.0,
    nav_constant=3.5,
    max_lateral_g=8.0,
    proximity=5.5,
    damage=105.0,
)

TORPEDO = MissileSpec(
    launch_speed=14.0,
    burn_speed=32.0,        # water is not air: a torpedo is slow
    burn_time=2.0,
    nav_constant=3.2,
    max_lateral_g=2.5,      # and it turns like a bus
    seeker_cos=0.05,
    proximity=5.5,
    lifetime=26.0,          # but it runs for a long way
    damage=190.0,           # and a hit on a hull is close to decisive
    trail_rate=11.0,
    run_depth=1.6,
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

# Ship-launched multipurpose missile: strategic range from the side seas, but
# less agile than a dedicated SAM against a hard-turning fighter.
NAVAL_STRIKE = MissileSpec(
    launch_speed=42.0,
    burn_speed=230.0,
    burn_time=1.8,
    nav_constant=3.8,
    max_lateral_g=9.0,
    proximity=6.5,
    lifetime=24.0,
    damage=135.0,
)

# Four-minute strategic salvo. Its guidance has no tactical range gate: the
# long lifetime and lofted mid-course arc let it reach any active combat unit
# in the streamed world, including targets hidden behind mountain ranges.
STRATEGIC_STRIKE = MissileSpec(
    launch_speed=58.0,
    burn_speed=315.0,
    burn_time=3.0,
    nav_constant=3.2,
    max_lateral_g=7.0,
    seeker_cos=-0.25,
    proximity=8.0,
    lifetime=120.0,
    damage=155.0,
    trail_rate=30.0,
    loft_altitude=220.0,
)


class Missile:
    """A single guided round in flight."""

    __slots__ = (
        "np", "spec", "shooter", "target",
        "age", "speed", "heading", "prev_pos", "launch_position",
        "launch_horizontal_range", "_trail_debt", "lost_lock", "run_level",
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
        self.launch_position = Point3(np.get_pos())
        initial = Vec3(target.position) - self.launch_position
        self.launch_horizontal_range = max(1.0, math.hypot(initial.x, initial.y))
        self._trail_debt = 0.0
        self.lost_lock = False
        self.run_level = None   # set by the launcher for underwater weapons

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
        if self.spec.run_depth is not None:
            self._hold_depth()
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
        target_position = Vec3(self.target.position)
        horizontal_delta = Vec3(
            target_position.x - self.position.x,
            target_position.y - self.position.y,
            0.0,
        )
        horizontal = horizontal_delta.length()

        if self.spec.loft_altitude > 0.0 and horizontal > 260.0:
            # Boost straight out of the VLS before pitching over. Afterwards
            # chase a point a short distance ahead on a parabola rather than
            # aiming directly at the final target through the mountain.
            if self.age < 1.35:
                bearing = Vec3(0, 0, 1)
            else:
                direction = Vec3(horizontal_delta)
                if direction.length_squared() > 1e-9:
                    direction.normalize()
                lookahead = min(280.0, horizontal)
                progress = max(
                    0.0,
                    min(1.0, 1.0 - horizontal / self.launch_horizontal_range),
                )
                ahead_progress = min(
                    1.0,
                    progress + lookahead / self.launch_horizontal_range,
                )
                baseline_z = (
                    self.launch_position.z
                    + (target_position.z - self.launch_position.z) * ahead_progress
                )
                arc_z = (
                    4.0
                    * self.spec.loft_altitude
                    * ahead_progress
                    * (1.0 - ahead_progress)
                )
                waypoint = Vec3(
                    self.position.x + direction.x * lookahead,
                    self.position.y + direction.y * lookahead,
                    baseline_z + arc_z,
                )
                bearing = waypoint - self.position
                if bearing.length_squared() > 1e-9:
                    bearing.normalize()
            self._pursue(dt, bearing)
            return

        # Terminal phase: switch from the planned arc to a real homing law so
        # moving vehicles can still be intercepted during the final dive.
        los = target_position - self.position
        range_sq = los.length_squared()
        if range_sq < 1e-6 or missile_v.length_squared() < 1e-6:
            return

        heading = Vec3(missile_v)
        heading.normalize()
        bearing = Vec3(los)
        bearing.normalize()

        # Seeker gimbal limit: once the target slides outside the cone the
        # missile is blind and simply carries on.
        if self.spec.loft_altitude <= 0.0 and bearing.dot(heading) < self.spec.seeker_cos:
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

    def _hold_depth(self) -> None:
        """Keep a torpedo on its running depth and steering only in plan.

        Without this it chases the vertical part of the bearing and either
        broaches or dives into the seabed instead of running true.
        """
        if self.run_level is None:
            return
        self.np.set_z(self.run_level)
        self.heading.set_z(0.0)
        if self.heading.length_squared() > 1e-9:
            self.heading.normalize()

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
