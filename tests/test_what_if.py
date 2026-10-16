"""Acceptance tests for #30: what-if settings are labelled WHAT-IF and never written as a
submission (C29, C43). The app's side (expander, banner, reset) is in test_app_what_if.py."""

import json
from pathlib import Path

import pytest
from fixture_solves import solved
from shapely.geometry import MultiPolygon, box, mapping

from hackhive import cli
from hackhive.contracts import Constraints, Sidecar
from hackhive.io import load_plan
from hackhive.solve import solve, solve_file, submit
from hackhive.viz import WHAT_IF_GID, draw_solution, label_what_if
from hackhive.what_if import what_if_changes

PLANS = Path(__file__).resolve().parent / "fixtures" / "plans"
C = Constraints.problem_statement()


def _big() -> dict:
    """1 unit = 0.1 m: a 12 × 10 m bedroom (over the 100 m² cap) and a small kitchen."""
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


# --------------------------------------------------------------------------- what changed


def test_the_preset_changes_nothing():
    assert what_if_changes(C) == []


@pytest.mark.parametrize(
    ("change", "text"),
    [
        ({"max_area_m2": 400}, "area cap 400 m² (problem statement: 100 m²)"),
        ({"hub_wall_clearance_m": 0.3}, "hub to wall ≥ 0.3 m (problem statement: 0.5 m)"),
        ({"zone_links_via_excluded": True}, "zones linked through a bathroom or stair"),
        ({"route_through_other_rooms": True}, "cable through bathrooms or other hubs' rooms"),
        ({"interior_windows_as_doors": True}, "windows on interior walls as doors"),
    ],
)
def test_each_change_is_named(change, text):
    assert what_if_changes(Constraints(**change)) == [text]


def test_a_setting_without_a_name_is_still_named():
    """Anything else off the preset (a test-only setting) still shows, by its field name."""
    assert what_if_changes(Constraints(grid_m=0.05)) == ["grid_m = 0.05 (problem statement: 0.1)"]


# --------------------------------------------------------------------------- solve and submit


def test_a_raised_area_cap_solves_a_room_over_100_m2_but_writes_no_submission(tmp_path):
    """C27, C29: D4's plan with a 120 m² room is solved under a 150 m² cap, sidecar only."""
    plan = load_plan(_big())
    assert solve(plan, C).submission is None  # the preset can't serve the 120 m² room
    c = Constraints(max_area_m2=150)
    result = solve(plan, c)
    assert result.submission is not None and result.sidecar.status in (
        "ok",
        "submitted_strict_only",
    )
    assert not result.submittable
    written = submit(result, tmp_path)
    assert written == [tmp_path / "PLAN_BIG.sidecar.json"]
    raw = json.loads(written[0].read_text(encoding="utf-8"))
    assert raw["settings"]["is_problem_statement"] is False
    side = Sidecar.model_validate(raw)
    assert any(w.startswith("WHAT-IF") and "area cap 150 m²" in w for w in side.warnings)


@pytest.mark.parametrize(
    "change", [{"zone_links_via_excluded": True}, {"route_through_other_rooms": True}]
)
def test_any_what_if_writes_no_submission(change, tmp_path):
    result = solve_file(PLANS / "plan-5924.json", Constraints(**change))
    assert result.submission is not None  # the design exists, for the app to show
    assert submit(result, tmp_path) == [tmp_path / "PLAN_5924.sidecar.json"]


def test_the_preset_still_writes_its_submission(tmp_path):
    result = solved("5924")
    assert result.submittable
    assert tmp_path / "PLAN_5924.json" in submit(result, tmp_path)
    assert not [w for w in result.sidecar.warnings if w.startswith("WHAT-IF")]


# --------------------------------------------------------------------------- CLI


def test_cli_what_if_writes_the_sidecar_and_a_labelled_png_only(tmp_path, capsys):
    raw = tmp_path / "big.json"
    raw.write_text(json.dumps(_big()), encoding="utf-8")
    out = tmp_path / "out"
    code = cli.main(["solve", str(raw), "--out", str(out), "--max-area", "150"])
    printed = capsys.readouterr().out
    assert code == 0, printed  # a design under its what-if settings; nothing to submit, by design
    assert sorted(p.name for p in out.iterdir()) == ["PLAN_BIG.png", "PLAN_BIG.sidecar.json"]
    assert "WHAT-IF" in printed and "area cap 150 m²" in printed
    assert "no submission" in printed


