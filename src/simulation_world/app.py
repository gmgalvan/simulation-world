"""Panda3D application: window, lighting, terrain streaming, camera and HUD."""

from __future__ import annotations

import math
from pathlib import Path

from direct.gui.OnscreenText import OnscreenText
from direct.showbase.ShowBase import ShowBase
from panda3d.bullet import BulletDebugNode, BulletWorld
from panda3d.core import (
    AmbientLight,
    CardMaker,
    DirectionalLight,
    Fog,
    NodePath,
    Point3,
    TextNode,
    TransparencyAttrib,
    Vec3,
    Vec4,
    WindowProperties,
)

from .assets import AssetLibrary
from .battle import TEAM_COLORS, TEAM_NAMES, Battle
from .chunks import ChunkManager
from .flight_hud import HeliHud, JetHud
from .player_control import PlayerController
from .terrain import WATER_COLOR, InfiniteTerrain

SKY_COLOR = Vec4(0.52, 0.68, 0.86, 1.0)
PHYSICS_STEP = 1.0 / 120.0
CAMERA_MODES = ("orbit", "chase", "high", "free")
# Short labels for the inspector, so the HUD names what you are looking at.
KIND_LABELS = {
    "jet": "caza F-35",
    "helicopter": "helicoptero Mi-24",
    "osprey": "convertiplano V-22",
    "tank": "tanque Leopard 2",
    "destroyer": "destructor lanzamisiles",
    "submarine": "submarino lanzamisiles",
    "sam": "bateria antiaerea",
    "rocket": "equipo de RPG",
    "rifleman": "fusilero",
}
MOVE_KEYS = ("w", "a", "s", "d", "q", "e")
LOOK_KEYS = ("arrow_left", "arrow_right", "arrow_up", "arrow_down")

# Legend: display order and short labels, heaviest units first.
ROSTER = (
    ("jet", "caza"),
    ("helicopter", "heli"),
    ("osprey", "V22"),
    ("tank", "tanque"),
    ("destroyer", "destructor"),
    ("submarine", "submarino"),
    ("sam", "AA"),
    ("rocket", "RPG"),
    ("rifleman", "fusil"),
)


