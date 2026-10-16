"""Acceptance tests for #19: the pairwise path cache and its routing benchmark (C12, C17, C22,
C29). Fixtures: load_plan → build_graph → LabelGrid, as in test_route_rings.py."""

import math
import random
from functools import cache
from pathlib import Path

import numpy as np
import pytest

from hackhive import route
from hackhive.contracts import Constraints, CostWeights
from hackhive.graph import build_graph
from hackhive.grid import LabelGrid
from hackhive.io import load_plan
from hackhive.route import (
    PathCache,
    bend_weight_cells,
    bends,
    path_length,
    plan_points,
    search,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PLAN_IDS = ["5088", "576", "1293", "2814", "3086", "5924", "13344"]
C = Constraints()
BLOCKED = -1


@cache
def _grid(plan_id: str, cell_m: float = C.grid_m) -> LabelGrid:
    plan = load_plan(FIXTURES / "plans" / f"plan-{plan_id}.json")
    build_graph(plan, C)  # fills Door.rooms and plan.openings (#9)
    return LabelGrid.from_plan(plan, cell_m=cell_m)


def _free_cells(grid: LabelGrid) -> list[tuple[int, int]]:
    return [(int(r), int(c)) for r, c in np.argwhere(grid.labels != BLOCKED)]


def _manhattan(a, b) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _w(grid: LabelGrid) -> float:
    return bend_weight_cells(C, grid.cell_m)


# --------------------------------------------------------------------------- exactness (C17)


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_cache_distance_equals_direct_search_on_random_pairs(plan_id):
    """Given random source/target pairs on the fixtures, the cache distance equals the direct
    `search` distance, in both directions (one search per source, targets by symmetry)."""
    grid = _grid(plan_id)
    rng = random.Random(f"19-{plan_id}")
    free = _free_cells(grid)
    sources = {f"s{i}": cell for i, cell in enumerate(rng.sample(free, 3))}
    targets = {f"t{i}": cell for i, cell in enumerate(rng.sample(free, 25))}
    pc = PathCache.build(grid, sources, C, targets=targets)
    assert pc.stats.searches == len(sources)
    for s, s_cell in sources.items():
        direct = search(grid.labels, grid.door_rooms, [s_cell], _w(grid))
        for t, t_cell in {**sources, **targets}.items():
            assert pc.dist(s, t) == pytest.approx(direct.cost(t_cell), abs=1e-9), (s, t)
    for t in ("t0", "t1"):  # a target's direct search gives the same costs back (symmetry)
        direct = search(grid.labels, grid.door_rooms, [targets[t]], _w(grid))
        for s, s_cell in sources.items():
            assert pc.dist(t, s) == pytest.approx(direct.cost(s_cell), abs=1e-9), (t, s)


@pytest.mark.parametrize("plan_id", ["5088", "3086", "13344"])
def test_cache_is_exact_from_nodes_to_hub_candidates(plan_id):
    """The pairs #20 reads: every node to every node and to a sample of hub candidates."""
    grid = _grid(plan_id)
    plan = load_plan(FIXTURES / "plans" / f"plan-{plan_id}.json")
    nodes, cands = plan_points(grid, plan, C)
    assert set(nodes) == {n.id for n in plan.nodes}
    assert cands, "every fixture has hub candidates"
    sample = dict(random.Random(19).sample(sorted(cands.items(), key=str), 40))
    pc = PathCache.build(grid, nodes, C, targets=sample)
    for n, cell in nodes.items():
        direct = search(grid.labels, grid.door_rooms, [cell], _w(grid))
        for t, t_cell in {**nodes, **sample}.items():
            assert pc.dist(n, t) == pytest.approx(direct.cost(t_cell), abs=1e-9)
            assert pc.dist(t, n) == pc.dist(n, t)


def _detour_raster() -> tuple[np.ndarray, dict[int, tuple[int, int]]]:
    """Rooms A=0 … E=4. A and B share one door, at the end of a long arm (≈ 88 steps); the
    straight way A → C → D → E → B (≈ 47 steps) is 4 door hops, so with a 1-room margin the
    ROI is {A, B, C, E} and misses D."""
    labels = np.full((26, 60), BLOCKED, dtype=np.int16)
    labels[1:6, 1:11] = 0  # A
    labels[6:20, 1:4] = 0  # A's arm, down …
    labels[20:25, 1:59] = 0  # … and along the bottom
    labels[1:6, 50:59] = 1  # B
    labels[6:19, 55:59] = 1  # B's leg down to the arm
    labels[1:6, 12:25] = 2  # C
    labels[1:6, 26:37] = 3  # D
    labels[1:6, 38:49] = 4  # E
    labels[19, 56:58] = 5  # door A–B (arm to B's leg)
    labels[3, 11] = 6  # door A–C
    labels[3, 25] = 7  # door C–D
    labels[3, 37] = 8  # door D–E
    labels[3, 49] = 9  # door E–B
    return labels, {5: (0, 1), 6: (0, 2), 7: (2, 3), 8: (3, 4), 9: (4, 1)}


def test_roi_misses_the_shorter_way_and_falls_back_to_the_full_search():
    """The door-graph ROI is a speed-up only: when a path leaving it could be cheaper (the
    Manhattan bound from its exits), the source is searched again on the whole raster."""
    labels, door_rooms = _detour_raster()
    a, b = (3, 5), (3, 53)
    direct = search(labels, door_rooms, [a], 0.5).cost(b)
    assert direct < 60  # the straight way through D
    pc = PathCache.build_raw(labels, door_rooms, {"a": a}, 0.5, targets={"b": b})
    assert pc.stats.roi_rooms["a"] == frozenset({0, 1, 2, 4})
    assert pc.stats.fallbacks == 1
    assert pc.dist("a", "b") == pytest.approx(direct)


def test_roi_alone_is_enough_when_no_exit_can_beat_it():
    labels, door_rooms = _detour_raster()
    a, c = (3, 5), (3, 15)
    pc = PathCache.build_raw(labels, door_rooms, {"a": a}, 0.5, targets={"c": c})
    assert pc.stats.roi_rooms["a"] == frozenset({0, 1, 2, 3})  # path A–C, margin B and D
    assert pc.stats.fallbacks == 0
    assert pc.stats.roi_cells["a"] < int((labels != BLOCKED).sum())
    assert pc.dist("a", "c") == pytest.approx(search(labels, door_rooms, [a], 0.5).cost(c))


def test_unreachable_target_is_inf_without_a_fallback():
    """A room no door reaches is unreachable on the door graph too: inf, no full search."""
    labels, door_rooms = _detour_raster()
    labels[labels >= 5] += 1  # doors move up one id, so room 5 can be an island
    door_rooms = {d + 1: pair for d, pair in door_rooms.items()}
    labels[9:12, 29:32] = 5  # a room with no door
    pc = PathCache.build_raw(labels, door_rooms, {"a": (3, 5)}, 0.5, targets={"x": (10, 30)})
    assert math.isinf(pc.dist("a", "x"))
    assert pc.stats.fallbacks == 0
    assert pc.path("a", "x").cells == []


# --------------------------------------------------------------------------- polylines (C12)


@pytest.mark.parametrize("plan_id", ["5088", "576", "2814"])
def test_every_cached_pair_is_at_least_the_manhattan_bound(plan_id):
    """Given every cached pair, the distance ≥ the Manhattan LB, and its polyline is an
    axis-aligned path from source to target whose steps + w_bend · bends is that distance."""
    grid = _grid(plan_id)
    plan = load_plan(FIXTURES / "plans" / f"plan-{plan_id}.json")
    nodes, _ = plan_points(grid, plan, C)
    pc = PathCache.build(grid, nodes, C)
    pairs = [(a, b) for a in nodes for b in nodes if a != b]
    assert pairs
    for a, b in pairs:
        p = pc.path(a, b)
        assert p.dist >= _manhattan(nodes[a], nodes[b]) - 1e-9
        assert p.cells[0] == nodes[a] and p.cells[-1] == nodes[b]
        for (r0, c0), (r1, c1) in zip(p.cells, p.cells[1:], strict=False):
            assert r0 == r1 or c0 == c1
        assert path_length(p.cells) >= _manhattan(nodes[a], nodes[b])
        assert path_length(p.cells) + _w(grid) * bends(p.cells) == pytest.approx(p.dist)
        assert p.polyline == [grid.to_xy(cell) for cell in p.cells]
        assert pc.path(b, a).cells == p.cells[::-1]  # symmetry: the same cable, reversed
        assert pc.cost_m(a, b) == pytest.approx(p.dist * grid.cell_m)


def test_unknown_or_target_only_pairs_raise():
    grid = _grid("5088")
    free = _free_cells(grid)
    pc = PathCache.build(grid, {"s": free[0]}, C, targets={"t": free[-1], "u": free[1]})
    with pytest.raises(KeyError):
        pc.dist("t", "u")  # neither end was searched
    with pytest.raises(KeyError):
        pc.dist("s", "nope")


# --------------------------------------------------------------------------- reuse (C29)


def test_cache_is_built_once_per_plan_grid_and_weights(monkeypatch):
    """Given a plan, the cache is built once per (plan, grid_m, cost_weights) and reused."""
    PathCache.clear()
    calls = []
    real = route.search
    monkeypatch.setattr(route, "search", lambda *a, **k: calls.append(1) or real(*a, **k))
    grid = _grid("5088")
    plan = load_plan(FIXTURES / "plans" / f"plan-5088.json")
    nodes, _ = plan_points(grid, plan, C)

    first = PathCache.cached(grid, nodes, C)
    n_first = len(calls)
    assert n_first == first.stats.searches + first.stats.fallbacks
    assert first.stats.searches == len(nodes)
    assert PathCache.cached(grid, nodes, C) is first
    assert len(calls) == n_first, "a second call searches nothing"

    build_graph(plan, C)
    again = LabelGrid.from_plan(plan)  # the same plan rebuilt: same content, same cache
    assert PathCache.cached(again, nodes, C) is first

    dearer = Constraints(cost_weights=CostWeights(1.0, 5.0, 1.0, 2.0))
    assert PathCache.cached(grid, nodes, dearer) is not first
    coarse = _grid("5088", 0.2)
    coarse_nodes, _ = plan_points(coarse, plan, C)
    assert PathCache.cached(coarse, coarse_nodes, C) is not first
    assert len(calls) >= 3 * len(nodes)


# --------------------------------------------------------------------------- benchmark (C22)


def test_bench_prints_ms_per_plan_and_percentiles(tmp_path, capsys):
    for pid in ("5088", "3086"):
        src = FIXTURES / "plans" / f"plan-{pid}.json"
        (tmp_path / src.name).write_bytes(src.read_bytes())
    (tmp_path / "notes.txt").write_text("not a plan")
    assert route.main(["--bench", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    rows = [line for line in out.splitlines() if line.startswith(("5088", "3086"))]
    assert len(rows) == 2
    assert "ms" in out
    assert "p50" in out and "p95" in out
