"""Acceptance tests for #16: `solve()`, the submit gate, the slide-8 writer and the CLI; #22:
`enumerate()`, `rank_key` and the fallback down the ranked list."""

import dataclasses
import functools
import json
from itertools import pairwise
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Point, box, mapping

import hackhive.solve as solve_mod
from hackhive import cli
from hackhive.checker import check_rules
from hackhive.contracts import Constraints, Sidecar, Submission, Violation, lower_bound
from hackhive.io import load_plan
from hackhive.rings import Hub
from hackhive.solve import Result, solve, solve_file, submit

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PLANS = FIXTURES / "plans"
SOLUTIONS = FIXTURES / "solutions"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
PLAN_IDS = sorted(EXPECTED, key=int)
C = Constraints()


def _sidecar(out: Path, plan_id: str) -> Sidecar:
    return Sidecar.model_validate_json((out / f"{plan_id}.sidecar.json").read_text("utf-8"))


def _submission(out: Path, plan_id: str) -> Submission:
    return Submission.model_validate_json((out / f"{plan_id}.json").read_text("utf-8"))


@functools.cache
def _solved(plan_id: str) -> Result:
    """One `solve_file` per fixture for this module: 13344 takes ~12 s on 4 workers (~34 s on one
    core) since #22 ranks every 3-hub arrangement. `Result` is frozen and `submit` never changes
    it, so sharing is safe."""
    return solve_file(PLANS / f"plan-{plan_id}.json", C)


# --------------------------------------------------------------------------- the 7 fixtures


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_each_fixture_solves_to_a_valid_submission(plan_id, tmp_path):
    """C16, C22, C24, C39: a submission that passes the six rules (no `error`), at min hubs."""
    path = PLANS / f"plan-{plan_id}.json"
    result = _solved(plan_id)
    written = submit(result, tmp_path)
    pid = result.sidecar.plan_id
    assert tmp_path / f"{pid}.json" in written
    assert tmp_path / f"{pid}.sidecar.json" in written
    sub = _submission(tmp_path, pid)
    side = _sidecar(tmp_path, pid)
    violations = check_rules(load_plan(path, C), sub, C)
    assert not [v for v in violations if v.severity == "error"], violations
    assert side.status == ("submitted_strict_only" if violations else "ok")
    assert side.violations == violations
    assert side.bounds.k_found == len(sub.hubs) == EXPECTED[plan_id]["min_hubs5"]
    assert side.bounds.k_lb == EXPECTED[plan_id]["lb5"]
    lb_met = side.bounds.k_found == side.bounds.k_lb
    assert side.bounds.proof == ("lb_met" if lb_met else "exhaustive")
    assert side.cost.H == len(sub.hubs)
    assert sub.reported_metrics.hub_count == len(sub.hubs)
    assert sub.reported_metrics.total_length_m == pytest.approx(side.cost.L)


@pytest.mark.parametrize("plan_id", ["5924", "13344"])
def test_the_writer_follows_slide_8(plan_id):
    """C48: assigned_rooms starts with the hub's room, nodes sorted, segments in ring order."""
    result = _solved(plan_id)
    for hub in result.submission.hubs:
        assert hub.assigned_rooms[0] == hub.room_id
        assert len(set(hub.assigned_rooms)) == len(hub.assigned_rooms)
        assert hub.assigned_nodes == sorted(hub.assigned_nodes)
        assert hub.closed_sequence[0] == hub.closed_sequence[-1] == hub.hub_id
        assert sorted(hub.closed_sequence[1:-1]) == hub.assigned_nodes
        assert [(s.from_, s.to) for s in hub.route_segments] == list(pairwise(hub.closed_sequence))
        assert hub.ring_length_m == pytest.approx(sum(s.length_m for s in hub.route_segments))


def test_solve_is_deterministic():
    a = _solved("13344")
    b = solve_file(PLANS / "plan-13344.json", C)  # a second, fresh run
    assert a.submission == b.submission and a.sidecar == b.sidecar


# --------------------------------------------------------------------------- the gate (C43)


def _move_first_hub(xy):
    def hook(hubs):
        return [dataclasses.replace(hubs[0], location=xy), *hubs[1:]]

    return hook


