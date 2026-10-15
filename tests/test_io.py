"""Acceptance tests for #4 (read a ResPlan plan into metric layers: slide 4 scale, C2, C4,
C19-C24, C27) and #6 (the Plan with fixed nodes: `test_nodes_*`, C1-C4, C7, C38, C46, C48)."""

import json
import math
import re
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.geometry import (
    GeometryCollection,
    MultiPolygon,
    Point,
    Polygon,
    box,
    mapping,
    shape,
)

from hackhive.contracts import Constraints
from hackhive.io import (
    ELIGIBLE,
    EXCLUDED,
    get_geometries,
    load_plan,
    read_json,
    read_layers,
    room_by_label,
)
from hackhive.io import main as io_main

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
PLAN_IDS = list(EXPECTED)


def load(plan_id) -> dict:
    return read_json(FIXTURES / "plans" / f"plan-{plan_id}.json")


def synthetic(area=100.0, **geoms) -> dict:
    """A plan whose inner is a 100 × 100-unit square, plus the given geometries."""
    raw = {"id": "SYN", "area": area, "wall_depth": 1.0, "inner": mapping(box(0, 0, 100, 100))}
    raw.update({k: mapping(g) for k, g in geoms.items()})
    return raw


def eligible_area(layers) -> float:
    return sum(p.area for t in ELIGIBLE for p in layers.rooms[t])


def parts(geojson) -> list:
    g = shape(geojson)
    return [p for p in getattr(g, "geoms", [g]) if not p.is_empty]


def test_scale_5924():
    """C19: m_per_unit = sqrt(area / inner.area); eligible area 150.0 m²."""
    raw = load(5924)
    layers = read_layers(raw)
    assert layers.m_per_unit == pytest.approx(math.sqrt(raw["area"] / shape(raw["inner"]).area))
    assert eligible_area(layers) == pytest.approx(150.0, abs=0.1)
    assert layers.wall_depth_m == pytest.approx(raw["wall_depth"] * layers.m_per_unit)


def test_two_living_rooms_576():
    """C2: each living polygon is its own room (no ResPlan living union)."""
    assert len(read_layers(load(576)).rooms["living"]) == 2


def test_bowtie_repaired():
    """C20: a self-intersecting room comes back as valid, non-empty polygons."""
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    assert not bowtie.is_valid
    layers = read_layers(synthetic(bedroom=bowtie))
    out = [p for t in ELIGIBLE + EXCLUDED for p in layers.rooms[t]] + [layers.inner]
    assert len(layers.rooms["bedroom"]) == 2
    assert all(p.is_valid and not p.is_empty for p in out)
    assert eligible_area(layers) == pytest.approx(50 * layers.m_per_unit**2)


def test_doors_repaired_per_part_1293():
    """C2/C20: 1293's door MultiPolygon is invalid as a whole; repairing per part keeps all 10."""
    raw = load(1293)
    assert not shape(raw["door"]).is_valid
    doors = read_layers(raw).door
    assert len(doors) == 10
    assert all(d.is_valid and not d.is_empty for d in doors)


def test_infeasible_room_over_100():
    """C27: an eligible room scaled to 120 m² makes the plan infeasible; 100 m² doesn't."""
    square = box(0, 0, 100, 100)
    big = read_layers(synthetic(area=120.0, bedroom=square))
    assert big.rooms["bedroom"][0].area == pytest.approx(120)
    assert "room > 100 m²" in big.infeasible_reasons
    assert big.infeasible_reasons.count("room > 100 m²") == 1
    assert read_layers(synthetic(area=100.0, bedroom=square)).infeasible_reasons == []


def test_nested_collection():
    """C2: a MultiPolygon nested in a GeometryCollection comes back as its polygons."""
    t1, t2 = Polygon([(0, 0), (1, 0), (0, 1)]), Polygon([(2, 2), (3, 2), (2, 3)])
    got = get_geometries(GeometryCollection([MultiPolygon([t1, t2])]))
    assert len(got) == 2
    assert all(isinstance(g, Polygon) for g in got)


def test_bathroom_excluded():
    """C4: a bathroom is read into rooms["bathroom"] and is not eligible."""
    layers = read_layers(synthetic(bathroom=box(0, 0, 10, 10)))
    assert len(layers.rooms["bathroom"]) == 1
    assert "bathroom" not in ELIGIBLE and "bathroom" in EXCLUDED
    assert eligible_area(layers) == 0


def _out_layers(layers) -> dict[str, list]:
    out = {"inner": [layers.inner], "wall": layers.wall, "door": layers.door}
    out |= {"window": layers.window, "front_door": layers.front_door, **layers.rooms}
    return out


