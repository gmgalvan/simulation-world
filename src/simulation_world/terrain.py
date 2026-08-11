"""Endless procedural terrain.

Height is a *pure function* of world coordinates, which is what makes the
world streamable: two neighbouring chunks evaluate the identical function on
their shared edge, so they meet with no cracks and no bookkeeping. It also
means height/normal queries work anywhere, including terrain that is not
currently loaded — the AI and the ballistics rely on that.
"""

from __future__ import annotations

import numpy as np
from panda3d.bullet import BulletRigidBodyNode, BulletTriangleMesh, BulletTriangleMeshShape
from panda3d.core import (
    Geom,
    GeomNode,
    GeomTriangles,
    GeomVertexData,
    GeomVertexFormat,
    NodePath,
    Vec3,
)

# Matches GeomVertexFormat.get_v3n3c4(): 3 float position, 3 float normal,
# 4 uint8 colour — 28 bytes, so a numpy array of this dtype can be blitted
# straight into the vertex buffer instead of written vertex by vertex.
VERTEX_DTYPE = np.dtype(
    [("pos", np.float32, 3), ("nrm", np.float32, 3), ("col", np.uint8, 4)]
)

# Colour stops in metres *relative to the water line*, so beaches, meadows and
# snow line up with the actual water rather than with an abstract 0..1 range.
_RAMP_STOPS = np.array([-8.0, -0.5, 2.0, 12.0, 26.0, 38.0, 52.0])
_RAMP_COLORS = np.array(
    [
        (0.36, 0.33, 0.24),  # river bed
        (0.72, 0.68, 0.47),  # beach
        (0.44, 0.60, 0.30),  # meadow
        (0.34, 0.52, 0.25),  # grass
        (0.28, 0.42, 0.23),  # upland
        (0.45, 0.43, 0.39),  # rock
        (0.94, 0.95, 0.97),  # snow
    ]
)
_ROCK = np.array((0.40, 0.38, 0.35))
WATER_COLOR = (0.16, 0.36, 0.52, 0.72)


def _hash01(ix: np.ndarray, iy: np.ndarray, salt: int) -> np.ndarray:
    """Deterministic pseudorandom value in [0,1) for an integer lattice point."""
    h = (
        ix.astype(np.int64) * 374761393
        + iy.astype(np.int64) * 668265263
        + int(salt) * 1442695041
    ) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h = h ^ (h >> 16)
    return h.astype(np.float64) / 4294967295.0


def _value_noise(x: np.ndarray, y: np.ndarray, salt: int) -> np.ndarray:
    """Smoothstep-interpolated value noise on the unit lattice."""
    x0 = np.floor(x)
    y0 = np.floor(y)
    tx = x - x0
    ty = y - y0
    tx = tx * tx * (3.0 - 2.0 * tx)
    ty = ty * ty * (3.0 - 2.0 * ty)

    ix = x0.astype(np.int64)
    iy = y0.astype(np.int64)
    c00 = _hash01(ix, iy, salt)
    c10 = _hash01(ix + 1, iy, salt)
    c01 = _hash01(ix, iy + 1, salt)
    c11 = _hash01(ix + 1, iy + 1, salt)

    top = c00 + (c10 - c00) * tx
    bottom = c01 + (c11 - c01) * tx
    return top + (bottom - top) * ty


def _fbm(x: np.ndarray, y: np.ndarray, seed: int, octaves: int = 5) -> np.ndarray:
    total = np.zeros_like(x, dtype=np.float64)
    amplitude = 1.0
    frequency = 1.0
    norm = 0.0
    for octave in range(octaves):
        total += amplitude * _value_noise(x * frequency, y * frequency, seed + octave * 1013)
        norm += amplitude
        amplitude *= 0.5
        frequency *= 2.0
    return total / norm