def _point_near_a_wall(plan, room_id: str, gap_m: float) -> tuple[float, float]:
    """A point of `room_id`, `gap_m` from the nearest wall, well inside the room otherwise."""
    from hackhive.zoning import wall_geometry

    room = next(r for r in plan.rooms if r.id == room_id)
    walls = wall_geometry(plan)
    band = walls.buffer(gap_m + 0.005).difference(walls.buffer(gap_m - 0.005))
    spots = room.polygon.buffer(-0.05).intersection(band)
    assert not spots.is_empty, room_id
    p = spots.representative_point()
    return (p.x, p.y)


def test_a_hub_in_a_wall_writes_no_submission(tmp_path):
    """The forced rule failure: a hook moves H1 into a wall; no submission, rule 6 named."""
    plan = load_plan(PLANS / "plan-5088.json", C)
    wall = max(plan.walls, key=lambda w: w.area).representative_point()
    result = solve(plan, C, hub_hook=_move_first_hub((wall.x, wall.y)))
    written = submit(result, tmp_path)
    pid = result.sidecar.plan_id
    assert not (tmp_path / f"{pid}.json").exists()
    assert written == [tmp_path / f"{pid}.sidecar.json"]
    side = _sidecar(tmp_path, pid)
    assert side.status == "infeasible"
    assert any(v.rule == 6 and v.severity == "error" for v in side.violations)
    assert "6" in side.reason


def test_a_hub_too_close_to_a_wall_writes_no_submission(tmp_path):
    """A design with an `error` (rule 6: 0.3 m from a wall) and no fallback: infeasible."""
    plan = load_plan(PLANS / "plan-5088.json", C)
    first = solve(plan, C)
    room = first.submission.hubs[0].room_id
    xy = _point_near_a_wall(load_plan(PLANS / "plan-5088.json", C), room, 0.30)
    result = solve(load_plan(PLANS / "plan-5088.json", C), C, hub_hook=_move_first_hub(xy))
    submit(result, tmp_path)
    pid = result.sidecar.plan_id
    assert not (tmp_path / f"{pid}.json").exists()
    side = _sidecar(tmp_path, pid)
    assert side.status == "infeasible"
    errors = {v.rule for v in side.violations if v.severity == "error"}
    assert 6 in errors
    assert "rule 6" in side.reason


def test_a_strict_only_design_is_submitted_and_flagged(tmp_path):
    """Hub ≥ 0.5 m from walls but 0.45 m from the open passage's edge: strict only (#46 Q3)."""
    path = SOLUTIONS / "syn-open-plan.json"
    plan = load_plan(path, C)
    first = solve(plan, C)
    hub_room = first.submission.hubs[0].room_id
    room = next(r for r in load_plan(path, C).rooms if r.id == hub_room)
    x0, _, x1, _ = room.polygon.bounds
    x = 5.0 - 0.45 if x1 <= 5.0 + 1e-6 else 5.0 + 0.45  # the passage is the line x = 5 m
    result = solve(load_plan(path, C), C, hub_hook=_move_first_hub((x, 5.0)))
    submit(result, tmp_path)
    pid = result.sidecar.plan_id
    side = _sidecar(tmp_path, pid)
    assert (tmp_path / f"{pid}.json").exists()
    assert side.status == "submitted_strict_only"
    assert not [v for v in side.violations if v.severity == "error"]
    assert any(v.rule == 6 and v.severity == "strict" for v in side.violations)


def _raw_with_a_120_m2_room() -> dict:
    """1 unit = 0.1 m: a 12 × 10 m bedroom and a small kitchen behind a door."""
    return {
        "id": "BIG",
        "area": 150.0,
        "wall_depth": 2.0,
        "inner": mapping(box(0, 0, 150, 100)),
        "bedroom": mapping(box(0, 0, 120, 100)),
        "kitchen": mapping(box(122, 0, 150, 100)),
        "wall": mapping(MultiPolygon([box(120, 0, 122, 40), box(120, 50, 122, 100)])),
        "door": mapping(box(120, 40, 122, 50)),
    }


def test_a_room_over_the_area_cap_writes_no_submission(tmp_path):
    """C27: a 120 m² room can't be served by any hub; the sidecar names it."""
    plan = load_plan(_raw_with_a_120_m2_room(), C)
    big = next(r for r in plan.rooms if r.type == "bedroom")
    assert big.area_m2 == pytest.approx(120.0)
    result = solve(plan, C)
    written = submit(result, tmp_path)
    pid = result.sidecar.plan_id
    assert written == [tmp_path / f"{pid}.sidecar.json"]
    side = _sidecar(tmp_path, pid)
    assert side.status == "infeasible"
    assert big.id in side.reason and "120" in side.reason
    assert result.submission is None


