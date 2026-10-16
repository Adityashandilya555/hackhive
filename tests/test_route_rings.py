"""Acceptance tests for #15: route ordered rings into slide-8 route segments (C10, C12-C15,
C17, C40-C42, C48). Fixtures: load_plan → build_graph → the fewest-hubs zoning → first_ring per
zone → LabelGrid, as in test_rings.py; every fixture plan is routed once per module."""

import ast
import math
import subprocess
import sys
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import shapely
from shapely.geometry import box

from hackhive import route
from hackhive.checker import (
    _free_space,
    _overlaps,
    check_rules,
    count_bends,
    crossing_points,
    metrics,
    segment_length,
)
from hackhive.contracts import Constraints, CostWeights, HubOut, Submission
from hackhive.graph import build_graph, eligible_graph
from hackhive.grid import LabelGrid
from hackhive.io import load_plan
from hackhive.rings import first_ring
from hackhive.route import Occupancy, Stop, route_passes, route_ring, route_rings
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
    routing = route_passes(grid, plan, hubs, c)
    return SimpleNamespace(
        plan=plan, G=G, zoning=zoning, hubs=hubs, grid=grid, rings=routing.rings, routing=routing
    )


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
    """C33: by default no ring runs inside an excluded room or another hub's room. Door
    polygons don't count (1293's door_9 into its own bedroom covers a 4 cm strip of living_0),
    nor does the land lot, which covers every room."""
    case = routed[plan_id]
    doors = shapely.union_all([d.polygon for d in case.plan.doors])
    for ring, zone in zip(case.rings, case.zoning, strict=True):
        for room in case.plan.rooms:
            if room.id in zone or room.type == "land":
                continue
            outside_doors = room.polygon.difference(doors)
            inside = sum(_length_inside(s.polyline, outside_doors) for s in ring.segments)
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


def _detour_hub(rooms, node, zone, hub_room, ring_rooms):
    from hackhive.rings import Hub

    x, y = node[rooms[hub_room].id].xy
    nodes = [node[rooms[r].id].id for r in ring_rooms]
    assigned = [rooms[hub_room].id, *sorted(rooms[r].id for r in zone if r != hub_room)]
    return Hub("H1", rooms[hub_room].id, (x + 1.0, y), ["H1", *nodes, "H1"], assigned)


def _inside_bath(rooms, ring) -> float:
    return sum(_length_inside(s.polyline, rooms["bathroom_0"].polygon) for s in ring.segments)


def test_own_rooms_detour_vs_whatif():
    """C17, C33: the shortest bedroom → bedroom leg cuts through the bathroom; by default the
    ring goes round through the living room, and with the WHAT-IF it takes the bathroom."""
    plan, rooms, node = _detour_plan()
    zone = ["living_0", "bedroom_0", "bedroom_1"]
    hub = _detour_hub(rooms, node, zone, "living_0", ["bedroom_0", "bedroom_1", "living_0"])
    grid = LabelGrid.from_plan(plan)
    own = route_rings(grid, plan, [hub], C)[0]
    loose = route_rings(grid, plan, [hub], Constraints(route_through_other_rooms=True))[0]
    assert _inside_bath(rooms, own) <= TOL
    assert _inside_bath(rooms, loose) > 1.0
    assert loose.ring_length_m < own.ring_length_m


def test_own_rooms_grid_opens_only_doors_between_own_rooms():
    """A door or opening is open only when both its rooms are allowed: the bedroom → bathroom
    doors are blocked for the living + bedrooms zone, the bedroom → living ones are not."""
    plan, rooms, _ = _detour_plan()
    grid = LabelGrid.from_plan(plan)
    own = route.own_rooms_grid(grid, [rooms[r].id for r in ("living_0", "bedroom_0", "bedroom_1")])
    bath = grid.room_label(rooms["bathroom_0"].id)
    assert grid.door_rooms
    for label, pair in grid.door_rooms.items():
        cells = grid.labels == label
        assert cells.any()
        expected = route.BLOCKED if bath in pair else label
        assert (own.labels[cells] == expected).all(), (label, pair)
    assert (own.labels[grid.labels == bath] == route.BLOCKED).all()


