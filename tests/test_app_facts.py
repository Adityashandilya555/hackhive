"""Acceptance tests for #12: plan facts, node ids, the room-graph view and the reviewer panel."""

from pathlib import Path

import pytest
from matplotlib.colors import to_hex
from streamlit.testing.v1 import AppTest

from hackhive import checker
from hackhive.contracts import Constraints, Violation
from hackhive.graph import build_graph
from hackhive.io import load_plan, room_by_label
from hackhive.ui import checker_ready, plan_facts, solution_path, violation_line
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


def test_no_solution_yet_for_a_plan_without_one():
    assert solution_path("5924") is None
    assert "no solution yet" in _main_text(_run()).lower()


def test_reviewer_panel_waits_for_the_checker_or_shows_five_ticks():
    at = _pick(_run(), "5088")
    text = _main_text(at)
    assert "Reviewer checklist" in text
    if checker_ready() and solution_path("5088") is not None:
        assert text.count("✓") == 5
    else:
        assert "no solution yet" in text.lower() or "#11" in text


@pytest.mark.skipif(not checker_ready(), reason="checker.check_rules arrives with #11")
def test_checklist_text_comes_from_the_checker():
    import json

    from hackhive.contracts import Submission

    path = solution_path("5088")
    assert path is not None
    sub = Submission.model_validate(json.loads(path.read_text(encoding="utf-8")))
    plan = _plan("5088")
    violations = checker.check_rules(plan, sub, Constraints())
    lines = checker.checklist(violations, checker.metrics(sub.hubs))
    text = _main_text(_pick(_run(), "5088"))
    for item in lines:
        assert (item if isinstance(item, str) else item[0]) in text
