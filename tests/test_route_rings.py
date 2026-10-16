"""Acceptance tests for #15: route ordered rings into slide-8 route segments (C10, C12-C15,
C17, C40-C42, C48). Fixtures: load_plan → build_graph → the fewest-hubs zoning → first_ring per
zone → LabelGrid, as in test_rings.py; every fixture plan is routed once per module."""

import math
import subprocess
import sys
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest
import shapely
from shapely.geometry import box

from hackhive import route
from hackhive.checker import (
    _free_space,
    _overlaps,
    check_rules,
    count_bends,
    metrics,
    segment_length,
)
from hackhive.contracts import Constraints, HubOut, Submission
from hackhive.graph import build_graph, eligible_graph
from hackhive.grid import LabelGrid
from hackhive.io import load_plan
from hackhive.rings import first_ring
from hackhive.route import Stop, route_ring, route_rings
from hackhive.zoning import eligible_area_and_nodes, enumerate_zonings, min_hubs

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PLAN_IDS = ["5088", "576", "1293", "2814", "3086", "5924", "13344"]
C = Constraints()
TOL = C.tolerance_m


def _route(plan_id: str, c: Constraints = C) -> SimpleNamespace:
    plan = load_plan(FIXTURES / "plans" / f"plan-{plan_id}.json")
    G = build_graph(plan, C)
    g = eligible_graph(G)
    area, nodes = eligible_area_and_nodes(plan)
    zonings = enumerate_zonings(g, area, C, nodes=nodes)
    k = min_hubs(zonings)
    zoning = next(z for z in zonings if len(z) == k)
    hubs = [first_ring(zone, plan, g, C, hub_id=f"H{i}") for i, zone in enumerate(zoning, 1)]
    grid = LabelGrid.from_plan(plan)
    rings = route_rings(grid, plan, hubs, c, zones=list(zoning))
    return SimpleNamespace(plan=plan, G=G, zoning=zoning, hubs=hubs, grid=grid, rings=rings)


@pytest.fixture(scope="module")
def routed() -> dict[str, SimpleNamespace]:
    return {pid: _route(pid) for pid in PLAN_IDS}


def _hub_out(hub, ring, zone) -> dict:
    rooms = [hub.room_id, *sorted(set(zone) - {hub.room_id})]
    return {
        "hub_id": hub.hub_id,
        "room_id": hub.room_id,
        "location": {"x": hub.location[0], "y": hub.location[1]},
        "assigned_rooms": rooms,
        "assigned_nodes": sorted(hub.ring[1:-1]),
        "closed_sequence": hub.ring,
        "route_segments": [s.model_dump(by_alias=True) for s in ring.segments],
        "ring_length_m": ring.ring_length_m,
    }


def submission(case) -> Submission:
    hubs = [_hub_out(h, r, z) for h, r, z in zip(case.hubs, case.rings, case.zoning, strict=True)]
    return Submission.model_validate(
        {
            "plan_id": case.plan.plan_id,
            "units": "m",
            "hubs": hubs,
            "reported_metrics": {
                "hub_count": len(hubs),
                "total_length_m": sum(h["ring_length_m"] for h in hubs),
            },
        }
    )


def _stops(case):
    """Every stop of every ring, in ring order (the hub first), with its plan point."""
    points = {n.id: n for n in case.plan.nodes}
    for hub in case.hubs:
        yield (
            hub,
            [
                Stop(hub.hub_id, hub.location, hub.room_id),
                *(Stop(n, points[n].xy, points[n].room_id) for n in hub.ring[1:-1]),
            ],
        )