def test_own_rooms_flag_is_whatif():
    """C29: allowing cable through other rooms is not the problem statement."""
    assert Constraints().route_through_other_rooms is False
    assert Constraints(route_through_other_rooms=True).is_problem_statement is False


def test_own_rooms_unreachable_raises():
    """Two bedrooms joined only through rooms outside the zone: no silent fallback, and the
    error names the leg and the hub."""
    plan, rooms, node = _detour_plan()
    beds = ["bedroom_0", "bedroom_1"]
    hub = _detour_hub(rooms, node, beds, "bedroom_0", beds)
    grid = LabelGrid.from_plan(plan)
    with pytest.raises(ValueError, match=r"N2→N3 unreachable within H1's rooms \(D7\)"):
        route_rings(grid, plan, [hub], C)
    whatif = Constraints(route_through_other_rooms=True)
    assert route_rings(grid, plan, [hub], whatif)[0].ring_length_m > 0


def test_own_rooms_via_excluded_zone_routes_through_its_bathroom():
    """#46 Q2: with zone_links_via_excluded a zone can be joined only through a bathroom, so
    that bathroom (and its doors) is part of the ring's rooms."""
    plan, rooms, node = _detour_plan()
    beds = ["bedroom_0", "bedroom_1"]
    hub = _detour_hub(rooms, node, beds, "bedroom_0", beds)
    grid = LabelGrid.from_plan(plan)
    ring = route_rings(grid, plan, [hub], Constraints(zone_links_via_excluded=True))[0]
    assert _inside_bath(rooms, ring) > 1.0


# main @ ef024cc, before D7: J per fixture (tests/test_route_rings.py demo, full precision).
# 3086 is after the µm hub tie in rings.first_ring: main gave 82.214805 on Python 3.13 but
# 88.614805 on 3.11 (CI), as its hub tie fell to the float sum order
J_MAIN = {
    "5088": 46.137406,
    "576": 94.167828,
    "1293": 96.851413,
    "2814": 90.270451,
    "3086": 87.014805,
    "5924": 84.392827,
    "13344": 108.1007,
}
# 13344's H1 ran 10 cm along opening_0's band (to H2's room) inside its own living_0; the strict
# door rule moves it off the band: +0.2 m L, +0.1 m O (#79 review, point 3)
D7_COST = {"13344": 0.3}


def _sequential(case) -> SimpleNamespace:
    """The case with its pass-0 rings: each ring routed alone, as main routed them (#80)."""
    return SimpleNamespace(**{**vars(case), "rings": case.routing.snapshots[0].rings})


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_own_rooms_j_against_main(routed, plan_id, monkeypatch):
    """C13, C17: the sequential pass's J matches main on every fixture except the recorded D7
    cost, and rule 5 passes; with the WHAT-IF on, J is main's exactly. Main routed one layout
    per ring, so #81's parallel-run variant is off here."""
    assert not [v for v in check_rules(routed[plan_id].plan, submission(routed[plan_id]), C)
                if v.rule == 5]  # fmt: skip
    monkeypatch.setattr(route, "_both_layouts", lambda stops, retrace_ok: False)
    case = _sequential(_route(plan_id))
    j = metrics(submission(case).hubs, C).J
    assert j == pytest.approx(J_MAIN[plan_id] + D7_COST.get(plan_id, 0.0), abs=TOL)
    assert not [v for v in check_rules(case.plan, submission(case), C) if v.rule == 5]
    loose = _sequential(_route(plan_id, Constraints(route_through_other_rooms=True)))
    assert metrics(submission(loose).hubs, C).J == pytest.approx(J_MAIN[plan_id], abs=TOL)


# --------------------------------------------------------------------------- occupancy (#80)


def _retraces(case) -> int:
    """Rule-4 retrace flags (strict, #46 Q5) on the case's rings."""
    found = check_rules(case.plan, submission(case), C)
    return sum(v.rule == 4 and v.severity == "strict" and "retrace" in v.message for v in found)


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_occupancy_j_not_above_the_sequential_baseline(routed, plan_id):
    """C17: J after the rip-up passes ≤ J of the sequential baseline (pass 0), as checker.metrics
    computes it from the submitted polylines; the winner is `best_pass`'s."""
    case = routed[plan_id]
    best = metrics(submission(case).hubs, C).J
    base = metrics(submission(_sequential(case)).hubs, C).J
    assert best <= base + 1e-9
    assert case.routing.best == route.best_pass(case.routing.snapshots)


