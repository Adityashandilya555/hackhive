"""Acceptance tests for #17: the solution overlay and the per-hub summary."""

import json
from pathlib import Path

import pytest
from matplotlib.colors import to_hex
from matplotlib.figure import Figure

from hackhive import checker
from hackhive.contracts import Constraints, Submission
from hackhive.graph import build_graph
from hackhive.io import load_plan
from hackhive.ui import hub_metrics, hub_summary_line
from hackhive.viz import (
    STATUS_CRITICAL,
    ZONE_COLOURS,
    draw_solution,
    main,
    to_png,
)

ROOT = Path(__file__).resolve().parents[1]
PLANS = ROOT / "tests" / "fixtures" / "plans"
SOLUTIONS = ROOT / "tests" / "fixtures" / "solutions"
VALID = ["5088-valid", "5088-two-hubs-valid", "5088-retrace-single-door", "5088-retrace-two-door"]
C = Constraints()


@pytest.fixture(scope="module")
def plan_5088():
    plan = load_plan(PLANS / "plan-5088.json")
    build_graph(plan)
    return plan


def _sub(name: str) -> Submission:
    return Submission.model_validate(json.loads((SOLUTIONS / f"{name}.json").read_text("utf-8")))


def _draw(plan, name: str, **kw) -> tuple[Figure, object, Submission]:
    sub = _sub(name)
    violations = checker.check_rules(plan, sub, C)
    fig = draw_solution(plan, sub, violations=violations, **kw)
    return fig, fig.axes[0], sub


def _gids(ax, prefix: str) -> list:
    return [
        a for a in [*ax.lines, *ax.patches, *ax.texts] if (a.get_gid() or "").startswith(prefix)
    ]


def _texts(ax) -> list[str]:
    return [t.get_text() for t in ax.texts]


# --------------------------------------------------------------------------- zones, hubs, numbers


def test_two_hubs_two_zone_colours_two_squares(plan_5088):
    _, ax, sub = _draw(plan_5088, "5088-two-hubs-valid")
    fills = {to_hex(p.get_facecolor()[:3]) for p in _gids(ax, "zone:")}
    assert len(fills) == 2 and fills <= {to_hex(c) for c in ZONE_COLOURS}
    squares = _gids(ax, "hub:")
    assert len(squares) == 2 and all(s.get_marker() == "s" for s in squares)
    for h, colour in zip(sub.hubs, ZONE_COLOURS, strict=False):
        rings = [ln for ln in _gids(ax, f"ring:{h.hub_id}:")]
        assert rings and all(to_hex(ln.get_color()) == to_hex(colour) for ln in rings)


@pytest.mark.parametrize("name", VALID)
def test_each_ring_is_numbered_1_to_k_plus_1_with_the_last_on_the_return(plan_5088, name):
    _, ax, sub = _draw(plan_5088, name)
    for h in sub.hubs:
        k = len(h.assigned_nodes)
        marks = {t.get_gid().split(":")[2]: t for t in _gids(ax, f"order:{h.hub_id}:")}
        assert sorted(marks, key=int) == [str(i) for i in range(1, k + 2)]
        last = h.route_segments[-1]
        assert last.to == h.hub_id  # the return leg …
        assert marks[str(k + 1)].get_text() == str(k + 1)  # … carries the last number
        x, y = marks[str(k + 1)].xy
        assert _on_polyline((x, y), last.polyline)


def test_every_hub_and_node_shows_its_id(plan_5088):
    for name in VALID:
        _, ax, sub = _draw(plan_5088, name)
        texts = _texts(ax)
        assert {h.hub_id for h in sub.hubs} <= set(texts)
        assert {n.id for n in plan_5088.nodes} <= set(texts)


# --------------------------------------------------------------------------- routes, exactly


