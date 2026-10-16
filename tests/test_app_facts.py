"""Acceptance tests for #12: plan facts, node ids, the room-graph view and the reviewer panel."""

from pathlib import Path

import pytest
from matplotlib.colors import to_hex
from streamlit.testing.v1 import AppTest

from hackhive import checker
from hackhive.contracts import Constraints, Violation
from hackhive.graph import build_graph
from hackhive.io import load_plan, room_by_label
from hackhive.ui import (
    checklist_line,
    load_submission,
    plan_facts,
    solution_label,
    solution_paths,
    violation_line,
)
from hackhive.viz import LINK_DOOR, LINK_OPENING, draw_graph, draw_layers, draw_nodes

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")
PLANS = ROOT / "tests" / "fixtures" / "plans"
TIMEOUT = 60


def _plan(pid: str):
    return load_plan(PLANS / f"plan-{pid}.json")


def _run(at: AppTest | None = None) -> AppTest:
    at = at or AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _pick(at: AppTest, pid: str) -> AppTest:
    at.sidebar.selectbox(key="fixture").set_value(pid)
    return _run(at)


def _main_text(at: AppTest) -> str:
    parts = [m.value for m in at.main.markdown] + [c.value for c in at.main.caption]
    return "\n".join(parts + [h.value for h in at.main.subheader])


def _edges(ax) -> list[str]:
    return [ln.get_gid() for ln in ax.lines if (ln.get_gid() or "").startswith("edge:")]


# --------------------------------------------------------------------------- facts


def test_facts_for_5924():
    assert "150.0 m² · 5 nodes · LB 2" in plan_facts(_plan("5924"), Constraints())


def test_app_shows_the_facts_for_5924():
    text = _main_text(_run())
    assert "150.0 m² · 5 nodes · LB 2" in text
    assert "m/unit" in text


# --------------------------------------------------------------------------- nodes


def test_every_node_is_drawn_with_its_id():
    plan = _plan("5924")
    ax = draw_layers(plan).axes[0]
    draw_nodes(plan, ax)
    texts = {t.get_text() for t in ax.texts}
    assert {n.id for n in plan.nodes} <= texts
    dots = [ln for ln in ax.lines if (ln.get_gid() or "").startswith("node:")]
    assert len(dots) == len(plan.nodes)


@pytest.mark.parametrize("pid", ["1293", "13344", "2814", "3086", "5088", "576", "5924"])
def test_node_ids_never_cover_a_room_label(pid):
    plan = _plan(pid)
    fig = draw_layers(plan)
    ax = fig.axes[0]
    draw_nodes(plan, ax)
    r = fig.canvas.get_renderer()
    rooms = [t.get_window_extent(r) for t in ax.texts if (t.get_gid() or "").startswith("label:")]
    ids = [t for t in ax.texts if (t.get_gid() or "").startswith("node-label:")]
    assert len(ids) == len(plan.nodes)
    clashes = [t.get_text() for t in ids if any(t.get_window_extent(r).overlaps(b) for b in rooms)]
    assert not clashes, clashes


# --------------------------------------------------------------------------- room graph


def test_2814_kitchen_has_no_edge_in_the_graph_view():
    plan = _plan("2814")
    G = build_graph(plan)
    ax = draw_layers(plan).axes[0]
    draw_graph(plan, G, ax)
    kitchen = room_by_label(plan, "kitchen_0").id
    edges = _edges(ax)
    assert edges, "the other rooms are linked"
    assert not [e for e in edges if kitchen in e.split(":")[1].split("-")]


def test_door_links_are_blue_and_openings_green_and_dashed():
    plan = _plan("1293")  # has an open passage
    G = build_graph(plan)
    ax = draw_layers(plan).axes[0]
    draw_graph(plan, G, ax)
    kinds = {d["kind"] for _, _, d in G.edges(data=True)}
    assert {"door", "opening"} <= kinds
    lines = {ln.get_gid(): ln for ln in ax.lines if (ln.get_gid() or "").startswith("edge:")}
    for u, v, d in G.edges(data=True):
        drawn = [ln for gid, ln in lines.items() if gid.startswith(f"edge:{u}-{v}")]
        assert drawn, (u, v)
        for ln in drawn:
            if d["kind"] == "door":
                assert to_hex(ln.get_color()) == LINK_DOOR
            else:
                assert to_hex(ln.get_color()) == LINK_OPENING
                assert ln.get_linestyle() == "--"
    legend = ax.get_legend()
    assert legend is not None and legend.get_title().get_text() == "Room graph"


def test_one_door_line_per_door():
    plan = _plan("5924")
    G = build_graph(plan)
    ax = draw_layers(plan).axes[0]
    draw_graph(plan, G, ax)
    n_doors = sum(len(d.get("door_ids", [])) for _, _, d in G.edges(data=True))
    assert len(_edges(ax)) == n_doors


def test_app_graph_toggle_draws_2814():
    at = _pick(_run(), "2814")
    at.sidebar.toggle(key="show_graph").set_value(True)
    _run(at)
    assert len(at.main.image) == 1


# --------------------------------------------------------------------------- reviewer panel


def test_violation_line_is_red_with_its_rule_number():
    v = Violation(rule=6, severity="error", message="hub 0.30 m from a wall", hub_id="H1")
    line = violation_line(v)
    assert line.startswith(":red[") and "Rule 6" in line and "H1" in line and "0.30 m" in line
    s = Violation(rule=6, severity="strict", message="hub 0.45 m from a door edge")
    assert violation_line(s).startswith(":orange[") and "strict" in violation_line(s)


def test_checklist_lines_keep_the_checkers_text_and_colour_only_the_crosses():
    assert checklist_line("✓ each zone connected") == "✓ each zone connected"
    assert checklist_line("✗ axis-aligned") == ":red[✗ axis-aligned]"


def test_solutions_are_found_for_5088_only_with_valid_first():
    assert solution_paths("5924") == []
    paths = solution_paths("5088")
    assert paths[0].name == "5088-valid.json"
    assert {"5088-hub-0.3m-from-wall.json", "5088-wall-crossing.json"} <= {p.name for p in paths}
    assert all(p.name.startswith("5088-") for p in paths)
    assert solution_label(paths[0]) == "valid"


def test_no_solution_yet_for_a_plan_without_one():
    assert "no solution yet" in _main_text(_run()).lower()


def test_5088_checklist_shows_five_ticks_from_the_checker():
    at = _pick(_run(), "5088")
    text = _main_text(at)
    assert "Reviewer checklist" in text
    rep = checker.report(_plan("5088"), load_submission(solution_paths("5088")[0]), Constraints())
    assert rep.verdict == "valid" and len(rep.lines) == 5
    for line in rep.lines:  # the panel shows the checker's own words, nothing of ours
        assert line in text
    assert text.count("✓") >= 5 and "✗" not in text
    assert not at.error


def test_broken_solution_lists_its_violations_in_red_with_the_rule_number():
    at = _pick(_run(), "5088")
    at.main.selectbox(key="solution").set_value("hub 0.3m from wall")
    _run(at)
    text = _main_text(at)
    assert ":red[✗ **Rule 6** · H1 · error:" in text
    assert "invalid" in text.lower()


def test_wall_crossing_shows_rule_5_in_red():
    at = _pick(_run(), "5088")
    at.main.selectbox(key="solution").set_value("wall crossing")
    _run(at)
    text = _main_text(at)
    assert ":red[✗ **Rule 5**" in text and ":red[✗ axis-aligned" in text