def test_occupancy_576_overlap_not_above_the_baseline(routed):
    """C17, C40: 576's O after the passes ≤ the sequential baseline's."""
    case = routed["576"]
    assert metrics(submission(case).hubs, C).O <= metrics(submission(_sequential(case)).hubs, C).O


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_occupancy_snapshots_one_per_pass_and_no_error(routed, plan_id):
    """C13, C14: passes + 1 snapshots, each with its J and O; no snapshot gives an `error`."""
    case = routed[plan_id]
    snaps = case.routing.snapshots
    assert [s.pass_no for s in snaps] == list(range(route.RIP_UP_PASSES + 1))
    for snap in snaps:
        view = SimpleNamespace(**{**vars(case), "rings": snap.rings})
        m = metrics(submission(view).hubs, C)
        assert (snap.J, snap.O) == (pytest.approx(m.J, abs=1e-9), pytest.approx(m.O, abs=1e-9))
        flags = check_rules(case.plan, submission(view), C)
        assert not [v for v in flags if v.severity == "error"] and snap.errors == 0
        assert snap.strict == sum(v.severity == "strict" for v in flags)
    assert case.routing.rings == snaps[case.routing.best].rings


def test_occupancy_best_pass_ranks_validity_before_j():
    """#93 review: a pass with a lower J but one more strict flag (a rule-4 retrace) loses; an
    `error` outranks both; equal flags fall to J, then to the first pass."""
    snap = route.Snapshot
    base = snap(0, [], 90.270, 6.86, 0, 1)
    assert route.best_pass([base, snap(1, [], 90.170, 0.20, 0, 2)]) == 0
    assert route.best_pass([snap(0, [], 50.0, 0.0, 1, 0), snap(1, [], 90.0, 0.0, 0, 3)]) == 1
    assert route.best_pass([base, snap(1, [], 88.0, 0.2, 0, 1), snap(2, [], 88.0, 0.2, 0, 1)]) == 1


def test_occupancy_passes_is_a_setting(routed):
    """`passes` sets the number of rip-up passes: 0 is the sequential routing alone."""
    case = routed["5088"]
    one = route_passes(case.grid, case.plan, case.hubs, C, passes=0)
    assert [s.pass_no for s in one.snapshots] == [0] and one.best == 0
    assert route_rings(case.grid, case.plan, case.hubs, C) == case.rings


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_occupancy_retraces_do_not_increase(routed, plan_id):
    """C15: the rule-4 retrace flags don't increase on any fixture (T4's target is zero)."""
    case = routed[plan_id]
    assert _retraces(case) <= _retraces(_sequential(case))


def test_occupancy_penalty_moves_a_leg_to_the_parallel_line():
    """search: an occupied edge costs w_ov more per use, so a leg takes the parallel line when
    the overlap costs more than its two bends and two steps, and stays on it when it doesn't."""
    labels = np.zeros((3, 6), dtype=np.int64)
    occ = Occupancy()
    occ.commit([(1, 0), (1, 5)])
    assert occ.count[((1, 2), (1, 3))] == 1 and len(occ.count) == 5
    pen = occ.step_costs(1.0, labels.shape[1])
    dear = route.search(labels, {}, [(1, 0)], 0.0, penalty=pen)
    assert dear.cost((1, 5)) == 7.0 and route.bends(route.path_to(dear, (1, 5))) == 2
    cheap = route.search(labels, {}, [(1, 0)], 0.0, penalty=occ.step_costs(0.1, 6))
    assert cheap.cost((1, 5)) == pytest.approx(5.5) and route.path_to(cheap, (1, 5)) == [
        (1, 0),
        (1, 5),
    ]
    occ.commit([(1, 2), (1, 3)])
    assert occ.count[((1, 2), (1, 3))] == 2
    occ.clear([(1, 2), (1, 3)])
    occ.clear([(1, 0), (1, 5)])
    assert not occ.count