def _on_polyline(p, polyline, tol=1e-6) -> bool:
    for a, b in zip(polyline, polyline[1:], strict=False):
        (ax_, ay), (bx, by) = a, b
        if (
            min(ax_, bx) - tol <= p[0] <= max(ax_, bx) + tol
            and min(ay, by) - tol <= p[1] <= max(ay, by) + tol
        ):
            if abs((bx - ax_) * (p[1] - ay) - (by - ay) * (p[0] - ax_)) <= tol * max(
                1.0, abs(bx - ax_) + abs(by - ay)
            ):
                return True
    return False


@pytest.mark.parametrize("name", VALID)
def test_no_drawn_route_is_offset_from_a_submitted_polyline(plan_5088, name):
    _, ax, sub = _draw(plan_5088, name)
    polylines = [s.polyline for h in sub.hubs for s in h.route_segments]
    drawn = _gids(ax, "ring:") + _gids(ax, "overlap:")
    assert drawn
    for ln in drawn:
        for p in zip(ln.get_xdata(), ln.get_ydata(), strict=True):
            assert any(_on_polyline(p, pl) for pl in polylines), (ln.get_gid(), p)


def test_single_door_room_shows_both_parallel_runs_through_its_door(plan_5088):
    """5088's kitchen (R03) has one door: H1→N3 goes in at x = 4.6, N3→N2 leaves at x = 5.3."""
    _, ax, _ = _draw(plan_5088, "5088-valid")
    xs = set()
    for ln in _gids(ax, "ring:"):
        pts = list(zip(ln.get_xdata(), ln.get_ydata(), strict=True))
        for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
            if abs(x0 - x1) < 1e-9 and min(y0, y1) < 5.0 < max(y0, y1):  # crosses the door's y
                xs.add(round(x0, 3))
    assert {4.6, 5.3} <= xs
    assert not _gids(ax, "overlap:")


def test_same_line_retrace_is_drawn_once_with_a_times_two_marker(plan_5088):
    fig, ax, sub = _draw(plan_5088, "5088-retrace-single-door")
    overlaps = _gids(ax, "overlap:")
    assert overlaps and all(ln.get_linestyle() == "--" for ln in overlaps)
    assert "×2" in _texts(ax)
    # drawn once: no ring line also covers an overlap stretch's midpoint
    for ln in overlaps:
        xs, ys = ln.get_xdata(), ln.get_ydata()
        mid = ((xs[0] + xs[-1]) / 2, (ys[0] + ys[-1]) / 2)
        rings = [list(zip(r.get_xdata(), r.get_ydata(), strict=True)) for r in _gids(ax, "ring:")]
        assert not any(_on_polyline(mid, r, tol=1e-9) for r in rings)


@pytest.mark.parametrize("name", [p.stem for p in sorted(SOLUTIONS.glob("5088-*.json"))])
def test_overlap_stretches_add_up_to_the_checkers_O(plan_5088, name):
    sub = _sub(name)
    try:
        m = checker.metrics(sub.hubs, C)
    except ValueError:
        pytest.skip("non-finite coordinates")
    legs = [checker.clean_polyline(s.polyline) for h in sub.hubs for s in h.route_segments]
    owners = [h.hub_id for h in sub.hubs for _ in h.route_segments]
    stretches = checker.overlap_stretches(legs, owners)
    assert sum((s.k - 1) * s.length for s in stretches) == pytest.approx(m.O, abs=1e-9)


# --------------------------------------------------------------------------- violations in red


def _is_red(colour) -> bool:
    return colour is not None and to_hex(colour) == to_hex(STATUS_CRITICAL)


def _reds(ax) -> list:
    out = []
    for a in [*ax.lines, *ax.patches, *ax.texts]:
        cols = []
        if hasattr(a, "get_color"):
            cols.append(a.get_color())
        if hasattr(a, "get_markerfacecolor"):
            cols += [a.get_markerfacecolor(), a.get_markeredgecolor()]
        if hasattr(a, "get_facecolor") and not hasattr(a, "get_markerfacecolor"):
            cols.append(a.get_facecolor())
        if any(isinstance(c, str | tuple) and _is_red(c) for c in cols):
            out.append(a)
    return out


