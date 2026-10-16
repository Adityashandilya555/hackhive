"""Acceptance tests for #9: the strict room graph with open-passage regions (C8, C13-C15, C34;
slide 3 rule 3: rooms connect only through doors and open passages, never windows, wall
adjacency or the entrance)."""

import json
from pathlib import Path

import networkx as nx
import numpy as np
import pytest
from shapely.geometry import MultiPolygon, box, mapping

from hackhive.contracts import Constraints
from hackhive.graph import OUTDOOR, build_graph, eligible_graph
from hackhive.graph import main as graph_main
from hackhive.grid import LabelGrid
from hackhive.io import load_plan, room_by_label
from hackhive.route import search

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
PLAN_IDS = list(EXPECTED)


def fixture(plan_id):
    plan = load_plan(FIXTURES / "plans" / f"plan-{plan_id}.json")
    return plan, build_graph(plan)


def synthetic(**geoms) -> dict:
    """A plan in drawing units with 1 unit = 0.1 m and 0.2 m walls (inner 100 × 100 units)."""
    raw = {"id": "SYN", "area": 100.0, "wall_depth": 2.0, "inner": mapping(box(0, 0, 100, 100))}
    raw.update({k: mapping(g) for k, g in geoms.items()})
    return raw


def kinds(G, plan, a, b):
    u, v = room_by_label(plan, a).id, room_by_label(plan, b).id
    return G.edges[u, v]["kind"] if G.has_edge(u, v) else None


def single_door_eligible(plan, G) -> int:
    """Eligible rooms with exactly one edge in the full graph (degree 1, door and opening edges
    to any room, excluded rooms included): the reading that matches expected.json's
    single_door_elig on all seven fixtures."""
    return sum(G.degree(r.id) == 1 for r in plan.rooms if r.eligible)


# --------------------------------------------------------------------------- acceptance criteria


def test_opening_edge_3086():
    """C13: an open passage becomes an `opening` edge with its region."""
    plan, G = fixture(3086)
    openings = [d for _, _, d in G.edges(data=True) if d["kind"] == "opening"]
    assert openings and all(not d["region"].is_empty for d in openings)
    assert plan.openings
    assert {o.id for o in plan.openings} == {i for d in openings for i in d["opening_ids"]}


def test_interior_window_doesnt_connect_2814():
    """C14, A10: 2814's kitchen meets the plan only through an interior window: no edge."""
    plan, G = fixture(2814)
    assert G.degree(room_by_label(plan, "kitchen_0").id) == 0


def test_576_has_two_eligible_components():
    """C34."""
    _, G = fixture(576)
    assert nx.number_connected_components(eligible_graph(G)) == 2


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_a_door_makes_at_most_one_edge(plan_id):
    """C13; every door that links two rooms records them on `Door.rooms`."""
    plan, G = fixture(plan_id)
    door_ids = [i for _, _, d in G.edges(data=True) for i in d.get("door_ids", [])]
    assert len(door_ids) == len(set(door_ids))
    for u, v, d in G.edges(data=True):
        for door in (x for x in plan.doors if x.id in d.get("door_ids", [])):
            assert set(door.rooms) == {u, v}


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_single_door_eligible_rooms(plan_id):
    """C15: within ±1 of the profile (measured: an exact match on all seven)."""
    plan, G = fixture(plan_id)
    got, want = single_door_eligible(plan, G), EXPECTED[plan_id]["single_door_elig"]
    print(f"{plan_id}: single-door eligible {got} (expected {want})")
    assert abs(got - want) <= 1


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_front_door_never_links_on_fixtures(plan_id):
    """C8, C34, slide 3 rule 3: no edge comes from the entrance."""
    plan, G = fixture(plan_id)
    for _, _, d in G.edges(data=True):
        if d["kind"] == "opening":
            assert all(d["region"].intersection(f).area < 1e-9 for f in plan.front_doors)