def test_occupancy_weight_comes_from_cost_weights():
    """C17: the overlap weight in steps is overlap / length from c.cost_weights, never a literal:
    a step costs length · cell in J, an overlapped step overlap · cell more."""
    assert route.overlap_weight_cells(C) == C.cost_weights.overlap / C.cost_weights.length
    dear = Constraints(cost_weights=CostWeights(1.0, 5.0, 3.0, 0.5))
    assert route.overlap_weight_cells(dear) == 3.0


def test_occupancy_no_weight_literals_in_route():
    """C17: no J weight (5, 0.5) appears in route.py as a number, except lines marked
    not-a-weight (a cell centre is half a cell)."""
    src = Path(route.__file__).read_text(encoding="utf-8")
    lines = src.splitlines()
    found = [
        f"{n.lineno}: {lines[n.lineno - 1].strip()}"
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Constant)
        and type(n.value) is float  # MAX_CORNER_COMBOS' 4**5 counts options, not cost
        and n.value in (5.0, 0.5)
        and "not-a-weight" not in lines[n.lineno - 1]
    ]
    assert found == []


# --------------------------------------------------------------------------- single-door rooms (#81)


def _synthetic(**geoms):
    from shapely.geometry import mapping

    raw = {"id": "SYN", "area": 100.0, "wall_depth": 2.0, "inner": mapping(box(0, 0, 100, 100))}
    return load_plan(raw | {k: mapping(g) for k, g in geoms.items()})


def _one_door_plan(x0: float = 45, x1: float = 55):
    """A living room (10.1 × 5.1 m) under a bedroom (4.1 × 4.1 m) whose only door spans x0..x1
    (units, 1 unit = 0.1 m). Odd sizes put both nodes on cell centres: N1 in the living room's
    middle, N2 2 m inside the bedroom."""
    from shapely.geometry import MultiPolygon

    walls = [box(28, 51, x0, 53), box(x1, 51, 73, 53)]
    walls += [box(28, 51, 30, 96), box(71, 51, 73, 96), box(28, 94, 73, 96)]
    return _synthetic(
        living=box(0, 0, 101, 51),
        bedroom=box(30, 53, 71, 94),
        wall=MultiPolygon(walls),
        door=box(x0, 51, x1, 53),
    )


def _ring(plan, hub_xy, ring, **kw):
    """Route hub → ring nodes → hub as route_passes' pass 0 does; the HubOut and its metrics."""
    retrace_ok = route.single_door_nodes(plan, C)  # its build_graph fills openings first
    free = _free_space(plan, C).free
    nodes = {n.id: n for n in plan.nodes}
    room = next(r.id for r in plan.rooms if r.polygon.contains(shapely.Point(hub_xy)))
    stops = [Stop("H1", hub_xy, room), *(Stop(n, nodes[n].xy, nodes[n].room_id) for n in ring)]
    segs = route_ring(LabelGrid.from_plan(plan), stops, C, retrace_ok=retrace_ok, free=free, **kw)
    hub = HubOut.model_validate(
        {
            "hub_id": "H1",
            "room_id": room,
            "location": {"x": hub_xy[0], "y": hub_xy[1]},
            "assigned_rooms": [room, *sorted({nodes[n].room_id for n in ring} - {room})],
            "assigned_nodes": sorted(ring),
            "closed_sequence": ["H1", *ring, "H1"],
            "route_segments": [s.model_dump(by_alias=True) for s in segs],
            "ring_length_m": sum(s.length_m for s in segs),
        }
    )
    return hub, metrics([hub], C)


def _today(monkeypatch):
    """Route one layout per ring, as main did before #81."""
    monkeypatch.setattr(route, "_both_layouts", lambda stops, retrace_ok: False)


def _one_node(d: float, monkeypatch=None):
    plan = _synthetic(living=box(0, 0, 81, 81))  # node on a cell centre (4.05, 4.05)
    (n1,) = plan.nodes
    if monkeypatch is not None:
        _today(monkeypatch)
    return plan, *_ring(plan, (n1.xy[0] - d, n1.xy[1]), [n1.id])