def test_hub_too_close_to_a_wall_is_red_with_its_rule(plan_5088):
    _, ax, _ = _draw(plan_5088, "5088-hub-0.3m-from-wall")
    (square,) = _gids(ax, "hub:H1")
    assert _is_red(square.get_markerfacecolor())
    labels = [t for t in _gids(ax, "violation:") if t in ax.texts]
    assert labels and any("rule 6" in t.get_text() for t in labels)
    assert all(_is_red(t.get_color()) for t in labels)


@pytest.mark.parametrize("name", VALID[:3])
def test_valid_solutions_show_no_red(plan_5088, name):
    _, ax, _ = _draw(plan_5088, name)
    assert not _reds(ax)


def test_wall_crossing_draws_its_where_points_in_red(plan_5088):
    _, ax, _ = _draw(plan_5088, "5088-wall-crossing")
    marks = [a for a in _gids(ax, "violation:") if a in ax.lines]
    assert marks and all(_is_red(m.get_color()) for m in marks)
    assert any("rule 5" in t.get_text() for t in ax.texts if t.get_gid())


# --------------------------------------------------------------------------- legend, step, output


def test_how_to_read_legend(plan_5088):
    _, ax, _ = _draw(plan_5088, "5088-retrace-single-door")
    legend = ax.get_legend()
    assert legend is not None and legend.get_title().get_text() == "How to read"
    entries = " ".join(t.get_text() for t in legend.get_texts())
    for word in ("zone", "hub", "node", "order", "×k"):
        assert word in entries
    _, ax, _ = _draw(plan_5088, "5088-valid")
    entries = " ".join(t.get_text() for t in ax.get_legend().get_texts())
    assert "×k" not in entries and "violation" not in entries  # only what is drawn


def test_step_draws_only_the_first_legs(plan_5088):
    _, ax, _ = _draw(plan_5088, "5088-valid", step=2)
    marks = sorted(int(t.get_gid().split(":")[2]) for t in _gids(ax, "order:H1:"))
    assert marks == [1, 2]


# --------------------------------------------------------------------------- #25 step-through


def _n_segments(sub) -> int:
    return sum(len(h.route_segments) for h in sub.hubs)


@pytest.mark.parametrize("name", ["5088-valid", "5088-two-hubs-valid", "5088-hub-0.3m-from-wall"])
def test_step_zero_draws_only_hubs_and_nodes(plan_5088, name):
    """#25: step 0 is the empty board: hubs, nodes (and the zone tints that say which is
    whose), no cable, no numbers, no ×k, no violation marks yet."""
    _, ax, sub = _draw(plan_5088, name, step=0)
    for prefix in ("ring:", "overlap:", "order:", "violation:", "current:"):
        assert not _gids(ax, prefix), prefix
    assert len(_gids(ax, "hub:")) == len(sub.hubs)
    assert len(_gids(ax, "node:")) == len(plan_5088.nodes)


@pytest.mark.parametrize(
    "name",
    [
        "5088-valid",
        "5088-two-hubs-valid",
        "5088-retrace-single-door",
        "5088-hub-0.3m-from-wall",
    ],
)
def test_step_n_equals_the_full_overlay(plan_5088, name):
    """#25: the last step is the full overlay, pixel for pixel (violations included)."""
    sub = _sub(name)
    violations = checker.check_rules(plan_5088, sub, C)
    full = to_png(draw_solution(plan_5088, sub, violations=violations))
    last = to_png(draw_solution(plan_5088, sub, violations=violations, step=_n_segments(sub)))
    assert last == full


def test_step_counts_segments_across_rings_in_hub_order(plan_5088):
    """#25: one slider over every segment: H1's ring first, then H2's."""
    sub = _sub("5088-two-hubs-valid")
    k1 = len(sub.hubs[0].route_segments)
    _, ax, _ = _draw(plan_5088, "5088-two-hubs-valid", step=k1 + 1)
    assert len(_gids(ax, "order:H1:")) == k1
    assert [t.get_gid() for t in _gids(ax, "order:H2:")] == ["order:H2:1"]