def test_an_unreadable_plan_is_an_input_error(tmp_path):
    """#38: an InputFormatError ends as sidecar `input_error`, never a crash or a submission."""
    bad = tmp_path / "plan-bad.json"
    bad.write_text('{"hello": 1}', encoding="utf-8")
    result = solve_file(bad, C)
    assert isinstance(result, Result) and result.submission is None
    written = submit(result, tmp_path / "out")
    assert [p.name for p in written] == ["plan-bad.sidecar.json"]
    side = _sidecar(tmp_path / "out", "plan-bad")
    assert side.status == "input_error"
    assert side.reason.startswith("input_error: ")


def test_the_sidecar_carries_bounds_cost_hubs_and_provenance():
    path = PLANS / "plan-5924.json"
    result = solve_file(path, C)
    side, sub = result.sidecar, result.submission
    plan = load_plan(path, C)
    area = sum(r.area_m2 for r in plan.rooms if r.eligible)
    assert side.bounds.k_lb == lower_bound(area, len(plan.nodes), C)
    assert [h.hub_id for h in side.hubs] == [h.hub_id for h in sub.hubs]
    assert sum(h.n_nodes for h in side.hubs) == len(plan.nodes)
    assert sum(h.area_m2 for h in side.hubs) == pytest.approx(area)
    assert side.cost.J == pytest.approx(
        side.cost.L + 5 * side.cost.X + side.cost.O + 0.5 * side.cost.B
    )
    assert side.provenance.input_path == str(path)
    assert side.provenance.input_digest and side.provenance.version
    assert side.settings == C


# --------------------------------------------------------------------------- CLI


def test_cli_solve_writes_submission_sidecar_and_png(tmp_path, capsys):
    """The issue's See it: hackhive solve plan-5924 --out dir."""
    code = cli.main(["solve", str(PLANS / "plan-5924.json"), "--out", str(tmp_path)])
    assert code == 0, capsys.readouterr().err
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["PLAN_5924.json", "PLAN_5924.png", "PLAN_5924.sidecar.json"]
    assert (tmp_path / "PLAN_5924.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "PLAN_5924" in capsys.readouterr().out


def test_cli_solve_exits_1_without_a_submission(tmp_path):
    raw = tmp_path / "big.json"
    raw.write_text(json.dumps(_raw_with_a_120_m2_room()), encoding="utf-8")
    assert cli.main(["solve", str(raw), "--out", str(tmp_path / "out")]) == 1
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "PLAN_BIG.png",
        "PLAN_BIG.sidecar.json",
    ]


def test_cli_check_prints_the_checklist_and_j(capsys):
    code = cli.main(["check", str(PLANS / "plan-5088.json"), str(SOLUTIONS / "5088-valid.json")])
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("✓") >= 5 and "J 30.331" in out and "valid" in out


def test_cli_check_exits_1_on_an_error(capsys):
    """C26, C39."""
    code = cli.main(["check", str(PLANS / "plan-5088.json"), str(SOLUTIONS / "5088-diagonal.json")])
    assert code == 1
    assert "invalid" in capsys.readouterr().out


def test_cli_check_strict_only_exits_0_with_strict_lines(capsys):
    """C26, C43: strict-only exits 0 and prints `STRICT (rule n, #46 Qm):`; 4 on request."""
    args = ["check", str(PLANS / "plan-5088.json"), str(SOLUTIONS / "5088-retrace-two-door.json")]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    strict = [line for line in out.splitlines() if line.startswith("STRICT ")]
    assert strict and all(line.startswith("STRICT (rule 4, #46 Q5): ") for line in strict)
    assert cli.main([*args, "--fail-on-strict"]) == 4  # not 2: argparse's usage error (#72)


def test_cli_check_on_an_unreadable_submission_exits_3(tmp_path, capsys):
    bad = tmp_path / "sub.json"
    bad.write_text("{}", encoding="utf-8")
    assert cli.main(["check", str(PLANS / "plan-5088.json"), str(bad)]) == 3
    assert "ValidationError" in capsys.readouterr().err


