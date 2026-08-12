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

    The end caps work out their own winding from the geometry rather than
    assuming one. The ring helpers in this file do not agree on a direction —
    ``_hull_ring`` runs one way round the section and ``_tube_ring`` the other —
    so any fixed convention leaves half the models with inside-out caps, lit
    from behind and rendering as black holes. The destroyer's transom was one.
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

    if cap_start or cap_end:
        axis = tuple(
            sum(p[k] for p in rings[-1]) / size - sum(p[k] for p in rings[0]) / size
            for k in range(3)
        )
        if cap_start:
            order = tuple(range(size))
            # Outward at this end is against the loft axis.
            if _ring_facing(rings[0], axis) > 0.0:
                order = order[::-1]
            faces.append(order)
        if cap_end:
            base = (len(rings) - 1) * size
            order = tuple(range(base, base + size))
            if _ring_facing(rings[-1], axis) < 0.0:
                order = order[::-1]
            faces.append(order)

    return make_solid(vertices, faces, color)


def _ring_facing(ring, axis) -> float:
    """Sign of the ring's own normal projected onto `axis` (Newell's method)."""
    nx = ny = nz = 0.0
    count = len(ring)
    for i in range(count):
        x0, y0, z0 = ring[i]
        x1, y1, z1 = ring[(i + 1) % count]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    return nx * axis[0] + ny * axis[1] + nz * axis[2]


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


def _chined_ring(
    y: float,
    w_deck: float,
    w_chine: float,
    w_keel: float,
    z_deck: float,
    z_chine: float,
    z_keel: float,
):
    """Six-point hull section with a knuckle between deck edge and keel.

    A plain trapezoid cannot describe a warship's forward sections, where the
    plating flares outwards above the waterline and tucks sharply in below it.
    That knuckle line running from the stem back to amidships is most of what
    separates a destroyer's profile from a barge's.
    """
    return [
        (-w_deck, y, z_deck),
        (w_deck, y, z_deck),
        (w_chine, y, z_chine),
        (w_keel, y, z_keel),
        (-w_keel, y, z_keel),
        (-w_chine, y, z_chine),
    ]


def _lattice_mast(
    y: float,
    z_base: float,
    z_top: float,
    half_base: float,
    half_top: float,
    color,
    yards=(),
) -> NodePath:
    """A tapering mast tower with horizontal yards, standing at station `y`.

    Built in two parts because that is how the reference ships are built: an
    enclosed plated tower for the lower half, carrying the heavy arrays, and a
    thin pole above it for the whip antennas. Modelled as a single taper it
    came out looking like a flagpole.
    """
    mast = NodePath("Mast")
    z_shoulder = z_base + (z_top - z_base) * 0.46
    half_shoulder = half_base * 0.60
    # Sections must be stacked horizontally for something that runs vertically.
    # Built from XZ-plane sections instead, both rings landed in the same plane
    # and the loft degenerated into a flat plate — the mast rendered as a
    # hairline that vanished whenever the ship turned edge-on.
    make_loft(
        [
            _pod_ring(z_base, half_base * 1.30, half_base * 1.45, y, sides=6),
            _pod_ring(z_base + (z_shoulder - z_base) * 0.25, half_base, half_base * 1.20, y, sides=6),
            _pod_ring(z_shoulder, half_shoulder, half_shoulder * 1.20, y, sides=6),
        ],
        color,
    ).reparent_to(mast)
    # Upper pole.
    make_loft(
        [
            _pod_ring(z_shoulder, half_shoulder * 0.82, half_shoulder * 0.90, y, sides=6),
            _pod_ring(z_top, half_top, half_top, y, sides=6),
        ],
        color,
    ).reparent_to(mast)
    for frac, span in yards:
        z = z_base + (z_top - z_base) * frac
        make_box((span, 0.06, 0.07), color, (0, y, z)).reparent_to(mast)
        for side in (-1, 1):
            # Vertical whip at each yardarm, rooted on the yard itself. The
            # earlier version also carried angled stays; built as rotated boxes
            # they swung clear of the mast and read as debris hanging in mid-air.
            make_box((0.05, 0.05, 0.22), color,
                     (side * span * 0.90, y, z + 0.22)).reparent_to(mast)
    return mast


def _superellipse_ring(
    z: float,
    half_x: float,
    half_y: float,
    cy: float = 0.0,
    squareness: float = 2.6,
    sides: int = 16,
):
    """Rounded-rectangle outline in the XY plane at height `z`.

    ``squareness`` 2.0 gives a plain ellipse; higher values push the outline
    towards a rectangle with rounded corners. A submarine's sail is neither —
    it is a rounded slab, and an ellipse made it look like a blimp.
    """
    exponent = 2.0 / squareness
    ring = []
    for i in range(sides):
        angle = i * math.tau / sides
        c, s = math.cos(angle), math.sin(angle)
        ring.append(
            (
                half_x * math.copysign(abs(c) ** exponent, c),
                cy + half_y * math.copysign(abs(s) ** exponent, s),
                z,
            )
        )
    return ring


