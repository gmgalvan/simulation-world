"""Weapon tracers, ballistic shells, explosions and physics debris."""

from __future__ import annotations

import math
import random

from panda3d.bullet import BulletRigidBodyNode, BulletSphereShape
from panda3d.core import (
    ColorBlendAttrib,
    LineSegs,
    NodePath,
    Point3,
    TransparencyAttrib,
    Vec3,
    Vec4,
)

from .assets import make_box
from .missiles import TORPEDO, Missile, MissileSpec, intercept_direction


class _Timed:
    __slots__ = ("np", "ttl", "max_ttl")

    def __init__(self, np: NodePath, ttl: float) -> None:
        self.np = np
        self.ttl = ttl
        self.max_ttl = ttl


class Fireball(_Timed):
    """A cluster of boxes that puffs outward and fades, low-poly style."""

    __slots__ = ("pieces", "scale")

    def __init__(self, np: NodePath, ttl: float, pieces, scale: float) -> None:
        super().__init__(np, ttl)
        self.pieces = pieces
        self.scale = scale


class Splatter(_Timed):
    """A burst of gore: pieces fly out on a ballistic arc and fade.

    Simulated by hand rather than through Bullet — a kill can throw a dozen of
    these and they never need to interact with anything.
    """

    __slots__ = ("pieces",)

    def __init__(self, np: NodePath, ttl: float, pieces) -> None:
        super().__init__(np, ttl)
        self.pieces = pieces


class Debris:
    """A real rigid body chunk thrown by an explosion."""

    __slots__ = ("np", "node", "ttl")

    def __init__(self, np: NodePath, node: BulletRigidBodyNode, ttl: float) -> None:
        self.np = np
        self.node = node
        self.ttl = ttl


class Shell:
    """A fired round: Bullet gives it the ballistic arc, a swept ray finds the hit."""

    __slots__ = ("np", "velocity", "prev_pos", "ttl", "shooter", "damage", "trail")

    def __init__(self, np, velocity, shooter, damage: float, ttl: float = 6.0, trail: bool = False) -> None:
        self.np = np
        self.velocity = Vec3(velocity)
        self.prev_pos = Point3(np.get_pos())
        self.ttl = ttl
        self.shooter = shooter
        self.damage = damage
        self.trail = trail


class _HatchSwing:
    """A launch-tube lid thrown open, held, then dropped shut again.

    Kept as its own timed object rather than a tween on the model, because the
    lid belongs to a unit that may be destroyed mid-cycle; when that happens
    the NodePath goes empty and the swing simply stops updating.
    """

    __slots__ = ("np", "elapsed", "open_for", "hold_for", "shut_for", "angle")

    def __init__(self, np: NodePath, angle: float = 104.0) -> None:
        self.np = np
        self.elapsed = 0.0
        self.open_for = 0.55
        self.hold_for = 3.4
        self.shut_for = 1.5
        self.angle = angle

    @property
    def total(self) -> float:
        return self.open_for + self.hold_for + self.shut_for

    def fraction(self) -> float:
        """How far open the lid is, 0 shut to 1 fully back."""
        t = self.elapsed
        if t < self.open_for:
            # Fast at first, easing as the ram runs out of travel.
            x = t / self.open_for
            return 1.0 - (1.0 - x) * (1.0 - x)
        if t < self.open_for + self.hold_for:
            return 1.0
        x = (t - self.open_for - self.hold_for) / self.shut_for
        return max(0.0, 1.0 - x * x)


