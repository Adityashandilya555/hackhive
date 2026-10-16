"""Acceptance tests for #24: the optimality badge and checklist line 5 (T7)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from hackhive import checker
from hackhive.contracts import Bounds, Constraints, CostBreakdown, Sidecar
from hackhive.io import load_plan
from hackhive.solve import solve
from hackhive.ui import optimality_badge, proven_min_hubs

PLANS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "plans"
C = Constraints.problem_statement()
COST = CostBreakdown(H=3, L=31.5, X=0, O=0.0, B=30, J=46.5, area_gap=62.0)


def _side(k_lb: int, k_found: int, proof: str) -> Sidecar:
    return Sidecar(
        plan_id="PLAN_X",
        status="ok",
        bounds=Bounds(k_lb=k_lb, k_found=k_found, proof=proof),
        cost=COST.model_copy(update={"H": k_found}),
    )


@pytest.fixture(scope="module")
def solved():
    def run(pid: str):
        plan = load_plan(PLANS / f"plan-{pid}.json")
        return plan, solve(plan, C).sidecar

    return run


def test_badge_5924_proven_minimum(solved):
    plan, side = solved("5924")
    b = optimality_badge(side, plan, C)
    assert b.kind == "success"
    assert "3 hubs · optimal (exhaustive search) · LB 2 (+1)" in b.text
    assert "✓ proven minimum" in b.text and "✗" not in b.text
    assert b.explain  # the contiguity / star-around-living expander


def test_badge_1293_proven_minimum(solved):
    """The issue's "1293 = lower bound" predates #62: 1293 is LB 2 but needs 3 hubs
    (tests/fixtures/README.md, docs/ARCHITECTURE.md), and #22's search proves it."""
    plan, side = solved("1293")
    b = optimality_badge(side, plan, C)
    assert b.kind == "success" and "✗" not in b.text
    assert "✓ proven minimum · 3 hubs · optimal (exhaustive search) · LB 2 (+1)" in b.text


@pytest.mark.parametrize("pid", ["576", "1293", "2814", "3086", "5088", "5924", "13344"])
def test_badge_follows_the_real_proof(solved, pid):
    """#22's real `bounds.proof`: lb_met reads "= lower bound", exhaustive "proven minimum"."""
    plan, side = solved(pid)
    b = optimality_badge(side, plan, C)
    assert side.bounds.proof in ("lb_met", "exhaustive")
    assert b.kind == "success" and "✗" not in b.text
    assert ("= lower bound" in b.text) == (side.bounds.proof == "lb_met")
    assert ("✓ proven minimum" in b.text) == (side.bounds.proof == "exhaustive")


def test_badge_k_equals_lb_5088(solved):
    plan, side = solved("5088")
    b = optimality_badge(side, plan, C)
    assert b.kind == "success"
    assert b.text.startswith("✓ 1 hub = lower bound (50.0 m², 3 nodes)")
    assert b.explain is None


@pytest.mark.parametrize("proof", ["none", "greedy"])
def test_badge_unproven_never_says_optimal(solved, proof):
    plan, _ = solved("5088")
    b = optimality_badge(_side(2, 3, proof), plan, C)
    assert "optimal" not in (b.text + (b.explain or "")).lower()
    assert b.kind == "error" and b.text.startswith("✗")


def test_badge_shows_j_and_its_breakdown(solved):
    plan, side = solved("5924")
    b = optimality_badge(side, plan, C)
    m = side.cost
    assert f"J = {m.J:.2f}" in b.text
    for part in (f"L {m.L:.2f} m", f"X {m.X}", f"O {m.O:.2f} m", f"B {m.B}"):
        assert part in b.text
    assert "optimal refers to the hub count only" in b.text


def test_no_badge_without_a_design(solved):
    plan, _ = solved("5088")
    assert optimality_badge(Sidecar(plan_id="PLAN_X", status="infeasible"), plan, C) is None


@pytest.mark.parametrize(
    "proof, want", [("exhaustive", 3), ("lb_met", 3), ("greedy", None), ("none", None)]
)
def test_proven_min_hubs_reads_only_proofs(proof, want):
    assert proven_min_hubs(_side(2, 3, proof)) == want


def test_checklist_line_5_is_green_when_proven_minimum():
    """T7: k > LB but proven minimal is ✓ "proven minimum", never ✗."""
    lines = checker.checklist([], SimpleNamespace(H=3), 2, 120.0, 6, C, proven_min=3)
    assert lines[4] == "✓ 3 hubs > lower bound 2 (120.0 m², 6 nodes) · proven minimum"


def test_checklist_line_5_stays_red_above_a_proven_minimum():
    """A submission with more hubs than the proven minimum is still ✗ (e.g. 5088 two hubs)."""
    lines = checker.checklist([], SimpleNamespace(H=3), 1, 50.0, 3, C, proven_min=1)
    assert lines[4].startswith("✗ 3 hubs > lower bound 1")


def test_checklist_line_5_unchanged_without_a_proof():
    """The checker CLI knows no proof: it keeps today's ✗ for k > LB."""
    lines = checker.checklist([], SimpleNamespace(H=3), 2, 120.0, 6, C)
    assert lines[4] == "✗ 3 hubs > lower bound 2 (120.0 m², 6 nodes)"


def test_report_passes_the_proof_through():
    """The app's path: `checker.report` with the sidecar's proven minimum (5924: 3 hubs, LB 2)."""
    plan = load_plan(PLANS / "plan-5924.json")
    res = solve(plan, C)
    rep = checker.report(plan, res.submission, C, proven_min=proven_min_hubs(res.sidecar))
    assert rep.lines[4] == "✓ 3 hubs > lower bound 2 (" + rep.lines[4].split("(", 1)[1]
    assert rep.lines[4].endswith("· proven minimum")


def test_app_best_tab_shows_the_badge_for_5924():
    """See it: the app opens on 5924; the badge is green ✓, with no red ✗ on checklist line 5."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(PLANS.parents[2] / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    success = "\n".join(s.value for s in at.main.success)
    assert "✓ proven minimum · 3 hubs · optimal (exhaustive search) · LB 2 (+1)" in success
    assert not any("lower bound" in e.value for e in at.main.error)
    lines = [m.value.replace("\\", "") for m in at.main.markdown]  # md_text escapes ">"
    assert any(v.startswith("✓ 3 hubs > lower bound 2") for v in lines), lines
    assert not any("✗" in v and "lower bound" in v for v in lines)