def _ciws_mount(grey, dark) -> NodePath:
    """A Phalanx-pattern close-in gun, muzzle along +Y.

    Three parts carry the whole silhouette and nothing else matters at battle
    range: the tall white search/track radome standing vertically with its
    domed cap, the faceted grey gun housing under it, and the black rotary
    barrel cluster cantilevered out front with its perforated muzzle shroud.
    """
    mount = NodePath("Ciws")
    white = (0.90, 0.90, 0.88, 1.0)

    # Pedestal and the training base it rotates on.
    make_box((0.34, 0.34, 0.09), _shade(grey, 0.78), (0, 0, 0.09)).reparent_to(mount)
    make_loft(
        [_pod_ring(0.18, 0.27, 0.27), _pod_ring(0.40, 0.24, 0.24)],
        _shade(grey, 0.9),
    ).reparent_to(mount)

    # Gun housing: sloped front plate, wider at the shoulders than the base.
    make_loft(
        [
            _hull_ring(-0.30, 0.26, 0.22, 1.02, 0.36),
            _hull_ring(0.14, 0.29, 0.25, 1.06, 0.36),
            _hull_ring(0.30, 0.26, 0.23, 0.92, 0.40),
        ],
        grey,
    ).reparent_to(mount)

    # Radome: vertical drum, slightly conical, with a rounded cap. This is the
    # part people recognise, so it is the tallest thing on the mount.
    make_loft(
        [
            _pod_ring(0.86, 0.25, 0.25),
            _pod_ring(1.46, 0.23, 0.23),
            _pod_ring(1.60, 0.20, 0.20),
            _pod_ring(1.68, 0.13, 0.13),
            _pod_ring(1.72, 0.05, 0.05),
        ],
        white,
    ).reparent_to(mount)
    # Banding round the drum, as in the photographs.
    for z in (1.02, 1.30):
        make_loft(
            [_pod_ring(z, 0.26, 0.26), _pod_ring(z + 0.04, 0.26, 0.26)],
            _shade(white, 0.88),
        ).reparent_to(mount)

    # Barrel cluster on its trunnion, elevated the way it sits when tracking.
    barrel = NodePath("CiwsBarrel")
    barrel.set_pos(0, 0.24, 0.80)
    barrel.set_p(16.0)
    barrel.reparent_to(mount)
    make_loft([_tube_ring(0.02, 0.13), _tube_ring(0.20, 0.12)], _shade(grey, 1.1)).reparent_to(barrel)
    make_loft([_tube_ring(0.20, 0.075), _tube_ring(0.74, 0.075)], dark).reparent_to(barrel)
    # Perforated muzzle shroud: fatter than the barrels behind it.
    make_loft([_tube_ring(0.74, 0.125), _tube_ring(1.02, 0.125)], dark).reparent_to(barrel)
    make_loft([_tube_ring(1.02, 0.10), _tube_ring(1.06, 0.10)], _shade(dark, 0.5)).reparent_to(barrel)
    # Ammunition drum slung under the trunnion.
    make_loft(
        [_tube_ring(-0.18, 0.20, z=-0.30), _tube_ring(0.16, 0.20, z=-0.30)],
        _shade(grey, 0.85),
    ).reparent_to(mount)
    return mount


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


