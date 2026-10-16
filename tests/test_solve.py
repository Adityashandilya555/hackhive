"""Acceptance tests for #16: `solve()`, the submit gate, the slide-8 writer and the CLI."""

import dataclasses
import json
from itertools import pairwise
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Point, box, mapping

from hackhive import cli
from hackhive.checker import check_rules
from hackhive.contracts import Constraints, Sidecar, Submission, lower_bound
from hackhive.io import load_plan
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


# --------------------------------------------------------------------------- the 7 fixtures


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_each_fixture_solves_to_a_valid_submission(plan_id, tmp_path):
    """C16, C22, C24, C39: a submission that passes the six rules (no `error`), at min hubs."""
    path = PLANS / f"plan-{plan_id}.json"
    result = solve_file(path, C)
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
    assert side.bounds.proof == "exhaustive"
    assert side.cost.H == len(sub.hubs)
    assert sub.reported_metrics.hub_count == len(sub.hubs)
    assert sub.reported_metrics.total_length_m == pytest.approx(side.cost.L)


@pytest.mark.parametrize("plan_id", ["5924", "13344"])
def test_the_writer_follows_slide_8(plan_id):
    """C48: assigned_rooms starts with the hub's room, nodes sorted, segments in ring order."""
    result = solve_file(PLANS / f"plan-{plan_id}.json", C)
    for hub in result.submission.hubs:
        assert hub.assigned_rooms[0] == hub.room_id
        assert len(set(hub.assigned_rooms)) == len(hub.assigned_rooms)
        assert hub.assigned_nodes == sorted(hub.assigned_nodes)
        assert hub.closed_sequence[0] == hub.closed_sequence[-1] == hub.hub_id
        assert sorted(hub.closed_sequence[1:-1]) == hub.assigned_nodes
        assert [(s.from_, s.to) for s in hub.route_segments] == list(pairwise(hub.closed_sequence))
        assert hub.ring_length_m == pytest.approx(sum(s.length_m for s in hub.route_segments))


def test_solve_is_deterministic():
    a = solve_file(PLANS / "plan-13344.json", C)
    b = solve_file(PLANS / "plan-13344.json", C)
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
    """C26, C43: strict-only exits 0 and prints `STRICT (rule n, #46 Qm):`; 2 on request."""
    args = ["check", str(PLANS / "plan-5088.json"), str(SOLUTIONS / "5088-retrace-two-door.json")]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    strict = [line for line in out.splitlines() if line.startswith("STRICT ")]
    assert strict and all(line.startswith("STRICT (rule 4, #46 Q5): ") for line in strict)
    assert cli.main([*args, "--fail-on-strict"]) == 2


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
