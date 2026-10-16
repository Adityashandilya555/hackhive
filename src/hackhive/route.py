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

import argparse
import hashlib
import heapq
import itertools
import math
import sys
import time
from collections import OrderedDict
from collections.abc import Callable, Collection, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import numpy as np
import shapely

from hackhive.contracts import Constraints, HubOut, Location, Plan, RouteSegment

if TYPE_CHECKING:  # grid imports this module, so these are for type hints only
    from hackhive.grid import LabelGrid
    from hackhive.rings import Candidate, Hub

Cell = tuple[int, int]
Point2 = tuple[float, float]
_DEFAULT = Constraints()  # frozen, so safe as a shared default

BLOCKED = -1
CELL_CENTRE = 0.5  # not-a-weight: a cell centre is half a cell in (rings.CELL_CENTRE_OFFSET)
# headings: north, east, south, west, as (d_row, d_col)
DIRS: tuple[Cell, ...] = ((-1, 0), (0, 1), (1, 0), (0, -1))


@dataclass(frozen=True)
class SearchResult:
    """One-to-all costs from the sources, per (cell, heading) state."""

    dist: np.ndarray  # (rows, cols, 4) float64, inf where unreached
    parent: np.ndarray  # (rows, cols, 4) int64 flat state index, −1 at a source
    w_bend: float
    settled: float = math.inf  # every state below this is final (inf: the search ran out)

    def cost(self, target: Cell) -> float:
        """Least cost (steps + w_bend · bends) to reach ``target``; inf if unreachable.

        This is the minimum over arrival headings, and sources start with every heading
        free, so a bend *at* the source or target is not included. Ring routing (#15,
        #19) must read the per-heading ``dist[target][h]`` and add ``w_bend`` where the
        next leg leaves in a different direction: slide 7 counts bends at nodes and hub.
        """
        return float(self.dist[target].min())