def build_placeholder_submarine(color) -> NodePath:
    """A cruise-missile boat, bow along +Y.

    Almost all of it runs below the surface, so the shapes that matter are the
    ones that break the water: the sail with its masts and the upper curve of
    the pressure hull.
    """
    root = NodePath("submarine_placeholder")
    hull = (0.19, 0.21, 0.23, 1.0)
    dark = (0.09, 0.10, 0.12, 1.0)
    deck = (0.26, 0.28, 0.30, 1.0)
    axis = -0.55  # centreline height, kept from the original placeholder

    # Pressure hull. Sixteen-sided sections instead of eight: a submarine is
    # one long unbroken curve, and at eight sides the facet edges caught the
    # light hard enough that the boat read as a hexagonal pencil. The extra
    # stations forward give a proper ogive nose rather than a blunt cone.
    make_loft(
        [
            _round_ring(26.4, 0.16, 0.16, axis, sides=16),
            _round_ring(25.6, 0.88, 0.88, axis, sides=16),
            _round_ring(24.0, 1.48, 1.48, axis, sides=16),
            _round_ring(21.5, 2.06, 2.06, axis, sides=16),
            _round_ring(18.0, 2.56, 2.56, axis, sides=16),
            _round_ring(13.5, 2.90, 2.90, axis, sides=16),
            _round_ring(7.0, 3.05, 3.05, axis, sides=16),
            _round_ring(0.0, 3.06, 3.06, axis, sides=16),
            _round_ring(-8.0, 3.02, 3.02, axis, sides=16),
            _round_ring(-14.0, 2.74, 2.74, axis, sides=16),
            _round_ring(-19.0, 2.12, 2.12, axis, sides=16),
            _round_ring(-22.5, 1.44, 1.44, axis, sides=16),
            _round_ring(-24.8, 0.80, 0.80, axis, sides=16),
        ],
        hull,
    ).reparent_to(root)
    # Casing: the narrow flat walking deck along the top of the hull. Sections
    # taken fore-and-aft, not stacked in height — built the other way round it
    # sheared into a flat wing hanging off the bow.
    make_loft(
        [
            _hull_ring(21.5, 0.42, 0.62, axis + 2.62, axis + 2.40),
            _hull_ring(15.0, 0.88, 1.06, axis + 2.96, axis + 2.74),
            _hull_ring(4.0, 1.02, 1.20, axis + 3.08, axis + 2.86),
            _hull_ring(-9.0, 0.96, 1.14, axis + 3.04, axis + 2.82),
            _hull_ring(-15.5, 0.62, 0.80, axis + 2.80, axis + 2.60),
        ],
        _shade(hull, 1.14),
    ).reparent_to(root)

    # Sail: a rounded slab, tapering as it rises, with a faired leading edge.
    make_loft(
        [
            _superellipse_ring(axis + 2.40, 1.16, 4.30, 3.00, 2.8, 16),
            _superellipse_ring(axis + 4.60, 1.06, 4.10, 3.10, 3.0, 16),
            _superellipse_ring(axis + 6.20, 0.90, 3.70, 3.20, 3.2, 16),
            _superellipse_ring(axis + 6.75, 0.66, 3.30, 3.25, 3.2, 16),
        ],
        deck,
    ).reparent_to(root)
    # Sail planes: thin swept fins, thicker at the root than the tip.
    # A control surface is a blade: thin across, broad in chord, tapering
    # outboard. Sections are stacked along the span and the whole blade is then
    # rolled into place, which is the only way to get that from lofted rings.
    def _blade(chord_root, chord_tip, span, thick, taper_shift=0.0):
        return make_loft(
            [
                _pod_ring(0.0, thick, chord_root, 0.0, sides=8),
                _pod_ring(span * 0.62, thick * 0.74, chord_root * 0.80,
                          taper_shift * 0.62, sides=8),
                _pod_ring(span, thick * 0.42, chord_tip, taper_shift, sides=8),
            ],
            deck,
        )

    for side in (-1, 1):
        plane = _blade(1.35, 0.78, 3.05, 0.15, -0.35)
        plane.set_pos(0, 0.60, axis + 4.55)
        plane.set_r(side * 90.0)
        plane.reparent_to(root)
    # Masts and the periscope fairing on top of the sail.
    make_loft([_pod_ring(axis + 6.75, 0.12, 0.12, 3.9), _pod_ring(axis + 9.20, 0.09, 0.09, 3.9)],
              dark).reparent_to(root)
    make_loft([_pod_ring(axis + 6.75, 0.10, 0.10, 2.3), _pod_ring(axis + 8.40, 0.07, 0.07, 2.3)],
              dark).reparent_to(root)
    make_box((0.60, 0.60, 0.16), _shade(color, 1.1), (0, 1.10, axis + 6.85)).reparent_to(root)

    # Vertical launch hatches on the foredeck, each on its own hinge so the
    # launch effect can throw the lids open before the missiles come out.
    for i in range(6):
        for side in (-1, 1):
            hatch = NodePath(f"LaunchHatch{i}{'S' if side > 0 else 'P'}")
            hatch.set_pos(side * 1.02, 18.6 - i * 1.55, axis + 3.02)
            hatch.reparent_to(root)
            make_loft(
                [_pod_ring(-0.05, 0.56, 0.56), _pod_ring(0.06, 0.56, 0.56)],
                _shade(hull, 0.8),
            ).reparent_to(hatch)
            # The bore below, visible once the lid swings clear.
            make_loft(
                [_pod_ring(-0.90, 0.44, 0.44), _pod_ring(0.04, 0.44, 0.44)],
                (0.03, 0.03, 0.04, 1.0),
            ).reparent_to(hatch)
            hinge = hatch.attach_new_node("Hinge")
            hinge.set_pos(0, -0.50, 0.06)
            lid = make_loft(
                [_pod_ring(0.0, 0.50, 0.50, 0.50), _pod_ring(0.09, 0.50, 0.50, 0.50)],
                _shade(deck, 1.15),
            )
            lid.reparent_to(hinge)

    # Stern control surfaces in X form, as on the reference boat, plus the
    # shrouded pump-jet propulsor.
    for i in range(4):
        fin = _blade(2.30, 1.15, 4.90, 0.20, -1.60)
        fin.set_pos(0, -18.2, axis)
        fin.set_r(45.0 + i * 90.0)
        fin.reparent_to(root)
    make_loft(
        [
            _round_ring(-24.6, 1.05, 1.05, axis, sides=14),
            _round_ring(-26.4, 1.16, 1.16, axis, sides=14),
            _round_ring(-27.0, 1.10, 1.10, axis, sides=14),
        ],
        _shade(hull, 0.85),
    ).reparent_to(root)
    make_loft(
        [_round_ring(-25.0, 0.46, 0.46, axis, sides=10),
         _round_ring(-26.6, 0.30, 0.30, axis, sides=10)],
        dark,
    ).reparent_to(root)

    return root


