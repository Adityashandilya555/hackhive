"""Acceptance tests for #14: hub candidates, first-cut hub placement and ring order.

Fixtures load through `io.load_plan` (one node per eligible room, #6) and #9's `build_graph`.
"""

import itertools
from pathlib import Path

import networkx as nx
import pytest
from shapely.geometry import Point, box

from hackhive.contracts import Constraints, Node, Plan, Room
from hackhive.graph import build_graph, eligible_graph
from hackhive.io import load_plan
from hackhive.rings import first_ring, hub_candidates, manhattan, order_ring
from hackhive.zoning import eligible_area_and_nodes, enumerate_zonings, min_hubs, wall_geometry

FIXTURES = Path(__file__).resolve().parent / "fixtures"
C = Constraints()


def _fixture(plan_id: str):
    plan = load_plan(FIXTURES / "plans" / f"plan-{plan_id}.json")
    return plan, eligible_graph(build_graph(plan))


def _min_zoning(plan: Plan, g: nx.Graph):
    area, nodes = eligible_area_and_nodes(plan)
    zonings = enumerate_zonings(g, area, C, nodes=nodes)
    k = min_hubs(zonings)
    return next(z for z in zonings if len(z) == k)


# --------------------------------------------------------------------------- order_ring


def test_order_ring_is_the_shortest_cycle():
    pts = {"H1": (0, 0), "N1": (0, 4), "N2": (4, 4), "N3": (4, 0)}

    def dist(a, b):
        return manhattan(pts[a], pts[b])

    ring = order_ring("H1", ["N2", "N1", "N3"], dist)
    assert ring[0] == ring[-1] == "H1"
    assert sorted(ring[1:-1]) == ["N1", "N2", "N3"]
    assert sum(dist(a, b) for a, b in itertools.pairwise(ring)) == 16  # the square


def test_order_ring_breaks_ties_deterministically():
    def dist(a, b):
        return 0.0 if a == b else 1.0

    r1 = order_ring("H1", ["N3", "N1", "N2"], dist)
    r2 = order_ring("H1", ["N2", "N3", "N1"], dist)
    assert r1 == r2 == ["H1", "N1", "N2", "N3", "H1"]


def test_order_ring_one_and_zero_nodes():
    assert order_ring("H1", ["N7"], lambda a, b: 1.0) == ["H1", "N7", "H1"]
    with pytest.raises(ValueError):
        order_ring("H1", [], lambda a, b: 1.0)


def test_order_ring_handles_asymmetric_costs():
    """Routed costs with node bends (#20) can differ by direction: a cycle != its reverse."""
    d = {("H", "A"): 9, ("A", "B"): 9, ("B", "H"): 9, ("H", "B"): 1, ("B", "A"): 1, ("A", "H"): 1}
    assert order_ring("H", ["A", "B"], lambda a, b: d[a, b]) == ["H", "B", "A", "H"]


def test_order_ring_refuses_more_than_brute_force_handles():
    with pytest.raises(ValueError, match="#28"):
        order_ring("H1", [f"N{i}" for i in range(9)], lambda a, b: 1.0)


# --------------------------------------------------------------------------- fixture 5924


def test_each_5924_zone_gets_a_closed_ring_over_its_nodes():
    plan, g = _fixture("5924")
    for i, zone in enumerate(_min_zoning(plan, g), start=1):
        hub = first_ring(zone, plan, g, C, hub_id=f"H{i}")
        zone_nodes = sorted(n.id for n in plan.nodes if n.room_id in zone)
        assert hub.ring[0] == hub.ring[-1] == hub.hub_id
        assert sorted(hub.ring[1:-1]) == zone_nodes  # each node exactly once
        assert hub.assigned_rooms == [hub.room_id, *sorted(zone - {hub.room_id})]  # #79


def test_each_5924_hub_obeys_rule_6_and_keeps_off_nodes():
    plan, g = _fixture("5924")
    walls = wall_geometry(plan)
    rooms = {r.id: r for r in plan.rooms}
    for zone in _min_zoning(plan, g):
        hub = first_ring(zone, plan, g, C)
        assert hub.room_id in zone and rooms[hub.room_id].eligible
        p = Point(hub.location)
        assert rooms[hub.room_id].polygon.contains(p)
        assert walls.distance(p) >= C.hub_wall_clearance_m  # checker rule 6
        assert min(Point(n.xy).distance(p) for n in plan.nodes) >= 0.10
        assert not hub.warnings


def test_hub_sits_on_a_grid_cell_centre():
    plan, g = _fixture("5924")
    hub = first_ring(next(iter(_min_zoning(plan, g))), plan, g, C)
    for v in hub.location:
        k = v / C.grid_m - 0.5
        assert abs(k - round(k)) < 1e-9