def test_frame_5924():
    """C24: default output = input × m (= input ÷ scale); flip_y gives y' = (y_max − y) · m."""
    raw = load(5924)
    y_max = shape(raw["inner"]).bounds[3]
    for c, fy in ((Constraints(), lambda y: y), (Constraints(flip_y=True), lambda y: y_max - y)):
        layers = read_layers(raw, c)
        m = layers.m_per_unit
        for key, polys in _out_layers(layers).items():
            src = parts(raw[key]) if key in raw else []
            assert len(polys) == len(src), key
            for got, want in zip(polys, src, strict=True):
                g, w = shapely.get_coordinates(got), shapely.get_coordinates(want)
                assert g.shape == w.shape, key
                assert np.allclose(g[:, 0], w[:, 0] * m, rtol=0, atol=1e-9), key
                assert np.allclose(g[:, 1], fy(w[:, 1]) * m, rtol=0, atol=1e-9), key


def test_origin_shift_is_metres():
    """origin_shift is subtracted after scaling, in metres (#38 owns the organiser frame check)."""
    raw = load(5924)
    base = read_layers(raw)
    shifted = read_layers(raw, Constraints(origin_shift=(1.0, 2.0)))
    for a, b in zip(_out_layers(base).values(), _out_layers(shifted).values(), strict=True):
        for p, q in zip(a, b, strict=True):
            g = shapely.get_coordinates(q)
            assert np.allclose(g, shapely.get_coordinates(p) - (1.0, 2.0), rtol=0, atol=1e-9)


class _NoNetArea(dict):
    """A plan that fails loudly if anything reads net_area (slide 4)."""

    def __getitem__(self, key):
        assert key != "net_area", "net_area was read"
        return super().__getitem__(key)

    def get(self, key, default=None):
        assert key != "net_area", "net_area was read"
        return super().get(key, default)


def test_net_area_never_read():
    """C19, slide 4: the scale uses only area and inner; net_area is never read."""
    raw = load(5924)
    assert "net_area" in raw
    trapped = read_layers(_NoNetArea(raw))
    without = {k: v for k, v in raw.items() if k != "net_area"}
    assert read_layers(without).m_per_unit == trapped.m_per_unit == read_layers(raw).m_per_unit


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_fixture_eligible_area_and_count(plan_id):
    """Every fixture's eligible area and polygon count match the measured profile."""
    layers = read_layers(load(plan_id))
    assert eligible_area(layers) == pytest.approx(EXPECTED[plan_id]["elig_m2"], abs=1e-6)
    assert sum(len(layers.rooms[t]) for t in ELIGIBLE) == EXPECTED[plan_id]["n_nodes"]
    assert layers.unknown_keys == []  # every geometry key was read; "neighbor" is not geometry


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_fixture_every_output_polygon_valid(plan_id):
    """C20: valid after scaling too (rounding can make a valid input self-intersect: 1293, 13344)."""
    layers = read_layers(load(plan_id))
    for key, polys in _out_layers(layers).items():
        for i, p in enumerate(polys):
            assert p.is_valid and not p.is_empty, f"{key}[{i}]"


def test_missing_keys_are_empty_layers():
    layers = read_layers(load(5924))
    assert set(layers.rooms) == set(ELIGIBLE + EXCLUDED)
    assert layers.rooms["storage"] == [] and layers.rooms["stair"] == []


def test_unknown_geometry_keys_reported():
    """C1 / slide 3 rule 1: a room under a key we don't know is reported, not silently dropped."""
    raw = synthetic(living_room=box(0, 0, 10, 10))
    raw["neighbor"] = [1, 2]  # not geometry: ignored
    layers = read_layers(raw)
    assert layers.unknown_keys == ["living_room"]
    assert layers.rooms["living"] == []


def test_caller_dict_not_mutated():
    raw = synthetic(balacony=box(0, 0, 10, 10))  # ResPlan's typo key
    before = dict(raw)
    layers = read_layers(raw)
    assert raw == before
    assert len(layers.rooms["balcony"]) == 1


@pytest.mark.parametrize("area", [0, -5, math.nan, math.inf, None])
def test_bad_area_raises(area):
    raw = synthetic(area=1.0)
    if area is None:
        del raw["area"]
    else:
        raw["area"] = area
    with pytest.raises(ValueError, match="SYN"):
        read_layers(raw)


@pytest.mark.parametrize("wall_depth", [0, -1, math.nan, None])
def test_bad_wall_depth_raises(wall_depth):
    """The organiser format may lack wall_depth (#46 Q1): fail loudly, naming the plan."""
    raw = synthetic()
    if wall_depth is None:
        del raw["wall_depth"]
    else:
        raw["wall_depth"] = wall_depth
    with pytest.raises(ValueError, match="SYN"):
        read_layers(raw)


