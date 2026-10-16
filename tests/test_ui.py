"""Acceptance tests for #83: case tags and the per-status banner, pure (no Streamlit)."""

from pathlib import Path

import pytest
from fixture_solves import solved

from hackhive.contracts import Constraints, Sidecar, Violation
from hackhive.graph import build_graph
from hackhive.ui import case_tags, status_banner

PLANS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "plans"
C = Constraints.problem_statement()


def _case(pid: str):
    result = solved(pid)  # one solve per fixture for the session
    return result.plan, build_graph(result.plan), result


def _labels(pid: str) -> list[str]:
    return [t.label for t in case_tags(*_case(pid))]


def test_case_tags_5088_en_suite_single_door():
    assert "en-suite single-door" in _labels("5088")


def test_case_tags_576():
    labels = _labels("576")
    for want in (
        "hubs > lower bound (+4, proven minimum)",
        "multi-part living",
        "isolated eligible room",
        "one-node ring",
        "4+ hubs",
    ):
        assert want in labels, labels
    assert "hubs = lower bound" not in labels


def test_case_tags_1293_open_passage():
    assert "open passage" in _labels("1293")


def test_case_tags_every_tag_has_an_id_and_a_look_for():
    for pid in ("5088", "576", "1293", "5924"):
        for t in case_tags(*_case(pid)):
            assert t.id and t.label and t.look_for


def test_case_tags_without_a_design_still_tag_the_plan():
    plan, G, res = _case("576")
    res = type(res)(plan, None, res.sidecar)
    assert "multi-part living" in [t.label for t in case_tags(plan, G, res)]


def test_banner_ok_5088():
    plan, _, res = _case("5088")
    kind, text = status_banner(res.sidecar, plan)
    assert kind == "success" and "✓" in text and "ok" in text


def test_banner_strict_only_names_its_retraces():
    """A strict-only sidecar of its own, so the test doesn't move each time a fixture's routing
    improves (5924, then 13344; #95 review)."""
    retrace = Violation(rule=4, severity="strict", hub_id="H1", message="N1 retrace (#46 Q5)")
    side = Sidecar(plan_id="P", status="submitted_strict_only", violations=[retrace] * 2)
    kind, text = status_banner(side, None)
    assert kind == "warning"
    assert "2 retraces" in text and "rule 4" in text and "Q2" in text


def test_banner_strict_only_names_each_rule_present():
    """#90 review: a rule-6-only strict design names rule 6 and Q4, never "0 retraces"."""
    hub = Violation(rule=6, severity="strict", message="H1 0.450 m from R01's edge")
    kind, text = status_banner(
        Sidecar(plan_id="P", status="submitted_strict_only", violations=[hub]), None
    )
    assert kind == "warning"
    assert "1 hub" in text and "rule 6" in text and "Q4" in text
    assert "retrace" not in text and "Q2" not in text


def test_banner_strict_only_says_one_retrace_in_the_singular():
    retrace = Violation(rule=4, severity="strict", message="N1 retrace")
    _, text = status_banner(
        Sidecar(plan_id="P", status="submitted_strict_only", violations=[retrace]), None
    )
    assert "1 retrace " in text and "rule 4" in text and "Q2" in text


def test_banner_room_over_100_names_the_room_and_area():
    plan, _, res = _case("5924")
    side = Sidecar(
        plan_id=plan.plan_id,
        status="infeasible",
        reason="infeasible: room R01 is 123.4 m² > 100 m²",
    )
    kind, text = status_banner(side, plan)
    assert kind == "error" and "R01" in text and "123.4 m²" in text and "#30" in text


@pytest.mark.parametrize(
    ("reason", "want"),
    [
        ("grid failed: door D1 too thin", "#78"),
        ("routing failed: N3 has no free cell", "routing failed"),
        ("infeasible: no hub-capable room can serve R04", "hub"),
        ("too large for exact zoning: 20 rooms (greedy fallback: #49)", "too large"),
    ],
)
def test_banner_infeasible_reasons(reason, want):
    plan, _, _ = _case("5088")
    kind, text = status_banner(Sidecar(plan_id="P", status="infeasible", reason=reason), plan)
    assert kind == "error" and want in text


def test_banner_input_error_shows_the_message():
    kind, text = status_banner(
        Sidecar(plan_id="?", status="input_error", reason="not a JSON file"), None
    )
    assert kind == "error" and "not a JSON file" in text