def test_the_newest_leg_is_highlighted(plan_5088):
    _, ax, sub = _draw(plan_5088, "5088-valid", step=2)
    (current,) = _gids(ax, "current:")
    assert current.get_gid() == "current:H1:2"
    seg = sub.hubs[0].route_segments[1]
    pts = list(zip(current.get_xdata(), current.get_ydata(), strict=True))
    assert pts[0] == pytest.approx(tuple(seg.polyline[0])) and pts[-1] == pytest.approx(
        tuple(seg.polyline[-1])
    )


def test_the_last_step_of_each_ring_returns_to_its_own_hub(plan_5088):
    """#25 (C10): at the step that closes a ring, the newest leg ends at that ring's own hub and
    is labelled as the return; at the final step the overlay is complete."""
    sub = _sub("5088-two-hubs-valid")
    total = _n_segments(sub)
    done = 0
    for h in sub.hubs:
        done += len(h.route_segments)
        last = h.route_segments[-1]
        assert last.to == h.hub_id
        assert tuple(last.polyline[-1]) == pytest.approx((h.location.x, h.location.y))
        if done == total:  # the final step is the plain full overlay
            continue
        _, ax, _ = _draw(plan_5088, "5088-two-hubs-valid", step=done)
        (current,) = _gids(ax, "current:")
        assert current.get_gid() == f"current:{h.hub_id}:{len(h.route_segments)}"
        end = (current.get_xdata()[-1], current.get_ydata()[-1])
        assert end == pytest.approx((h.location.x, h.location.y))
        labels = [t.get_text() for t in _gids(ax, "return-label:")]
        assert labels == [f"↩ back to {h.hub_id}"]


def test_step_captions():
    from hackhive.ui import step_caption

    sub = _sub("5088-two-hubs-valid")
    total = _n_segments(sub)
    k1 = len(sub.hubs[0].route_segments)
    assert step_caption(sub, 0) == f"Step 0 of {total} · hubs and nodes only"
    first = sub.hubs[0].route_segments[0]
    assert step_caption(sub, 1) == f"Step 1 of {total} · H1: {first.from_} → {first.to}"
    back = sub.hubs[0].route_segments[-1]
    assert step_caption(sub, k1) == (
        f"Step {k1} of {total} · H1: {back.from_} → {back.to}, back to its hub"
    )
    assert step_caption(sub, total) == f"Step {total} of {total} · the full design"


def test_png_and_no_pyplot(plan_5088):
    import matplotlib.pyplot as plt

    before = plt.get_fignums()
    fig, _, _ = _draw(plan_5088, "5088-valid")
    assert to_png(fig)[:8] == b"\x89PNG\r\n\x1a\n"
    assert plt.get_fignums() == before


def test_cli_draws_a_solution(tmp_path):
    out = tmp_path / "5088.png"
    code = main(
        [
            str(PLANS / "plan-5088.json"),
            str(out),
            "--solution",
            str(SOLUTIONS / "5088-valid.json"),
        ]
    )
    assert code == 0 and out.read_bytes()[:4] == b"\x89PNG"


# --------------------------------------------------------------------------- per-hub summary


@pytest.mark.parametrize("name", ["5088-valid", "5088-two-hubs-valid", "5088-retrace-single-door"])
def test_summary_matches_the_checker_per_hub(plan_5088, name):
    sub = _sub(name)
    m = checker.metrics(sub.hubs, C)
    rows = hub_metrics(plan_5088, sub, m)
    rooms = {r.id: r for r in plan_5088.rooms}
    assert [r.hub_id for r in rows] == [h.hub_id for h in sub.hubs]
    for row, h in zip(rows, sub.hubs, strict=True):
        assert row.area_m2 == pytest.approx(sum(rooms[r].area_m2 for r in set(h.assigned_rooms)))
        assert row.n_nodes == len(set(h.assigned_nodes))
        per = m.per_hub[h.hub_id]
        assert (row.L, row.X, row.O, row.B) == (per.L, per.X, per.O, per.B)


