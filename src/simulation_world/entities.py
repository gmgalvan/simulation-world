"""Combat units: a Bullet rigid body, a model, and a flight/drive controller.

The physics engine handles contacts, gravity and ballistics; the flight model
for the helicopters is written by hand and fed into Bullet as forces, which is
how it works in real games too — a rigid-body solver knows nothing about rotors.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
from dataclasses import dataclass

from panda3d.bullet import BulletBoxShape, BulletRigidBodyNode
from panda3d.core import NodePath, Point3, Vec3

GRAVITY = 9.81
# Grade above which a ground unit would rather go round (tangent, ~29 deg) and
# the grade above which it genuinely cannot pass (~45 deg). Only the second one
# makes it refuse: treating the first as a wall trapped anything that spawned
# in a hollow, which then just followed the gully instead of joining the fight.
MAX_CLIMB = 0.55
IMPASSABLE_CLIMB = 1.0
_ids = itertools.count()


@dataclass(frozen=True)
class UnitSpec:
    mass: float
    half_extents: Vec3
    max_health: float
    cruise_speed: float
    turn_rate: float          # rad/s
    attack_range: float
    damage: float
    fire_period: float
    model_length: float       # used to scale placeholder/real models alike
    # Fraction of attack_range the unit tries to hold. Low = closes in and
    # brawls; high = stays at the edge of its reach.
    stand_off: float = 0.45
    # Drive-force gain. Ground friction fights this force, and the two balance
    # out at  cruise_speed - mu*g/gain  — so too low a gain silently caps a
    # unit far below its cruise speed no matter what cruise_speed says.
    drive_gain: float = 2.5
    # Seconds a ground unit stands still after firing. Armour and launcher
    # teams shoot from a halt; a rifleman fires on the move. Kept well under
    # fire_period so they still advance between shots instead of parking.
    fire_halt: float = 0.0


HELICOPTER = UnitSpec(
    mass=1400.0,
    half_extents=Vec3(1.1, 2.6, 1.0),
    max_health=130.0,
    cruise_speed=26.0,
    turn_rate=1.9,
    attack_range=62.0,
    damage=6.8,
    fire_period=0.30,
    model_length=11.0,
)

TANK = UnitSpec(
    mass=4200.0,
    half_extents=Vec3(1.6, 2.6, 0.9),
    max_health=190.0,
    cruise_speed=15.0,
    turn_rate=1.0,
    attack_range=78.0,
    damage=38.0,
    fire_period=2.0,
    model_length=8.0,
    stand_off=0.45,
    drive_gain=4.5,
    fire_halt=0.9,     # of a 2.0 s reload: halt, shoot, move on
)

# Tiltrotor (V-22 style). The real aircraft is a transport, not a gunship, so
# it is fast and tough but only lightly armed and less agile than the gunship.
OSPREY = UnitSpec(
    mass=15000.0,
    half_extents=Vec3(2.0, 5.0, 1.7),
    max_health=240.0,
    cruise_speed=20.0,      # hover mode; airplane mode multiplies this
    turn_rate=1.1,
    attack_range=52.0,
    damage=5.4,
    fire_period=0.42,
    model_length=16.0,
)

# Infantry. The rifle squad is cheap and numerous; the rocket team is the
# counter to armour that nothing else on foot provides.
RIFLEMAN = UnitSpec(
    mass=95.0,
    half_extents=Vec3(0.45, 0.45, 1.15),
    max_health=42.0,
    cruise_speed=7.0,
    turn_rate=3.0,
    attack_range=38.0,
    damage=3.6,
    fire_period=0.16,
    model_length=2.4,
    stand_off=0.62,
    drive_gain=9.0,
    fire_halt=0.0,     # rifles are fired on the move
)

ROCKET = UnitSpec(
    mass=105.0,
    half_extents=Vec3(0.45, 0.45, 1.15),
    max_health=62.0,
    cruise_speed=6.0,
    turn_rate=2.6,
    # Outranges a tank gun (78 m) on purpose. Now that a shell that connects
    # kills a launcher team outright, their only defence is shooting first.
    attack_range=112.0,
    damage=74.0,
    fire_period=2.5,
    model_length=2.4,
    # Hold near maximum reach: standing off is now the whole survival plan.
    stand_off=0.85,
    drive_gain=9.0,
    fire_halt=1.5,     # of a 3.8 s reload: you cannot aim a launcher running
)

# Fixed-wing strike fighter. Unlike everything else that flies here it cannot
# hover: it holds speed, overshoots, and has to come round again.
JET = UnitSpec(
    mass=13000.0,
    half_extents=Vec3(5.0, 7.0, 1.0),
    max_health=155.0,
    cruise_speed=88.0,
    turn_rate=0.8,        # wide circles; that is the whole character of it
    attack_range=190.0,   # stands off and shoots down at things
    damage=105.0,         # one hit ruins whatever it lands on
    fire_period=4.2,      # guided missiles, not a gun
    model_length=15.0,
)

# Self-propelled anti-air. Outranges the jet on purpose: that reach is the
# entire reason it exists. It keeps a token ability to shoot at ground units —
# real systems carry a cannon alongside the missiles — which is not decoration:
# a purely anti-air unit left facing infantry can never finish the battle.
SAM = UnitSpec(
    mass=3200.0,
    half_extents=Vec3(1.5, 2.6, 1.3),
    max_health=125.0,
    cruise_speed=11.0,
    turn_rate=1.2,
    attack_range=155.0,
    damage=85.0,
    fire_period=2.8,
    model_length=8.5,
    stand_off=0.8,
    drive_gain=4.5,
    fire_halt=1.2,
)

# Guided-missile destroyer. It is deliberately a stand-off unit: its launch
# cells can engage aircraft and land targets long before a naval gun could.
DESTROYER = UnitSpec(
    mass=8_500.0,
    half_extents=Vec3(4.5, 35.0, 4.9),
    max_health=360.0,
    cruise_speed=12.0,
    turn_rate=0.48,
    attack_range=1_200.0,
    damage=135.0,
    fire_period=4.2,
    model_length=70.0,
    stand_off=0.55,
    drive_gain=3.2,
)

# Cruise-missile submarine. Almost harmless up close: its whole reason to
# exist is the strategic salvo, which used to live on the destroyer where a
# weapon of unlimited reach made much less sense.
SUBMARINE = UnitSpec(
    mass=7_200.0,
    half_extents=Vec3(3.1, 26.0, 2.0),
    max_health=240.0,
    cruise_speed=7.0,
    turn_rate=0.35,
    attack_range=520.0,
    damage=70.0,
    fire_period=9.0,
    model_length=53.0,
    stand_off=0.6,
    drive_gain=3.0,
)

SPECS = {
    "helicopter": HELICOPTER,
    "tank": TANK,
    "osprey": OSPREY,
    "rifleman": RIFLEMAN,
    "rocket": ROCKET,
    "jet": JET,
    "sam": SAM,
    "destroyer": DESTROYER,
    "submarine": SUBMARINE,
}

# Kinds that walk or drive rather than fly.
GROUND = frozenset({"tank", "rifleman", "rocket", "sam"})
INFANTRY = frozenset({"rifleman", "rocket"})
NAVAL = frozenset({"destroyer", "submarine"})
# How deep a boat runs when it is not shooting, and how long it stays up.
SUBMERGED_DEPTH = 2.6
SURFACING_SECONDS = 7.0
# A submerged boat is invisible to anything without sonar or a look from above:
# only aircraft and other submarines get to engage it at all.
SUBSURFACE = frozenset({"submarine"})
# A destroyer belongs here: hunting submarines is what the type is for, and
# leaving it out made the boats invulnerable to the one ship built to kill them.
DETECTS_SUBSURFACE = frozenset(
    {"jet", "helicopter", "osprey", "submarine", "destroyer"}
)

# How much faster the tiltrotor is with the nacelles rotated fully forward.
OSPREY_FORWARD_BOOST = 1.35
OSPREY_TILT_SECONDS = 1.8

# Kinds that hold themselves up with rotors rather than wheels/tracks.
FLYING = frozenset({"helicopter", "osprey", "jet"})
# Fixed wing: needs airspeed, cannot stop, only shoots straight ahead.
FIXED_WING = frozenset({"jet"})


class Unit:
    def __init__(
        self,
        kind: str,
        team: int,
        spec: UnitSpec,
        model: NodePath,
        parent: NodePath,
        world,
        position: Vec3,
        heading: float,
        cruise_alt: float = 0.0,
    ) -> None:
        self.id = next(_ids)
        self.kind = kind
        self.team = team
        self.spec = spec
        self.health = spec.max_health
        self.alive = True
        self.cooldown = 0.0
        # Independent close-in defensive channel for destroyers. It prevents
        # an incoming-missile burst from being tied to the main gun/VLS reload.
        self.ciws_cooldown = 0.0
        # Strategic naval strike is independent of the normal weapon cooldown.
        # Only the submarine carries the strategic battery.
        self.strategic_cooldown = 30.0 if kind in SUBSURFACE else 0.0
        self.surface_time = 0.0    # seconds left running on the surface
        self.pending_salvo = False  # ordered up to shoot, not yet shallow enough
        self.cruise_alt = cruise_alt
        self.orbit_dir = 1.0
        self.tilt = 0.0  # tiltrotor nacelles: 0 = hover, 1 = airplane mode
        self.halt = 0.0  # seconds left standing still to shoot
        self.stuck_time = 0.0
        self.escape_time = 0.0
        self.escape_dir = Vec3(0, 1, 0)
        self.climb_grade = 0.0  # slope straight ahead, slows the unit down
        self.target: Unit | None = None
        # The autonomous battle skips movement and firing while a player owns
        # this unit.  The controller lives outside Unit so input concerns do
        # not leak into the reusable physics/entity layer.
        self.manual_controlled = False
        # Procedural infantry animation.  The phase advances from actual
        # horizontal velocity, so steep climbs, firing halts and jams are
        # visible in the gait instead of feet skating at a fixed rate.
        self.gait_phase = (self.id * 2.39996) % math.tau
        self.gait_blend = 0.0
        bounds = model.get_tight_bounds()
        # Root height that leaves roughly 42 cm of the lowest hull immersed.
        # Computing it from the model also keeps optional external destroyer
        # assets on their waterline instead of assuming placeholder geometry.
        self.waterline_offset = (
            -0.42 - float(bounds[0].z)
            if kind in NAVAL and bounds is not None
            else 0.0
        )

        node = BulletRigidBodyNode(f"{kind}-{self.id}")
        node.add_shape(BulletBoxShape(spec.half_extents))
        node.set_mass(spec.mass)
        node.set_friction(0.3 if kind in FLYING else (0.55 if kind in INFANTRY else 0.8))
        node.set_restitution(0.05)
        node.set_linear_damping(0.25 if kind in FLYING else 0.4)
        node.set_angular_damping(0.7)
        # Without this, Bullet puts slow bodies to sleep and they stop responding.
        node.set_deactivation_enabled(False)
        # Yaw-only while alive, for both kinds: a free-rotating hull flips onto
        # its roof on steep ground and never recovers. Slope tilt is applied
        # cosmetically to the model instead. take_damage() releases this so the
        # wreck tumbles for real.
        node.set_angular_factor(Vec3(0, 0, 1))
        node.set_python_tag("unit", self)

        self.node = node
        self.np: NodePath = parent.attach_new_node(node)
        self.np.set_pos(position)
        self.np.set_h(math.degrees(heading))

        # Cosmetic layer: banking and rotor spin never touch the rigid body.
        self.model_np = model
        self.model_np.reparent_to(self.np)
        self.world = world
        world.attach(node)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    @property
    def position(self) -> Point3:
        return self.np.get_pos()

    @property
    def velocity(self) -> Vec3:
        return self.node.get_linear_velocity()

    @property
    def heading(self) -> float:
        return math.radians(self.np.get_h())

    @property
    def forward(self) -> Vec3:
        return self.np.get_quat().get_forward()

    @property
    def hp_frac(self) -> float:
        return max(0.0, self.health / self.spec.max_health)

    def muzzle(self) -> Point3:
        offset, height = {
            "helicopter": (3.0, 0.2),
            "osprey": (4.0, -0.4),   # door gun, below the fuselage line
            "rifleman": (1.30, 0.56),
            "rocket": (1.88, 0.72),
            "jet": (6.5, -0.8),   # off the rails, below the wing
            "sam": (1.6, 2.4),    # off the launcher rack on top
            "destroyer": (18.5, 2.0),  # forward VLS bank above the deck
        }.get(self.kind, (4.2, 1.1))
        return self.position + self.forward * offset + Vec3(0, 0, height)

    # The three mount points below are read off the destroyer placeholder,
    # whose geometry is authored at 1/2.15 scale. Keep them in step with
    # `build_placeholder_destroyer` or the muzzle flashes drift off the model.
    def naval_gun_muzzle(self) -> Point3:
        """Muzzle of the 127 mm bow gun on the destroyer placeholder."""
        return self.position + self.forward * 32.5 + Vec3(0, 0, 3.4)

    def naval_ciws_muzzle(self, toward: Point3 | None = None) -> Point3:
        """Whichever close-in mount bears on `toward`.

        The ship carries one station over the bridge and two on aft sponsons.
        Firing everything from the forward mount looked wrong for a missile
        coming in over the stern, which is the case the guns exist for.
        """
        offset = 18.6
        if toward is not None and self.forward.dot(toward - self.position) < 0.0:
            offset = -20.6
        return self.position + self.forward * offset + Vec3(0, 0, 6.9)

    def can_bear(self, target: Unit) -> bool:
        """Fixed-wing aircraft only shoot at what is lined up ahead of them.

        The test is in plan view on purpose. Measured in 3D it fails almost
        always: the jet cruises tens of metres up, so a ground target sits far
        below the nose and never enters the cone — the aircraft ends up
        decorative, flying about without ever firing.
        """
        if self.kind not in FIXED_WING:
            return True
        to_target = target.position - self.position
        flat = Vec3(to_target.x, to_target.y, 0.0)
        forward = self.forward
        nose = Vec3(forward.x, forward.y, 0.0)
        if flat.length_squared() < 1e-9 or nose.length_squared() < 1e-9:
            return True
        flat.normalize()
        nose.normalize()
        return flat.dot(nose) > 0.86  # ~30 degree cone in plan

    def is_surfaced(self, water_level: float) -> bool:
        """Shallow enough for the launch tubes to clear the water."""
        return self.position.z >= water_level + self.waterline_offset - 0.45

    def take_damage(self, amount: float) -> bool:
        """Apply damage; returns True if this hit destroyed the unit."""
        if not self.alive:
            return False
        self.health -= amount
        if self.health > 0:
            return False
        self.health = 0.0
        self.alive = False
        # Let the wreck tumble freely and drop out of the sky.
        self.node.set_angular_factor(Vec3(1, 1, 1))
        self.node.set_linear_damping(0.05)
        return True

    def destroy(self) -> None:
        self.world.remove(self.node)
        self.np.remove_node()

    # ------------------------------------------------------------------
    # Controllers
    # ------------------------------------------------------------------
    def _steer_yaw(self, desired_heading: float, dt: float) -> None:
        current = self.heading
        error = (desired_heading - current + math.pi) % (2 * math.pi) - math.pi
        rate = max(-self.spec.turn_rate, min(self.spec.turn_rate, error * 2.5))
        angular = self.node.get_angular_velocity()
        self.node.set_angular_velocity(Vec3(angular.x, angular.y, rate))

    def _drive_horizontal(self, desired: Vec3, gain: float = 1.6) -> None:
        """Push the body towards a desired horizontal velocity."""
        velocity = self.velocity
        error = Vec3(desired.x - velocity.x, desired.y - velocity.y, 0)
        self.node.apply_central_force(error * self.spec.mass * gain)

    def update_manual_rifleman(self, throttle: float, turn: float) -> None:
        """Apply direct walking orders to a player-controlled rifleman."""
        if self.kind != "rifleman" or not self.alive:
            return
        angular = self.node.get_angular_velocity()
        self.node.set_angular_velocity(
            Vec3(angular.x, angular.y, turn * self.spec.turn_rate)
        )
        speed = self.spec.cruise_speed * (1.0 if throttle >= 0.0 else 0.58)
        self._drive_horizontal(
            self.forward * (speed * throttle),
            gain=self.spec.drive_gain,
        )

    def update_manual_jet(
        self,
        terrain,
        throttle: float,
        turn: float,
        climb: float,
    ) -> None:
        """Fly a directly controlled jet without allowing hover or strafing."""
        if self.kind != "jet" or not self.alive:
            return

        angular = self.node.get_angular_velocity()
        self.node.set_angular_velocity(
            Vec3(angular.x, angular.y, turn * self.spec.turn_rate)
        )

        ground = max(
            terrain.height_at(self.position.x, self.position.y),
            terrain.water_level,
        )
        clearance = self.position.z - ground
        commanded_vertical_speed = climb * 28.0
        # Terrain avoidance remains active under manual control. It only
        # overrides the pilot close to the ground and fades above 24 metres.
        if clearance < 24.0:
            commanded_vertical_speed = max(
                commanded_vertical_speed,
                (24.0 - clearance) * 1.35,
            )
        lift = self.spec.mass * (
            GRAVITY + (commanded_vertical_speed - self.velocity.z) * 1.9
        )
        self.node.apply_central_force(Vec3(0, 0, max(0.0, lift)))

        forward = self.forward
        flat = Vec3(forward.x, forward.y, 0.0)
        if flat.length_squared() > 1e-6:
            flat.normalize()
        self._drive_horizontal(
            flat * (self.spec.cruise_speed * throttle),
            gain=self.spec.drive_gain,
        )

    def _fly(
        self,
        dt: float,
        terrain,
        target: Unit | None,
        engaged: bool,
        speed: float,
        stand_off_frac: float,
    ) -> None:
        """Shared rotorcraft controller: hold altitude, then close or circle."""
        ground = terrain.height_at(self.position.x, self.position.y)
        target_z = ground + self.cruise_alt

        # PD hover controller: weight compensation plus altitude correction.
        error_z = target_z - self.position.z
        lift = self.spec.mass * (GRAVITY + 2.2 * error_z - 2.4 * self.velocity.z)
        self.node.apply_central_force(Vec3(0, 0, max(0.0, lift)))

        if target is None:
            self._drive_horizontal(Vec3(0, 0, 0))
            return

        to_target = target.position - self.position
        distance = to_target.length()
        desired_heading = math.atan2(to_target.y, to_target.x) - math.pi / 2
        self._steer_yaw(desired_heading, dt)

        flat = Vec3(to_target.x, to_target.y, 0)
        if flat.length_squared() > 1e-6:
            flat.normalize()
        # If the shot is blocked, push in close instead of orbiting a hill forever.
        stand_off = self.spec.attack_range * (stand_off_frac if engaged else 0.2)
        if distance > stand_off:
            self._drive_horizontal(flat * speed)
        else:
            # Circle the target while shooting instead of ramming it.
            strafe = Vec3(-flat.y, flat.x, 0) * self.orbit_dir
            closing = (distance - stand_off * 0.75) * 0.35
            self._drive_horizontal((strafe + flat * closing) * speed * 0.75)

    def update_helicopter(self, dt: float, terrain, target: Unit | None, engaged: bool = True) -> None:
        self._fly(dt, terrain, target, engaged, self.spec.cruise_speed, 0.65)

    def update_jet(self, dt: float, terrain, target: Unit | None, engaged: bool = True) -> None:
        """Fly the aeroplane: always forward, turn towards the target, overshoot.

        Deliberately not the rotorcraft controller. A jet that can hover and
        strafe sideways stops being a jet — the overshoot and the long turn
        back are what make the attack runs read as attack runs.
        """
        ground = terrain.height_at(self.position.x, self.position.y)
        target_z = max(ground, terrain.water_level) + self.cruise_alt

        error_z = target_z - self.position.z
        lift = self.spec.mass * (GRAVITY + 1.4 * error_z - 1.9 * self.velocity.z)
        self.node.apply_central_force(Vec3(0, 0, max(0.0, lift)))

        if target is not None:
            to_target = target.position - self.position
            desired_heading = math.atan2(to_target.y, to_target.x) - math.pi / 2
            self._steer_yaw(desired_heading, dt)

        # Thrust is always along the nose. There is no braking and no strafing,
        # so passing the target means flying on and coming round again.
        forward = self.forward
        flat = Vec3(forward.x, forward.y, 0)
        if flat.length_squared() > 1e-6:
            flat.normalize()
        self._drive_horizontal(flat * self.spec.cruise_speed, gain=self.spec.drive_gain)

    def update_osprey(self, dt: float, terrain, target: Unit | None, engaged: bool = True) -> None:
        # Nacelles rotate forward for the transit and back upright to fight;
        # `tilt` runs 0 (helicopter mode) to 1 (airplane mode) and the model
        # layer reads it to swing the actual nacelle geometry.
        distance = (target.position - self.position).length() if target else 0.0
        wants_forward = target is not None and distance > self.spec.attack_range * 1.2
        goal = 1.0 if wants_forward else 0.0
        step = dt / OSPREY_TILT_SECONDS
        self.tilt = max(self.tilt - step, min(self.tilt + step, goal))

        speed = self.spec.cruise_speed * (1.0 + OSPREY_FORWARD_BOOST * self.tilt)
        self._fly(dt, terrain, target, engaged, speed, 0.7)

    def update_naval(self, dt: float, terrain, target: Unit | None, engaged: bool = True) -> None:
        """Keep a hull exactly on its waterline and manoeuvring at sea.

        Shared by the destroyer and the submarine: both must never use the
        ground controller, which drives straight at a target that is always
        inland and beaches them within seconds.
        """
        water = terrain.water_level
        # The procedural hull's root-to-keel distance is 1.2 m. Holding its
        # root 0.78 m above sea level immerses only the lower 0.42 m of hull.
        # This hard waterline avoids accumulated Bullet gravity and oscillation
        # making a ship appear to dive between frames.
        surfaced = water + self.waterline_offset
        position = self.position
        if self.kind in SUBSURFACE:
            # Runs submerged and only comes up to launch, so the surfacing is
            # a visible event rather than a boat that is always on top.
            self.surface_time = max(0.0, self.surface_time - dt)
            goal = surfaced if self.surface_time > 0.0 else water - SUBMERGED_DEPTH
            target_z = position.z + (goal - position.z) * min(1.0, 1.7 * dt)
        else:
            target_z = surfaced
        if abs(position.z - target_z) > 0.005:
            self.np.set_z(target_z)
        velocity = self.velocity
        self.node.set_linear_velocity(Vec3(velocity.x, velocity.y, 0.0))
        self.node.apply_central_force(Vec3(0, 0, self.spec.mass * GRAVITY))

        if target is None:
            self._drive_horizontal(Vec3(0, 0, 0), gain=self.spec.drive_gain)
            return

        to_target = target.position - self.position
        flat = Vec3(to_target.x, to_target.y, 0)
        distance = flat.length()
        if distance > 1e-6:
            flat.normalize()
            self._steer_yaw(math.atan2(flat.y, flat.x) - math.pi / 2, dt)

        # Keep sea room. Their targets are all ashore, so steering at one just
        # presses the hull against the beach until it looks stranded; nudge
        # back out whenever the water under the keel gets thin.
        depth = water - terrain.height_at(position.x, position.y)
        if depth < 4.5:
            seaward = 1.0 if position.x >= 0.0 else -1.0
            offshore = Vec3(seaward * (4.5 - depth) * 0.9, 0.0, 0.0)
            self._drive_horizontal(
                offshore * self.spec.cruise_speed, gain=self.spec.drive_gain
            )

        # Ships remain in deep water. If the next sea mile is land or shallow
        # water, turn along the coast; their missiles still cover the shore.
        probe = self.position + flat * 22.0
        clear_ahead = terrain.height_at(probe.x, probe.y) < water - 0.8
        stand_off = self.spec.attack_range * (self.spec.stand_off if engaged else 0.45)
        if clear_ahead and distance > stand_off:
            self._drive_horizontal(flat * self.spec.cruise_speed, gain=self.spec.drive_gain)
        else:
            patrol = Vec3(-flat.y, flat.x, 0) * self.orbit_dir
            patrol_probe = self.position + patrol * 22.0
            if terrain.height_at(patrol_probe.x, patrol_probe.y) >= water - 0.8:
                patrol = -patrol
                patrol_probe = self.position + patrol * 22.0
            if terrain.height_at(patrol_probe.x, patrol_probe.y) < water - 0.8:
                self._drive_horizontal(
                    patrol * self.spec.cruise_speed * 0.55, gain=self.spec.drive_gain
                )
            else:
                self._drive_horizontal(Vec3(0, 0, 0), gain=self.spec.drive_gain)

    def _probe_costs(self, directions, terrain, probe: float):
        """Cost of driving each way, in a single vectorised terrain query.

        One numpy call for the whole fan rather than two per direction: the
        naive version issued fourteen array-building height queries per unit
        per frame and dominated the cost of the simulation.
        """
        position = self.position
        count = len(directions)
        xs = np.empty(count + 1)
        ys = np.empty(count + 1)
        for i, direction in enumerate(directions):
            xs[i] = position.x + direction.x * probe
            ys[i] = position.y + direction.y * probe
        xs[count] = position.x
        ys[count] = position.y

        heights = terrain.heights(xs, ys)
        here = heights[count]
        water = getattr(terrain, "water_level", None)

        costs = []
        grades = []
        for i in range(count):
            height = heights[i]
            grade = (height - here) / probe
            grades.append(grade)

            cost = 0.0
            if water is not None and height < water + 1.0:
                # Nothing floats here, so water is close to impassable.
                cost += 100.0 + (water - height)
            if grade > IMPASSABLE_CLIMB:
                cost += 90.0 * (grade - IMPASSABLE_CLIMB)
            elif grade > MAX_CLIMB:
                # Climbable, just slow and unpleasant — a mild preference to
                # go round, not a refusal.
                cost += 6.0 * (grade - MAX_CLIMB)
            costs.append(cost)
        return costs, grades

    def _avoid_obstacles(self, heading: Vec3, terrain) -> Vec3:
        """Bend the heading around water and slopes it cannot climb.

        Probes a fan of directions and takes the cheapest. Measured on the
        default terrain, every jam a ground unit got into was against a slope,
        not water — steering only around rivers solved half the problem.
        """
        if heading.length_squared() < 1e-6:
            return heading

        probe = 24.0
        side = Vec3(-heading.y, heading.x, 0.0)
        blends = (0.6, 0.6, 1.2, 1.2, 2.2, 2.2)
        directions = [heading]
        for index, blend in enumerate(blends):
            turn = side if index % 2 == 0 else -side
            candidate = heading + turn * blend
            if candidate.length_squared() > 1e-9:
                candidate.normalize()
            directions.append(candidate)

        costs, grades = self._probe_costs(directions, terrain, probe)
        self.climb_grade = grades[0]
        if costs[0] <= 0.0:
            return heading

        best, best_cost, best_index = heading, costs[0], 0
        for index, blend in enumerate(blends):
            # Prefer the smallest detour that clears the obstacle.
            cost = costs[index + 1] + blend * 0.6
            if cost < best_cost:
                best, best_cost, best_index = directions[index + 1], cost, index + 1
        self.climb_grade = grades[best_index]
        return best

    def _escape_heading(self, heading: Vec3, terrain) -> Vec3:
        """Sidestep direction for a unit that has jammed against something."""
        side = Vec3(-heading.y, heading.x, 0.0)
        if side.length_squared() < 1e-9:
            return heading
        side.normalize()
        costs, _ = self._probe_costs([side, -side], terrain, 26.0)
        left, right = costs
        return side if left <= right else -side

    def _unjam(self, dt: float, terrain, heading: Vec3) -> Vec3 | None:
        """Track whether the unit is actually moving, and break it loose.

        A catch-all: the obstacle probe looks 24 m ahead and cannot see every
        way a body can wedge itself, so this notices the symptom — wants to
        advance, is not advancing — whatever the cause.
        """
        if self.escape_time > 0.0:
            self.escape_time -= dt
            return self.escape_dir

        speed = Vec3(self.velocity.x, self.velocity.y, 0.0).length()
        if speed < 1.0:
            self.stuck_time += dt
        else:
            self.stuck_time = 0.0

        if self.stuck_time > 1.5:
            self.stuck_time = 0.0
            self.escape_time = 2.5
            self.escape_dir = self._escape_heading(heading, terrain)
            return self.escape_dir
        return None


    def update_ground(self, dt: float, terrain, target: Unit | None, engaged: bool = True) -> None:
        """Drive/run towards the target, hold at weapon range, keep manoeuvring."""
        if target is None:
            self._drive_horizontal(Vec3(0, 0, 0), gain=self.spec.drive_gain)
            return

        to_target = target.position - self.position
        flat = Vec3(to_target.x, to_target.y, 0)
        distance = flat.length()
        if distance > 1e-6:
            flat.normalize()
        desired_heading = math.atan2(to_target.y, to_target.x) - math.pi / 2
        self._steer_yaw(desired_heading, dt)

        # Firing halt: keep laying the gun on the target, but plant the tracks.
        self.halt = max(0.0, self.halt - dt)
        if self.halt > 0.0:
            self._drive_horizontal(Vec3(0, 0, 0), gain=self.spec.drive_gain)
            return

        escape = self._unjam(dt, terrain, flat)
        flat = escape if escape is not None else self._avoid_obstacles(flat, terrain)

        stand_off = self.spec.attack_range * (self.spec.stand_off if engaged else 0.2)
        if distance > stand_off:
            # Drive mostly towards the target rather than purely along the
            # current facing: while the hull is still slewing round, pure
            # forward thrust makes almost no headway and it looks parked.
            heading = self.forward
            heading_flat = Vec3(heading.x, heading.y, 0)
            if heading_flat.length_squared() > 1e-6:
                heading_flat.normalize()
            drive = flat * 0.7 + heading_flat * 0.3
            if drive.length_squared() > 1e-6:
                drive.normalize()
            # Climbing is allowed but slow: this is what lets a unit grind out
            # of a hollow instead of being walled in by its own pathing.
            climb = 1.0 / (1.0 + 2.4 * max(0.0, self.climb_grade))
            self._drive_horizontal(
                drive * self.spec.cruise_speed * climb, gain=self.spec.drive_gain
            )
        else:
            # Never a dead stop once in range — keep jockeying sideways so the
            # line stays alive instead of freezing into a firing-range diorama.
            strafe = Vec3(-flat.y, flat.x, 0) * self.orbit_dir
            self._drive_horizontal(
                strafe * self.spec.cruise_speed * 0.45, gain=self.spec.drive_gain * 0.8
            )

    def update_wreck(self, dt: float) -> None:
        """Dead units keep falling under gravity; nothing else to do."""
        return
