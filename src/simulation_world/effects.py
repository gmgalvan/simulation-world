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
from .missiles import Missile, MissileSpec, intercept_direction


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
        for i in range(5):
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

        self.fireballs.append(Fireball(root, 0.45, pieces, scale))
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

    def launch_missile(self, shooter, target, spec: MissileSpec) -> None:
        """Fire a guided round from `shooter` at `target`."""
        start = shooter.muzzle()
        direction = intercept_direction(start, target, spec.burn_speed)

        np = self.root.attach_new_node("missile")
        np.set_pos(start)

        body = make_box((0.26, 1.5, 0.26), (0.86, 0.86, 0.88, 1.0))
        body.reparent_to(np)
        fins = make_box((0.9, 0.35, 0.06), (0.62, 0.63, 0.66, 1.0), (0, -0.55, 0))
        fins.reparent_to(np)
        flame = make_box((0.22, 0.9, 0.22), (1.0, 0.72, 0.30, 1.0), (0, -1.1, 0))
        flame.set_light_off()
        flame.reparent_to(np)

        self.missiles.append(Missile(np, spec, shooter, target, direction))
        self.muzzle_flash(start, scale=1.1)

    # ------------------------------------------------------------------
    # Per-frame update
    # ------------------------------------------------------------------
    def update(self, dt: float, on_hit) -> None:
        self._update_missiles(dt, on_hit)
        self._update_shells(dt, on_hit)
        self._update_timed(dt)

    def _update_missiles(self, dt: float, on_hit) -> None:
        alive: list[Missile] = []
        for missile in self.missiles:
            missile.update(dt)
            current = missile.position

            for _ in range(missile.trail_puffs(dt)):
                self.smoke_puff(current, scale=0.55)

            detonate = False
            victim = None
            burst_at = current

            if missile.fuzed():
                detonate, victim = True, missile.target
                burst_at = Point3(missile.target.position)
            else:
                # Sweep the travel so a fast round cannot pass through anything.
                hit_node, hit_pos = self._sweep_from(
                    missile.prev_pos, current, None, missile.shooter
                )
                if hit_node is not None:
                    detonate = True
                    victim = hit_node.get_python_tag("unit")
                    burst_at = hit_pos
                elif missile.expired or current.z < self.terrain.height_at(
                    current.x, current.y
                ) - 2.0:
                    detonate = True

            if detonate:
                self.explosion(burst_at, scale=1.9, debris_count=5)
                if victim is not None and getattr(victim, "alive", False):
                    on_hit(missile.shooter, victim, missile.spec.damage)
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

    def _update_shells(self, dt: float, on_hit) -> None:
        alive: list[Shell] = []
        for shell in self.shells:
            shell.ttl -= dt
            shell.velocity += Vec3(0, 0, -9.81) * dt
            current = Point3(shell.np.get_pos() + shell.velocity * dt)
            shell.np.set_pos(current)
            # Sweep from the previous position so a fast round cannot tunnel.
            hit_node, hit_pos = self._sweep(shell, current)
            hit_unit = None
            detonated = False

            if hit_node is not None:
                detonated = True
                hit_unit = hit_node.get_python_tag("unit")
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