def test_single_door_one_node_ring_long_takes_parallel(monkeypatch):
    """C15, C17, C40: d = 3 m: two parallel runs (O = 0) beat the out-and-back (3d + 1 = 10),
    at about 2d + 2.2 (§5's one-node ring row)."""
    d = 3.0
    _, _, m = _one_node(d)
    _, _, back = _one_node(d, monkeypatch)
    assert m.O <= TOL and back.O > 1.0
    assert m.J < back.J and m.J < 3 * d + 1
    assert m.J == pytest.approx(2 * d + 2.2, abs=0.3)


def test_single_door_one_node_ring_short_takes_retrace():
    """C15, C17, C40: d = 0.8 m: the out-and-back wins (3d + 1 = 3.4 < 2d + 2.2 = 3.8), and the
    checker mirror takes it as legal (no error, no rule-4 strict)."""
    d = 0.8
    plan, hub, m = _one_node(d)
    assert m.O > 0.5
    assert m.J == pytest.approx(3 * d + 1, abs=0.3)
    sub = Submission(
        plan_id=plan.plan_id,
        units="m",
        hubs=[hub],
        reported_metrics={"hub_count": 1, "total_length_m": m.L},
    )
    found = check_rules(plan, sub, C)
    assert not [v for v in found if v.severity == "error"], found
    assert not [v for v in found if v.rule == 4 and v.severity == "strict"], found


def _door_pieces(hub, door):
    return [
        shapely.LineString(s.polyline).intersection(door)
        for s in hub.route_segments
        if shapely.LineString(s.polyline).intersects(door)
    ]


def test_single_door_node_legs_run_parallel_through_its_door(monkeypatch):
    """C15, C17: hub → N2 (2 m inside the single-door bedroom) → N1: the legs in and out of the
    bedroom go through its door on two lines at least a cell apart, with no overlap, and J is
    below the retraced layout's."""
    plan = _one_door_plan()
    n1, n2 = plan.nodes
    hub_xy = (n1.xy[0] - 3.0, n1.xy[1])
    door = next(iter(plan.doors)).polygon
    hub, m = _ring(plan, hub_xy, [n2.id, n1.id])
    a, b = _door_pieces(hub, door)
    assert a.distance(b) >= C.grid_m - TOL
    assert m.O <= TOL
    _today(monkeypatch)
    _, back = _ring(plan, hub_xy, [n2.id, n1.id])
    assert back.O > 1.0 and m.J < back.J


def test_single_door_narrow_door_falls_back_to_retrace():
    """A door that fits one lane (0.15 m: one raster column): routing succeeds, the legs share
    its line (the retrace), nothing raises."""
    plan = _one_door_plan(50, 51.5)
    n1, n2 = plan.nodes
    hub, m = _ring(plan, (n1.xy[0] - 3.0, n1.xy[1]), [n2.id, n1.id])
    assert m.O > 1.0
    a, b = _door_pieces(hub, next(iter(plan.doors)).polygon)
    assert a.distance(b) <= TOL


# a node in a two-door room whose two neighbours lie the same way: the legs in and out share
# the node cell's first edge, and leaving another way costs two bends (1.0 J) against 0.1 J of
# overlap, so both layouts keep it. Only a validity-first search or pass ranking per ring would
# drop it (search / Occupancy / best_pass, outside #81's change).
TWO_DOOR_RETRACE = {
    "3086": "H1→N1 and N1→N4 share 0.10 m at N1 (living_0, two ways out)",
    "13344": "N2→N3 and N3→N4 share 0.16 m at N3 (bedroom_1, two ways out)",
}


@pytest.mark.parametrize(
    "plan_id",
    [
        pytest.param(p, marks=pytest.mark.xfail(strict=True, reason=TWO_DOOR_RETRACE[p]))
        if p in TWO_DOOR_RETRACE
        else p
        for p in PLAN_IDS
    ],
)
def test_single_door_retraces_only_where_legal(routed, plan_id):
    """C15: after the passes, the only same-line retraces left are legal ones (into a
    single-door room or in a one-node ring): no rule-4 strict retrace flag."""
    assert _retraces(routed[plan_id]) == 0


