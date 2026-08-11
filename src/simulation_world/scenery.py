"""Procedural low-poly vegetation scattered over the terrain.

Every tree is merged into a single Geom, so a whole forest costs one draw
call. Trees are decoration only — they are never added to the Bullet world,
because giving hundreds of them colliders would block the line-of-sight
raycasts everywhere and stall the battle.
"""

from __future__ import annotations

import math

import numpy as np
from panda3d.core import (
    Geom,
    GeomNode,
    GeomTriangles,
    GeomVertexData,
    GeomVertexFormat,
    GeomVertexWriter,
    NodePath,
)

_TRUNK_COLORS = (
    (0.34, 0.25, 0.17),
    (0.29, 0.21, 0.14),
    (0.38, 0.29, 0.20),
)
_LEAF_COLORS = (
    (0.19, 0.42, 0.19),
    (0.24, 0.50, 0.23),
    (0.15, 0.35, 0.17),
    (0.30, 0.54, 0.26),
    (0.42, 0.48, 0.20),
)


class _MeshBuilder:
    """Accumulates low-poly organic solids into one flat-shaded Geom."""

    def __init__(self, name: str) -> None:
        fmt = GeomVertexFormat.get_v3n3c4()
        self.vdata = GeomVertexData(name, fmt, Geom.UH_static)
        self.vertex = GeomVertexWriter(self.vdata, "vertex")
        self.normal = GeomVertexWriter(self.vdata, "normal")
        self.color = GeomVertexWriter(self.vdata, "color")
        self.prim = GeomTriangles(Geom.UH_static)
        self.index = 0
        self.name = name

    @staticmethod
    def _cross(a, b):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    @staticmethod
    def _normalize(vector):
        length = math.sqrt(sum(value * value for value in vector))
        return tuple(value / max(length, 1e-9) for value in vector)

    def _face(self, points, rgb) -> None:
        if len(points) < 3:
            return
        edge_a = tuple(points[1][axis] - points[0][axis] for axis in range(3))
        edge_b = tuple(points[2][axis] - points[0][axis] for axis in range(3))
        nrm = self._normalize(self._cross(edge_a, edge_b))
        for point in points:
            self.vertex.add_data3(*point)
            self.normal.add_data3(*nrm)
            self.color.add_data4(rgb[0], rgb[1], rgb[2], 1.0)
        for step in range(1, len(points) - 1):
            self.prim.add_vertices(self.index, self.index + step, self.index + step + 1)
        self.index += len(points)

    def add_tapered_segment(
        self, start, end, radius_start: float, radius_end: float, rgb,
        segments: int = 7,
    ) -> None:
        axis = tuple(end[i] - start[i] for i in range(3))
        direction = self._normalize(axis)
        reference = (0.0, 0.0, 1.0) if abs(direction[2]) < 0.88 else (1.0, 0.0, 0.0)
        side = self._normalize(self._cross(direction, reference))
        up = self._cross(side, direction)

        rings = []
        for centre, radius in ((start, radius_start), (end, radius_end)):
            ring = []
            for step in range(segments):
                angle = math.tau * step / segments
                ring.append(tuple(
                    centre[axis]
                    + side[axis] * math.cos(angle) * radius
                    + up[axis] * math.sin(angle) * radius
                    for axis in range(3)
                ))
            rings.append(ring)
        for step in range(segments):
            nxt = (step + 1) % segments
            self._face(
                [rings[0][step], rings[0][nxt], rings[1][nxt], rings[1][step]],
                rgb,
            )

    def add_cone(self, base, radius: float, height: float, rgb, heading: float) -> None:
        segments = 8
        ring = [
            (
                base[0] + math.cos(heading + math.tau * step / segments) * radius,
                base[1] + math.sin(heading + math.tau * step / segments) * radius,
                base[2],
            )
            for step in range(segments)
        ]
        apex = (base[0], base[1], base[2] + height)
        for step in range(segments):
            nxt = (step + 1) % segments
            self._face([ring[step], ring[nxt], apex], rgb)
        self._face(list(reversed(ring)), rgb)

    def add_ellipsoid(self, center, radii, rgb, heading: float, jitter) -> None:
        """Rounded irregular crown with three belts of low-poly foliage."""
        segments = len(jitter)
        rings = []
        for ring_index, (z_fraction, radius_fraction) in enumerate(
            ((-0.55, 0.78), (-0.04, 1.0), (0.48, 0.76))
        ):
            ring = []
            for step in range(segments):
                angle = heading + math.tau * step / segments + ring_index * 0.10
                variation = jitter[(step + ring_index) % segments]
                ring.append((
                    center[0] + math.cos(angle) * radii[0] * radius_fraction * variation,
                    center[1] + math.sin(angle) * radii[1] * radius_fraction * variation,
                    center[2] + radii[2] * z_fraction,
                ))
            rings.append(ring)
        bottom = (center[0], center[1], center[2] - radii[2] * 0.92)
        top = (center[0], center[1], center[2] + radii[2] * 0.94)
        for step in range(segments):
            nxt = (step + 1) % segments
            self._face([bottom, rings[0][nxt], rings[0][step]], rgb)
            for ring_index in range(len(rings) - 1):
                self._face(
                    [
                        rings[ring_index][step], rings[ring_index][nxt],
                        rings[ring_index + 1][nxt], rings[ring_index + 1][step],
                    ],
                    rgb,
                )
            self._face([top, rings[-1][step], rings[-1][nxt]], rgb)

    def add_leaf(
        self, center, length: float, width: float, heading: float,
        tilt: float, rgb,
    ) -> None:
        """Add one opaque, pointed leaf as a cheap two-triangle diamond."""
        forward = (
            math.cos(heading) * math.cos(tilt),
            math.sin(heading) * math.cos(tilt),
            math.sin(tilt),
        )
        side = (-math.sin(heading), math.cos(heading), 0.0)
        tip = tuple(center[axis] + forward[axis] * length * 0.58 for axis in range(3))
        tail = tuple(center[axis] - forward[axis] * length * 0.42 for axis in range(3))
        left = tuple(center[axis] + side[axis] * width * 0.5 for axis in range(3))
        right = tuple(center[axis] - side[axis] * width * 0.5 for axis in range(3))
        self._face([tip, left, tail, right], rgb)

    def build(self) -> NodePath:
        geom = Geom(self.vdata)
        geom.add_primitive(self.prim)
        node = GeomNode(self.name)
        node.add_geom(geom)
        result = NodePath(node)
        # Leaf diamonds must remain visible from above and below as the free
        # camera moves through the canopy.
        result.set_two_sided(True)
        return result


