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

from .assets import BOX_FACES

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
    """Accumulates transformed boxes into one flat-shaded Geom."""

    def __init__(self, name: str) -> None:
        fmt = GeomVertexFormat.get_v3n3c4()
        self.vdata = GeomVertexData(name, fmt, Geom.UH_static)
        self.vertex = GeomVertexWriter(self.vdata, "vertex")
        self.normal = GeomVertexWriter(self.vdata, "normal")
        self.color = GeomVertexWriter(self.vdata, "color")
        self.prim = GeomTriangles(Geom.UH_static)
        self.index = 0
        self.name = name

    def add_box(self, center, size, rgb, heading: float) -> None:
        cos_h, sin_h = math.cos(heading), math.sin(heading)
        for nrm, corners in BOX_FACES:
            nx = nrm[0] * cos_h - nrm[1] * sin_h
            ny = nrm[0] * sin_h + nrm[1] * cos_h
            for cx, cy, cz in corners:
                px, py = cx * size[0], cy * size[1]
                self.vertex.add_data3(
                    px * cos_h - py * sin_h + center[0],
                    px * sin_h + py * cos_h + center[1],
                    cz * size[2] + center[2],
                )
                self.normal.add_data3(nx, ny, nrm[2])
                self.color.add_data4(rgb[0], rgb[1], rgb[2], 1.0)
            self.prim.add_vertices(self.index, self.index + 1, self.index + 2)
            self.prim.add_vertices(self.index, self.index + 2, self.index + 3)
            self.index += 4

    def build(self) -> NodePath:
        geom = Geom(self.vdata)
        geom.add_primitive(self.prim)
        node = GeomNode(self.name)
        node.add_geom(geom)
        return NodePath(node)


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

        scale = float(rng.uniform(0.7, 1.5))
        heading = float(rng.uniform(0.0, math.pi * 2))
        trunk = _TRUNK_COLORS[int(rng.integers(len(_TRUNK_COLORS)))]
        leaf = _LEAF_COLORS[int(rng.integers(len(_LEAF_COLORS)))]

        builder.add_box(
            (x, y, height + 1.15 * scale), (0.42 * scale, 0.42 * scale, 2.3 * scale), trunk, heading
        )
        builder.add_box(
            (x, y, height + 2.9 * scale), (2.5 * scale, 2.5 * scale, 2.3 * scale), leaf, heading
        )
        builder.add_box(
            (x, y, height + 4.5 * scale),
            (1.6 * scale, 1.6 * scale, 1.7 * scale),
            tuple(min(1.0, c * 1.15) for c in leaf),
            heading + 0.6,
        )
        planted += 1

    if planted == 0:
        return None
    return builder.build()