def build_placeholder_destroyer(color) -> NodePath:
    """A modern guided-missile destroyer, bow pointing along +Y.

    The silhouette prioritises the features that distinguish a destroyer at
    battle-camera distance: a long chined hull, raised bridge, radar mast,
    gun turret and two banks of vertical-launch cells.
    """
    root = NodePath("destroyer_placeholder")
    # Built at a convenient working size and scaled at the end. The shape that
    # reads as "warship" is mostly one number: length over beam. Real
    # destroyers run about 8:1 (Arleigh Burke 155 x 20 m), and at the 4:3:1
    # this hull used to have it looked like a tug no matter how much detail
    # was piled on top. Everything below is laid out to keep that ratio.
    scale = 2.15
    # Naval grey stays distinct from both the blue sea and the red/blue team
    # colours. Small identification panels carry the team colour instead.
    # Haze grey, as in the photographs. The first pass used a 0.34 hull, which
    # under this lighting came out almost black and swallowed every panel line
    # on it; real warship grey is light, and it is the dark boot topping and
    # the darker deck that give the hull its contrast.
    hull = (0.54, 0.57, 0.59, 1.0)
    boot = (0.12, 0.14, 0.16, 1.0)
    deck = (0.40, 0.43, 0.45, 1.0)
    superstructure = (0.58, 0.61, 0.63, 1.0)
    dark = (0.16, 0.18, 0.21, 1.0)
    radar = (0.38, 0.46, 0.52, 1.0)

    # Hull: 32.6 long by 4.2 in the beam, so 7.8:1. The knuckle line flares
    # out above the waterline forward and tucks under aft, and the sheer lifts
    # a full metre towards the stem — the raked bow of the reference ships.
    stations = [
        # y,     deck,  chine, keel,  z_deck, z_chine, z_keel
        (17.00, 0.10, 0.09, 0.05, 1.62, 0.30, -0.50),
        (15.40, 0.62, 0.46, 0.16, 1.42, 0.02, -0.95),
        (13.20, 1.18, 0.94, 0.40, 1.18, -0.14, -1.18),
        (10.00, 1.76, 1.52, 0.78, 0.92, -0.26, -1.30),
        (6.00, 2.04, 1.88, 1.14, 0.74, -0.32, -1.34),
        (1.00, 2.10, 2.02, 1.40, 0.62, -0.34, -1.34),
        (-5.00, 2.10, 2.02, 1.42, 0.58, -0.34, -1.32),
        (-10.50, 2.00, 1.90, 1.30, 0.56, -0.32, -1.24),
        (-14.20, 1.82, 1.74, 1.40, 0.54, -0.30, -0.90),
        (-15.60, 1.72, 1.68, 1.54, 0.54, -0.34, -0.60),
    ]
    make_loft([_chined_ring(*s) for s in stations], hull).reparent_to(root)
    # Boot topping: the dark band at the waterline. Cheap, and it does more for
    # reading the hull as floating than any amount of superstructure detail.
    # Its lower edge has to follow the hull inwards — carried at constant width
    # it stood proud of the tuck aft and hung off the stern as a dark blade.
    boot_rings = []
    for _y, _deck, chine, keel, _zd, z_chine, z_keel in stations[1:]:
        drop = 0.30
        blend = min(1.0, drop / max(0.05, z_chine - z_keel))
        # Stop just short of the transom: run flush to it and the band's end
        # cap lands exactly on the transom plate and the two z-fight.
        boot_rings.append(
            _hull_ring(
                _y + (0.22 if _y <= stations[-1][0] else 0.0),
                chine * 1.004,
                (chine + (keel - chine) * blend) * 1.004,
                z_chine + 0.15,
                z_chine - drop,
            )
        )
    make_loft(boot_rings, boot).reparent_to(root)

    # Main deck, following the hull outline instead of a rectangle laid on top.
    make_loft(
        [
            _hull_ring(y, w * 0.97, w * 0.97, z_deck + 0.02, z_deck - 0.10)
            for y, w, _, _, z_deck, _, _ in stations
        ],
        deck,
    ).reparent_to(root)
    # Deck edge and the breakwater across the forecastle.
    for side in (-1, 1):
        make_box((0.09, 15.0, 0.16), _shade(hull, 1.18),
                 (side * 2.06, -1.0, 0.62)).reparent_to(root)
    make_box((2.9, 0.16, 0.34), deck, (0, 12.4, 0.98)).reparent_to(root)

    # Forward gun in an independently turning mount, on a raised bandstand.
    make_loft(
        [_hull_ring(12.9, 1.35, 1.45, 0.92, 0.60), _hull_ring(10.1, 1.55, 1.65, 0.92, 0.72)],
        deck,
    ).reparent_to(root)
    turret = NodePath("Turret")
    turret.set_pos(0, 11.5, 0.92)
    turret.reparent_to(root)
    # Faceted shield: sloped front plate, like the OTO Melara of the reference.
    make_loft(
        [
            _hull_ring(-1.45, 1.10, 1.18, 1.30, 0.02),
            _hull_ring(0.35, 1.04, 1.14, 1.42, 0.02),
            _hull_ring(1.30, 0.68, 0.86, 1.02, 0.04),
            _hull_ring(1.72, 0.34, 0.48, 0.86, 0.06),
        ],
        superstructure,
    ).reparent_to(turret)
    # Barrel with the fume extractor bulge that makes a naval gun recognisable.
    make_loft(
        [_tube_ring(1.30, 0.19, z=0.66), _tube_ring(3.60, 0.12, z=0.66)],
        dark,
    ).reparent_to(turret)
    make_loft(
        [_tube_ring(1.95, 0.26, z=0.66), _tube_ring(2.55, 0.26, z=0.66)],
        _shade(dark, 1.5),
    ).reparent_to(turret)

    # Vertical launch cells, forward and aft: flush hatches in a low armoured
    # deck module, the way a Mk 41 farm actually sits. They only needed to be
    # big enough to pick out — the first version was a 45 cm dark square that
    # vanished at any distance.
    for y, rows, z_deck in ((8.4, 4, 0.87), (-9.10, 2, 0.58)):
        depth = rows * 0.66
        make_loft(
            [
                _hull_ring(y + 0.40, 1.02, 1.08, z_deck + 0.15, z_deck - 0.16),
                _hull_ring(y - depth, 1.02, 1.08, z_deck + 0.15, z_deck - 0.16),
            ],
            _shade(deck, 0.92),
        ).reparent_to(root)
        for row in range(rows):
            cy = y - row * 0.66
            for x in (-0.48, 0.48):
                # Hatch leaf, sitting in its dark surround.
                make_box((0.46, 0.30, 0.02), dark, (x, cy, 0.16 + z_deck)).reparent_to(root)
                make_box((0.40, 0.25, 0.03), _shade(superstructure, 0.88),
                         (x, cy, 0.17 + z_deck)).reparent_to(root)
            make_box((1.02, 0.025, 0.025), dark, (0, cy + 0.33, 0.17 + z_deck)).reparent_to(root)

    # Anti-ship missile canisters in raked deck boxes, abaft the funnels. The
    # single most recognisable "this thing launches missiles" feature there is,
    # and the reason a corvette reads as armed from a kilometre away.
    for side in (-1, 1):
        battery = NodePath("MissileBattery")
        battery.set_pos(side * 0.92, -7.00, 2.16)
        battery.set_p(14.0)
        battery.set_h(side * 18.0)
        battery.reparent_to(root)
        make_box((0.46, 0.86, 0.10), _shade(superstructure, 0.8), (0, 0.10, -0.10)).reparent_to(battery)
        for cx in (-0.21, 0.21):
            for cz in (0.0, 0.43):
                make_loft(
                    [_tube_ring(-0.62, 0.195, x=cx, z=cz), _tube_ring(0.86, 0.195, x=cx, z=cz)],
                    _shade(superstructure, 0.92),
                ).reparent_to(battery)
                # Frangible cover on the muzzle end, and a banding strap.
                make_loft(
                    [_tube_ring(0.86, 0.175, x=cx, z=cz), _tube_ring(0.92, 0.175, x=cx, z=cz)],
                    (0.30, 0.28, 0.26, 1.0),
                ).reparent_to(battery)
                make_loft(
                    [_tube_ring(0.10, 0.21, x=cx, z=cz), _tube_ring(0.18, 0.21, x=cx, z=cz)],
                    _shade(superstructure, 0.72),
                ).reparent_to(battery)

    # Superstructure as ONE continuous stepped block from frame 8 to frame -9.
    # Previously it was three separate lumps with gaps of open deck between
    # them, which is what made the ship look assembled out of spare crates.
    make_loft(
        [
            _hull_ring(8.20, 1.30, 1.42, 2.05, 0.58),
            _hull_ring(6.20, 1.62, 1.74, 2.15, 0.56),
            _hull_ring(-4.00, 1.66, 1.78, 2.15, 0.56),
            _hull_ring(-6.60, 1.58, 1.70, 2.10, 0.56),
            _hull_ring(-8.20, 1.44, 1.54, 2.00, 0.56),
        ],
        deck,
    ).reparent_to(root)
    # 01 level, then the bridge on top of it: each tier steps inboard.
    make_loft(
        [
            _hull_ring(6.40, 1.12, 1.26, 3.10, 2.05),
            _hull_ring(4.60, 1.34, 1.46, 3.16, 2.05),
            _hull_ring(-3.60, 1.36, 1.48, 3.16, 2.05),
            _hull_ring(-6.60, 1.20, 1.32, 3.02, 2.05),
        ],
        superstructure,
    ).reparent_to(root)
    # Bridge tower. Deliberately narrower and a good deal taller than the tier
    # below: the reference ships read as a long low hull with one tall block
    # forward, and when every tier was the same width the whole deckhouse
    # flattened into a single slab.
    make_loft(
        [
            _hull_ring(5.70, 0.86, 1.00, 4.30, 3.16),
            _hull_ring(4.40, 1.08, 1.18, 4.36, 3.16),
            _hull_ring(2.30, 1.04, 1.14, 4.30, 3.16),
        ],
        superstructure,
    ).reparent_to(root)
    # Pilot house on top, stepped in again, with its own roof.
    make_loft(
        [
            _hull_ring(5.20, 0.72, 0.84, 5.28, 4.36),
            _hull_ring(4.30, 0.90, 0.98, 5.32, 4.36),
            _hull_ring(3.00, 0.86, 0.94, 5.26, 4.36),
        ],
        _shade(superstructure, 0.94),
    ).reparent_to(root)
    # Wraparound bridge windows: front band plus both wings, on both levels.
    for y_front, x_wing, y_wing, z, span in ((5.66, 1.02, 4.00, 3.92, 0.90),
                                             (5.16, 0.86, 4.10, 4.92, 0.76)):
        make_box((span, 0.10, 0.32), dark, (0, y_front, z)).reparent_to(root)
        for side in (-1, 1):
            make_box((0.10, 1.30, 0.32), dark, (side * x_wing, y_wing, z)).reparent_to(root)
    for side in (-1, 1):
        # Bridge wing, overhanging the tier below the way real ones do.
        make_box((0.40, 0.32, 0.06), deck, (side * 1.38, 4.40, 4.38)).reparent_to(root)
        make_box((0.05, 0.32, 0.20), dark, (side * 1.74, 4.40, 4.58)).reparent_to(root)

    # Deckhouse fittings. Without these the 17-unit-long block reads as one
    # smooth slab; the reference ships are covered in small hard edges.
    for side in (-1, 1):
        # Life-raft canisters in their racks along the side.
        for y in (7.20, 5.10, -2.20, -4.60, -8.30):
            make_loft(
                [_tube_ring(y - 0.28, 0.16, x=side * 1.72, z=1.35),
                 _tube_ring(y + 0.28, 0.16, x=side * 1.72, z=1.35)],
                (0.72, 0.73, 0.70, 1.0),
            ).reparent_to(root)
        # Watertight doors and the outboard walkway they open onto.
        for y in (6.40, 0.60, -6.20):
            make_box((0.05, 0.24, 0.36), _shade(dark, 1.6),
                     (side * 1.68, y, 0.94)).reparent_to(root)
        make_box((0.10, 8.60, 0.05), _shade(deck, 1.1),
                 (side * 1.76, -0.60, 0.60)).reparent_to(root)
        # Boat davit and the RHIB it carries, amidships.
        make_box((0.06, 0.06, 0.62), _shade(superstructure, 0.8),
                 (side * 1.60, -1.30, 2.46)).reparent_to(root)
        rhib = make_loft(
            [
                _hull_ring(-2.30, 0.16, 0.10, 2.34, 2.10),
                _hull_ring(-1.10, 0.22, 0.14, 2.36, 2.06),
                _hull_ring(-0.20, 0.10, 0.06, 2.32, 2.14),
            ],
            (0.24, 0.26, 0.28, 1.0),
        )
        rhib.set_x(side * 1.62)
        rhib.reparent_to(root)
    # Twin raked uptakes between the masts.
    for y in (-0.30, -3.40):
        make_loft(
            [
                _hull_ring(y + 0.68, 0.62, 0.70, 5.05, 3.10),
                _hull_ring(y - 0.68, 0.52, 0.62, 5.35, 3.10),
            ],
            superstructure,
        ).reparent_to(root)
        # Dark uptake cap, recessed inside the casing rim.
        make_box((0.54, 0.60, 0.06), dark, (0, y - 0.14, 5.22)).reparent_to(root)
        make_box((0.60, 0.66, 0.05), _shade(superstructure, 1.1),
                 (0, y - 0.14, 5.28)).reparent_to(root)

    # Two masts, tall and slender. This is the other half of the silhouette:
    # the reference ships carry a foremast abaft the bridge and a mainmast aft,
    # both rising well clear of everything else.
    mast_grey = _shade(superstructure, 0.62)
    _lattice_mast(1.60, 3.16, 10.40, 0.72, 0.15, mast_grey,
                  yards=((0.62, 1.35), (0.80, 0.92), (0.93, 0.55))).reparent_to(root)
    _lattice_mast(-5.60, 3.02, 8.20, 0.58, 0.13, mast_grey,
                  yards=((0.64, 1.05), (0.86, 0.62))).reparent_to(root)

    # Phased-array faces on the bridge front, air-search antenna up top.
    for face_y, face_x, heading in ((5.52, 0.0, 0.0), (4.10, 1.02, 82.0), (4.10, -1.02, -82.0)):
        panel = make_box((0.46, 0.07, 0.46), radar, (0, 0, 0))
        panel.set_pos(face_x, face_y, 3.40)
        panel.set_h(heading)
        panel.set_p(-12.0)
        panel.reparent_to(root)
    # Air-search antenna. Named so the battle code can turn it: a warship with
    # a dead radar looks like a model on a shelf, and it is the one moving part
    # visible from any distance.
    # Platform carrying it, so the antenna is visibly borne by the mast rather
    # than floating above it.
    make_loft(
        [_pod_ring(7.42, 0.44, 0.44, 1.60), _pod_ring(7.54, 0.40, 0.40, 1.60)],
        mast_grey,
    ).reparent_to(root)
    rotator = NodePath("AirSearchRadar")
    rotator.set_pos(0, 1.60, 7.86)
    rotator.set_h(24.0)
    rotator.reparent_to(root)
    make_box((1.55, 0.10, 0.30), radar, (0, 0, 0)).reparent_to(rotator)
    # Curved backing frame, so the sweep is legible as it turns edge-on.
    for x in (-1.30, -0.65, 0.0, 0.65, 1.30):
        make_box((0.05, 0.16, 0.34), _shade(radar, 0.7), (x, -0.10, 0)).reparent_to(rotator)
    make_box((0.22, 0.22, 0.14), dark, (0, 0, -0.22)).reparent_to(rotator)
    make_box((0.66, 0.10, 0.66), radar, (0, -5.60, 7.50)).reparent_to(root)
    # Fire-control directors with their radomes: main one on the pilot house
    # roof, the aft one on the boat deck.
    for y, z in ((4.20, 5.32), (-7.30, 3.02)):
        make_box((0.30, 0.30, 0.22), superstructure, (0, y, z)).reparent_to(root)
        make_box((0.36, 0.36, 0.34), (0.80, 0.81, 0.79, 1.0), (0, y, z + 0.28)).reparent_to(root)

    # Close-in weapon stations: one over the bridge, two on aft sponsons. Each
    # is a named node so the battle code can find it, and each is built at a
    # size you can actually pick out — a Phalanx is barely a metre across in
    # real terms, which at this scale would be a single invisible pixel.
    # One forward over the bridge and one aft on the hangar roof, the Burke
    # arrangement: between them they cover the whole horizon.
    for name, x, y, z, heading in (
        ("CiwsFwd", 0.0, 7.40, 2.15, 0.0),
        ("CiwsAft", 0.0, -8.30, 2.00, 180.0),
    ):
        mount = _ciws_mount(superstructure, dark)
        mount.set_name(name)
        mount.set_pos(x, y, z)
        mount.set_h(heading)
        mount.reparent_to(root)
        # Sponson carrying the mount clear of the deckhouse side.
        make_box((0.44, 0.44, 0.07), deck, (x, y, z - 0.04)).reparent_to(root)

    # Flight deck aft, following the hull line, with its H and a hangar door.
    make_loft(
        [
            _hull_ring(-10.60, 1.96, 1.96, 0.62, 0.50),
            _hull_ring(-13.20, 1.88, 1.88, 0.60, 0.48),
            _hull_ring(-15.40, 1.70, 1.70, 0.58, 0.46),
        ],
        (0.24, 0.26, 0.27, 1.0),
    ).reparent_to(root)
    make_box((1.20, 0.10, 0.62), _shade(deck, 0.85), (0, -9.90, 1.20)).reparent_to(root)
    flight_mark = (0.88, 0.88, 0.82, 1.0)
    make_box((0.12, 1.05, 0.03), flight_mark, (0, -13.00, 0.64)).reparent_to(root)
    make_box((0.72, 0.12, 0.03), flight_mark, (0, -12.30, 0.64)).reparent_to(root)
    make_box((0.72, 0.12, 0.03), flight_mark, (0, -13.70, 0.64)).reparent_to(root)

    # Team recognition panels: readable at battle range without tinting the
    # whole hull, on the bow where the pennant number sits on a real ship.
    for side in (-1, 1):
        make_box((0.10, 1.90, 0.42), color, (side * 1.94, 9.60, 0.30)).reparent_to(root)

    root.set_scale(scale)
    return root