def _smoothstep(edge0: float, edge1: float, value: np.ndarray) -> np.ndarray:
    t = np.clip((value - edge0) / max(edge1 - edge0, 1e-9), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _ramp_colors(height_above_water: np.ndarray) -> np.ndarray:
    idx = np.clip(np.searchsorted(_RAMP_STOPS, height_above_water) - 1, 0, len(_RAMP_STOPS) - 2)
    span = _RAMP_STOPS[idx + 1] - _RAMP_STOPS[idx]
    t = np.clip(
        (height_above_water - _RAMP_STOPS[idx]) / np.where(span > 0, span, 1.0), 0.0, 1.0
    )
    return _RAMP_COLORS[idx] * (1 - t)[..., None] + _RAMP_COLORS[idx + 1] * t[..., None]


class InfiniteTerrain:
    """Unbounded heightfield, generated on demand one chunk at a time."""

    def __init__(
        self,
        seed: int = 0,
        relief: float = 46.0,
        feature_scale: float = 220.0,
        chunk_size: float = 128.0,
        chunk_divisions: int = 32,
        clear_radius: float = 420.0,
    ) -> None:
        self.seed = int(seed)
        self.relief = relief
        self.feature_scale = feature_scale
        self.chunk_size = chunk_size
        self.chunk_divisions = chunk_divisions
        # Sea level for the whole world; rivers and lakes are dug below it, and
        # the plains sit on a shelf comfortably above it.
        self.water_level = relief * 0.12
        self.base_elevation = relief * 0.34
        # Rivers and lakes fade out inside this radius of the origin. The
        # fighting happens there, and a river cutting across the middle of it
        # just drowns the ground units — they have to cross somewhere.
        self.clear_radius = clear_radius
        # Two permanent sea lanes flank the dry battlefield. They give naval
        # units an actual ocean to operate in instead of spawning them in a
        # random lake, while keeping the central land battle traversable.
        self.sea_start = max(300.0, clear_radius * 0.86)
        self.sea_end = max(420.0, clear_radius * 1.10)

    # ------------------------------------------------------------------
    # Height field
    # ------------------------------------------------------------------
    def water_carve(self, u: np.ndarray, v: np.ndarray, mountains: np.ndarray):
        """Masks for river channels and lake basins, both in [0,1]."""
        # A river is where a smooth field crosses its own midline: that level
        # set is a long winding curve, which is exactly a river's plan view.
        flow = _fbm(u * 0.45, v * 0.45, self.seed + 331, octaves=3)
        channel = np.abs(flow - 0.5)
        rivers = 1.0 - _smoothstep(0.014, 0.060, channel)
        # Rivers cut shallower as they climb into the ranges.
        rivers = rivers * (1.0 - 0.6 * mountains)

        basin = _fbm(u * 0.13, v * 0.13, self.seed + 9911, octaves=2)
        lakes = (1.0 - _smoothstep(0.20, 0.36, basin)) * (1.0 - mountains)
        return rivers, lakes

    def heights(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Vectorised terrain height for arrays of world coordinates."""
        u = x / self.feature_scale
        v = y / self.feature_scale

        # A slow mask decides where the world is plain and where it is
        # mountainous, so an endless world gets regions instead of uniform mush.
        mask = _fbm(u * 0.22, v * 0.22, self.seed + 7717, octaves=2)
        mountains = _smoothstep(0.40, 0.62, mask)

        detail = _fbm(u, v, self.seed, octaves=5)

        # Everything is in absolute metres so it can be reasoned about against
        # the water line. Scaling the amplitude instead once sank the entire
        # world: low-amplitude plains ended up below sea level everywhere.
        plains = self.base_elevation + (detail - 0.5) * self.relief * 0.16
        height = plains + mountains * detail * self.relief * 1.8

        rivers, lakes = self.water_carve(u, v, mountains)
        carve = np.maximum(rivers, lakes * 0.85)
        if self.clear_radius > 0.0:
            # Keep the battlefield dry, let the water start beyond it.
            distance = np.sqrt(x * x + y * y)
            carve = carve * _smoothstep(
                self.clear_radius * 0.62, self.clear_radius, distance
            )
        # Open water at the left and right of the battlefield (world X axis).
        # Unlike procedural lakes this is continuous along Y, so a destroyer
        # can sail and patrol without ever having to cross land.
        side_sea = _smoothstep(self.sea_start, self.sea_end, np.abs(x))
        carve = np.maximum(carve, side_sea)
        # Pull the ground down towards a bed that sits below the water line, so
        # the water plane shows through instead of floating over dry land.
        bed = self.water_level - 6.0
        return height + (bed - height) * carve

    def height_at(self, x: float, y: float) -> float:
        return float(self.heights(np.array([float(x)]), np.array([float(y)]))[0])

    def normal_at(self, x: float, y: float) -> Vec3:
        d = self.chunk_size / self.chunk_divisions
        xs = np.array([x + d, x - d, x, x])
        ys = np.array([y, y, y + d, y - d])
        h = self.heights(xs, ys)
        normal = Vec3(float(h[1] - h[0]), float(h[3] - h[2]), 2.0 * d)
        normal.normalize()
        return normal

    # ------------------------------------------------------------------
    # Chunk geometry
    # ------------------------------------------------------------------
    def chunk_origin(self, cx: int, cy: int) -> tuple[float, float]:
        return cx * self.chunk_size, cy * self.chunk_size

    def chunk_center(self, cx: int, cy: int) -> tuple[float, float]:
        ox, oy = self.chunk_origin(cx, cy)
        return ox + self.chunk_size * 0.5, oy + self.chunk_size * 0.5

    def chunk_coords_at(self, x: float, y: float) -> tuple[int, int]:
        return int(np.floor(x / self.chunk_size)), int(np.floor(y / self.chunk_size))

    def _chunk_triangles(self, cx: int, cy: int):
        """Corner arrays for every triangle in a chunk, shape (T, 3, 3)."""
        n = self.chunk_divisions + 1
        ox, oy = self.chunk_origin(cx, cy)
        axis_x = ox + np.linspace(0.0, self.chunk_size, n)
        axis_y = oy + np.linspace(0.0, self.chunk_size, n)
        gx, gy = np.meshgrid(axis_x, axis_y)
        gz = self.heights(gx, gy)

        def corner(dy: int, dx: int) -> np.ndarray:
            sl_y = slice(dy, dy + self.chunk_divisions)
            sl_x = slice(dx, dx + self.chunk_divisions)
            return np.stack((gx[sl_y, sl_x], gy[sl_y, sl_x], gz[sl_y, sl_x]), axis=-1)

        p00 = corner(0, 0)
        p10 = corner(0, 1)
        p01 = corner(1, 0)
        p11 = corner(1, 1)

        # Alternate the diagonal so the faceting does not read as stripes.
        rows, cols = np.meshgrid(
            np.arange(self.chunk_divisions), np.arange(self.chunk_divisions), indexing="ij"
        )
        flip = ((rows + cols) % 2 == 1)[..., None]

        tri_a = np.stack((p00, p10, np.where(flip, p01, p11)), axis=-2)
        tri_b = np.stack((np.where(flip, p10, p00), p11, p01), axis=-2)
        tris = np.concatenate((tri_a.reshape(-1, 3, 3), tri_b.reshape(-1, 3, 3)), axis=0)
        return tris

    def build_chunk_geom(self, cx: int, cy: int) -> tuple[NodePath, Geom]:
        """Flat-shaded chunk mesh; returns the NodePath and its Geom."""
        tris = self._chunk_triangles(cx, cy)

        edge1 = tris[:, 1] - tris[:, 0]
        edge2 = tris[:, 2] - tris[:, 0]
        normals = np.cross(edge1, edge2)
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        normals = np.divide(normals, np.where(lengths > 1e-9, lengths, 1.0))

        mean_h = tris[:, :, 2].mean(axis=1)
        rgb = _ramp_colors(mean_h - self.water_level)
        steepness = np.clip((1.0 - normals[:, 2]) * 2.2, 0.0, 1.0)[..., None]
        rgb = rgb * (1.0 - steepness) + _ROCK * steepness

        count = tris.shape[0]
        buf = np.empty(count * 3, dtype=VERTEX_DTYPE)
        buf["pos"] = tris.reshape(-1, 3).astype(np.float32)
        buf["nrm"] = np.repeat(normals, 3, axis=0).astype(np.float32)
        colors = np.repeat((rgb * 255.0).clip(0, 255).astype(np.uint8), 3, axis=0)
        buf["col"][:, :3] = colors
        buf["col"][:, 3] = 255

        vdata = GeomVertexData(f"chunk{cx}_{cy}", GeomVertexFormat.get_v3n3c4(), Geom.UH_static)
        vdata.unclean_set_num_rows(count * 3)
        vdata.modify_array(0).modify_handle().set_data(buf.tobytes())

        prim = GeomTriangles(Geom.UH_static)
        prim.add_consecutive_vertices(0, count * 3)
        prim.close_primitive()

        geom = Geom(vdata)
        geom.add_primitive(prim)
        node = GeomNode(f"chunk{cx}_{cy}")
        node.add_geom(geom)
        return NodePath(node), geom

    def build_chunk_collider(self, geom: Geom, cx: int, cy: int) -> BulletRigidBodyNode:
        """Static body built from the very geometry that gets drawn."""
        mesh = BulletTriangleMesh()
        mesh.add_geom(geom)
        shape = BulletTriangleMeshShape(mesh, dynamic=False)
        node = BulletRigidBodyNode(f"terrain{cx}_{cy}")
        node.add_shape(shape)
        node.set_friction(0.9)
        return node
