"""Acceptance tests for #26: the Arrangements tab, a gallery of every arrangement (#22's
`enumerate`) with a hub-count filter, the best J per hub count, tiles with H, J and the checker's
verdict, 12 per page, and "Open" showing one fully routed and validated in the Best tab."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from hackhive import solve as solve_mod
from hackhive.checker import check_rules, metrics
from hackhive.contracts import Constraints
from hackhive.io import load_plan
from hackhive.rings import first_ring
from hackhive.solve import (
    arrangement_submission,
    enumerate,
    rank_key,
    routed_submission,
    solve,
    zoning_count,
)
from hackhive.ui import (
    ARRANGEMENTS_PER_PAGE,
    best_by_hub_count,
    filter_by_hubs,
    hub_counts,
    page_count,
    page_of,
    tile_caption,
    tile_verdict,
)
from hackhive.viz import draw_tile

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")
PLANS = ROOT / "tests" / "fixtures" / "plans"
C = Constraints()
TIMEOUT = 180


@pytest.fixture(scope="module")
def plan_5088():
    return load_plan(PLANS / "plan-5088.json")


@pytest.fixture(scope="module")
def arr_5088(plan_5088):
    return enumerate(plan_5088, C, workers=1)


def _fast_zone_ring(zone, plan, g, grid, c):
    """A stand-in for `solve._zone_ring`: the first-cut hub, no routing. Enough to count and
    filter arrangements without #22's ~2 min of routing on 13344."""
    return first_ring(zone, plan, g, c, hub_id="H"), []


# --------------------------------------------------------------------------- the list


def test_zoning_count_is_what_the_tab_says_before_ranking():
    """The number the tab shows before ranking, with no routing: 13344's real 57 (#26 review)."""
    assert zoning_count(load_plan(PLANS / "plan-13344.json"), C) == 57
    assert zoning_count(load_plan(PLANS / "plan-5088.json"), C) == 4


def test_13344_lists_57_arrangements_and_filters_to_three_hubs(monkeypatch):
    """C29: 13344 at cap 5 has 57 arrangements over k = 3…7; filtering to k = 3 keeps only
    those (15), each with its rank in the full list."""
    monkeypatch.setattr(solve_mod, "_zone_ring", _fast_zone_ring)
    arrs = enumerate(load_plan(PLANS / "plan-13344.json"), C, workers=1)
    assert len(arrs) == 57
    assert hub_counts(arrs) == [3, 4, 5, 6, 7]
    three = filter_by_hubs(arrs, [3])
    assert len(three) == 15 and all(a.H == 3 for _, a in three)
    assert [n for n, _ in three] == sorted(n for n, _ in three)  # ranks from the full list
    assert filter_by_hubs(arrs, []) == list(zip(range(1, 58), arrs, strict=True))


def test_tiles_follow_rank_key(arr_5088):
    """C16: the gallery's order is `rank_key`'s (H, J, area gap, signature)."""
    assert [a.H for a in arr_5088] == sorted(a.H for a in arr_5088)
    assert arr_5088 == sorted(arr_5088, key=rank_key)
    assert [n for n, _ in filter_by_hubs(arr_5088, [])] == [1, 2, 3, 4]


def test_header_is_the_best_j_per_hub_count(arr_5088):
    rows = best_by_hub_count(arr_5088)
    assert [r["Hubs"] for r in rows] == hub_counts(arr_5088)
    for r in rows:
        same = [a for a in arr_5088 if a.H == r["Hubs"]]
        assert r["Arrangements"] == len(same)
        assert r["Best J"] == pytest.approx(round(min(a.J for a in same), 3))


def test_pages_hold_12_tiles():
    assert ARRANGEMENTS_PER_PAGE == 12
    assert page_count(0) == 1 and page_count(12) == 1 and page_count(13) == 2
    assert page_count(57) == 5
    items = list(range(57))
    assert page_of(items, 1) == list(range(12)) and page_of(items, 5) == list(range(48, 57))
    assert page_of(items, 9) == list(range(48, 57))  # past the end: the last page


# --------------------------------------------------------------------------- a tile


def test_tile_caption_shows_h_j_and_the_checker_verdict(plan_5088, arr_5088):
    """C16, C17, C47: H, J (the ranking's, D3's worse reading), length, bends and ✓ / ⚠ / ✗."""
    a = arr_5088[0]
    sub = arrangement_submission(plan_5088, a, C)
    verdict = tile_verdict(check_rules(plan_5088, sub, C))
    m = metrics(sub.hubs, C)
    text = tile_caption(1, a, m, verdict)
    assert text.startswith("#1 · ")
    assert f"H {a.H}" in text and f"J {a.J:.2f}" in text
    assert f"{m.L:.1f} m" in text and f"{m.B} bends" in text
    assert verdict in ("✓ valid", "⚠ strict only", "✗ invalid") and verdict in text


