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
    metrics_line,
    plan_facts,
    solution_label,
    solution_paths,
    submission_mismatch,
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
    assert sum(e.split(":")[2].startswith("door") for e in _edges(ax)) == n_doors


def test_app_graph_toggle_draws_2814():
    at = _pick(_run(), "2814")
    at.sidebar.toggle(key="show_graph").set_value(True)
    _run(at)
    assert len(at.main.image) == 2  # Best's design and the Plan tab with its graph


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


@pytest.mark.parametrize("pid", ["1293", "13344", "2814", "3086", "5088", "576", "5924"])
def test_best_tab_shows_solves_design_for_every_fixture(pid):
    """#18 (C26): the Best tab runs solve() (#16) on the picked plan and shows its design, first
    in the Solution list, with the checker's verdict: never "no solution yet" (#72 is merged)."""
    at = _pick(_run(), pid)
    text = _main_text(at)
    assert "no solution yet" not in text.lower()
    assert at.sidebar.selectbox(key="solution").index == 0
    assert "Reviewer checklist" in text and "Per-hub summary" in text
    assert "valid" in text and "invalid" not in text
    assert at.main.image, "the design overlay is drawn"


def test_5088_checklist_shows_five_ticks_from_the_checker():
    at = _pick(_run(), "5088")
    at.sidebar.selectbox(key="solution").set_value("valid")  # the hand-made one, after solve()'s
    _run(at)
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
    at.sidebar.selectbox(key="solution").set_value("hub 0.3m from wall")
    _run(at)
    text = _main_text(at)
    assert ":red[✗ **Rule 6** · H1 · error:" in text
    assert "invalid" in text.lower()


def test_wall_crossing_shows_rule_5_in_red():
    at = _pick(_run(), "5088")
    at.sidebar.selectbox(key="solution").set_value("wall crossing")
    _run(at)
    text = _main_text(at)
    assert ":red[✗ **Rule 5**" in text and ":red[✗ axis-aligned" in text


# --------------------------------------------------------------------------- #69 review follow-ups


@pytest.mark.parametrize("pid", ["**", "*", "../plans/plan", "[50]88", "5088-valid", ""])
def test_solution_lookup_treats_the_plan_id_as_text_not_a_pattern(pid):
    """Follow-up 1: an uploaded plan's id must never act as a glob or a path."""
    assert solution_paths(pid) == []


def test_any_error_while_loading_an_upload_is_shown_not_raised(monkeypatch):
    """Follow-up 2: the graph build (or anything else) failing is an st.error, not a traceback."""
    import hackhive.graph

    def boom(*_a, **_k):
        raise RuntimeError("graph exploded")

    monkeypatch.setattr(hackhive.graph, "build_graph", boom)
    content = (PLANS / "plan-5088.json").read_bytes() + b"\n "  # a fresh cache key
    at = _run()
    at.sidebar.radio(key="source").set_value("Upload")
    _run(at)
    at.sidebar.file_uploader(key="upload").set_value(("p.json", content, "application/json"))
    _run(at)
    assert at.error and "graph exploded" in at.error[0].value
    assert not at.main.image


def test_a_submission_for_another_plan_is_flagged():
    """Follow-up 3: the panel warns when the submission's plan_id isn't the plan's."""
    sub = load_submission(solution_paths("5088")[0])
    assert submission_mismatch(sub, _plan("5088")) is None
    msg = submission_mismatch(sub, _plan("5924"))
    assert msg is not None and "PLAN_5088" in msg and "PLAN_5924" in msg


def test_streamlit_secrets_are_git_ignored():
    """Follow-up 4: .streamlit/ is committed, so its secrets file must be ignored."""
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".streamlit/secrets.toml" in lines


def _unescaped(text: str, ch: str) -> int:
    return len([i for i, c in enumerate(text) if c == ch and (i == 0 or text[i - 1] != "\\")])


def test_violation_text_cannot_break_out_of_the_red_span():
    """Follow-up 5: brackets, $, * and _ in a message are escaped inside :red[...]."""
    v = Violation(
        rule=4,
        severity="error",
        message="closed_sequence must start at H1: ['H1', 'N1'] *x* $5 _y_ ]",
        hub_id="H[1]",
    )
    line = violation_line(v)
    assert line.startswith(":red[") and line.endswith("]")
    assert _unescaped(line, "[") == 1 and _unescaped(line, "]") == 1
    assert _unescaped(line, "$") == 0 and r"\*x\*" in line and r"\_y\_" in line
    assert "**Rule 4**" in line  # our own markup still works


def test_app_and_cli_share_one_metrics_format(capsys):
    """Follow-up 6: the panel's metrics line is the checker CLI's, character for character."""
    plan = _plan("5088")
    rep = checker.report(plan, load_submission(solution_paths("5088")[0]), Constraints())
    checker.main([str(PLANS / "plan-5088.json"), str(solution_paths("5088")[0])])
    out = capsys.readouterr().out
    assert metrics_line(rep.metrics) == checker.metrics_text(rep.metrics)
    assert checker.metrics_text(rep.metrics) in out


@pytest.mark.parametrize("pid", ["1293", "13344", "2814", "3086", "5088", "576", "5924"])
def test_hollow_points_stay_off_room_labels(pid):
    """Follow-up 7: an excluded room's hollow point never sits on its own label."""
    plan = _plan(pid)
    fig = draw_layers(plan)
    ax = fig.axes[0]
    draw_graph(plan, build_graph(plan), ax)
    r = fig.canvas.get_renderer()
    labels = {
        t.get_gid().removeprefix("label:"): t.get_window_extent(r)
        for t in ax.texts
        if (t.get_gid() or "").startswith("label:")
    }
    hollows = [ln for ln in ax.lines if (ln.get_gid() or "").startswith("hollow:")]
    assert hollows
    for ln in hollows:
        room = ln.get_gid().removeprefix("hollow:")
        x, y = ax.transData.transform((ln.get_xdata()[0], ln.get_ydata()[0]))
        assert not labels[room].contains(x, y), room


