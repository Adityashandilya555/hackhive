"""Grid routing: a bend-aware Dijkstra on a label raster (#8).

A cell is ``(row, col)``. ``labels`` holds −1 for blocked cells (walls, windows, outside),
room ids for room cells and door/opening ids for door cells. A step may stay on one label,
or move between a door and one of the two rooms it joins; nothing else changes rooms.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np

Cell = tuple[int, int]

BLOCKED = -1
# headings: north, east, south, west, as (d_row, d_col)
DIRS: tuple[Cell, ...] = ((-1, 0), (0, 1), (1, 0), (0, -1))


@dataclass(frozen=True)
class SearchResult:
    """One-to-all costs from the sources, per (cell, heading) state."""

    dist: np.ndarray  # (rows, cols, 4) float64, inf where unreached
    parent: np.ndarray  # (rows, cols, 4) int64 flat state index, −1 at a source
    w_bend: float

    def cost(self, target: Cell) -> float:
        """Least cost (steps + w_bend · bends) to reach ``target``; inf if unreachable."""
        return float(self.dist[target].min())


def _can_step(a: int, b: int, door_rooms: Mapping[int, tuple[int, int]]) -> bool:
    if a == BLOCKED or b == BLOCKED:
        return False
    if a == b:
        return True
    return b in door_rooms.get(a, ()) or a in door_rooms.get(b, ())


def search(
    labels: np.ndarray,
    door_rooms: Mapping[int, tuple[int, int]],
    sources: Iterable[Cell],
    w_bend: float,
) -> SearchResult:
    """Dijkstra over (cell, heading): a straight step costs 1, a 90° turn 1 + ``w_bend``.

    180° turns are forbidden. Every source is seeded with all four headings at cost 0,
    so the first step never pays a bend.
    """
    rows, cols = labels.shape
    dist = np.full((rows, cols, 4), math.inf)
    parent = np.full((rows, cols, 4), -1, dtype=np.int64)
    heap: list[tuple[float, int, int, int]] = []
    for r, c in sources:
        if labels[r, c] == BLOCKED:
            raise ValueError(f"source {(r, c)} is a blocked cell")
        for h in range(4):
            dist[r, c, h] = 0.0
            heap.append((0.0, r, c, h))
    heapq.heapify(heap)

    while heap:
        d, r, c, h = heapq.heappop(heap)
        if d > dist[r, c, h]:
            continue
        here = int(labels[r, c])
        for nh, (dr, dc) in enumerate(DIRS):
            if nh == (h + 2) % 4:
                continue  # no reversal
            nr, nc = r + dr, c + dc
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            if not _can_step(here, int(labels[nr, nc]), door_rooms):
                continue
            nd = d + 1.0 + (w_bend if nh != h else 0.0)
            if nd < dist[nr, nc, nh]:
                dist[nr, nc, nh] = nd
                parent[nr, nc, nh] = (r * cols + c) * 4 + h
                heapq.heappush(heap, (nd, nr, nc, nh))
    return SearchResult(dist, parent, float(w_bend))


def path_to(result: SearchResult, target: Cell) -> list[Cell]:
    """The cheapest path to ``target`` as corner points (source, turns, target); [] if none."""
    states = result.dist[target]
    h = int(np.argmin(states))
    if math.isinf(states[h]):
        return []
    cols = result.dist.shape[1]
    cells = [target]
    state = int(result.parent[target][h])
    while state >= 0:
        cell_index, _ = divmod(state, 4)
        r, c = divmod(cell_index, cols)
        cells.append((r, c))
        state = int(result.parent[r, c, state % 4])
    cells.reverse()
    return simplify(cells)


def simplify(cells: list[Cell]) -> list[Cell]:
    """Drop cells that lie on a straight run, keeping the ends and every corner."""
    if len(cells) <= 2:
        return list(cells)
    out = [cells[0]]
    for prev, cur, nxt in zip(cells, cells[1:], cells[2:], strict=False):
        if _heading(prev, cur) != _heading(cur, nxt):
            out.append(cur)
    out.append(cells[-1])
    return out


def expand(path: list[Cell]) -> list[Cell]:
    """Every cell along a corner-point path."""
    if not path:
        return []
    cells = [path[0]]
    for (r0, c0), (r1, c1) in zip(path, path[1:], strict=False):
        dr, dc = (r1 > r0) - (r1 < r0), (c1 > c0) - (c1 < c0)
        r, c = r0, c0
        while (r, c) != (r1, c1):
            r, c = r + dr, c + dc
            cells.append((r, c))
    return cells


def path_length(path: list[Cell]) -> int:
    """Number of unit steps along a corner-point path."""
    return sum(
        abs(r1 - r0) + abs(c1 - c0) for (r0, c0), (r1, c1) in zip(path, path[1:], strict=False)
    )


def bends(path: list[Cell]) -> int:
    """Direction changes along a corner-point path."""
    headings = [_heading(a, b) for a, b in zip(path, path[1:], strict=False) if a != b]
    return sum(1 for a, b in zip(headings, headings[1:], strict=False) if a != b)


def _heading(a: Cell, b: Cell) -> Cell:
    return ((b[0] > a[0]) - (b[0] < a[0]), (b[1] > a[1]) - (b[1] < a[1]))
