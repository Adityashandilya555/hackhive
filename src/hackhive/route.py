"""Grid routing: a bend-aware Dijkstra on a label raster (#8).

A cell is ``(row, col)``. ``labels`` holds −1 for blocked cells (walls, windows, outside),
room ids for room cells and door/opening ids for door cells. A step may stay on one label,
or move between a door and one of the two rooms it joins; nothing else changes rooms.

**Id convention (#10):** rooms are ``0 … R−1`` and doors/openings ``R + k``, so every door
id is greater than every room id. ``search`` rejects inputs that break it, because a door id
equal to a room id would let a cable change rooms without a door (slide 3 rule 5).

**Units:** costs are in cells. One step is one cell (``grid_m`` metres), so the bend weight
must be in cells too: use ``bend_weight_cells(c)``, never the raw J weight.
"""

from __future__ import annotations

import heapq
import itertools
import math
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from hackhive.contracts import Constraints, HubOut, Location, Plan, RouteSegment

if TYPE_CHECKING:  # grid imports this module, so these are for type hints only
    from hackhive.grid import LabelGrid
    from hackhive.rings import Hub

Cell = tuple[int, int]
Point2 = tuple[float, float]
_DEFAULT = Constraints()  # frozen, so safe as a shared default

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
        """Least cost (steps + w_bend · bends) to reach ``target``; inf if unreachable.

        This is the minimum over arrival headings, and sources start with every heading
        free, so a bend *at* the source or target is not included. Ring routing (#15,
        #19) must read the per-heading ``dist[target][h]`` and add ``w_bend`` where the
        next leg leaves in a different direction: slide 7 counts bends at nodes and hub.
        """
        return float(self.dist[target].min())


def bend_weight_cells(c: Constraints) -> float:
    """The J bend weight in step units: a step costs ``length · grid_m`` in J, a bend ``bend``."""
    return c.cost_weights.bend / (c.cost_weights.length * c.grid_m)