def test_no_node_legend_entry_only_when_a_hollow_point_is_drawn():
    plan = _plan("5924")
    G = build_graph(plan)
    eligible = G.subgraph([r.id for r in plan.rooms if r.eligible])
    ax = draw_layers(plan).axes[0]
    draw_graph(plan, eligible, ax)
    assert "no node" not in [t.get_text() for t in ax.get_legend().get_texts()]
    assert not [ln for ln in ax.lines if (ln.get_gid() or "").startswith("hollow:")]


@pytest.mark.parametrize("pid", ["1293", "13344", "2814", "3086", "5088", "576", "5924"])
def test_node_ids_avoid_the_entrance_and_other_dots(pid):
    """Follow-up 8: node ids also keep off the "Entrance" tag and every node dot."""
    plan = _plan(pid)
    fig = draw_layers(plan)
    ax = fig.axes[0]
    draw_nodes(plan, ax)
    r = fig.canvas.get_renderer()
    entrance = [t.get_window_extent(r) for t in ax.texts if t.get_gid() == "entrance"]
    dots = [ln.get_window_extent(r) for ln in ax.lines if (ln.get_gid() or "").startswith("node:")]
    for t in (t for t in ax.texts if (t.get_gid() or "").startswith("node-label:")):
        box = t.get_window_extent(r)
        assert not any(box.overlaps(e) for e in entrance), t.get_text()
        assert not any(box.overlaps(d) for d in dots), t.get_text()


def test_one_passage_line_per_open_passage():
    """Follow-up 9: door and passage lines are counted separately (1293 has an opening)."""
    plan = _plan("1293")
    G = build_graph(plan)
    ax = draw_layers(plan).axes[0]
    draw_graph(plan, G, ax)
    tags = [e.split(":")[2] for e in _edges(ax)]
    n_doors = sum(len(d.get("door_ids", [])) for _, _, d in G.edges(data=True))
    n_openings = sum(d["kind"] == "opening" for _, _, d in G.edges(data=True))
    assert n_openings >= 1
    assert sum(t.startswith("door") for t in tags) == n_doors
    assert tags.count("opening") == n_openings


def test_an_unknown_edge_kind_is_drawn_and_named_in_the_legend():
    """Follow-up 10: a future edge kind is visible and labelled, never silently grey."""
    import networkx as nx

    plan = _plan("5088")
    G = nx.Graph()
    G.add_edge("R01", "R02", kind="mystery")
    ax = draw_layers(plan).axes[0]
    draw_graph(plan, G, ax)
    assert _edges(ax) == ["edge:R01-R02:mystery"]
    assert "mystery" in [t.get_text() for t in ax.get_legend().get_texts()]


def test_the_app_builds_the_room_graph_once_per_plan(monkeypatch):
    """Follow-up 10: load() keeps the graph, so the picture doesn't build it again. solve() and
    the checker build their own on purpose (they trust nothing of the app's), so only the app
    script's own calls count."""
    import sys

    import hackhive.graph

    calls = []
    real = hackhive.graph.build_graph

    def counting(*a, **k):
        calls.append(sys._getframe(1).f_globals.get("__name__"))
        return real(*a, **k)

    monkeypatch.setattr(hackhive.graph, "build_graph", counting)
    content = (PLANS / "plan-5924.json").read_bytes() + b"\n\n "  # a fresh cache key
    at = _run()
    at.sidebar.toggle(key="show_graph").set_value(True)
    at.sidebar.radio(key="source").set_value("Upload")
    _run(at)
    at.sidebar.file_uploader(key="upload").set_value(("p.json", content, "application/json"))
    _run(at)
    assert at.main.image and calls.count("__main__") == 1, calls


# --------------------------------------------------------------------------- #83 banners and chips


def _upload_raw(at: AppTest, name: str, content: bytes) -> AppTest:
    at.sidebar.radio(key="source").set_value("Upload")
    _run(at)
    at.sidebar.file_uploader(key="upload").set_value((name, content, "application/json"))
    return _run(at)


def test_banner_5924_is_ok_and_chips_show():
    """The default plan: ok since #20's ring choice, with its case chips."""
    at = _run()
    assert any("✓" in s.value for s in at.main.success)
    assert any("one-node ring" in m.value for m in at.main.markdown)


def test_banner_13344_is_strict_only():
    at = _run()
    at.sidebar.selectbox(key="fixture").set_value("13344")
    _run(at)
    assert any("strict" in w.value and "retrace" in w.value for w in at.main.warning)


def test_banner_5088_is_ok():
    at = _run()
    at.sidebar.selectbox(key="fixture").set_value("5088")
    _run(at)
    assert any("✓" in s.value for s in at.main.success)


def test_banner_area_x4_upload_is_red_and_plan_still_draws():
    import json

    raw = json.loads((PLANS / "plan-5924.json").read_text(encoding="utf-8"))
    raw["area"] = raw["area"] * 4
    at = _upload_raw(_run(), "big.json", json.dumps(raw).encode())
    assert any("> 100" in e.value and "m²" in e.value for e in at.main.error)
    assert at.main.image  # the Plan tab still draws


def test_banner_input_error_on_an_unreadable_upload():
    at = _upload_raw(_run(), "bad.json", b"not json at all")
    assert any("input_error" in e.value for e in at.main.error)