def _pod_ring(z: float, half_x: float, half_y: float, cy: float = 0.0, sides: int = 8):
    """Elliptical section in the XY plane at height `z`.

    For anything whose long axis runs vertically — the tilting nacelles are
    built upright and then pitched forward, so their sections stack in Z.
    """
    return [
        (
            math.cos(i * math.tau / sides) * half_x,
            cy + math.sin(i * math.tau / sides) * half_y,
            z,
        )
        for i in range(sides)
    ]


def build_placeholder_osprey(color) -> NodePath:
    """A V-22 shaped tiltrotor, nose along +Y.

    Modelled from sections like the rest: the deep fuselage with its drooping
    nose, the shoulder wing, the outsized wingtip nacelles and the canted
    H-tail are what make it read as an Osprey rather than a generic transport.
    The nacelles stay named nodes so the flight code can still swing them.
    """
    root = NodePath("osprey_placeholder")
    dark = (0.14, 0.15, 0.17, 1.0)
    glass = (0.21, 0.31, 0.37, 1.0)
    grey = (0.44, 0.45, 0.47, 1.0)
    panel = _shade(color, 0.9)

    # Fuselage: blunt drooping nose, deep cabin, ramp cut away at the back.
    make_loft(
        [
            _round_ring(7.90, 0.42, 0.38, -0.55),
            _round_ring(7.10, 0.78, 0.70, -0.40),
            _round_ring(5.70, 1.06, 1.00, -0.16),
            _round_ring(3.90, 1.24, 1.22, 0.02),
            _round_ring(1.60, 1.34, 1.34, 0.06),
            _round_ring(-1.60, 1.34, 1.34, 0.06),
            _round_ring(-4.40, 1.26, 1.28, 0.14),
            _round_ring(-6.40, 1.06, 1.10, 0.42),
            _round_ring(-7.90, 0.80, 0.82, 0.86),
        ],
        color,
    ).reparent_to(root)

    # Stepped flight deck glazing.
    make_loft(
        [
            _round_ring(7.05, 0.60, 0.34, 0.42),
            _round_ring(6.10, 0.92, 0.52, 0.62),
            _round_ring(4.60, 0.98, 0.54, 0.72),
            _round_ring(3.90, 0.80, 0.42, 0.66),
        ],
        glass,
    ).reparent_to(root)

    # Rear ramp, dropped open under the tail cone.
    make_box((1.9, 2.2, 0.16), panel, (0, -7.0, -0.62)).reparent_to(root)

    # Sponsons: the fat fairings along the belly that hold the gear.
    for side in (-1, 1):
        sponson = make_loft(
            [
                _round_ring(3.10, 0.34, 0.42, -0.62),
                _round_ring(0.60, 0.52, 0.58, -0.72),
                _round_ring(-2.60, 0.44, 0.50, -0.66),
            ],
            panel,
        )
        sponson.set_x(side * 1.28)
        sponson.reparent_to(root)

    # Shoulder wing, slightly swept forward, carrying both nacelles.
    for side in (-1, 1):
        wing = make_loft(
            [
                _airfoil_ring(side * 1.20, 1.55, -1.65, 0.62),
                _airfoil_ring(side * 4.20, 1.70, -1.45, 0.50),
                _airfoil_ring(side * 6.30, 1.80, -1.30, 0.40),
            ],
            panel,
        )
        wing.set_pos(0, -0.20, 1.42)
        wing.reparent_to(root)

    # H-tail: horizontal stabiliser with the fins canted out at its tips.
    tailplane = make_loft(
        [
            _airfoil_ring(-3.30, -5.90, -7.70, 0.34),
            _airfoil_ring(0.00, -6.10, -7.90, 0.42),
            _airfoil_ring(3.30, -5.90, -7.70, 0.34),
        ],
        panel,
    )
    tailplane.set_z(1.15)
    tailplane.reparent_to(root)
    for side in (-1, 1):
        fin = make_loft(
            [
                _airfoil_ring(0.00, -5.70, -7.90, 0.40),
                _airfoil_ring(2.60, -6.55, -7.85, 0.20),
            ],
            panel,
        )
        fin.set_pos(side * 3.25, 0, 1.30)
        fin.set_r(side * -74.0)
        fin.reparent_to(root)

    # Nacelles: built upright so the flight code can pitch them forward.
    for side in (-1, 1):
        nacelle = NodePath(f"Nacelle{'Left' if side < 0 else 'Right'}")
        nacelle.set_pos(side * 6.55, -0.20, 1.55)
        nacelle.reparent_to(root)

        make_loft(
            [
                _pod_ring(-1.55, 0.62, 0.98, -0.10),
                _pod_ring(-0.40, 0.86, 1.30, 0.00),
                _pod_ring(1.30, 0.88, 1.28, 0.05),
                _pod_ring(2.45, 0.66, 0.86, 0.10),
                _pod_ring(2.95, 0.40, 0.48, 0.10),
            ],
            grey,
        ).reparent_to(nacelle)
        # Exhaust under the pod and the spinner on top.
        make_loft(
            [_pod_ring(-1.55, 0.50, 0.78, -0.10), _pod_ring(-2.05, 0.34, 0.54, -0.14)],
            dark,
        ).reparent_to(nacelle)
        make_loft(
            [_pod_ring(2.95, 0.34, 0.34), _pod_ring(3.35, 0.16, 0.16)],
            dark,
        ).reparent_to(nacelle)

        proprotor = NodePath("Proprotor")
        proprotor.set_pos(0, 0, 3.05)
        proprotor.reparent_to(nacelle)
        # Single-ended blades at 120 degrees: a symmetric box counts twice and
        # a three-blade rotor comes out with six.
        for i in range(3):
            blade = make_box((0.44, 5.60, 0.13), dark, (0, 2.85, 0))
            blade.set_h(i * 120.0)
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
            "destroyer": build_placeholder_destroyer,
            "submarine": build_placeholder_submarine,
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