def test_empty_inner_raises():
    raw = synthetic()
    raw["inner"] = {"type": "MultiPolygon", "coordinates": []}
    with pytest.raises(ValueError, match="SYN"):
        read_layers(raw)


# --------------------------------------------------------------------------- #6: Plan + fixed nodes

ID_RE = re.compile(r"^R\d{2,}$|^N\d+$|^H\d+$")
# a 5 × 5 m living part plus a 0.3 m-wide part sharing its edge (synthetic: 1 unit = 0.1 m)
SLIVER = MultiPolygon([box(0, 0, 50, 50), box(50, 0, 53, 30)])


def fixture_plan(plan_id):
    return load_plan(FIXTURES / "plans" / f"plan-{plan_id}.json")


def test_nodes_5924_one_per_eligible_room():
    """C1, C4: 5 generated nodes, one inside each eligible room, ≥ 0.5 m clear, none in a bathroom."""
    plan = fixture_plan(5924)
    rooms = {r.id: r for r in plan.rooms}
    baths = [r.polygon for r in plan.rooms if r.type == "bathroom"]
    assert len(plan.nodes) == 5 and baths
    assert sorted(n.room_id for n in plan.nodes) == sorted(r.id for r in plan.rooms if r.eligible)
    for n in plan.nodes:
        assert rooms[n.room_id].eligible and rooms[n.room_id].polygon.covers(Point(n.xy))
        assert n.clearance_m >= 0.5 and n.given is False
        assert not any(b.covers(Point(n.xy)) for b in baths)
    assert plan.warnings == [] and plan.folded_parts == []


def test_nodes_576_two_living_parts():
    """C2: each living polygon is its own room with its own node (576's second part is 1.52 m)."""
    plan = fixture_plan(576)
    living = {r.id for r in plan.rooms if r.type == "living"}
    assert len(plan.nodes) == 8
    assert len(living) == 2 and sum(n.room_id in living for n in plan.nodes) == 2
    assert plan.folded_parts == []


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_nodes_fixture_counts_and_ids(plan_id):
    """C46, C48: n_nodes per fixture; ids are R-, N-style, unique and plan-global; labels aren't."""
    plan = fixture_plan(plan_id)
    rooms = {r.id: r for r in plan.rooms}
    assert plan.plan_id == f"PLAN_{plan_id}"
    assert len(plan.nodes) == EXPECTED[plan_id]["n_nodes"]
    assert all(rooms[n.room_id].polygon.covers(Point(n.xy)) for n in plan.nodes)
    ids = [r.id for r in plan.rooms] + [n.id for n in plan.nodes]
    assert all(ID_RE.match(i) for i in ids) and len(set(ids)) == len(ids)
    assert not any(ID_RE.match(r.label) for r in plan.rooms)


def test_nodes_5924_ids_in_documented_order():
    """C46, C48: eligible rooms R01… living → bedroom → kitchen → storage, then excluded; N1… in
    room order; doors door_<i>, not yet linked to rooms (#9)."""
    plan = fixture_plan(5924)
    eligible = [(r.id, r.label) for r in plan.rooms if r.eligible]
    assert eligible == [
        ("R01", "living_0"),
        ("R02", "bedroom_0"),
        ("R03", "bedroom_1"),
        ("R04", "bedroom_2"),
        ("R05", "kitchen_0"),
    ]
    assert [r.id for r in plan.rooms if not r.eligible] == ["R06", "R07", "R08", "R09"]
    assert [(n.id, n.room_id) for n in plan.nodes] == [(f"N{i}", f"R0{i}") for i in range(1, 6)]
    assert [d.id for d in plan.doors] == [f"door_{i}" for i in range(len(plan.doors))]
    assert plan.doors and all(d.rooms is None for d in plan.doors) and plan.openings == []


