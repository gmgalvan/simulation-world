"""Procedural defended city, destructible buildings and civilian evacuation."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from panda3d.bullet import BulletBoxShape, BulletRigidBodyNode
from panda3d.core import (
    Geom,
    GeomNode,
    GeomTriangles,
    GeomVertexData,
    GeomVertexFormat,
    GeomVertexWriter,
    NodePath,
    Point3,
    TransformState,
    Vec3,
)

from .assets import make_box, make_loft

CITY_EXTENT = 62.0
BUILDING_COUNT = 8
CIVILIAN_COUNT = 18
CAR_COUNT = 6
TEAM_COLORS = {
    0: (0.92, 0.26, 0.24, 1.0),
    1: (0.24, 0.52, 0.92, 1.0),
}


@dataclass(eq=False)
class CityBuilding:
    id: int
    np: NodePath
    node: BulletRigidBodyNode
    model: NodePath
    position: Point3
    size: Vec3
    team: int
    max_health: float
    health: float
    alive: bool = True

    @property
    def velocity(self) -> Vec3:
        return Vec3(0, 0, 0)

    @property
    def city_structure(self) -> bool:
        return True

    @property
    def city_asset(self) -> bool:
        return True

    @property
    def kind(self) -> str:
        return "building"

    @property
    def critical(self) -> bool:
        return self.alive and self.health <= self.max_health * 0.35


@dataclass(eq=False)
class Civilian:
    id: int
    np: NodePath
    node: BulletRigidBodyNode
    model: NodePath
    team: int
    alive: bool
    sheltered: bool
    in_world: bool
    home: CityBuilding | None
    target: CityBuilding | None
    speed: float
    health: float = 18.0
    gait_phase: float = 0.0

    @property
    def position(self) -> Point3:
        return self.np.get_pos()

    @property
    def velocity(self) -> Vec3:
        return Vec3(0, 0, 0)

    @property
    def kind(self) -> str:
        return "civilian"

    @property
    def city_asset(self) -> bool:
        return True


@dataclass(eq=False)
class CivilianCar:
    id: int
    np: NodePath
    node: BulletRigidBodyNode
    model: NodePath
    wheels: list[NodePath]
    team: int
    progress: float
    speed: float
    direction: float
    health: float = 55.0
    alive: bool = True
    motion: Vec3 = field(default_factory=Vec3)

    @property
    def position(self) -> Point3:
        return self.np.get_pos()

    @property
    def velocity(self) -> Vec3:
        return Vec3(self.motion)

    @property
    def kind(self) -> str:
        return "civilian_car"

    @property
    def city_asset(self) -> bool:
        return True


class City:
    """Small city whose survival is an objective for one randomly chosen team."""

    def __init__(
        self,
        parent: NodePath,
        world,
        terrain,
        origin: tuple[float, float],
        defending_team: int,
        seed: int,
    ) -> None:
        self.world = world
        self.terrain = terrain
        self.origin = origin
        self.defending_team = defending_team
        self.rng = random.Random(seed + 44_921)
        self.root = parent.attach_new_node("defended-city")
        self.buildings: list[CityBuilding] = []
        self.civilians: list[Civilian] = []
        self.cars: list[CivilianCar] = []
        self.alert_time = 0.0
        self.initial_buildings = BUILDING_COUNT
        self.initial_civilians = CIVILIAN_COUNT
        self.initial_cars = CAR_COUNT
        self._build_roads()
        self._build_buildings()
        self._spawn_civilians()
        self._spawn_cars()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _ground(self, x: float, y: float) -> float:
        return max(self.terrain.height_at(x, y), self.terrain.water_level)

    def _terrain_patch(
        self,
        name: str,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
        color: tuple[float, float, float, float],
        lift: float = 0.10,
        step: float = 3.0,
    ) -> NodePath:
        """Make a continuous surface draped over the procedural terrain."""
        x_cells = max(1, math.ceil((x_max - x_min) / step))
        y_cells = max(1, math.ceil((y_max - y_min) / step))
        fmt = GeomVertexFormat.get_v3n3c4()
        vdata = GeomVertexData(name, fmt, Geom.UH_static)
        vertex = GeomVertexWriter(vdata, "vertex")
        normal = GeomVertexWriter(vdata, "normal")
        colours = GeomVertexWriter(vdata, "color")
        triangles = GeomTriangles(Geom.UH_static)

        for yi in range(y_cells + 1):
            y = y_min + (y_max - y_min) * yi / y_cells
            for xi in range(x_cells + 1):
                x = x_min + (x_max - x_min) * xi / x_cells
                vertex.add_data3(x, y, self._ground(x, y) + lift)
                surface_normal = self.terrain.normal_at(x, y)
                normal.add_data3(surface_normal)
                colours.add_data4(*color)

        row = x_cells + 1
        for yi in range(y_cells):
            for xi in range(x_cells):
                a = yi * row + xi
                b = a + 1
                c = a + row
                d = c + 1
                triangles.add_vertices(a, b, d)
                triangles.add_vertices(a, d, c)
        triangles.close_primitive()

        geom = Geom(vdata)
        geom.add_primitive(triangles)
        node = GeomNode(name)
        node.add_geom(geom)
        patch = self.root.attach_new_node(node)
        patch.set_two_sided(True)
        return patch

    def _build_roads(self) -> None:
        ox, oy = self.origin
        asphalt = (0.16, 0.17, 0.18, 1.0)
        markings = (0.80, 0.72, 0.28, 1.0)
        pavement = (0.43, 0.44, 0.43, 1.0)

        # Streets run between the building lots, not through their centres.
        # Each one is a single terrain-following mesh: no buried box ends and
        # no cracks between tiles on a hillside.
        streets = (-57.0, -19.0, 19.0, 57.0)
        road_half_width = 5.8
        extent = 66.0
        for index, fixed in enumerate(streets):
            self._terrain_patch(
                f"road-h-{index}", ox - extent, ox + extent,
                oy + fixed - road_half_width, oy + fixed + road_half_width,
                asphalt, lift=0.11,
            )
            self._terrain_patch(
                f"road-v-{index}", ox + fixed - road_half_width,
                ox + fixed + road_half_width, oy - extent, oy + extent,
                asphalt, lift=0.115,
            )

        # Dashed centre lines stop before crossings, like actual urban roads.
        for fixed in streets:
            for dash in range(-61, 62, 8):
                if any(abs(dash - crossing) < 7.0 for crossing in streets):
                    continue
                self._terrain_patch(
                    "road-mark-h", ox + dash - 2.2, ox + dash + 2.2,
                    oy + fixed - 0.10, oy + fixed + 0.10,
                    markings, lift=0.15, step=2.2,
                )
                self._terrain_patch(
                    "road-mark-v", ox + fixed - 0.10, ox + fixed + 0.10,
                    oy + dash - 2.2, oy + dash + 2.2,
                    markings, lift=0.155, step=2.2,
                )

        # One draped pavement slab per occupied block creates continuous curbs
        # without the large floating grey squares seen on sloped terrain.
        lots = (
            (-38, -38), (0, -38), (38, -38), (-38, 0), (38, 0),
            (-38, 38), (0, 38), (38, 38),
        )
        for index, (dx, dy) in enumerate(lots):
            self._terrain_patch(
                f"sidewalk-{index}", ox + dx - 14.0, ox + dx + 14.0,
                oy + dy - 14.0, oy + dy + 14.0,
                pavement, lift=0.14,
            )

        # Team-coloured sign in the central square.
        team_color = TEAM_COLORS[self.defending_team]
        self._terrain_patch(
            "central-plaza", ox - 14.0, ox + 14.0, oy - 14.0, oy + 14.0,
            (0.55, 0.53, 0.48, 1.0), lift=0.15,
        )
        for sx, sy in ((-5, -5), (5, -5), (-5, 5), (5, 5)):
            post = make_box((0.22, 0.22, 3.5), (0.18, 0.19, 0.20, 1.0))
            post.reparent_to(self.root)
            post.set_pos(ox + sx, oy + sy, self._ground(ox + sx, oy + sy) + 1.75)
            lamp = make_box((0.55, 0.55, 0.35), team_color)
            lamp.reparent_to(self.root)
            lamp.set_pos(ox + sx, oy + sy, self._ground(ox + sx, oy + sy) + 3.55)

    @staticmethod
    def _chamfer_ring(
        z: float, half_x: float, half_y: float, chamfer: float,
        offset_x: float = 0.0, slope: float = 0.0,
    ) -> list[tuple[float, float, float]]:
        points = (
            (-half_x + chamfer, -half_y), (half_x - chamfer, -half_y),
            (half_x, -half_y + chamfer), (half_x, half_y - chamfer),
            (half_x - chamfer, half_y), (-half_x + chamfer, half_y),
            (-half_x, half_y - chamfer), (-half_x, -half_y + chamfer),
        )
        return [
            (x + offset_x, y, z + slope * x / max(half_x, 0.1))
            for x, y in points
        ]

    def _modern_tower(
        self, model: NodePath, width: float, depth: float, height: float,
        facade, team_color,
    ) -> None:
        bottom, top = -height * 0.5, height * 0.5
        brick = tuple(max(0.16, channel * 0.57) for channel in facade[:3]) + (1.0,)
        stone = (0.59, 0.59, 0.56, 1.0)
        roof = (0.13, 0.16, 0.18, 1.0)
        glass = (0.12, 0.20, 0.23, 1.0)
        frame = (0.055, 0.070, 0.075, 1.0)

        # Straight dark-brick body, like a renovated city office/loft block.
        make_loft(
            [
                self._chamfer_ring(bottom, width * 0.46, depth * 0.46, 1.25),
                self._chamfer_ring(top, width * 0.46, depth * 0.46, 1.25),
            ],
            brick,
        ).reparent_to(model)
        # Pale stone ground floor and roof cornice frame the darker masonry.
        make_loft(
            [
                self._chamfer_ring(bottom, width * 0.475, depth * 0.475, 1.2),
                self._chamfer_ring(bottom + 3.15, width * 0.475, depth * 0.475, 1.2),
            ],
            stone,
        ).reparent_to(model)
        make_loft(
            [
                self._chamfer_ring(top - 0.45, width * 0.475, depth * 0.475, 1.2),
                self._chamfer_ring(top + 0.12, width * 0.475, depth * 0.475, 1.2),
            ],
            stone,
        ).reparent_to(model)

        window_height = min(1.65, height * 0.055)
        for floor_z in range(int(bottom + 4), int(top - 2), 3):
            for side in (-1.0, 1.0):
                for column in (-0.30, -0.10, 0.10, 0.30):
                    # Each opening has its own attached cross-frame; there are
                    # no storey-wide bars capable of protruding at the corner.
                    front_y = side * depth * 0.468
                    side_x = side * width * 0.468
                    window_width = width * 0.135
                    make_box(
                        (window_width, 0.13, window_height), glass,
                        (column * width, front_y, floor_z),
                    ).reparent_to(model)
                    make_box(
                        (0.035, 0.15, window_height * 0.94), frame,
                        (column * width, front_y - side * 0.012, floor_z),
                    ).reparent_to(model)
                    make_box(
                        (window_width * 0.94, 0.15, 0.035), frame,
                        (column * width, front_y - side * 0.012, floor_z),
                    ).reparent_to(model)
                    make_box(
                        (0.13, depth * 0.135, window_height), glass,
                        (side_x, column * depth, floor_z),
                    ).reparent_to(model)
                    make_box(
                        (0.15, 0.035, window_height * 0.94), frame,
                        (side_x - side * 0.012, column * depth, floor_z),
                    ).reparent_to(model)
                    make_box(
                        (0.15, depth * 0.127, 0.035), frame,
                        (side_x - side * 0.012, column * depth, floor_z),
                    ).reparent_to(model)

        # Recessed glazed entrance and a dark steel canopy with a restrained
        # team-colour strip along its front edge.
        make_box(
            (width * 0.34, 0.16, 2.35), glass,
            (0, -depth * 0.495, bottom + 1.25),
        ).reparent_to(model)
        make_box(
            (width * 0.48, 1.35, 0.20), roof,
            (0, -depth * 0.515, bottom + 2.55),
        ).reparent_to(model)
        make_box(
            (width * 0.46, 0.09, 0.10), team_color,
            (0, -depth * 0.548, bottom + 2.46),
        ).reparent_to(model)

        # Flat roof with a continuous parapet, centred service room, vents and
        # HVAC boxes. Every part sits within the roof perimeter.
        parapet_z = top + 0.42
        for side in (-1.0, 1.0):
            make_box(
                (width * 0.84, 0.28, 0.72), roof,
                (0, side * depth * 0.405, parapet_z),
            ).reparent_to(model)
            make_box(
                (0.28, depth * 0.84, 0.72), roof,
                (side * width * 0.405, 0, parapet_z),
            ).reparent_to(model)
        service_z = top + 1.25
        make_box(
            (width * 0.34, depth * 0.31, 2.25), (0.48, 0.48, 0.45, 1.0),
            (0, depth * 0.06, service_z),
        ).reparent_to(model)
        for x in (-width * 0.24, width * 0.24):
            make_box(
                (1.65, 1.20, 0.78), (0.38, 0.40, 0.39, 1.0),
                (x, -depth * 0.16, top + 0.58),
            ).reparent_to(model)
        self._add_flagpole(model, top + 2.38, height * 0.17, team_color)

    def _add_flagpole(
        self, model: NodePath, base_z: float, pole_height: float, team_color,
    ) -> None:
        """Centred metal mast with a solid team flag attached at one edge."""
        self._limb(
            Vec3(0, 0, base_z), Vec3(0, 0, base_z + pole_height),
            0.10, (0.24, 0.27, 0.28, 1.0),
        ).reparent_to(model)
        flag_width = min(2.2, max(1.25, pole_height * 0.40))
        flag_height = min(1.15, max(0.72, pole_height * 0.21))
        flag_z = base_z + pole_height * 0.76
        make_box(
            (flag_width, 0.08, flag_height), team_color,
            (flag_width * 0.5, 0, flag_z),
        ).reparent_to(model)
        # Small pale canton gives the flag readable detail without textures.
        make_box(
            (flag_width * 0.24, 0.085, flag_height * 0.42),
            (0.88, 0.88, 0.82, 1.0),
            (flag_width * 0.15, -0.005, flag_z + flag_height * 0.17),
        ).reparent_to(model)

    def _deco_tower(
        self, model: NodePath, width: float, depth: float, height: float,
        facade, team_color,
    ) -> None:
        bottom, top = -height * 0.5, height * 0.5
        rings = [
            self._chamfer_ring(bottom, width * 0.49, depth * 0.48, 0.9),
            self._chamfer_ring(bottom + height * 0.18, width * 0.49, depth * 0.48, 0.9),
            self._chamfer_ring(bottom + height * 0.20, width * 0.40, depth * 0.40, 0.7),
            self._chamfer_ring(bottom + height * 0.66, width * 0.40, depth * 0.40, 0.7),
            self._chamfer_ring(bottom + height * 0.68, width * 0.28, depth * 0.30, 0.5),
            self._chamfer_ring(bottom + height * 0.88, width * 0.28, depth * 0.30, 0.5),
            self._chamfer_ring(bottom + height * 0.90, width * 0.15, depth * 0.17, 0.3),
            self._chamfer_ring(top, width * 0.15, depth * 0.17, 0.3),
        ]
        make_loft(rings, facade).reparent_to(model)
        window = (0.17, 0.25, 0.29, 1.0)
        columns = (-0.27, -0.09, 0.09, 0.27)
        for floor_z in range(int(bottom + 3), int(bottom + height * 0.65), 3):
            lower_storey = floor_z < bottom + height * 0.20
            face_x = width * (0.492 if lower_storey else 0.405)
            face_y = depth * (0.482 if lower_storey else 0.405)
            for side in (-1.0, 1.0):
                for column in columns:
                    x = column * width
                    make_box(
                        (width * 0.075, 0.15, 1.45), window,
                        (x, side * face_y, floor_z),
                    ).reparent_to(model)
                    make_box(
                        (0.15, depth * 0.075, 1.45), window,
                        (side * face_x, column * depth, floor_z),
                    ).reparent_to(model)
        # Projecting cornices make each setback read as a deliberate storey.
        for z, scale in (
            (bottom + height * 0.19, 0.48),
            (bottom + height * 0.67, 0.40),
            (bottom + height * 0.89, 0.28),
        ):
            make_box(
                (width * scale * 2.0, depth * scale * 2.0, 0.34),
                facade, (0, 0, z),
            ).reparent_to(model)
        make_box(
            (width * 0.32, 0.24, 0.32), team_color,
            (width * 0.22, -depth * 0.405, bottom + height * 0.23),
        ).reparent_to(model)
        self._add_flagpole(model, top, height * 0.25, team_color)

    def _civic_building(
        self, model: NodePath, width: float, depth: float, height: float,
        facade, team_color,
    ) -> None:
        bottom = -height * 0.5
        cornice = bottom + height * 0.58
        stone = tuple(min(0.92, channel * 1.18) for channel in facade[:3]) + (1.0,)
        make_loft(
            [
                self._chamfer_ring(bottom, width * 0.49, depth * 0.47, 1.4),
                self._chamfer_ring(cornice, width * 0.49, depth * 0.47, 1.4),
            ],
            stone,
        ).reparent_to(model)
        # Cornices, central portico and an arched-looking faceted dome.
        make_box((width, depth * 0.98, 0.55), facade, (0, 0, cornice)).reparent_to(model)
        portico_y = -depth * 0.50
        for x in (-width * 0.23, -width * 0.08, width * 0.08, width * 0.23):
            self._limb(
                Vec3(x, portico_y, bottom + 0.30),
                Vec3(x, portico_y, cornice + 0.25),
                0.24, (0.80, 0.79, 0.74, 1.0),
            ).reparent_to(model)
        # Solid triangular pediment over the entrance.
        pediment = []
        for y in (portico_y - 0.15, portico_y + 0.22):
            pediment.append([
                (-width * 0.34, y, cornice + 0.30),
                (width * 0.34, y, cornice + 0.30),
                (0, y, cornice + 2.0),
            ])
        make_loft(pediment, facade).reparent_to(model)
        make_box(
            (width * 0.12, 0.18, height * 0.28), (0.09, 0.13, 0.15, 1.0),
            (0, portico_y - 0.03, bottom + height * 0.22),
        ).reparent_to(model)
        # Broad entrance steps and a low roof parapet add believable scale.
        for step, (step_width, step_depth) in enumerate(((0.48, 2.7), (0.42, 2.0), (0.36, 1.3))):
            make_box(
                (width * step_width, step_depth, 0.18), (0.69, 0.68, 0.64, 1.0),
                (0, portico_y - 0.65 - step * 0.42, bottom + 0.09 + step * 0.17),
            ).reparent_to(model)
        parapet_z = cornice + 0.58
        for side in (-1.0, 1.0):
            make_box(
                (width * 0.88, 0.24, 0.62), facade,
                (0, side * depth * 0.42, parapet_z),
            ).reparent_to(model)
            make_box(
                (0.24, depth * 0.84, 0.62), facade,
                (side * width * 0.44, 0, parapet_z),
            ).reparent_to(model)

        # Opaque drum and convex dome: no intersecting sphere or hollow centre.
        drum_bottom = cornice + 0.25
        drum_top = drum_bottom + height * 0.12
        dome_rx, dome_ry = width * 0.245, depth * 0.245
        drum = make_loft(
            [
                list(reversed(self._ellipse_ring(Vec3(0, 0, drum_bottom), dome_rx, dome_ry, 12))),
                list(reversed(self._ellipse_ring(Vec3(0, 0, drum_top), dome_rx, dome_ry, 12))),
            ],
            (0.74, 0.74, 0.70, 1.0),
        )
        drum.set_two_sided(True)
        drum.reparent_to(model)
        dome_color = (0.55, 0.39, 0.17, 1.0)
        dome_height = height * 0.25
        dome_rings = []
        for fraction, radius in ((0.0, 1.0), (0.30, 0.94), (0.58, 0.75), (0.82, 0.45), (1.0, 0.08)):
            dome_rings.append(
                list(reversed(
                    self._ellipse_ring(
                        Vec3(0, 0, drum_top + dome_height * fraction),
                        dome_rx * radius, dome_ry * radius, 12,
                    )
                ))
            )
        dome = make_loft(dome_rings, dome_color)
        dome.set_two_sided(True)
        dome.reparent_to(model)
        dome_top = drum_top + dome_height
        self._add_flagpole(model, dome_top, height * 0.20, team_color)
        window = (0.10, 0.18, 0.22, 1.0)
        for side in (-1.0, 1.0):
            for column in (-0.37, -0.19, 0.19, 0.37):
                make_box(
                    (width * 0.09, 0.14, height * 0.24), window,
                    (column * width, side * depth * 0.475, bottom + height * 0.34),
                ).reparent_to(model)

    def _residential_building(
        self, model: NodePath, width: float, depth: float, height: float,
        facade, team_color,
    ) -> None:
        """Mixed-use mid-rise with individual windows and balconies."""
        bottom, top = -height * 0.5, height * 0.5
        make_loft(
            [
                self._chamfer_ring(bottom, width * 0.49, depth * 0.47, 1.0),
                self._chamfer_ring(top - 1.1, width * 0.47, depth * 0.45, 1.0),
                self._chamfer_ring(top, width * 0.43, depth * 0.41, 0.8),
            ],
            facade,
        ).reparent_to(model)
        window = (0.15, 0.23, 0.27, 1.0)
        floors = max(4, int(height / 3.0))
        floor_height = (height - 3.2) / floors
        for floor in range(floors):
            z = bottom + 2.3 + floor * floor_height
            for side in (-1.0, 1.0):
                for column in (-0.31, -0.10, 0.10, 0.31):
                    make_box(
                        (width * 0.12, 0.12, min(1.25, floor_height * 0.55)),
                        window, (column * width, side * depth * 0.468, z),
                    ).reparent_to(model)
                    make_box(
                        (0.12, depth * 0.12, min(1.25, floor_height * 0.55)),
                        window, (side * width * 0.468, column * depth, z),
                    ).reparent_to(model)
            if floor % 2 == 0:
                # Shallow French balcony: it reads as part of the facade and
                # cannot leave long floating rails beyond a building corner.
                balcony_y = -depth * 0.474
                make_box(
                    (width * 0.48, 0.18, 0.14), (0.65, 0.64, 0.60, 1.0),
                    (0, balcony_y, z - 0.78),
                ).reparent_to(model)
                for x in (-width * 0.22, 0, width * 0.22):
                    make_box(
                        (0.07, 0.10, 0.42), (0.28, 0.30, 0.30, 1.0),
                        (x, balcony_y - 0.04, z - 0.50),
                    ).reparent_to(model)
                make_box(
                    (width * 0.48, 0.10, 0.07), (0.28, 0.30, 0.30, 1.0),
                    (0, balcony_y - 0.04, z - 0.29),
                ).reparent_to(model)
        # Shopfront, entrance canopy and rooftop mechanical room.
        make_box(
            (width * 0.72, 0.16, 1.65), (0.10, 0.17, 0.20, 1.0),
            (0, -depth * 0.475, bottom + 1.15),
        ).reparent_to(model)
        make_box(
            (width * 0.42, 1.25, 0.16), team_color,
            (0, -depth * 0.51, bottom + 2.1),
        ).reparent_to(model)
        make_loft(
            [
                self._chamfer_ring(top, width * 0.20, depth * 0.20, 0.45),
                self._chamfer_ring(top + 1.6, width * 0.17, depth * 0.17, 0.35),
            ],
            (0.43, 0.44, 0.42, 1.0),
        ).reparent_to(model)
        # Small rooftop services: tank, vents and parapet rather than an empty slab.
        tank_bottom = top + 1.62
        tank = make_loft(
            [
                list(reversed(self._ellipse_ring(Vec3(0, 0, tank_bottom), 0.72, 0.72, 10))),
                list(reversed(self._ellipse_ring(Vec3(0, 0, tank_bottom + 1.15), 0.72, 0.72, 10))),
            ],
            (0.24, 0.27, 0.28, 1.0),
        )
        tank.set_two_sided(True)
        tank.reparent_to(model)
        for x in (-width * 0.27, width * 0.27):
            make_box(
                (1.25, 1.05, 0.75), (0.36, 0.38, 0.37, 1.0),
                (x, 0, top + 0.42),
            ).reparent_to(model)

    def _build_buildings(self) -> None:
        ox, oy = self.origin
        lots = [
            (-38, -38), (0, -38), (38, -38),
            (-38, 0), (38, 0),
            (-38, 38), (0, 38), (38, 38),
        ]
        team_color = TEAM_COLORS[self.defending_team]
        for index, (dx, dy) in enumerate(lots):
            style = index % 4
            width = self.rng.uniform(18.0, 24.0) if style != 2 else self.rng.uniform(22.0, 25.0)
            depth = self.rng.uniform(17.0, 23.0) if style != 2 else self.rng.uniform(20.0, 24.0)
            height = (
                self.rng.uniform(25.0, 39.0) if style == 0
                else self.rng.uniform(29.0, 45.0) if style == 1
                else self.rng.uniform(14.0, 19.0)
                if style == 2
                else self.rng.uniform(19.0, 28.0)
            )
            x, y = ox + dx, oy + dy
            ground = self._ground(x, y)
            position = Point3(x, y, ground + height * 0.5)
            size = Vec3(width, depth, height)

            node = BulletRigidBodyNode(f"city-building-{index}")
            node.add_shape(BulletBoxShape(size * 0.5))
            node.set_mass(0.0)
            building_np = self.root.attach_new_node(node)
            building_np.set_pos(position)

            model = building_np.attach_new_node(f"city-building-model-{index}")
            # Procedural loft caps can be seen from steep camera angles. Keep
            # both sides rendered so no facade ever reads as a transparent gap.
            model.set_two_sided(True)
            architecture_colors = (
                (0.46, 0.50, 0.51),
                (0.67, 0.62, 0.53),
                (0.76, 0.74, 0.68),
                (0.55, 0.47, 0.40),
            )
            base_color = architecture_colors[style]
            variation = self.rng.uniform(-0.035, 0.035)
            tint = 0.055
            facade = tuple(
                min(
                    0.90,
                    (base_color[channel] + variation) * (1.0 - tint)
                    + team_color[channel] * tint,
                )
                for channel in range(3)
            ) + (1.0,)
            if style == 0:
                self._modern_tower(model, width, depth, height, facade, team_color)
            elif style == 1:
                self._deco_tower(model, width, depth, height, facade, team_color)
            elif style == 2:
                self._civic_building(model, width, depth, height, facade, team_color)
            else:
                self._residential_building(model, width, depth, height, facade, team_color)
            if index % 2:
                model.set_h(90.0)

            health = 125.0 + height * 7.5
            building = CityBuilding(
                index, building_np, node, model, position, size,
                self.defending_team, health, health,
            )
            node.set_python_tag("city_building", building)
            node.set_python_tag("city_target", building)
            self.world.attach(node)
            self.buildings.append(building)

    @staticmethod
    def _ellipse_ring(
        centre: Vec3, radius_x: float, radius_y: float, segments: int = 8
    ) -> list[tuple[float, float, float]]:
        return [
            (
                centre.x + math.cos(math.tau * step / segments) * radius_x,
                centre.y + math.sin(math.tau * step / segments) * radius_y,
                centre.z,
            )
            for step in range(segments)
        ]

    def _ellipsoid(
        self,
        centre: Vec3,
        radii: Vec3,
        color: tuple[float, float, float, float],
    ) -> NodePath:
        rings = []
        for latitude in (-0.88, -0.48, 0.0, 0.48, 0.88):
            horizontal = math.sqrt(max(0.0, 1.0 - latitude * latitude))
            rings.append(
                self._ellipse_ring(
                    Vec3(centre.x, centre.y, centre.z + latitude * radii.z),
                    radii.x * horizontal,
                    radii.y * horizontal,
                )
            )
        return make_loft(rings, color)

    def _limb(
        self,
        start: Vec3,
        end: Vec3,
        radius: float,
        color: tuple[float, float, float, float],
    ) -> NodePath:
        """Tapered octagonal limb between two joints."""
        axis = end - start
        axis.normalize()
        reference = Vec3(0, 0, 1) if abs(axis.z) < 0.90 else Vec3(0, 1, 0)
        side = axis.cross(reference)
        side.normalize()
        up = axis.cross(side)
        rings = []
        for centre, scale in ((start, 1.0), (start * 0.35 + end * 0.65, 0.88), (end, 0.72)):
            rings.append(
                [
                    tuple(
                        centre
                        + side * (math.cos(math.tau * step / 8) * radius * scale)
                        + up * (math.sin(math.tau * step / 8) * radius * scale)
                    )
                    for step in range(8)
                ]
            )
        return make_loft(rings, color)

    def _make_person(
        self, index: int
    ) -> tuple[NodePath, BulletRigidBodyNode, NodePath]:
        node = BulletRigidBodyNode(f"civilian-{index}")
        node.add_shape(
            BulletBoxShape(Vec3(0.32, 0.27, 0.90)),
            TransformState.make_pos(Vec3(0, 0, 0.50)),
        )
        node.set_mass(0.0)
        node.set_kinematic(True)
        root = self.root.attach_new_node(node)
        model = root.attach_new_node(f"civilian-model-{index}")
        model.set_scale(self.rng.uniform(0.90, 1.02))
        team = TEAM_COLORS[self.defending_team]
        shade = 0.82 + self.rng.uniform(-0.08, 0.10)
        shirt = tuple(min(1.0, channel * shade) for channel in team[:3]) + (1.0,)
        skin_palette = (
            (0.88, 0.68, 0.52, 1.0),
            (0.70, 0.48, 0.33, 1.0),
            (0.48, 0.31, 0.22, 1.0),
            (0.94, 0.77, 0.62, 1.0),
        )
        hair_palette = (
            (0.10, 0.065, 0.045, 1.0),
            (0.24, 0.13, 0.07, 1.0),
            (0.055, 0.045, 0.04, 1.0),
            (0.48, 0.32, 0.12, 1.0),
        )
        trouser_palette = (
            (0.10, 0.13, 0.18, 1.0),
            (0.18, 0.22, 0.29, 1.0),
            (0.24, 0.19, 0.15, 1.0),
            (0.16, 0.16, 0.17, 1.0),
        )
        skin = skin_palette[index % len(skin_palette)]
        hair = hair_palette[(index * 3) % len(hair_palette)]
        trousers = trouser_palette[(index * 5) % len(trouser_palette)]
        female = index % 2 == 1

        # Anatomical torso silhouette: broader shoulders for one variant,
        # defined waist and hips for the other. Both remain stylised low-poly.
        if female:
            torso_sizes = ((0.25, 0.15, 0.43), (0.17, 0.13, 0.73), (0.24, 0.15, 1.08))
        else:
            torso_sizes = ((0.20, 0.14, 0.43), (0.22, 0.15, 0.73), (0.29, 0.17, 1.08))
        torso = make_loft(
            [self._ellipse_ring(Vec3(0, 0, z), rx, ry) for rx, ry, z in torso_sizes],
            shirt,
        )
        torso.reparent_to(model)
        # Collar, belt and occasional skirt/long coat prevent every outfit
        # from reading as the same coloured shirt.
        self._ellipsoid(
            Vec3(0, -0.135, 1.055), Vec3(0.11, 0.025, 0.055),
            (0.88, 0.88, 0.84, 1.0),
        ).reparent_to(model)
        make_box(
            (torso_sizes[0][0] * 1.65, 0.025, 0.045),
            (0.08, 0.08, 0.075, 1.0), (0, -torso_sizes[0][1], 0.48),
        ).reparent_to(model)
        if female and index % 4 == 1:
            skirt = make_loft(
                [
                    self._ellipse_ring(Vec3(0, 0, 0.37), 0.24, 0.15),
                    self._ellipse_ring(Vec3(0, 0, 0.70), 0.18, 0.13),
                ],
                tuple(channel * 0.78 for channel in shirt[:3]) + (1.0,),
            )
            skirt.reparent_to(model)

        # Legs have separate thigh/calf lines and feet instead of one cuboid.
        for side in (-1.0, 1.0):
            hip = Vec3(side * 0.13, 0, 0.45)
            knee = Vec3(side * 0.13, 0.005, 0.08)
            ankle = Vec3(side * 0.14, -0.01, -0.27)
            self._limb(hip, knee, 0.095, trousers).reparent_to(model)
            self._limb(knee, ankle, 0.083, trousers).reparent_to(model)
            self._ellipsoid(
                Vec3(side * 0.14, -0.07, -0.34), Vec3(0.11, 0.19, 0.08),
                (0.055, 0.06, 0.07, 1.0),
            ).reparent_to(model)

        # Sleeves, forearms and hands form a relaxed asymmetric stance.
        pose = -1.0 if index % 3 == 0 else 1.0
        for side in (-1.0, 1.0):
            shoulder = Vec3(side * torso_sizes[-1][0] * 0.92, 0, 1.00)
            elbow = Vec3(side * 0.32, pose * side * 0.025, 0.73)
            hand = Vec3(side * 0.30, -pose * 0.035, 0.48)
            self._limb(shoulder, elbow, 0.075, shirt).reparent_to(model)
            self._limb(elbow, hand, 0.062, skin).reparent_to(model)
            self._ellipsoid(hand, Vec3(0.072, 0.065, 0.09), skin).reparent_to(model)

        self._limb(Vec3(0, 0, 1.07), Vec3(0, 0, 1.20), 0.09, skin).reparent_to(model)
        if female:
            # Hair behind the head produces a clearly different silhouette.
            self._ellipsoid(
                Vec3(0, 0.050, 1.39), Vec3(0.195, 0.135, 0.29), hair
            ).reparent_to(model)
        head = self._ellipsoid(Vec3(0, -0.01, 1.42), Vec3(0.165, 0.145, 0.205), skin)
        head.reparent_to(model)
        self._ellipsoid(
            Vec3(0, 0.015, 1.575),
            Vec3(0.18 if female else 0.17, 0.145, 0.08 if female else 0.060),
            hair,
        ).reparent_to(model)
        # Eyes and nose make faces readable in close free-camera views.
        for side in (-1.0, 1.0):
            self._ellipsoid(
                Vec3(side * 0.055, -0.144, 1.455), Vec3(0.015, 0.009, 0.019),
                (0.035, 0.035, 0.03, 1.0),
            ).reparent_to(model)
        self._ellipsoid(
            Vec3(0, -0.154, 1.405), Vec3(0.019, 0.018, 0.035), skin
        ).reparent_to(model)
        self._ellipsoid(
            Vec3(0, -0.150, 1.357), Vec3(0.044, 0.009, 0.012),
            (0.32, 0.10, 0.09, 1.0),
        ).reparent_to(model)
        return root, node, model

    def _spawn_civilians(self) -> None:
        ox, oy = self.origin
        for index in range(CIVILIAN_COUNT):
            np, node, model = self._make_person(index)
            home = self.buildings[index % len(self.buildings)]
            sheltered = index < CIVILIAN_COUNT // 2
            if sheltered:
                np.set_pos(home.position)
                np.hide()
            else:
                x = ox + self.rng.uniform(-25.0, 25.0)
                y = oy + self.rng.uniform(-25.0, 25.0)
                np.set_pos(x, y, self._ground(x, y) + 0.4)
                self.world.attach(node)
            civilian = Civilian(
                index, np, node, model, self.defending_team, True, sheltered,
                not sheltered, home if sheltered else None, None,
                self.rng.uniform(2.4, 3.4),
            )
            node.set_python_tag("city_target", civilian)
            self.civilians.append(civilian)

    @staticmethod
    def _car_section(
        y: float, half_width: float, bottom: float, top: float
    ) -> list[tuple[float, float, float]]:
        bevel = min(0.16, half_width * 0.22)
        return [
            (-half_width + bevel, y, bottom),
            (half_width - bevel, y, bottom),
            (half_width, y, bottom + bevel),
            (half_width, y, top - bevel),
            (half_width - bevel, y, top),
            (-half_width + bevel, y, top),
            (-half_width, y, top - bevel),
            (-half_width, y, bottom + bevel),
        ]

    def _car_wheel(self, radius: float = 0.32) -> NodePath:
        wheel = NodePath("civilian-car-wheel")
        tyre_rings = []
        hub_rings = []
        for x in (-0.13, 0.13):
            tyre_rings.append([
                (x, math.cos(math.tau * step / 10) * radius,
                 math.sin(math.tau * step / 10) * radius)
                for step in range(10)
            ])
            hub_rings.append([
                (x * 1.05, math.cos(math.tau * step / 10) * radius * 0.46,
                 math.sin(math.tau * step / 10) * radius * 0.46)
                for step in range(10)
            ])
        make_loft(tyre_rings, (0.025, 0.028, 0.03, 1.0)).reparent_to(wheel)
        make_loft(hub_rings, (0.53, 0.55, 0.57, 1.0)).reparent_to(wheel)
        return wheel

    def _make_car_model(
        self, parent: NodePath, index: int, color
    ) -> tuple[NodePath, list[NodePath]]:
        model = parent.attach_new_node(f"civilian-car-model-{index}")
        style = index % 3
        roof = (1.28, 1.38, 1.48)[style]
        rear_top = (0.72, 1.03, 1.14)[style]
        rings = [
            self._car_section(-1.95, 0.62, 0.18, 0.60),
            self._car_section(-1.48, 0.84, 0.14, 0.82),
            self._car_section(-0.62, 0.86, 0.14, 0.88),
            self._car_section(-0.30, 0.70, 0.42, roof),
            self._car_section(0.78, 0.72, 0.42, roof - 0.03),
            self._car_section(1.25, 0.84, 0.16, rear_top),
            self._car_section(1.88, 0.66, 0.20, 0.66),
        ]
        make_loft(rings, color).reparent_to(model)

        glass = (0.075, 0.14, 0.18, 1.0)
        # Side windows are split by a central pillar; front and rear glass are
        # tilted to follow the cabin profile.
        for side in (-1.0, 1.0):
            for y in (0.0, 0.58):
                make_box(
                    (0.045, 0.47, 0.40 if style < 2 else 0.48), glass,
                    (side * 0.715, y, 0.94 if style < 2 else 1.02),
                ).reparent_to(model)
            mirror = self._ellipsoid(
                Vec3(side * 0.91, -0.48, 0.88), Vec3(0.13, 0.08, 0.07), color
            )
            mirror.reparent_to(model)
        windshield = make_box((1.26, 0.055, 0.48), glass)
        windshield.reparent_to(model)
        windshield.set_pos(0, -0.43, 1.01 if style < 2 else 1.10)
        windshield.set_p(-20.0)
        rear_glass = make_box((1.22, 0.055, 0.43), glass)
        rear_glass.reparent_to(model)
        rear_glass.set_pos(0, 0.92, 0.99 if style < 2 else 1.08)
        rear_glass.set_p(18.0)

        # Lamps, bumpers, handles and plates make front and rear recognizable.
        for side in (-1.0, 1.0):
            self._ellipsoid(
                Vec3(side * 0.52, -1.90, 0.58), Vec3(0.20, 0.055, 0.11),
                (1.0, 0.90, 0.58, 1.0),
            ).reparent_to(model)
            self._ellipsoid(
                Vec3(side * 0.54, 1.84, 0.55), Vec3(0.18, 0.055, 0.10),
                (0.76, 0.04, 0.025, 1.0),
            ).reparent_to(model)
            make_box(
                (0.035, 0.22, 0.035), (0.68, 0.70, 0.70, 1.0),
                (side * 0.73, 0.27, 0.79),
            ).reparent_to(model)
        make_box((1.45, 0.12, 0.13), (0.10, 0.11, 0.12, 1.0), (0, -1.94, 0.32)).reparent_to(model)
        make_box((0.46, 0.06, 0.16), (0.86, 0.86, 0.80, 1.0), (0, -2.01, 0.47)).reparent_to(model)

        wheels = []
        for x in (-0.88, 0.88):
            for y in (-1.24, 1.20):
                wheel = self._car_wheel(0.34 if style == 2 else 0.31)
                wheel.reparent_to(model)
                wheel.set_pos(x, y, 0.28 if style == 2 else 0.25)
                wheels.append(wheel)
        return model, wheels

    def _spawn_cars(self) -> None:
        team = TEAM_COLORS[self.defending_team]
        for index in range(CAR_COUNT):
            direction = 1.0 if index % 2 == 0 else -1.0
            node = BulletRigidBodyNode(f"civilian-car-{index}")
            node.add_shape(
                BulletBoxShape(Vec3(0.90, 1.96, 0.78)),
                TransformState.make_pos(Vec3(0, 0, 0.74)),
            )
            node.set_mass(0.0)
            node.set_kinematic(True)
            root = self.root.attach_new_node(node)
            shade = 0.72 + 0.08 * (index % 4)
            color = tuple(min(1.0, channel * shade) for channel in team[:3]) + (1.0,)
            model, wheels = self._make_car_model(root, index, color)
            car = CivilianCar(
                index, root, node, model, wheels, self.defending_team, index / CAR_COUNT,
                5.4 if direction > 0 else 5.8, direction,
            )
            node.set_python_tag("city_target", car)
            self.world.attach(node)
            self.cars.append(car)
        self._update_cars(0.0, False)

    # ------------------------------------------------------------------
    # Behaviour and damage
    # ------------------------------------------------------------------
    def _nearest_shelter(
        self, position: Point3, exclude: CityBuilding | None = None
    ) -> CityBuilding | None:
        candidates = [
            building
            for building in self.buildings
            if building.alive and not building.critical and building is not exclude
        ]
        return min(
            candidates,
            key=lambda building: (building.position - position).length_squared(),
            default=None,
        )

    def _evacuate(self, building: CityBuilding) -> None:
        for civilian in self.civilians:
            if not civilian.alive or not civilian.sheltered or civilian.home is not building:
                continue
            civilian.sheltered = False
            civilian.home = None
            civilian.target = self._nearest_shelter(building.position, exclude=building)
            civilian.np.set_pos(
                building.position.x,
                building.position.y - building.size.y * 0.55,
                self._ground(building.position.x, building.position.y) + 0.4,
            )
            if not civilian.in_world:
                self.world.attach(civilian.node)
                civilian.in_world = True
            civilian.np.show()

    def damage_building(self, building: CityBuilding, amount: float, effects) -> bool:
        if not building.alive:
            return False
        self.alert_time = max(self.alert_time, 24.0)
        building.health -= amount
        fraction = max(0.0, building.health / building.max_health)
        building.model.set_color_scale(0.45 + fraction * 0.55, 0.45 + fraction * 0.55, 0.45 + fraction * 0.55, 1)
        if building.critical:
            self._evacuate(building)
        if building.health > 0.0:
            return False

        building.health = 0.0
        building.alive = False
        self._evacuate(building)
        self.world.remove(building.node)
        building.np.remove_node()
        effects.explosion(building.position, scale=3.8, debris_count=12)
        rubble_color = (0.30, 0.29, 0.27, 1.0)
        for _ in range(9):
            rubble = make_box(
                (
                    self.rng.uniform(1.5, 4.0),
                    self.rng.uniform(1.5, 4.0),
                    self.rng.uniform(0.5, 1.5),
                ),
                rubble_color,
            )
            rubble.reparent_to(self.root)
            rx = building.position.x + self.rng.uniform(-building.size.x * 0.45, building.size.x * 0.45)
            ry = building.position.y + self.rng.uniform(-building.size.y * 0.45, building.size.y * 0.45)
            rubble.set_pos(rx, ry, self._ground(rx, ry) + 0.45)
            rubble.set_h(self.rng.uniform(0, 360))
        return True

    def damage_target(self, target, amount: float, effects) -> bool:
        """Damage any attackable city asset and report whether it was lost."""
        self.alert_time = max(self.alert_time, 24.0)
        if isinstance(target, CityBuilding):
            return self.damage_building(target, amount, effects)
        if not target.alive:
            return False
        target.health -= amount
        if target.health > 0.0:
            if isinstance(target, Civilian):
                effects.blood(target.position + Vec3(0, 0, 0.5), scale=0.6, count=3)
            else:
                effects.smoke_puff(target.position + Vec3(0, 0, 0.8), scale=0.65)
            return False
        target.health = 0.0
        target.alive = False
        is_attached = target.in_world if isinstance(target, Civilian) else True
        if is_attached:
            self.world.remove(target.node)
            if isinstance(target, Civilian):
                target.in_world = False
        position = Point3(target.position)
        if isinstance(target, Civilian):
            effects.blood(position + Vec3(0, 0, 0.5), scale=1.25, count=10)
            target.np.hide()
        else:
            effects.explosion(position, scale=1.25, debris_count=5)
            target.np.set_color_scale(0.22, 0.22, 0.22, 1.0)
        return True

    def apply_blast(
        self, position: Point3, damage: float, radius: float, effects,
        on_damage=None,
    ) -> None:
        self.alert_time = max(self.alert_time, 24.0)
        centre = Vec3(position)

        def damage_asset(target, amount: float) -> None:
            if on_damage is not None:
                on_damage(target, amount)
            else:
                self.damage_target(target, amount, effects)

        # Resolve civilians who were already outdoors before damaged shelters
        # release their occupants; evacuation should not retroactively place a
        # sheltered person inside the blast that triggered it.
        for civilian in self.civilians:
            if not civilian.alive or civilian.sheltered:
                continue
            distance = (Vec3(civilian.position) - centre).length()
            if distance <= radius:
                falloff = max(0.25, 1.0 - distance / max(radius, 1.0))
                damage_asset(civilian, damage * falloff)
        for car in self.cars:
            if not car.alive:
                continue
            distance = (Vec3(car.position) - centre).length()
            if distance <= radius:
                falloff = max(0.25, 1.0 - distance / max(radius, 1.0))
                damage_asset(car, damage * falloff)
        for building in self.buildings:
            if not building.alive:
                continue
            distance = (Vec3(building.position) - centre).length()
            reach = radius + math.hypot(building.size.x, building.size.y) * 0.25
            if distance <= reach:
                falloff = max(0.20, 1.0 - distance / max(reach, 1.0))
                damage_asset(building, damage * falloff)

    def update(self, dt: float, effects, enemies) -> None:
        self.alert_time = max(0.0, self.alert_time - dt)
        centre = Vec3(self.origin[0], self.origin[1], self._ground(*self.origin))
        nearby_enemy = any(
            enemy.alive
            and (Vec3(enemy.position) - centre).length_squared() < 155.0**2
            for enemy in enemies
        )
        threatened = nearby_enemy or self.alert_time > 0.0

        for building in self.buildings:
            if building.critical:
                self._evacuate(building)
            if building.alive and building.health < building.max_health * 0.6:
                if self.rng.random() < dt * 4.0:
                    effects.smoke_puff(building.position + Vec3(0, 0, building.size.z * 0.45), scale=1.2)

        for civilian in self.civilians:
            if not civilian.alive or civilian.sheltered:
                continue
            if threatened and civilian.target is None:
                civilian.target = self._nearest_shelter(civilian.position)
            target = civilian.target
            if target is None or not target.alive or target.critical:
                civilian.target = self._nearest_shelter(civilian.position, exclude=target)
                target = civilian.target
            if target is None:
                civilian.model.set_z(0.0)
                civilian.model.set_r(0.0)
                continue
            door = Point3(
                target.position.x,
                target.position.y - target.size.y * 0.55,
                self._ground(target.position.x, target.position.y) + 0.4,
            )
            delta = door - civilian.position
            delta.z = 0
            distance = delta.length()
            if distance <= 1.0:
                civilian.sheltered = True
                civilian.home = target
                civilian.target = None
                if civilian.in_world:
                    self.world.remove(civilian.node)
                    civilian.in_world = False
                civilian.np.hide()
                civilian.model.set_z(0.0)
                civilian.model.set_r(0.0)
                continue
            delta.normalize()
            step = min(distance, civilian.speed * dt)
            position = civilian.position + delta * step
            position.z = self._ground(position.x, position.y) + 0.4
            civilian.np.set_pos(position)
            civilian.np.set_h(math.degrees(math.atan2(delta.y, delta.x)) - 90.0)
            civilian.gait_phase += step * 5.2
            stride = math.sin(civilian.gait_phase)
            civilian.model.set_z(abs(stride) * 0.035)
            civilian.model.set_r(stride * 1.1)

        self._update_cars(dt, threatened)

    def _update_cars(self, dt: float, threatened: bool) -> None:
        ox, oy = self.origin
        half = 53.0
        perimeter = half * 8.0
        for car in self.cars:
            if not car.alive:
                continue
            speed = car.speed * (1.55 if threatened else 1.0)
            previous = Point3(car.np.get_pos())
            # Opposite traffic gets its own lane, preventing kinematic cars
            # from visually passing through one another head-on.
            half = 54.0 if car.direction > 0 else 60.0
            perimeter = half * 8.0
            car.progress = (car.progress + car.direction * speed * dt / perimeter) % 1.0
            distance = car.progress * perimeter
            if distance < half * 2:
                x, y, heading = -half + distance, -half, 90.0
            elif distance < half * 4:
                x, y, heading = half, -half + (distance - half * 2), 0.0
            elif distance < half * 6:
                x, y, heading = half - (distance - half * 4), half, -90.0
            else:
                x, y, heading = -half, half - (distance - half * 6), 180.0
            position = Point3(ox + x, oy + y, self._ground(ox + x, oy + y) + 0.12)
            car.np.set_pos(position)
            car.np.set_h(heading if car.direction > 0 else heading + 180.0)
            if dt > 1e-6:
                car.motion = (position - previous) / dt
                wheel_turn = math.degrees(speed * dt / 0.32) * car.direction
                for wheel in car.wheels:
                    wheel.set_p(wheel.get_p() + wheel_turn)

    # ------------------------------------------------------------------
    @property
    def buildings_alive(self) -> int:
        return sum(building.alive for building in self.buildings)

    @property
    def civilians_alive(self) -> int:
        return sum(civilian.alive for civilian in self.civilians)

    @property
    def cars_alive(self) -> int:
        return sum(car.alive for car in self.cars)

    @property
    def targets(self) -> list:
        """Assets exposed to hostile target selection right now."""
        return (
            [building for building in self.buildings if building.alive]
            + [civilian for civilian in self.civilians if civilian.alive and not civilian.sheltered]
            + [car for car in self.cars if car.alive]
        )

    @property
    def failed(self) -> bool:
        return (
            self.buildings_alive <= self.initial_buildings * 0.40
            or self.civilians_alive <= self.initial_civilians * 0.50
        )

    def cleanup(self) -> None:
        for building in self.buildings:
            if building.alive:
                self.world.remove(building.node)
        for civilian in self.civilians:
            if civilian.in_world:
                self.world.remove(civilian.node)
        for car in self.cars:
            if car.alive:
                self.world.remove(car.node)
        self.root.remove_node()