def test_cli_without_a_command_prints_help(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2


def test_hub_hook_point_helper_is_sane():
    """The 0.3 m helper really lands 0.3 m from a wall, inside the room."""
    from hackhive.zoning import wall_geometry

    plan = load_plan(PLANS / "plan-5088.json", C)
    room = next(r for r in plan.rooms if r.eligible)
    xy = _point_near_a_wall(plan, room.id, 0.30)
    assert wall_geometry(plan).distance(Point(xy)) == pytest.approx(0.30, abs=0.01)
    assert room.polygon.contains(Point(xy))


# --------------------------------------------------------------------------- #72 review


@pytest.mark.parametrize(
    ("plan_id", "stem"),
    [
        ("../escaped", "_escaped"),
        ("A/B", "A_B"),
        ("A:B", "A_B"),
        ("..", "plan"),
        ("PLAN 7", "PLAN_7"),
    ],
)
def test_an_unsafe_plan_id_never_leaves_the_output_folder(plan_id, stem, tmp_path):
    """An organiser id is kept verbatim in the submission, but its file name is made safe (a
    path separator, `..` or `:` would write elsewhere or crash on Windows); the sidecar warns."""
    raw = json.loads((FIXTURES / "organiser" / "guess-5924.json").read_text(encoding="utf-8"))
    raw["plan_id"] = plan_id
    src = tmp_path / "in" / "plan.json"
    src.parent.mkdir()
    src.write_text(json.dumps(raw), encoding="utf-8")
    out = tmp_path / "out"
    assert cli.main(["solve", str(src), "--out", str(out)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in", "out"]
    assert sorted(p.name for p in out.iterdir()) == [
        f"{stem}.json",
        f"{stem}.png",
        f"{stem}.sidecar.json",
    ]
    sub = Submission.model_validate_json((out / f"{stem}.json").read_text("utf-8"))
    side = Sidecar.model_validate_json((out / f"{stem}.sidecar.json").read_text("utf-8"))
    assert sub.plan_id == side.plan_id == plan_id
    assert any("file name" in w and stem in w for w in side.warnings)


def test_an_unsafe_plan_id_on_an_unreadable_plan_is_filed_safely(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"plan_id": "../../x", "rooms": "nope"}), encoding="utf-8")
    written = submit(solve_file(bad, C), tmp_path / "out")
    assert [p.parent for p in written] == [tmp_path / "out"]
    assert [p.name for p in written] == ["_.._x.sidecar.json"]
    side = Sidecar.model_validate_json(written[0].read_text("utf-8"))
    assert side.plan_id == "../../x" and side.status == "input_error"


def test_cli_check_strict_only_under_fail_on_strict_exits_4_not_argparse_2(capsys):
    """#72 review: argparse exits 2 on a usage error, so strict-only must not share it."""
    args = ["check", str(PLANS / "plan-5088.json"), str(SOLUTIONS / "5088-retrace-two-door.json")]
    assert cli.main([*args, "--fail-on-strict"]) == 4
    with pytest.raises(SystemExit) as e:
        cli.main([*args, "--fail-on-strcit"])
    assert e.value.code == 2


def test_a_ring_that_cant_be_ordered_ends_in_the_sidecar(monkeypatch):
    """#72 review: `order_ring` raises ValueError past 8 nodes (reachable with max_nodes raised
    toward the ≤ 20 what-if); solve() reports it instead of raising. Every ring is ordered there:
    best_ring's (#22's ranking) and the walking skeleton's first_ring."""
    import hackhive.rings as rings_mod

    def too_many(*args, **kwargs):
        raise ValueError("9 nodes > 8: use Held-Karp (#28)")

    monkeypatch.setattr(rings_mod, "order_ring", too_many)
    # inline: a monkeypatch doesn't reach worker processes
    result = solve(load_plan(PLANS / "plan-5088.json", C), C, workers=1)
    assert result.sidecar.status == "infeasible" and result.submission is None
    assert "Held-Karp" in result.sidecar.reason


def test_a_grid_failure_is_not_reported_as_routing(monkeypatch):
    import hackhive.solve as solve_mod

    def broken(*args, **kwargs):
        raise ValueError("door_3 touches no room")

    monkeypatch.setattr(solve_mod.LabelGrid, "from_plan", broken)
    result = solve(load_plan(PLANS / "plan-5088.json", C), C)
    assert result.sidecar.status == "infeasible"
    assert result.sidecar.reason.startswith("grid failed: ") and "door_3" in result.sidecar.reason


# --------------------------------------------------------------------------- #23 the gate's edge cases


def _raw(inner, **rooms) -> dict:
    """A raw plan in drawing units (1 unit = 0.1 m, 0.2 m walls); geometries as given."""
    raw = {"id": "SYN23", "area": inner.area * 0.01, "wall_depth": 2.0, "inner": mapping(inner)}
    raw.update({k: mapping(g) for k, g in rooms.items()})
    return raw


def _no_submission(result, tmp_path) -> Sidecar:
    written = submit(result, tmp_path)
    pid = result.sidecar.plan_id
    assert written == [tmp_path / f"{pid}.sidecar.json"], "no submission may be written"
    side = _sidecar(tmp_path, pid)
    assert side.status == "infeasible" and result.submission is None
    return side


def test_infeasible_without_a_hub_capable_room(tmp_path):
    """C27, rule 6: a 0.8 m-wide bedroom has no spot 0.5 m from its walls, so no hub can go in
    it and no zone can serve it: no submission, and the sidecar names the room."""
    plan = load_plan(_raw(box(0, 0, 8, 40), bedroom=box(0, 0, 8, 40)), C)
    side = _no_submission(solve(plan, C), tmp_path)
    assert "hub-capable" in side.reason and plan.rooms[0].id in side.reason


def test_infeasible_when_a_component_is_too_large_for_exact_zoning(tmp_path):
    """D8, #23 spec: a connected group of more than 16 rooms gets no submission and a "too
    large" sidecar (the greedy fallback, #49, is in the Backlog: 0 of 16,999 ResPlan plans
    need it). #23's last criterion predates that decision."""
    n = 17
    living = box(0, 0, 10 * n, 20)
    bedrooms = MultiPolygon([box(10 * i + 1, 22, 10 * i + 9, 60) for i in range(n)])
    doors = MultiPolygon([box(10 * i + 3, 20, 10 * i + 7, 22) for i in range(n)])
    walls = MultiPolygon(
        [box(10 * i + s, 20, 10 * i + e, 22) for i in range(n) for s, e in ((0, 3), (7, 10))]
        + [box(10 * i + 9, 22, 10 * i + 11, 60) for i in range(n)]
    )
    raw = _raw(box(0, 0, 10 * n, 60), living=living, bedroom=bedrooms, door=doors, wall=walls)
    plan = load_plan(raw, C)
    side = _no_submission(solve(plan, C), tmp_path)
    assert "too large" in side.reason and "Backlog" in side.reason and "#49" in side.reason


def test_reported_values_that_disagree_are_a_warning_not_an_error(tmp_path):
    """C42: the organisers recompute every length, so a tampered `length_m` (or ring or total
    length) breaks no rule; reading the written submission back flags it in the sidecar."""
    plan = load_plan(PLANS / "plan-5088.json", C)
    result = solve(plan, C)
    sub = result.submission.model_copy(deep=True)
    seg = sub.hubs[0].route_segments[0]
    seg.length_m += 1.0
    sub.reported_metrics.total_length_m += 2.0
    assert not [v for v in check_rules(plan, sub, C) if v.severity == "error"]
    tampered = Result(result.plan, sub, result.sidecar)
    written = submit(tampered, tmp_path)
    assert tmp_path / f"{plan.plan_id}.json" in written  # still submitted
    warnings = _sidecar(tmp_path, plan.plan_id).warnings
    reported = [w for w in warnings if w.startswith("reported ")]
    assert any(f"{seg.from_}→{seg.to}" in w and "length_m" in w for w in reported)
    assert any("total_length_m" in w for w in reported)


def test_reported_values_written_by_solve_agree(tmp_path):
    """The writer fills every reported value from the mirror, so read back they all agree."""
    result = solve(load_plan(PLANS / "plan-5924.json", C), C)
    submit(result, tmp_path)
    warnings = _sidecar(tmp_path, result.sidecar.plan_id).warnings
    assert not [w for w in warnings if w.startswith("reported ")]


def test_cli_check_warns_about_reported_values(tmp_path, capsys):
    """`hackhive check` prints the same read-back warnings (exit code unchanged)."""
    sub = json.loads((SOLUTIONS / "5088-valid.json").read_text("utf-8"))
    sub["hubs"][0]["ring_length_m"] += 5.0
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(sub), encoding="utf-8")
    assert cli.main(["check", str(PLANS / "plan-5088.json"), str(path)]) == 0
    out = capsys.readouterr().out
    assert "warning: reported H1 ring_length_m" in out