# --------------------------------------------------------------------------- acceptance criteria


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_rings_are_axis_aligned_and_pass_rule_5(routed, plan_id):
    """C12, C13: every piece exactly horizontal or vertical; checker rule 5 passes and no rule
    gives an error (grid-level retraces stay rule-4 `strict`, #21)."""
    case = routed[plan_id]
    for ring in case.rings:
        for s in ring.segments:
            assert all(p[0] == q[0] or p[1] == q[1] for p, q in pairwise(s.polyline)), s
    violations = check_rules(case.plan, submission(case), C)
    assert not [v for v in violations if v.rule == 5]
    assert not [v for v in violations if v.severity == "error"]


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_legs_chain_around_the_ring(routed, plan_id):
    """C10, C41, C48: segment i starts where i−1 ended; the ring starts and ends at the hub."""
    case = routed[plan_id]
    for hub, ring in zip(case.hubs, case.rings, strict=True):
        segs = ring.segments
        assert [(s.from_, s.to) for s in segs] == list(pairwise(hub.ring))
        assert math.dist(segs[0].polyline[0], hub.location) <= TOL
        assert math.dist(segs[-1].polyline[-1], hub.location) <= TOL
        for a, b in pairwise(segs):
            assert math.dist(a.polyline[-1], b.polyline[0]) <= TOL


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_legs_are_at_least_manhattan(routed, plan_id):
    """C12."""
    for ring in routed[plan_id].rings:
        for s in ring.segments:
            (x0, y0), (x1, y1) = s.polyline[0], s.polyline[-1]
            assert s.length_m >= abs(x1 - x0) + abs(y1 - y0) - 1e-9


def test_5924_ends_are_the_exact_points(routed):
    """C3, C12, C41, C48: first and last points are the plan's points, bit for bit."""
    case = routed["5924"]
    points = {n.id: n.xy for n in case.plan.nodes}
    for hub, ring in zip(case.hubs, case.rings, strict=True):
        points[hub.hub_id] = hub.location
        for s in ring.segments:
            assert tuple(s.polyline[0]) == tuple(points[s.from_])
            assert tuple(s.polyline[-1]) == tuple(points[s.to])


def _living_cell(case):
    cell = case.grid.cell_of((2.5, 3.0))
    return cell, case.grid.to_xy(cell)


def _first_lattice_index(polyline, grid) -> int:
    """Index of the first vertex on a cell centre."""
    for i, p in enumerate(polyline):
        cx, cy = grid.to_xy(grid.cell_of(p))
        if abs(p[0] - cx) <= TOL and abs(p[1] - cy) <= TOL:
            return i
    raise AssertionError("no vertex on a cell centre")


def test_seven_cm_stub_is_one_l_inside_the_room(routed):
    """C12, C13: a node 4 cm left of and 3 cm below its cell centre gets an L of 7 cm, inside
    its room, and rules 4 (endpoints) and 5 pass. One of its two legs may join the grid run at
    the corner instead (the corner lies on that run, so the cable never reaches C, trap 3)."""
    case = routed["5088"]
    (cell, (cx, cy)), hub = _living_cell(case), case.hubs[0]
    p = (cx - 0.04, cy - 0.03)
    assert case.grid.snap(p, "R01")[0] == cell
    stops = [Stop(hub.hub_id, hub.location, hub.room_id), Stop("N9", p, "R01")]
    segs = route_ring(case.grid, stops, C)
    living = next(r for r in case.plan.rooms if r.id == "R01").polygon
    stubs = []
    for s, at_end in ((segs[0], True), (segs[1], False)):
        line = list(reversed(s.polyline)) if at_end else list(s.polyline)
        stub = line[: _first_lattice_index(line, case.grid) + 1]
        assert all(living.covers(shapely.Point(q)) for q in stub)
        stubs.append(stub)
    full = [st for st in stubs if st[-1] == (cx, cy)]
    assert full and all(len(st) == 3 for st in full)
    assert segment_length(full[0]) == pytest.approx(0.07, abs=TOL)
    hub_out = _hub_out(hub, SimpleNamespace(segments=segs, ring_length_m=0), {"R01"})
    hub_out.update(assigned_nodes=[], closed_sequence=[hub.hub_id, "N9", hub.hub_id])
    sub = Submission.model_validate(
        {
            "plan_id": case.plan.plan_id,
            "units": "m",
            "hubs": [hub_out],
            "reported_metrics": {"hub_count": 1, "total_length_m": 0},
        }
    )
    found = check_rules(case.plan, sub, C)
    assert not [v for v in found if v.rule == 5]
    assert not [v for v in found if v.rule == 4 and "cm from" in v.message]


