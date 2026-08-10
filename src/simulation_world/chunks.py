"""Streaming of the endless terrain: builds chunks near the action and drops
them once they fall behind.

Two radii, deliberately different. Everything inside ``view_radius`` is drawn;
only the tighter ``physics_radius`` gets a Bullet collider, because the
colliders are what cost real time to build and nothing but units and shells
need them — and both stay near the fighting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from panda3d.core import NodePath

from .scenery import build_chunk_forest


@dataclass
class _Chunk:
    np: NodePath
    body: object | None  # BulletRigidBodyNode while collidable


class ChunkManager:
    def __init__(
        self,
        terrain,
        render: NodePath,
        world,
        view_radius: int = 5,
        physics_radius: int = 3,
        trees_per_chunk: int = 120,
        build_budget: int = 2,
    ) -> None:
        self.terrain = terrain
        self.root = render.attach_new_node("terrain-chunks")
        self.world = world
        self.view_radius = view_radius
        self.physics_radius = physics_radius
        self.trees_per_chunk = trees_per_chunk
        self.build_budget = build_budget
        self.chunks: dict[tuple[int, int], _Chunk] = {}
        self._offset_cache: list[tuple[int, int, float]] | None = None

    def _offsets(self) -> list[tuple[int, int, float]]:
        """Disc of chunk offsets, nearest first. Fixed, so build it once."""
        if self._offset_cache is None:
            offsets = []
            for dy in range(-self.view_radius, self.view_radius + 1):
                for dx in range(-self.view_radius, self.view_radius + 1):
                    distance = math.hypot(dx, dy)
                    if distance <= self.view_radius + 0.5:
                        offsets.append((dx, dy, distance))
            offsets.sort(key=lambda o: o[2])
            self._offset_cache = offsets
        return self._offset_cache

    @property
    def view_distance(self) -> float:
        return self.view_radius * self.terrain.chunk_size

    def loaded_count(self) -> int:
        return len(self.chunks)

    # ------------------------------------------------------------------
    def update(self, anchors, budget: int | None = None) -> int:
        """Load/unload around the given world points. Returns chunks built."""
        # Collapse the anchors to the chunks they sit in first. A squad of 18
        # units usually occupies three or four chunks, so this turns eighteen
        # neighbourhood sweeps into three — the difference between a few
        # hundred and a few thousand Python iterations every single frame.
        anchor_chunks = {self.terrain.chunk_coords_at(p[0], p[1]) for p in anchors}

        wanted: dict[tuple[int, int], float] = {}
        for cx, cy in anchor_chunks:
            for dx, dy, distance in self._offsets():
                key = (cx + dx, cy + dy)
                if wanted.get(key, 1e9) > distance:
                    wanted[key] = distance

        for key in [k for k in self.chunks if k not in wanted]:
            self._unload(key)

        # Nearest first, so the ground under the action appears before scenery
        # far out on the horizon.
        pending = sorted(
            (d, k) for k, d in wanted.items() if k not in self.chunks
        )
        allowance = self.build_budget if budget is None else budget
        built = 0
        for _, key in pending:
            if built >= allowance:
                break
            self._load(key, wanted[key])
            built += 1

        # A chunk that drifted in or out of the physics ring flips its collider.
        for key, chunk in self.chunks.items():
            close = wanted.get(key, 1e9) <= self.physics_radius + 0.5
            if close and chunk.body is None:
                self._attach_body(key, chunk)
            elif not close and chunk.body is not None:
                self._detach_body(chunk)
        return built

    # ------------------------------------------------------------------
    def _load(self, key: tuple[int, int], distance: float) -> None:
        cx, cy = key
        node_path, geom = self.terrain.build_chunk_geom(cx, cy)
        node_path.reparent_to(self.root)
        node_path.set_python_tag("geom", geom)

        if self.trees_per_chunk > 0:
            forest = build_chunk_forest(self.terrain, cx, cy, self.trees_per_chunk)
            if forest is not None:
                forest.reparent_to(node_path)

        chunk = _Chunk(np=node_path, body=None)
        self.chunks[key] = chunk
        if distance <= self.physics_radius + 0.5:
            self._attach_body(key, chunk)

    def _attach_body(self, key: tuple[int, int], chunk: _Chunk) -> None:
        geom = chunk.np.get_python_tag("geom")
        if geom is None:
            return
        body = self.terrain.build_chunk_collider(geom, key[0], key[1])
        chunk.np.attach_new_node(body)
        self.world.attach(body)
        chunk.body = body

    def _detach_body(self, chunk: _Chunk) -> None:
        if chunk.body is None:
            return
        self.world.remove(chunk.body)
        for child in chunk.np.find_all_matches("**/+BulletRigidBodyNode"):
            child.remove_node()
        chunk.body = None

    def _unload(self, key: tuple[int, int]) -> None:
        chunk = self.chunks.pop(key)
        self._detach_body(chunk)
        chunk.np.remove_node()

    def clear(self) -> None:
        for key in list(self.chunks):
            self._unload(key)
