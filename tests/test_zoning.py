"""Acceptance tests for #13: enumerate every feasible zoning and the minimum hub count.

The eligible graph is #9's `build_graph` on `io.load_plan`: rooms link only through doors and
open passages (slide 3 rule 3).
"""

import importlib.util
import json
from pathlib import Path

import networkx as nx
import pytest

from hackhive.contracts import Constraints, Plan, Room
from hackhive.graph import build_graph, eligible_graph
from hackhive.io import load_plan, room_by_label
from hackhive.zoning import (
    MAX_ZONINGS,
    Infeasible,
    TooLarge,
    eligible_area_and_nodes,
    enumerate_zonings,
    feasible_zones,
    hub_capable_rooms,
    min_hubs,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())


def _fixture(plan_id: str) -> tuple[Plan, nx.Graph]:
    plan = load_plan(FIXTURES / "plans" / f"plan-{plan_id}.json")
    return plan, eligible_graph(build_graph(plan))


def _fixture_graph(plan_id: str) -> tuple[nx.Graph, dict[str, float]]:
    """Eligible rooms with their m² and the door/opening links between them."""
    plan, g = _fixture(plan_id)
    area, _ = eligible_area_and_nodes(plan)
    return g, area


PLAN_IDS = sorted(EXPECTED, key=int)


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_min_hubs_and_count_match_expected(plan_id):
    g, area = _fixture_graph(plan_id)
    assert sum(area.values()) == pytest.approx(EXPECTED[plan_id]["elig_m2"], abs=0.1)
    zonings = enumerate_zonings(g, area, Constraints())
    assert min_hubs(zonings) == EXPECTED[plan_id]["min_hubs5"]
    assert len(zonings) == EXPECTED[plan_id]["n_all5"]


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_cap_20_what_if_matches_expected(plan_id):
    g, area = _fixture_graph(plan_id)
    zonings = enumerate_zonings(g, area, Constraints(max_nodes=20))
    assert min_hubs(zonings) == EXPECTED[plan_id]["min_hubs20"]
    assert len(zonings) == EXPECTED[plan_id]["n_all20"]


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_every_zoning_is_a_valid_exact_cover(plan_id):
    g, area = _fixture_graph(plan_id)
    c = Constraints()
    nodes = {r: 1 for r in g.nodes}
    for zoning in enumerate_zonings(g, area, c, nodes=nodes):
        covered = [room for zone in zoning for room in zone]
        assert sorted(covered) == sorted(g.nodes)  # each room exactly once
        for zone in zoning:
            assert nx.is_connected(g.subgraph(zone))
            assert sum(area[r] for r in zone) <= c.max_area_m2 + 1e-6
            assert sum(nodes[r] for r in zone) <= c.max_nodes


def test_doorless_room_is_always_a_singleton():
    g, area = _fixture_graph("2814")  # one zero-door eligible room
    isolated = [r for r in g.nodes if g.degree(r) == 0]
    assert len(isolated) == EXPECTED["2814"]["zero_door_elig"] == 1
    for zoning in enumerate_zonings(g, area, Constraints()):
        assert frozenset(isolated) in zoning


def test_zonings_are_deterministic_and_sorted():
    g, area = _fixture_graph("13344")
    a = enumerate_zonings(g, area, Constraints())
    b = enumerate_zonings(g, area, Constraints())
    assert a == b
    assert [len(z) for z in a] == sorted(len(z) for z in a)  # fewest hubs first


def _path(n: int, room_m2: float = 10.0) -> tuple[nx.Graph, dict[str, float]]:
    g = nx.path_graph([f"R{i:02d}" for i in range(n)])
    return g, {r: room_m2 for r in g.nodes}


def test_feasible_zones_respect_caps_and_contiguity():
    g, area = _path(4, 40.0)
    zones = set(feasible_zones(g, area, Constraints()))
    assert frozenset({"R00", "R01"}) in zones
    assert frozenset({"R00", "R02"}) not in zones  # not connected
    assert frozenset({"R00", "R01", "R02"}) not in zones  # 120 m² > 100


def test_node_counts_cap_zones():
    g, area = _path(6, 1.0)
    nodes = {r: 1 for r in g.nodes}
    assert min_hubs(enumerate_zonings(g, area, Constraints(), nodes=nodes)) == 2
    nodes["R00"] = 0  # a folded sliver carries no node
    zones = feasible_zones(g, area, Constraints(), nodes=nodes)
    assert frozenset(g.nodes) - {"R05"} in zones  # 5 rooms, 4 nodes


def test_component_over_16_rooms_raises_too_large():
    g, area = _path(17, 1.0)
    with pytest.raises(TooLarge):
        enumerate_zonings(g, area, Constraints())


def test_room_over_cap_is_infeasible():
    g, area = _path(2, 50.0)
    area["R01"] = 120.0
    with pytest.raises(Infeasible, match="R01"):
        enumerate_zonings(g, area, Constraints())


def test_zone_needs_a_hub_capable_room():
    g, area = _path(3, 10.0)
    # only R02 can host a hub: every zone must contain it, so all three rooms form one zone
    zonings = enumerate_zonings(g, area, Constraints(), hub_capable={"R02"})
    assert zonings == [(frozenset(g.nodes),)]
    # a doorless room that can't host a hub has no zone at all
    g.add_node("R09")
    area["R09"] = 1.0
    with pytest.raises(Infeasible, match="no hub-capable room"):
        enumerate_zonings(g, area, Constraints(), hub_capable={"R02"})


def test_missing_node_count_names_the_room():
    g, area = _path(3, 10.0)
    with pytest.raises(ValueError, match="R02"):
        feasible_zones(g, area, Constraints(), nodes={"R00": 1, "R01": 1})