def _check_ids(labels: np.ndarray, door_rooms: Mapping[int, tuple[int, int]]) -> None:
    if not door_rooms:
        return
    first_door = min(door_rooms)
    joined = {room for pair in door_rooms.values() for room in pair}
    rooms_in_raster = {int(v) for v in np.unique(labels)} - set(door_rooms) - {BLOCKED}
    clash = sorted(r for r in joined | rooms_in_raster if r >= first_door)
    if clash:
        raise ValueError(
            f"room ids {clash} are not below the first door id {first_door}; "
            "rooms must be 0..R-1 and doors R+k (#10)"
        )


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
    so the first step never pays a bend. ``w_bend`` is in cells (``bend_weight_cells``).
    """
    if w_bend < 0:
        raise ValueError(f"w_bend must be >= 0, got {w_bend}")
    _check_ids(labels, door_rooms)
    rows, cols = labels.shape
    grid = labels.tolist()  # plain ints: numpy scalar indexing is slow in the hot loop
    dist = np.full((rows, cols, 4), math.inf)
    parent = np.full((rows, cols, 4), -1, dtype=np.int64)
    heap: list[tuple[float, int, int, int]] = []
    for r, c in ((int(r), int(c)) for r, c in sources):  # np.int64 cells from argwhere too
        if not (0 <= r < rows and 0 <= c < cols):
            raise ValueError(f"source {(r, c)} is outside the {rows}x{cols} raster")
        if grid[r][c] == BLOCKED:
            raise ValueError(f"source {(r, c)} is a blocked cell")
        for h in range(4):
            dist[r, c, h] = 0.0
            heap.append((0.0, r, c, h))
    heapq.heapify(heap)

    while heap:
        d, r, c, h = heapq.heappop(heap)
        if d > dist[r, c, h]:
            continue
        here = grid[r][c]
        for nh, (dr, dc) in enumerate(DIRS):
            if nh == (h + 2) % 4:
                continue  # no reversal
            nr, nc = r + dr, c + dc
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            if not _can_step(here, grid[nr][nc], door_rooms):
                continue
            nd = d + 1.0 + (w_bend if nh != h else 0.0)
            if nd < dist[nr, nc, nh]:
                dist[nr, nc, nh] = nd
                parent[nr, nc, nh] = (r * cols + c) * 4 + h
                heapq.heappush(heap, (nd, nr, nc, nh))
    return SearchResult(dist, parent, float(w_bend))


def path_to(result: SearchResult, target: Cell) -> list[Cell]:
    """The cheapest path to ``target`` as corner points (source, turns, target); [] if none."""
    target = (int(target[0]), int(target[1]))  # np.int64 cells from argwhere too
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


# --------------------------------------------------------------------------- rings (#15)

# shortcut: every corner combination is scored, up to 4 options × 5 stops; a bigger ring (the
# ≤ 20-node what-if, #28) picks corners greedily one stop at a time. Revisit with #19/#28.
MAX_CORNER_COMBOS = 4**5


@dataclass(frozen=True)
class Stop:
    """A point a ring visits: its hub or one of its nodes."""

    id: str  # "H1" or "N3"
    xy: Point2  # the exact point in metres: a node's plan point or the hub location
    room_id: str  # the room containing it (grid.snap needs it)


@dataclass(frozen=True)
class RoutedRing:
    hub_id: str
    segments: list[RouteSegment]  # one per leg, closing back to the hub
    ring_length_m: float  # checker.metrics' L for this ring
    bends: int  # checker.metrics' B for this ring


@dataclass(frozen=True)
class _Snapped:
    stop: Stop
    cell: Cell
    centre: Point2  # C, the cell centre
    corners: tuple[Point2, Point2]  # snap's (C.x, P.y) and (P.x, C.y)
    kind: str  # "centre" (P within tol of C), "near" (on C's row or column within tol), "full"


@dataclass(frozen=True)
class _Prepared:
    stops: list[_Snapped]
    paths: list[list[Point2]]  # leg i's grid path (stop i → stop i + 1, cyclic) as cell centres
    c: Constraints


def _prepare(grid: LabelGrid, stops: Sequence[Stop], c: Constraints) -> _Prepared:
    """Snap every stop to its cell and route every leg on the grid (one search per stop)."""
    if len(stops) < 2:
        raise ValueError(f"a ring needs at least 2 stops, got {len(stops)}")
    tol = c.tolerance_m
    snapped = []
    for s in stops:
        try:
            cell, corners = grid.snap(s.xy, s.room_id)
        except ValueError as e:
            raise ValueError(f"{s.id}: {e}") from e
        centre = grid.to_xy(cell)
        near_x = abs(s.xy[0] - centre[0]) <= tol
        near_y = abs(s.xy[1] - centre[1]) <= tol
        kind = "centre" if near_x and near_y else "near" if near_x or near_y else "full"
        snapped.append(_Snapped(s, cell, centre, corners, kind))
    w_bend = bend_weight_cells(c)
    paths = []
    for a, b in zip(snapped, [*snapped[1:], snapped[0]], strict=True):
        cells = path_to(search(grid.labels, grid.door_rooms, [a.cell], w_bend), b.cell)
        if not cells:
            raise ValueError(f"{a.stop.id}→{b.stop.id} unreachable")
        paths.append([grid.to_xy(cell) for cell in cells])
    return _Prepared(snapped, paths, c)


def _on_piece(k: Point2, a: Point2, b: Point2, tol: float) -> bool:
    """Whether k lies on the axis-aligned piece a–b (within tol)."""
    if abs(a[0] - b[0]) <= tol:  # vertical
        lo, hi = sorted((a[1], b[1]))
        return abs(k[0] - a[0]) <= tol and lo - tol <= k[1] <= hi + tol
    if abs(a[1] - b[1]) <= tol:  # horizontal
        lo, hi = sorted((a[0], b[0]))
        return abs(k[1] - a[1]) <= tol and lo - tol <= k[0] <= hi + tol
    return False


def _attach(p: Point2, corner: Point2 | None, path: list[Point2], tol: float) -> list[Point2]:
    """The exact point p joined to a grid path that starts at p's cell centre, via a corner.

    When the corner lies on the path's first piece, the cable joins the run there and never
    reaches the centre, so the two legs at a node don't loop round it (an X at C, #15 trap 3).
    """
    if corner is None:  # p is its cell centre (within tol): squared up later
        return [p, *path]
    if len(path) >= 2 and _on_piece(corner, path[0], path[1], tol):
        return [p, corner, *path[1:]]
    return [p, corner, *path]


def _square_up(points: Sequence[Point2], tol: float) -> list[Point2]:
    """Every piece exactly horizontal or vertical, with both ends kept bit for bit.

    A piece off an axis by ≤ tol (a stop within 1 mm of its cell's row or column, #15 trap 5)
    is squared by moving its next point onto the previous one's coordinate, forwards from the
    first point and then backwards from the last, so the neighbouring run follows the exact
    point by ≤ tol. Pieces shorter than tol become zero-length and are dropped.
    """
    p = [list(q) for q in points]
    n = len(p)

    def match(i: int, j: int) -> None:  # move p[j] onto p[i] wherever they differ by ≤ tol
        for axis in (0, 1):
            if 0 < abs(p[i][axis] - p[j][axis]) <= tol:
                p[j][axis] = p[i][axis]

    for i in range(n - 2):
        match(i, i + 1)
    for i in range(n - 1, 1, -1):
        match(i, i - 1)
    out: list[Point2] = [(p[0][0], p[0][1])]
    for x, y in p[1:]:
        if (x, y) != out[-1]:
            out.append((x, y))
    return out


def _corner(s: _Snapped, index: int | None) -> Point2 | None:
    return None if s.kind == "centre" or index is None else s.corners[index]


def _leg(prep: _Prepared, i: int, out_corner: int | None, in_corner: int | None) -> list[Point2]:
    """Leg i's polyline: [exact from] + stub + grid path + stub + [exact to], squared and
    cleaned (corner points only, reversals kept)."""
    from hackhive.checker import clean_polyline  # checker imports grid, which imports this module

    tol = prep.c.tolerance_m
    a, b = prep.stops[i], prep.stops[(i + 1) % len(prep.stops)]
    pts = _attach(a.stop.xy, _corner(a, out_corner), prep.paths[i], tol)
    pts = _attach(b.stop.xy, _corner(b, in_corner), pts[::-1], tol)[::-1]
    return clean_polyline(_square_up(pts, tol), tol)


def _legs(prep: _Prepared, choices: Sequence[tuple[int | None, int | None]]) -> list[list[Point2]]:
    """Every leg for one choice of (incoming, outgoing) corner per stop."""
    n = len(prep.stops)
    return [_leg(prep, i, choices[i][1], choices[(i + 1) % n][0]) for i in range(n)]


def _segments(
    prep: _Prepared, choices: Sequence[tuple[int | None, int | None]]
) -> list[RouteSegment]:
    from hackhive.checker import segment_length  # checker imports grid, which imports this module

    n = len(prep.stops)
    return [
        RouteSegment.model_validate(
            {
                "from": prep.stops[i].stop.id,
                "to": prep.stops[(i + 1) % n].stop.id,
                "length_m": segment_length(line),
                "polyline": line,
            }
        )
        for i, line in enumerate(_legs(prep, choices))
    ]


def _options(s: _Snapped, retrace_ok: Collection[str]) -> list[tuple[int | None, int | None]]:
    """(incoming, outgoing) corner pairs at a stop. Different corners always; the same corner
    only where a retrace is legal (a node in a single-door room) or no other corner exists (a
    stop within tol of its cell's row or column)."""
    if s.kind == "centre":
        return [(None, None)]
    options: list[tuple[int | None, int | None]] = [(0, 1), (1, 0)]
    if s.kind == "near" or s.stop.id in retrace_ok:
        options += [(0, 0), (1, 1)]
    return options


def _score(prep: _Prepared, legs: list[list[Point2]]) -> float:
    """J of one ring (checker.metrics), on a lightweight HubOut."""
    from hackhive.checker import metrics  # checker imports grid, which imports this module

    n = len(prep.stops)
    hub = prep.stops[0].stop
    hub_out = HubOut.model_construct(
        hub_id=hub.id,
        room_id=hub.room_id,
        location=Location.model_construct(x=hub.xy[0], y=hub.xy[1]),
        assigned_rooms=[],
        assigned_nodes=[],
        closed_sequence=[s.stop.id for s in prep.stops] + [hub.id],
        route_segments=[
            RouteSegment.model_construct(
                from_=prep.stops[i].stop.id,
                to=prep.stops[(i + 1) % n].stop.id,
                length_m=0.0,
                polyline=line,
            )
            for i, line in enumerate(legs)
        ],
        ring_length_m=0.0,
    )
    return metrics([hub_out], prep.c).J


def route_ring(
    grid: LabelGrid,
    stops: Sequence[Stop],
    c: Constraints = _DEFAULT,
    *,
    retrace_ok: Collection[str] = (),
) -> list[RouteSegment]:
    """One closed ring as slide-8 route segments: stops[0] (the hub) → … → stops[-1] → stops[0].

    Each leg is `[exact from] + stub + grid path + stub + [exact to]` (#15): a stub is an
    axis-aligned L from the exact point to its snapped cell centre via one of `grid.snap`'s two
    corners, or joins the grid run at the corner when the corner lies on it. A node's incoming
    and outgoing stubs use different corners, unless the node is in `retrace_ok` (a single-door
    room, where the retrace is legal) or within tol of its cell's row or column. Among those
    choices the ring with the lowest J (`checker.metrics`) wins; ties go to the first. Raises
    ValueError for fewer than 2 stops, a stop its room can't snap, or an unreachable leg.
    """
    prep = _prepare(grid, stops, c)
    n = len(prep.stops)
    options = [_options(s, retrace_ok) for s in prep.stops]
    cache: dict[tuple[int, int | None, int | None], list[Point2]] = {}

    def legs(choice: Sequence[tuple[int | None, int | None]]) -> list[list[Point2]]:
        out = []
        for i in range(n):
            key = (i, choice[i][1], choice[(i + 1) % n][0])
            if key not in cache:
                cache[key] = _leg(prep, *key)
            out.append(cache[key])
        return out

    if math.prod(map(len, options)) <= MAX_CORNER_COMBOS:
        best = min(itertools.product(*options), key=lambda choice: _score(prep, legs(choice)))
    else:  # greedy, one stop at a time (see MAX_CORNER_COMBOS)
        best = [opts[0] for opts in options]
        for i, opts in enumerate(options):
            best[i] = min(opts, key=lambda o: _score(prep, legs([*best[:i], o, *best[i + 1 :]])))
    return _segments(prep, best)


def single_door_nodes(plan: Plan, c: Constraints = _DEFAULT) -> frozenset[str]:
    """Nodes in single-door rooms: degree 1 in the full room graph, as check_rules counts them."""
    from hackhive.graph import build_graph

    g = build_graph(plan, c)
    return frozenset(n.id for n in plan.nodes if g.degree(n.room_id) == 1)


def route_rings(
    grid: LabelGrid, plan: Plan, hubs: Sequence[Hub], c: Constraints = _DEFAULT
) -> list[RoutedRing]:
    """Every hub's ring, routed independently in hub order (no rip-up yet, #21)."""
    from hackhive.checker import metrics  # checker imports grid, which imports this module

    retrace_ok = single_door_nodes(plan, c)
    nodes = {n.id: n for n in plan.nodes}
    out = []
    for hub in hubs:
        stops = [
            Stop(hub.hub_id, hub.location, hub.room_id),
            *(Stop(n, nodes[n].xy, nodes[n].room_id) for n in hub.ring[1:-1]),
        ]
        segments = route_ring(grid, stops, c, retrace_ok=retrace_ok)
        hub_out = HubOut.model_validate(
            {
                "hub_id": hub.hub_id,
                "room_id": hub.room_id,
                "location": {"x": hub.location[0], "y": hub.location[1]},
                "assigned_rooms": [hub.room_id],
                "assigned_nodes": hub.ring[1:-1],
                "closed_sequence": hub.ring,
                "route_segments": [s.model_dump(by_alias=True) for s in segments],
                "ring_length_m": 0.0,
            }
        )
        per_hub = metrics([hub_out], c).per_hub[hub.hub_id]
        out.append(RoutedRing(hub.hub_id, segments, per_hub.L, per_hub.B))
    return out