def test_warnings_list_a_folded_sliver_in_the_sidecar(tmp_path):
    """C38: a folded living sliver is listed in `folded_parts` and named in the warnings."""
    living = MultiPolygon([box(0, 0, 50, 50), box(50, 0, 53, 30)])
    plan = load_plan(_raw(box(0, 0, 100, 100), living=living), C)
    assert plan.folded_parts
    result = solve(plan, C)
    submit(result, tmp_path)
    side = _sidecar(tmp_path, result.sidecar.plan_id)
    (fold,) = side.folded_parts
    assert (fold.room_id, fold.into_room_id) == ("living_1", "R01")
    assert any("living_1" in w and "folded" in w and "R01" in w for w in side.warnings)


def test_warnings_carry_a_low_clearance_node_even_without_a_design(tmp_path):
    """A2: a node under 0.5 m from the walls is a sidecar warning, design or not."""
    plan = load_plan(_raw(box(0, 0, 8, 40), bedroom=box(0, 0, 8, 40)), C)
    assert any("clearance" in w for w in plan.warnings)
    side = _no_submission(solve(plan, C), tmp_path)
    assert any("clearance" in w and "< 0.5 m" in w for w in side.warnings)


# --------------------------------------------------------------------------- #22 rank every arrangement


@pytest.fixture(scope="module")
def ranked_13344():
    """Every arrangement of the node-cap fixture (57 zonings, 63 zones), built once: the slowest
    enumerate here (`solve()` ranks one hub count at a time instead)."""
    return solve_mod.enumerate(load_plan(PLANS / "plan-13344.json", C), C)