def test_nodes_given_are_scaled_never_moved():
    """C3: organiser nodes (drawing units) go through the same frame as the geometry."""
    raw = synthetic(bedroom=box(0, 0, 50, 50), kitchen=box(60, 0, 100, 40))
    raw["nodes"] = [[25, 25], {"x": 80, "y": 20, "id": "N7"}]
    plan = load_plan(raw)
    m = plan.m_per_unit
    assert [n.given for n in plan.nodes] == [True, True]
    assert plan.nodes[0].xy == (25 * m, 25 * m)  # exact: no flip, no shift
    assert plan.nodes[1].xy == (80 * m, 20 * m)
    assert [(n.id, n.room_id) for n in plan.nodes] == [("N1", "R01"), ("N7", "R02")]
    assert plan.nodes[0].clearance_m == pytest.approx(2.5)

    raw["nodes"] = {"type": "MultiPoint", "coordinates": [[25, 25], [80, 20]]}
    assert [n.xy for n in load_plan(raw).nodes] == [(25 * m, 25 * m), (80 * m, 20 * m)]

    shifted = load_plan(raw, Constraints(origin_shift=(1.0, 2.0)))
    assert shifted.nodes[0].xy == pytest.approx((25 * m - 1.0, 25 * m - 2.0))
    assert shifted.rooms[0].polygon.covers(Point(shifted.nodes[0].xy))


def test_nodes_two_given_in_one_room_kept_with_warning():
    raw = synthetic(bedroom=box(0, 0, 50, 50))
    raw["nodes"] = [[20, 20], [30, 30]]
    plan = load_plan(raw)
    assert [n.room_id for n in plan.nodes] == ["R01", "R01"]
    assert len(plan.warnings) == 1 and "R01" in plan.warnings[0]


def test_nodes_narrow_room_warns():
    """A2: a 0.6 m-wide room keeps its node at max clearance (≈ 0.3 m) and gets a warning."""
    plan = load_plan(synthetic(bedroom=box(10, 10, 16, 40)))
    (node,) = plan.nodes
    assert plan.rooms[0].polygon.covers(Point(node.xy)) and node.given is False
    assert node.clearance_m == pytest.approx(0.3, abs=0.01)
    assert len(plan.warnings) == 1
    assert all(s in plan.warnings[0] for s in ("R01", "bedroom_0", "N1", "< 0.5 m"))


def test_nodes_living_sliver_folded():
    """C38: a 0.3 m living part (no organiser nodes) folds into the part it shares an edge with."""
    raw = synthetic(living=SLIVER)
    plan = load_plan(raw)
    living = [r for r in plan.rooms if r.type == "living"]
    assert len(living) == 1 and len(plan.nodes) == 1
    assert living[0].area_m2 == pytest.approx(25.9)
    assert sum(r.area_m2 for r in plan.rooms if r.eligible) == pytest.approx(
        eligible_area(read_layers(raw))
    )
    (fold,) = plan.folded_parts
    assert (fold.room_id, fold.into_room_id) == ("living_1", "R01")
    assert fold.area_m2 == pytest.approx(0.9)
    assert plan.warnings == []


def test_nodes_lone_sliver_keeps_its_node():
    """C38: with no living part to fold into, the sliver keeps its node and gets a warning."""
    plan = load_plan(synthetic(living=box(50, 0, 53, 30)))
    (node,) = plan.nodes
    assert plan.folded_parts == [] and node.room_id == "R01"
    assert node.clearance_m == pytest.approx(0.15, abs=0.01)
    assert len(plan.warnings) == 1 and "living_0" in plan.warnings[0]


def test_nodes_sliver_kept_with_given_nodes():
    """C38: with organiser nodes nothing is folded, and the node in the sliver is kept."""
    raw = synthetic(living=SLIVER)
    raw["nodes"] = [[25, 25], [51.5, 15]]
    plan = load_plan(raw)
    assert plan.folded_parts == []
    assert len([r for r in plan.rooms if r.type == "living"]) == 2
    assert [n.given for n in plan.nodes] == [True, True]
    assert plan.nodes[1].xy == pytest.approx((5.15, 1.5))


def test_nodes_avoid_a_wall_inside_the_room():
    """Slide 2 (nodes 0.5 m from walls): ResPlan rooms can run under walls (1293 living_0), so the
    node and its clearance use the room's free space, not its bare outline (#59 review)."""
    plan = load_plan(synthetic(bedroom=box(0, 0, 60, 40), wall=box(19, 0, 21, 25)))
    (node,) = plan.nodes
    assert not plan.walls[0].intersects(Point(node.xy))
    assert node.clearance_m == pytest.approx(1.95, abs=0.01)
    assert node.xy == pytest.approx((4.05, 2.0), abs=0.03)
    assert plan.warnings == []


def test_nodes_wall_narrowed_room_warns():
    """A room roomy by its outline but 0.8 m wide between walls gets the clearance warning."""
    plan = load_plan(synthetic(bedroom=box(0, 0, 30, 40), wall=box(8, 0, 30, 40)))
    (node,) = plan.nodes
    assert node.clearance_m == pytest.approx(0.4, abs=0.01)
    assert len(plan.warnings) == 1 and "N1" in plan.warnings[0]