def bend_weight_cells(c: Constraints, cell_m: float | None = None) -> float:
    """The J bend weight in step units: a step costs ``length · cell`` in J, a bend ``bend``. Pass
    the grid's own ``cell_m``: ``LabelGrid.from_plan`` doesn't read ``c`` (#70 review)."""
    return c.cost_weights.bend / (c.cost_weights.length * (cell_m or c.grid_m))


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
    targets: Iterable[Cell] | None = None,
) -> SearchResult:
    """Dijkstra over (cell, heading): a straight step costs 1, a 90° turn 1 + ``w_bend``.

    180° turns are forbidden. Every source is seeded with all four headings at cost 0,
    so the first step never pays a bend. ``w_bend`` is in cells (``bend_weight_cells``).

    With ``targets``, the search stops once every target's cheapest state is final (#19):
    ``cost`` and ``path_to`` stay exact for them, other states are final only below
    ``SearchResult.settled``.
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
    waiting = None if targets is None else {(int(r), int(c)) for r, c in targets}

    while heap:
        d, r, c, h = heapq.heappop(heap)
        if d > dist[r, c, h]:
            continue
        if waiting is not None:
            waiting.discard((r, c))  # its first pop is its cheapest heading
            if not waiting:
                return SearchResult(dist, parent, float(w_bend), d)
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


# --------------------------------------------------------------------------- path cache (#19)

ROI_MARGIN_ROOMS = 1  # rooms added around the door-graph shortest paths (#19)
CACHE_BUILDS_KEPT = 16  # PathCache.cached keeps this many builds (plans × settings)
_EPS = 1e-9


@dataclass(frozen=True)
class CachedPath:
    dist: float  # cells: steps + w_bend · bends, the cheapest arrival (SearchResult.cost)
    cells: list[Cell]  # corner cells, source → target; [] if unreachable
    polyline: list[Point2]  # the corner cells' centres in metres


@dataclass
class CacheStats:
    searches: int = 0  # one per source
    fallbacks: int = 0  # sources searched again on the whole raster (the ROI wasn't provably exact)
    roi_rooms: dict[Hashable, frozenset[int]] = field(default_factory=dict)  # per source
    roi_cells: dict[Hashable, int] = field(default_factory=dict)  # free cells in each ROI
    free_cells: int = 0  # free cells in the whole raster
    ms: float = 0.0  # build time


@dataclass(frozen=True)
class _Searched:
    result: SearchResult
    offset: Cell  # (row, col) of result's [0, 0] in the full raster


def _rooms_of(label: int, door_rooms: Mapping[int, tuple[int, int]]) -> tuple[int, ...]:
    """The room(s) a free cell belongs to: its own, or both rooms of its door."""
    return tuple(door_rooms[label]) if label in door_rooms else (label,)


def _hops(adj: Mapping[int, set[int]], starts: Iterable[int]) -> dict[int, int]:
    """Door hops from the nearest start room (BFS on the door graph)."""
    seen = dict.fromkeys(starts, 0)
    frontier = list(seen)
    while frontier:
        nxt = []
        for room in frontier:
            for nb in adj.get(room, ()):
                if nb not in seen:
                    seen[nb] = seen[room] + 1
                    nxt.append(nb)
        frontier = nxt
    return seen


def _exits(
    labels: np.ndarray, mask: np.ndarray, door_rooms: Mapping[int, tuple[int, int]]
) -> tuple[np.ndarray, np.ndarray]:
    """Every legal step out of the ROI: (u, x) cell pairs with u inside, x outside, as two (E, 2)
    arrays."""
    lab = np.pad(labels, 1, constant_values=BLOCKED)
    inside = np.pad(mask, 1, constant_values=False)
    rows, cols = labels.shape
    us, xs = [], []
    for dr, dc in DIRS:
        nb_lab = lab[1 + dr : rows + 1 + dr, 1 + dc : cols + 1 + dc]
        nb_in = inside[1 + dr : rows + 1 + dr, 1 + dc : cols + 1 + dc]
        for r, c in np.argwhere(mask & ~nb_in & (nb_lab != BLOCKED)):
            r, c = int(r), int(c)
            if _can_step(int(labels[r, c]), int(labels[r + dr, c + dc]), door_rooms):
                us.append((r, c))
                xs.append((r + dr, c + dc))
    return np.array(us, dtype=np.int64).reshape(-1, 2), np.array(xs, dtype=np.int64).reshape(-1, 2)


class PathCache:
    """Routed costs and polylines between every source and every point, built once (#19).

    One one-to-all bend-aware ``search`` per source, with costs exactly as ``search`` gives them
    (cells: steps + ``w_bend`` · bends, no bend at either end). A path reversed is the same
    cable at the same cost, so a target needs no search of its own: pass the nodes as
    ``sources`` and the hub candidates as ``targets``, and every node–node and node–hub pair
    is answered (one search per hub candidate would be thousands per plan).

    **ROI:** each source searches only the rooms on door-graph shortest paths to its points,
    plus ``ROI_MARGIN_ROOMS`` around them, cropped to their box. The ROI is a speed-up, never
    an approximation: a path that leaves it costs at least (its exact cost to the last cell
    inside) + 1 + (the Manhattan distance on from the first cell outside), and if that bound
    is below a target's ROI cost the source is searched again on the whole raster.

    ``dist`` is eager for every pair; ``path`` builds the polyline on first use and keeps it.
    """

    def __init__(
        self,
        points: dict[Hashable, Cell],
        searched: dict[Hashable, _Searched],
        dist: dict[Hashable, dict[Hashable, float]],
        to_xy: Callable[[Cell], Point2],
        metres_per_cell: float,
        stats: CacheStats,
    ) -> None:
        self.points = points
        self._searched = searched
        self._dist = dist
        self._to_xy = to_xy
        self._metres_per_cell = metres_per_cell
        self._paths: dict[tuple[Hashable, Hashable], CachedPath] = {}
        self.stats = stats

    # ----------------------------------------------------------------------- construction

    @classmethod
    def build(
        cls,
        grid: LabelGrid,
        sources: Mapping[Hashable, Cell],
        c: Constraints = _DEFAULT,
        *,
        targets: Mapping[Hashable, Cell] | None = None,
        roi: bool = True,
    ) -> PathCache:
        """Search from every source to every point (sources and targets) on ``grid``."""
        return cls._build(
            grid.labels,
            grid.door_rooms,
            sources,
            bend_weight_cells(c, grid.cell_m),
            targets or {},
            grid.to_xy,
            c.cost_weights.length * grid.cell_m,
            roi,
        )

    @classmethod
    def build_raw(
        cls,
        labels: np.ndarray,
        door_rooms: Mapping[int, tuple[int, int]],
        sources: Mapping[Hashable, Cell],
        w_bend: float,
        *,
        targets: Mapping[Hashable, Cell] | None = None,
        roi: bool = True,
    ) -> PathCache:
        """``build`` on a bare raster (tests): polylines are (col, row) cell centres, 1 m cells."""
        return cls._build(
            labels,
            door_rooms,
            sources,
            w_bend,
            targets or {},
            lambda cell: (cell[1] + CELL_CENTRE, cell[0] + CELL_CENTRE),
            1.0,
            roi,
        )

    @classmethod
    def _build(
        cls,
        labels: np.ndarray,
        door_rooms: Mapping[int, tuple[int, int]],
        sources: Mapping[Hashable, Cell],
        w_bend: float,
        targets: Mapping[Hashable, Cell],
        to_xy: Callable[[Cell], Point2],
        metres_per_cell: float,
        roi: bool,
    ) -> PathCache:
        t0 = time.perf_counter()
        points = {k: (int(r), int(c)) for k, (r, c) in {**targets, **sources}.items()}
        for k, (r, c) in points.items():
            if not (0 <= r < labels.shape[0] and 0 <= c < labels.shape[1]):
                raise ValueError(f"{k}: cell {(r, c)} is outside the {labels.shape} raster")
            if labels[r, c] == BLOCKED:
                raise ValueError(f"{k}: cell {(r, c)} is blocked")
        adj: dict[int, set[int]] = {}
        for a, b in door_rooms.values():
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        cells = np.array(list(points.values()), dtype=np.int64).reshape(-1, 2)
        point_rooms = {k: _rooms_of(int(labels[cell]), door_rooms) for k, cell in points.items()}
        stats = CacheStats(free_cells=int((labels != BLOCKED).sum()))
        searched: dict[Hashable, _Searched] = {}
        dist: dict[Hashable, dict[Hashable, float]] = {}
        for s in sources:
            s_cell = points[s]
            hops = _hops(adj, point_rooms[s])
            reachable = {k for k, rooms in point_rooms.items() if any(r in hops for r in rooms)}
            run = None
            if roi:
                rooms = cls._roi_rooms(adj, hops, point_rooms, reachable)
                run = cls._search_roi(labels, door_rooms, s_cell, w_bend, cells, rooms, points)
                stats.roi_rooms[s] = rooms
                stats.roi_cells[s] = run[2]
                if not cls._exact(run, labels, door_rooms, points, reachable):
                    run = None
                    stats.fallbacks += 1
            if run is None:
                res = search(labels, door_rooms, [s_cell], w_bend, targets=list(points.values()))
                run = (_Searched(res, (0, 0)), None, stats.free_cells)
                stats.roi_cells.setdefault(s, stats.free_cells)
            stats.searches += 1
            searched[s] = run[0]
            dist[s] = cls._costs(run[0], list(points), cells)
        stats.ms = (time.perf_counter() - t0) * 1000
        return cls(points, searched, dist, to_xy, metres_per_cell, stats)

    @staticmethod
    def _roi_rooms(
        adj: Mapping[int, set[int]],
        hops: Mapping[int, int],
        point_rooms: Mapping[Hashable, tuple[int, ...]],
        reachable: Collection[Hashable],
    ) -> frozenset[int]:
        """Rooms on any fewest-door path from the source to a reachable point, plus margin."""
        keep = {r for r, h in hops.items() if h == 0}
        for target_rooms in {point_rooms[k] for k in reachable}:
            best = min(hops[r] for r in target_rooms if r in hops)
            back = _hops(adj, target_rooms)
            keep |= {r for r, h in hops.items() if r in back and h + back[r] == best}
        for _ in range(ROI_MARGIN_ROOMS):
            keep |= {nb for r in keep for nb in adj.get(r, ())}
        return frozenset(keep)

    @staticmethod
    def _search_roi(
        labels: np.ndarray,
        door_rooms: Mapping[int, tuple[int, int]],
        s_cell: Cell,
        w_bend: float,
        cells: np.ndarray,
        rooms: frozenset[int],
        points: Mapping[Hashable, Cell],
    ) -> tuple[_Searched, np.ndarray, int]:
        """Search the ROI's rooms, the doors between them and the points' own cells, cropped."""
        allowed = set(rooms) | {d for d, (a, b) in door_rooms.items() if a in rooms and b in rooms}
        allowed |= {int(labels[cell]) for cell in points.values()}
        mask = np.isin(labels, list(allowed)) & (labels != BLOCKED)
        rows, cols = np.nonzero(mask.any(axis=1))[0], np.nonzero(mask.any(axis=0))[0]
        r0, r1, c0, c1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
        crop = np.where(mask, labels, BLOCKED)[r0:r1, c0:c1]
        local = [(r - r0, c - c0) for r, c in cells.tolist()]
        res = search(crop, door_rooms, [(s_cell[0] - r0, s_cell[1] - c0)], w_bend, targets=local)
        return _Searched(res, (int(r0), int(c0))), mask, int(mask.sum())

    @staticmethod
    def _exact(
        run: tuple[_Searched, np.ndarray, int],
        labels: np.ndarray,
        door_rooms: Mapping[int, tuple[int, int]],
        points: Mapping[Hashable, Cell],
        reachable: Collection[Hashable],
    ) -> bool:
        """Whether no path leaving the ROI can beat any reachable point's ROI cost."""
        found, mask, _ = run
        us, xs = _exits(labels, mask, door_rooms)
        if not len(us):
            return True
        res, (r0, c0) = found.result, found.offset
        inside = np.minimum(res.dist[us[:, 0] - r0, us[:, 1] - c0].min(axis=1), res.settled)
        ts = np.array([points[k] for k in reachable], dtype=np.int64).reshape(-1, 2)
        roi_cost = res.dist[ts[:, 0] - r0, ts[:, 1] - c0].min(axis=1)
        for chunk in range(0, len(ts), 4096):  # (exits × targets) in bounded slices
            t = ts[chunk : chunk + 4096]
            away = np.abs(xs[:, None, 0] - t[None, :, 0]) + np.abs(xs[:, None, 1] - t[None, :, 1])
            bound = (inside[:, None] + 1 + away).min(axis=0)
            if (roi_cost[chunk : chunk + 4096] > bound + _EPS).any():
                return False
        return True

    @staticmethod
    def _costs(found: _Searched, keys: Sequence[Hashable], cells: np.ndarray) -> dict:
        """Every point's cheapest arrival cost (inf outside the searched crop)."""
        res, (r0, c0) = found.result, found.offset
        rows, cols, _ = res.dist.shape
        r, c = cells[:, 0] - r0, cells[:, 1] - c0
        inside = (r >= 0) & (r < rows) & (c >= 0) & (c < cols)
        cost = np.full(len(keys), math.inf)
        cost[inside] = res.dist[r[inside], c[inside]].min(axis=1)
        return dict(zip(keys, cost.tolist(), strict=True))

    # ----------------------------------------------------------------------- reuse (C29)

    _built: ClassVar[OrderedDict[tuple, PathCache]] = OrderedDict()

    @classmethod
    def cached(
        cls,
        grid: LabelGrid,
        sources: Mapping[Hashable, Cell],
        c: Constraints = _DEFAULT,
        *,
        targets: Mapping[Hashable, Cell] | None = None,
    ) -> PathCache:
        """``build``, once per (plan, grid_m, cost_weights) and points: the plan is the raster's
        content, so the same plan loaded twice reuses one build, and a what-if raster doesn't."""
        key = (
            hashlib.blake2b(grid.labels.tobytes(), digest_size=16).hexdigest(),
            grid.labels.shape,
            tuple(sorted(grid.door_rooms.items())),
            grid.cell_m,
            c.cost_weights,
            frozenset((k, (int(r), int(cc))) for k, (r, cc) in sources.items()),
            frozenset((k, (int(r), int(cc))) for k, (r, cc) in (targets or {}).items()),
        )
        if key in cls._built:
            cls._built.move_to_end(key)
            return cls._built[key]
        built = cls.build(grid, sources, c, targets=targets)
        cls._built[key] = built
        while len(cls._built) > CACHE_BUILDS_KEPT:
            cls._built.popitem(last=False)
        return built

    @classmethod
    def clear(cls) -> None:
        cls._built.clear()

    # ----------------------------------------------------------------------- lookups

    def dist(self, a: Hashable, b: Hashable) -> float:
        """Cost a → b in cells (as ``SearchResult.cost``); either end must be a source."""
        if a in self._dist:
            return self._dist[a][b]
        if b in self._dist:
            return self._dist[b][a]
        raise KeyError(f"neither {a!r} nor {b!r} is a searched source")

    def cost_m(self, a: Hashable, b: Hashable) -> float:
        """``dist`` in J metres: length weight · cell size · cells (bends at the ends excluded)."""
        return self.dist(a, b) * self._metres_per_cell

    def path(self, a: Hashable, b: Hashable) -> CachedPath:
        """The cached cable a → b: its cost, corner cells and polyline."""
        if (a, b) in self._paths:
            return self._paths[a, b]
        d = self.dist(a, b)
        if a in self._searched:
            found = self._searched[a]
            (r0, c0), (r, c) = found.offset, self.points[b]
            local = path_to(found.result, (r - r0, c - c0)) if not math.isinf(d) else []
            cells = [(lr + r0, lc + c0) for lr, lc in local]
        else:
            cells = self.path(b, a).cells[::-1]
        p = CachedPath(d, cells, [self._to_xy(cell) for cell in cells])
        self._paths[a, b] = p
        return p


def plan_points(
    grid: LabelGrid, plan: Plan, c: Constraints = _DEFAULT
) -> tuple[dict[str, Cell], dict[Candidate, Cell]]:
    """The cache's points for one plan: every node's snapped cell, and every eligible room's hub
    candidates (``rings.hub_candidates``, room by room, so each room keeps its own spots or its
    ``wall_clearance_only`` fallback). Candidates that can't snap are left out."""
    from hackhive.rings import hub_candidates  # grid imports rings, which this module serves

    nodes = {}
    for n in plan.nodes:
        try:
            nodes[n.id] = grid.snap(n.xy, n.room_id)[0]
        except ValueError as e:
            raise ValueError(f"{n.id}: {e}") from e
    cands: dict[Candidate, Cell] = {}
    for room in (r for r in plan.rooms if r.eligible):
        for cand in hub_candidates({room.id}, plan, c):
            try:
                cands[cand] = grid.snap(cand.xy, cand.room_id)[0]
            except ValueError:
                continue
    return nodes, cands


# --------------------------------------------------------------------------- rings (#15)

# Every corner combination is scored up to 5 nodes with all four options each; past that (the
# ≤ 20-node what-if, #28) one greedy pass per stop, so routing can't hang (#70 review).
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
    w_bend = bend_weight_cells(c, grid.cell_m)
    paths = []
    for a, b in zip(snapped, [*snapped[1:], snapped[0]], strict=True):
        cells = path_to(search(grid.labels, grid.door_rooms, [a.cell], w_bend), b.cell)
        if not cells:
            raise ValueError(f"{a.stop.id}→{b.stop.id} unreachable")
        paths.append([grid.to_xy(cell) for cell in cells])
    return _Prepared(snapped, paths, c)


def _on_line(k: Point2, a: Point2, b: Point2, tol: float) -> bool:
    """Whether k lies on the axis-aligned line through a and b (within tol)."""
    if abs(a[0] - b[0]) <= tol:  # vertical
        return abs(k[0] - a[0]) <= tol
    if abs(a[1] - b[1]) <= tol:  # horizontal
        return abs(k[1] - a[1]) <= tol
    return False


def _attach(p: Point2, corner: Point2 | None, path: list[Point2], tol: float) -> list[Point2]:
    """The exact point p joined to a grid path that starts at p's cell centre, via a corner.

    When the corner lies on the line of the path's first piece, the cable joins the run there
    and never reaches the centre, so the two legs at a node don't loop round it (an X at C, #15
    trap 3), and a corner past the run's next cell doesn't go on to C and come back (#70 review).
    """
    if corner is None:  # p is its cell centre (within tol): squared up later
        return [p, *path]
    if len(path) >= 2 and _on_line(corner, path[0], path[1], tol):
        return [p, corner, *path[1:]]
    return [p, corner, *path]


def _square_up(points: Sequence[Point2], tol: float) -> list[Point2]:
    """Every piece exactly horizontal or vertical, with both ends kept bit for bit.

    A piece off an axis by ≤ tol (a stop within 1 mm of its cell's row or column, #15 trap 5)
    is squared by moving its next point onto the previous one's coordinate, forwards from the
    first point and then backwards from the last, so the neighbouring run follows the exact
    point by ≤ tol. Pieces shorter than tol become zero-length and are dropped.

    A straight run pinned by both ends to lines ≤ tol apart (a near-aligned stop on the hub's
    straight run) gets a sub-mm jog at its start instead of a skew, so every piece stays
    exactly horizontal or vertical (decision_conventions §1, #70 review).
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
        a = out[-1]
        if (x, y) == a:
            continue
        dx, dy = abs(a[0] - x), abs(a[1] - y)
        if dx and dy and min(dx, dy) <= tol:  # pinned at both ends: jog at a, then the run
            out.append((x, a[1]) if dx <= dy else (a[0], y))
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
    # clean before squaring, then drop only exactly collinear vertices: a sub-mm jog must stay
    return clean_polyline(_square_up(clean_polyline(pts, tol), tol), 0.0)


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


def _options(
    s: _Snapped, retrace_ok: Collection[str], free: shapely.Geometry | None
) -> list[tuple[int | None, int | None]]:
    """(incoming, outgoing) corner pairs at a stop. Different corners always; the same corner
    only where a retrace is legal (a node in a single-door room) or no other corner exists (a
    stop within tol of its cell's row or column). A corner whose L to the cell centre leaves
    `free` (a wall inside a snapped neighbour cell, #70 review) is never used; with one corner
    left both stubs take it, since a strict retrace beats a rule-5 error."""
    if s.kind == "centre":
        return [(None, None)]
    ok = {
        k
        for k in (0, 1)
        if free is None or shapely.LineString([s.stop.xy, s.corners[k], s.centre]).within(free)
    }
    if not ok:  # ponytail: snap's nearest cell only; try its other neighbours if this ever fires
        raise ValueError(f"{s.stop.id}: no stub to its cell stays in free space")
    options: list[tuple[int | None, int | None]] = [(0, 1), (1, 0)]
    if s.kind == "near" or s.stop.id in retrace_ok or len(ok) == 1:
        options += [(0, 0), (1, 1)]
    return [o for o in options if set(o) <= ok]


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
    free: shapely.Geometry | None = None,
) -> list[RouteSegment]:
    """One closed ring as slide-8 route segments: stops[0] (the hub) → … → stops[-1] → stops[0].

    Each leg is `[exact from] + stub + grid path + stub + [exact to]` (#15): a stub is an
    axis-aligned L from the exact point to its snapped cell centre via one of `grid.snap`'s two
    corners, or joins the grid run at the corner when the corner lies on it. A node's incoming
    and outgoing stubs use different corners, unless the node is in `retrace_ok` (a single-door
    room, where the retrace is legal) or within tol of its cell's row or column. Among those
    choices the ring with the lowest J (`checker.metrics`) wins; ties go to the first; past
    `MAX_CORNER_COMBOS` combinations, one greedy pass per stop. With `free` (rule 5's free
    space), no stub corner runs through a wall. Raises ValueError for fewer than 2 stops, a stop
    its room can't snap or reach without a wall, or an unreachable leg.
    """
    prep = _prepare(grid, stops, c)
    n = len(prep.stops)
    options = [_options(s, retrace_ok, free) for s in prep.stops]
    cache: dict[tuple[int, int | None, int | None], list[Point2]] = {}

    def legs(choice: Sequence[tuple[int | None, int | None]]) -> list[list[Point2]]:
        out = []
        for i in range(n):
            key = (i, choice[i][1], choice[(i + 1) % n][0])
            if key not in cache:
                cache[key] = _leg(prep, *key)
            out.append(cache[key])
        return out

    def score(choice: Sequence[tuple[int | None, int | None]]) -> float:
        return _score(prep, legs(choice))

    if math.prod(len(o) for o in options) <= MAX_CORNER_COMBOS:
        best = list(min(itertools.product(*options), key=score))
    else:  # ponytail: one greedy pass from every stop's first option; exact search is #19's
        best = [o[0] for o in options]
        for i, opts in enumerate(options):
            best = min(([*best[:i], o, *best[i + 1 :]] for o in opts), key=score)
    return _segments(prep, best)


def single_door_nodes(plan: Plan, c: Constraints = _DEFAULT) -> frozenset[str]:
    """Nodes in single-door rooms (`graph.single_door_rooms`, exactly as check_rules counts
    them): a dead-end bathroom door is no second way out (2026-10-16)."""
    from hackhive.graph import build_graph, single_door_rooms

    one_door = single_door_rooms(build_graph(plan, c))
    return frozenset(n.id for n in plan.nodes if n.room_id in one_door)


def route_rings(
    grid: LabelGrid, plan: Plan, hubs: Sequence[Hub], c: Constraints = _DEFAULT
) -> list[RoutedRing]:
    """Every hub's ring, routed independently in hub order (no rip-up yet, #21)."""
    from hackhive.checker import _free_space, metrics  # checker imports grid, which imports us

    retrace_ok = single_door_nodes(plan, c)  # its build_graph fills the openings _free_space needs
    free = _free_space(plan, c).free  # rule 5's own geometry, so stubs and checker agree
    nodes = {n.id: n for n in plan.nodes}
    out = []
    for hub in hubs:
        stops = [
            Stop(hub.hub_id, hub.location, hub.room_id),
            *(Stop(n, nodes[n].xy, nodes[n].room_id) for n in hub.ring[1:-1]),
        ]
        segments = route_ring(grid, stops, c, retrace_ok=retrace_ok, free=free)
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


# --------------------------------------------------------------------------- benchmark (#19)

BUDGET_MS = 5000  # ADR-003: past this p95 per plan, take the ladder (20 cm cost grid first)


def _bench_one(path: Path, c: Constraints) -> dict[str, float | int | str]:
    """Load → raster → points → cache for one plan file, timed in ms."""
    from hackhive.graph import build_graph  # these import this module
    from hackhive.grid import LabelGrid
    from hackhive.io import load_plan

    plan = load_plan(path, c)
    build_graph(plan, c)  # fills Door.rooms and plan.openings (#9)
    t0 = time.perf_counter()
    grid = LabelGrid.from_plan(plan, cell_m=c.grid_m)
    t1 = time.perf_counter()
    nodes, cands = plan_points(grid, plan, c)
    t2 = time.perf_counter()
    pc = PathCache.build(grid, nodes, c, targets=cands)
    s = pc.stats
    roi = sum(s.roi_cells.values()) / max(1, len(s.roi_cells) * s.free_cells)
    return {
        "plan": plan.plan_id,
        "cells": s.free_cells,
        "nodes": len(nodes),
        "hub_cands": len(cands),
        "fallbacks": s.fallbacks,
        "roi_pct": 100 * roi,
        "grid_ms": (t1 - t0) * 1000,
        "points_ms": (t2 - t1) * 1000,
        "cache_ms": s.ms,
        "total_ms": (t1 - t0) * 1000 + (t2 - t1) * 1000 + s.ms,
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m hackhive.route --bench <dir>…``: routing ms per plan, then p50 and p95.

    Per plan: the 10 cm raster, the cache's points (nodes and every eligible room's hub
    candidates) and the path cache (one search per node; node–hub pairs by symmetry). ``total``
    is their sum; the ADR-003 budget is p95 ≤ 5 s per plan."""
    p = argparse.ArgumentParser(prog="python -m hackhive.route")
    p.add_argument("--bench", type=Path, nargs="+", required=True, metavar="DIR")
    args = p.parse_args(argv)
    c = Constraints()
    files = sorted(f for d in args.bench for f in d.glob("*.json"))
    cols = "plan cells nodes hub_cands fallbacks roi_pct grid_ms points_ms cache_ms total_ms"
    print(f"{'plan':<10}" + "".join(f"{h:>11}" for h in cols.split()[1:]))
    rows = []
    for f in files:
        try:
            row = _bench_one(f, c)
        except Exception as e:  # a plan that can't be routed is reported, not fatal
            print(f"{f.stem:<10} skipped: {type(e).__name__}: {e}")
            continue
        rows.append(row)
        cells = [
            f"{row[h]:>11.1f}" if isinstance(row[h], float) else f"{row[h]:>11}"
            for h in cols.split()[1:]
        ]
        print(f"{row['plan']!s:<10}" + "".join(cells))
    if not rows:
        print("no plans")
        return 1
    for h in ("cache_ms", "total_ms"):
        v = np.array([r[h] for r in rows], dtype=float)
        p50, p95 = np.percentile(v, 50), np.percentile(v, 95)
        print(f"{h}: p50 {p50:.0f} ms, p95 {p95:.0f} ms, max {v.max():.0f} ms over {len(v)} plans")
    p95 = float(np.percentile([r["total_ms"] for r in rows], 95))
    verdict = "within" if p95 <= BUDGET_MS else "OVER: apply the ADR-003 ladder (20 cm cost grid)"
    print(f"ADR-003 budget {BUDGET_MS} ms per plan at p95: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