def test_no_extra_bend_at_the_cell_centre(routed):
    """C17, C40: on 5088, a leg whose grid run leaves its start's cell heading east enters that
    run horizontally, or joins it before the cell centre: never a vertical piece into C."""
    case = routed["5088"]
    checked = 0
    for hub, stops in _stops(case):
        ring = next(r for r in case.rings if r.hub_id == hub.hub_id)
        for (a, b), seg in zip(pairwise([*stops, stops[0]]), ring.segments, strict=True):
            cell_a = case.grid.snap(a.xy, a.room_id)[0]
            cell_b = case.grid.snap(b.xy, b.room_id)[0]
            found = route.search(
                case.grid.labels, case.grid.door_rooms, [cell_a], route.bend_weight_cells(C)
            )
            cells = route.path_to(found, cell_b)
            (x0, y0), (x1, y1) = case.grid.to_xy(cells[0]), case.grid.to_xy(cells[1])
            if not (y1 == y0 and x1 > x0) or tuple(a.xy) == (x0, y0):
                continue  # not heading east, or no stub (the stop is its cell centre)
            checked += 1
            line = seg.polyline
            for i in range(1, len(line) - 1):
                if tuple(line[i]) == (x0, y0):
                    assert line[i - 1][1] == y0, (a.id, b.id, line)
    assert checked


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_stubs_never_retrace_at_a_node_unless_legal(routed, plan_id):
    """C15, C40: at a node not in a single-door room (and not within 1 mm of its cell's row or
    column), the incoming and outgoing legs share no stretch inside the stub's box."""
    case = routed[plan_id]
    single_door = route.single_door_nodes(case.plan, C)  # the checker's own definition
    for hub, stops in _stops(case):
        ring = next(r for r in case.rings if r.hub_id == hub.hub_id)
        for i, stop in enumerate(stops[1:], start=1):
            cell, _ = case.grid.snap(stop.xy, stop.room_id)
            cx, cy = case.grid.to_xy(cell)
            dx, dy = abs(stop.xy[0] - cx), abs(stop.xy[1] - cy)
            if stop.id in single_door or dx <= TOL or dy <= TOL:
                continue
            # the stub's own box, not grown: grid runs leaving C only touch its corner
            area = box(
                min(stop.xy[0], cx), min(stop.xy[1], cy), max(stop.xy[0], cx), max(stop.xy[1], cy)
            )
            clipped = []
            for seg in (ring.segments[i - 1], ring.segments[i]):
                part = shapely.LineString(seg.polyline).intersection(area)
                clipped += [list(g.coords) for g in getattr(part, "geoms", [part]) if g.length > 0]
            total, _ = _overlaps(clipped, [str(k) for k in range(len(clipped))], TOL)
            assert total <= TOL, (plan_id, stop.id, total)


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_bends_agree_with_the_checker(routed, plan_id):
    """C40, C42: B from checker.metrics on the written polylines is what the router reports."""
    case = routed[plan_id]
    for hub, ring, zone in zip(case.hubs, case.rings, case.zoning, strict=True):
        hub_out = HubOut.model_validate(_hub_out(hub, ring, zone))
        polylines = [s.polyline for s in ring.segments]
        assert ring.bends == metrics([hub_out], C).per_hub[hub.hub_id].B
        assert ring.bends == count_bends(polylines, closed=True)


def test_corner_choice_beats_the_literal_rule(routed):
    """Choosing corners by J is never worse than always (in corner 0, out corner 1)."""
    case = routed["5088"]
    hub, stops = next(_stops(case))
    zone = case.zoning[0]
    chosen = route_ring(case.grid, stops, C, retrace_ok=route.single_door_nodes(case.plan, C))
    forced = route._segments(route._prepare(case.grid, stops, C), [(0, 1)] * len(stops))
    j = [
        metrics(
            [
                HubOut.model_validate(
                    _hub_out(hub, SimpleNamespace(segments=segs, ring_length_m=0), zone)
                )
            ],
            C,
        ).J
        for segs in (chosen, forced)
    ]
    assert j[0] <= j[1]


