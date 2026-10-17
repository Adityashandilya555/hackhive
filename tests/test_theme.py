"""The Ink Wash theme's KPI cards: pure, built from the sidecar and the plan (no Streamlit)."""

from pathlib import Path

import pytest

from hackhive.contracts import Bounds, Constraints, CostBreakdown, CostWeights, Sidecar
from hackhive.io import load_plan
from hackhive.theme import kpi_cards, kpi_html

PLANS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "plans"
C = Constraints.problem_statement()
COST = CostBreakdown(H=3, L=31.5, X=0, O=0.0, B=30, J=46.5, area_gap=0.0)  # 31.5 + 0.5 × 30


@pytest.fixture(scope="module")
def plan():
    return load_plan(PLANS / "plan-5924.json")


def _side(status="ok", proof="exhaustive", cost=COST, **kw) -> Sidecar:
    bounds = Bounds(k_lb=2, k_found=3, proof=proof)
    return Sidecar(plan_id="PLAN_5924", status=status, bounds=bounds, cost=cost, **kw)


def test_ok_design_proven_minimum(plan):
    hubs, cost, coverage = kpi_cards(_side(), plan, C)
    assert (hubs["tone"], hubs["label"], hubs["value"]) == ("cream", "Hubs · H vs LB", "3")
    assert (hubs["unit"], hubs["pill"], hubs["meter"]) == ("of LB 2", "✓ proven min", 2 / 3)
    assert (cost["tone"], cost["value"], cost["unit"]) == ("slate", "46.50", "= L + 5X + O + 0.5B")
    assert cost["pill"] == "lower is better"
    assert [s for s, _, _ in cost["terms"]] == ["L", "5X", "O", "0.5B"]
    assert sum(float(v) for _, v, _ in cost["terms"]) == pytest.approx(46.5)
    assert [w for w, _ in cost["stack"]] == pytest.approx([31.5, 0, 0, 15])
    assert (coverage["tone"], coverage["value"], coverage["unit"]) == (
        "grey",
        "150.0",
        "m² · 5 nodes",
    )
    assert coverage["meter"] == pytest.approx(0.5, abs=1e-3)  # 150.01 m² of 300
    assert coverage["caption"] == "50% of 3 hubs × 100 m² cap"


def test_cost_terms_follow_the_active_weights(plan):
    c = Constraints(cost_weights=CostWeights(1.0, 10.0, 2.0, 1.0))
    cost = CostBreakdown(H=3, L=20.0, X=1, O=0.5, B=4, J=35.0, area_gap=0.0)  # 20 + 10 + 1 + 4
    _, card, _ = kpi_cards(_side(cost=cost), plan, c)
    assert card["unit"] == "= L + 10X + 2O + B"
    assert [(s, v) for s, v, _ in card["terms"]] == [
        ("L", "20.00"),
        ("10X", "10.00"),
        ("2O", "1.00"),
        ("B", "4.00"),
    ]
    assert sum(float(v) for _, v, _ in card["terms"]) == pytest.approx(cost.J)


def test_strict_only_over_the_lower_bound(plan):
    hubs, _, _ = kpi_cards(_side("submitted_strict_only", proof="greedy"), plan, C)
    assert (hubs["tone"], hubs["value"], hubs["pill"]) == ("cream", "3", "+1 over LB")


def test_infeasible_turns_hubs_danger_with_the_reason(plan):
    side = Sidecar(
        plan_id="PLAN_5924", status="infeasible", reason="routing failed", bounds=Bounds(k_lb=2)
    )
    hubs, cost, coverage = kpi_cards(side, plan, C)
    assert (hubs["tone"], hubs["value"], hubs["caption"]) == ("danger", "✗", "routing failed")
    assert cost["value"] == coverage["value"] == "–"
    assert all(k.get("meter") is None for k in (hubs, cost, coverage))


@pytest.mark.parametrize("side", [None, Sidecar(plan_id="bad", status="input_error", reason="x")])
def test_no_plan_shows_dashes_and_no_meters(side):
    cards = kpi_cards(side, None, C)
    assert [k["tone"] for k in cards] == ["cream" if side is None else "danger", "slate", "grey"]
    assert [k["value"] for k in cards] == ["–" if side is None else "✗", "–", "–"]
    assert all(k.get("meter") is None and not k.get("terms") for k in cards)


def test_kpi_html_escapes_every_text():
    card = {"tone": "danger", "label": "<b>Hubs</b>", "value": "✗", "caption": 'a < b & "c"'}
    html = kpi_html(card)
    assert html.startswith('<div class="hh-kpi danger">')
    assert "<b>Hubs</b>" not in html and "&lt;b&gt;Hubs&lt;/b&gt;" in html
    assert "a &lt; b &amp; &quot;c&quot;" in html
    assert 'class="meter"' not in html  # no meter without one
    assert 'style="width:50%"' in kpi_html(
        {"tone": "cream", "label": "x", "value": 1, "meter": 0.5}
    )