def test_summary_line_lists_rooms_area_nodes_length_and_bends(plan_5088):
    sub = _sub("5088-two-hubs-valid")
    rows = hub_metrics(plan_5088, sub, checker.metrics(sub.hubs, C))
    line = hub_summary_line(rows[0], sub.hubs[0], plan_5088)
    assert line.startswith("H1: R01, R03 (living, kitchen) · ")
    assert f"{rows[0].area_m2:.1f} m²" in line and "2 nodes" in line
    assert f"{rows[0].L:.2f} m" in line and f"{rows[0].B} bends" in line


# --------------------------------------------------------------------------- 5924, from the solver


def test_5924_solution_has_three_zones_and_three_hubs():
    solve = pytest.importorskip("hackhive.solve", reason="solve() arrives with #16")
    plan = load_plan(PLANS / "plan-5924.json")
    result = solve.solve(plan, C)
    fig = draw_solution(plan, result.submission)
    ax = fig.axes[0]
    assert len({to_hex(p.get_facecolor()[:3]) for p in _gids(ax, "zone:")}) == 3
    assert len(_gids(ax, "hub:")) == 3


@pytest.mark.parametrize("name", [*VALID, "5088-hub-0.3m-from-wall"])
def test_order_numbers_keep_off_every_label(plan_5088, name):
    fig, ax, _ = _draw(plan_5088, name)
    r = fig.canvas.get_renderer()
    labels = [
        t.get_window_extent(r)
        for t in ax.texts
        if (t.get_gid() or "").startswith(("label:", "node-label:", "hub-label:", "entrance"))
    ]
    for t in _gids(ax, "order:"):
        box = t.get_window_extent(r)
        assert not any(box.overlaps(b) for b in labels), t.get_gid()


# --------------------------------------------------------------------------- the app's Best tab


def _app(pid: str):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.sidebar.selectbox(key="fixture").set_value(pid)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_best_tab_shows_the_overlay_and_the_per_hub_summary():
    at = _app("5088")
    at.sidebar.selectbox(key="solution").set_value("valid")  # the hand-made one, after solve()'s
    at.run()
    assert [t.label for t in at.tabs][:2] == ["Best", "Plan"]
    best = at.tabs[0]
    assert len(best.image) == 1
    (table,) = best.dataframe
    df = table.value
    assert list(df["Hub"]) == ["H1"]
    assert df.loc[0, "Rooms"] == "R01, R02, R03 (living, bedroom, kitchen)"
    assert df.loc[0, "Nodes"] == 3 and df.loc[0, "Bends"] == 12
    assert df.loc[0, "L (m)"] == pytest.approx(24.331, abs=1e-3)
    assert any("Reviewer checklist" in h.value for h in best.subheader)


def test_best_tab_step_slider_walks_the_rings():
    """#25: a slider under the overlay, from 0 (hubs and nodes) to every segment (the full
    design, its default)."""
    at = _app("5088")
    best = at.tabs[0]
    slider = best.slider(key="step")
    assert slider.min == 0 and slider.value == slider.max >= 1
    assert any(f"of {slider.max} · the full design" in c.value for c in best.caption)
    slider.set_value(0)
    at.run()
    assert not at.exception
    best = at.tabs[0]
    assert len(best.image) == 1
    assert any(f"Step 0 of {slider.max} · hubs and nodes only" in c.value for c in best.caption)


def test_best_tab_follows_the_solution_picker():
    at = _app("5088")
    at.sidebar.selectbox(key="solution").set_value("two hubs valid")
    at.run()
    assert list(at.tabs[0].dataframe[0].value["Hub"]) == ["H1", "H2"]


