"""The 10 cm label raster the router searches (#10, ADR-003).

Labels: −1 is blocked (walls, windows, the front door, outside ``inner``); ``0 … R−1`` are the
plan's rooms in ``plan.rooms`` order; ``R + k`` are doors, then openings. Every door id is
above every room id, which ``route.search`` enforces.

Lattice (shared with #14's hub candidates): cell column k spans ``[k, k + 1) · cell_m`` in x
and row j spans ``[j, j + 1) · cell_m`` in y, anchored at the plan origin, y up. Row 0 of
``labels`` is the lowest row that covers the plan.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from hackhive.contracts import Constraints, Opening, Plan
from hackhive.rings import CELL_CENTRE_OFFSET
from hackhive.route import BLOCKED, Cell

Point2 = tuple[float, float]

NOT_PAINTED = ("land",)  # the plot outline covers the whole house; it is not a room
AMBIGUOUS_THIRD_ROOM = 0.25  # a third room touching a door this much (vs the 2nd) is an error
_NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass(frozen=True)
class LabelGrid:
    labels: np.ndarray  # (rows, cols) int16
    cell_m: float
    origin: tuple[int, int]  # lattice (col, row) of labels[0, 0]
    room_ids: list[str]  # label i -> plan room id
    passage_ids: list[str]  # label R + k -> door or opening id
    door_rooms: dict[int, tuple[int, int]]  # passage label -> the two room labels it joins
    exterior_doors: list[str]  # doors with one room inside `inner` (balconies): blocked

    # ----------------------------------------------------------------------- construction

    @classmethod
    def from_plan(
        cls, plan: Plan, openings: Sequence[Opening] = (), cell_m: float | None = None
    ) -> LabelGrid:
        """Paint, in order: rooms, walls/windows/front door, doors + openings, outside."""
        g = cell_m if cell_m is not None else Constraints().grid_m
        x0, y0, x1, y1 = plan.inner.buffer(2 * g).bounds
        col0, row0 = math.floor(x0 / g), math.floor(y0 / g)
        cols, rows = math.ceil(x1 / g) - col0, math.ceil(y1 / g) - row0
        xs, ys = np.meshgrid(
            (np.arange(cols) + col0 + CELL_CENTRE_OFFSET) * g,
            (np.arange(rows) + row0 + CELL_CENTRE_OFFSET) * g,
        )

        def inside(geom: BaseGeometry) -> np.ndarray:
            return shapely.contains_xy(geom, xs, ys)

        labels = np.full((rows, cols), BLOCKED, dtype=np.int16)
        for i, room in enumerate(plan.rooms):
            if room.type not in NOT_PAINTED:
                labels[inside(room.polygon)] = i
        never_passable = np.zeros_like(labels, dtype=bool)
        for geom in [*plan.windows, *plan.front_doors]:
            never_passable |= inside(geom)
        for geom in plan.walls:
            labels[inside(geom)] = BLOCKED
        labels[never_passable] = BLOCKED
        outside = ~inside(plan.inner)
        before_doors = labels.copy()

        n_rooms = len(plan.rooms)
        passages: list[tuple[str, np.ndarray]] = []
        for d in plan.doors:
            passages.append((d.id, inside(d.polygon.buffer(plan.wall_depth_m / 2))))
        for o in openings:
            passages.append((o.id, inside(o.polygon)))
        for k, (pid, mask) in enumerate(passages):
            mask &= ~never_passable & ~outside
            if not mask.any():
                raise ValueError(f"{plan.plan_id}: {pid} covers no cell centre")
            labels[mask] = n_rooms + k
        labels[outside] = BLOCKED

        door_rooms: dict[int, tuple[int, int]] = {}
        exterior: list[str] = []
        for k, (pid, _) in enumerate(passages):
            label = n_rooms + k
            here = labels == label
            touched = _contacts(labels, here, n_rooms)
            if not touched:
                raise ValueError(f"{plan.plan_id}: {pid} touches no room")
            if len(touched) == 1:  # the other side is outside `inner` (a balcony)
                labels[here] = before_doors[here]
                labels[outside] = BLOCKED
                exterior.append(pid)
                continue
            ranked = sorted(touched, key=lambda r: (-touched[r], r))
            if len(ranked) > 2 and touched[ranked[2]] >= AMBIGUOUS_THIRD_ROOM * touched[ranked[1]]:
                names = ", ".join(plan.rooms[r].id for r in ranked)
                raise ValueError(f"{plan.plan_id}: {pid} touches 3+ rooms ({names})")
            door_rooms[label] = (ranked[0], ranked[1])

        return cls(
            labels=labels,
            cell_m=g,
            origin=(col0, row0),
            room_ids=[r.id for r in plan.rooms],
            passage_ids=[pid for pid, _ in passages],
            door_rooms=door_rooms,
            exterior_doors=exterior,
        )

    # ----------------------------------------------------------------------- coordinates

    def to_xy(self, cell: Cell) -> Point2:
        r, c = cell
        return (
            (self.origin[0] + c + CELL_CENTRE_OFFSET) * self.cell_m,
            (self.origin[1] + r + CELL_CENTRE_OFFSET) * self.cell_m,
        )

    def cell_of(self, xy: Point2) -> Cell:
        return (
            math.floor(xy[1] / self.cell_m) - self.origin[1],
            math.floor(xy[0] / self.cell_m) - self.origin[0],
        )

    def centres(self) -> tuple[np.ndarray, np.ndarray]:
        """Cell-centre x and y arrays, shaped like ``labels``."""
        rows, cols = self.labels.shape
        return np.meshgrid(
            (np.arange(cols) + self.origin[0] + CELL_CENTRE_OFFSET) * self.cell_m,
            (np.arange(rows) + self.origin[1] + CELL_CENTRE_OFFSET) * self.cell_m,
        )

    def room_label(self, room_id: str) -> int:
        try:
            return self.room_ids.index(room_id)
        except ValueError:
            raise ValueError(f"unknown room {room_id}") from None

    # ----------------------------------------------------------------------- snap

    def snap(self, xy: Point2, room_id: str) -> tuple[Cell, tuple[Point2, Point2]]:
        """The nearest cell of ``room_id`` at most one cell away, plus its two L-corners.

        The corners ``(C.x, P.y)`` and ``(P.x, C.y)`` join the exact point P to the cell centre
        C with axis-aligned pieces (#15's stub rule); both must lie on cells of the same room.
        """
        own = self.room_label(room_id)
        rows, cols = self.labels.shape
        r0, c0 = self.cell_of(xy)
        near = [
            (r0 + dr, c0 + dc)
            for dr in (-1, 0, 1)
            for dc in (-1, 0, 1)
            if 0 <= r0 + dr < rows and 0 <= c0 + dc < cols
        ]
        near.sort(key=lambda cell: (math.dist(self.to_xy(cell), xy), cell))
        for cell in near:
            if self.labels[cell] != own:
                continue
            cx, cy = self.to_xy(cell)
            corners = ((cx, xy[1]), (xy[0], cy))
            if all(self._label_at(p) == own for p in corners):
                return cell, corners
        raise ValueError(f"{xy} has no free cell of room {room_id} within one cell")

    def _label_at(self, xy: Point2) -> int:
        r, c = self.cell_of(xy)
        rows, cols = self.labels.shape
        return int(self.labels[r, c]) if 0 <= r < rows and 0 <= c < cols else BLOCKED


def _contacts(labels: np.ndarray, here: np.ndarray, n_rooms: int) -> dict[int, int]:
    """Room label -> number of room cells 4-adjacent to the ``here`` cells."""
    rows, cols = labels.shape
    touching = np.zeros_like(here)
    for dr, dc in _NEIGHBOURS:
        shifted = np.zeros_like(here)
        shifted[max(dr, 0) : rows + min(dr, 0), max(dc, 0) : cols + min(dc, 0)] = here[
            max(-dr, 0) : rows + min(-dr, 0), max(-dc, 0) : cols + min(-dc, 0)
        ]
        touching |= shifted
    found = labels[touching & ~here]
    rooms = found[(found >= 0) & (found < n_rooms)]
    values, counts = np.unique(rooms, return_counts=True)
    return {int(v): int(n) for v, n in zip(values, counts, strict=True)}


def main(argv: list[str] | None = None) -> int:
    """``python -m hackhive.grid <plan.json> <out.png>``: the raster with passages highlighted."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from hackhive.io import load_plan

    p = argparse.ArgumentParser(prog="python -m hackhive.grid")
    p.add_argument("plan", type=Path)
    p.add_argument("out", type=Path)
    args = p.parse_args(argv)
    plan = load_plan(args.plan)
    grid = LabelGrid.from_plan(plan)
    n_rooms = len(grid.room_ids)
    image = np.zeros((*grid.labels.shape, 3))
    rooms = (grid.labels >= 0) & (grid.labels < n_rooms)
    image[rooms] = plt.get_cmap("Pastel1")(grid.labels[rooms] % 9)[:, :3]
    image[np.isin(grid.labels, list(grid.door_rooms))] = (0.85, 0.1, 0.1)
    fig, ax = plt.subplots(figsize=(8, 8 * grid.labels.shape[0] / grid.labels.shape[1]))
    ax.imshow(image, origin="lower", interpolation="nearest")
    ax.set_title(f"{plan.plan_id}: rooms, passages (red), blocked (black)")
    ax.set_axis_off()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=100, bbox_inches="tight")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