def test_too_many_zonings_raises_too_large():
    """A 4x4 grid of doors has ~1M zonings; stop at MAX_ZONINGS so #49's fallback kicks in."""
    g = nx.relabel_nodes(nx.grid_2d_graph(4, 4), lambda n: f"R{n[0]}{n[1]}")
    with pytest.raises(TooLarge, match="zonings"):
        enumerate_zonings(g, {r: 12.0 for r in g.nodes}, Constraints())
    assert MAX_ZONINGS >= 116  # the most any ResPlan plan has


# --------------------------------------------------------------------------- rule 6 (C18)


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_hub_capability_leaves_fixture_minimums_unchanged(plan_id):
    plan, g = _fixture(plan_id)
    capable = hub_capable_rooms(plan, Constraints())
    assert capable == set(g.nodes)  # every fixture room can host a hub
    area, nodes = eligible_area_and_nodes(plan)
    zonings = enumerate_zonings(g, area, Constraints(), nodes=nodes, hub_capable=capable)
    assert min_hubs(zonings) == EXPECTED[plan_id]["min_hubs5"]
    assert len(zonings) == EXPECTED[plan_id]["n_all5"]


def _room(rid: str, poly) -> Room:
    return Room(id=rid, label=rid, type="bedroom", eligible=True, polygon=poly, area_m2=poly.area)


def _synthetic_plan(rooms, walls, inner) -> Plan:
    return Plan(
        plan_id="PLAN_T", m_per_unit=1.0, wall_depth_m=0.2, inner=inner, rooms=rooms, walls=walls
    )


def test_doorless_sliver_room_makes_the_plan_infeasible():
    """A 0.6 m wide room walled on all sides has no point 0.51 m from a wall."""
    from shapely.geometry import box

    big, sliver = box(0, 0, 4, 4), box(4.2, 0, 4.8, 4)
    walls = [box(-0.2, -0.2, 5.0, 0), box(-0.2, 4, 5.0, 4.2), box(-0.2, 0, 0, 4),
             box(4, 0, 4.2, 4), box(4.8, 0, 5.0, 4)]  # fmt: skip
    plan = _synthetic_plan(
        [_room("R00", big), _room("R01", sliver)], walls, box(-0.2, -0.2, 5, 4.2)
    )
    capable = hub_capable_rooms(plan, Constraints())
    assert capable == {"R00"}
    g = nx.Graph()
    g.add_nodes_from(["R00", "R01"])  # doorless: no link
    with pytest.raises(Infeasible, match="no hub-capable room"):
        enumerate_zonings(g, {"R00": 16.0, "R01": 2.4}, Constraints(), hub_capable=capable)


def test_narrow_room_open_to_a_passage_is_hub_capable():
    """1 m wide (no point 0.51 m from its own outline) but open on one side: walls are far."""
    from shapely.geometry import box

    narrow, hall = box(0, 0, 1, 3), box(1, 0, 4, 3)  # shared edge x = 1 is an open passage
    walls = [box(-0.2, -0.2, 4.2, 0), box(-0.2, 3, 4.2, 3.2), box(-0.2, 0, 0, 3),
             box(4, 0, 4.2, 3)]  # fmt: skip
    plan = _synthetic_plan(
        [_room("R00", narrow), _room("R01", hall)], walls, box(-0.2, -0.2, 4.2, 3.2)
    )
    assert narrow.buffer(-0.51).is_empty  # nothing 0.51 m from the room's own boundary
    assert "R00" in hub_capable_rooms(plan, Constraints())


# --------------------------------------------------------------------------- CLI (See it)


def _cli(*args):
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-m", "hackhive.zoning", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_cli_prints_min_hubs_lb_and_count():
    """The issue's See it: a plain plan file, through load_plan + build_graph."""
    r = _cli(str(FIXTURES / "plans" / "plan-5924.json"))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "min hubs 3 (LB 2) · 7 zonings"


def test_cli_fails_loudly_on_a_bad_plan(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"hello": 1}', encoding="utf-8")
    r = _cli(str(bad))
    assert r.returncode == 1
    assert "Error" in r.stderr and not r.stdout


# --------------------------------------------------------------------------- #9's graph


def test_1293_needs_3_hubs_once_the_vault_corner_door_links_are_gone():
    """The vault linked bedroom_0–bedroom_1 and living_0–bedroom_2 through corner doors that
    join neither pair (#62); with #9's graph 1293 needs 3 hubs (6 zonings), not 2 (12)."""
    plan, g = _fixture("1293")
    label = {r.id: r.label for r in plan.rooms}
    ours = {frozenset((label[u], label[v])) for u, v in g.edges}
    raw = json.loads(next((FIXTURES / "raw").glob("plan-1293-*.json")).read_text())
    eligible = {r.label for r in plan.rooms if r.eligible}
    vault = {
        frozenset((e["u"], e["v"]))
        for e in raw["strict_graph_edges"]
        if e["type"] in ("via_door", "via_opening") and {e["u"], e["v"]} <= eligible
    }
    assert vault - ours == {
        frozenset(("bedroom_0", "bedroom_1")),
        frozenset(("living_0", "bedroom_2")),
    }
    assert ours - vault == {frozenset(("kitchen_0", "bedroom_2"))}  # door_9's real pair
    area, nodes = eligible_area_and_nodes(plan)
    zonings = enumerate_zonings(g, area, Constraints(), nodes=nodes)
    assert (min_hubs(zonings), len(zonings)) == (3, 6)
    k2 = room_by_label(plan, "bedroom_2").id
    assert nx.has_path(g, k2, room_by_label(plan, "living_0").id)  # still one component


def test_the_vault_bridge_is_gone():
    """#13 part 2: zoning reads plain plans; the temporary vault reader is deleted."""
    assert importlib.util.find_spec("hackhive.vault") is None