def test_enumerate_13344_lists_every_zoning(ranked_13344):
    """C29: one arrangement per feasible zoning."""
    assert len(ranked_13344) == EXPECTED["13344"]["n_all5"] == 57
    assert len({a.signature for a in ranked_13344}) == 57


def test_enumerate_is_sorted_by_hubs_j_area_gap_signature(ranked_13344):
    """C16, C47: (H, J, area gap, signature), 3-hub rows first; J is the worse reading's (D3)."""
    keys = [solve_mod.rank_key(a) for a in ranked_13344]
    assert keys == sorted(keys)
    for a, key in zip(ranked_13344, keys, strict=True):
        assert key == (a.H, round(a.J, 6), a.area_gap, a.signature)
        assert a.H == len(a.hubs) == len(a.rings) == len(a.zoning)
        assert a.J == max(a.J_by_reading.values())
        assert set(a.J_by_reading) == {"strict", "lenient"}
    assert ranked_13344[0].H == EXPECTED["13344"]["min_hubs5"] == 3


def test_enumerate_is_deterministic():
    """C16: two runs give the same order and the same numbers, inline or over worker processes."""
    a = solve_mod.enumerate(load_plan(PLANS / "plan-5924.json", C), C, workers=1)
    b = solve_mod.enumerate(load_plan(PLANS / "plan-5924.json", C), C, workers=2)
    assert [solve_mod.rank_key(x) for x in a] == [solve_mod.rank_key(x) for x in b]
    assert [(x.hubs, x.rings) for x in a] == [(x.hubs, x.rings) for x in b]


def test_enumerate_k_keeps_only_that_hub_count():
    plan = load_plan(PLANS / "plan-5088.json", C)
    every = solve_mod.enumerate(plan, C)
    two = solve_mod.enumerate(plan, C, k=2)
    assert {a.H for a in two} == {2}
    assert [a.signature for a in two] == [a.signature for a in every if a.H == 2]


@pytest.mark.parametrize("plan_id", ["5088", "5924"])
def test_solve_submits_enumerate_first_fully_routed(plan_id):
    """C7, C16: solve() is enumerate()[0] after the rip-up passes (`route`), passing every rule,
    with k_lb, k_found and proof right."""
    plan = load_plan(PLANS / f"plan-{plan_id}.json", C)
    top = solve_mod.enumerate(plan, C)[0]
    result = solve(load_plan(PLANS / f"plan-{plan_id}.json", C), C)
    side, sub = result.sidecar, result.submission
    assert [(h.hub_id, h.room_id, (h.location.x, h.location.y)) for h in sub.hubs] == [
        (h.hub_id, h.room_id, h.location) for h in top.hubs
    ]
    assert [h.closed_sequence for h in sub.hubs] == [h.ring for h in top.hubs]
    assert [h.assigned_rooms for h in sub.hubs] == [h.assigned_rooms for h in top.hubs]
    routed = solve_mod.route(top, plan, C)
    assert [h.route_segments for h in sub.hubs] == [r.segments for r in routed.rings]
    assert check_rules(plan, sub, C) == [] and side.status == "ok"
    assert (side.arrangement.rank, side.arrangement.signature) == (1, top.signature)
    assert side.arrangement.skipped == []
    assert side.bounds.k_lb == EXPECTED[plan_id]["lb5"]
    assert side.bounds.k_found == top.H
    assert side.bounds.proof == ("lb_met" if top.H == side.bounds.k_lb else "exhaustive")