def test_rooms_meeting_only_at_the_entrance_are_not_linked():
    """Slide 3 rule 3: a gap filled by the front door is neither a door nor an open passage."""
    walls = MultiPolygon([box(40, -2, 42, 10), box(40, 20, 42, 42)])
    raw = synthetic(bedroom=box(0, 0, 40, 40), kitchen=box(42, 0, 82, 40), wall=walls)
    raw["front_door"] = mapping(box(40, 10, 42, 20))
    plan = load_plan(raw)
    G = build_graph(plan)
    assert G.number_of_edges() == 0 and plan.openings == []


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_default_settings_are_strict(plan_id):
    """C34: no via_excluded edge by default, and the eligible components match the profile."""
    _, G = fixture(plan_id)
    assert all(d["kind"] in ("door", "opening") for _, _, d in G.edges(data=True))
    assert nx.number_connected_components(eligible_graph(G)) == EXPECTED[plan_id]["comp_direct"]


def _bed_bath_bed() -> dict:
    """Two bedrooms, each with a door to the bathroom between them."""
    walls = [box(40, -2, 42, 10), box(40, 20, 42, 42), box(62, -2, 64, 10), box(62, 20, 64, 42)]
    return synthetic(
        bedroom=MultiPolygon([box(0, 0, 40, 40), box(64, 0, 100, 40)]),
        bathroom=box(42, 0, 62, 40),
        wall=MultiPolygon(walls),
        door=MultiPolygon([box(40, 10, 42, 20), box(62, 10, 64, 20)]),
    )


def test_zone_links_via_excluded():
    """C34, #46 Q2: off by default; when on, two bedrooms door-linked to one bathroom are linked."""
    plan = load_plan(_bed_bath_bed())
    b0, b1 = room_by_label(plan, "bedroom_0").id, room_by_label(plan, "bedroom_1").id
    bath = room_by_label(plan, "bathroom_0").id
    assert not build_graph(plan).has_edge(b0, b1)

    c = Constraints(zone_links_via_excluded=True)
    assert c.is_problem_statement is False
    G = build_graph(plan, c)
    assert G.edges[b0, b1]["kind"] == "via_excluded" and G.edges[b0, b1]["via"] == [bath]
    assert G.edges[b0, bath]["kind"] == G.edges[bath, b1]["kind"] == "door"
    assert nx.number_connected_components(eligible_graph(G)) == 1


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_via_excluded_never_replaces_a_door_or_opening(plan_id):
    """The setting only adds edges: every default edge keeps its kind."""
    plan, G = fixture(plan_id)
    loose = build_graph(plan, Constraints(zone_links_via_excluded=True))
    assert all(loose.edges[u, v]["kind"] == d["kind"] for u, v, d in G.edges(data=True))


def test_via_excluded_doesnt_pass_through_a_balcony():
    """decision_conventions §3 names bathrooms and stairs only."""
    raw = _bed_bath_bed()
    raw["balcony"] = raw.pop("bathroom")
    plan = load_plan(raw)
    G = build_graph(plan, Constraints(zone_links_via_excluded=True))
    b0, b1 = room_by_label(plan, "bedroom_0").id, room_by_label(plan, "bedroom_1").id
    assert not G.has_edge(b0, b1)


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_linked_rooms_reach_each_other_on_the_raster(plan_id):
    """#10's raster painted with these openings must agree with the graph: rooms it links can
    reach each other (an opening that swallowed a room or a door's side cut 576's storage_0
    and 1293's bedroom_2 off, #62 review)."""
    plan, G = fixture(plan_id)
    grid = LabelGrid.from_plan(plan, plan.openings)
    for group in nx.connected_components(eligible_graph(G)):
        first, *rest = sorted(group)
        result = search(
            grid.labels, grid.door_rooms, np.argwhere(grid.labels == grid.room_label(first)), 0.0
        )
        for room in rest:
            cells = grid.labels == grid.room_label(room)
            assert np.isfinite(result.dist[cells]).any(), f"{room} unreachable from {first}"


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_openings_are_bands_not_rooms(plan_id):
    """An opening is the band along a shared edge with no wall: it never covers more than a
    quarter of either room (it covered 93% of 576's storage_0 before the #62 review fix)."""
    plan, _ = fixture(plan_id)
    rooms = {r.id: r for r in plan.rooms}
    for o in plan.openings:
        for rid in o.rooms:
            share = o.polygon.intersection(rooms[rid].polygon).area / rooms[rid].area_m2
            assert share <= 0.25, f"{o.id} covers {share:.0%} of {rid}"


