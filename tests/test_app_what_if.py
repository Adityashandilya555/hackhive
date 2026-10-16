"""Acceptance tests for #30 in the app: the What-if expander, its reset button, the WHAT-IF
banner on every view, and the counting-rules view. The CLI and solve side is test_what_if.py."""

import json
from pathlib import Path

from shapely.geometry import MultiPolygon, box, mapping
from streamlit.testing.v1 import AppTest

from hackhive.contracts import Constraints, Sidecar
from hackhive.io import load_plan
from hackhive.solve import solve
from hackhive.ui import readings_rows

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")
PLANS = ROOT / "tests" / "fixtures" / "plans"
TIMEOUT = 120
C = Constraints()


def _run(at: AppTest | None = None) -> AppTest:
    at = at or AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _what_if_banner(at: AppTest) -> list[str]:
    return [e.value for e in at.main.error if "WHAT-IF" in e.value]


def _big_upload(at: AppTest) -> AppTest:
    """A plan with a 120 m² bedroom (over the 100 m² cap) and a kitchen, as an upload."""
    raw = {
        "id": "BIG",
        "area": 150.0,
        "wall_depth": 2.0,
        "inner": mapping(box(0, 0, 150, 100)),
        "bedroom": mapping(box(0, 0, 120, 100)),
        "kitchen": mapping(box(122, 0, 150, 100)),
        "wall": mapping(MultiPolygon([box(120, 0, 122, 40), box(120, 50, 122, 100)])),
        "door": mapping(box(120, 40, 122, 50)),
    }
    at.sidebar.radio(key="source").set_value("Upload")
    _run(at)
    content = json.dumps(raw).encode()
    at.sidebar.file_uploader(key="upload").set_value(("big.json", content, "application/json"))
    return _run(at)


def _shown_solution(at: AppTest) -> Path:
    return Path(str(at.sidebar.selectbox(key="solution").value))


# --------------------------------------------------------------------------- banner


def test_the_preset_shows_no_banner():
    at = _run()
    assert not _what_if_banner(at)
    assert at.sidebar.number_input(key="wi_max_area_m2").value == 100


def test_any_change_shows_the_banner_and_writes_no_submission():
    at = _run()
    at.sidebar.number_input(key="wi_hub_wall_clearance_m").set_value(0.3)
    _run(at)
    (text,) = _what_if_banner(at)
    assert "hub to wall ≥ 0.3 m" in text and "never" in text.lower()
    shown = _shown_solution(at)
    side = Sidecar.model_validate_json(next(shown.parent.parent.glob("*.sidecar.json")).read_text())
    assert side.settings.is_problem_statement is False
    assert not (shown.parent.parent / "PLAN_5924.json").exists()  # no submission, only the sidecar
    assert ".what-if." in shown.name  # what the app draws is a display copy, named so


def test_the_banner_shows_on_the_plan_tab_too():
    """The banner sits above the tabs, so every view carries it."""
    at = _run()
    at.sidebar.toggle(key="wi_zone_links_via_excluded").set_value(True)
    _run(at)
    assert _what_if_banner(at)
    assert at.main.tabs[0].label == "Best" and at.main.tabs[1].label == "Plan"
    tabs_at = next(i for i, e in enumerate(at.main.children.values()) if e.type == "tab_container")
    banner_at = next(
        i
        for i, e in enumerate(at.main.children.values())
        if e.type == "error" and "WHAT-IF" in str(getattr(e, "value", ""))
    )
    assert banner_at < tabs_at


def test_reset_returns_to_the_problem_statement():
    at = _run()
    at.sidebar.number_input(key="wi_max_area_m2").set_value(250)
    at.sidebar.toggle(key="wi_route_through_other_rooms").set_value(True)
    _run(at)
    assert _what_if_banner(at)
    at.sidebar.button(key="wi_reset").click()
    _run(at)
    assert not _what_if_banner(at)
    assert at.sidebar.number_input(key="wi_max_area_m2").value == 100
    assert at.sidebar.toggle(key="wi_route_through_other_rooms").value is False
    assert (_shown_solution(at).parent / "PLAN_5924.json").exists()  # the submission again


# --------------------------------------------------------------------------- area cap (D4)


def test_a_raised_area_cap_designs_a_plan_with_a_room_over_100_m2():
    at = _big_upload(_run())
    assert not [s for s in at.sidebar.selectbox if s.key == "solution"]  # the preset: no design
    at.sidebar.number_input(key="wi_max_area_m2").set_value(150)
    _run(at)
    assert _what_if_banner(at)
    assert len(at.main.image) == 2  # the what-if design on Best, the plan on Plan
    assert not list(_shown_solution(at).parent.parent.glob("PLAN_BIG.json"))


# --------------------------------------------------------------------------- counting rules (D3)


def test_readings_rows_give_both_readings_of_one_design():
    sub = solve(load_plan(PLANS / "plan-5924.json"), C).submission
    rows = readings_rows(sub, C)
    assert [r["Reading"] for r in rows] == ["strict (ours)", "lenient"]
    strict, lenient = rows
    assert strict["L (m)"] == lenient["L (m)"]
    assert lenient["J"] <= strict["J"]


def test_counting_toggle_shows_both_readings_without_a_banner():
    """Showing both readings changes no setting: the design, and its submission, stay the same."""
    at = _run()
    at.sidebar.toggle(key="wi_counting").set_value(True)
    _run(at)
    assert not _what_if_banner(at)
    tables = [d.value for d in at.main.dataframe if "Reading" in d.value.columns]
    assert len(tables) == 1
    assert list(tables[0]["Reading"]) == ["strict (ours)", "lenient"]
