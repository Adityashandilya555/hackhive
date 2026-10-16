"""Acceptance tests for #2: data contracts, the problem-statement preset and the schemas."""

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from hackhive.contracts import (
    ORGANISER_WEIGHTS,
    Constraints,
    CostWeights,
    Submission,
    lower_bound,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def slide8() -> dict:
    return json.loads((FIXTURES / "slide8_example.json").read_text())


def test_slide8_example_parses(slide8):
    sub = Submission.model_validate(slide8)
    hub = sub.hubs[0]
    assert sub.units == "m"
    assert isinstance(slide8["hubs"][0]["location"], dict)
    assert hub.location.x == slide8["hubs"][0]["location"]["x"]
    for seg in hub.route_segments:
        assert all(len(p) == 2 for p in seg.polyline)


def _paths_to_objects(obj, path=()):
    """Every path (tuple of keys/indices) that points at a JSON object."""
    if isinstance(obj, dict):
        yield path
        for k, v in obj.items():
            yield from _paths_to_objects(v, (*path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _paths_to_objects(v, (*path, i))


def test_extra_key_rejected_at_every_level(slide8):
    paths = list(_paths_to_objects(slide8))
    assert len(paths) >= 5  # root, hub, location, segment, reported_metrics
    for path in paths:
        bad = copy.deepcopy(slide8)
        target = bad
        for step in path:
            target = target[step]
        target["unexpected"] = 1
        with pytest.raises(ValidationError):
            Submission.model_validate(bad)


def test_problem_statement_preset():
    c = Constraints.problem_statement()
    w = c.cost_weights
    assert (w.length, w.cross, w.overlap, w.bend) == (1.0, 5.0, 1.0, 0.5)
    assert c.hub_wall_clearance_m == 0.5
    assert c.tolerance_m == 0.001
    assert c.max_area_m2 == 100
    assert c.max_nodes == 5
    assert c.is_problem_statement
    assert Constraints() == c


@pytest.mark.parametrize(
    "change",
    [
        {"max_area_m2": 120},
        {"max_nodes": 20},
        {"hub_wall_clearance_m": 0.4},
        {"tolerance_m": 0.01},
        {"grid_m": 0.05},
        {"flip_y": True},
        {"origin_shift": (1.0, 0.0)},
        {"interior_windows_as_doors": True},
        {"zone_links_via_excluded": True},
        {"route_through_other_rooms": True},
        {"cost_weights": CostWeights(1.0, 10.0, 1.0, 0.5)},
    ],
)
def test_any_change_leaves_the_preset(change):
    assert not Constraints(**change).is_problem_statement


def test_constraints_frozen_and_hashable():
    c = Constraints()
    assert hash(c) == hash(Constraints.problem_statement())
    with pytest.raises(ValidationError):
        c.max_nodes = 20


def test_settings_json_says_whether_it_is_the_problem_statement():
    """#30: the sidecar's `settings` carry `is_problem_statement`, true only for the preset."""
    assert json.loads(Constraints().model_dump_json())["is_problem_statement"] is True
    what_if = json.loads(Constraints(max_area_m2=400).model_dump_json())
    assert what_if["is_problem_statement"] is False


@pytest.mark.parametrize("c", [Constraints(), Constraints(hub_wall_clearance_m=0.3)])
def test_settings_json_reads_back(c):
    """A written sidecar reads back: the flag is output only, recomputed from the settings."""
    assert Constraints.model_validate_json(c.model_dump_json()) == c


def test_settings_json_with_a_false_flag_is_rejected():
    """A hand-edited flag that disagrees with the settings is an error, never trusted."""
    raw = json.loads(Constraints(max_area_m2=400).model_dump_json())
    raw["is_problem_statement"] = True
    with pytest.raises(ValidationError, match="is_problem_statement"):
        Constraints.model_validate(raw)


def test_sidecar_schema_lists_the_flag():
    schema = json.loads((ROOT / "schemas" / "sidecar.schema.json").read_text(encoding="utf-8"))
    assert "is_problem_statement" in schema["$defs"]["Constraints"]["properties"]


def test_zone_links_via_excluded_default_false():
    assert Constraints().zone_links_via_excluded is False
    assert not Constraints(zone_links_via_excluded=True).is_problem_statement


def test_lower_bound():
    assert lower_bound(150, 5, Constraints()) == 2
    assert lower_bound(91.5, 7, Constraints()) == 2
    assert lower_bound(91.5, 7, Constraints(max_nodes=20)) == 1


def test_schemas_up_to_date():
    r = subprocess.run(
        [sys.executable, "-m", "hackhive.contracts", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "schemas up to date" in r.stdout


def test_weights_defined_only_once():
    assert ORGANISER_WEIGHTS == CostWeights(1.0, 5.0, 1.0, 0.5)
    hits = []
    for f in (ROOT / "src").rglob("*.py"):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if (
                re.search(r"(?<![\d.])(5\.0|0\.5)(?![\d])", line)
                and "ORGANISER_WEIGHTS" not in line
            ):
                hits.append(f"{f.name}:{n}: {line.strip()}")
    # hub_wall_clearance_m is a clearance, not a weight; other deliberate non-weight
    # literals (e.g. half a grid cell) carry a "not-a-weight" marker on the same line
    hits = [h for h in hits if "clearance" not in h and "not-a-weight" not in h]
    assert not hits, hits


def test_fixtures_and_expected():
    expected = json.loads((FIXTURES / "expected.json").read_text())
    plans = sorted(p.stem.split("-")[1] for p in (FIXTURES / "plans").glob("plan-*.json"))
    assert sorted(expected) == plans
    assert len(plans) == 7
    assert expected["5924"]["elig_m2"] == pytest.approx(150.0, abs=0.1)
    assert expected["5924"]["lb5"] == 2
    assert expected["13344"]["lb5"] == 2 and expected["13344"]["lb20"] == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"max_area_m2": 0},
        {"max_area_m2": -10},
        {"max_nodes": 0},
        {"grid_m": 0},
        {"tolerance_m": 0},
        {"hub_wall_clearance_m": -0.1},
    ],
)
def test_invalid_limits_rejected(bad):
    with pytest.raises(ValidationError):
        Constraints(**bad)


def test_plan_keeps_obstacles():
    from shapely.geometry import box

    from hackhive.contracts import Plan

    plan = Plan(
        plan_id="PLAN_T",
        m_per_unit=0.05,
        wall_depth_m=0.2,
        inner=box(0, 0, 10, 10),
        rooms=[],
        walls=[box(0, 0, 10, 0.2)],
        windows=[box(2, 0, 3, 0.2)],
        front_doors=[box(5, 0, 6, 0.2)],
    )
    assert plan.walls and plan.windows and plan.front_doors
    with pytest.raises(ValidationError):
        Plan(plan_id="P", m_per_unit=1, wall_depth_m=0.2, inner=box(0, 0, 1, 1), rooms=[])


# --------------------------------------------------------------------------- review fixes (#53)


def test_schema_check_does_not_depend_on_the_locale_encoding():
    """Windows defaults to cp1252; the schemas contain non-ASCII, so every read/write is UTF-8."""
    r = subprocess.run(
        [
            sys.executable,
            "-X",
            "warn_default_encoding",
            "-W",
            "error::EncodingWarning",
            "-m",
            "hackhive.contracts",
            "--check",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_submission_round_trips_with_the_from_key(slide8):
    """A writer that forgets by_alias=True must still emit slide 8's "from", never "from_"."""
    sub = Submission.model_validate(slide8)
    assert json.loads(sub.model_dump_json()) == slide8
    assert sub.model_dump()["hubs"][0]["route_segments"][0]["from"] == "H1"


def test_violation_carries_hub_and_location_for_the_red_overlay():
    from hackhive.contracts import Violation

    v = Violation(
        rule=6, severity="error", message="hub 0.3 m from a wall", hub_id="H1", where=[(1.0, 2.0)]
    )
    assert v.hub_id == "H1" and v.where == [(1.0, 2.0)]
    assert Violation(rule=1, severity="strict", message="m").where == []


def test_bounds_proof_allows_lb_met():
    from hackhive.contracts import Bounds

    assert Bounds(k_lb=2, k_found=2, proof="lb_met").proof == "lb_met"


def test_sidecar_holds_per_hub_metrics():
    from hackhive.contracts import HubMetrics, Sidecar

    hm = HubMetrics(hub_id="H1", area_m2=71.0, n_nodes=3, L=23.8, X=0, O=0.0, B=8)
    sc = Sidecar(plan_id="P", status="ok", hubs=[hm])
    assert sc.hubs[0].B == 8
    assert Sidecar(plan_id="P", status="ok").hubs == []


@pytest.mark.parametrize(
    ("area", "nodes", "expected"),
    [
        (100.0000000001, 5, 1),  # float noise from summing polygon areas must not add a hub
        (sum([0.1] * 1000), 1, 1),
        (100.01, 5, 2),  # a real excess still needs another hub
        (200.0000000001, 1, 2),
        (0, 0, 0),
    ],
)
def test_lower_bound_ignores_float_noise(area, nodes, expected):
    assert lower_bound(area, nodes, Constraints()) == expected
