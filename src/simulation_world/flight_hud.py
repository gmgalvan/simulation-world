"""Code-native combat HUDs for the aircraft a player can fly.

Two cockpits share one set of symbology. The flight marker, artificial horizon,
target cue and status lines are identical work whichever machine is being
flown, so they live in `_CockpitHud`; each subclass only draws its own interior
and decides what its instruments say.
"""

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
HUD_RED = (1.0, 0.30, 0.17, 1.0)


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


def _ring(radius: float, segments: int = 24):
    return [
        (
            math.cos(math.tau * index / segments) * radius,
            math.sin(math.tau * index / segments) * radius,
        )
        for index in range(segments + 1)
    ]


class _CockpitHud:
    """Flight marker, artificial horizon, target box and weapon feedback."""

    def __init__(self, aspect2d: NodePath, name: str) -> None:
        self.cockpit_root = aspect2d.attach_new_node(f"{name}-cockpit")
        self.cockpit_root.set_bin("fixed", 90)
        self._build_cockpit()

        self.root = aspect2d.attach_new_node(f"{name}-hud")
        self.root.set_bin("fixed", 100)

        self.flight_marker = _lines(
            self.root,
            "flight-marker",
            (
                _ring(0.020, 16),
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

    # ------------------------------------------------------------------
    def _build_cockpit(self) -> None:
        raise NotImplementedError

    def show(self) -> None:
        self.cockpit_root.show()
        self.root.show()

    def hide(self) -> None:
        self.cockpit_root.hide()
        self.root.hide()
        self.target_root.set_pos(0, 0, 0)
        self.target_root.set_scale(1.0)
        self.launch_timer = 0.0

    def _tick(self, dt: float, shot_fired: bool) -> None:
        self.elapsed += dt
        if shot_fired:
            self.launch_timer = 0.85
        else:
            self.launch_timer = max(0.0, self.launch_timer - dt)

    def _place_target_cue(
        self, target, camera, render, lens, aspect_ratio: float, ready: bool
    ) -> None:
        """Slide the cue onto the target's projected screen position."""
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


class JetHud(_CockpitHud):
    """Fixed-wing cockpit: HUD combiner glass, glare shield and canopy rails."""

    def __init__(self, aspect2d: NodePath) -> None:
        super().__init__(aspect2d, "jet")

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
        self._tick(dt, shot_fired)

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
        self._place_target_cue(target, camera, render, lens, aspect_ratio, ready)

        low_altitude = altitude < 35.0 and vertical_speed < -1.0
        if low_altitude:
            self.weapon_text.setText("ALTITUD BAJA - SUBA")
            self.weapon_text.setFg(HUD_RED)
            self.flight_marker.set_color_scale(*HUD_RED)
            self.centre_mfd.setText("WARN\nPULL UP")
            self.centre_mfd.setFg(HUD_RED)
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


class HeliHud(_CockpitHud):
    """Gunship cockpit: armoured glazing, rotor shadow and a finite magazine.

    Deliberately not the jet's canopy. The gunship's view is boxed in by heavy
    frames and a low armoured coaming, and the instruments a pilot actually
    watches are different: rotor speed, torque and rounds remaining rather than
    throttle percentage.
    """

    def __init__(self, aspect2d: NodePath) -> None:
        super().__init__(aspect2d, "heli")

    def _build_cockpit(self) -> None:
        frame_dark = (0.045, 0.050, 0.045, 0.99)
        frame_edge = (0.15, 0.17, 0.14, 1.0)
        panel = (0.070, 0.078, 0.068, 0.99)

        # Rotor shadow: a faint bar sweeping across the top of the view. One
        # cheap moving element does more to say "helicopter" than any amount of
        # static frame detail, and it is kept very transparent so it never
        # strobes over a target.
        self.rotor_shadow = _polygon(
            self.cockpit_root,
            "rotor-shadow",
            ((-1.85, -0.06), (1.85, -0.06), (1.85, 0.06), (-1.85, 0.06)),
            (0.0, 0.0, 0.0, 0.16),
        )
        self.rotor_shadow.set_z(0.86)

        # Armoured coaming: taller and flatter than the jet's glare shield, with
        # the centre console standing up between the pilot's knees.
        _polygon(
            self.cockpit_root,
            "coaming",
            (
                (-1.85, -1.05),
                (1.85, -1.05),
                (1.85, -0.60),
                (0.62, -0.55),
                (0.34, -0.66),
                (-0.34, -0.66),
                (-0.62, -0.55),
                (-1.85, -0.60),
            ),
            panel,
        )
        # Door posts either side of the pilot, running from the coaming up into
        # the glazing frame. Drawn as free-floating panels they read as black
        # slabs hanging in the middle of the view.
        for side in (-1.0, 1.0):
            _polygon(
                self.cockpit_root,
                f"door-post-{'left' if side < 0 else 'right'}",
                (
                    (side * 1.85, -0.62),
                    (side * 1.60, -0.62),
                    (side * 1.44, 0.26),
                    (side * 1.85, 0.30),
                ),
                (0.030, 0.036, 0.030, 0.98),
            )

        # Instrument faces. Round dials rather than the jet's flat screens.
        for name, x in (("rotor", -1.50), ("torque", 1.50)):
            _polygon(
                self.cockpit_root,
                f"{name}-face",
                _ring(0.155, 20),
                (0.040, 0.090, 0.055, 0.96),
            ).set_pos(x, 0, -0.80)
            bezel = _lines(self.cockpit_root, f"{name}-bezel", (_ring(0.155, 20),), 2.4)
            bezel.set_color_scale(0.20, 0.26, 0.20, 1.0)
            bezel.set_pos(x, 0, -0.80)

        self.rotor_gauge = OnscreenText(
            parent=self.cockpit_root,
            text="RRPM\n---",
            pos=(-1.50, -0.83),
            scale=0.026,
            fg=(0.36, 1.0, 0.52, 0.92),
            align=TextNode.A_center,
            mayChange=True,
        )
        self.torque_gauge = OnscreenText(
            parent=self.cockpit_root,
            text="PAR\n---",
            pos=(1.50, -0.83),
            scale=0.026,
            fg=(0.36, 1.0, 0.52, 0.92),
            align=TextNode.A_center,
            mayChange=True,
        )

        # Weapons panel: the rack state is the thing worth a dedicated readout,
        # because unlike every other unit in the simulation it runs out.
        _card(
            self.cockpit_root,
            "armament-panel",
            (-0.30, 0.30, -1.00, -0.78),
            (0.030, 0.130, 0.060, 0.96),
        )
        rack_frame = _lines(
            self.cockpit_root,
            "armament-frame",
            (((-0.30, -1.00), (0.30, -1.00), (0.30, -0.78), (-0.30, -0.78), (-0.30, -1.00)),),
            2.1,
        )
        rack_frame.set_color_scale(0.18, 0.24, 0.18, 1.0)
        self.rack_text = OnscreenText(
            parent=self.cockpit_root,
            text="MISILES\n-",
            pos=(0.0, -0.84),
            scale=0.026,
            fg=(0.36, 1.0, 0.52, 0.92),
            align=TextNode.A_center,
            mayChange=True,
        )
        # One pip per round left, so the count reads at a glance.
        self.rack_pips = []
        for index in range(8):
            pip = _card(
                self.cockpit_root,
                f"rack-pip-{index}",
                (-0.012, 0.012, -0.020, 0.020),
                (0.36, 1.0, 0.52, 0.95),
            )
            pip.set_pos(-0.245 + index * 0.070, 0, -0.955)
            self.rack_pips.append(pip)

        # Heavy glazing frame: a boxy armoured windscreen with a centre post
        # and thick lower corners, unlike the jet's swept bubble.
        glazing = (
            ((-1.85, -0.62), (-1.42, 0.30), (-1.05, 0.86)),
            ((1.85, -0.62), (1.42, 0.30), (1.05, 0.86)),
            ((-1.05, 0.86), (1.05, 0.86)),
            ((-1.42, 0.30), (-0.60, 0.34)),
            ((1.42, 0.30), (0.60, 0.34)),
        )
        outer = _lines(self.cockpit_root, "glazing-frame", glazing, 14.0)
        outer.set_color_scale(*frame_dark)
        inner = _lines(self.cockpit_root, "glazing-highlight", glazing, 2.4)
        inner.set_color_scale(*frame_edge)

        # Gunsight, slung from the canopy bow on its bracket. Fixed to the
        # airframe, so aiming means pointing the whole helicopter.
        sight = self.cockpit_root.attach_new_node("gunsight")
        # The projector head has to sit directly above the reticle or the two
        # read as unrelated objects: the sight glass floating in mid-air and a
        # separate box bolted to the roof.
        bracket = _lines(
            sight,
            "gunsight-bracket",
            (((-0.048, 0.86), (-0.048, 0.24)), ((0.048, 0.86), (0.048, 0.24))),
            5.0,
        )
        bracket.set_color_scale(*frame_dark)
        housing = _lines(
            sight,
            "gunsight-housing",
            (((-0.105, 0.24), (0.105, 0.24), (0.105, 0.13), (-0.105, 0.13), (-0.105, 0.24)),),
            4.0,
        )
        housing.set_color_scale(*frame_edge)
        # Combiner glass hanging off the head, down over the reticle.
        glass = _card(sight, "gunsight-glass", (-0.105, 0.105, -0.02, 0.13),
                      (0.30, 0.52, 0.38, 0.10))
        glass.set_bin("fixed", 91)
        self.reticle = _lines(
            sight,
            "gunsight-reticle",
            (
                _ring(0.085, 20),
                _ring(0.028, 12),
                ((-0.135, 0.0), (-0.085, 0.0)),
                ((0.085, 0.0), (0.135, 0.0)),
                ((0.0, 0.085), (0.0, 0.125)),
                ((0.0, -0.085), (0.0, -0.125)),
            ),
            1.9,
        )
        self.reticle.set_color_scale(0.36, 1.0, 0.50, 0.66)

        # Faint reflections on the flat armoured glass: straighter than the
        # jet's, because the panes are flat.
        reflections = _lines(
            self.cockpit_root,
            "glass-reflections",
            (((-1.24, 0.62), (-0.86, 0.10)), ((1.30, 0.40), (1.06, 0.12))),
            1.2,
        )
        reflections.set_color_scale(0.74, 0.88, 0.96, 0.16)

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
        self._tick(dt, shot_fired)

        speed = math.hypot(unit.velocity.x, unit.velocity.y)
        vertical_speed = unit.velocity.z
        heading = unit.np.get_h() % 360.0
        missiles = getattr(unit, "manual_missiles", 0)
        self.flight_text.setText(
            f"VEL {speed:03.0f} m/s\n"
            f"ALT {altitude:03.0f} m\n"
            f"V/S {vertical_speed:+04.0f} m/s\n"
            f"RUMBO {heading:03.0f}\n"
            f"MISILES {missiles}"
        )

        # Rotor speed barely moves in level flight; torque follows how hard the
        # machine is being worked, which is what the pilot actually watches.
        rotor_rpm = 95.0 + 4.0 * math.sin(self.elapsed * 1.7) + abs(throttle) * 1.5
        torque = min(
            100.0,
            42.0 + speed * 1.5 + max(0.0, vertical_speed) * 3.4,
        )
        self.rotor_gauge.setText(f"RRPM\n{rotor_rpm:03.0f}%")
        self.torque_gauge.setText(f"PAR\n{torque:03.0f}%")
        self.torque_gauge.setFg(
            HUD_AMBER if torque > 92.0 else (0.36, 1.0, 0.52, 0.92)
        )

        for index, pip in enumerate(self.rack_pips):
            spent = index >= missiles
            pip.set_color(
                (0.20, 0.24, 0.20, 0.85) if spent else (0.36, 1.0, 0.52, 0.95)
            )
        self.rack_text.setText(
            "MISILES\nVACIO" if missiles == 0 else f"MISILES\n{missiles}"
        )
        self.rack_text.setFg(HUD_RED if missiles == 0 else (0.36, 1.0, 0.52, 0.92))

        # The rotor shadow sweeps rather than spins: seen from inside, a blade
        # crossing overhead is a bar travelling across the glazing.
        self.rotor_shadow.set_x(math.sin(self.elapsed * 5.5) * 1.15)
        self.rotor_shadow.set_r(math.sin(self.elapsed * 5.5) * 7.0)

        self.horizon.set_r(-unit.model_np.get_r() * 0.70)
        self.horizon.set_z(max(-0.30, min(0.30, camera.get_p() / 55.0)))
        self.cockpit_root.set_r(unit.model_np.get_r() * 0.10)

        locked = target is not None
        armed = missiles > 0
        ready = locked and armed and unit.cooldown <= 0.0
        target_distance = (
            (target.position - unit.position).length() if locked else 0.0
        )
        if ready:
            colour = HUD_GREEN
            label = f"MISIL LISTO  {target_distance:.0f}m"
        elif locked and not armed:
            colour = HUD_RED
            label = f"SIN MISILES  {target_distance:.0f}m"
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
        self._place_target_cue(target, camera, render, lens, aspect_ratio, ready)

        # A gunship lives low, so the jet's altitude warning would nag forever.
        # What matters instead is descending onto the ground with rate on.
        sinking = altitude < 12.0 and vertical_speed < -2.5
        if sinking:
            self.weapon_text.setText("SUELO - TIRE DEL COLECTIVO")
            self.weapon_text.setFg(HUD_RED)
            self.reticle.set_color_scale(*HUD_RED)
        elif self.launch_timer > 0.0:
            self.weapon_text.setText("MISIL FUERA")
            self.weapon_text.setFg((1.0, 0.88, 0.35, 1.0))
            self.reticle.set_color_scale(1.0, 0.90, 0.45, 0.85)
        elif ready:
            self.weapon_text.setText("CLIC IZQ: MISIL   CLIC DER: CANON")
            self.weapon_text.setFg(HUD_GREEN)
            self.reticle.set_color_scale(0.36, 1.0, 0.50, 0.80)
        elif locked and not armed:
            self.weapon_text.setText("RACKS VACIOS - CLIC DER: CANON")
            self.weapon_text.setFg(HUD_AMBER)
            self.reticle.set_color_scale(1.0, 0.78, 0.20, 0.80)
        elif locked:
            self.weapon_text.setText(f"RECARGANDO {max(0.0, unit.cooldown):.1f}s")
            self.weapon_text.setFg(HUD_AMBER)
            self.reticle.set_color_scale(0.36, 1.0, 0.50, 0.66)
        else:
            self.weapon_text.setText("SIN BLANCO   CLIC DER: CANON")
            self.weapon_text.setFg(HUD_WHITE)
            self.reticle.set_color_scale(0.36, 1.0, 0.50, 0.66)
        self.flight_marker.set_color_scale(
            *(HUD_RED if sinking else HUD_GREEN)
        )