def test_best_tab_says_why_when_solve_writes_no_design():
    """C27, C43: 5924 scaled ×4 has a room over 100 m², so solve()'s gate writes no submission;
    the Best tab says so with the sidecar's status, and the Plan tab still draws the plan."""
    raw = json.loads((PLANS / "plan-5924.json").read_text(encoding="utf-8"))
    raw["area"] *= 4
    at = _app("5924")
    at.sidebar.radio(key="source").set_value("Upload")
    at.run()
    at.sidebar.file_uploader(key="upload").set_value(
        ("big.json", json.dumps(raw).encode(), "application/json")
    )
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    best = at.tabs[0]
    assert not best.image and not best.dataframe
    assert any(
        "no valid design" in w.value.lower() and "infeasible" in w.value for w in best.warning
    )
    assert len(at.tabs[1].image) == 1  # the Plan tab still draws the plan


# --------------------------------------------------------------------------- #71 review follow-ups


@pytest.mark.parametrize("name", [p.stem for p in sorted(SOLUTIONS.glob("5088-*.json"))])
def test_order_numbers_keep_off_violations_and_times_k_labels(plan_5088, name):
    """Review 1: a ✗ marker, a violation label or a ×k label never covers a visit-order number
    (5088-wall-crossing had an X on the circled 3)."""
    sub = _sub(name)
    violations = checker.check_rules(plan_5088, sub, C)
    try:
        fig = draw_solution(plan_5088, sub, violations=violations)
    except ValueError:
        pytest.skip("non-finite coordinates")
    ax = fig.axes[0]
    r = fig.canvas.get_renderer()
    from matplotlib.transforms import Bbox

    blockers = [a.get_window_extent(r) for a in _gids(ax, "violation:") if a in ax.texts]
    blockers += [a.get_window_extent(r) for a in _gids(ax, "overlap-label:")]
    for ln in (a for a in _gids(ax, "violation:") if a in ax.lines):  # each ✗ marker
        for x, y in zip(ln.get_xdata(), ln.get_ydata(), strict=True):
            dx, dy = ax.transData.transform((x, y))
            blockers.append(Bbox.from_extents(dx - 6, dy - 6, dx + 6, dy + 6))
    for t in _gids(ax, "order:"):
        box = t.get_window_extent(r)
        assert not any(box.overlaps(b) for b in blockers), (name, t.get_gid())


def test_best_tab_shows_a_drawing_error_instead_of_a_traceback(monkeypatch):
    """Review 2: Best is the landing tab, so any drawing error is an st.error there."""
    import streamlit as st

    import hackhive.viz

    def boom(*_a, **_k):
        raise RuntimeError("overlay exploded")

    monkeypatch.setattr(hackhive.viz, "draw_solution", boom)
    st.cache_data.clear()
    try:
        at = _app("5088")
        assert any("overlay exploded" in e.value for e in at.tabs[0].error)
    finally:
        st.cache_data.clear()


def test_cli_warns_when_the_submission_is_for_another_plan(tmp_path, capsys):
    """Nit 2: like `python -m hackhive.checker`, the overlay CLI warns on a plan_id mismatch."""
    out = tmp_path / "x.png"
    code = main(
        [str(PLANS / "plan-5924.json"), str(out), "--solution", str(SOLUTIONS / "5088-valid.json")]
    )
    err = capsys.readouterr().err
    assert code == 0 and "warning" in err and "PLAN_5088" in err and "PLAN_5924" in err


def test_summary_rooms_text_is_one_helper(plan_5088):
    """Nit 1: the table and the one-line summary share one rooms string."""
    from hackhive.ui import hub_table, rooms_text

    sub = _sub("5088-two-hubs-valid")
    m = checker.metrics(sub.hubs, C)
    rows = hub_metrics(plan_5088, sub, m)
    for row, table_row, h in zip(rows, hub_table(plan_5088, sub, m), sub.hubs, strict=True):
        text = rooms_text(h, plan_5088)
        assert table_row["Rooms"] == text
        assert hub_summary_line(row, h, plan_5088).startswith(f"{h.hub_id}: {text} · ")
        assert row.area_m2 == checker.assigned_area(h, {r.id: r for r in plan_5088.rooms})
