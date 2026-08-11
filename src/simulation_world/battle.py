"""Battle orchestration: spawning, target selection, weapons and win condition."""

from __future__ import annotations

import math
import random

from panda3d.core import NodePath, Point3, Vec3

from .assets import make_health_bar
from .effects import Effects
from .stats import BattleStats
from .missiles import (
    AIR_TO_GROUND,
    NAVAL_STRIKE,
    STRATEGIC_STRIKE,
    SURFACE_TO_AIR,
    TORPEDO,
)
from .entities import (
    DETECTS_SUBSURFACE,
    SURFACING_SECONDS,
    FIXED_WING,
    GRAVITY,
    GROUND,
    INFANTRY,
    NAVAL,
    SPECS,
    SUBSURFACE,
    Unit,
)

TEAM_COLORS = {
    0: (0.92, 0.26, 0.24, 1.0),
    1: (0.24, 0.52, 0.92, 1.0),
}
TEAM_NAMES = {0: "Rojo", 1: "Azul"}

SHELL_SPEED = 110.0
ROCKET_SPEED = 78.0   # slower than a tank shell, but fast enough to connect
WRECK_LIFETIME = 4.0
# How far around the closest contact still counts as the same engagement,
# for camera framing purposes.
ENGAGEMENT_RADIUS = 190.0
# Battles run about a minute, so a four-minute cycle meant the boat fired
# once and never again. This gets a second and third salvo into a long one.
STRATEGIC_SALVO_PERIOD = 55.0
STRATEGIC_SALVO_SIZE = 3

# Target preference as (shooter kind, target kind) -> score multiplier; lower
# is more attractive, missing pairs default to 1.0. Tanks must not chase
# helicopters: a slow ballistic shell almost never catches an orbiting one, so
# they burn every round for nothing and never end up duelling each other. The
# tiltrotor is a big, less nimble target, so it is easier for them to lead.
PREFERENCE = {
    # An attack helicopter is an anti-armour weapon, so armour has to beat
    # another helicopter by a wide margin. With these nearly equal, the
    # aircraft reached the middle first, latched onto each other and spent the
    # whole battle dogfighting: measured 993 damage in the air against 94 on
    # the ground, which is not what the machine is for.
    ("helicopter", "tank"): 0.42,
    ("helicopter", "rocket"): 0.5,   # launcher teams are the other real threat
    ("helicopter", "sam"): 0.55,
    ("helicopter", "rifleman"): 0.85,
    ("helicopter", "helicopter"): 1.5,
    ("helicopter", "osprey"): 1.3,
    ("tank", "tank"): 0.6,
    ("tank", "helicopter"): 1.9,
    ("tank", "osprey"): 1.35,
    ("tank", "rocket"): 0.55,
    ("tank", "rifleman"): 0.9,
    ("osprey", "helicopter"): 1.0,
    ("osprey", "tank"): 0.9,
    ("osprey", "osprey"): 1.0,
    ("osprey", "rocket"): 0.8,
    ("osprey", "rifleman"): 1.1,
    ("rifleman", "rifleman"): 0.7,
    ("rifleman", "rocket"): 0.65,
    ("rifleman", "tank"): 2.2,       # a rifle barely scratches armour
    ("rifleman", "helicopter"): 1.6,
    ("rifleman", "osprey"): 1.5,
    ("rocket", "tank"): 0.35,        # this is the whole point of the launcher
    ("rocket", "osprey"): 0.9,
    ("rocket", "helicopter"): 1.4,
    ("rocket", "rifleman"): 1.8,
    ("rocket", "rocket"): 1.5,
    ("rocket", "jet"): 2.2,          # far too fast to hit with a launcher
    ("rifleman", "jet"): 2.2,
    ("tank", "jet"): 2.4,
    ("helicopter", "jet"): 2.0,   # it cannot catch one anyway
    ("osprey", "jet"): 1.3,
    ("jet", "tank"): 0.7,            # a strike fighter goes for the armour
    ("jet", "rocket"): 0.8,
    ("jet", "jet"): 0.85,
    ("jet", "helicopter"): 0.95,
    ("jet", "osprey"): 0.9,
    ("jet", "rifleman"): 1.2,
    ("jet", "sam"): 0.45,            # kill the thing that can kill you
    # The SAM exists to hunt aircraft, and only bothers with the ground when
    # there is nothing left in the air.
    ("sam", "jet"): 0.3,
    ("sam", "helicopter"): 0.45,
    ("sam", "osprey"): 0.4,
    ("sam", "tank"): 3.0,
    ("sam", "rifleman"): 3.2,
    ("sam", "rocket"): 3.0,
    ("sam", "sam"): 3.0,
    ("tank", "sam"): 0.7,
    ("rocket", "sam"): 0.6,
    ("rifleman", "sam"): 1.0,
    # The destroyer is a broad-area air/land missile platform. It favours the
    # aircraft that can threaten its fleet, then armoured shore targets.
    ("destroyer", "jet"): 0.35,
    ("destroyer", "helicopter"): 0.45,
    ("destroyer", "osprey"): 0.42,
    ("destroyer", "sam"): 0.65,
    ("destroyer", "tank"): 0.7,
    ("destroyer", "rocket"): 0.9,
    ("destroyer", "rifleman"): 1.0,
    # Deliberately unattractive: a destroyer is a dedicated SAM platform, and
    # jets that preferred it flew straight into the fleet's air defence and
    # were shot to pieces. Their war is over the land.
    ("jet", "destroyer"): 2.4,
    ("jet", "submarine"): 2.6,
    ("helicopter", "destroyer"): 2.2,
    ("helicopter", "submarine"): 2.4,
    ("rocket", "destroyer"): 1.2,
}