def test_nodes_room_under_walls_keeps_a_node():
    """A room entirely under walls still gets its one node (coverage), flagged with clearance 0."""
    plan = load_plan(synthetic(bedroom=box(0, 0, 30, 30), wall=box(0, 0, 30, 30)))
    (node,) = plan.nodes
    assert plan.rooms[0].polygon.covers(Point(node.xy))
    assert node.clearance_m == 0.0 and len(plan.warnings) == 1


def test_nodes_take_the_middle_of_a_max_clearance_plateau():
    """An 8 × 3 m room is 1.5 m clear all along its centre line. GEOS returns one end of it, and a
    different point on macOS arm64 than on Linux (up to 0.73 m apart, #38), so the node is the
    line's middle, the same on every platform."""
    (node,) = load_plan(synthetic(bedroom=box(0, 0, 80, 30))).nodes
    assert node.xy == pytest.approx((4.0, 1.5), abs=1e-3)
    assert node.clearance_m == pytest.approx(1.5, abs=1e-3)


def test_nodes_sliver_by_free_space_is_folded():
    """C38: a living part 1.2 m wide by its outline but 0.3 m wide between walls is a sliver,
    judged on the same free space as the node."""
    living = MultiPolygon([box(0, 0, 50, 50), box(50, 0, 62, 30)])
    plan = load_plan(synthetic(living=living, wall=box(53, 0, 62, 30)))
    (fold,) = plan.folded_parts
    assert (fold.room_id, fold.into_room_id) == ("living_1", "R01")
    assert len(plan.nodes) == 1


def test_nodes_given_node_inside_a_wall_is_kept_and_flagged():
    """Organiser nodes are never moved: one under a wall keeps its point, clearance 0, warning."""
    raw = synthetic(bedroom=box(0, 0, 60, 40), wall=box(19, 0, 21, 25))
    raw["nodes"] = [[20, 10]]
    plan = load_plan(raw)
    (node,) = plan.nodes
    assert node.given and node.xy == (20 * plan.m_per_unit, 10 * plan.m_per_unit)
    assert node.clearance_m == 0.0
    assert len(plan.warnings) == 1 and "N1" in plan.warnings[0]


def test_nodes_zero_given_in_a_room_warns():
    """Legal (decision_conventions §3), but likely a room-set mismatch with the organisers."""
    raw = synthetic(bedroom=box(0, 0, 50, 50), kitchen=box(60, 0, 100, 40))
    raw["nodes"] = [[25, 25]]
    plan = load_plan(raw)
    assert len(plan.nodes) == 1
    assert len(plan.warnings) == 1
    assert all(s in plan.warnings[0] for s in ("R02", "kitchen_0", "no given node"))


@pytest.mark.parametrize(
    "nodes",
    [
        [[99, 99]],  # in no eligible room
        [[70, 10]],  # in the bathroom
        [[1, 2, 3]],
        {"type": "Point", "coordinates": [1, 2]},
        "N1",
    ],
)
def test_nodes_bad_given_nodes_raise(nodes):
    raw = synthetic(bedroom=box(0, 0, 50, 50), bathroom=box(60, 0, 80, 20))
    raw["nodes"] = nodes
    with pytest.raises(ValueError, match="SYN"):
        load_plan(raw)


def test_nodes_unknown_geometry_key_raises():
    """#4's unknown_keys fail loudly here: a dropped eligible room would break coverage."""
    with pytest.raises(ValueError, match="SYN.*living_room"):
        load_plan(synthetic(living_room=box(0, 0, 10, 10)))


def test_nodes_plan_id_and_infeasible_reason():
    raw = synthetic(area=120.0, bedroom=box(0, 0, 100, 100))
    plan = load_plan(raw)
    assert plan.plan_id == "PLAN_SYN"
    assert "room > 100 m²" in plan.warnings
    raw["plan_id"] = "PLAN_0123"  # an organiser id is used as given
    assert load_plan(raw).plan_id == "PLAN_0123"


def test_nodes_room_by_label_2814():
    """C46: debug labels resolve to R-style ids; an unknown label names the plan."""
    plan = fixture_plan(2814)
    assert re.match(r"^R\d{2,}$", room_by_label(plan, "kitchen_0").id)
    with pytest.raises(KeyError, match="PLAN_2814"):
        room_by_label(plan, "nope_9")


def test_nodes_facts_cli_5924(capsys):
    """C7, C19: the facts line, in the issue's "See it" form."""
    assert io_main([str(FIXTURES / "plans" / "plan-5924.json")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("5924 · 0.0719 m/unit · ")
    assert all(piece in out for piece in ("150.0 m²", "5 nodes", "LB 2"))