def test_first_ring_is_deterministic():
    plan, g = _fixture("13344")
    zoning = _min_zoning(plan, g)
    a = [first_ring(z, plan, g, C) for z in zoning]
    b = [first_ring(z, plan, g, C) for z in zoning]
    assert a == b


PLAN_IDS = ("576", "1293", "2814", "3086", "5088", "5924", "13344")


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_every_candidate_and_hub_obeys_rule_6_on_every_fixture(plan_id):
    """Rooms can run under walls (1293 living_0), so the outline alone isn't enough."""
    plan, g = _fixture(plan_id)
    walls = wall_geometry(plan)
    for i, zone in enumerate(_min_zoning(plan, g), start=1):
        for cand in hub_candidates(zone, plan, C):
            assert walls.distance(Point(cand.xy)) >= C.hub_wall_clearance_m, (plan_id, cand)
        hub = first_ring(zone, plan, g, C, hub_id=f"H{i}")
        assert walls.distance(Point(hub.location)) >= C.hub_wall_clearance_m
        assert sorted(hub.ring[1:-1]) == sorted(n.id for n in plan.nodes if n.room_id in zone)


# --------------------------------------------------------------------------- synthetic plans


def _room(rid, poly, kind="bedroom"):
    return Room(id=rid, label=rid, type=kind, eligible=True, polygon=poly, area_m2=poly.area)


def _walled_box(x0, y0, x1, y1, t=0.2):
    return [box(x0 - t, y0 - t, x1 + t, y0), box(x0 - t, y1, x1 + t, y1 + t),
            box(x0 - t, y0, x0, y1), box(x1, y0, x1 + t, y1)]  # fmt: skip


def test_one_node_zone_shares_the_room_and_keeps_off_the_node():
    room = box(0, 0, 4, 3)
    plan = Plan(
        plan_id="PLAN_T", m_per_unit=1.0, wall_depth_m=0.2, inner=box(-0.2, -0.2, 4.2, 3.2),
        rooms=[_room("R01", room)], walls=_walled_box(0, 0, 4, 3),
        nodes=[Node(id="N1", room_id="R01", xy=(2.05, 1.55))],
    )  # fmt: skip
    g = nx.Graph()
    g.add_node("R01")
    hub = first_ring(frozenset({"R01"}), plan, g, C)
    assert hub.ring == ["H1", "N1", "H1"]
    assert hub.room_id == "R01"
    assert Point(hub.location).distance(Point(2.05, 1.55)) >= 0.10


def test_hub_goes_in_the_best_connected_room():
    hall, a, b = box(0, 0, 3, 3), box(3, 0, 6, 3), box(-3, 0, 0, 3)
    plan = Plan(
        plan_id="PLAN_T", m_per_unit=1.0, wall_depth_m=0.2, inner=box(-3.2, -0.2, 6.2, 3.2),
        rooms=[_room("R01", b), _room("R02", hall, "living"), _room("R03", a)],
        walls=_walled_box(-3, 0, 6, 3),
        nodes=[Node(id=f"N{i}", room_id=r, xy=xy) for i, (r, xy) in
               enumerate([("R01", (-1.5, 1.5)), ("R02", (1.5, 1.5)), ("R03", (4.5, 1.5))], 1)],
    )  # fmt: skip
    g = nx.Graph([("R01", "R02"), ("R02", "R03")])
    hub = first_ring(frozenset(g.nodes), plan, g, C)
    assert hub.room_id == "R02"  # two links inside the zone


def test_narrow_room_open_to_a_passage_gets_a_wall_clearance_only_candidate():
    """1 m wide: nothing 0.51 m from its outline, but its open side is far from any wall."""
    narrow, hall = box(0, 0, 1, 3), box(1, 0, 4, 3)
    plan = Plan(
        plan_id="PLAN_T", m_per_unit=1.0, wall_depth_m=0.2, inner=box(-0.2, -0.2, 4.2, 3.2),
        rooms=[_room("R01", narrow), _room("R02", hall)], walls=_walled_box(0, 0, 4, 3),
        nodes=[Node(id="N1", room_id="R01", xy=(0.5, 1.5))],
    )  # fmt: skip
    cands = hub_candidates(frozenset({"R01"}), plan, C)
    assert cands, "rule 6 allows a hub here"
    assert all(cand.wall_clearance_only for cand in cands)
    assert all(cand.room_id == "R01" for cand in cands)
    single = nx.Graph()
    single.add_node("R01")
    hub = first_ring(frozenset({"R01"}), plan, single, C)
    assert "wall_clearance_only" in hub.warnings