# Damage multiplier by (shooter kind, target kind); missing pairs are 1.0.
# Without this a rifle squad shreds tanks purely by volume of fire, and the
# whole point of carrying a launcher disappears.
DAMAGE_VS = {
    ("rifleman", "tank"): 0.10,
    ("rifleman", "helicopter"): 0.45,
    ("rifleman", "osprey"): 0.40,
    ("rocket", "rifleman"): 0.45,
    ("rocket", "rocket"): 0.45,
    # A 120 mm round that connects with a man on foot kills him, full stop.
    # Both of these are one-shot kills; the launcher teams survive by not being
    # hit, not by soaking a tank shell.
    ("tank", "rifleman"): 1.4,
    ("tank", "rocket"): 1.8,
    ("helicopter", "rifleman"): 1.4,
    ("osprey", "rifleman"): 1.4,
    ("jet", "rifleman"): 1.5,
    ("jet", "tank"): 0.75,
    # Nothing on the ground leads a jet well; only other aircraft really can.
    ("tank", "jet"): 0.5,
    ("rifleman", "jet"): 0.3,
    ("rocket", "jet"): 0.5,
    # Token self-defence only. Not zero on purpose: an anti-air unit that
    # literally cannot hurt infantry can never finish a battle, and two SAMs
    # left facing each other would stall forever.
    ("sam", "tank"): 0.14,
    ("sam", "rifleman"): 0.30,
    ("sam", "rocket"): 0.30,
    ("sam", "sam"): 0.20,
    ("rifleman", "destroyer"): 0.08,
    ("destroyer", "rifleman"): 1.5,
}


def ballistic_pitch(distance: float, rise: float, speed: float) -> float | None:
    """Launch angle for the flat (low) arc, or None if the shot cannot reach."""
    if distance < 1e-3:
        return math.pi / 2 if rise > 0 else 0.0
    v2 = speed * speed
    disc = v2 * v2 - GRAVITY * (GRAVITY * distance * distance + 2.0 * rise * v2)
    if disc < 0.0:
        return None
    return math.atan2(v2 - math.sqrt(disc), GRAVITY * distance)