def _open_passage(wall_units: int, gap_units: int) -> dict:
    """Living and kitchen meeting along the wall's centre line over an open gap (like 13344's),
    so the wall stubs flank both ends of the band; 1 unit = 0.1 m."""
    w, y0, y1 = wall_units, 20 - gap_units / 2, 20 + gap_units / 2
    c = 40 + w / 2
    raw = synthetic(
        living=box(0, 0, 40, 40).union(box(40, y0, c, y1)),
        kitchen=box(40 + w, 0, 80 + w, 40).union(box(c, y0, 40 + w, y1)),
        wall=MultiPolygon([box(40, -w, 40 + w, y0), box(40, y1, 40 + w, 40 + w)]),
    )
    raw["wall_depth"] = float(w)
    return raw


@pytest.mark.parametrize(("gap_units", "linked"), [(8, True), (5, False)])
def test_open_passage_needs_two_wall_depths_of_open_edge(gap_units, linked):
    """Spec: a passage needs ≥ 2·wd of open edge. Trimming the band away from the walls must
    not shorten it, or an 0.8 m passage between 0.3 m walls (13344's shape) would be lost."""
    plan = load_plan(_open_passage(3, gap_units))
    G = build_graph(plan)
    assert (kinds(G, plan, "living_0", "kitchen_0") == "opening") is linked
    if linked:
        (o,) = plan.openings
        x0, y0, x1, y1 = o.polygon.bounds
        assert max(x1 - x0, y1 - y0) == pytest.approx(gap_units / 10, abs=0.01)


# --------------------------------------------------------------------------- extras


def test_1293_corner_doors_pick_one_room_per_side():
    """door_9 sits at a corner where living_0's sliver runs inside the wall band; top-2 by
    contact would link living–kitchen and cut bedroom_2 off. One room per side doesn't."""
    plan, G = fixture(1293)
    assert kinds(G, plan, "bedroom_2", "kitchen_0") == "door"
    assert kinds(G, plan, "kitchen_0", "living_0") == "opening"
    assert nx.number_connected_components(eligible_graph(G)) == 1


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_outdoor_rooms_are_nodes_without_edges(plan_id):
    """garden/pool/parking/land are plot layers (land covers every room): never connectors."""
    plan, G = fixture(plan_id)
    outdoor = [r.id for r in plan.rooms if r.type in OUTDOOR]
    assert outdoor and all(G.has_node(r) and G.degree(r) == 0 for r in outdoor)
    assert set(G.nodes) == {r.id for r in plan.rooms}


def test_eligible_graph_is_a_view_of_eligible_rooms():
    plan, G = fixture(576)
    E = eligible_graph(G)
    assert set(E.nodes) == {r.id for r in plan.rooms if r.eligible}
    assert nx.is_frozen(E)


def test_build_graph_twice_is_stable():
    plan, _ = fixture(3086)
    first = (len(plan.openings), [d.rooms for d in plan.doors], list(plan.warnings))
    build_graph(plan)
    assert (len(plan.openings), [d.rooms for d in plan.doors], list(plan.warnings)) == first


def test_near_square_door_still_links_its_two_rooms():
    """A 0.2 × 0.2 m door has no clear long side: both side pairs are tried, with a warning."""
    walls = MultiPolygon([box(40, -2, 42, 19), box(40, 21, 42, 42)])
    raw = synthetic(
        bedroom=box(0, 0, 40, 40), kitchen=box(42, 0, 82, 40), wall=walls, door=box(40, 19, 42, 21)
    )
    plan = load_plan(raw)
    G = build_graph(plan)
    assert kinds(G, plan, "bedroom_0", "kitchen_0") == "door"
    assert any("door_0" in w and "square" in w for w in plan.warnings)