def build_chunk_forest(terrain, cx: int, cy: int, count: int) -> NodePath | None:
    """Scatter trees over one terrain chunk.

    Seeded from the chunk coordinates, so a chunk regrows exactly the same
    forest every time it streams back in instead of shuffling on each visit.
    """
    rng = np.random.default_rng((terrain.seed * 8191 + cx * 73856093 + cy * 19349663) & 0x7FFFFFFF)
    builder = _MeshBuilder(f"forest{cx}_{cy}")
    ox, oy = terrain.chunk_origin(cx, cy)
    size = terrain.chunk_size
    water = terrain.water_level
    tree_line = water + terrain.relief * 0.55
    planted = 0

    for _ in range(count * 3):
        if planted >= count:
            break
        x = float(ox + rng.uniform(0.0, size))
        y = float(oy + rng.uniform(0.0, size))
        height = terrain.height_at(x, y)
        # No trees in the water, on the beach, or above the treeline.
        if height < water + 1.5 or height > tree_line:
            continue
        if terrain.normal_at(x, y).z < 0.86:  # too steep to hold soil
            continue
        # Thin out towards the treeline so the edge fades instead of cutting.
        altitude = (height - water) / max(tree_line - water, 1e-6)
        if rng.random() > 1.0 - altitude**2 * 0.85:
            continue

        scale = float(rng.uniform(0.72, 1.48))
        heading = float(rng.uniform(0.0, math.pi * 2))
        trunk = _TRUNK_COLORS[int(rng.integers(len(_TRUNK_COLORS)))]
        leaf = _LEAF_COLORS[int(rng.integers(len(_LEAF_COLORS)))]
        crown_light = tuple(min(1.0, color * 1.10) for color in leaf)
        tree_roll = float(rng.random())

        if tree_roll < 0.24 + altitude * 0.42:
            # Conifer: visible tapered trunk, low radial branches and
            # overlapping asymmetric layers of needles.
            trunk_height = 4.2 * scale
            builder.add_tapered_segment(
                (x, y, height), (x, y, height + trunk_height),
                0.22 * scale, 0.09 * scale, trunk,
            )
            for branch_index in range(3):
                angle = heading + math.tau * branch_index / 3.0
                branch_z = height + (1.25 + branch_index * 0.16) * scale
                builder.add_tapered_segment(
                    (x, y, branch_z),
                    (
                        x + math.cos(angle) * 1.42 * scale,
                        y + math.sin(angle) * 1.42 * scale,
                        branch_z + 0.14 * scale,
                    ),
                    0.075 * scale, 0.016 * scale, trunk, segments=5,
                )
            for layer, (z, radius, cone_height) in enumerate((
                (1.15, 1.55, 2.45), (2.15, 1.27, 2.25), (3.05, 0.92, 1.95),
            )):
                layer_color = leaf if layer != 1 else crown_light
                builder.add_cone(
                    (x, y, height + z * scale), radius * scale,
                    cone_height * scale, layer_color, heading + layer * 0.31,
                )
        else:
            # Broadleaf tree: the fork extends beyond the crown into visible
            # secondary twigs, finished with small pointed leaf silhouettes.
            trunk_height = float(rng.uniform(2.45, 3.25)) * scale
            fork_z = height + trunk_height * 0.70
            top_z = height + trunk_height
            builder.add_tapered_segment(
                (x, y, height), (x, y, top_z),
                0.25 * scale, 0.105 * scale, trunk,
            )
            branch_angle = heading + float(rng.uniform(-0.35, 0.35))
            branch_tips = []
            for direction in (-1.0, 0.0, 1.0):
                angle = branch_angle + direction * 0.82
                end = (
                    x + math.cos(angle) * 1.02 * scale,
                    y + math.sin(angle) * 1.02 * scale,
                    top_z + (0.48 + 0.12 * (1.0 - abs(direction))) * scale,
                )
                builder.add_tapered_segment(
                    (x, y, fork_z), end, 0.12 * scale, 0.045 * scale, trunk,
                    segments=5,
                )
                twig_angle = angle + direction * 0.18
                twig_tip = (
                    x + math.cos(twig_angle) * 1.58 * scale,
                    y + math.sin(twig_angle) * 1.58 * scale,
                    end[2] + 0.34 * scale,
                )
                builder.add_tapered_segment(
                    end, twig_tip, 0.047 * scale, 0.016 * scale, trunk,
                    segments=5,
                )
                branch_tips.append((twig_tip, twig_angle))

            jitter = [float(rng.uniform(0.82, 1.17)) for _ in range(8)]
            crown_center = (
                x + math.cos(heading) * 0.12 * scale,
                y + math.sin(heading) * 0.12 * scale,
                top_z + 1.18 * scale,
            )
            builder.add_ellipsoid(
                crown_center,
                (1.50 * scale, 1.34 * scale, 1.48 * scale),
                leaf, heading, jitter,
            )
            # Three small sprays break the crown outline into recognisable
            # leaves. They share the forest Geom and add no extra draw calls.
            for tip, angle in branch_tips:
                for leaf_index, spread in enumerate((-0.42, 0.0, 0.42)):
                    leaf_center = (
                        tip[0] + math.cos(angle + spread) * 0.13 * scale,
                        tip[1] + math.sin(angle + spread) * 0.13 * scale,
                        tip[2] + (leaf_index - 1) * 0.10 * scale,
                    )
                    builder.add_leaf(
                        leaf_center, 0.48 * scale, 0.25 * scale,
                        angle + spread, 0.20 + spread * 0.55,
                        crown_light if leaf_index == 1 else leaf,
                    )
            if tree_roll > 0.82:
                side = -1.0 if rng.random() < 0.5 else 1.0
                lobe_center = (
                    crown_center[0] + math.cos(heading + side * 1.25) * 0.75 * scale,
                    crown_center[1] + math.sin(heading + side * 1.25) * 0.75 * scale,
                    crown_center[2] - 0.18 * scale,
                )
                builder.add_ellipsoid(
                    lobe_center,
                    (0.88 * scale, 0.78 * scale, 0.92 * scale),
                    crown_light, heading + 0.47, list(reversed(jitter)),
                )
        planted += 1

    if planted == 0:
        return None
    return builder.build()