class SimulationApp(ShowBase):
    def __init__(self, args) -> None:
        super().__init__()
        self.args = args
        self.paused = False
        self.camera_mode = "orbit"
        self.chase_index = 0
        self.orbit_angle = 40.0
        self.orbit_height = 0.62
        self.orbit_distance = 0.72
        self.sim_time = 0.0
        self._physics_debt = 0.0
        self.focus_smooth: Point3 | None = None
        self._report_written = False
        self.keys: set[str] = set()
        self.mouse_look = False
        self._last_pointer: tuple[float, float] | None = None
        self.dragging = False
        self._drag_from: tuple[float, float] | None = None
        self.inspect_unit = None
        self.inspect_angle = 0.0
        self.inspect_zoom = 1.0
        self.unit_view_masked = None
        self.player_control = PlayerController()

        self.disable_mouse()
        self.set_background_color(SKY_COLOR)

        self.world = BulletWorld()
        self.world.set_gravity(Vec3(0, 0, -9.81))

        self.terrain = InfiniteTerrain(
            seed=args.seed,
            relief=args.relief,
            feature_scale=args.feature_scale,
            chunk_size=args.chunk_size,
            clear_radius=args.clear_radius,
        )
        self.chunks = ChunkManager(
            self.terrain,
            self.render,
            self.world,
            view_radius=args.view_chunks,
            physics_radius=max(1, args.view_chunks - 2),
            trees_per_chunk=args.trees,
        )

        self._setup_lighting()
        self._setup_water()

        assets_dir = Path(args.assets) if args.assets else Path.cwd() / "assets"
        self.assets = AssetLibrary(self.loader, assets_dir)

        self.debug_np: NodePath | None = None
        self.battle: Battle | None = None
        self._start_battle(args.seed)
        self.assets.print_report()

        # Build the first ring up front, otherwise the units spawn over a void
        # and fall through it before streaming catches up.
        self.chunks.update(self._stream_anchors(), budget=10_000)
        print(
            f"[terreno] {self.chunks.loaded_count()} chunks cargados "
            f"({self.chunks.view_distance:.0f} m de alcance)"
        )

        self._setup_hud()
        self._setup_camera()
        self._bind_keys()
        self.task_mgr.add(self._update, "simulation-update")

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _setup_lighting(self) -> None:
        sun = DirectionalLight("sun")
        sun.set_color(Vec4(1.05, 1.0, 0.9, 1.0))
        sun.set_shadow_caster(True, 3072, 3072)
        # An endless world cannot have one shadow frustum covering everything,
        # so it covers a generous patch and is moved onto the action each frame.
        self.shadow_span = 460.0
        lens = sun.get_lens()
        lens.set_film_size(self.shadow_span, self.shadow_span)
        lens.set_near_far(-900.0, 900.0)
        sun_np = self.render.attach_new_node(sun)
        sun_np.set_hpr(-38, -50, 0)
        self.render.set_light(sun_np)
        self.sun_np = sun_np

        ambient = AmbientLight("ambient")
        ambient.set_color(Vec4(0.38, 0.42, 0.52, 1.0))
        self.render.set_light(self.render.attach_new_node(ambient))

        # Linear fog tuned to the streaming radius: it swallows the chunk
        # boundary so terrain pops in inside the haze rather than in plain view.
        view = self.chunks.view_distance
        fog = Fog("distance")
        fog.set_color(SKY_COLOR.get_xyz())
        fog.set_linear_range(view * 0.72, view * 1.0)
        self.render.set_fog(fog)

        try:
            self.render.set_shader_auto()
        except Exception:  # noqa: BLE001 - fall back to fixed-function lighting
            pass

    def _setup_water(self) -> None:
        """One big translucent quad that rides along under the camera."""
        span = self.chunks.view_distance * 1.2
        card = CardMaker("water")
        card.set_frame(-span, span, -span, span)
        self.water = self.render.attach_new_node(card.generate())
        self.water.set_p(-90)  # cards face -Y by default; lay it flat
        self.water.set_color(*WATER_COLOR)
        self.water.set_transparency(TransparencyAttrib.M_alpha)
        self.water.set_light_off()
        self.water.set_bin("fixed", 10)
        self.water.set_depth_write(False)
        self.water.set_z(self.terrain.water_level)

    def _setup_hud(self) -> None:
        self.status_text = OnscreenText(
            text="",
            pos=(0.0, 0.90),
            scale=0.062,
            fg=(1, 1, 1, 1),
            shadow=(0, 0, 0, 0.85),
            align=TextNode.A_center,
            mayChange=True,
        )
        # Per-team legend, in team colours, so you can read the shape of the
        # battle at a glance instead of just a total.
        self.roster_text = {}
        for team, colour, y in (
            (0, TEAM_COLORS[0], 0.83),
            (1, TEAM_COLORS[1], 0.78),
        ):
            self.roster_text[team] = OnscreenText(
                text="",
                pos=(0.0, y),
                scale=0.045,
                fg=colour,
                shadow=(0, 0, 0, 0.9),
                align=TextNode.A_center,
                mayChange=True,
            )

        # Separate civil ledger: the military roster stays readable while the
        # city shows both survivors and losses for every protected asset type.
        self.civil_text = OnscreenText(
            text="",
            pos=(0.0, 0.725),
            scale=0.038,
            fg=(1, 1, 1, 1),
            shadow=(0, 0, 0, 0.9),
            align=TextNode.A_center,
            mayChange=True,
        )

        self.help_text = OnscreenText(
            text="",
            pos=(0.0, -0.94),
            scale=0.040,
            fg=(0.92, 0.94, 1.0, 1),
            shadow=(0, 0, 0, 0.85),
            align=TextNode.A_center,
            mayChange=True,
        )
        self.crosshair_text = OnscreenText(
            text="+",
            pos=(0.0, -0.025),
            scale=0.055,
            fg=(0.94, 0.96, 0.88, 0.92),
            shadow=(0, 0, 0, 0.9),
            align=TextNode.A_center,
        )
        self.crosshair_text.hide()
        # One cockpit per flyable aircraft, keyed by unit kind.
        self.cockpits = {
            "jet": JetHud(self.aspect2d),
            "helicopter": HeliHud(self.aspect2d),
        }
        self._refresh_help()

    def _inspect_label(self) -> str:
        unit = self.inspect_unit
        if unit is None:
            return ""
        kind = KIND_LABELS.get(unit.kind, unit.kind)
        team = TEAM_NAMES[unit.team]
        if not unit.alive:
            return f"{kind} ({team}) — destruido"
        if unit.kind == "submarine":
            remaining = max(0, math.ceil(unit.strategic_cooldown))
            strategic = (
                "LISTO"
                if remaining == 0
                else f"{remaining // 60}:{remaining % 60:02d}"
            )
            return (
                f"{kind} ({team}) — vida {unit.hp_frac * 100:.0f}% — "
                f"salva estratégica {strategic}"
            )
        return f"{kind} ({team}) — vida {unit.hp_frac * 100:.0f}%"

    def _refresh_help(self) -> None:
        if self.player_control.active:
            controlled = self.player_control.unit.kind
            if controlled == "jet":
                text = (
                    "CAZA: W/S potencia   A/D virar   E/ARRIBA subir   Q/ABAJO bajar   "
                    "CLIC IZQ misil guiado   [T] soltar   [ESPACIO] pausa"
                )
            elif controlled == "helicopter":
                missiles = self.player_control.unit.manual_missiles
                text = (
                    "HELICOPTERO: W/S adelante-atras   A/D guiñada   "
                    "E/ARRIBA subir   Q/ABAJO bajar   ←/→ desplazar   "
                    f"CLIC IZQ disparar ({missiles} misiles)   [T] soltar"
                )
            else:
                text = (
                    "FUSILERO: W/S avanzar-retroceder   A/D girar   "
                    "CLIC IZQ disparar   [T] soltar control   [ESPACIO] pausa"
                )
        elif self.camera_mode == "free":
            text = (
                "LIBRE: WASD mover   Q/E bajar-subir   ARRASTRA raton para mirar   "
                "RUEDA avanzar   SHIFT rapido   [C] camara   [R] batalla   [ESC] salir"
            )
        elif self.camera_mode == "inspect":
            text = (
                "INSPECCION: [TAB] siguiente unidad   [SHIFT+TAB] anterior   "
                "ARRASTRA girar   RUEDA acercar   [V] vista frontal   "
                "[T] controlar fusilero/caza/heli   [I] salir"
            )
        elif self.camera_mode == "unit":
            text = (
                "VISTA DE UNIDAD: [TAB] siguiente   [SHIFT+TAB] anterior   "
                "[T] controlar fusilero/caza/heli   [V] volver a inspeccion   [I] salir"
            )
        else:
            text = (
                f"{self.camera_mode.upper()}: ARRASTRA raton para girar   RUEDA zoom   "
                "[I] inspeccionar unidad   [C] camara   [R] nueva batalla   "
                "[ESPACIO] pausa   [ESC] salir"
            )
        self.help_text.setText(text)

    def _setup_camera(self) -> None:
        self.camLens.set_fov(60)
        self.camLens.set_near_far(0.8, self.chunks.view_distance * 2.2)

    def _bind_keys(self) -> None:
        self.accept("escape", self.user_exit)
        self.accept("r", self.restart_battle)
        self.accept("c", self.cycle_camera)
        self.accept("space", self.toggle_pause)
        self.accept("f", self.toggle_physics_debug)
        self.accept("m", self.toggle_mouse_look)
        # Drag to look around, wheel to zoom. Uses plain absolute mouse
        # coordinates rather than relative-mouse capture, which is far more
        # reliable across windowing setups.
        self.accept("mouse1", self._grab_mouse, [True])
        self.accept("mouse1-up", self._release_mouse)
        self.accept("mouse3", self._grab_mouse, [False])
        self.accept("mouse3-up", self._release_mouse)
        self.accept("wheel_up", self._zoom, [-1.0])
        self.accept("wheel_down", self._zoom, [1.0])
        # Inspector: get right up to a single unit and look it over.
        self.accept("i", self.toggle_inspect)
        self.accept("v", self.toggle_unit_view)
        self.accept("t", self.toggle_player_control)
        self.accept("tab", self.cycle_inspect, [1])
        self.accept("shift-tab", self.cycle_inspect, [-1])
        # Held keys rather than key repeat: repeat rates are jerky and differ
        # between systems, which makes camera movement feel broken.
        for key in (*MOVE_KEYS, *LOOK_KEYS, "shift", "-", "=", "+"):
            self.accept(key, self.keys.add, [key])
            self.accept(f"{key}-up", self.keys.discard, [key])

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def _start_battle(self, seed: int) -> None:
        self._release_player_control()
        if self.battle is not None:
            self.battle.cleanup()
        self.battle = Battle(
            self.render,
            self.world,
            self.terrain,
            self.assets,
            n_heli=self.args.n_heli,
            n_tanks=self.args.n_tanks,
            n_destroyers=self.args.n_destroyers,
            n_submarines=self.args.n_submarines,
            city_enabled=self.args.city,
            n_osprey=self.args.n_osprey,
            n_jets=self.args.n_jets,
            n_sam=self.args.n_sam,
            n_rifles=self.args.n_rifles,
            n_rockets=self.args.n_rockets,
            seed=seed,
            deploy_radius=self.args.deploy,
        )
        self.sim_time = 0.0
        self.focus_smooth = None

    def restart_battle(self) -> None:
        # NB: not named `restart` — ShowBase already defines that and calls it.
        self._restore_unit_view_parts()
        self._start_battle(self.args.seed + int(self.sim_time * 1000) + 1)
        self.inspect_unit = None
        if self.camera_mode in ("inspect", "unit"):
            self.camera_mode = "orbit"
            self.camLens.set_near(0.8)
            self._refresh_help()

    def cycle_camera(self) -> None:
        self._release_player_control()
        self._restore_unit_view_parts()
        if self.camera_mode in CAMERA_MODES:
            index = CAMERA_MODES.index(self.camera_mode)
            self.camera_mode = CAMERA_MODES[(index + 1) % len(CAMERA_MODES)]
        else:
            # The inspector is not part of the rotation, so cycling out of it
            # rejoins at the start rather than looking itself up and failing.
            self.camera_mode = CAMERA_MODES[0]
        self.inspect_unit = None
        self.camLens.set_near(0.8)
        self.chase_index += 1
        if self.camera_mode != "free" and self.mouse_look:
            self.toggle_mouse_look()
        self._refresh_help()

    # ------------------------------------------------------------------
    # Unit inspector
    # ------------------------------------------------------------------
    def _inspectable(self):
        """Units that still have a model in the scene, wrecks included."""
        if self.battle is None:
            return []
        return [u for u in self.battle.units if not u.np.is_empty()]

    def toggle_inspect(self) -> None:
        self._release_player_control()
        if self.camera_mode in ("inspect", "unit"):
            self._restore_unit_view_parts()
            self.camera_mode = "orbit"
            self.inspect_unit = None
            self.camLens.set_near(0.8)
        else:
            self.camera_mode = "inspect"
            self.inspect_angle = 35.0
            self.inspect_zoom = 1.0
            if self.inspect_unit is None:
                units = self._inspectable()
                self.inspect_unit = units[0] if units else None
        self._refresh_help()

    def toggle_unit_view(self) -> None:
        """See straight ahead from the currently selected living unit."""
        if self.camera_mode == "unit":
            self._release_player_control()
            self._restore_unit_view_parts()
            self.camera_mode = "inspect"
            self.camLens.set_near(0.8)
            self._refresh_help()
            return

        living = [unit for unit in self._inspectable() if unit.alive]
        if self.inspect_unit not in living:
            self.inspect_unit = living[0] if living else None
        if self.inspect_unit is None:
            return
        self.camera_mode = "unit"
        # A cockpit/head camera needs a short near plane or nearby parts of a
        # vehicle vanish abruptly. The normal value is restored on exit.
        self.camLens.set_near(0.16)
        self._refresh_help()

    def toggle_player_control(self) -> None:
        """Take or release a controllable unit from an inspection camera."""
        if self.player_control.active:
            self._release_player_control()
            self._refresh_help()
            return
        if self.camera_mode not in ("inspect", "unit"):
            return
        if not self.player_control.take(self.inspect_unit):
            return
        self.camera_mode = "unit"
        self.camLens.set_near(0.16)
        kind = self.inspect_unit.kind
        self.camLens.set_fov(78 if kind == "jet" else 60)
        self._hide_cockpits()
        cockpit = self.cockpits.get(kind)
        if cockpit is not None:
            self.crosshair_text.hide()
            cockpit.show()
        else:
            self.crosshair_text.setText("+")
            self.crosshair_text.show()
        self._refresh_help()

    def _release_player_control(self) -> bool:
        released = self.player_control.release()
        if hasattr(self, "crosshair_text"):
            self.crosshair_text.hide()
        self._hide_cockpits()
        self.camLens.set_fov(60)
        return released

    def _hide_cockpits(self) -> None:
        for cockpit in getattr(self, "cockpits", {}).values():
            cockpit.hide()

    def _restore_unit_view_parts(self) -> None:
        """Restore pieces hidden only to keep a first-person view unobstructed."""
        if self.unit_view_masked is not None:
            rotor = self.unit_view_masked.main_rotor
            if rotor is not None and not rotor.is_empty():
                rotor.show()
        self.unit_view_masked = None

    def cycle_inspect(self, step: int) -> None:
        """Step through every unit on the field, either team."""
        self._release_player_control()
        units = self._inspectable()
        if self.camera_mode == "unit":
            units = [unit for unit in units if unit.alive]
        if not units:
            self.inspect_unit = None
            return
        if self.camera_mode not in ("inspect", "unit"):
            self.camera_mode = "inspect"
            self.inspect_angle = 35.0
        try:
            index = units.index(self.inspect_unit)
        except ValueError:
            index = -1 if step > 0 else 0
        self.inspect_unit = units[(index + step) % len(units)]
        self._refresh_help()

    def _update_unit_camera(self) -> None:
        living = [unit for unit in self._inspectable() if unit.alive]
        if self.inspect_unit not in living:
            self.inspect_unit = living[0] if living else None
        if self.inspect_unit is None:
            self._restore_unit_view_parts()
            self.camera_mode = "orbit"
            self.camLens.set_near(0.8)
            self._refresh_help()
            return

        unit = self.inspect_unit
        if self.unit_view_masked is not unit:
            self._restore_unit_view_parts()
            # From the external nose position the full-size rotor disc can
            # cross the lens as a black slab. Other observers still see it;
            # it is hidden only while this helicopter owns the camera.
            if unit.kind == "helicopter" and not unit.main_rotor.is_empty():
                unit.main_rotor.hide()
                self.unit_view_masked = unit
        forward = Vec3(unit.forward)
        if forward.length_squared() < 1e-9:
            forward = Vec3(0, 1, 0)
        forward.normalize()
        forward_offset, eye_height = {
            "rifleman": (0.22, 0.72),
            "rocket": (0.22, 0.72),
            "tank": (0.75, 2.10),
            "sam": (2.85, 2.35),
            # Procedural aircraft use opaque canopy geometry. Put the camera
            # just ahead of the glazing rather than inside that dark shell.
            "jet": (7.65, 0.72),
            "helicopter": (4.78, 0.62),
            "osprey": (7.18, 0.42),
            # Naval views belong on the bridge, not at the centre of the hull.
            # These offsets are measured off the placeholders: the destroyer's
            # pilot-house glazing sits 11.2 m forward and 10.6 m up, and the
            # submarine's is on top of the sail. Held at the hull centre the
            # camera ended up buried inside the deckhouse looking at a wall.
            "destroyer": (12.2, 10.3),
            "submarine": (5.5, 6.6),
        }.get(unit.kind, (0.5, max(0.8, unit.spec.half_extents.z * 0.8)))
        eye = Point3(unit.position + forward * forward_offset + Vec3(0, 0, eye_height))
        view_direction = Vec3(forward)
        if unit.kind == "sam":
            # This is the optical/radar station, not the driver's slit: follow
            # the tracked aircraft. With no lock, scan slightly above horizon.
            tracked = unit.target
            if tracked is not None and getattr(tracked, "alive", False):
                view_direction = Vec3(tracked.position) - eye
            else:
                view_direction += Vec3(0, 0, 0.11)
            if view_direction.length_squared() > 1e-9:
                view_direction.normalize()
        elif unit.kind in ("destroyer", "submarine"):
            # From the bridge the sightline has to drop a little or the shot is
            # pure horizon: the foredeck, the gun and the launch cells all sit
            # well below eye level on a hull this long.
            view_direction.z = -0.20
            view_direction.normalize()
        elif unit.kind == "jet":
            # The rigid body is yaw-only for stability, but vertical velocity
            # is the aircraft's real climb angle. Reflect it in the pilot view
            # instead of leaving the horizon fixed while ascending.
            horizontal_speed = math.hypot(unit.velocity.x, unit.velocity.y)
            # A slight downward sightline keeps the horizon and terrain in the
            # narrow vertical FOV instead of showing only sky at cruise height.
            view_direction.z = (
                unit.velocity.z * 1.8 / max(30.0, horizontal_speed) - 0.10
            )
            view_direction.normalize()
        self.camera.set_pos(eye)
        self.camera.look_at(eye + view_direction * 120.0)
        if unit.kind == "jet":
            self.camera.set_r(unit.model_np.get_r() * 0.65)

    def _update_inspect_camera(self, dt: float) -> None:
        units = self._inspectable()
        if self.inspect_unit not in units:
            self.inspect_unit = units[0] if units else None
        if self.inspect_unit is None:
            # Nothing left to look at; hand the camera back to the battle.
            self.camera_mode = "orbit"
            self._refresh_help()
            return

        unit = self.inspect_unit
        focus = Point3(unit.position)
        # Frame the unit by its own size, so a soldier and a jet both fill it.
        span = unit.spec.model_length
        radius = max(4.0, span * 1.15) * self.inspect_zoom

        self.inspect_angle += dt * 16.0
        angle = math.radians(self.inspect_angle)
        position = Point3(
            focus.x + math.cos(angle) * radius,
            focus.y + math.sin(angle) * radius,
            focus.z + radius * 0.42,
        )
        position.z = max(position.z, self._ground_clearance(position.x, position.y) + 1.5)
        self.camera.set_pos(position)
        self.camera.look_at(focus)

    def _grab_mouse(self, fire_button: bool = False) -> None:
        if self.player_control.active:
            if fire_button:
                self.player_control.firing = True
            return
        self.dragging = True
        self._drag_from = None

    def _release_mouse(self) -> None:
        self.player_control.firing = False
        self.dragging = False
        self._drag_from = None

    def _zoom(self, direction: float) -> None:
        if self.camera_mode == "inspect":
            self.inspect_zoom = min(3.0, max(0.35, self.inspect_zoom + direction * 0.12))
            return
        if self.camera_mode == "free":
            # Dolly along the view axis.
            forward = self.camera.get_quat().get_forward()
            self.camera.set_pos(self.camera.get_pos() - forward * direction * 28.0)
        else:
            self.orbit_distance = min(2.6, max(0.28, self.orbit_distance + direction * 0.12))

    def _apply_mouse_drag(self) -> None:
        """Drag with a mouse button held to swing the camera around."""
        # mouseWatcherNode is absent when running without a window, so guard it
        # rather than relying on `dragging` never being set in that case.
        if (
            not self.dragging
            or self.mouse_look
            or self.mouseWatcherNode is None
            or not self.mouseWatcherNode.has_mouse()
        ):
            self._drag_from = None
            return

        pointer = self.mouseWatcherNode.get_mouse()
        current = (pointer.get_x(), pointer.get_y())
        if self._drag_from is not None:
            dx = current[0] - self._drag_from[0]
            dy = current[1] - self._drag_from[1]
            if self.camera_mode == "free":
                self.camera.set_h(self.camera.get_h() - dx * 110.0)
                self.camera.set_p(max(-88.0, min(88.0, self.camera.get_p() + dy * 110.0)))
            elif self.camera_mode == "inspect":
                self.inspect_angle -= dx * 220.0
                self.inspect_zoom = min(3.0, max(0.35, self.inspect_zoom - dy * 1.2))
            else:
                self.orbit_angle -= dx * 200.0
                self.orbit_height = min(1.4, max(0.12, self.orbit_height + dy * 1.3))
        self._drag_from = current

    def toggle_pause(self) -> None:
        self.paused = not self.paused

    def toggle_mouse_look(self) -> None:
        """Relative mouse mode is not available everywhere, so it is opt-in."""
        self.mouse_look = not self.mouse_look
        props = WindowProperties()
        props.set_cursor_hidden(self.mouse_look)
        props.set_mouse_mode(
            WindowProperties.M_relative if self.mouse_look else WindowProperties.M_absolute
        )
        try:
            self.win.request_properties(props)
        except Exception:  # noqa: BLE001
            self.mouse_look = False
        self._last_pointer = None

    def toggle_physics_debug(self) -> None:
        if self.debug_np is None:
            node = BulletDebugNode("debug")
            node.show_wireframe(True)
            node.show_bounding_boxes(False)
            self.debug_np = self.render.attach_new_node(node)
            self.world.set_debug_node(node)
        if self.debug_np.is_hidden():
            self.debug_np.show()
        else:
            self.debug_np.hide()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def _stream_anchors(self):
        """Points that need terrain under them: the camera and every unit.

        Anchoring on the units too is not optional — a unit that outruns the
        loaded ring would have no collider beneath it and drop out of the world.
        """
        anchors = [(self.camera.get_x(), self.camera.get_y())]
        if self.battle is not None:
            focus = self.battle.focus_point()
            anchors.append((focus.x, focus.y))
            for unit in self.battle.alive_units():
                anchors.append((unit.position.x, unit.position.y))
            if self.battle.city is not None:
                anchors.append(self.battle.city.origin)
        return anchors

    def _update(self, task):
        dt = min(self.clock.get_dt(), 0.05)
        if not self.paused:
            self.sim_time += dt
            had_control = self.player_control.active
            self.player_control.update(dt, self.keys, self.battle, self.terrain)
            if had_control and not self.player_control.active:
                # The controller releases itself when its unit dies. Clear
                # every view-owned element as well, or the next inspected unit
                # inherits the dead jet's HUD and target cue.
                self._release_player_control()
                self._refresh_help()
            elif self.player_control.active:
                locked = self.player_control.locked_target is not None
                unit = self.player_control.unit
                if unit.kind != "jet":
                    colour = (
                        (0.32, 1.0, 0.38, 1.0)
                        if locked
                        else (0.94, 0.96, 0.88, 0.92)
                    )
                    self.crosshair_text.setFg(colour)
            self.battle.step(dt)
            # Damage and missile impacts happen inside Battle.step. Validate a
            # second time in the same frame so a destroyed aircraft cannot
            # lend its cockpit HUD to the next unit for even one rendered frame.
            self._sync_player_control_view()
            # Fixed-step the solver so behaviour does not depend on framerate.
            self._physics_debt += dt
            while self._physics_debt >= PHYSICS_STEP:
                self.world.do_physics(PHYSICS_STEP, 0)
                self._physics_debt -= PHYSICS_STEP

        self._update_camera(dt)
        self._update_cockpit_hud(dt)
        self.chunks.update(self._stream_anchors())
        self._follow_world()

        self._write_report_once()
        suffix = "   ·   PAUSA" if self.paused else ""
        if self.camera_mode in ("inspect", "unit") and self.inspect_unit is not None:
            controlled = ""
            if self.player_control.active:
                controlled = " — CONTROL MANUAL"
            self.status_text.setText(self._inspect_label() + controlled + suffix)
        else:
            self.status_text.setText(self.battle.status_text() + suffix)
        self._update_roster()
        return task.cont

    def _sync_player_control_view(self) -> None:
        """Keep controller ownership and all view-only state atomic."""
        if not self.player_control.active:
            return
        if self.player_control.validate():
            return
        self._release_player_control()
        self.dragging = False
        self._drag_from = None
        self._refresh_help()

    def _update_cockpit_hud(self, dt: float) -> None:
        """Feed flight and targeting state to whichever cockpit is in use."""
        if not self.player_control.active:
            return
        unit = self.player_control.unit
        cockpit = self.cockpits.get(unit.kind)
        if cockpit is None:
            return
        ground = max(
            self.terrain.height_at(unit.position.x, unit.position.y),
            self.terrain.water_level,
        )
        altitude = max(0.0, unit.position.z - ground)
        cockpit.update(
            dt,
            unit,
            self.player_control.locked_target,
            self.player_control.throttle,
            altitude,
            self.camera,
            self.render,
            self.camLens,
            self.get_aspect_ratio(),
            self.player_control.shot_fired,
        )

    def _write_report_once(self) -> None:
        """Dump the battle report the moment the battle is decided."""
        if self._report_written or self.battle is None or self.battle.winner is None:
            return
        self._report_written = True
        try:
            txt_path, json_path = self.battle.stats.write(self.args.stats_dir)
            print(f"[informe] TXT  guardado en {txt_path}")
            print(f"[informe] JSON guardado en {json_path}")
        except OSError as exc:
            print(f"[informe] no se pudo escribir el informe: {exc}")

    def _submarine_status(self) -> str:
        """Countdown to the next cruise-missile salvo, per team."""
        parts = []
        for team in (0, 1):
            boats = [
                u for u in self.battle.units
                if u.alive and u.team == team and u.kind == "submarine"
            ]
            if not boats:
                continue
            soonest = min(boats, key=lambda b: b.strategic_cooldown)
            if soonest.pending_salvo:
                state = "EMERGIENDO"
            else:
                state = f"{max(0.0, soonest.strategic_cooldown):.0f}s"
            parts.append(f"{TEAM_NAMES[team]} {state}")
        return "   crucero: " + "  |  ".join(parts) if parts else ""

    def _update_roster(self) -> None:
        if (
            self.player_control.active
            and self.player_control.unit.kind == "jet"
        ):
            for text in self.roster_text.values():
                text.setText("")
            self.civil_text.setText("")
            return
        for team, text in self.roster_text.items():
            counts = self.battle.roster(team)
            parts = [f"{label} {counts.get(kind, 0)}" for kind, label in ROSTER]
            text.setText(f"{TEAM_NAMES[team]:5} " + "   ".join(parts))
        city = self.battle.city
        if city is None:
            self.civil_text.setText("")
        else:
            people_lost = city.initial_civilians - city.civilians_alive
            cars_lost = city.initial_cars - city.cars_alive
            buildings_lost = city.initial_buildings - city.buildings_alive
            self.civil_text.setFg(TEAM_COLORS[city.defending_team])
            self.civil_text.setText(
                f"Civil {TEAM_NAMES[city.defending_team]}   "
                f"personas {city.civilians_alive}/{city.initial_civilians} "
                f"(bajas {people_lost})   "
                f"coches {city.cars_alive}/{city.initial_cars} "
                f"(destruidos {cars_lost})   "
                f"edificios {city.buildings_alive}/{city.initial_buildings} "
                f"(caídos {buildings_lost})"
            )
        if self.status_text is not None and self.camera_mode not in ("inspect", "unit"):
            self.status_text.setText(self.status_text.getText() + self._submarine_status())

    def _follow_world(self) -> None:
        """Keep the camera-relative scenery (water, shadow frustum) with us."""
        camera = self.camera.get_pos()
        self.water.set_pos(camera.x, camera.y, self.terrain.water_level)

        target = self.focus_smooth or camera
        self.sun_np.set_pos(target.x, target.y, self.terrain.relief + 220.0)

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------
    def _update_camera(self, dt: float) -> None:
        self._apply_mouse_drag()
        if self.camera_mode == "free":
            self._update_free_camera(dt)
            return
        if self.camera_mode == "inspect":
            self._update_inspect_camera(dt)
            return
        if self.camera_mode == "unit":
            self._update_unit_camera()
            return

        # focus_point() jumps whenever the closest enemy pair changes; ease
        # into it so the rig pans instead of snapping across the field.
        target_focus = Point3(self.battle.focus_point())
        if self.focus_smooth is None:
            self.focus_smooth = target_focus
        else:
            self.focus_smooth = Point3(
                self.focus_smooth + (target_focus - self.focus_smooth) * min(1.0, 2.0 * dt)
            )
        focus = self.focus_smooth

        self._apply_orbit_keys(dt)

        if self.camera_mode == "chase":
            alive = self.battle.alive_units()
            if alive:
                unit = alive[self.chase_index % len(alive)]
                focus = Point3(unit.position)
                behind = unit.np.get_quat().get_forward() * -26.0
                desired = focus + behind + Vec3(0, 0, 11.0)
                desired.z = max(desired.z, self._ground_clearance(desired.x, desired.y) + 4.0)
                blend = min(1.0, 4.5 * dt)
                self.camera.set_pos(
                    self.camera.get_pos() + (desired - self.camera.get_pos()) * blend
                )
                self.camera.look_at(focus)
                return

        if self.camera_mode == "high":
            radius = 260.0
            angle = math.radians(-90.0)
            pitch = math.radians(55.0)
        else:
            self.orbit_angle += dt * 5.0
            reach = max(200.0, self.battle.focus_spread * 1.5)
            radius = min(reach, 520.0) * self.orbit_distance
            angle = math.radians(self.orbit_angle)
            pitch = math.radians(12.0 + 34.0 * self.orbit_height)

        # Placed exactly, not eased. Interpolating the position was a bug once:
        # the rig chased a moving target so its real distance and pitch never
        # matched the intended ones, and the shot collapsed into a top-down.
        position = Point3(
            focus.x + math.cos(angle) * radius,
            focus.y + math.sin(angle) * radius,
            focus.z + radius * math.tan(pitch),
        )
        position.z = max(position.z, self._ground_clearance(position.x, position.y) + 8.0)

        self.camera.set_pos(position)
        self.camera.look_at(focus)

    def _ground_clearance(self, x: float, y: float) -> float:
        """Never dip below the terrain, nor below the water surface."""
        return max(self.terrain.height_at(x, y), self.terrain.water_level)

    def _apply_orbit_keys(self, dt: float) -> None:
        if "arrow_left" in self.keys:
            self.orbit_angle -= 45.0 * dt
        if "arrow_right" in self.keys:
            self.orbit_angle += 45.0 * dt
        if "arrow_up" in self.keys:
            self.orbit_height = min(1.4, self.orbit_height + 0.5 * dt)
        if "arrow_down" in self.keys:
            self.orbit_height = max(0.12, self.orbit_height - 0.5 * dt)
        if "-" in self.keys:
            self.orbit_distance = min(2.6, self.orbit_distance + 0.7 * dt)
        if "=" in self.keys or "+" in self.keys:
            self.orbit_distance = max(0.28, self.orbit_distance - 0.7 * dt)

    def _update_free_camera(self, dt: float) -> None:
        heading = self.camera.get_h()
        pitch = self.camera.get_p()

        # Arrow keys always work; the mouse is a bonus, because relative mouse
        # mode is not reliable on every windowing setup (WSL in particular).
        look = 90.0 * dt
        if "arrow_left" in self.keys:
            heading += look
        if "arrow_right" in self.keys:
            heading -= look
        if "arrow_up" in self.keys:
            pitch += look
        if "arrow_down" in self.keys:
            pitch -= look

        if self.mouse_look and self.mouseWatcherNode is not None and self.mouseWatcherNode.has_mouse():
            pointer = self.win.get_pointer(0)
            current = (pointer.get_x(), pointer.get_y())
            if self._last_pointer is not None:
                heading -= (current[0] - self._last_pointer[0]) * 0.16
                pitch -= (current[1] - self._last_pointer[1]) * 0.16
            self._last_pointer = current

        self.camera.set_hpr(heading, max(-88.0, min(88.0, pitch)), 0)

        speed = 220.0 if "shift" in self.keys else 70.0
        quat = self.camera.get_quat()
        move = Vec3(0, 0, 0)
        if "w" in self.keys:
            move += quat.get_forward()
        if "s" in self.keys:
            move -= quat.get_forward()
        if "d" in self.keys:
            move += quat.get_right()
        if "a" in self.keys:
            move -= quat.get_right()
        if "e" in self.keys:
            move += Vec3(0, 0, 1)
        if "q" in self.keys:
            move -= Vec3(0, 0, 1)
        if move.length_squared() > 1e-6:
            move.normalize()
            self.camera.set_pos(self.camera.get_pos() + move * speed * dt)

        position = self.camera.get_pos()
        floor = self._ground_clearance(position.x, position.y) + 3.0
        if position.z < floor:
            self.camera.set_z(floor)