def _arrangement(H: int, strict: float, lenient: float, gap: float, signature: str):
    hubs = [Hub(f"H{i}", "r", (0.0, 0.0), [f"H{i}", "N1", f"H{i}"], ["r"]) for i in range(H)]
    return solve_mod.Arrangement(
        zoning=tuple(frozenset({f"r{i}"}) for i in range(H)),
        hubs=hubs,
        rings=[[] for _ in range(H)],
        J_by_reading={"strict": strict, "lenient": lenient},
        area_gap=gap,
        signature=signature,
    )


def test_rank_key_breaks_equal_hubs_and_j_on_the_smaller_area_gap():
    """C47: equal H and J (to 1e-6 m), so the smaller max − min assigned area ranks first."""
    wide = _arrangement(2, 30.0, 28.0, gap=12.0, signature="a")
    even = _arrangement(2, 30.0 + 1e-9, 28.0, gap=3.0, signature="b")
    assert sorted([wide, even], key=solve_mod.rank_key) == [even, wide]


def test_rank_key_uses_the_worse_counting_reading():
    """C17, C40, D3: A is lower under the lenient reading but higher in its worse case: B first."""
    a = _arrangement(2, strict=12.0, lenient=8.0, gap=0.0, signature="a")
    b = _arrangement(2, strict=11.0, lenient=10.0, gap=0.0, signature="b")
    assert (a.J, b.J) == (12.0, 11.0)
    assert sorted([a, b], key=solve_mod.rank_key) == [b, a]


def test_rank_key_puts_fewer_hubs_first_whatever_j():
    """C16: a 3-hub design beats any 4-hub one, even with a higher J."""
    three = _arrangement(3, 90.0, 90.0, gap=0.0, signature="b")
    four = _arrangement(4, 40.0, 40.0, gap=0.0, signature="a")
    assert sorted([four, three], key=solve_mod.rank_key) == [three, four]


def _first_call_only(hook):
    """`hook` on the best-ranked arrangement only: solve() calls the hub hook once per candidate,
    in rank order."""
    calls = []

    def once(hubs):
        calls.append(None)
        return hook(hubs) if len(calls) == 1 else hubs

    return once


def test_a_best_arrangement_that_fails_rule_6_falls_back_to_the_next(tmp_path):
    """C43: the hook puts the best arrangement's H1 in a wall; the next one is clean, submitted,
    and the sidecar says which and why."""
    plan = load_plan(PLANS / "plan-5088.json", C)
    ranked = solve_mod.enumerate(plan, C)
    wall = max(plan.walls, key=lambda w: w.area).representative_point()
    hook = _first_call_only(_move_first_hub((wall.x, wall.y)))
    result = solve(load_plan(PLANS / "plan-5088.json", C), C, hub_hook=hook)
    written = submit(result, tmp_path)
    side = _sidecar(tmp_path, result.sidecar.plan_id)
    assert tmp_path / f"{side.plan_id}.json" in written
    assert side.status == "ok" and side.violations == []
    assert (side.arrangement.rank, side.arrangement.signature) == (2, ranked[1].signature)
    (skipped,) = side.arrangement.skipped
    assert skipped.startswith(f"#1 {ranked[0].signature}") and "rule 6" in skipped
    assert [h.closed_sequence for h in result.submission.hubs] == [h.ring for h in ranked[1].hubs]


def test_a_best_arrangement_with_an_error_falls_back_to_a_clean_one(tmp_path):
    """C43: the best arrangement routes but breaks rule 6 (H1 0.3 m from a wall, an `error`), the
    next is clean: the clean one is submitted."""
    plan = load_plan(PLANS / "plan-5088.json", C)
    ranked = solve_mod.enumerate(plan, C)
    xy = _point_near_a_wall(plan, ranked[0].hubs[0].room_id, 0.30)
    hook = _first_call_only(_move_first_hub(xy))
    result = solve(load_plan(PLANS / "plan-5088.json", C), C, hub_hook=hook)
    submit(result, tmp_path)
    side = _sidecar(tmp_path, result.sidecar.plan_id)
    assert (tmp_path / f"{side.plan_id}.json").exists()
    assert side.status == "ok" and side.violations == []
    assert side.arrangement.rank == 2
    assert "rule 6" in side.arrangement.skipped[0]