class Effects:
    def __init__(self, render: NodePath, world, terrain) -> None:
        self.render = render
        self.world = world
        self.terrain = terrain
        self.root = render.attach_new_node("effects")
        self.tracers: list[_Timed] = []
        self.fireballs: list[Fireball] = []
        self.debris: list[Debris] = []
        self.shells: list[Shell] = []
        self.smoke: list[_Timed] = []
        self.splatters: list[Splatter] = []
        self.missiles: list[Missile] = []
        self.hatches: list[_HatchSwing] = []

    # ------------------------------------------------------------------
    # Spawning
    # ------------------------------------------------------------------
    def tracer(self, start: Point3, end: Point3, color) -> None:
        segs = LineSegs()
        segs.set_thickness(2.4)
        segs.set_color(Vec4(*color))
        segs.move_to(start)
        segs.draw_to(end)
        np = self.root.attach_new_node(segs.create())
        np.set_transparency(TransparencyAttrib.M_alpha)
        np.set_attrib(ColorBlendAttrib.make(ColorBlendAttrib.M_add))
        np.set_light_off()
        np.set_bin("fixed", 40)
        np.set_depth_write(False)
        self.tracers.append(_Timed(np, 0.09))

    def muzzle_flash(self, position: Point3, scale: float = 1.0) -> None:
        np = make_box((scale, scale, scale), (1.0, 0.85, 0.4, 1.0))
        np.reparent_to(self.root)
        np.set_pos(position)
        np.set_hpr(random.uniform(0, 360), random.uniform(0, 360), 0)
        np.set_transparency(TransparencyAttrib.M_alpha)
        np.set_attrib(ColorBlendAttrib.make(ColorBlendAttrib.M_add))
        np.set_light_off()
        self.tracers.append(_Timed(np, 0.07))

    def ciws_burst(self, start: Point3, end: Point3, color) -> None:
        """The wall of tracer a close-in gun puts up on one trigger pull.

        A Phalanx fires about seventy rounds a second, so what you see is a
        continuous rope of light, not a bullet. Drawing a single thin tracer
        per burst — as this used to — made the ship's last-ditch defence
        invisible at exactly the moment it matters.
        """
        direction = end - start
        spread = max(0.9, direction.length() * 0.02)
        for i in range(7):
            aim = Point3(
                end.x + random.uniform(-spread, spread),
                end.y + random.uniform(-spread, spread),
                end.z + random.uniform(-spread, spread),
            )
            segs = LineSegs()
            # Thick and hot at the core, thinner strands around it.
            segs.set_thickness(4.6 if i == 0 else 2.0)
            segs.set_color(Vec4(1.0, 0.86, 0.42, 1.0) if i == 0 else Vec4(*color))
            segs.move_to(start)
            segs.draw_to(aim)
            np = self.root.attach_new_node(segs.create())
            np.set_transparency(TransparencyAttrib.M_alpha)
            np.set_attrib(ColorBlendAttrib.make(ColorBlendAttrib.M_add))
            np.set_light_off()
            np.set_bin("fixed", 40)
            np.set_depth_write(False)
            self.tracers.append(_Timed(np, random.uniform(0.10, 0.20)))
        self.muzzle_flash(start, scale=1.15)

    def explosion(self, position: Point3, scale: float = 1.0, debris_count: int = 6) -> None:
        root = self.root.attach_new_node("fireball")
        root.set_pos(position)
        root.set_transparency(TransparencyAttrib.M_alpha)
        root.set_attrib(ColorBlendAttrib.make(ColorBlendAttrib.M_add))
        root.set_light_off()
        root.set_depth_write(False)
        root.set_bin("fixed", 30)

        pieces = []
        # Kept dim and few: additive blending saturates to flat white fast.
        palette = ((0.95, 0.55, 0.12, 1.0), (0.85, 0.32, 0.07, 1.0), (0.98, 0.74, 0.26, 1.0))
        piece_count = max(5, min(14, round(scale * 2.2)))
        for i in range(piece_count):
            size = scale * random.uniform(0.55, 1.15)
            piece = make_box((size, size, size), palette[i % len(palette)])
            piece.reparent_to(root)
            direction = Vec3(
                random.uniform(-1, 1), random.uniform(-1, 1), random.uniform(-0.1, 1.0)
            )
            if direction.length_squared() < 1e-6:
                direction = Vec3(0, 0, 1)
            direction.normalize()
            piece.set_hpr(random.uniform(0, 360), random.uniform(0, 360), 0)
            pieces.append((piece, direction * scale * random.uniform(1.4, 3.2)))

        duration = 0.45 + min(0.45, scale * 0.06)
        self.fireballs.append(Fireball(root, duration, pieces, scale))
        self._spawn_debris(position, scale, debris_count)

    def _spawn_debris(self, position: Point3, scale: float, count: int) -> None:
        for _ in range(count):
            size = random.uniform(0.28, 0.6)
            shade = random.uniform(0.26, 0.42)
            model = make_box((size, size, size), (shade, shade * 0.96, shade * 0.92, 1.0))

            node = BulletRigidBodyNode("debris")
            node.add_shape(BulletSphereShape(size * 0.55))
            node.set_mass(6.0)
            node.set_restitution(0.3)
            node.set_friction(0.8)
            np = self.root.attach_new_node(node)
            np.set_pos(position + Vec3(0, 0, scale * 0.4))
            model.reparent_to(np)
            self.world.attach(node)

            # Set velocity directly: impulses here are easy to get wrong by an
            # order of magnitude and send chunks into orbit.
            node.set_linear_velocity(
                Vec3(
                    random.uniform(-1, 1) * 7.0,
                    random.uniform(-1, 1) * 7.0,
                    random.uniform(4.0, 11.0),
                )
            )
            node.set_angular_velocity(
                Vec3(random.uniform(-7, 7), random.uniform(-7, 7), random.uniform(-7, 7))
            )
            self.debris.append(Debris(np, node, random.uniform(2.5, 4.0)))

    def blood(self, position: Point3, scale: float = 1.0, count: int = 10) -> None:
        """Low-poly gore burst. Alpha blended, never additive — unlike the
        fireballs, blood must not glow."""
        root = self.root.attach_new_node("blood")
        root.set_pos(position)
        root.set_transparency(TransparencyAttrib.M_alpha)
        root.set_light_off()
        root.set_depth_write(False)
        root.set_bin("fixed", 25)

        palette = ((0.52, 0.05, 0.05, 1.0), (0.38, 0.03, 0.04, 1.0), (0.62, 0.10, 0.08, 1.0))
        pieces = []
        for i in range(count):
            size = scale * random.uniform(0.12, 0.30)
            piece = make_box((size, size, size), palette[i % len(palette)])
            piece.reparent_to(root)
            piece.set_hpr(random.uniform(0, 360), random.uniform(0, 360), 0)
            velocity = Vec3(
                random.uniform(-1, 1) * 4.5 * scale,
                random.uniform(-1, 1) * 4.5 * scale,
                random.uniform(1.5, 5.5) * scale,
            )
            pieces.append((piece, velocity))

        self.splatters.append(Splatter(root, random.uniform(0.9, 1.4), pieces))

    def launch_plume(self, position: Point3, scale: float = 1.0) -> None:
        """Efflux boiling off the deck as a cell fires.

        The single most recognisable thing a missile ship does, and the piece
        that was missing: launches were silent puffs of nothing.
        """
        for _ in range(9):
            size = scale * random.uniform(0.9, 2.1)
            shade = random.uniform(0.66, 0.88)
            puff = make_box((size, size, size), (shade, shade, shade * 0.98, 0.62))
            puff.reparent_to(self.root)
            puff.set_pos(
                position.x + random.uniform(-1.4, 1.4) * scale,
                position.y + random.uniform(-1.4, 1.4) * scale,
                position.z + random.uniform(-0.6, 1.2) * scale,
            )
            puff.set_hpr(random.uniform(0, 360), random.uniform(0, 360), 0)
            puff.set_transparency(TransparencyAttrib.M_alpha)
            puff.set_light_off()
            puff.set_depth_write(False)
            self.smoke.append(_Timed(puff, random.uniform(1.4, 2.4)))
        # A brief flash at the cell mouth.
        flash = make_box((scale * 1.5, scale * 1.5, scale * 1.5), (1.0, 0.82, 0.42, 1.0))
        flash.reparent_to(self.root)
        flash.set_pos(position)
        flash.set_transparency(TransparencyAttrib.M_alpha)
        flash.set_attrib(ColorBlendAttrib.make(ColorBlendAttrib.M_add))
        flash.set_light_off()
        self.tracers.append(_Timed(flash, 0.16))

    def open_launch_hatch(self, unit, index: int) -> Point3 | None:
        """Throw open one of a submarine's launch-tube lids.

        Returns the world position of the open tube mouth so the caller can
        put the missile where the hole actually is, or None if this model has
        no hatch by that name.
        """
        side = "S" if index % 2 else "P"
        hatch = unit.model_np.find(f"**/LaunchHatch{index // 2}{side}")
        if hatch.is_empty():
            return None
        hinge = hatch.find("**/Hinge")
        if not hinge.is_empty():
            self.hatches.append(_HatchSwing(hinge))
        return hatch.get_pos(self.render)

    def breach_column(self, position: Point3, scale: float = 1.0) -> None:
        """The wall of white water a missile drags up as it leaves the sea.

        This is the moment that sells a submarine launch: without it a missile
        simply materialised above the waves with nothing to show it had come
        from underneath.
        """
        # The column itself: a stack of foam slabs, widest at the base.
        for i in range(7):
            height = i / 6.0
            width = scale * (1.5 - height * 0.95) * random.uniform(0.8, 1.15)
            slab = make_box(
                (width, width, scale * 0.55),
                (0.92, 0.95, 0.97, 0.62 - height * 0.22),
            )
            slab.reparent_to(self.root)
            slab.set_pos(
                position.x + random.uniform(-0.4, 0.4) * scale,
                position.y + random.uniform(-0.4, 0.4) * scale,
                position.z + height * scale * 4.2,
            )
            slab.set_h(random.uniform(0, 360))
            slab.set_transparency(TransparencyAttrib.M_alpha)
            slab.set_light_off()
            slab.set_depth_write(False)
            self.smoke.append(_Timed(slab, random.uniform(0.9, 1.6)))
        # Spray thrown outwards off the base, flatter and shorter-lived.
        for _ in range(10):
            drop = make_box(
                (scale * 0.3, scale * 0.3, scale * 0.22), (0.97, 0.99, 1.0, 0.75)
            )
            drop.reparent_to(self.root)
            angle = random.uniform(0, math.tau)
            reach = random.uniform(0.9, 2.6) * scale
            drop.set_pos(
                position.x + math.cos(angle) * reach,
                position.y + math.sin(angle) * reach,
                position.z + random.uniform(0.1, 1.3) * scale,
            )
            drop.set_transparency(TransparencyAttrib.M_alpha)
            drop.set_light_off()
            drop.set_depth_write(False)
            self.smoke.append(_Timed(drop, random.uniform(0.5, 1.0)))

    def wake(self, position: Point3, scale: float = 1.0) -> None:
        """Foam on the surface above a running torpedo."""
        np = make_box((scale * 1.1, scale * 1.1, 0.12), (0.86, 0.90, 0.94, 0.5))
        np.reparent_to(self.root)
        np.set_pos(
            position.x + random.uniform(-0.5, 0.5),
            position.y + random.uniform(-0.5, 0.5),
            self.terrain.water_level + 0.06,
        )
        np.set_h(random.uniform(0, 360))
        np.set_transparency(TransparencyAttrib.M_alpha)
        np.set_light_off()
        np.set_depth_write(False)
        self.smoke.append(_Timed(np, 2.2))

    def smoke_puff(self, position: Point3, scale: float = 1.0) -> None:
        shade = random.uniform(0.30, 0.46)
        np = make_box((scale, scale, scale), (shade, shade, shade * 1.04, 0.34))
        np.reparent_to(self.root)
        np.set_pos(position + Vec3(random.uniform(-0.6, 0.6), random.uniform(-0.6, 0.6), 0))
        np.set_hpr(random.uniform(0, 360), random.uniform(0, 360), 0)
        np.set_transparency(TransparencyAttrib.M_alpha)
        np.set_light_off()
        np.set_depth_write(False)
        self.smoke.append(_Timed(np, 1.3))

    def shell(
        self,
        shooter,
        start: Point3,
        direction: Vec3,
        speed: float,
        damage: float,
        trail: bool = False,
    ) -> None:
        # Not a rigid body: a projectile inside the physics world has its
        # contacts resolved by the solver and visibly bounces off the thing it
        # was meant to destroy. Ballistics here are a two-line integration and
        # every hit is found by the swept ray below.
        np = self.root.attach_new_node("shell")
        np.set_pos(start)

        model = make_box((0.32, 0.9, 0.32), (1.0, 0.86, 0.45, 1.0))
        model.set_light_off()
        model.reparent_to(np)

        self.shells.append(Shell(np, direction * speed, shooter, damage, trail=trail))
        self.muzzle_flash(start, scale=0.9 if trail else 1.4)

    def launch_missile(
        self, shooter, target, spec: MissileSpec, rack_offset: float = 0.0
    ) -> None:
        """Fire a guided round from `shooter` at `target`."""
        start = shooter.muzzle() + shooter.np.get_quat().get_right() * rack_offset
        direction = (
            Vec3(0, 0, 1)
            if spec.loft_altitude > 0.0
            else intercept_direction(start, target, spec.burn_speed)
        )

        np = self.root.attach_new_node("missile")
        np.set_pos(start)

        body = make_box((0.26, 1.5, 0.26), (0.86, 0.86, 0.88, 1.0))
        body.reparent_to(np)
        fins = make_box((0.9, 0.35, 0.06), (0.62, 0.63, 0.66, 1.0), (0, -0.55, 0))
        fins.reparent_to(np)
        flame = make_box((0.22, 0.9, 0.22), (1.0, 0.72, 0.30, 1.0), (0, -1.1, 0))
        flame.set_light_off()
        flame.reparent_to(np)

        missile = Missile(np, spec, shooter, target, direction)
        if spec.run_depth is not None:
            # Underwater weapon: put it on its running depth immediately.
            missile.run_level = self.terrain.water_level - spec.run_depth
            np.set_z(missile.run_level)
            missile.prev_pos = Point3(np.get_pos())
        self.missiles.append(missile)
        jet_launch = getattr(shooter, "kind", None) == "jet"
        self.muzzle_flash(start, scale=1.65 if jet_launch else 1.1)
        if jet_launch:
            # A brief dense plume makes the rail launch readable from the
            # cockpit and from external cameras before the normal trail begins.
            for distance, scale in ((0.35, 0.46), (0.75, 0.38), (1.15, 0.30)):
                self.smoke_puff(start - direction * distance, scale=scale)

    def intercept_missile(self, missile: Missile) -> bool:
        """Remove an airborne missile destroyed by a close-in defence burst."""
        if missile not in self.missiles:
            return False
        self.missiles.remove(missile)
        position = Point3(missile.position)
        missile.np.remove_node()
        self.explosion(position, scale=0.72, debris_count=0)
        return True

    # ------------------------------------------------------------------
    # Per-frame update
    # ------------------------------------------------------------------
    def update(self, dt: float, on_hit, on_blast=None, on_structure_hit=None) -> None:
        self._update_missiles(dt, on_hit, on_blast, on_structure_hit)
        self._update_shells(dt, on_hit, on_structure_hit)
        self._update_timed(dt)

    def _update_missiles(
        self, dt: float, on_hit, on_blast=None, on_structure_hit=None
    ) -> None:
        alive: list[Missile] = []
        for missile in self.missiles:
            missile.update(dt)
            current = missile.position

            for _ in range(missile.trail_puffs(dt)):
                if missile.run_level is not None:
                    self.wake(current)
                else:
                    self.smoke_puff(current, scale=0.55)

            detonate = False
            victim = None
            structure = None
            burst_at = current

            if missile.fuzed():
                detonate = True
                if getattr(missile.target, "city_asset", False):
                    structure = missile.target
                else:
                    victim = missile.target
                burst_at = Point3(missile.target.position)
            else:
                # Sweep the travel so a fast round cannot pass through anything.
                hit_node, hit_pos = self._sweep_from(
                    missile.prev_pos, current, None, missile.shooter
                )
                if hit_node is not None:
                    detonate = True
                    victim = hit_node.get_python_tag("unit")
                    structure = hit_node.get_python_tag("city_target")
                    burst_at = hit_pos
                elif missile.expired or current.z < self.terrain.height_at(
                    current.x, current.y
                ) - 2.0:
                    detonate = True

            if detonate:
                scale = missile.spec.explosion_scale
                debris = 18 if missile.spec.blast_radius > 0.0 else 5
                self.explosion(burst_at, scale=scale, debris_count=debris)
                if missile.spec.blast_radius > 0.0 and on_blast is not None:
                    on_blast(missile.shooter, burst_at, missile.spec)
                elif victim is not None and getattr(victim, "alive", False):
                    on_hit(
                        missile.shooter,
                        victim,
                        missile.spec.damage,
                        missile.spec.weapon_name,
                    )
                elif structure is not None and on_structure_hit is not None:
                    on_structure_hit(
                        missile.shooter,
                        structure,
                        missile.spec.damage,
                    )
                missile.np.remove_node()
                continue

            missile.prev_pos = Point3(current)
            alive.append(missile)
        self.missiles = alive

    # Bodies a shell must fly straight through instead of detonating on.
    _TRANSPARENT_TO_SHELLS = frozenset({"shell", "debris", "missile"})

    def _sweep(self, shell: Shell, current: Point3):
        return self._sweep_from(shell.prev_pos, current, None, shell.shooter)

    def _sweep_from(self, start: Point3, end: Point3, own_node, shooter):
        """Nearest real obstacle along the shell's travel this frame.

        ray_test_closest is not usable here: the closest body along the sweep
        is the shell's *own* rigid body, so every round used to detonate at the
        muzzle on its first frame. Walk all hits and take the nearest one that
        is not the projectile itself, its shooter, or other debris.
        """
        result = self.world.ray_test_all(start, end)
        best_node, best_pos, best_fraction = None, None, float("inf")

        for hit in result.get_hits():
            node = hit.get_node()
            if node is None or node is own_node:
                continue
            if node.get_name() in self._TRANSPARENT_TO_SHELLS:
                continue
            if node.get_python_tag("unit") is shooter:
                continue
            fraction = hit.get_hit_fraction()
            if fraction < best_fraction:
                best_node, best_pos, best_fraction = node, hit.get_hit_pos(), fraction

        return best_node, best_pos

    def _update_shells(self, dt: float, on_hit, on_structure_hit=None) -> None:
        alive: list[Shell] = []
        for shell in self.shells:
            shell.ttl -= dt
            shell.velocity += Vec3(0, 0, -9.81) * dt
            current = Point3(shell.np.get_pos() + shell.velocity * dt)
            shell.np.set_pos(current)
            # Sweep from the previous position so a fast round cannot tunnel.
            hit_node, hit_pos = self._sweep(shell, current)
            hit_unit = None
            hit_structure = None
            detonated = False

            if hit_node is not None:
                detonated = True
                hit_unit = hit_node.get_python_tag("unit")
                hit_structure = hit_node.get_python_tag("city_target")
                current = hit_pos

            if not detonated and (
                shell.ttl <= 0
                or current.z < self.terrain.height_at(current.x, current.y) - 2.0
            ):
                detonated = True

            if detonated:
                self.explosion(current, scale=1.1, debris_count=3)
                if hit_unit is not None and getattr(hit_unit, "alive", False):
                    on_hit(shell.shooter, hit_unit, shell.damage)
                elif hit_structure is not None and on_structure_hit is not None:
                    on_structure_hit(shell.shooter, hit_structure, shell.damage)
                shell.np.remove_node()
                continue

            if shell.trail and random.random() < 0.55:
                self.smoke_puff(current, scale=0.5)

            shell.prev_pos = current
            if shell.velocity.length_squared() > 1e-6:
                shell.np.look_at(current + shell.velocity)
            alive.append(shell)
        self.shells = alive

    def _update_timed(self, dt: float) -> None:
        keep_hatches = []
        for swing in self.hatches:
            swing.elapsed += dt
            if swing.elapsed >= swing.total or swing.np.is_empty():
                if not swing.np.is_empty():
                    swing.np.set_p(0.0)
                continue
            swing.np.set_p(-swing.angle * swing.fraction())
            keep_hatches.append(swing)
        self.hatches = keep_hatches

        for collection in (self.tracers, self.smoke):
            keep = []
            for item in collection:
                item.ttl -= dt
                if item.ttl <= 0:
                    item.np.remove_node()
                    continue
                fade = item.ttl / item.max_ttl
                item.np.set_alpha_scale(fade)
                if collection is self.smoke:
                    grow = 1.0 + (1.0 - fade) * 2.2
                    item.np.set_scale(grow)
                    item.np.set_z(item.np.get_z() + 3.0 * dt)
                keep.append(item)
            collection[:] = keep

        keep_fire = []
        for ball in self.fireballs:
            ball.ttl -= dt
            if ball.ttl <= 0:
                ball.np.remove_node()
                continue
            age = 1.0 - ball.ttl / ball.max_ttl
            ball.np.set_alpha_scale((1.0 - age) ** 0.7)
            for piece, velocity in ball.pieces:
                piece.set_pos(velocity * age)
                piece.set_scale(max(0.05, 1.0 - age * 0.55))
            keep_fire.append(ball)
        self.fireballs = keep_fire

        keep_blood = []
        for burst in self.splatters:
            burst.ttl -= dt
            if burst.ttl <= 0:
                burst.np.remove_node()
                continue
            age = burst.max_ttl - burst.ttl
            burst.np.set_alpha_scale(min(1.0, (burst.ttl / burst.max_ttl) * 1.6))
            for piece, velocity in burst.pieces:
                piece.set_pos(
                    velocity.x * age,
                    velocity.y * age,
                    velocity.z * age - 0.5 * 9.81 * age * age,
                )
            keep_blood.append(burst)
        self.splatters = keep_blood

        keep_debris = []
        for chunk in self.debris:
            chunk.ttl -= dt
            if chunk.ttl <= 0:
                self.world.remove(chunk.node)
                chunk.np.remove_node()
                continue
            keep_debris.append(chunk)
        self.debris = keep_debris

    def clear(self) -> None:
        self.missiles.clear()
        for chunk in self.debris:
            self.world.remove(chunk.node)
        self.shells.clear()
        self.debris.clear()
        self.tracers.clear()
        self.fireballs.clear()
        self.splatters.clear()
        self.smoke.clear()
        self.root.node().remove_all_children()
