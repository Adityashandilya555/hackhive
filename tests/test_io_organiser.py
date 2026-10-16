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


# --------------------------------------------------------------------------- solved (#16)


def _solve_raw(raw: dict, tmp_path: Path, name: str = "plan.json"):
    from hackhive.solve import solve_file

    path = tmp_path / name
    path.write_text(json.dumps(raw), encoding="utf-8")
    return solve_file(path)


def _leg_ends(sub) -> dict[str, list[tuple[float, float]]]:
    ends: dict[str, list[tuple[float, float]]] = {}
    for hub in sub.hubs:
        for seg in hub.route_segments:
            ends.setdefault(seg.from_, []).append(tuple(seg.polyline[0]))
            ends.setdefault(seg.to, []).append(tuple(seg.polyline[-1]))
    return ends


def _assert_solved_in_the_nodes_frame(raw: dict, result) -> None:
    """No `error`, the organiser's ids verbatim, and every leg ends exactly on a given node
    point (scaled, never moved or transformed back: the checker matches *their* points)."""
    from hackhive.checker import check_rules

    sub, side = result.submission, result.sidecar
    assert result.submittable, side.reason
    assert sub.plan_id == side.plan_id == raw["plan_id"]
    room_ids = {r["id"] for r in raw["rooms"]}
    for hub in sub.hubs:
        assert hub.room_id in room_ids and set(hub.assigned_rooms) <= room_ids
    assert sorted(n for h in sub.hubs for n in h.assigned_nodes) == sorted(
        n["id"] for n in raw["nodes"]
    )
    m = result.plan.m_per_unit
    given = {n["id"]: (n["x"] * m, n["y"] * m) for n in raw["nodes"]}
    ends = _leg_ends(sub)
    for nid, xy in given.items():
        assert len(ends[nid]) == 2
        assert all(e == pytest.approx(xy, abs=1e-4) for e in ends[nid]), nid  # T6: 4-decimal output
    errors = [v for v in check_rules(load_plan(raw), sub) if v.severity == "error"]
    assert not errors and side.violations == check_rules(load_plan(raw), sub)


def test_solving_the_guess_uses_the_organiser_ids_verbatim(tmp_path):
    """C46, C48: plan_id, room ids and node ids pass through to the submission."""
    raw = _guess()
    result = _solve_raw(raw, tmp_path)
    _assert_solved_in_the_nodes_frame(raw, result)
    assert result.sidecar.frame == "identity"


@pytest.mark.parametrize("frame", ["flip_y", "origin_shift"])
def test_a_reframed_guess_is_solved_in_the_nodes_frame(frame, tmp_path):
    """#61 review (Rohit): the geometry moved into the nodes' frame, so the writer emits that
    frame as is; nothing is transformed back on output. The sidecar records the frame."""
    raw = _guess()
    x0, y0, _, y1 = _units_bounds(raw)
    for n in raw["nodes"]:
        if frame == "flip_y":
            n["y"] = y0 + y1 - n["y"]
        else:
            n["x"] -= x0
            n["y"] -= y0
    result = _solve_raw(raw, tmp_path)
    assert result.plan.frame == result.sidecar.frame == frame
    assert any(w.startswith(f"frame: {frame}") for w in result.sidecar.warnings)
    _assert_solved_in_the_nodes_frame(raw, result)
    inner = result.plan.inner
    assert all(inner.covers(Point(h.location.x, h.location.y)) for h in result.submission.hubs)


@pytest.mark.parametrize(
    ("break_it", "says"),
    [
        (lambda raw: raw.pop("area"), "missing area"),
        (lambda raw: raw.pop("inner"), "missing inner"),
        (
            lambda raw: [n.update(x=n["x"] + 1e6) for n in raw["nodes"]],
            "frame: 5 nodes outside rooms",
        ),
    ],
    ids=["no-area", "no-inner", "nodes-outside"],
)
def test_an_unreadable_organiser_plan_is_an_input_error_sidecar(break_it, says, tmp_path):
    """C21, C43: no submission, never a crash; the sidecar says `input_error: …` and why."""
    from hackhive.solve import submit

    raw = _guess()
    break_it(raw)
    result = _solve_raw(raw, tmp_path)
    written = submit(result, tmp_path / "out")
    assert [p.name for p in written] == ["PLAN_5924.sidecar.json"]
    side = result.sidecar
    assert side.status == "input_error" and result.submission is None
    assert side.reason.startswith("input_error: ") and says in side.reason
    assert side.frame is None  # no plan loaded, so no frame was detected (#73 review)


def test_a_node_a_centimetre_outside_is_kept_and_the_sidecar_warns(tmp_path):
    """C3, C9: kept unmoved, assigned to its room, and the sidecar carries the warning."""
    raw = _guess()
    m = load_plan(GUESS).m_per_unit
    node = raw["nodes"][0]
    room = next(r for r in raw["rooms"] if r["id"] == node["room_id"])
    poly = shape(room["polygon"])
    ray = LineString([(node["x"], node["y"]), (poly.bounds[0] - 10, node["y"])])
    node["x"] = min(p.x for p in shapely.get_parts(ray.intersection(poly.boundary))) - 0.01 / m
    result = _solve_raw(raw, tmp_path)
    got = next(n for n in result.plan.nodes if n.id == node["id"])
    assert got.xy == pytest.approx((node["x"] * m, node["y"] * m), abs=1e-9)
    assert got.room_id == node["room_id"]
    assert any(node["id"] in w and "outside" in w for w in result.sidecar.warnings)
    # Known gap (#73, decision_conventions ⚠ #46): this node sits in the wall band, more than one
    # grid cell from R01's free cells, so it can't be routed yet. Pinned so a change to grid.snap
    # or a #46 answer shows up here.
    assert result.sidecar.status == "infeasible" and result.submission is None
    assert "no free cell" in result.sidecar.reason and node["id"] in result.sidecar.reason


def test_cli_solves_the_guess_and_reports_an_input_error(tmp_path, capsys):
    from hackhive import cli

    assert cli.main(["solve", str(GUESS), "--out", str(tmp_path / "ok")]) == 0
    assert sorted(p.name for p in (tmp_path / "ok").iterdir()) == [
        "PLAN_5924.json",
        "PLAN_5924.png",
        "PLAN_5924.sidecar.json",
    ]
    raw = _guess()
    del raw["area"]
    bad = tmp_path / "no-area.json"
    bad.write_text(json.dumps(raw), encoding="utf-8")
    assert cli.main(["solve", str(bad), "--out", str(tmp_path / "bad")]) == 1
    assert [p.name for p in (tmp_path / "bad").iterdir()] == ["PLAN_5924.sidecar.json"]
    assert "input_error" in capsys.readouterr().out


def test_the_frame_type_is_defined_once():
    """#73 review: Plan, Sidecar and the loader share one `Frame` literal from contracts."""
    import typing

    from hackhive import contracts
    from hackhive.contracts import Plan, Sidecar

    assert io.Frame is contracts.Frame
    assert typing.get_args(contracts.Frame) == ("identity", "flip_y", "origin_shift")
    assert Plan.model_fields["frame"].annotation is contracts.Frame
    assert Sidecar.model_fields["frame"].annotation == contracts.Frame | None