def test_cli_list_prints_the_ranked_table(capsys):
    """`python -m hackhive.solve --list <plan.json>`: a header and one row per arrangement."""
    path = PLANS / "plan-5088.json"
    ranked = solve_mod.enumerate(load_plan(path, C), C)
    assert solve_mod.main(["--list", str(path)]) == 0
    rows = capsys.readouterr().out.strip().splitlines()[1:]
    assert len(rows) == len(ranked) == EXPECTED["5088"]["n_all5"]
    for i, (row, a) in enumerate(zip(rows, ranked, strict=True), start=1):
        cells = row.split()
        assert cells[:3] == [str(i), str(a.H), f"{a.J:.3f}"]
        assert cells[-1] == a.signature


def _strict_for(hub_counts: set[int]):
    """A `check_rules` that adds a rule-4 `strict` flag to every design with one of these hub
    counts, so the gate's choice across hub counts is pinned without building such plans; and the
    hub counts it was called for, in order (one call per routed candidate)."""
    real = solve_mod.check_rules
    seen: list[int] = []

    def fake(plan, sub, c):
        seen.append(len(sub.hubs))
        out = real(plan, sub, c)
        if len(sub.hubs) in hub_counts:
            out = [*out, Violation(rule=4, severity="strict", message="test: a retrace")]
        return out

    return fake, seen


def test_a_clean_design_one_hub_count_up_beats_a_strict_only_one(monkeypatch):
    """C43 (Adi, 2026-10-16): 5088's 1-hub design is strict-only and its first 2-hub one clean, so
    the clean one is submitted; 2 hubs isn't then a proven minimum (#22 review)."""
    fake, seen = _strict_for({1})
    monkeypatch.setattr(solve_mod, "check_rules", fake)
    ranked = solve_mod.enumerate(load_plan(PLANS / "plan-5088.json", C), C)
    result = solve(load_plan(PLANS / "plan-5088.json", C), C)
    side = result.sidecar
    assert side.status == "ok" and len(result.submission.hubs) == 2
    assert (side.arrangement.rank, side.arrangement.signature) == (2, ranked[1].signature)
    assert side.arrangement.skipped == [f"#1 {ranked[0].signature}: strict only: rule 4"]
    assert (side.bounds.k_lb, side.bounds.k_found, side.bounds.proof) == (1, 2, "none")
    assert seen == [1, 2]


def test_the_gate_looks_one_hub_count_up_and_no_further(monkeypatch):
    """#22 review: every 1- and 2-hub design is strict-only, the 3-hub one clean. The clean search
    stops after 2 hubs, so the best strict-only one (1 hub) is submitted, and neither the 3-hub
    arrangement nor the walking skeleton is routed."""
    fake, seen = _strict_for({1, 2})
    monkeypatch.setattr(solve_mod, "check_rules", fake)
    result = solve(load_plan(PLANS / "plan-5088.json", C), C)
    side = result.sidecar
    assert side.status == "submitted_strict_only" and len(result.submission.hubs) == 1
    assert (side.arrangement.rank, side.arrangement.skipped) == (1, [])
    assert seen == [1, 2, 2]
    assert side.bounds.proof == "lb_met"


def test_the_walking_skeleton_is_submitted_when_every_ranked_design_fails():
    """C43: a hook puts H1 in a wall in each of 5088's 4 ranked arrangements, so the walking
    skeleton's first cut, tried last, is submitted, with no rank."""
    plan = load_plan(PLANS / "plan-5088.json", C)
    ranked = solve_mod.enumerate(plan, C)
    wall = max(plan.walls, key=lambda w: w.area).representative_point()
    in_wall = _move_first_hub((wall.x, wall.y))
    calls = []

    def hook(hubs):
        calls.append(None)
        return in_wall(hubs) if len(calls) <= len(ranked) else hubs

    result = solve(load_plan(PLANS / "plan-5088.json", C), C, hub_hook=hook)
    side = result.sidecar
    assert len(calls) == len(ranked) + 1 == 5
    assert side.status == "ok" and result.submittable
    assert (side.arrangement.rank, side.arrangement.signature) == (None, ranked[0].signature)
    assert [s.split(":")[0] for s in side.arrangement.skipped] == [
        f"#{i} {a.signature}" for i, a in enumerate(ranked, start=1)
    ]
    assert all("rule 6" in s for s in side.arrangement.skipped)