class Battle:
    def __init__(
        self,
        render: NodePath,
        world,
        terrain,
        assets,
        n_heli: int = 3,
        n_tanks: int = 4,
        n_osprey: int = 1,
        n_jets: int = 4,
        n_sam: int = 2,
        n_rifles: int = 6,
        n_rockets: int = 3,
        n_destroyers: int = 1,
        n_submarines: int = 1,
        seed: int = 0,
        deploy_radius: float = 240.0,
        origin: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        self.render = render
        self.world = world
        self.terrain = terrain
        self.assets = assets
        self.rng = random.Random(seed)
        self.root = render.attach_new_node("battle")
        self.effects = Effects(render, world, terrain)

        self.units: list[Unit] = []
        self.wrecks: list[tuple[Unit, float]] = []
        self.winner: int | None = None
        self.elapsed = 0.0
        self.kills = {0: 0, 1: 0}
        self.focus_spread = 0.0
        # Which side's sea both fleets share this battle.
        self.naval_lane = 1.0 if random.Random(seed).random() < 0.5 else -1.0
        self.stats = BattleStats(seed)
        self.deploy_radius = deploy_radius
        self.origin = origin

        self._spawn(
            n_heli, n_tanks, n_osprey, n_jets, n_sam, n_rifles, n_rockets,
            n_destroyers, n_submarines,
        )

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _spawn(
        self,
        n_heli: int,
        n_tanks: int,
        n_osprey: int = 0,
        n_jets: int = 0,
        n_sam: int = 0,
        n_rifles: int = 0,
        n_rockets: int = 0,
        n_destroyers: int = 0,
        n_submarines: int = 0,
    ) -> None:
        # The world has no edges any more, so deployment is a radius around an
        # origin rather than a fraction of the map.
        half = self.deploy_radius
        plain_half = self.deploy_radius * 0.45
        ox, oy = self.origin
        for team, sign in ((0, -1.0), (1, 1.0)):
            heading = math.pi / 2 if sign < 0 else -math.pi / 2
            color = TEAM_COLORS[team]

            for i in range(n_heli):
                x = ox + sign * self.rng.uniform(half * 0.55, half * 0.85)
                y = oy + self.rng.uniform(-plain_half, plain_half)
                cruise = 38.0 + i * 8.0
                z = max(self.terrain.height_at(x, y), self.terrain.water_level) + cruise
                unit = self._make_unit("helicopter", team, color, Vec3(x, y, z), heading)
                unit.cruise_alt = cruise
                unit.orbit_dir = 1.0 if i % 2 == 0 else -1.0

            for i in range(n_osprey):
                x = ox + sign * self.rng.uniform(half * 0.85, half)
                y = oy + self.rng.uniform(-plain_half, plain_half)
                cruise = 34.0 + i * 6.0
                z = max(self.terrain.height_at(x, y), self.terrain.water_level) + cruise
                unit = self._make_unit("osprey", team, color, Vec3(x, y, z), heading)
                unit.cruise_alt = cruise
                unit.orbit_dir = 1.0 if i % 2 == 0 else -1.0
                unit.tilt = 1.0  # start in airplane mode, inbound

            for i in range(n_jets):
                # In from the north and the south, over the land corridor. They
                # used to launch on their team's side in X, which is where the
                # sea lane is: every jet started inside the enemy destroyer's
                # missile envelope and simply flew into the fleet's air defence.
                x = ox + self.rng.uniform(-plain_half, plain_half)
                y = oy + sign * self.rng.uniform(half * 1.6, half * 2.2)
                cruise = 135.0 + i * 16.0
                z = max(self.terrain.height_at(x, y), self.terrain.water_level) + cruise
                jet = self._make_unit("jet", team, color, Vec3(x, y, z), heading)
                jet.cruise_alt = cruise
                # Already at speed: a jet spawning from rest just falls.
                jet.node.set_linear_velocity(jet.forward * SPECS["jet"].cruise_speed)

            for i in range(n_destroyers):
                x, y = self._side_sea_spot(sign, i)
                # Match Unit.update_naval's fixed waterline from frame one.
                z = self.terrain.water_level + 0.78
                ship = self._make_unit("destroyer", team, color, Vec3(x, y, z), heading)
                ship.orbit_dir = 1.0 if i % 2 == 0 else -1.0

            for i in range(n_submarines):
                x, y = self._side_sea_spot(sign, i + 3)
                z = self.terrain.water_level + 0.30
                boat = self._make_unit("submarine", team, color, Vec3(x, y, z), heading)
                boat.orbit_dir = 1.0 if i % 2 == 0 else -1.0

            for i in range(n_tanks):
                x, y = self._dry_spot(
                    ox + sign * self.rng.uniform(half * 0.8, half),
                    oy + self.rng.uniform(-plain_half, plain_half),
                )
                z = self.terrain.height_at(x, y) + 3.0
                tank = self._make_unit("tank", team, color, Vec3(x, y, z), heading)
                # Alternate which way each tank sidesteps once in range.
                tank.orbit_dir = 1.0 if i % 2 == 0 else -1.0

            # Air defence sits behind the armour, covering it.
            for i in range(n_sam):
                x, y = self._dry_spot(
                    ox + sign * self.rng.uniform(half * 1.0, half * 1.2),
                    oy + self.rng.uniform(-plain_half, plain_half),
                )
                z = self.terrain.height_at(x, y) + 3.0
                battery = self._make_unit("sam", team, color, Vec3(x, y, z), heading)
                battery.orbit_dir = 1.0 if i % 2 == 0 else -1.0

            # Infantry deploys in a loose squad line, a little behind the armour.
            for kind, count in (("rifleman", n_rifles), ("rocket", n_rockets)):
                for i in range(count):
                    x, y = self._dry_spot(
                        ox + sign * self.rng.uniform(half * 0.86, half * 1.04),
                        oy + self.rng.uniform(-plain_half, plain_half),
                    )
                    z = self.terrain.height_at(x, y) + 2.0
                    soldier = self._make_unit(kind, team, color, Vec3(x, y, z), heading)
                    soldier.orbit_dir = 1.0 if i % 2 == 0 else -1.0

    def _route_is_clear(self, x: float, y: float) -> bool:
        """Can a ground unit actually drive from here to the battle?

        Walks the straight line to the origin and rejects the spot if any step
        is under water or too steep. Without this a unit can deploy in a hollow
        or behind a ridge, spend the battle grinding along a gully and never
        arrive — which is exactly what it looked like.
        """
        ox, oy = self.origin
        steps = 9
        water = self.terrain.water_level
        previous = self.terrain.height_at(x, y)
        for i in range(1, steps + 1):
            t = i / steps
            px = x + (ox - x) * t
            py = y + (oy - y) * t
            height = self.terrain.height_at(px, py)
            span = math.hypot(ox - x, oy - y) / steps
            if height < water + 1.0:
                return False
            if span > 1e-6 and (height - previous) / span > 0.75:
                return False
            previous = height
        return True

    def _dry_spot(self, x: float, y: float, radius: float = 70.0) -> tuple[float, float]:
        """Pick a ground spawn that is dry *and* has a way out to the fight."""
        margin = self.terrain.water_level + 2.5
        if self.terrain.height_at(x, y) > margin and self._route_is_clear(x, y):
            return x, y

        fallback = None
        for attempt in range(60):
            # Widen the search as attempts fail: a bad neighbourhood needs a
            # bigger step to escape, not more samples of the same hollow.
            reach = radius * (1.0 + attempt / 15.0)
            nx = x + self.rng.uniform(-reach, reach)
            ny = y + self.rng.uniform(-reach, reach)
            if self.terrain.height_at(nx, ny) <= margin:
                continue
            if fallback is None:
                fallback = (nx, ny)
            if self._route_is_clear(nx, ny):
                return nx, ny
        return fallback if fallback is not None else (x, y)

    def _side_sea_spot(self, sign: float, index: int) -> tuple[float, float]:
        """Deploy a hull in the shared sea lane, on its team's end of it.

        Both fleets use the *same* stretch of water, separated along it rather
        than placed in the seas on opposite sides of the map. Split between two
        seas with land in between they could never reach each other with
        anything but the strategic salvo, and a naval-only endgame simply
        stalled — which is exactly what happened.
        """
        ox, oy = self.origin
        water = self.terrain.water_level
        shore = getattr(self.terrain, "sea_end", self.deploy_radius * 2.0)
        # One lane, chosen per battle, so the fleets always share a sea.
        lane = self.naval_lane

        for attempt in range(120):
            nx = ox + lane * (shore + 60.0 + self.rng.uniform(0.0, 110.0))
            # Red steams up the lane, blue steams down it; they meet in between.
            ny = oy + sign * (300.0 + index * 55.0 + self.rng.uniform(0.0, 90.0))
            if self.terrain.height_at(nx, ny) < water - 1.0:
                return nx, ny
        raise RuntimeError("No se encontró agua para desplegar la unidad naval.")

    def _make_unit(self, kind: str, team: int, color, position: Vec3, heading: float) -> Unit:
        spec = SPECS[kind]
        model = self.assets.get(kind, color)
        unit = Unit(
            kind=kind,
            team=team,
            spec=spec,
            model=model,
            parent=self.root,
            world=self.world,
            position=position,
            heading=heading,
        )
        unit.main_rotor = model.find(f"**/{self.assets.node_name(kind, 'main_rotor', 'MainRotor')}")
        unit.tail_rotor = model.find(f"**/{self.assets.node_name(kind, 'tail_rotor', 'TailRotor')}")
        unit.turret = model.find(f"**/{self.assets.node_name(kind, 'turret', 'Turret')}")
        unit.nacelles = [
            model.find(f"**/{self.assets.node_name(kind, 'nacelle_left', 'NacelleLeft')}"),
            model.find(f"**/{self.assets.node_name(kind, 'nacelle_right', 'NacelleRight')}"),
        ]
        unit.nacelles = [n for n in unit.nacelles if not n.is_empty()]
        unit.proprotors = [n.find("**/Proprotor") for n in unit.nacelles]
        # Optional procedural infantry skeleton. Real imported models are
        # allowed to omit it; empty NodePaths simply disable this animation.
        unit.left_hip = model.find("**/LeftHip")
        unit.right_hip = model.find("**/RightHip")
        unit.left_knee = model.find("**/LeftKnee")
        unit.right_knee = model.find("**/RightKnee")
        unit.left_boot = model.find("**/LeftBoot")
        unit.right_boot = model.find("**/RightBoot")
        unit.left_shoulder = model.find("**/LeftShoulder")
        unit.right_shoulder = model.find("**/RightShoulder")
        unit.upper_body = model.find("**/UpperBody")
        # Cap the stagger: with a 5 s reload the launcher teams were spending
        # the whole opening exchange idle and dying before their first shot.
        unit.cooldown = self.rng.uniform(0.0, min(spec.fire_period, 1.2))

        bar_root, bar_fill = make_health_bar()
        bar_root.reparent_to(unit.np)
        bar_root.set_z(spec.half_extents.z + (1.4 if kind in INFANTRY else 3.4))
        if kind in INFANTRY:
            bar_root.set_scale(0.55)
        bar_root.hide()
        unit.health_bar = bar_root
        unit.health_fill = bar_fill

        self.units.append(unit)
        self.stats.deploy(kind, team)
        return unit

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def alive_units(self) -> list[Unit]:
        return [u for u in self.units if u.alive]

    def roster(self, team: int) -> dict[str, int]:
        """Surviving units of each kind, for the HUD legend."""
        counts: dict[str, int] = {}
        for unit in self.units:
            if unit.alive and unit.team == team:
                counts[unit.kind] = counts.get(unit.kind, 0) + 1
        return counts

    def alive_count(self, team: int) -> int:
        return sum(1 for u in self.units if u.alive and u.team == team)

    def centroid(self) -> Point3:
        alive = self.alive_units()
        if not alive:
            return Point3(self.origin[0], self.origin[1], self.terrain.relief)
        total = Vec3(0, 0, 0)
        for unit in alive:
            total += Vec3(unit.position)
        return Point3(total / len(alive))

    def focus_point(self) -> Point3:
        """Where the camera should look: the closest pair of enemies.

        The plain centroid is useless for framing — with the two sides still
        apart it lands on empty ground halfway between them and the fighting
        happens off screen.
        """
        reds = [u for u in self.units if u.alive and u.team == 0]
        blues = [u for u in self.units if u.alive and u.team == 1]
        if not reds or not blues:
            return self._ground_biased(self.centroid())

        best_pair, best_distance = None, float("inf")
        for red in reds:
            for blue in blues:
                distance = (red.position - blue.position).length_squared()
                if distance < best_distance:
                    best_distance, best_pair = distance, (red, blue)

        first, second = best_pair
        flashpoint = (Vec3(first.position) + Vec3(second.position)) * 0.5

        # Then widen to everyone fighting around that flashpoint. Framing the
        # closest pair alone locks the camera onto whichever two aircraft meet
        # first and leaves the whole ground battle off screen.
        engaged = [
            u
            for u in self.units
            if u.alive and (Vec3(u.position) - flashpoint).length_squared() < ENGAGEMENT_RADIUS**2
        ]
        if len(engaged) > 2:
            total = Vec3(0, 0, 0)
            for unit in engaged:
                total += Vec3(unit.position)
            centre = total / len(engaged)
            self.focus_spread = max(
                (Vec3(u.position) - centre).length() for u in engaged
            ) * 1.35
            return self._ground_biased(Point3(centre))

        self.focus_spread = math.sqrt(best_distance)
        return self._ground_biased(Point3(flashpoint))

    def _ground_biased(self, point: Point3) -> Point3:
        """Pull the look-at point down towards the terrain.

        Aiming straight at a pair of helicopters at cruise height fills the
        frame with empty sky and pushes the ground fight out of shot.
        """
        ceiling = self.terrain.height_at(point.x, point.y) + 18.0
        return Point3(point.x, point.y, min(point.z, ceiling))

    def _can_detect(self, observer: Unit, target: Unit) -> bool:
        """Whether `observer` can even see `target`.

        Only aircraft and other submarines can find a submerged boat; nothing
        else in this simulation carries sonar, so for a tank or a rifleman it
        may as well not be there.
        """
        if target.kind in SUBSURFACE:
            return observer.kind in DETECTS_SUBSURFACE
        return True

    def _pick_target(self, unit: Unit) -> Unit | None:
        best, best_score = None, float("inf")
        for other in self.units:
            if not other.alive or other.team == unit.team:
                continue
            if not self._can_detect(unit, other):
                continue
            distance = (other.position - unit.position).length()
            # Weight by what this shooter can realistically kill, and jitter so
            # a whole squad does not converge on the exact same victim.
            score = (
                distance
                * PREFERENCE.get((unit.kind, other.kind), 1.0)
                * self.rng.uniform(0.75, 1.3)
            )
            if score < best_score:
                best_score, best = score, other
        return best

    def _acquire_target(self, unit: Unit) -> Unit | None:
        """Stick with the current target until it dies or breaks away.

        Without this every unit re-picks the nearest enemy each frame, fire
        concentrates on one victim and the battle snowballs into a shutout.
        """
        current = unit.target
        if current is not None and current.alive and self._can_detect(unit, current):
            distance = (current.position - unit.position).length()
            if distance <= unit.spec.attack_range * 1.6:
                return current
        return self._pick_target(unit)

    def _has_line_of_sight(self, unit: Unit, target: Unit) -> bool:
        result = self.world.ray_test_closest(unit.muzzle(), target.position)
        if not result.has_hit():
            return True
        node = result.get_node()
        hit_unit = node.get_python_tag("unit") if node is not None else None
        return hit_unit is target

    # ------------------------------------------------------------------
    # Simulation step
    # ------------------------------------------------------------------
    def step(self, dt: float) -> None:
        self.elapsed += dt
        for unit in self.units:
            if not unit.alive:
                continue
            target = self._acquire_target(unit)
            unit.target = target

            # One raycast per unit per frame, shared by the mover and the gun.
            engaged = (
                target is not None
                and (target.position - unit.position).length() <= unit.spec.attack_range
                and unit.can_bear(target)
                and self._has_line_of_sight(unit, target)
            )

            if unit.kind == "helicopter":
                unit.update_helicopter(dt, self.terrain, target, engaged)
            elif unit.kind == "osprey":
                unit.update_osprey(dt, self.terrain, target, engaged)
            elif unit.kind == "jet":
                unit.update_jet(dt, self.terrain, target, engaged)
            elif unit.kind in NAVAL:
                unit.update_naval(dt, self.terrain, target, engaged)
            elif unit.kind in NAVAL:
                unit.update_naval(dt, self.terrain, target, engaged)
            else:
                unit.update_ground(dt, self.terrain, target, engaged)

            self._animate(unit, dt)

            if unit.kind == "submarine":
                unit.strategic_cooldown -= dt
                if unit.strategic_cooldown <= 0.0 and not unit.pending_salvo:
                    # Order the boat up first; the salvo waits until the tubes
                    # are actually clear of the water.
                    unit.pending_salvo = True
                    unit.surface_time = SURFACING_SECONDS
                if unit.pending_salvo and unit.is_surfaced(self.terrain.water_level):
                    if self._fire_strategic_salvo(unit):
                        unit.strategic_cooldown = STRATEGIC_SALVO_PERIOD
                    else:
                        unit.strategic_cooldown = 20.0
                    unit.pending_salvo = False

            unit.cooldown -= dt
            if engaged and unit.cooldown <= 0.0:
                self._fire(unit, target)

            # Wounded units show it, but a bleeding soldier is not a smoking
            # engine: infantry drips instead of trailing a smoke column.
            if unit.hp_frac < 0.4:
                if unit.kind in INFANTRY:
                    if self.rng.random() < dt * 3.0:
                        self.effects.blood(
                            unit.position + Vec3(0, 0, 0.3), scale=0.5, count=2
                        )
                elif self.rng.random() < dt * 9.0:
                    self.effects.smoke_puff(unit.position, scale=1.1)

        self.effects.update(dt, self._apply_damage)
        self._update_wrecks(dt)
        self._check_winner()

    def _fire_strategic_salvo(self, unit: Unit) -> bool:
        """Cruise-missile salvo at land targets anywhere, ignoring range and LOS.

        This lives on the submarine rather than the destroyer: a weapon with no
        practical limit on reach is what a missile boat exists for, and a
        surface combatant having one made much less sense.
        """
        enemies = [o for o in self.units if o.alive and o.team != unit.team]
        targets = [o for o in enemies if o.kind in GROUND]
        if not targets:
            # Nothing ashore left. Fall back to whatever is out there, or two
            # navies in separate seas simply stare at each other forever —
            # neither can reach the other with anything but this.
            targets = enemies
        if not targets:
            return False

        # Prefer valuable armour/air-defence while still including distance so
        # a salvo does not always converge on the same class of target.
        targets.sort(
            key=lambda target: (
                (target.position - unit.position).length()
                * PREFERENCE.get((unit.kind, target.kind), 1.0),
                target.id,
            )
        )
        selected = targets[:STRATEGIC_SALVO_SIZE]
        offsets = (-0.9, 0.0, 0.9)
        for index, target in enumerate(selected):
            self.effects.launch_missile(
                unit, target, STRATEGIC_STRIKE, rack_offset=offsets[index]
            )
        self.stats.salvo(unit.kind, unit.team, len(selected))
        for _ in selected:
            self.stats.missile(unit.kind, unit.team, "misil de crucero")
            self.stats.shot(unit.kind, unit.team)
        return True

    def _animate(self, unit: Unit, dt: float) -> None:
        """Cosmetic-only motion: never touches the rigid body."""
        fraction = unit.hp_frac
        if fraction < 0.999:
            unit.health_bar.show()
            unit.health_fill.set_sx(max(0.02, fraction))
            if fraction > 0.6:
                unit.health_fill.set_color(0.24, 0.85, 0.35, 1.0)
            elif fraction > 0.3:
                unit.health_fill.set_color(0.95, 0.78, 0.18, 1.0)
            else:
                unit.health_fill.set_color(0.92, 0.26, 0.20, 1.0)

        if unit.kind == "jet":
            # Aircraft bank hard into a turn; without it a jet looks like a
            # paper dart sliding sideways round a corner.
            yaw_rate = unit.node.get_angular_velocity().z
            target_roll = max(-72.0, min(72.0, -yaw_rate * 48.0))
            roll = unit.model_np.get_r() + (target_roll - unit.model_np.get_r()) * min(1.0, 3.0 * dt)
            climb = max(-18.0, min(18.0, unit.velocity.z * 0.9))
            pitch = unit.model_np.get_p() + (climb - unit.model_np.get_p()) * min(1.0, 3.0 * dt)
            unit.model_np.set_hpr(0, pitch, roll)
            return

        if unit.kind == "osprey":
            # Swing the nacelles between hover (upright) and airplane mode.
            for nacelle in unit.nacelles:
                nacelle.set_p(-88.0 * unit.tilt)
            for proprotor in unit.proprotors:
                if not proprotor.is_empty():
                    proprotor.set_h(proprotor.get_h() + 1100.0 * dt)
            right = unit.np.get_quat().get_right()
            target_roll = max(-16.0, min(16.0, -unit.velocity.dot(right) * 0.8))
            roll = unit.model_np.get_r() + (target_roll - unit.model_np.get_r()) * min(1.0, 3.0 * dt)
            unit.model_np.set_hpr(0, 0, roll)
            return

        if unit.kind == "helicopter":
            if not unit.main_rotor.is_empty():
                unit.main_rotor.set_h(unit.main_rotor.get_h() + 1500.0 * dt)
            if not unit.tail_rotor.is_empty():
                unit.tail_rotor.set_p(unit.tail_rotor.get_p() + 2000.0 * dt)
            # Bank into the turn, proportional to sideways velocity.
            right = unit.np.get_quat().get_right()
            lateral = unit.velocity.dot(right)
            forward_speed = unit.velocity.dot(unit.np.get_quat().get_forward())
            target_roll = max(-32.0, min(32.0, -lateral * 1.5))
            target_pitch = max(-18.0, min(18.0, -forward_speed * 0.55))
            roll = unit.model_np.get_r() + (target_roll - unit.model_np.get_r()) * min(1.0, 4.0 * dt)
            pitch = unit.model_np.get_p() + (target_pitch - unit.model_np.get_p()) * min(1.0, 4.0 * dt)
            unit.model_np.set_hpr(0, pitch, roll)
            return

        if unit.kind in NAVAL:
            # A hull rides level on the water; terrain-normal tilting is only
            # for wheeled/tracked units and makes a ship pitch into the seabed.
            unit.model_np.set_p(0)
            unit.model_np.set_r(0)
            if not unit.turret.is_empty() and unit.target is not None:
                unit.turret.look_at(self.render, unit.target.position)
                unit.turret.set_p(0)
                unit.turret.set_r(0)
            return

        # Ground units: lean onto the slope. The rigid body stays yaw-locked,
        # so this is purely visual.
        normal = self.terrain.normal_at(unit.position.x, unit.position.y)
        quat = unit.np.get_quat()
        target_pitch = math.degrees(math.asin(max(-1.0, min(1.0, normal.dot(quat.get_forward())))))
        target_roll = -math.degrees(math.asin(max(-1.0, min(1.0, normal.dot(quat.get_right())))))
        blend = min(1.0, 6.0 * dt)
        unit.model_np.set_p(unit.model_np.get_p() + (target_pitch - unit.model_np.get_p()) * blend)
        unit.model_np.set_r(unit.model_np.get_r() + (target_roll - unit.model_np.get_r()) * blend)

        if unit.kind in INFANTRY:
            self._animate_infantry(unit, dt)
            return

        if not unit.turret.is_empty() and unit.target is not None:
            # Turret tracks the target independently of the hull.
            unit.turret.look_at(self.render, unit.target.position)
            unit.turret.set_p(0)
            unit.turret.set_r(0)

    @staticmethod
    def _animate_infantry(unit: Unit, dt: float) -> None:
        """Velocity-driven walk/run cycle for the procedural soldier skeleton."""
        if unit.left_hip.is_empty() or unit.right_hip.is_empty():
            return

        velocity = unit.velocity
        speed = math.hypot(velocity.x, velocity.y)
        moving = max(0.0, min(1.0, (speed - 0.18) / 1.4))
        blend_rate = min(1.0, dt * (9.0 if moving > unit.gait_blend else 12.0))
        unit.gait_blend += (moving - unit.gait_blend) * blend_rate

        speed_ratio = max(0.0, min(1.25, speed / unit.spec.cruise_speed))
        # Roughly 1.7 steps/m while walking, opening into a longer running
        # stride at speed. Phase is in radians and therefore frame-rate safe.
        cadence = 5.2 + speed_ratio * 6.4
        unit.gait_phase = (unit.gait_phase + cadence * dt * unit.gait_blend) % math.tau
        cycle = math.sin(unit.gait_phase)
        opposite = -cycle
        contact = math.cos(unit.gait_phase)
        stride = (21.0 + 25.0 * speed_ratio) * unit.gait_blend

        unit.left_hip.set_p(cycle * stride)
        unit.right_hip.set_p(opposite * stride)

        # The trailing leg folds at the knee while the planted leg straightens.
        knee_bend = (24.0 + 30.0 * speed_ratio) * unit.gait_blend
        if not unit.left_knee.is_empty():
            unit.left_knee.set_p(-max(0.0, -cycle) * knee_bend)
        if not unit.right_knee.is_empty():
            unit.right_knee.set_p(-max(0.0, cycle) * knee_bend)

        # Keep boot soles closer to level; this makes foot plants read clearly
        # even though the figure is deliberately low-poly.
        if not unit.left_boot.is_empty():
            unit.left_boot.set_p(-cycle * stride * 0.42)
        if not unit.right_boot.is_empty():
            unit.right_boot.set_p(-opposite * stride * 0.42)

        # The arms retain the exact two-handed firing solution built into the
        # model. Tactical movement is carried by the legs and upper body; a
        # free arm swing would disconnect the wrists from the weapon grips.
        if not unit.upper_body.is_empty():
            bob = abs(contact) * (0.018 + 0.028 * speed_ratio) * unit.gait_blend
            unit.upper_body.set_z(bob)
            unit.upper_body.set_p(-speed_ratio * 7.0 * unit.gait_blend)
            unit.upper_body.set_r(-cycle * 2.8 * unit.gait_blend)

    def _fire(self, unit: Unit, target: Unit) -> None:
        unit.cooldown = unit.spec.fire_period * self.rng.uniform(0.85, 1.15)
        self.stats.shot(unit.kind, unit.team)
        # Armour and launcher teams shoot from a halt; the halt outlives the
        # shot slightly so it reads as "stop, fire, move on" rather than a stutter.
        unit.halt = unit.spec.fire_halt
        muzzle = unit.muzzle()

        if unit.kind == "destroyer":
            # A Burke-style destroyer does not waste a cruise missile on a
            # target it can reach with its Mk 45 or its close-in guns. This is
            # automatic weapon selection; the battle remains autonomous.
            distance = (target.position - unit.position).length()
            if distance <= 82.0:
                unit.cooldown = 0.16 * self.rng.uniform(0.85, 1.15)
                muzzle = unit.naval_ciws_muzzle()
                spread = 2.0 + distance * 0.025
                aim = target.position + Vec3(
                    self.rng.uniform(-spread, spread),
                    self.rng.uniform(-spread, spread),
                    self.rng.uniform(-spread, spread),
                )
                self.effects.tracer(muzzle, aim, TEAM_COLORS[unit.team])
                self.effects.muzzle_flash(muzzle, scale=0.34)
                if self.rng.random() < 0.72:
                    self._apply_damage(unit, target, 11.0 * self.rng.uniform(0.75, 1.2))
                return
            if distance <= 310.0:
                unit.cooldown = 2.1 * self.rng.uniform(0.85, 1.15)
                muzzle = unit.naval_gun_muzzle()
                delta = target.position - muzzle
                flight = delta.length() / 235.0
                predicted = target.position + target.velocity * flight
                delta = predicted - muzzle
                flat = Vec3(delta.x, delta.y, 0)
                horizontal = flat.length()
                pitch = ballistic_pitch(horizontal, delta.z, 235.0)
                if pitch is None:
                    return
                if horizontal > 1e-6:
                    flat.normalize()
                direction = flat * math.cos(pitch) + Vec3(0, 0, math.sin(pitch))
                direction.normalize()
                self.effects.muzzle_flash(muzzle, scale=0.9)
                self.effects.shell(unit, muzzle, direction, 235.0, 58.0)
                return
            self.effects.launch_missile(unit, target, NAVAL_STRIKE)
            self.stats.missile(unit.kind, unit.team, "misil naval")
            return

        if unit.kind == "submarine":
            # Torpedoes only. Anything ashore is the strategic salvo's problem.
            if target.kind in NAVAL:
                self.effects.launch_missile(unit, target, TORPEDO)
                self.stats.missile(unit.kind, unit.team, "torpedo")
            return

        if unit.kind in ("jet", "sam"):
            # Guided rounds: the Missile object flies itself with proportional
            # navigation, so nothing here has to solve an intercept.
            spec = (
                SURFACE_TO_AIR if unit.kind == "sam"
                else NAVAL_STRIKE if unit.kind == "destroyer"
                else AIR_TO_GROUND
            )
            self.effects.launch_missile(unit, target, spec)
            self.stats.missile(
                unit.kind, unit.team,
                "misil tierra-aire" if unit.kind == "sam" else "misil aire-tierra",
            )
            return

        if unit.kind in ("helicopter", "osprey", "rifleman"):
            distance = (target.position - muzzle).length()
            close = 1.0 - min(1.0, distance / unit.spec.attack_range)
            accuracy = 0.35 + 0.5 * close  # 85% point blank, 35% at max range
            spread = 1.2 + 3.0 * (1.0 - close)
            aim = target.position + Vec3(
                self.rng.uniform(-spread, spread),
                self.rng.uniform(-spread, spread),
                self.rng.uniform(-spread, spread),
            )
            flash = 0.35 if unit.kind == "rifleman" else 0.7
            self.effects.tracer(muzzle, aim, TEAM_COLORS[unit.team])
            self.effects.muzzle_flash(muzzle, scale=flash)
            if self.rng.random() < accuracy:
                self._apply_damage(unit, target, unit.spec.damage * self.rng.uniform(0.8, 1.2))
            return

        # Tank shell and RPG round are both real projectiles: lead the target,
        # then solve the launch arc. The rocket is much slower, so the lead
        # matters far more for it than for the tank.
        speed = ROCKET_SPEED if unit.kind == "rocket" else SHELL_SPEED
        delta = target.position - muzzle
        flight = delta.length() / speed
        predicted = target.position + target.velocity * flight
        delta = predicted - muzzle
        flat = Vec3(delta.x, delta.y, 0)
        distance = flat.length()
        pitch = ballistic_pitch(distance, delta.z, speed)
        if pitch is None:
            return
        if distance > 1e-6:
            flat.normalize()
        direction = flat * math.cos(pitch) + Vec3(0, 0, math.sin(pitch))
        direction.normalize()
        self.effects.shell(
            unit,
            muzzle,
            direction,
            speed,
            unit.spec.damage,
            trail=unit.kind == "rocket",
        )

    def _apply_damage(self, shooter: Unit, target: Unit, amount: float) -> None:
        if not target.alive:
            return
        amount *= DAMAGE_VS.get((shooter.kind, target.kind), 1.0)
        self.stats.hit(shooter.kind, target.kind, shooter.team, amount)

        # Infantry bleeds; vehicles burn. A hit that does not kill still marks
        # a soldier, which is most of the visual feedback a 2 m unit can give.
        if target.kind in INFANTRY and self.rng.random() < 0.5:
            self.effects.blood(target.position + Vec3(0, 0, 0.4), scale=0.8, count=4)

        if target.take_damage(amount):
            target.health_bar.hide()
            self.kills[shooter.team] += 1
            self.stats.kill(shooter.kind, target.kind, shooter.team)
            if target.kind in INFANTRY:
                self.effects.blood(target.position + Vec3(0, 0, 0.5), scale=1.5, count=14)
            else:
                scale = 2.4 if target.kind == "helicopter" else 2.0
                self.effects.explosion(target.position, scale=scale, debris_count=8)
            self.wrecks.append((target, WRECK_LIFETIME))

    def _update_wrecks(self, dt: float) -> None:
        remaining = []
        for unit, ttl in self.wrecks:
            ttl -= dt
            if ttl <= 0:
                if unit.kind not in INFANTRY:
                    self.effects.explosion(unit.position, scale=1.6, debris_count=4)
                unit.destroy()
                continue
            # Wrecks burn; bodies just lie there.
            if unit.kind not in INFANTRY and self.rng.random() < dt * 14.0:
                self.effects.smoke_puff(unit.position, scale=1.5)
            remaining.append((unit, ttl))
        self.wrecks = remaining

    def _check_winner(self) -> None:
        if self.winner is not None:
            return
        teams = {u.team for u in self.units if u.alive}
        if len(teams) <= 1:
            self.winner = next(iter(teams)) if teams else -1
            self.stats.finish(self.elapsed, self.winner)

    def status_text(self) -> str:
        if self.winner is None:
            return (
                f"Rojo {self.alive_count(0)}  vs  Azul {self.alive_count(1)}"
                f"      bajas  R:{self.kills[1]}  A:{self.kills[0]}"
            )
        if self.winner == -1:
            return "Empate: aniquilación mutua   ·   [R] nueva batalla"
        return f"¡Gana el equipo {TEAM_NAMES[self.winner]}!   ·   [R] nueva batalla"

    def cleanup(self) -> None:
        self.effects.clear()
        for unit in self.units:
            if not unit.np.is_empty():
                self.world.remove(unit.node)
                unit.np.remove_node()
        self.units.clear()
        self.wrecks.clear()
        self.root.remove_node()