def test_near_aligned_node_is_squared_exactly(routed):
    """Trap 5: a stop 0.5 mm off its cell centre's row gives exactly axis-aligned pieces, none
    shorter than 1 mm."""
    case = routed["5088"]
    (_, (cx, cy)), hub = _living_cell(case), case.hubs[0]
    stops = [
        Stop(hub.hub_id, hub.location, hub.room_id),
        Stop("N9", (cx - 0.03, cy + 0.0005), "R01"),
    ]
    for s in route_ring(case.grid, stops, C):
        for p, q in pairwise(s.polyline):
            assert p[0] == q[0] or p[1] == q[1]
            assert math.dist(p, q) > TOL


def _reversals(polyline) -> list:
    """Vertices where a leg turns straight back on itself (it retraces its own last piece)."""
    return [
        b
        for a, b, c in zip(polyline, polyline[1:], polyline[2:], strict=False)
        if (a[0] == b[0] == c[0] and (b[1] - a[1]) * (c[1] - b[1]) < 0)
        or (a[1] == b[1] == c[1] and (b[0] - a[0]) * (c[0] - b[0]) < 0)
    ]


def _near_door_576(routed):
    """#70 review: a node 6.8 cm from a wall beside a door in 576's R04, routed from H5; its
    nearest free cell is a diagonal neighbour, so one stub corner lies inside the wall."""
    case = routed["576"]
    hub = next(h for h in case.hubs if h.hub_id == "H5")
    stops = [Stop(hub.hub_id, hub.location, hub.room_id), Stop("N9", (17.5602, 9.2996), "R04")]
    return case, stops


def test_stubs_stay_in_free_space(routed):
    """C13, rule 5 (#70 review): a stub never takes a corner whose L runs through a wall."""
    case, stops = _near_door_576(routed)
    free = _free_space(case.plan, C).free
    for s in route_ring(case.grid, stops, C, free=free):
        assert shapely.LineString(s.polyline).within(free), s.polyline


def test_a_leg_never_doubles_back_on_itself(routed):
    """C15 (#70 review): when a stub's corner lies past the run's first cell on the run's own
    line, the cable joins the run there instead of going on to C and coming back."""
    case, stops = _near_door_576(routed)
    free = _free_space(case.plan, C).free
    for s in route_ring(case.grid, stops, C, free=free):
        assert not _reversals(s.polyline), s.polyline
    for ring in (r for pid in PLAN_IDS for r in routed[pid].rings):
        for s in ring.segments:
            assert not _reversals(s.polyline), (ring.hub_id, s.from_, s.to, s.polyline)


def test_near_aligned_node_on_the_hubs_row_is_exactly_axis_aligned(routed):
    """decision_conventions §1 (#70 review): a node 0.5 mm off the row of a hub's straight run
    gets a sub-mm jog, never a 0.5 mm diagonal; both ends stay the exact points."""
    case = routed["5088"]
    hub = case.hubs[0]
    node = Stop("N9", (1.98, hub.location[1] + 0.0005), "R01")
    for s in route_ring(case.grid, [Stop(hub.hub_id, hub.location, hub.room_id), node], C):
        assert all(p[0] == q[0] or p[1] == q[1] for p, q in pairwise(s.polyline)), s.polyline
        assert {tuple(s.polyline[0]), tuple(s.polyline[-1])} == {hub.location, node.xy}


def test_bend_weight_uses_the_grids_cell_size(routed, monkeypatch):
    """#70 review: a step is one grid cell, so the bend weight converts with the grid's own cell
    size, not `c.grid_m` (LabelGrid.from_plan doesn't read c)."""
    case = routed["5088"]
    seen = []
    real = route.search
    monkeypatch.setattr(route, "search", lambda *a: seen.append(a[3]) or real(*a))
    route_ring(case.grid, next(_stops(case))[1], Constraints(grid_m=0.05))
    c = Constraints().cost_weights
    assert set(seen) == {c.bend / (c.length * case.grid.cell_m)}