def test_tile_verdict_reads_the_checker():
    from hackhive.contracts import Violation

    assert tile_verdict([]) == "✓ valid"
    strict = Violation(rule=4, severity="strict", message="m")
    error = Violation(rule=5, severity="error", message="m")
    assert tile_verdict([strict]) == "⚠ strict only"
    assert tile_verdict([strict, error]) == "✗ invalid"


def test_draw_tile_is_the_overlay_without_its_legend(plan_5088, arr_5088):
    a = arr_5088[0]
    fig = draw_tile(plan_5088, a, C)
    ax = fig.axes[0]
    assert ax.get_legend() is None
    assert ax.lines  # its rings are drawn
    gids = {o.get_gid() for o in ax.findobj() if o.get_gid()}
    assert {f"hub-label:{h.hub_id}" for h in a.hubs} <= gids  # and every hub is labelled


def test_arrangement_submission_is_the_cached_rings(plan_5088, arr_5088):
    a = arr_5088[0]
    sub = arrangement_submission(plan_5088, a, C)
    assert sub.plan_id == plan_5088.plan_id and [h.hub_id for h in sub.hubs] == [
        h.hub_id for h in a.hubs
    ]
    assert [len(h.route_segments) for h in sub.hubs] == [len(r) for r in a.rings]


def test_open_routes_it_fully_and_matches_solve(plan_5088, arr_5088):
    """C26: "Open" is the arrangement after the rip-up passes, checked; for the arrangement
    solve() submitted, it is the same design."""
    result = solve(plan_5088, C)
    rank = result.sidecar.arrangement.rank
    sub = routed_submission(plan_5088, arr_5088[rank - 1], C)
    assert not [v for v in check_rules(plan_5088, sub, C) if v.severity == "error"]
    assert metrics(sub.hubs, C).J == pytest.approx(result.sidecar.cost.J)


# --------------------------------------------------------------------------- the tab


def _run(at: AppTest | None = None) -> AppTest:
    at = at or AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _tab(at: AppTest):
    return next(t for t in at.main.tabs if t.label == "Arrangements")


def _5088(at: AppTest) -> AppTest:
    at.sidebar.selectbox(key="fixture").set_value("5088")
    return _run(at)


def test_the_tab_ranks_only_on_request():
    """Ranking routes every zone (13344: ~2 min), so the tab asks first; nothing is enumerated
    on a plain page load."""
    tab = _tab(_5088(_run()))
    assert not tab.image
    assert any("4 arrangements" in c.value for c in tab.caption)
    assert tab.button(key="arr_go")


def test_the_gallery_shows_tiles_filters_and_the_header():
    at = _5088(_run())
    _tab(at).button(key="arr_go").click()
    tab = _tab(_run(at))
    assert len(tab.image) == 4
    (header,) = [d.value for d in tab.dataframe if "Best J" in d.value.columns]
    assert list(header["Hubs"]) == [1, 2, 3]
    assert any("#1 · H 1" in c.value for c in tab.caption)
    tab.multiselect(key="arr_k").set_value([2])
    tab = _tab(_run(at))
    assert len(tab.image) == 2
    assert all(" · H 2 · " in c.value for c in tab.caption if c.value.startswith("#"))


def test_open_shows_the_arrangement_validated_in_the_best_tab():
    at = _5088(_run())
    _tab(at).button(key="arr_go").click()
    _run(at)
    _tab(at).button(key="arr_open_2").click()
    _run(at)
    shown = at.sidebar.selectbox(key="solution")
    assert "arrangement #2" in shown.format_func(shown.value)
    best = at.main.tabs[0]
    assert any("arrangement #2" in i.value for i in best.info)
    assert not best.success  # solve()'s green banner is about another design: not shown here
    assert any("valid" in m.value for m in best.markdown)  # the checker's verdict
    assert len(best.image) == 1


def test_a_worker_importing_the_app_never_runs_the_page(monkeypatch):
    """#22's pool workers import app.py as `__mp_main__` (spawn on macOS and Windows, and the
    fork server): that must define functions only, never draw the page or solve a plan (#26)."""
    import runpy

    def boom(*_a, **_k):
        raise AssertionError("a worker re-ran the app")

    monkeypatch.setattr(solve_mod, "solve", boom)
    monkeypatch.setattr(solve_mod, "enumerate", boom)
    names = runpy.run_path(APP, run_name="__mp_main__")
    assert callable(names["page"]) and callable(names["arrangements_tab"])
