"""Acceptance tests for #11: the organisers' six hard rules (slide 3), the reviewer checklist
(slide 5) and the checker CLI. Fixtures: tests/fixtures/solutions/ (see its README)."""

import importlib.util
import json
import math
from pathlib import Path

import pytest

from hackhive import checker
from hackhive.checker import check_rules, checklist, metrics, report, verdict
from hackhive.checker import main as checker_main
from hackhive.contracts import Constraints, Submission, lower_bound
from hackhive.graph import build_graph
from hackhive.grid import LabelGrid
from hackhive.io import load_plan
from hackhive.route import path_to, search

FIXTURES = Path(__file__).parent / "fixtures"
SOL = FIXTURES / "solutions"
PLAN_IDS = list(json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8")))

BROKEN = [  # the issue's order and expected rules
    ("5088-omitted-room.json", 1),
    ("5088-duplicated-node.json", 1),
    ("syn-overcap-zone.json", 2),
    ("syn-entrance-zone.json", 3),
    ("5088-open-ring.json", 4),
    ("5088-repeated-node.json", 4),
    ("5088-ring-ends-at-other-hub.json", 4),
    ("5088-off-node-5cm.json", 4),
    ("5088-diagonal.json", 5),
    ("5088-wall-crossing.json", 5),
    ("5088-window-crossing.json", 5),
    ("5088-hub-0.3m-from-wall.json", 6),
    ("5088-hub-in-unassigned-room.json", 6),
    ("5088-duplicate-hub-id.json", 1),  # #68 review
]


def plan_path(name: str) -> Path:
    if name.startswith("syn-"):
        return SOL / f"{name.rsplit('-', 1)[0]}-plan.json"
    return FIXTURES / "plans" / "plan-5088.json"


def submission(name: str) -> Submission:
    return Submission.model_validate_json((SOL / name).read_text(encoding="utf-8"))


def run(name: str, sub: Submission | None = None):
    plan = load_plan(plan_path(name))
    return plan, check_rules(plan, sub or submission(name))


def lines_for(plan, sub, violations):
    c = Constraints()
    area = sum(r.area_m2 for r in plan.rooms if r.eligible)
    m = metrics(sub.hubs, c)
    return checklist(violations, m, lower_bound(area, len(plan.nodes), c), area, len(plan.nodes))


# --------------------------------------------------------------------------- acceptance criteria


def test_valid_5088_has_no_violations_and_five_ticks():
    """C26, C39."""
    plan, violations = run("5088-valid.json")
    assert violations == []
    lines = lines_for(plan, submission("5088-valid.json"), violations)
    assert len(lines) == 5 and all(line.startswith("✓") for line in lines)
    assert verdict(violations) == "valid"


def test_two_hub_5088_is_valid_but_above_the_lower_bound():
    plan, violations = run("5088-two-hubs-valid.json")
    assert violations == []
    lines = lines_for(plan, submission("5088-two-hubs-valid.json"), violations)
    assert all(line.startswith("✓") for line in lines[:4])
    assert lines[4].startswith("✗") and "2 hubs > lower bound 1" in lines[4]


@pytest.mark.parametrize(("name", "rule"), BROKEN)
def test_each_broken_variant_fails_exactly_its_rule(name, rule):
    """C1, C3, C5, C8, C10-C14, C18, C34, C39: the expected rule only, each with a `where`."""
    _, violations = run(name)
    assert {v.rule for v in violations if v.severity == "error"} == {rule}
    assert all(v.where for v in violations)


@pytest.mark.parametrize(("name", "rule"), BROKEN)
def test_broken_variants_are_errors(name, rule):
    """C39: every one of these is a certain slide break, so `error` (the issue's table)."""
    _, violations = run(name)
    assert violations and all(v.severity == "error" for v in violations)


def test_hub_point_rule_6():
    """C18: 0.3 m from a wall is an error; sharing a node's room (5088-valid: H1 and N1 in R01)
    at ≥ the clearance is fine."""
    _, violations = run("5088-hub-0.3m-from-wall.json")
    (v,) = [v for v in violations if v.rule == 6]
    assert v.severity == "error" and v.where == [(3.05, 6.83)]
    _, violations = run("5088-valid.json")
    assert not [v for v in violations if v.rule == 6]


def test_ring_with_a_missing_hub_reports_and_doesnt_raise():
    """C11."""
    sub = submission("5088-valid.json")
    sub.hubs[0].closed_sequence = ["N3", "N2", "N1", "N3"]
    _, violations = run("5088-valid.json", sub)
    assert [v.rule for v in violations if v.severity == "error"] == [4]


def test_single_door_retrace_is_no_violation():
    """C15: retracing out of the single-door kitchen is overlap, scored by #43."""
    _, violations = run("5088-retrace-single-door.json")
    assert violations == []


def _open_plan_hub(location):
    """A one-node ring around the living's node of syn-open-plan, with the hub at `location`."""
    plan = load_plan(SOL / "syn-open-plan.json")
    n1 = plan.nodes[0].xy
    x, y = location
    legs = [
        {"from": "H1", "to": "N1", "length_m": 0, "polyline": [location, (x, n1[1]), n1]},
        {"from": "N1", "to": "H1", "length_m": 0, "polyline": [n1, (n1[0], y), location]},
    ]
    sub = Submission.model_validate(
        {
            "plan_id": plan.plan_id,
            "units": "m",
            "hubs": [
                {
                    "hub_id": "H1",
                    "room_id": "R01",
                    "location": {"x": x, "y": y},
                    "assigned_rooms": ["R01"],
                    "assigned_nodes": ["N1"],
                    "closed_sequence": ["H1", "N1", "H1"],
                    "route_segments": legs,
                    "ring_length_m": 0,
                }
            ],
            "reported_metrics": {"hub_count": 1, "total_length_m": 0},
        }
    )
    return [v for v in check_rules(plan, sub) if v.rule == 6]


@pytest.mark.parametrize(
    ("location", "severity"),
    [
        ((4.55, 5.0), "strict"),  # 0.45 m from the open-passage edge, ≥ 0.5 m from walls
        ((4.6, 5.0), "strict"),  # 3.015 m from walls, 0.4 m from the open-passage edge
        ((0.4, 5.0), "error"),  # 0.4 m from the outside of inner
        ((4.4, 5.0), None),  # 0.6 m from the edge
    ],
)
def test_rule_6_error_versus_strict(location, severity):
    """C18, #46 Q3: walls (and the outside) give `error`; only the room edge, `strict`."""
    found = _open_plan_hub(location)
    assert [v.severity for v in found] == ([severity] if severity else [])


def test_retrace_where_the_only_other_door_is_a_dead_end_bathroom_is_legal():
    """2026-10-16 (Rohit): 5088's bedroom R02 has a door to the living room and one to a
    bathroom that leads nowhere else. That bathroom door is no second way out, so R02 counts as
    single-door and the 1 m retrace at N2 is legal (it was flagged before)."""
    _, violations = run("5088-retrace-dead-end-bathroom.json")
    assert violations == []


def test_one_node_ring_out_and_back_is_strict_until_q2a():
    """Q2(a) is open: an out-and-back H → N → H where N's room has two ways out (5088's living
    room: bedroom and kitchen doors) may be a branch, so it stays rule-4 strict, as ADR-005
    allows it only when forced (#97 review). Only this ring is submitted, so rule 1's errors
    are expected."""
    plan = load_plan(plan_path("5088-valid.json"))
    n1 = plan.nodes[0]
    hub = (n1.xy[0] + 1.0, n1.xy[1])
    sub = Submission.model_validate(
        {
            "plan_id": plan.plan_id,
            "units": "m",
            "hubs": [
                {
                    "hub_id": "H1",
                    "room_id": n1.room_id,
                    "location": {"x": hub[0], "y": hub[1]},
                    "assigned_rooms": [n1.room_id],
                    "assigned_nodes": [n1.id],
                    "closed_sequence": ["H1", n1.id, "H1"],
                    "route_segments": [
                        {"from": "H1", "to": n1.id, "length_m": 1.0, "polyline": [hub, n1.xy]},
                        {"from": n1.id, "to": "H1", "length_m": 1.0, "polyline": [n1.xy, hub]},
                    ],
                    "ring_length_m": 2.0,
                }
            ],
            "reported_metrics": {"hub_count": 1, "total_length_m": 2.0},
        }
    )
    found = check_rules(plan, sub)
    assert [(v.rule, v.severity) for v in found if v.rule == 4] == [(4, "strict")], found
    assert {v.rule for v in found} == {1, 4}


def test_two_door_retrace_is_strict_with_a_where():
    """C11, C15, #46 Q5: 1 m retraced at N1 inside the two-door living room (bedroom and
    kitchen doors; its third door, to a dead-end bathroom, doesn't count)."""
    _, violations = run("5088-retrace-two-door.json")
    (v,) = violations
    assert (v.rule, v.severity) == (4, "strict") and len(v.where) == 2
    assert math.dist(*v.where) == pytest.approx(1.0, abs=0.001)
    _, violations = run("5088-retrace-single-door.json")
    assert violations == []


# --------------------------------------------------------------------------- never raises, gating


def _valid_dict() -> dict:
    return json.loads((SOL / "5088-valid.json").read_text(encoding="utf-8"))


def _malformed() -> list[tuple[str, dict]]:
    cases = []

    def case(label, edit):
        d = _valid_dict()
        edit(d["hubs"][0])
        cases.append((label, d))

    case(
        "nan in a polyline",
        lambda h: h["route_segments"][0]["polyline"].__setitem__(1, [math.nan, 4.6]),
    )
    case("inf in the location", lambda h: h["location"].__setitem__("x", math.inf))
    case("negative length_m", lambda h: h["route_segments"][0].__setitem__("length_m", -5))
    case("no route segments", lambda h: h.__setitem__("route_segments", []))
    case("empty polyline", lambda h: h["route_segments"][1].__setitem__("polyline", []))
    case(
        "one-point polyline", lambda h: h["route_segments"][1].__setitem__("polyline", [[5.3, 4.4]])
    )
    case(
        "zero-length pieces",
        lambda h: h["route_segments"][2]["polyline"].insert(
            1, h["route_segments"][2]["polyline"][1]
        ),
    )
    case("unknown rooms", lambda h: h.update(room_id="R99", assigned_rooms=["R99", "R01"]))
    case("unknown node", lambda h: h.update(assigned_nodes=["N1", "N2", "N9"]))
    case("empty sequence", lambda h: h.__setitem__("closed_sequence", []))
    case("no hub in sequence", lambda h: h.__setitem__("closed_sequence", ["N3", "N2", "N1"]))
    case("segment off the sequence", lambda h: h["route_segments"][0].__setitem__("to", "N7"))
    return cases


@pytest.mark.parametrize(("label", "raw"), _malformed(), ids=[c[0] for c in _malformed()])
def test_never_raises_on_malformed_input(label, raw):
    """Anything that passes validation comes back as violations (reported lengths never read)."""
    sub = Submission.model_validate(raw)
    plan = load_plan(plan_path("5088-valid.json"))
    violations = check_rules(plan, sub)
    assert isinstance(violations, list)
    if label in ("negative length_m", "zero-length pieces"):  # neither breaks a rule
        assert violations == []
    else:
        assert violations and verdict(violations) == "invalid"


def test_open_ring_gates_rules_4_to_6():
    """No cascades: one rule-4 error, nothing from rules 5-6."""
    _, violations = run("5088-open-ring.json")
    assert [(v.rule, v.severity) for v in violations] == [(4, "error")]


def test_rule_3_builds_the_room_graph_once(monkeypatch):
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return build_graph(*args, **kwargs)

    monkeypatch.setattr(checker, "build_graph", counting)
    run("5088-two-hubs-valid.json")
    assert len(calls) == 1


def test_checklist_lines():
    """Line 5 states the bound; strict-only lines stay ✓ with a count; a hub-point violation adds
    a sixth line (#68 review), so an error never hides behind five ticks."""
    plan, violations = run("5088-valid.json")
    lines = lines_for(plan, submission("5088-valid.json"), violations)
    assert lines[4] == "✓ 1 hub = lower bound 1 (50.0 m², 3 nodes)"

    plan, violations = run("5088-retrace-two-door.json")
    lines = lines_for(plan, submission("5088-retrace-two-door.json"), violations)
    assert lines[0].startswith("✓") and lines[0].endswith("(strict: 1)")
    assert verdict(violations) == "valid (strict only)"

    plan, violations = run("5088-hub-0.3m-from-wall.json")
    lines = lines_for(plan, submission("5088-hub-0.3m-from-wall.json"), violations)
    assert len(lines) == 6 and all(line.startswith("✓") for line in lines[:5])
    assert lines[5].startswith("✗ each hub in an assigned eligible room")
    assert verdict(violations) == "invalid"

    no_metrics = checklist(violations, None, 1, 50.0, 3)
    assert no_metrics[4].startswith("✗") and "not computed" in no_metrics[4]


@pytest.mark.parametrize(("name", "rule"), BROKEN)
def test_no_error_hides_behind_the_checklist(name, rule):
    """#68 review: every invalid fixture shows at least one ✗ line."""
    plan, violations = run(name)
    assert any(line.startswith("✗") for line in lines_for(plan, submission(name), violations))


def test_valid_plans_keep_slide_5s_five_lines():
    for name in (
        "5088-valid.json",
        "5088-two-hubs-valid.json",
        "5088-hub-in-single-door-room.json",
    ):
        plan, violations = run(name)
        assert len(lines_for(plan, submission(name), violations)) == 5


def test_hub_ids_are_unique_and_not_node_ids():
    """#68 review: two hubs called H1 (5088-duplicate-hub-id, in BROKEN), or a hub called N1,
    make `closed_sequence` ambiguous: rule 1."""
    sub = submission("5088-two-hubs-valid.json")
    h2 = sub.hubs[1]
    h2.hub_id, h2.closed_sequence = "N1", ["N1", "N2", "N1"]
    h2.route_segments[0].from_, h2.route_segments[1].to = "N1", "N1"
    _, violations = run("5088-two-hubs-valid.json", sub)
    assert any(v.rule == 1 and v.severity == "error" and "node id" in v.message for v in violations)


def test_hub_in_a_single_door_room_may_leave_and_return_on_one_line():
    """#68 review, slide 3 rule 4: a hub sharing the single-door kitchen with N3 must leave and
    re-enter through its passage; that retrace (2.20 m here, between legs that aren't adjacent)
    is legal, like a node's."""
    _, violations = run("5088-hub-in-single-door-room.json")
    assert violations == []


def test_report_bundles_what_a_reviewer_sees():
    """#68 review: one call for the CLI and the app's reviewer panel (#12)."""
    plan = load_plan(plan_path("5088-hub-0.3m-from-wall.json"))
    sub = submission("5088-hub-0.3m-from-wall.json")
    r = report(plan, sub)
    assert r.violations == check_rules(plan, sub)
    assert r.lines == lines_for(plan, sub, r.violations) and r.verdict == "invalid"
    assert r.metrics is not None and r.metrics.H == 1 and r.lower_bound == 1

    bad = sub.model_copy(deep=True)
    bad.hubs[0].location.x = math.nan
    r = report(plan, bad)
    assert r.metrics is None and r.lines[4].startswith("✗") and r.verdict == "invalid"


# --------------------------------------------------------------------------- checker and router agree


def _routed_legs(plan):
    """Every ordered node pair routed by #10's raster + search, with L-stubs to the exact points."""
    build_graph(plan)
    grid = LabelGrid.from_plan(plan, plan.openings)
    snapped = {n.id: grid.snap(n.xy, n.room_id) for n in plan.nodes}
    for a in plan.nodes:
        cell_a, corners_a = snapped[a.id]
        result = search(grid.labels, grid.door_rooms, [cell_a], 0.0)
        for b in plan.nodes:
            if a.id == b.id:
                continue
            cell_b, corners_b = snapped[b.id]
            cells = path_to(result, cell_b)
            if cells:  # 576's living_1 and 2814's kitchen can't be reached from the rest
                yield a.id, b.id, [a.xy, corners_a[0], *map(grid.to_xy, cells), corners_b[1], b.xy]


# routable ordered node pairs per fixture: 148 legs in all
ROUTED_LEGS = {"5088": 6, "576": 42, "1293": 20, "2814": 6, "3086": 12, "5924": 20, "13344": 42}


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_rule_5_accepts_every_routed_leg(plan_id):
    """The checker accepts every door crossing the router makes (door_gap, #60 review)."""
    plan = load_plan(FIXTURES / "plans" / f"plan-{plan_id}.json")
    c = Constraints()
    build_graph(plan)  # fills plan.openings, which the free space needs (check_rules does this)
    space = checker._free_space(plan, c)
    legs = list(_routed_legs(plan))
    assert len(legs) == ROUTED_LEGS[plan_id]
    flagged = [
        (a, b, v.message)
        for a, b, pts in legs
        for v in checker._rule5_leg(pts, space, c, "H1", f"{a}→{b}")
    ]
    assert flagged == []


# --------------------------------------------------------------------------- CLI, fixture script


def test_cli(capsys):
    plan = str(FIXTURES / "plans" / "plan-5088.json")
    assert checker_main([plan, str(SOL / "5088-valid.json")]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "valid" and sum(line.startswith("✓") for line in out) == 5
    assert checker_main([plan, str(SOL / "5088-wall-crossing.json")]) == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "invalid" and any(line.startswith("rule 5 [error] H1:") for line in out)
    assert checker_main([plan, str(SOL / "missing.json")]) == 2


def test_fixture_script_reproduces_the_committed_files(tmp_path):
    spec = importlib.util.spec_from_file_location("make_fixtures", SOL / "make_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    written = module.write(tmp_path)
    committed = sorted(p.name for p in SOL.glob("*.json"))
    assert sorted(p.name for p in written) == committed
    for p in written:
        assert p.read_text(encoding="utf-8") == (SOL / p.name).read_text(encoding="utf-8"), p.name


# --------------------------------------------------------------------------- #31 interior windows

WINDOWS_ON = Constraints(interior_windows_as_doors=True)


def _2814_kitchen_leg(c: Constraints):
    """A leg from 2814's living node to its kitchen node, routed under `c`'s graph."""
    from hackhive.io import room_by_label

    plan = load_plan(FIXTURES / "plans" / "plan-2814.json")
    build_graph(plan, WINDOWS_ON)  # route through the window
    grid = LabelGrid.from_plan(plan, cell_m=c.grid_m)
    kitchen, living = (room_by_label(plan, x).id for x in ("kitchen_0", "living_0"))
    a = next(n for n in plan.nodes if n.room_id == living)
    b = next(n for n in plan.nodes if n.room_id == kitchen)
    (ca, (sa, _)), (cb, (_, sb)) = grid.snap(a.xy, a.room_id), grid.snap(b.xy, b.room_id)
    cells = path_to(search(grid.labels, grid.door_rooms, [ca], 0.0), cb)
    assert cells
    return plan, [a.xy, sa, *map(grid.to_xy, cells), sb, b.xy]


def test_interior_window_route_accepted_only_with_the_setting():
    """#31, C37, C14: rule 5 accepts a route through 2814's interior window only when the setting
    is on; the strict default rejects it as a window crossing."""
    plan, pts = _2814_kitchen_leg(WINDOWS_ON)
    build_graph(plan, WINDOWS_ON)
    assert (
        checker._rule5_leg(pts, checker._free_space(plan, WINDOWS_ON), WINDOWS_ON, "H1", "x") == []
    )
    c = Constraints()
    build_graph(plan, c)
    flagged = checker._rule5_leg(pts, checker._free_space(plan, c), c, "H1", "x")
    assert flagged and any("window" in v.message for v in flagged)


def test_interior_window_setting_still_rejects_exterior_windows():
    """#31, C14: with the setting on, the exterior-window crossing on 5088 stays a rule-5 error."""
    plan = load_plan(plan_path("5088-window-crossing.json"))
    violations = check_rules(plan, submission("5088-window-crossing.json"), WINDOWS_ON)
    assert any(v.rule == 5 and "window" in v.message for v in violations)


def test_interior_window_rule_3_zone_through_the_window():
    """#31: a zone of living + kitchen on 2814 is connected (rule 3) only with the setting on."""
    from hackhive.io import room_by_label

    plan = load_plan(FIXTURES / "plans" / "plan-2814.json")
    on = checker.RuleContext(plan, WINDOWS_ON).ctx
    pair = [room_by_label(plan, x).id for x in ("kitchen_0", "living_0")]
    assert on.eligible.subgraph(pair).number_of_edges() == 1
    off = checker.RuleContext(plan, Constraints()).ctx
    assert off.eligible.subgraph(pair).number_of_edges() == 0
