"""Acceptance tests for #38: the organiser input adapter.

The organisers' format isn't known yet (#46 Q1); `tests/fixtures/organiser/guess-5924.json` is
plan 5924 in a guessed shape, so these tests pin the mechanism, not the real keys.
"""

import copy
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
import shapely
from shapely.geometry import LineString, Point, shape

from hackhive import io
from hackhive.contracts import Constraints, lower_bound
from hackhive.io import InputFormatError, detect_format, load_plan, normalise

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RESPLAN = FIXTURES / "plans" / "plan-5924.json"
GUESS = FIXTURES / "organiser" / "guess-5924.json"


def _guess() -> dict:
    return json.loads(GUESS.read_text(encoding="utf-8"))


def _resplan() -> dict:
    return json.loads(RESPLAN.read_text(encoding="utf-8"))


def _eligible_m2(plan) -> float:
    return sum(r.area_m2 for r in plan.rooms if r.eligible)


# --------------------------------------------------------------------------- detection


def test_detect_format():
    assert detect_format(_resplan()) == "resplan"
    assert detect_format(_guess()) == "organiser"
    assert detect_format({}) == "unknown"
    assert detect_format({"hello": 1}) == "unknown"
    assert detect_format([1, 2]) == "unknown"


def test_unknown_format_fails_loudly():
    with pytest.raises(InputFormatError, match="unknown input format"):
        load_plan({"hello": 1})


@pytest.mark.parametrize("key", ["area", "inner"])
@pytest.mark.parametrize("make", [_resplan, _guess], ids=["resplan", "organiser"])
def test_missing_area_or_inner_names_the_key(make, key):
    raw = make()
    del raw[key]
    with pytest.raises(InputFormatError, match=f"missing {key}"):
        load_plan(raw)


# --------------------------------------------------------------------------- both shapes agree


def test_resplan_and_organiser_shapes_give_the_same_plan():
    a, b = load_plan(RESPLAN), load_plan(GUESS)
    assert _eligible_m2(a) == pytest.approx(150.0, abs=0.1)
    assert _eligible_m2(b) == pytest.approx(_eligible_m2(a), abs=0.1)
    assert len(a.nodes) == len(b.nodes) == 5
    for n in a.nodes:
        assert min(math.dist(n.xy, m.xy) for m in b.nodes) <= 0.001
    c = Constraints()
    assert lower_bound(_eligible_m2(b), len(b.nodes), c) == 2


def test_organiser_ids_are_used_verbatim():
    raw = _guess()
    plan = load_plan(raw)
    assert plan.plan_id == raw["plan_id"]
    by_id = {r.id: r for r in plan.rooms}
    for room in raw["rooms"]:
        assert by_id[room["id"]].type == room["type"]
        assert by_id[room["id"]].area_m2 == pytest.approx(
            shape(room["polygon"]).area * plan.m_per_unit**2, rel=1e-6
        )
    assert {n.id: n.room_id for n in plan.nodes} == {n["id"]: n["room_id"] for n in raw["nodes"]}
    assert all(n.given for n in plan.nodes)


def test_given_nodes_are_scaled_never_moved():
    raw = _guess()
    plan = load_plan(raw)
    m = plan.m_per_unit
    for n in raw["nodes"]:
        got = next(p for p in plan.nodes if p.id == n["id"])
        assert got.xy == pytest.approx((n["x"] * m, n["y"] * m), abs=1e-9)


def test_key_mapping_is_one_table(monkeypatch):
    """When the real sample arrives, renaming a key is a one-line change in ORGANISER_KEYS."""
    expected = len(load_plan(GUESS).walls)
    raw = _guess()
    raw["wall_polygons"] = raw.pop("walls")
    monkeypatch.setitem(io.ORGANISER_KEYS, "walls", "wall_polygons")
    assert len(load_plan(raw).walls) == expected > 0


# --------------------------------------------------------------------------- frame check


def _units_bounds(raw: dict):
    return shape(raw["inner"]).bounds


def _assert_nodes_fixed_and_inside(raw: dict, plan) -> None:
    """Slide 2: given nodes never move. A non-identity frame moves the *geometry* into the
    nodes' frame instead, so every node sits at its given (scaled) point, inside its room."""
    m = plan.m_per_unit
    for given in raw["nodes"]:
        node = next(n for n in plan.nodes if n.id == given["id"])
        assert node.xy == pytest.approx((given["x"] * m, given["y"] * m), abs=1e-9)
        room = next(r for r in plan.rooms if r.id == node.room_id)
        assert room.polygon.covers(Point(node.xy))
    assert all(plan.inner.covers(Point(n.xy)) for n in plan.nodes)