def test_door_with_one_room_makes_no_edge_and_warns():
    walls = MultiPolygon([box(40, -2, 42, 10), box(40, 20, 42, 42)])
    raw = synthetic(bedroom=box(0, 0, 40, 40), wall=walls, door=box(40, 10, 42, 20))
    plan = load_plan(raw)
    G = build_graph(plan)
    assert G.number_of_edges() == 0 and plan.doors[0].rooms is None
    assert any("door_0" in w for w in plan.warnings)


def test_cli_576(capsys):
    """See it: the edges with their kinds, then "2 components"."""
    assert graph_main([str(FIXTURES / "plans" / "plan-576.json")]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "2 components"
    assert any(" · door door_" in line for line in out) and any(
        " · opening" in line for line in out
    )


# --------------------------------------------------------------------------- single-door rooms


def _typed_graph(edges, types):
    G = nx.Graph()
    G.add_nodes_from(
        (r, {"type": t, "eligible": t in ("living", "bedroom", "kitchen", "storage")})
        for r, t in types.items()
    )
    G.add_edges_from(edges)
    return G


def test_a_dead_end_bathroom_door_is_no_second_way_out():
    """2026-10-16 (Rohit): a room whose only other door leads into a bathroom that opens nowhere
    else counts as single-door for retracing."""
    from hackhive.graph import single_door_rooms

    types = {"L": "living", "B": "bedroom", "W": "bathroom", "K": "kitchen"}
    G = _typed_graph([("L", "B"), ("B", "W"), ("L", "K")], types)
    assert single_door_rooms(G) >= {"B", "K", "W"}
    assert "L" not in single_door_rooms(G)  # bedroom and kitchen: two real ways out


def test_a_bathroom_that_opens_into_another_room_still_counts():
    """…unless the bathroom also connects to another room: then it's a real second way."""
    from hackhive.graph import single_door_rooms

    types = {"L": "living", "B": "bedroom", "W": "bathroom", "K": "kitchen"}
    G = _typed_graph([("L", "B"), ("B", "W"), ("W", "K")], types)
    assert "B" not in single_door_rooms(G)


def test_only_bathrooms_are_ignored_not_balconies():
    """The decision covers bathrooms only: a dead-end balcony door still counts."""
    from hackhive.graph import single_door_rooms

    types = {"L": "living", "B": "bedroom", "Y": "balcony"}
    G = _typed_graph([("L", "B"), ("B", "Y")], types)
    assert "B" not in single_door_rooms(G)


def test_a_room_reached_only_through_a_bathroom_keeps_its_one_door():
    """A room whose only door is into a dead-end bathroom is still single-door, as before."""
    from hackhive.graph import single_door_rooms

    G = _typed_graph([("B", "W")], {"B": "bedroom", "W": "bathroom"})
    assert single_door_rooms(G) == {"B", "W"}


def test_single_door_rooms_on_the_fixtures():
    from hackhive.graph import single_door_rooms

    plan = load_plan(FIXTURES / "plans" / "plan-5088.json")
    one_door = single_door_rooms(build_graph(plan))
    assert room_by_label(plan, "bedroom_0").id in one_door  # living + a dead-end bathroom
    assert room_by_label(plan, "kitchen_0").id in one_door
    assert room_by_label(plan, "living_0").id not in one_door  # bedroom and kitchen doors
    plan = load_plan(FIXTURES / "plans" / "plan-576.json")  # a bedroom: living + a dead-end balcony
    g = build_graph(plan)
    balcony_bed = [
        r.id
        for r in plan.rooms
        if r.type == "bedroom"
        and sorted(g.nodes[n]["type"] for n in g.neighbors(r.id)) == ["balcony", "living"]
    ]
    assert balcony_bed and not set(balcony_bed) & single_door_rooms(g)