def test_corner_search_is_bounded(routed, monkeypatch):
    """#70 review: past `MAX_CORNER_COMBOS` (the ≤ 20-node what-if) the corner choice is one
    pass per stop, not every combination, so routing can't hang."""
    case = routed["5088"]
    hub, stops = next(_stops(case))
    nodes = [s for s in stops[1:]] * 2  # 10 stops, every one allowed all 4 corner pairs
    calls = []
    real = route._score

    def score(*a):
        calls.append(1)
        assert len(calls) <= route.MAX_CORNER_COMBOS, "scored every combination"
        return real(*a)

    monkeypatch.setattr(route, "_score", score)
    segs = route_ring(case.grid, [stops[0], *nodes], C, retrace_ok={s.id for s in nodes})
    assert len(segs) == len(nodes) + 1


def test_errors_name_the_stop_or_leg(routed):
    case = routed["5088"]
    hub = case.hubs[0]
    h = Stop(hub.hub_id, hub.location, hub.room_id)
    with pytest.raises(ValueError):
        route_ring(case.grid, [h], C)
    with pytest.raises(ValueError, match="N9"):
        route_ring(case.grid, [h, Stop("N9", (2.5, 3.0), "R02")], C)
    case = routed["2814"]
    lonely = next(n for n in case.plan.nodes if case.G.degree(n.room_id) == 0)
    other = next(n for n in case.plan.nodes if n.id != lonely.id)
    stops = [Stop(other.id, other.xy, other.room_id), Stop(lonely.id, lonely.xy, lonely.room_id)]
    with pytest.raises(ValueError, match=f"{other.id}→{lonely.id} unreachable"):
        route_ring(case.grid, stops, C)


@pytest.mark.parametrize("module", ["hackhive.route", "hackhive.grid", "hackhive.checker"])
def test_each_module_imports_on_its_own(module):
    """Trap 2: route imports neither grid nor checker at the top (they import route)."""
    subprocess.run([sys.executable, "-c", f"import {module}"], check=True)


# --------------------------------------------------------------------------- own rooms (D7, #79)


def _length_inside(polyline, polygon) -> float:
    """Metres of the polyline strictly inside the polygon (shrunk 1 mm, so a run along a shared
    edge or through a door in the wall doesn't count)."""
    return shapely.LineString(polyline).intersection(polygon.buffer(-TOL)).length


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_own_rooms_every_fixture(routed, plan_id):
    """C33: by default no ring runs inside an excluded room or another hub's room."""
    case = routed[plan_id]
    for ring, zone in zip(case.rings, case.zoning, strict=True):
        for room in case.plan.rooms:
            if room.id in zone:
                continue
            inside = sum(_length_inside(s.polyline, room.polygon) for s in ring.segments)
            assert inside <= TOL, f"{ring.hub_id} runs {inside:.3f} m inside {room.id}"


def _detour_plan():
    """bedroom | bathroom | bedroom on top, each bedroom with a door to the bathroom, and a
    living room along the bottom with a door into each bedroom (1 unit = 0.1 m)."""
    from shapely.geometry import MultiPolygon, mapping

    walls = [
        *(box(-2, 48, 10, 50), box(20, 48, 80, 50), box(90, 48, 102, 50)),
        *(box(40, 48, 42, 70), box(40, 80, 42, 102), box(62, 48, 64, 70), box(62, 80, 64, 102)),
    ]
    doors = [box(40, 70, 42, 80), box(62, 70, 64, 80), box(10, 48, 20, 50), box(80, 48, 90, 50)]
    geoms = {
        "bedroom": MultiPolygon([box(0, 50, 40, 100), box(64, 50, 100, 100)]),
        "bathroom": box(42, 50, 62, 100),
        "living": box(0, 0, 100, 48),
        "wall": MultiPolygon(walls),
        "door": MultiPolygon(doors),
    }
    raw = {"id": "SYN", "area": 100.0, "wall_depth": 2.0, "inner": mapping(box(0, 0, 100, 100))}
    plan = load_plan(raw | {k: mapping(g) for k, g in geoms.items()})
    rooms = {r.label: r for r in plan.rooms}
    node = {n.room_id: n for n in plan.nodes}
    return plan, rooms, node


