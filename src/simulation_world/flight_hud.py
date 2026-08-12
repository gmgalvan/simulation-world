"""Code-native combat HUD for a player-controlled fixed-wing aircraft."""

from __future__ import annotations

import math

from direct.gui.OnscreenText import OnscreenText
from panda3d.core import (
    CardMaker,
    Geom,
    GeomNode,
    GeomTriangles,
    GeomVertexData,
    GeomVertexFormat,
    GeomVertexWriter,
    LineSegs,
    NodePath,
    Point2,
    TextNode,
    TransparencyAttrib,
)

HUD_GREEN = (0.32, 1.0, 0.38, 0.96)
HUD_AMBER = (1.0, 0.78, 0.20, 1.0)
HUD_WHITE = (0.94, 0.96, 0.88, 0.90)


def _lines(parent: NodePath, name: str, paths, thickness: float = 2.0) -> NodePath:
    lines = LineSegs(name)
    lines.set_thickness(thickness)
    lines.set_color(1.0, 1.0, 1.0, 1.0)
    for points in paths:
        first, *rest = points
        lines.move_to(first[0], 0.0, first[1])
        for x, z in rest:
            lines.draw_to(x, 0.0, z)
    node = parent.attach_new_node(lines.create())
    node.set_depth_test(False)
    node.set_depth_write(False)
    return node


def _card(parent: NodePath, name: str, frame, colour) -> NodePath:
    card = CardMaker(name)
    card.set_frame(*frame)
    node = parent.attach_new_node(card.generate())
    node.set_color(*colour)
    node.set_transparency(TransparencyAttrib.M_alpha)
    node.set_depth_test(False)
    node.set_depth_write(False)
    return node


def _polygon(parent: NodePath, name: str, points, colour) -> NodePath:
    data = GeomVertexData(name, GeomVertexFormat.get_v3(), Geom.UH_static)
    vertex = GeomVertexWriter(data, "vertex")
    for x, z in points:
        vertex.add_data3(x, 0.0, z)
    triangles = GeomTriangles(Geom.UH_static)
    for index in range(1, len(points) - 1):
        triangles.add_vertices(0, index, index + 1)
    geom = Geom(data)
    geom.add_primitive(triangles)
    geom_node = GeomNode(name)
    geom_node.add_geom(geom)
    node = parent.attach_new_node(geom_node)
    node.set_color(*colour)
    node.set_transparency(TransparencyAttrib.M_alpha)
    node.set_depth_test(False)
    node.set_depth_write(False)
    return node