# main @ fa9853c (#80 merged): J of route_passes' best pass per fixture
J_MAIN_BEST = {
    "5088": 38.907734,
    "576": 61.899546,
    "1293": 73.579815,
    "2814": 90.270451,
    "3086": 84.124317,
    "5924": 64.467449,
    "13344": 98.332113,
}


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_single_door_j_not_above_main(routed, plan_id):
    """C17: the parallel-run variant only adds a choice, so J never goes up."""
    assert metrics(submission(routed[plan_id]).hubs, C).J <= J_MAIN_BEST[plan_id] + TOL


# --------------------------------------------------------------------------- crossings (#82)

J80_576 = (3, 61.8995)  # 576's (X, J) after #80's rip-up passes, before crossings were priced


def _crossing_search(blocker, src=(3, 0), dst=(3, 6), shape=(7, 7)):
    """One leg src → dst on an open grid, after `blocker` is committed; X in J weight (5 m)."""
    occ = Occupancy()
    occ.commit(blocker)
    w_x = route.crossing_weight_cells(C, C.grid_m)
    pen = occ.step_costs(0.0, shape[1], w_x, exempt=[dst])
    found = route.search(np.zeros(shape, dtype=np.int64), {}, [src], 0.0, [dst], pen)
    path = route.path_to(found, dst)
    xs = crossing_points([blocker, path], [blocker[0], blocker[-1], src, dst], TOL)
    return found.cost(dst), path, len(xs)


def test_crossing_detour_under_5_m_avoids_it():
    """C17, C40: a detour cheaper than w_X (6 extra steps < 50 at 10 cm) is taken: X = 0."""
    cost, path, x = _crossing_search([(1, 3), (5, 3)])
    assert x == 0 and cost == 12.0 and route.bends(path) == 2


def test_crossing_without_a_cheap_detour_is_kept():
    """C17: no way around (the cable spans the grid), so the leg crosses once and pays w_X."""
    cost, path, x = _crossing_search([(0, 3), (6, 3)])
    assert x == 1 and path == [(3, 0), (3, 6)]
    assert cost == pytest.approx(6 + route.crossing_weight_cells(C, C.grid_m))


def test_crossing_t_touch_costs_w_x():
    """#43's strict X: entering the cell where another leg ends (a T-touch) costs w_X too."""
    cost, path, x = _crossing_search([(0, 3), (3, 3)])
    assert x == 0 and (3, 3) not in route.expand(path) and cost == 8.0


def test_crossing_own_endpoint_is_free():
    """The leg's own endpoint is exempt: ending on a cell another cable crosses costs nothing."""
    cost, path, _ = _crossing_search([(0, 6), (6, 6)])
    assert cost == 6.0 and path == [(3, 0), (3, 6)]


def test_crossing_parallel_cable_is_no_crossing():
    """Running alongside on the next row is not a crossing: no w_X."""
    cost, _, x = _crossing_search([(2, 0), (2, 6)])
    assert cost == 6.0 and x == 0


def test_crossing_clear_undoes_commit():
    occ = Occupancy()
    occ.commit([(0, 3), (6, 3)])
    occ.clear([(0, 3), (6, 3)])
    assert not occ.count and not occ.step_costs(1.0, 7, 50.0)


def test_crossing_weight_comes_from_cost_weights():
    """C17: w_X in steps is cross / (length · cell) from c.cost_weights, never a literal."""
    w = C.cost_weights
    assert route.crossing_weight_cells(C, 0.1) == pytest.approx(w.cross / (w.length * 0.1))
    dear = Constraints(cost_weights=CostWeights(1.0, 9.0, 1.0, 0.5))
    assert route.crossing_weight_cells(dear, 0.1) == pytest.approx(90.0)


def test_crossing_576_not_above_80(routed):
    """C17, C33: on 576, X ≤ #80's result and J ≤ it."""
    m = metrics(submission(routed["576"]).hubs, C)
    assert m.X <= J80_576[0] and m.J <= J80_576[1] + 1e-4


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
