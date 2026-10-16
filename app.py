"""HackHive demo app: pick a plan, see it drawn, and see the organisers' rules, locked (#7);
plan facts, node ids, the room graph and the reviewer checklist (#12); the Best tab shows
solve()'s design (#16) with its overlay and summary (#17).

Run it with `uv run streamlit run app.py`. Later issues add the what-if sidebar (#30), the
arrangements gallery and the batch leaderboard.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import networkx as nx
import streamlit as st

from hackhive import checker, graph
from hackhive.contracts import Constraints, Plan, Sidecar
from hackhive.io import load_plan
from hackhive.solve import Result, solve, submit
from hackhive.ui import (
    DEFAULT_FIXTURE,
    RESPLAN_JSONL,
    case_tags,
    checklist_line,
    fixture_ids,
    fixture_label,
    fixture_path,
    hub_table,
    load_submission,
    metrics_line,
    plan_facts,
    plan_from_bytes,
    preset_rows,
    resplan_raw,
    room_counts,
    solution_label,
    solution_paths,
    status_banner,
    step_caption,
    submission_mismatch,
    tag_chips,
    verdict_line,
    violation_line,
)
from hackhive.viz import (
    draw_graph,
    draw_layers,
    draw_nodes,
    draw_solution,
    segment_count,
    to_png,
)

PRESET = Constraints.problem_statement()
FIXTURE, RESPLAN, UPLOAD = "Test plans", "ResPlan id", "Upload"
BEST = "best design (solve)"
SOLVED_DIR = Path(tempfile.gettempdir()) / "hackhive-app"  # solve()'s files, a folder per input


# --------------------------------------------------------------------------- cached work
# Cached on (source, key, content), never on a Figure: cache_data pickles what it returns, and
# PNG bytes and the Plan model both pickle cleanly. The Plan is cached so later overlays reuse it.


@st.cache_data(max_entries=64, show_spinner="Loading the plan…")
def load(source: str, key: str, content: bytes | None = None) -> tuple[Plan, nx.Graph]:
    """The plan and its room graph, built once: `build_graph` also fills the plan's openings,
    door rooms and graph warnings, so both are cached together."""
    if source == UPLOAD:
        plan = plan_from_bytes(content or b"")
    elif source == RESPLAN:
        plan = load_plan(resplan_raw(RESPLAN_JSONL, key))
    else:
        plan = load_plan(fixture_path(key))
    return plan, graph.build_graph(plan)


@st.cache_data(max_entries=64, show_spinner="Drawing…")
def picture(source: str, key: str, content: bytes | None = None, show: bool = False) -> bytes:
    plan, G = load(source, key, content)
    fig = draw_layers(plan)
    ax = fig.axes[0]
    if show:
        draw_graph(plan, G, ax)
    draw_nodes(plan, ax)
    return to_png(fig)


@st.cache_data(max_entries=64, show_spinner="Solving…")
def solved(source: str, key: str, content: bytes | None) -> tuple[str | None, str]:
    """`solve()` (#16) through the submit gate (C43), into a temp folder per input: the path of
    the submission it wrote (None when the gate refused one) and the sidecar as JSON (#83)."""
    plan, _ = load(source, key, content)
    result = solve(plan, PRESET)
    digest = hashlib.sha256(f"{source}\0{key}\0".encode() + (content or b"")).hexdigest()[:16]
    written = submit(result, SOLVED_DIR / digest)
    sub = next((p for p in written if not p.name.endswith(".sidecar.json")), None)
    return (str(sub) if sub else None), result.sidecar.model_dump_json()


@st.cache_data(max_entries=256, show_spinner="Drawing the solution…")
def overlay(
    source: str, key: str, content: bytes | None, solution: str, step: int | None = None
) -> bytes:
    """`viz.draw_solution` of a solution file, with the checker's violations in red (#17), up to
    `step` segments (#25). Each step's PNG is cached, so sliding back and forth is instant."""
    plan, _ = load(source, key, content)
    rep = checked(source, key, content, solution)
    sub = load_submission(Path(solution))
    return to_png(draw_solution(plan, sub, rep.violations, step=step))


@st.cache_data(max_entries=64, show_spinner="Checking…")
def checked(source: str, key: str, content: bytes | None, solution: str) -> checker.Report:
    """`checker.report` on a solution file: the six rules, the metrics, the checklist. The
    checker builds its own room graph from the plan, on purpose: it trusts nothing of ours."""
    plan, _ = load(source, key, content)
    return checker.report(plan, load_submission(Path(solution)), PRESET)


# --------------------------------------------------------------------------- sidebar


def pick_plan() -> tuple[str, str, bytes | None] | None:
    """The chosen plan as (source, key, content), or None while an upload is pending."""
    st.header("Plan")
    sources = [FIXTURE, *([RESPLAN] if RESPLAN_JSONL.exists() else []), UPLOAD]
    source = st.radio("Source", sources, key="source", horizontal=True)
    if source == FIXTURE:
        ids = fixture_ids()
        plan_id = st.selectbox(
            "Test plan",
            ids,
            index=ids.index(DEFAULT_FIXTURE),
            format_func=fixture_label,
            key="fixture",
        )
        return source, plan_id, None
    if source == RESPLAN:
        plan_id = st.text_input("ResPlan plan id", value=DEFAULT_FIXTURE, key="resplan_id")
        return source, plan_id.strip(), None
    up = st.file_uploader("Plan JSON", type=["json"], key="upload")
    if up is None:
        return None
    return source, up.name, up.getvalue()


def _solution_name(path: Path) -> str:
    """By where the file is, not a captured path: a widget may keep an older run's format_func."""
    return BEST if SOLVED_DIR in path.parents else solution_label(path)


def pick_solution(plan: Plan, best: Path | None) -> Path | None:
    """The solution to show: solve()'s design first (#16), then the plan's hand-made fixtures
    (#11), valid first. None only when solve() wrote no submission and there are no fixtures."""
    solutions = [*([best] if best else []), *solution_paths(plan.plan_id.removeprefix("PLAN_"))]
    if not solutions:
        return None
    names = [str(p) for p in solutions]
    if st.session_state.get("solution_options") != names:  # another plan: start at its best,
        st.session_state.pop("solution", None)  # never keep the last plan's file selected
        st.session_state["solution_options"] = names
    return st.selectbox(
        "Solution",
        solutions,
        format_func=_solution_name,
        key="solution",
        help="solve()'s design for this plan, then, for 5088, the checker's hand-made "
        "submissions (#11): the valid one and one broken variant per rule.",
    )


def best_tab(
    source: str, key: str, content: bytes | None, plan: Plan, path: Path | None, side: Sidecar
) -> None:
    """Slide 5's expected solution: the status banner (#83), the overlay, the per-hub summary
    (C25) and the reviewer checklist (C26), all from the submission and the checker mirror."""
    banner(side, plan)
    if path is None:  # the gate wrote nothing: an invalid design scores zero (C43)
        st.caption("No design was submitted. The Plan tab still shows the plan.")
        return
    try:
        sub = load_submission(path)
        rep = checked(source, key, content, str(path))
    except ValueError as e:
        st.error(f"Can't check {path.name}. {e}")
        return
    picture_col, panel = st.columns([5, 3], gap="large")
    with picture_col:
        if mismatch := submission_mismatch(sub, plan):
            st.warning(mismatch)
        total = segment_count(sub)
        # #25: walk the rings one segment at a time; the default is the full design
        step = st.slider(
            "Step through the rings",
            min_value=0,
            max_value=max(total, 1),
            value=max(total, 1),
            key="step",
            help="0 = hubs and nodes only; each step adds one cable segment, ring by ring.",
            disabled=total == 0,
        )
        try:
            png = overlay(source, key, content, str(path), None if step >= total else step)
        except Exception as e:  # Best is the landing tab: a drawing error is never a traceback
            st.error(f"Can't draw this solution. {type(e).__name__}: {e}")
            png = None
        if png is not None:
            st.image(png, width="stretch")
        st.caption(step_caption(sub, step))
        st.subheader("Per-hub summary")
        if rep.metrics is None:
            st.caption("Metrics not computed: a coordinate isn't finite (rule 4).")
        else:
            st.dataframe(hub_table(plan, sub, rep.metrics), hide_index=True)
    with panel:
        reviewer_panel(rep)


def banner(side: Sidecar, plan: Plan | None) -> None:
    kind, text = status_banner(side, plan)
    getattr(st, kind)(text)


def reviewer_panel(rep: checker.Report) -> None:
    """Slide 5's "what a reviewer confirms", rendered only from the checker mirror (C26, #11):
    its checklist lines verbatim, its verdict and metrics, and every violation in red."""
    st.subheader("Reviewer checklist")
    for line in rep.lines:
        st.markdown(checklist_line(line))
    st.markdown(verdict_line(rep.verdict))
    st.caption(metrics_line(rep.metrics))
    if rep.violations:
        st.markdown("**Violations**")
        for v in rep.violations:
            st.markdown(violation_line(v))


def locked_preset(c: Constraints) -> None:
    """The organisers' rules, read-only. What-ifs (#30) will live in their own expander."""
    st.subheader(":material/lock: Problem statement")
    st.caption("The organisers' rules. Locked: every result on this page is judged by them.")
    st.number_input(
        "Hub area cap (m²)", value=int(c.max_area_m2), disabled=True, key="ps_max_area_m2"
    )
    st.number_input("Nodes per hub", value=c.max_nodes, disabled=True, key="ps_max_nodes")
    st.number_input(
        "Hub to wall, at least (m)",
        value=c.hub_wall_clearance_m,
        format="%.1f",
        disabled=True,
        key="ps_hub_wall_clearance_m",
    )
    st.number_input(
        "Tolerance (mm)",
        value=int(round(c.tolerance_m * 1000)),
        disabled=True,
        key="ps_tolerance_mm",
    )
    rows = dict(preset_rows(c))
    st.markdown(f"**Cost** `{rows['Cost']}`")
    st.caption(
        f"Each hub: at most {rows['Hub area cap']} and {rows['Nodes per hub']}, "
        f"{rows['Hub to wall']} from walls. Ranking: {rows['Ranking']}."
    )


# --------------------------------------------------------------------------- page

st.set_page_config(page_title="HackHive · hub planner", layout="wide")

with st.sidebar:
    choice = pick_plan()
    solution_slot = st.container()  # filled once the plan is loaded
    show_graph = st.toggle("Show room graph", key="show_graph", help="On the Plan tab.")
    st.divider()
    locked_preset(PRESET)

st.title("HackHive")
if choice is None:
    st.caption("Upload a plan JSON in the sidebar: a ResPlan plan or one of our vault files.")
    st.stop()

source, key, content = choice
try:
    plan, G = load(source, key, content)
except (OSError, ValueError) as e:
    banner(Sidecar(plan_id=key, status="input_error", reason=str(e)), None)
    st.stop()
except Exception as e:  # a plan we can parse but not handle further: say so, never a traceback
    banner(Sidecar(plan_id=key, status="input_error", reason=f"{type(e).__name__}: {e}"), None)
    st.stop()

try:
    best_path, side_json = solved(source, key, content)
    side = Sidecar.model_validate_json(side_json)
except Exception as e:  # solve() never raises for a loaded plan; the landing tab never tracebacks
    best_path = None
    side = Sidecar(plan_id=plan.plan_id, status="infeasible", reason=f"{type(e).__name__}: {e}")
with solution_slot:
    solution = pick_solution(plan, Path(best_path) if best_path else None)

st.caption(f"**{plan.plan_id}** · {room_counts(plan)}")
try:
    best_sub = load_submission(Path(best_path)) if best_path else None
except ValueError:  # a stale cached path (temp dir cleaned): the design tags just drop out
    best_sub = None
if tags := case_tags(plan, G, Result(plan, best_sub, side)):
    st.markdown(tag_chips(tags), help="\n".join(f"{t.label}: {t.look_for}" for t in tags))
best, plan_tab = st.tabs(["Best", "Plan"])
with best:
    best_tab(source, key, content, plan, solution, side)
with plan_tab:
    drawing, panel = st.columns([5, 3], gap="large")
    with drawing:
        try:
            st.image(picture(source, key, content, show_graph), width="stretch")
        except Exception as e:  # drawing a plan we loaded: never a traceback
            st.error(f"Can't draw this plan. {type(e).__name__}: {e}")
        if plan.warnings:
            with st.expander(f"{len(plan.warnings)} plan warning(s)"):
                for w in plan.warnings:
                    st.markdown(f"- {w}")
    with panel:
        st.subheader("Facts")
        st.markdown(f"**{plan_facts(plan, PRESET)}**")
        st.markdown(f"{plan.m_per_unit:.4f} m/unit · walls {plan.wall_depth_m:.2f} m")
        st.caption("Eligible area · nodes · LB = the fewest hubs any zoning can use.")