class JetHud:
    """Flight marker, artificial horizon, target box and weapon feedback."""

    def __init__(self, aspect2d: NodePath) -> None:
        self.cockpit_root = aspect2d.attach_new_node("jet-cockpit")
        self.cockpit_root.set_bin("fixed", 90)
        self._build_cockpit()

        self.root = aspect2d.attach_new_node("jet-hud")
        self.root.set_bin("fixed", 100)

        circle = []
        for index in range(17):
            angle = math.tau * index / 16.0
            circle.append((math.cos(angle) * 0.020, math.sin(angle) * 0.020))
        self.flight_marker = _lines(
            self.root,
            "flight-marker",
            (
                circle,
                ((-0.095, 0.0), (-0.030, 0.0)),
                ((0.030, 0.0), (0.095, 0.0)),
                ((0.0, -0.020), (0.0, -0.052)),
            ),
            2.2,
        )
        self.flight_marker.set_color_scale(*HUD_GREEN)

        self.horizon = _lines(
            self.root,
            "artificial-horizon",
            (
                ((-0.58, 0.0), (-0.18, 0.0)),
                ((0.18, 0.0), (0.58, 0.0)),
                ((-0.36, -0.018), (-0.36, 0.018)),
                ((0.36, -0.018), (0.36, 0.018)),
            ),
            1.35,
        )
        self.horizon.set_color_scale(0.48, 0.92, 0.55, 0.68)

        # Open corners keep the target visible instead of covering it with a
        # solid square. The whole root follows the projected target position.
        self.target_root = self.root.attach_new_node("target-cue")
        s, corner = 0.060, 0.024
        self.target_box = _lines(
            self.target_root,
            "target-box",
            (
                ((-s, -s + corner), (-s, -s), (-s + corner, -s)),
                ((s - corner, -s), (s, -s), (s, -s + corner)),
                ((s, s - corner), (s, s), (s - corner, s)),
                ((-s + corner, s), (-s, s), (-s, s - corner)),
            ),
            2.8,
        )
        self.target_label = OnscreenText(
            parent=self.target_root,
            text="SIN BLOQUEO",
            pos=(0.0, -0.095),
            scale=0.026,
            fg=(1, 1, 1, 1),
            shadow=(0, 0, 0, 0.9),
            align=TextNode.A_center,
            mayChange=True,
        )

        self.flight_text = OnscreenText(
            parent=self.root,
            text="",
            pos=(-1.30, -0.54),
            scale=0.034,
            fg=HUD_GREEN,
            shadow=(0, 0, 0, 0.85),
            align=TextNode.A_left,
            mayChange=True,
        )
        self.weapon_text = OnscreenText(
            parent=self.root,
            text="",
            pos=(0.0, -0.72),
            scale=0.038,
            fg=HUD_WHITE,
            shadow=(0, 0, 0, 0.9),
            align=TextNode.A_center,
            mayChange=True,
        )
        self.elapsed = 0.0
        self.launch_timer = 0.0
        self.cockpit_root.hide()
        self.root.hide()

    def _build_cockpit(self) -> None:
        """Build a stylised interior around the view without hiding the sky."""
        frame_dark = (0.035, 0.045, 0.055, 0.98)
        frame_edge = (0.12, 0.15, 0.17, 1.0)
        panel = (0.055, 0.070, 0.075, 0.99)

        # Subtle green-grey HUD combiner glass. It is intentionally faint:
        # enough to place the symbology inside a cockpit, never enough to wash
        # out a distant target or the terrain.
        _card(
            self.cockpit_root,
            "hud-glass",
            (-0.70, 0.70, -0.42, 0.64),
            (0.18, 0.38, 0.30, 0.045),
        )

        # Glare shield and instrument coaming. The centre notch leaves the
        # forward view open while the raised sides make the eye position read
        # as seated inside the aircraft instead of floating ahead of its nose.
        _polygon(
            self.cockpit_root,
            "glare-shield",
            (
                (-1.78, -1.02),
                (1.78, -1.02),
                (1.78, -0.72),
                (0.70, -0.64),
                (0.40, -0.74),
                (-0.40, -0.74),
                (-0.70, -0.64),
                (-1.78, -0.72),
            ),
            panel,
        )
        _card(
            self.cockpit_root,
            "left-console",
            (-1.78, -1.16, -0.72, -0.47),
            (0.025, 0.032, 0.038, 0.98),
        )
        _card(
            self.cockpit_root,
            "right-console",
            (1.16, 1.78, -0.72, -0.47),
            (0.025, 0.032, 0.038, 0.98),
        )

        # Small powered displays add believable cockpit detail without adding
        # unreadable decorative text or competing with the actual flight HUD.
        for side in (-1.0, 1.0):
            _card(
                self.cockpit_root,
                f"mfd-{'left' if side < 0 else 'right'}",
                (side * 1.48 - 0.22, side * 1.48 + 0.22, -0.91, -0.69),
                (0.035, 0.16, 0.13, 0.92),
            )
        mfd_frames = _lines(
            self.cockpit_root,
            "mfd-frames",
            (
                ((-1.72, -0.94), (-1.24, -0.94), (-1.24, -0.67), (-1.72, -0.67), (-1.72, -0.94)),
                ((1.24, -0.94), (1.72, -0.94), (1.72, -0.67), (1.24, -0.67), (1.24, -0.94)),
                ((-0.31, -0.98), (0.31, -0.98), (0.25, -0.76), (-0.25, -0.76), (-0.31, -0.98)),
            ),
            2.1,
        )
        mfd_frames.set_color_scale(0.16, 0.22, 0.22, 1.0)
        _card(
            self.cockpit_root,
            "centre-display",
            (-0.25, 0.25, -0.96, -0.78),
            (0.025, 0.12, 0.10, 0.96),
        )
        self.left_mfd = OnscreenText(
            parent=self.cockpit_root,
            text="NAV\n---",
            pos=(-1.48, -0.73),
            scale=0.025,
            fg=(0.32, 1.0, 0.50, 0.90),
            align=TextNode.A_center,
            mayChange=True,
        )
        self.right_mfd = OnscreenText(
            parent=self.cockpit_root,
            text="ARM\nMISIL",
            pos=(1.48, -0.73),
            scale=0.025,
            fg=(0.32, 1.0, 0.50, 0.90),
            align=TextNode.A_center,
            mayChange=True,
        )
        self.centre_mfd = OnscreenText(
            parent=self.cockpit_root,
            text="FCS\nAUTO",
            pos=(0.0, -0.81),
            scale=0.024,
            fg=(0.32, 1.0, 0.50, 0.90),
            align=TextNode.A_center,
            mayChange=True,
        )

        # Canopy bow and diagonal side rails. Thick outer rails, then a narrow
        # highlight, create volume while keeping almost all of the view clear.
        rails = (
            ((-1.78, -0.74), (-1.57, 0.55), (-0.82, 1.00)),
            ((1.78, -0.74), (1.57, 0.55), (0.82, 1.00)),
            ((-0.82, 1.00), (0.82, 1.00)),
        )
        outer = _lines(self.cockpit_root, "canopy-rails", rails, 12.0)
        outer.set_color_scale(*frame_dark)
        inner = _lines(self.cockpit_root, "canopy-highlights", rails, 2.2)
        inner.set_color_scale(*frame_edge)

        # HUD-glass supports and the curved-looking forward coaming edge.
        details = _lines(
            self.cockpit_root,
            "cockpit-details",
            (
                ((-0.70, -0.42), (-0.70, 0.64), (-0.56, 0.71)),
                ((0.70, -0.42), (0.70, 0.64), (0.56, 0.71)),
                ((-0.70, -0.42), (-0.40, -0.53), (0.40, -0.53), (0.70, -0.42)),
            ),
            3.0,
        )
        details.set_color_scale(0.15, 0.19, 0.20, 0.96)

        # A handful of illuminated panel strokes sell switches and screens at
        # the resolution where the rest of this procedural world is viewed.
        panel_lights = _lines(
            self.cockpit_root,
            "panel-lights",
            (
                ((-1.66, -0.60), (-1.25, -0.60)),
                ((1.25, -0.60), (1.66, -0.60)),
                ((-1.57, -0.65), (-1.48, -0.65)),
                ((1.48, -0.65), (1.57, -0.65)),
            ),
            2.0,
        )
        panel_lights.set_color_scale(0.30, 0.95, 0.55, 0.72)

        # Restrained canopy reflections: asymmetric and very transparent, so
        # they suggest curved glass without looking like cracks or obscuring a lock.
        reflections = _lines(
            self.cockpit_root,
            "canopy-reflections",
            (
                ((-1.18, 0.72), (-0.78, 0.42), (-0.62, 0.08)),
                ((1.34, 0.48), (1.08, 0.22)),
            ),
            1.15,
        )
        reflections.set_color_scale(0.72, 0.90, 1.0, 0.20)

    def show(self) -> None:
        self.cockpit_root.show()
        self.root.show()

    def hide(self) -> None:
        self.cockpit_root.hide()
        self.root.hide()
        self.target_root.set_pos(0, 0, 0)
        self.target_root.set_scale(1.0)
        self.launch_timer = 0.0

    def update(
        self,
        dt: float,
        unit,
        target,
        throttle: float,
        altitude: float,
        camera,
        render,
        lens,
        aspect_ratio: float,
        shot_fired: bool,
    ) -> None:
        self.elapsed += dt
        if shot_fired:
            self.launch_timer = 0.85
        else:
            self.launch_timer = max(0.0, self.launch_timer - dt)

        speed = math.hypot(unit.velocity.x, unit.velocity.y)
        vertical_speed = unit.velocity.z
        heading = unit.np.get_h() % 360.0
        self.flight_text.setText(
            f"VEL {speed:03.0f} m/s\n"
            f"ALT {altitude:03.0f} m\n"
            f"V/S {vertical_speed:+04.0f} m/s\n"
            f"RUMBO {heading:03.0f}\n"
            f"POT {throttle * 100:03.0f}%"
        )
        self.left_mfd.setText(f"NAV\n{heading:03.0f}\n{altitude:03.0f}M")
        self.right_mfd.setText(
            "ARM\nLISTO" if unit.cooldown <= 0.0 else f"ARM\n{unit.cooldown:.1f}S"
        )

        # The artificial horizon counters the aircraft bank and slides with
        # camera pitch, so climb and roll remain legible against an empty sky.
        self.horizon.set_r(-unit.model_np.get_r() * 0.70)
        self.horizon.set_z(max(-0.30, min(0.30, camera.get_p() / 55.0)))
        # The pilot and canopy share the aircraft roll, but damp it to keep the
        # view comfortable and to prevent black rails sweeping over the target.
        self.cockpit_root.set_r(unit.model_np.get_r() * 0.10)

        locked = target is not None
        ready = locked and unit.cooldown <= 0.0
        target_distance = (
            (target.position - unit.position).length() if locked else 0.0
        )
        if ready:
            colour = HUD_GREEN
            label = f"MISIL LISTO  {target_distance:.0f}m"
        elif locked:
            colour = HUD_AMBER
            label = (
                f"RECARGA {max(0.0, unit.cooldown):.1f}  "
                f"{target_distance:.0f}m"
            )
        else:
            colour = HUD_WHITE
            label = "SIN BLOQUEO"
        self.target_root.set_color_scale(*colour)
        self.target_label.setText(label)

        if target is None:
            self.target_root.set_pos(0, 0, 0)
        else:
            camera_point = camera.get_relative_point(render, target.position)
            screen = Point2()
            if lens.project(camera_point, screen):
                x = max(
                    -aspect_ratio + 0.10,
                    min(aspect_ratio - 0.10, screen.x * aspect_ratio),
                )
                z = max(-0.82, min(0.82, screen.y))
                self.target_root.set_pos(x, 0, z)
            else:
                self.target_root.set_pos(0, 0, 0)

        pulse = 1.0
        if ready:
            pulse += 0.055 * math.sin(self.elapsed * 8.5)
        self.target_root.set_scale(pulse)

        low_altitude = altitude < 35.0 and vertical_speed < -1.0
        if low_altitude:
            self.weapon_text.setText("ALTITUD BAJA - SUBA")
            self.weapon_text.setFg((1.0, 0.28, 0.16, 1.0))
            self.flight_marker.set_color_scale(1.0, 0.32, 0.18, 1.0)
            self.centre_mfd.setText("WARN\nPULL UP")
            self.centre_mfd.setFg((1.0, 0.32, 0.18, 1.0))
        elif self.launch_timer > 0.0:
            self.weapon_text.setText("MISIL FUERA")
            self.weapon_text.setFg((1.0, 0.88, 0.35, 1.0))
            self.flight_marker.set_color_scale(1.0, 0.90, 0.45, 1.0)
        elif ready:
            self.weapon_text.setText("CLIC IZQ: LANZAR")
            self.weapon_text.setFg(HUD_GREEN)
            self.flight_marker.set_color_scale(*HUD_GREEN)
        elif locked:
            self.weapon_text.setText(f"RECARGANDO {max(0.0, unit.cooldown):.1f}s")
            self.weapon_text.setFg(HUD_AMBER)
            self.flight_marker.set_color_scale(*HUD_GREEN)
        else:
            self.weapon_text.setText("BUSCANDO BLANCO")
            self.weapon_text.setFg(HUD_WHITE)
            self.flight_marker.set_color_scale(*HUD_GREEN)
        if not low_altitude:
            self.centre_mfd.setText("FCS\nAUTO")
            self.centre_mfd.setFg((0.32, 1.0, 0.50, 0.90))
