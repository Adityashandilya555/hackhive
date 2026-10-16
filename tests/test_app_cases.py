"""#85: the "Cases" source. Every case in `cases.json` opens from the sidebar, says what it shows,
and compares what `solve()` gives now with the case's `expected`."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from hackhive import ui
from hackhive.contracts import Bounds, Sidecar

APP = str(Path(__file__).resolve().parents[1] / "app.py")
TIMEOUT = 240  # strict-only's plan (15333) solves in 60-90 s alone, more on a busy runner
ROWS = ui.case_rows()
ROW = {r["case"]: r for r in ROWS}


# --------------------------------------------------------------------------- pure helpers


def test_options_are_grouped_designs_then_warnings_then_no_design():
    groups = [ui.case_group(ROW[c]) for c in ui.case_options(ROWS)]
    assert groups == sorted(groups, key=list(ui.CASE_GROUPS).index)
    assert sorted(ui.case_options(ROWS)) == sorted(ROW)  # every case, once


def test_option_label_names_the_group_and_the_title():
    assert ui.case_option_label(ROW["room_over_cap"]) == "no design · A room over 100 m²"
    assert ui.case_option_label(ROW["strict_only"]) == "warnings · Strict-only design"
    assert ui.case_option_label(ROW["ok"]) == "designs · A clean design"


def test_check_is_green_when_status_and_hubs_match():
    row = ROW["above_lb_4"]  # 576: ok, 6 hubs
    side = Sidecar(plan_id="PLAN_576", status="ok", bounds=Bounds(k_lb=2, k_found=6))
    kind, text = ui.case_check(row, side)
    assert kind == "success" and text.startswith("✓") and "6 hubs" in text


def test_check_is_red_when_the_hub_count_differs():
    side = Sidecar(plan_id="PLAN_576", status="ok", bounds=Bounds(k_lb=2, k_found=5))
    kind, text = ui.case_check(ROW["above_lb_4"], side)
    assert kind == "error" and text.startswith("✗") and "6 hubs" in text and "5 hubs" in text


def test_check_without_a_design_compares_the_status_only():
    side = Sidecar(plan_id="PLAN_1782", status="infeasible", bounds=Bounds(k_lb=3))
    kind, text = ui.case_check(ROW["room_over_cap"], side)
    assert kind == "success" and "infeasible" in text and "hubs" not in text


# --------------------------------------------------------------------------- the app


def _run(at: AppTest | None = None) -> AppTest:
    at = at or AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _open(case: str) -> AppTest:
    at = _run()
    at.sidebar.radio(key="source").set_value(ui.CASES_SOURCE)
    _run(at)
    at.sidebar.selectbox(key="case").set_value(case)
    return _run(at)


def _plain(text: str) -> str:
    return text.replace("\\", "")  # md_text escapes markdown


def _params() -> list:
    """One per row; rows that share a plan share an xdist worker, so the app's cache solves each
    plan once (`--dist loadgroup`)."""
    return [
        pytest.param(r, id=r["case"], marks=pytest.mark.xdist_group(f"case-{r['plan']}"))
        for r in ROWS
    ]


@pytest.mark.parametrize("row", _params())
def test_each_case_opens_and_says_what_it_shows(row):
    """C22, C26: no exception, the callout shows `look_for`, the banner matches `expected`, and
    expected against got is green."""
    at = _open(row["case"])
    infos = [_plain(i.value) for i in at.main.info]
    assert any(row["look_for"] in i for i in infos), infos
    status = row["expected"]["status"]
    kind = {"ok": "success", "submitted_strict_only": "warning"}.get(status, "error")
    banners = [_plain(b.value) for b in getattr(at.main, kind)]
    assert any(f"**{status}**" in b for b in banners), banners
    checks = [_plain(s.value) for s in at.main.success if s.value.startswith("✓ Expected")]
    assert checks, [s.value for s in at.main.error]


def test_room_over_100_shows_the_red_banner_and_the_plan_still_draws():
    """C27: no design, but the Plan tab draws the plan."""
    at = _open("room_over_cap")
    assert any("**infeasible**" in e.value and "100 m²" in _plain(e.value) for e in at.main.error)
    assert at.main.image, "the Plan tab's drawing"


def test_the_default_source_still_opens_fixture_5924():
    at = _run()
    assert at.sidebar.radio(key="source").value == "Test plans"
    assert at.sidebar.selectbox(key="fixture").value == "5924"