@pytest.mark.parametrize(
    ("flag", "field"),
    [
        (["--hub-clearance", "0.3"], {"hub_wall_clearance_m": 0.3}),
        (["--zone-links-via-excluded"], {"zone_links_via_excluded": True}),
        (["--route-through-other-rooms"], {"route_through_other_rooms": True}),
    ],
)
def test_cli_flags_set_the_what_if(flag, field, tmp_path, capsys):
    code = cli.main(["solve", str(PLANS / "plan-5924.json"), "--out", str(tmp_path), *flag])
    assert code == 0, capsys.readouterr().out
    assert not (tmp_path / "PLAN_5924.json").exists()
    side = Sidecar.model_validate_json((tmp_path / "PLAN_5924.sidecar.json").read_text("utf-8"))
    assert side.settings == Constraints(**field)


def test_cli_preset_values_are_not_a_what_if(tmp_path, capsys):
    """Passing the problem statement's own values changes nothing: a normal submission."""
    args = ["--max-area", "100", "--hub-clearance", "0.5"]
    assert cli.main(["solve", str(PLANS / "plan-5924.json"), "--out", str(tmp_path), *args]) == 0
    assert (tmp_path / "PLAN_5924.json").exists()
    assert "WHAT-IF" not in capsys.readouterr().out


# --------------------------------------------------------------------------- the PNG label


def test_label_what_if_marks_the_figure():
    plan = load_plan(PLANS / "plan-5924.json")
    result = solve(plan, Constraints(max_area_m2=400))
    fig = draw_solution(plan, result.submission)
    label_what_if(fig, what_if_changes(result.sidecar.settings))
    texts = [t for t in fig.findobj() if getattr(t, "get_gid", lambda: None)() == WHAT_IF_GID]
    assert len(texts) == 1
    assert texts[0].get_text().startswith("WHAT-IF") and "area cap 400 m²" in texts[0].get_text()


def test_label_what_if_does_nothing_for_the_preset():
    result = solved("5924")
    fig = draw_solution(result.plan, result.submission)
    label_what_if(fig, [])
    assert not [t for t in fig.findobj() if getattr(t, "get_gid", lambda: None)() == WHAT_IF_GID]


# --------------------------------------------------------------------------- #31 interior windows


def test_interior_window_2814_one_hub_and_never_submitted(tmp_path):
    """#31, C37, C39, C43: strict 2814 needs 2 hubs and writes a valid submission; with interior
    windows as doors the kitchen joins living, 1 hub, WHAT-IF, and no submission is written."""
    strict = solved("2814")
    assert strict.submission is not None and len(strict.submission.hubs) == 2
    assert strict.submittable
    written = submit(strict, tmp_path / "strict")
    assert any(p.name == "PLAN_2814.json" for p in written)

    on = solve_file(PLANS / "plan-2814.json", Constraints(interior_windows_as_doors=True))
    assert on.submission is not None and len(on.submission.hubs) == 1
    assert on.sidecar.settings.is_problem_statement is False and on.what_if
    assert not [v for v in on.sidecar.violations if v.severity == "error"]
    assert "windows on interior walls as doors" in " ".join(on.sidecar.warnings)
    written = submit(on, tmp_path / "on")
    assert [p.name for p in written] == ["PLAN_2814.sidecar.json"]


def test_interior_window_route_on_a_plan_built_with_the_default_graph():
    """#116 review 1: `solve.route` (the gallery's Open, through `routed_submission`) builds the
    room graph for its own settings, so a plan whose graph was built with the default (as the
    app's `load()` hands it) still routes through the window."""
    from hackhive.checker import check_rules
    from hackhive.graph import build_graph
    from hackhive.solve import enumerate as enumerate_arrangements
    from hackhive.solve import routed_submission

    on = Constraints(interior_windows_as_doors=True)
    best = enumerate_arrangements(load_plan(PLANS / "plan-2814.json"), on)[0]
    assert best.H == 1
    fresh = load_plan(PLANS / "plan-2814.json")
    build_graph(fresh)
    sub = routed_submission(fresh, best, on)
    assert len(sub.hubs) == 1
    assert not [v for v in check_rules(fresh, sub, on) if v.severity == "error"]


def test_cli_interior_windows_flag(tmp_path, capsys):
    """#116 review nit: the CLI reaches this what-if too; 2814 gets one hub, no submission."""
    args = ["solve", str(PLANS / "plan-2814.json"), "--out", str(tmp_path)]
    assert cli.main([*args, "--interior-windows-as-doors"]) == 0, capsys.readouterr().out
    assert not (tmp_path / "PLAN_2814.json").exists()
    side = Sidecar.model_validate_json((tmp_path / "PLAN_2814.sidecar.json").read_text("utf-8"))
    assert side.settings == Constraints(interior_windows_as_doors=True)
    assert len(side.hubs) == 1