def test_flipped_y_is_detected_and_nodes_land_in_their_rooms():
    raw = _guess()
    _, y0, _, y1 = _units_bounds(raw)
    for n in raw["nodes"]:
        n["y"] = y0 + y1 - n["y"]
    plan = load_plan(raw)
    straight = load_plan(GUESS)
    assert plan.frame == "flip_y" and straight.frame == "identity"
    assert any("frame: flip_y" in w for w in plan.warnings)
    assert {n.id: n.room_id for n in plan.nodes} == {n.id: n.room_id for n in straight.nodes}
    _assert_nodes_fixed_and_inside(raw, plan)


def test_nodes_relative_to_the_lower_left_corner_are_detected():
    raw = _guess()
    x0, y0, _, _ = _units_bounds(raw)
    for n in raw["nodes"]:
        n["x"] -= x0
        n["y"] -= y0
    plan = load_plan(raw)
    assert plan.frame == "origin_shift"
    assert any("frame: origin_shift" in w for w in plan.warnings)
    assert {n.id: n.room_id for n in plan.nodes} == {
        n.id: n.room_id for n in load_plan(GUESS).nodes
    }
    _assert_nodes_fixed_and_inside(raw, plan)


def test_nodes_outside_rooms_under_every_frame_are_an_input_error():
    raw = _guess()
    for n in raw["nodes"]:
        n["x"] += 1e6
    with pytest.raises(InputFormatError, match="frame: 5 nodes outside rooms"):
        load_plan(raw)


def test_node_a_centimetre_outside_its_room_is_kept_and_assigned():
    raw = _guess()
    m = load_plan(GUESS).m_per_unit
    node = raw["nodes"][0]
    room = next(r for r in raw["rooms"] if r["id"] == node["room_id"])
    poly = shape(room["polygon"])
    ray = LineString([(node["x"], node["y"]), (poly.bounds[0] - 10, node["y"])])
    hits = shapely.get_parts(ray.intersection(poly.boundary))
    edge_x = min(p.x for p in hits)  # the room's left edge, at the node's y
    node["x"] = edge_x - 0.01 / m  # 1 cm outside, inside the wall band
    plan = load_plan(raw)
    got = next(n for n in plan.nodes if n.id == node["id"])
    assert got.xy == pytest.approx((node["x"] * m, node["y"] * m), abs=1e-9)  # never moved
    assert got.room_id == node["room_id"]
    assert any(node["id"] in w and "outside" in w for w in plan.warnings)


def test_normalise_leaves_resplan_input_alone():
    raw = _resplan()
    out, ids = normalise(copy.deepcopy(raw))
    assert out == raw and ids is None


# --------------------------------------------------------------------------- CLI (See it)


def test_cli_prints_the_same_facts_for_the_guess():
    def facts(path):
        r = subprocess.run(
            [sys.executable, "-m", "hackhive.io", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert r.returncode == 0, r.stderr
        return r.stdout.strip().split(" · ", 1)[1]  # drop the id column

    assert facts(GUESS) == facts(RESPLAN)


def test_identity_frame_is_recorded():
    assert load_plan(GUESS).frame == "identity"
    assert load_plan(RESPLAN).frame == "identity"


def test_a_node_room_id_is_authoritative():
    """The organiser's room id for a node wins; geometry only checks it."""
    raw = _guess()
    plan = load_plan(raw)
    assert {n.id: n.room_id for n in plan.nodes} == {g["id"]: g["room_id"] for g in raw["nodes"]}


def test_a_node_room_id_that_contradicts_the_geometry_is_an_input_error():
    raw = _guess()
    far = next(r["id"] for r in raw["rooms"] if r["id"] != raw["nodes"][0]["room_id"])
    raw["nodes"][0]["room_id"] = far
    with pytest.raises(InputFormatError, match=f"{raw['nodes'][0]['id']}.*{far}"):
        load_plan(raw)


def test_a_node_room_id_that_names_no_eligible_room_is_an_input_error():
    raw = _guess()
    raw["nodes"][0]["room_id"] = "R99"
    with pytest.raises(InputFormatError, match="R99"):
        load_plan(raw)