def _detour_hub(plan, rooms, node, zone, hub_room, ring_rooms):
    from hackhive.rings import Hub

    x, y = node[rooms[hub_room].id].xy
    nodes = [node[rooms[r].id].id for r in ring_rooms]
    return Hub("H1", rooms[hub_room].id, (x + 1.0, y), ["H1", *nodes, "H1"]), [
        {rooms[r].id for r in zone}
    ]


def test_own_rooms_detour_vs_whatif():
    """C17, C33: the shortest bedroom → bedroom leg cuts through the bathroom; by default the
    ring goes round through the living room, and with the WHAT-IF it takes the bathroom."""
    plan, rooms, node = _detour_plan()
    hub, zones = _detour_hub(
        plan, rooms, node, ["living_0", "bedroom_0", "bedroom_1"], "living_0",
        ["bedroom_0", "bedroom_1", "living_0"],
    )  # fmt: skip
    grid = LabelGrid.from_plan(plan)
    bath = rooms["bathroom_0"].polygon
    own = route_rings(grid, plan, [hub], C, zones=zones)[0]
    loose = route_rings(
        grid, plan, [hub], Constraints(route_through_other_rooms=True), zones=zones
    )[0]
    assert sum(_length_inside(s.polyline, bath) for s in own.segments) <= TOL
    assert sum(_length_inside(s.polyline, bath) for s in loose.segments) > 1.0
    assert loose.ring_length_m < own.ring_length_m


def test_own_rooms_flag_is_whatif():
    """C29: allowing cable through other rooms is not the problem statement."""
    assert Constraints().route_through_other_rooms is False
    assert Constraints(route_through_other_rooms=True).is_problem_statement is False


def test_own_rooms_unreachable_raises():
    """Two bedrooms joined only through rooms outside the zone: no silent fallback."""
    plan, rooms, node = _detour_plan()
    hub, zones = _detour_hub(
        plan, rooms, node, ["bedroom_0", "bedroom_1"], "bedroom_0", ["bedroom_0", "bedroom_1"]
    )
    grid = LabelGrid.from_plan(plan)
    with pytest.raises(ValueError, match="D7"):
        route_rings(grid, plan, [hub], C, zones=zones)
    whatif = Constraints(route_through_other_rooms=True)
    assert route_rings(grid, plan, [hub], whatif, zones=zones)[0].ring_length_m > 0


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_own_rooms_j_unchanged(routed, plan_id):
    """C13, C17: no fixture ring used another room before, so J is the same with the WHAT-IF
    on, and rule 5 still passes."""
    case = routed[plan_id]
    loose = _route(plan_id, Constraints(route_through_other_rooms=True))
    j = metrics(submission(case).hubs, C).J
    assert metrics(submission(loose).hubs, C).J == pytest.approx(j, abs=TOL)
    assert not [v for v in check_rules(case.plan, submission(case), C) if v.rule == 5]


# --------------------------------------------------------------------------- see it


def demo() -> None:
    """One line per fixture: plan H L X O B J, then the checker's rule-4 strict and rule-5 counts."""
    for pid in PLAN_IDS:
        case = _route(pid)
        m = metrics(submission(case).hubs, C)
        found = check_rules(case.plan, submission(case), C)
        strict4 = sum(v.rule == 4 and v.severity == "strict" for v in found)
        rule5 = sum(v.rule == 5 for v in found)
        print(
            f"{pid:>5} H {m.H} L {m.L:7.2f} X {m.X:2d} O {m.O:6.3f} B {m.B:3d} J {m.J:7.2f}"
            f"  rule4_strict {strict4}  rule5 {rule5}"
        )


if __name__ == "__main__":
    demo()
