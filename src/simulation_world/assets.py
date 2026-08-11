"""Model loading, with procedural low-poly placeholders as a fallback.

Drop real models into ``assets/models/`` (see the project README) and they
are picked up automatically; until then every unit is built out of boxes so
the simulation always runs.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from panda3d.core import (
    Geom,
    GeomNode,
    GeomTriangles,
    GeomVertexData,
    GeomVertexFormat,
    GeomVertexWriter,
    InternalName,
    NodePath,
    Vec3,
)

# Panda3D reads .bam/.egg natively, .gltf/.glb through panda3d-gltf, and
# .obj/.dae/.fbx through the bundled assimp loader.
MODEL_SUFFIXES = (".bam", ".glb", ".gltf", ".egg", ".obj", ".dae")

# Each face is (normal, four corners in counter-clockwise winding seen from
# outside), for a cube spanning -0.5..0.5 on every axis.
BOX_FACES = (
    ((1, 0, 0), ((0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (0.5, 0.5, 0.5), (0.5, -0.5, 0.5))),
    ((-1, 0, 0), ((-0.5, 0.5, -0.5), (-0.5, -0.5, -0.5), (-0.5, -0.5, 0.5), (-0.5, 0.5, 0.5))),
    ((0, 1, 0), ((0.5, 0.5, -0.5), (-0.5, 0.5, -0.5), (-0.5, 0.5, 0.5), (0.5, 0.5, 0.5))),
    ((0, -1, 0), ((-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, -0.5, 0.5), (-0.5, -0.5, 0.5))),
    ((0, 0, 1), ((-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5))),
    ((0, 0, -1), ((-0.5, 0.5, -0.5), (0.5, 0.5, -0.5), (0.5, -0.5, -0.5), (-0.5, -0.5, -0.5))),
)


def make_box(size=(1.0, 1.0, 1.0), color=(1, 1, 1, 1), center=(0.0, 0.0, 0.0)) -> NodePath:
    """Build a flat-shaded coloured box as a standalone NodePath."""
    fmt = GeomVertexFormat.get_v3n3c4()
    vdata = GeomVertexData("box", fmt, Geom.UH_static)
    vdata.set_num_rows(24)
    vertex = GeomVertexWriter(vdata, "vertex")
    normal = GeomVertexWriter(vdata, "normal")
    col = GeomVertexWriter(vdata, "color")
    tris = GeomTriangles(Geom.UH_static)

    for i, (nrm, corners) in enumerate(BOX_FACES):
        for cx, cy, cz in corners:
            vertex.add_data3(
                cx * size[0] + center[0],
                cy * size[1] + center[1],
                cz * size[2] + center[2],
            )
            normal.add_data3(*nrm)
            col.add_data4(*color)
        base = i * 4
        tris.add_vertices(base, base + 1, base + 2)
        tris.add_vertices(base, base + 2, base + 3)

    geom = Geom(vdata)
    geom.add_primitive(tris)
    node = GeomNode("box")
    node.add_geom(geom)
    return NodePath(node)


def make_loft(rings, color, cap_start: bool = True, cap_end: bool = True) -> NodePath:
    """Skin a sequence of cross-sections into one flat-shaded solid.

    Boxes cannot describe a swept wing or a chined fuselage. Lofting between
    rings of points can, and it is still one flat face per quad, so the result
    stays in the same faceted low-poly language as everything else.

    ``rings`` is a list of equal-length point rings, each ordered consistently
    around the section; consecutive rings are joined with quads.
    """
    faces: list[tuple[int, ...]] = []
    vertices: list[tuple[float, float, float]] = []
    for ring in rings:
        vertices.extend(ring)

    size = len(rings[0])
    for r in range(len(rings) - 1):
        a = r * size
        b = (r + 1) * size
        for i in range(size):
            j = (i + 1) % size
            faces.append((a + i, b + i, b + j, a + j))

    if cap_start:
        faces.append(tuple(range(size - 1, -1, -1)))
    if cap_end:
        base = (len(rings) - 1) * size
        faces.append(tuple(range(base, base + size)))

    return make_solid(vertices, faces, color)


def make_solid(vertices, faces, color) -> NodePath:
    """Flat-shaded mesh from explicit vertices and convex polygon faces."""
    fmt = GeomVertexFormat.get_v3n3c4()
    vdata = GeomVertexData("solid", fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    normal = GeomVertexWriter(vdata, "normal")
    col = GeomVertexWriter(vdata, "color")
    prim = GeomTriangles(Geom.UH_static)

    index = 0
    for face in faces:
        points = [Vec3(*vertices[i]) for i in face]
        # Newell's method: robust for polygons that are not perfectly planar,
        # which lofted rings rarely are.
        nrm = Vec3(0, 0, 0)
        for k, current in enumerate(points):
            nxt = points[(k + 1) % len(points)]
            nrm.set_x(nrm.x + (current.y - nxt.y) * (current.z + nxt.z))
            nrm.set_y(nrm.y + (current.z - nxt.z) * (current.x + nxt.x))
            nrm.set_z(nrm.z + (current.x - nxt.x) * (current.y + nxt.y))
        if nrm.length_squared() < 1e-12:
            continue
        nrm.normalize()

        for point in points:
            vertex.add_data3(point)
            normal.add_data3(nrm)
            col.add_data4(*color)
        for k in range(1, len(points) - 1):
            prim.add_vertices(index, index + k, index + k + 1)
        index += len(points)

    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode("solid")
    node.add_geom(geom)
    return NodePath(node)


def has_normals(root: NodePath) -> bool:
    """True if any geometry carries a normal column.

    A model exported without normals renders solid black under lighting, which
    is a confusing failure mode, so callers warn and fall back to unlit.
    """
    column = InternalName.get_normal()
    for geom_np in root.find_all_matches("**/+GeomNode"):
        node = geom_np.node()
        for i in range(node.get_num_geoms()):
            if node.get_geom(i).get_vertex_data().has_column(column):
                return True
    return False


def make_health_bar() -> tuple[NodePath, NodePath]:
    """Billboarded health bar; returns (root, fill) so callers can scale the fill."""
    from panda3d.core import CardMaker, TransparencyAttrib

    root = NodePath("healthbar")
    root.set_billboard_point_eye()
    root.set_light_off()
    root.set_transparency(TransparencyAttrib.M_alpha)
    root.set_depth_write(False)
    root.set_bin("fixed", 20)

    back = CardMaker("bg")
    back.set_frame(-1.55, 1.55, -0.19, 0.19)
    bg_np = root.attach_new_node(back.generate())
    bg_np.set_color(0.05, 0.05, 0.07, 0.75)

    front = CardMaker("fill")
    # Anchored at the left edge so setting SX scales it like a real gauge.
    front.set_frame(0.0, 2.9, -0.13, 0.13)
    fill_np = root.attach_new_node(front.generate())
    fill_np.set_pos(-1.45, -0.02, 0)
    fill_np.set_color(0.2, 0.85, 0.35, 1.0)

    return root, fill_np


def _shade(color, factor: float):
    """Lighten (factor>1) or darken (factor<1) an RGBA tuple."""
    r, g, b, a = color
    return (min(1.0, r * factor), min(1.0, g * factor), min(1.0, b * factor), a)


def _round_ring(y: float, half_w: float, half_h: float, z: float = 0.0, sides: int = 8):
    """Elliptical section in the XZ plane at station `y`."""
    return [
        (
            math.cos(i * math.tau / sides) * half_w,
            y,
            z + math.sin(i * math.tau / sides) * half_h,
        )
        for i in range(sides)
    ]


def _tapered_vertical(
    height: float,
    top_size: tuple[float, float],
    bottom_size: tuple[float, float],
    color,
) -> NodePath:
    """A faceted limb/body segment running from its joint down the local Z axis."""
    tx, ty = top_size
    bx, by = bottom_size
    vertices = [
        (-tx, -ty, 0), (tx, -ty, 0), (tx, ty, 0), (-tx, ty, 0),
        (-bx, -by, -height), (bx, -by, -height), (bx, by, -height), (-bx, by, -height),
    ]
    faces = [
        (3, 2, 1, 0), (4, 5, 6, 7),
        (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
    ]
    return make_solid(vertices, faces, color)


def _joint(parent: NodePath, name: str, position) -> NodePath:
    """Create a named animation pivot without adding render geometry."""
    node = parent.attach_new_node(name)
    node.set_pos(*position)
    return node


def _limb_between(
    parent: NodePath,
    name: str,
    start,
    end,
    top_radius: float,
    bottom_radius: float,
    color,
) -> NodePath:
    """Build a tapered segment whose endpoints are exact in parent space."""
    start_v = Vec3(*start)
    end_v = Vec3(*end)
    length = (end_v - start_v).length()
    pivot = _joint(parent, name, start)
    pivot.look_at(parent, end_v)
    make_loft(
        [
            _round_ring(0.0, top_radius, top_radius, 0.0, 6),
            _round_ring(length, bottom_radius, bottom_radius, 0.0, 6),
        ],
        color,
    ).reparent_to(pivot)
    return pivot


def build_placeholder_helicopter(color) -> NodePath:
    """A Mi-24 shaped gunship, nose along +Y.

    The recognisable bits are the tandem bubble canopies, the heavily
    anhedral stub wings loaded with pods, and the chin turret — so those get
    the geometry rather than a generic pod with a stick.
    """
    root = NodePath("heli_placeholder")
    dark = (0.14, 0.15, 0.17, 1.0)
    glass = (0.20, 0.31, 0.36, 1.0)
    steel = _shade(color, 0.72)

    # Fuselage: deep forward body tapering into a slim tail boom.
    make_loft(
        [
            _round_ring(5.30, 0.22, 0.22, 0.10),
            _round_ring(4.55, 0.52, 0.46, 0.02),
            _round_ring(3.20, 0.80, 0.72, -0.02),
            _round_ring(1.40, 0.96, 0.92, 0.06),
            _round_ring(-0.60, 1.00, 0.98, 0.10),
            _round_ring(-2.60, 0.86, 0.84, 0.16),
            _round_ring(-4.30, 0.52, 0.52, 0.26),
            _round_ring(-5.60, 0.34, 0.34, 0.34),
        ],
        color,
    ).reparent_to(root)

    # Tail boom and fin.
    make_loft(
        [
            _round_ring(-5.60, 0.34, 0.34, 0.34),
            _round_ring(-8.20, 0.26, 0.26, 0.46),
            _round_ring(-9.60, 0.22, 0.24, 0.62),
        ],
        _shade(color, 0.95),
    ).reparent_to(root)
    fin = make_loft(
        [
            _airfoil_ring(0.0, -8.60, -10.10, 0.26),
            _airfoil_ring(1.85, -9.35, -10.15, 0.12),
        ],
        _shade(color, 0.9),
    )
    fin.set_r(-84.0)
    fin.set_pos(0, 0, 0.55)
    fin.reparent_to(root)
    # Stabiliser.
    for side in (-1, 1):
        make_box((1.5, 0.7, 0.12), _shade(color, 0.9), (side * 0.85, -8.85, 0.55)).reparent_to(root)

    # The double bubble: gunner low in front, pilot stepped up behind.
    make_loft(
        [
            _round_ring(4.60, 0.44, 0.34, 0.42),
            _round_ring(3.90, 0.62, 0.52, 0.52),
            _round_ring(3.10, 0.60, 0.50, 0.56),
        ],
        glass,
    ).reparent_to(root)
    make_loft(
        [
            _round_ring(3.05, 0.60, 0.50, 0.86),
            _round_ring(2.30, 0.74, 0.62, 1.02),
            _round_ring(1.30, 0.72, 0.58, 1.00),
        ],
        glass,
    ).reparent_to(root)

    # Chin turret and nose sensors.
    make_loft(
        [_round_ring(4.75, 0.26, 0.24, -0.42), _round_ring(4.05, 0.34, 0.30, -0.48)],
        dark,
    ).reparent_to(root)
    make_box((0.16, 1.0, 0.16), dark, (0.10, 5.30, -0.46)).reparent_to(root)

    # Stub wings with the Hind's pronounced anhedral, and their pods.
    for side in (-1, 1):
        wing = make_loft(
            [
                _airfoil_ring(side * 0.90, 0.70, -1.30, 0.34),
                _airfoil_ring(side * 3.30, 0.35, -1.35, 0.16),
            ],
            _shade(color, 0.95),
        )
        wing.set_pos(0, -0.30, 0.30)
        wing.set_r(side * 16.0)
        wing.reparent_to(root)

        for offset, radius in ((1.75, 0.30), (2.95, 0.26)):
            pod = make_loft(
                [
                    _round_ring(0.85, radius, radius),
                    _round_ring(-0.95, radius, radius),
                ],
                steel,
            )
            pod.set_pos(side * offset, -0.45, -0.28)
            pod.reparent_to(root)
            make_box((radius * 1.6, 0.16, radius * 1.6), dark, (side * offset, 0.88, -0.28)).reparent_to(root)

    # Engine intakes and exhausts over the fuselage.
    for side in (-1, 1):
        intake = make_loft(
            [_round_ring(1.85, 0.34, 0.30), _round_ring(0.60, 0.38, 0.34)],
            steel,
        )
        intake.set_pos(side * 0.52, 0, 0.95)
        intake.reparent_to(root)

    # Main rotor: five blades on a hub, plus the mast.
    make_loft(
        [_round_ring(0.0, 0.26, 0.26, 1.10), _round_ring(0.0, 0.22, 0.22, 1.62)],
        dark,
    ).reparent_to(root)
    main_rotor = NodePath("MainRotor")
    main_rotor.set_pos(0, -0.10, 1.70)
    main_rotor.reparent_to(root)
    make_loft(
        [_round_ring(0.0, 0.42, 0.16), _round_ring(0.0, 0.34, 0.14)],
        _shade(color, 0.8),
    ).reparent_to(main_rotor)
    for i in range(5):
        blade = make_box((0.34, 8.6, 0.07), dark, (0, 4.1, 0))
        blade.set_h(i * 72.0)
        blade.reparent_to(main_rotor)

    # Tail rotor, on the left side of the fin as on the real aircraft.
    tail_rotor = NodePath("TailRotor")
    tail_rotor.set_pos(-0.32, -9.35, 1.15)
    tail_rotor.reparent_to(root)
    # Two double-ended blades read as a four-blade rotor. Three at 60 degrees
    # only span 180 and pile up into a star.
    for angle in (0.0, 90.0):
        blade = make_box((0.05, 0.20, 1.30), dark)
        blade.set_p(angle)
        blade.reparent_to(tail_rotor)
    make_loft(
        [_round_ring(0.0, 0.16, 0.16), _round_ring(-0.18, 0.14, 0.14)],
        _shade(color, 0.8),
    ).reparent_to(tail_rotor)

    # Stub undercarriage fairings.
    for side in (-1, 1):
        make_box((0.34, 1.1, 0.44), steel, (side * 0.90, -1.60, -0.90)).reparent_to(root)
    make_box((0.28, 0.9, 0.40), steel, (0, 4.10, -0.85)).reparent_to(root)

    return root


def _hull_ring(y: float, w_top: float, w_bot: float, z_top: float, z_bot: float):
    """Four-point hull section: a trapezoid, wider at the sponson line."""
    return [
        (-w_top, y, z_top),
        (w_top, y, z_top),
        (w_bot, y, z_bot),
        (-w_bot, y, z_bot),
    ]


def _disc_ring(x: float, y: float, z: float, radius: float, sides: int = 8):
    """Polygonal wheel section in the YZ plane, extruded along X."""
    return [
        (
            x,
            y + math.cos(i * math.tau / sides) * radius,
            z + math.sin(i * math.tau / sides) * radius,
        )
        for i in range(sides)
    ]


def _tube_ring(y: float, radius: float, x: float = 0.0, z: float = 0.0, sides: int = 10):
    """Disc in the XZ plane at station `y` — for something pointing along +Y.

    Distinct from `_disc_ring`, which lies in YZ for wheels. Using the wheel
    helper for a gun barrel produces overlapping rings and a degenerate loft
    that renders as a hairline.
    """
    return [
        (
            x + math.cos(i * math.tau / sides) * radius,
            y,
            z + math.sin(i * math.tau / sides) * radius,
        )
        for i in range(sides)
    ]


def build_placeholder_tank(color) -> NodePath:
    """A Leopard 2-shaped main battle tank, gun pointing along +Y.

    Lofted rather than stacked from boxes so the sloped glacis and the wedge
    turret read as an actual tank instead of a brick with a stick on it.
    """
    root = NodePath("tank_placeholder")
    dark = (0.13, 0.14, 0.15, 1.0)
    rubber = (0.10, 0.10, 0.11, 1.0)
    steel = _shade(color, 0.72)
    deck = _shade(color, 0.88)

    # Hull: nose low and sharp, deck rising over the sloped glacis.
    make_loft(
        [
            _hull_ring(4.00, 1.20, 1.30, -0.18, -0.52),
            _hull_ring(2.90, 1.48, 1.58, 0.30, -0.52),
            _hull_ring(1.30, 1.56, 1.62, 0.46, -0.50),
            _hull_ring(-1.70, 1.56, 1.62, 0.46, -0.50),
            _hull_ring(-3.75, 1.44, 1.52, 0.40, -0.46),
        ],
        color,
    ).reparent_to(root)

    # Running gear. One solid track unit a side rather than two thin rails with
    # the road wheels poking through the gap, which read as a dashed line.
    for side in (-1, 1):
        track = make_loft(
            [
                _hull_ring(3.45, 0.17, 0.17, -0.16, -1.02),
                _hull_ring(3.05, 0.17, 0.17, -0.02, -1.06),
                _hull_ring(-3.10, 0.17, 0.17, -0.02, -1.06),
                _hull_ring(-3.55, 0.17, 0.17, -0.16, -1.02),
            ],
            rubber,
        )
        track.set_x(side * 1.46)
        track.reparent_to(root)
        # Drive sprocket and idler peek out at each end.
        make_loft(
            [_disc_ring(side * 1.30, -3.30, -0.52, 0.30), _disc_ring(side * 1.62, -3.30, -0.52, 0.30)],
            steel,
        ).reparent_to(root)
        make_loft(
            [_disc_ring(side * 1.30, 3.25, -0.52, 0.30), _disc_ring(side * 1.62, 3.25, -0.52, 0.30)],
            steel,
        ).reparent_to(root)
        # Skirt plate over the upper run.
        make_box((0.11, 6.5, 0.62), steel, (side * 1.66, -0.25, -0.18)).reparent_to(root)
        # Fender stowage.
        make_box((0.34, 1.3, 0.38), deck, (side * 1.40, 1.70, 0.50)).reparent_to(root)

    # Engine deck louvres, set into the rear of the hull.
    make_box((2.4, 1.3, 0.10), steel, (0, -2.95, 0.44)).reparent_to(root)

    turret = NodePath("Turret")
    turret.set_pos(0, -0.35, 0.46)
    turret.reparent_to(root)

    # Wedge turret: narrow, angular front widening to a squared-off bustle.
    make_loft(
        [
            _hull_ring(1.72, 0.62, 0.55, 0.30, 0.02),
            _hull_ring(1.05, 1.12, 1.02, 0.66, 0.00),
            _hull_ring(-0.55, 1.22, 1.14, 0.70, 0.00),
            _hull_ring(-1.60, 1.18, 1.10, 0.62, 0.02),
            _hull_ring(-2.15, 1.05, 1.00, 0.50, 0.06),
        ],
        _shade(color, 1.06),
    ).reparent_to(turret)

    # Stowage basket hugging the bustle, not floating off the back.
    make_box((1.9, 0.75, 0.44), steel, (0, -1.95, 0.26)).reparent_to(turret)
    make_loft(
        [_disc_ring(-0.34, 0.10, 0.86, 0.30), _disc_ring(0.02, 0.10, 0.86, 0.30)],
        _shade(color, 1.12),
    ).reparent_to(turret)                                   # commander's cupola
    make_box((0.46, 0.42, 0.34), dark, (0.62, 0.75, 0.80)).reparent_to(turret)   # gunner's sight
    make_box((0.24, 0.24, 0.5), dark, (-0.60, -0.30, 0.92)).reparent_to(turret)  # MG mount

    # 120 mm gun: mantlet, thermal sleeve over the breech end, then the thin
    # barrel and a slightly flared muzzle.
    make_box((1.15, 0.70, 0.62), _shade(color, 0.9), (0, 1.85, 0.30)).reparent_to(turret)
    make_loft(
        [
            _tube_ring(2.05, 0.24, z=0.30),
            _tube_ring(3.70, 0.24, z=0.30),
            _tube_ring(3.85, 0.155, z=0.30),
            _tube_ring(6.30, 0.145, z=0.30),
            _tube_ring(6.45, 0.20, z=0.30),
            _tube_ring(6.65, 0.20, z=0.30),
        ],
        steel,
    ).reparent_to(turret)

    return root


def build_placeholder_osprey(color) -> NodePath:
    """A blocky stand-in tiltrotor, nose pointing along +Y.

    The two wingtip nacelles are separate named nodes so the animation layer
    can swing them between hover and airplane mode.
    """
    root = NodePath("osprey_placeholder")
    dark = (0.16, 0.17, 0.2, 1.0)
    grey = (0.42, 0.44, 0.46, 1.0)

    make_box((2.5, 7.8, 2.2), color, (0, 0, 0)).reparent_to(root)
    make_box((2.0, 1.9, 1.6), _shade(color, 1.2), (0, 4.3, 0.2)).reparent_to(root)
    make_box((2.2, 1.7, 1.9), _shade(color, 0.8), (0, -4.2, 0.15)).reparent_to(root)
    make_box((1.6, 1.4, 0.45), dark, (0, -4.8, -0.8)).reparent_to(root)  # rear ramp

    # Shoulder wing carrying both nacelles.
    make_box((11.0, 2.0, 0.5), _shade(color, 0.9), (0, -0.4, 1.45)).reparent_to(root)

    # Twin tail, the giveaway silhouette of the real aircraft.
    make_box((4.8, 0.8, 0.4), _shade(color, 0.85), (0, -4.9, 1.3)).reparent_to(root)
    for side in (-1, 1):
        make_box((0.35, 1.5, 2.5), _shade(color, 0.85), (side * 2.3, -4.9, 2.5)).reparent_to(root)

    for side in (-1, 1):
        make_box((0.8, 2.9, 0.85), _shade(color, 0.8), (side * 1.55, -0.6, -0.8)).reparent_to(root)

        nacelle = NodePath(f"Nacelle{'Left' if side < 0 else 'Right'}")
        nacelle.set_pos(side * 5.35, -0.4, 1.65)
        nacelle.reparent_to(root)

        # Nacelle body points +Z when upright, so tilting it pitches thrust forward.
        make_box((1.45, 1.7, 2.9), grey, (0, 0, 0.5)).reparent_to(nacelle)
        make_box((1.1, 1.25, 0.7), dark, (0, 0, 2.1)).reparent_to(nacelle)

        proprotor = NodePath("Proprotor")
        proprotor.set_pos(0, 0, 2.45)
        proprotor.reparent_to(nacelle)
        for angle in (0, 60, 120):
            blade = make_box((0.38, 9.0, 0.11), dark)
            blade.set_h(angle)
            blade.reparent_to(proprotor)

    return root


def build_placeholder_soldier(color, launcher: bool = False) -> NodePath:
    """Articulated low-poly infantryman facing +Y.

    The named pivots are a tiny procedural skeleton.  They keep the fallback
    model cheap, but allow the animation layer to produce a real heel-to-toe
    run instead of sliding a rigid pawn over the terrain.
    """
    root = NodePath("soldier_placeholder")
    uniform = _shade(color, 0.82)
    cloth_dark = _shade(color, 0.58)
    webbing = (0.22, 0.25, 0.18, 1.0)
    armour = (0.18, 0.21, 0.16, 1.0)
    metal = (0.10, 0.11, 0.12, 1.0)
    rubber = (0.07, 0.075, 0.07, 1.0)
    wood = (0.28, 0.16, 0.08, 1.0)
    skin = (0.61, 0.43, 0.30, 1.0)
    skin_shadow = (0.48, 0.32, 0.22, 1.0)

    # Pelvis and articulated legs. Geometry hangs from each joint so rotations
    # bend at the hip and knee instead of through the middle of a solid block.
    make_box((0.58, 0.34, 0.30), cloth_dark, (0, 0, -0.15)).reparent_to(root)
    for side, label in ((-1, "Left"), (1, "Right")):
        hip = _joint(root, f"{label}Hip", (side * 0.18, 0, -0.20))
        _tapered_vertical(0.48, (0.145, 0.16), (0.125, 0.13), uniform).reparent_to(hip)
        # Cargo pocket and a hard kneepad break up the trouser silhouette.
        make_box((0.25, 0.08, 0.20), cloth_dark, (0, 0.15, -0.25)).reparent_to(hip)
        knee = _joint(hip, f"{label}Knee", (0, 0, -0.48))
        make_box((0.24, 0.10, 0.16), armour, (0, 0.13, -0.04)).reparent_to(knee)
        _tapered_vertical(0.46, (0.12, 0.13), (0.095, 0.105), cloth_dark).reparent_to(knee)
        boot = _joint(knee, f"{label}Boot", (0, 0, -0.46))
        make_box((0.24, 0.42, 0.17), rubber, (0, 0.09, -0.05)).reparent_to(boot)

    upper = _joint(root, "UpperBody", (0, 0, 0))
    # Tapered shoulders and waist give a human outline from every camera angle.
    torso = _tapered_vertical(0.72, (0.38, 0.22), (0.27, 0.17), uniform)
    torso.set_z(0.60)
    torso.reparent_to(upper)
    make_box((0.66, 0.12, 0.48), armour, (0, 0.22, 0.30)).reparent_to(upper)
    make_box((0.60, 0.10, 0.38), webbing, (0, -0.23, 0.28)).reparent_to(upper)
    # Magazine/utility pouches along the vest.
    for x in (-0.21, 0.0, 0.21):
        make_box((0.17, 0.12, 0.23), webbing, (x, 0.29, 0.12)).reparent_to(upper)
    make_box((0.10, 0.48, 0.10), webbing, (0, 0, 0.43)).reparent_to(upper)

    # Neck, faceted head, ears, nose and a helmet with a slight front brim.
    make_box((0.18, 0.18, 0.14), skin_shadow, (0, 0, 0.68)).reparent_to(upper)
    make_loft(
        [
            _round_ring(-0.14, 0.14, 0.19, 0.88, 8),
            _round_ring(0.14, 0.14, 0.19, 0.88, 8),
        ],
        skin,
    ).reparent_to(upper)
    make_box((0.055, 0.08, 0.08), skin_shadow, (0, 0.19, 0.88)).reparent_to(upper)
    for side in (-1, 1):
        make_box((0.035, 0.08, 0.10), skin_shadow, (side * 0.155, 0, 0.88)).reparent_to(upper)
    make_loft(
        [
            _round_ring(-0.16, 0.18, 0.10, 1.02, 8),
            _round_ring(0.13, 0.20, 0.12, 1.01, 8),
        ],
        armour,
    ).reparent_to(upper)
    make_box((0.42, 0.15, 0.055), armour, (0, 0.12, 1.00)).reparent_to(upper)

    # Arms are solved from explicit shoulder -> elbow -> grip points. This is
    # more deliberate than rotating two dangling sticks: both wrists now end
    # exactly on their grip, and the stock sits outside the chest.
    if launcher:
        arm_points = {
            "Left": ((-0.40, 0.00, 0.55), (-0.42, 0.38, 0.34), (0.02, 0.79, 0.73)),
            "Right": ((0.40, 0.00, 0.55), (0.47, 0.28, 0.31), (0.26, 0.34, 0.52)),
        }
        weapon_pos = (0.16, 0.12, 0.64)
    else:
        arm_points = {
            "Left": ((-0.40, 0.00, 0.55), (-0.43, 0.35, 0.31), (0.10, 0.72, 0.56)),
            "Right": ((0.40, 0.00, 0.55), (0.48, 0.25, 0.29), (0.28, 0.31, 0.35)),
        }
        weapon_pos = (0.25, 0.14, 0.50)

    for label, (shoulder_pos, elbow_pos, hand_pos) in arm_points.items():
        _limb_between(
            upper, f"{label}Shoulder", shoulder_pos, elbow_pos, 0.115, 0.09, uniform
        )
        _limb_between(
            upper, f"{label}Elbow", elbow_pos, hand_pos, 0.088, 0.065, cloth_dark
        )
        make_box((0.15, 0.17, 0.15), skin, hand_pos).reparent_to(upper)

    weapon = _joint(upper, "Weapon", weapon_pos)
    if launcher:
        # RPG-style launcher: long olive tube, flared venturi, optical sight,
        # grips and the distinctive bulbous warhead ahead of the shoulder.
        tube = (0.24, 0.27, 0.15, 1.0)
        make_box((0.15, 1.72, 0.15), tube, (0, 0.54, 0.10)).reparent_to(weapon)
        make_box((0.24, 0.26, 0.24), metal, (0, -0.33, 0.10)).reparent_to(weapon)
        make_box((0.11, 0.23, 0.30), rubber, (0, 0.26, -0.10)).reparent_to(weapon)
        make_box((0.08, 0.19, 0.14), metal, (-0.12, 0.45, 0.25)).reparent_to(weapon)
        make_loft(
            [
                _round_ring(1.35, 0.035, 0.035, 0.10, 8),
                _round_ring(1.48, 0.16, 0.16, 0.10, 8),
                _round_ring(1.76, 0.10, 0.10, 0.10, 8),
            ],
            (0.30, 0.31, 0.16, 1.0),
        ).reparent_to(weapon)
        # Pack and a spare round make the anti-armour role legible at distance.
        make_box((0.48, 0.25, 0.58), webbing, (0, -0.33, 0.24)).reparent_to(upper)
        make_box((0.11, 0.16, 0.68), tube, (-0.23, -0.42, 0.25)).reparent_to(upper)
    else:
        # AK-inspired silhouette: stock, receiver, handguard, gas tube, sights,
        # barrel, muzzle brake and a visibly curved two-piece magazine.
        make_box((0.16, 0.43, 0.16), wood, (0, -0.18, 0.02)).reparent_to(weapon)
        make_box((0.18, 0.36, 0.18), metal, (0, 0.20, 0.05)).reparent_to(weapon)
        make_box((0.15, 0.38, 0.14), wood, (0, 0.55, 0.05)).reparent_to(weapon)
        make_box((0.07, 0.72, 0.07), metal, (0, 0.83, 0.08)).reparent_to(weapon)
        make_box((0.09, 0.17, 0.09), metal, (0, 1.20, 0.08)).reparent_to(weapon)
        make_box((0.035, 0.035, 0.12), metal, (0, 1.02, 0.17)).reparent_to(weapon)
        make_box((0.11, 0.10, 0.27), rubber, (0, 0.15, -0.17)).reparent_to(weapon)
        mag = make_box((0.13, 0.14, 0.31), metal, (0, 0, -0.12))
        mag.set_p(-15)
        mag.set_pos(0, 0.34, -0.12)
        mag.reparent_to(weapon)
        make_box((0.05, 0.09, 0.06), metal, (0, 0.28, 0.19)).reparent_to(weapon)

    return root


def _fuselage_ring(y: float, w_top: float, w_chine: float, w_bot: float, z_top: float, z_bot: float):
    """Eight-point section: flat top, sharp side chine, tapered belly."""
    return [
        (0.0, y, z_top),
        (w_top, y, z_top * 0.55),
        (w_chine, y, 0.0),
        (w_bot, y, z_bot * 0.55),
        (0.0, y, z_bot),
        (-w_bot, y, z_bot * 0.55),
        (-w_chine, y, 0.0),
        (-w_top, y, z_top * 0.55),
    ]


def _airfoil_ring(x: float, leading: float, trailing: float, thickness: float, z: float = 0.0):
    """Diamond section for a wing panel, spanning leading to trailing edge."""
    mid = (leading + trailing) * 0.5
    return [
        (x, leading, z),
        (x, mid, z + thickness * 0.5),
        (x, trailing, z),
        (x, mid, z - thickness * 0.5),
    ]


def _canopy_ring(y: float, width: float, z_bot: float, z_top: float):
    """Six-point bubble section that sits *on* the spine, not inside it."""
    z_mid = z_bot + (z_top - z_bot) * 0.7
    return [
        (0.0, y, z_top),
        (width, y, z_mid),
        (width * 0.92, y, z_bot),
        (0.0, y, z_bot),
        (-width * 0.92, y, z_bot),
        (-width, y, z_mid),
    ]


def build_placeholder_jet(color) -> NodePath:
    """An F-35-shaped stealth fighter, nose along +Y.

    Built from lofted sections rather than boxes so the chined fuselage, the
    swept trapezoidal wings and the canted twin tails actually read.
    """
    root = NodePath("jet_placeholder")
    body = _shade(color, 1.0)
    panel = _shade(color, 0.86)
    dark = (0.13, 0.14, 0.16, 1.0)
    glass = (0.20, 0.30, 0.38, 1.0)
    burner = (1.0, 0.62, 0.24, 1.0)

    # Fuselage: nose point back to the exhaust.
    make_loft(
        [
            _fuselage_ring(7.4, 0.05, 0.06, 0.05, 0.05, -0.05),
            _fuselage_ring(6.1, 0.34, 0.52, 0.34, 0.34, -0.40),
            _fuselage_ring(4.3, 0.60, 0.98, 0.60, 0.56, -0.64),
            _fuselage_ring(2.3, 0.74, 1.26, 0.74, 0.74, -0.74),
            _fuselage_ring(0.2, 0.98, 1.58, 0.96, 0.82, -0.82),
            _fuselage_ring(-2.6, 0.92, 1.38, 0.88, 0.80, -0.76),
            _fuselage_ring(-5.0, 0.64, 0.88, 0.62, 0.62, -0.56),
            _fuselage_ring(-6.6, 0.44, 0.52, 0.42, 0.44, -0.42),
        ],
        body,
    ).reparent_to(root)

    # Canopy: raised clear of the spine, otherwise it vanishes inside the
    # fuselage, which is thicker than the bubble at every station.
    make_loft(
        [
            _canopy_ring(4.85, 0.14, 0.44, 0.52),
            _canopy_ring(3.85, 0.44, 0.50, 1.02),
            _canopy_ring(2.70, 0.48, 0.60, 1.12),
            _canopy_ring(1.60, 0.34, 0.72, 0.92),
        ],
        glass,
    ).reparent_to(root)

    # Wings: swept trapezoids, thinning towards the tip.
    for side in (1, -1):
        make_loft(
            [
                _airfoil_ring(side * 1.30, 2.10, -2.70, 0.36),
                _airfoil_ring(side * 3.40, 0.70, -2.82, 0.24),
                _airfoil_ring(side * 5.75, -1.15, -2.95, 0.10),
            ],
            panel,
        ).reparent_to(root)

        # Stabilators, swept harder than the wings.
        make_loft(
            [
                _airfoil_ring(side * 1.05, -3.90, -6.30, 0.22),
                _airfoil_ring(side * 3.05, -5.05, -6.75, 0.09),
            ],
            panel,
        ).reparent_to(root)

        # Canted vertical tails — the F-35's clearest silhouette cue.
        tail = make_loft(
            [
                _airfoil_ring(0.0, -2.90, -5.60, 0.26),
                _airfoil_ring(2.35, -4.70, -5.85, 0.10),
            ],
            panel,
        )
        tail.set_pos(side * 1.05, 0.0, 0.55)
        tail.set_r(side * -63.0)  # lean the fin outward from vertical
        tail.reparent_to(root)

    # Intake cheeks either side of the fuselage.
    for side in (1, -1):
        cheek = make_loft(
            [
                _fuselage_ring(2.7, 0.10, 0.26, 0.22, 0.28, -0.32),
                _fuselage_ring(0.5, 0.14, 0.34, 0.30, 0.38, -0.42),
            ],
            dark,
        )
        cheek.set_pos(side * 1.16, 0.0, -0.08)
        cheek.reparent_to(root)

    nozzle = make_loft(
        [
            _fuselage_ring(-6.4, 0.40, 0.46, 0.38, 0.40, -0.38),
            _fuselage_ring(-7.1, 0.30, 0.34, 0.28, 0.30, -0.28),
        ],
        dark,
    )
    nozzle.reparent_to(root)

    flame = NodePath("Afterburner")
    flame.set_pos(0, -7.2, 0)
    flame.reparent_to(root)
    make_loft(
        [
            _fuselage_ring(0.0, 0.22, 0.26, 0.22, 0.22, -0.22),
            _fuselage_ring(-1.9, 0.04, 0.05, 0.04, 0.05, -0.05),
        ],
        burner,
    ).reparent_to(flame)
    flame.set_light_off()

    return root


def build_placeholder_sam(color) -> NodePath:
    """Self-propelled SAM: tracked hull, slewing launcher rack and radar.

    The turret node is named ``Turret`` so it reuses the same target-tracking
    the tanks already have.
    """
    root = NodePath("sam_placeholder")
    dark = (0.15, 0.16, 0.18, 1.0)
    metal = (0.38, 0.39, 0.41, 1.0)
    tube = _shade(color, 0.62)

    # Hull and running gear.
    make_box((2.7, 5.0, 1.1), color, (0, 0, 0)).reparent_to(root)
    make_box((3.0, 5.2, 0.5), _shade(color, 0.7), (0, 0, -0.6)).reparent_to(root)
    for side in (-1, 1):
        make_box((0.55, 5.4, 0.8), dark, (side * 1.4, 0, -0.5)).reparent_to(root)
    make_box((1.9, 1.4, 0.55), _shade(color, 1.15), (0, 1.9, 0.75)).reparent_to(root)  # cab

    turret = NodePath("Turret")
    turret.set_pos(0, -0.7, 0.72)
    turret.reparent_to(root)
    make_box((2.0, 2.1, 0.85), _shade(color, 1.1), (0, 0, 0.1)).reparent_to(turret)

    # Missile rack: two banks of tubes angled up, the silhouette that reads
    # "anti-air" instantly at a distance.
    for side in (-1, 1):
        for row in range(2):
            bank = NodePath("bank")
            bank.set_pos(side * 1.15, 0.1, 0.35 + row * 0.42)
            bank.set_p(28.0)  # tubes elevated
            bank.reparent_to(turret)
            for i in (0, 1):
                make_box((0.30, 2.6, 0.30), tube, (i * 0.36 - 0.18, 0, 0)).reparent_to(bank)
                make_box((0.16, 0.5, 0.16), dark, (i * 0.36 - 0.18, 1.45, 0)).reparent_to(bank)

    # Search radar plate and optics.
    radar = make_box((1.7, 0.18, 1.0), metal, (0, 0, 0))
    radar.set_pos(0, -0.85, 1.25)
    radar.set_p(-18.0)
    radar.reparent_to(turret)
    make_box((0.7, 0.5, 0.45), dark, (0, 0.85, 0.85)).reparent_to(turret)

    return root


class AssetLibrary:
    """Resolves a unit kind to a renderable model.

    Looks for ``assets/models/<kind>.<ext>``; if nothing is found it falls
    back to the procedural placeholder. ``assets/models.json`` may override
    per-model scale/rotation/offset and the rotor/turret node names.
    """

    def __init__(self, loader, assets_dir: Path, verbose: bool = True) -> None:
        self.loader = loader
        self.assets_dir = Path(assets_dir)
        self.models_dir = self.assets_dir / "models"
        self.verbose = verbose
        self.config = self._load_config()
        self._cache: dict[str, NodePath | None] = {}
        self.report: dict[str, str] = {}

    def _load_config(self) -> dict:
        cfg_path = self.assets_dir / "models.json"
        if cfg_path.is_file():
            try:
                return json.loads(cfg_path.read_text())
            except json.JSONDecodeError as exc:
                print(f"[assets] models.json inválido, se ignora: {exc}")
        return {}

    def _find_file(self, kind: str) -> Path | None:
        explicit = self.config.get(kind, {}).get("file")
        if explicit:
            path = self.models_dir / explicit
            return path if path.is_file() else None
        if not self.models_dir.is_dir():
            return None
        for suffix in MODEL_SUFFIXES:
            for candidate in sorted(self.models_dir.glob(f"{kind}*{suffix}")):
                return candidate
        return None

    def _load_real_model(self, kind: str) -> NodePath | None:
        path = self._find_file(kind)
        if path is None:
            return None
        try:
            model = self.loader.load_model(str(path))
        except Exception as exc:  # noqa: BLE001 - any loader failure falls back
            print(f"[assets] no se pudo cargar {path.name}: {exc}")
            return None
        if model is None or model.is_empty():
            print(f"[assets] {path.name} se cargó vacío, se usa el placeholder")
            return None

        cfg = self.config.get(kind, {})
        wrapper = NodePath(f"{kind}_model")
        model.reparent_to(wrapper)

        # Normalise orientation/size: most downloadable models are Y-up and
        # come in arbitrary units, so fit them to a known length along +Y.
        model.set_hpr(*cfg.get("hpr", (0, 0, 0)))
        target = cfg.get("length")
        if target:
            low, high = wrapper.get_tight_bounds()
            extent = max(high - low)
            if extent > 1e-6:
                wrapper.set_scale(float(target) / extent)
        elif "scale" in cfg:
            wrapper.set_scale(float(cfg["scale"]))
        if "offset" in cfg:
            model.set_pos(*cfg["offset"])
        if cfg.get("center", True):
            low, high = wrapper.get_tight_bounds()
            mid = (low + high) * 0.5
            model.set_pos(model.get_pos() - mid / max(wrapper.get_scale()[0], 1e-6))

        if not has_normals(wrapper):
            print(
                f"[assets] {path.name} no trae normales: se dibuja sin iluminación "
                "(saldría negro). Reexpórtalo con normales para que reciba luz y sombras."
            )
            wrapper.set_light_off()

        self.report[kind] = path.name
        return wrapper

    def get(self, kind: str, color) -> NodePath:
        """Return a fresh copy of the model for ``kind`` tinted for a team."""
        if kind not in self._cache:
            self._cache[kind] = self._load_real_model(kind)
            if self._cache[kind] is None:
                self.report.setdefault(kind, "placeholder procedural")

        template = self._cache[kind]
        if template is not None:
            instance = template.copy_to(NodePath())
            if self.config.get(kind, {}).get("tint", True):
                instance.set_color_scale(*color)
            return instance

        if kind == "rifleman":
            return build_placeholder_soldier(color, launcher=False)
        if kind == "rocket":
            return build_placeholder_soldier(color, launcher=True)
        builder = {
            "helicopter": build_placeholder_helicopter,
            "osprey": build_placeholder_osprey,
            "jet": build_placeholder_jet,
            "sam": build_placeholder_sam,
        }.get(kind, build_placeholder_tank)
        return builder(color)

    def node_name(self, kind: str, role: str, default: str) -> str:
        """Name of a sub-node to animate (rotor, turret) for this model."""
        return self.config.get(kind, {}).get("nodes", {}).get(role, default)

    def print_report(self) -> None:
        if not self.verbose:
            return
        for kind, source in sorted(self.report.items()):
            print(f"[assets] {kind:<11} -> {source}")
