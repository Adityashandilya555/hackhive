"""HackHive demo app: pick a plan, see it drawn, and see the organisers' rules, locked (#7);
plan facts, node ids, the room graph and the reviewer checklist (#12); the Best tab shows
solve()'s design (#16) with its overlay and summary (#17); the What-if expander (#30) re-solves
under changed settings, labelled WHAT-IF on every view and never written as a submission.

Run it with `uv run streamlit run app.py`. Later issues add the arrangements gallery and the
batch leaderboard.
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
from hackhive.solve import Result, file_stem, solve, submit
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
    md_text,
    metrics_line,
    optimality_badge,
    plan_facts,
    plan_from_bytes,
    preset_rows,
    proven_min_hubs,
    readings_rows,
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
    label_what_if,
    segment_count,
    to_png,
)
from hackhive.what_if import WHAT_IF, what_if_changes

PRESET = Constraints.problem_statement()
FIXTURE, RESPLAN, UPLOAD = "Test plans", "ResPlan id", "Upload"
BEST = "best design (solve)"
BEST_WHAT_IF = "what-if design (solve)"
SOLVED_DIR = Path(tempfile.gettempdir()) / "hackhive-app"  # solve()'s files, a folder per input
WHAT_IF_COPY = ".what-if.json"  # a what-if design, kept only so the app can draw it (#30)
# the What-if expander's widgets (#30), each starting at the problem statement's value
WHAT_IF_DEFAULTS = {
    "wi_max_area_m2": float(PRESET.max_area_m2),
    "wi_hub_wall_clearance_m": float(PRESET.hub_wall_clearance_m),
    "wi_zone_links_via_excluded": PRESET.zone_links_via_excluded,
    "wi_route_through_other_rooms": PRESET.route_through_other_rooms,
}


# --------------------------------------------------------------------------- cached work
# Cached on (source, key, content), never on a Figure: cache_data pickles what it returns, and
# PNG bytes and the Plan model both pickle cleanly. The Plan is cached so later overlays reuse it.
# Everything a setting changes is also keyed on `settings`, the Constraints as JSON (#30).


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
def solved(source: str, key: str, content: bytes | None, settings: str) -> tuple[str | None, str]:
    """`solve()` (#16) through the submit gate (C43), into a temp folder per input and settings:
    the path of the design to draw (None when there is none) and the sidecar as JSON (#83).

    Under the problem statement that's the submission `submit` wrote. A what-if design (#30) is
    never written as a submission: `submit` writes only its sidecar, and the app keeps a
    `*.what-if.json` display copy in a `what-if` folder, only to draw it."""
    plan, _ = load(source, key, content)
    result = solve(plan, Constraints.model_validate_json(settings))
    raw = f"{source}\0{key}\0{settings}\0".encode() + (content or b"")
    out = SOLVED_DIR / hashlib.sha256(raw).hexdigest()[:16]
    written = submit(result, out)
    sub = next((p for p in written if not p.name.endswith(".sidecar.json")), None)
    if result.what_if and result.submission is not None:
        sub = out / "what-if" / f"{file_stem(result.sidecar.plan_id)}{WHAT_IF_COPY}"
        sub.parent.mkdir(parents=True, exist_ok=True)
        sub.write_text(result.submission.model_dump_json(indent=2), encoding="utf-8")
    return (str(sub) if sub else None), result.sidecar.model_dump_json()


@st.cache_data(max_entries=256, show_spinner="Drawing the solution…")
def overlay(
    source: str,
    key: str,
    content: bytes | None,
    solution: str,
    settings: str,
    step: int | None = None,
) -> bytes:
    """`viz.draw_solution` of a solution file, with the checker's violations in red (#17), up to
    `step` segments (#25), and a red WHAT-IF band naming any what-if (#30). Each step's PNG is
    cached, so sliding back and forth is instant."""
    plan, _ = load(source, key, content)
    rep = checked(source, key, content, solution, settings)
    sub = load_submission(Path(solution))
    fig = draw_solution(plan, sub, rep.violations, step=step)
    label_what_if(fig, what_if_changes(Constraints.model_validate_json(settings)))
    return to_png(fig)


@st.cache_data(max_entries=64, show_spinner="Checking…")
def checked(
    source: str,
    key: str,
    content: bytes | None,
    solution: str,
    settings: str,
    proven_min: int | None = None,
) -> checker.Report:
    """`checker.report` on a solution file under the active settings: the six rules, the
    metrics, the checklist. The checker builds its own room graph from the plan, on purpose: it
    trusts nothing of ours but solve()'s proven hub minimum (`proven_min`, #24 T7)."""
    plan, _ = load(source, key, content)
    c = Constraints.model_validate_json(settings)
    return checker.report(plan, load_submission(Path(solution)), c, proven_min)


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
    if SOLVED_DIR not in path.parents:
        return solution_label(path)
    return BEST_WHAT_IF if path.name.endswith(WHAT_IF_COPY) else BEST


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
    source: str,
    key: str,
    content: bytes | None,
    plan: Plan,
    G: nx.Graph,
    path: Path | None,
    side: Sidecar,
    c: Constraints,
    counting: bool,
) -> None:
    """Slide 5's expected solution: the status banner (#83), the overlay, the per-hub summary
    (C25) and the reviewer checklist (C26), all from the submission and the checker mirror,
    judged under `c`; with `counting`, J under both readings of the counting rules (D3, #30)."""
    settings = c.model_dump_json()
    banner(side, plan)
    if badge := optimality_badge(side, plan, G, c):  # #24: proof of the hub count, never of J
        getattr(st, badge.kind)(badge.text)
        if badge.explain:
            with st.expander("Why more hubs than the lower bound?"):
                st.markdown(badge.explain)
    if path is None:  # the gate wrote nothing: an invalid design scores zero (C43)
        st.caption("No design was submitted. The Plan tab still shows the plan.")
        return
    try:
        sub = load_submission(path)
        rep = checked(source, key, content, str(path), settings, proven_min_hubs(side))
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
            png = overlay(
                source, key, content, str(path), settings, None if step >= total else step
            )
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
        if counting:
            st.subheader("Counting rules (D3)")
            st.caption(
                "The same design under both readings of slides 6-7's unclear rules: T-touches "
                "and shared-stretch ends as crossings, overlap between rings, bends at nodes. "
                "We submit by the strict one."
            )
            st.dataframe(readings_rows(sub, c), hide_index=True)
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
    """The organisers' rules, read-only. What-ifs (#30) live in their own expander below."""
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


def _reset_what_ifs() -> None:
    """The reset button's callback: every what-if widget back to the problem statement."""
    for k, v in WHAT_IF_DEFAULTS.items():
        st.session_state[k] = v


def what_if_panel() -> tuple[Constraints, bool]:
    """The What-if expander (#30): the settings it gives, and whether to show J under both
    counting readings (D3). No node-cap control (D1: 5 only); the J weights aren't editable
    (they stay `ORGANISER_WEIGHTS`, #48). Interior windows as doors arrives with #31."""
    for k, v in WHAT_IF_DEFAULTS.items():
        st.session_state.setdefault(k, v)
    changed = any(st.session_state[k] != v for k, v in WHAT_IF_DEFAULTS.items())
    with st.expander(f":material/science: {WHAT_IF.title()}", expanded=changed):
        st.caption("Change a rule to see what it does. Labelled WHAT-IF, never submitted.")
        st.number_input(
            "Hub area cap (m²)",
            min_value=10.0,
            max_value=1000.0,
            step=10.0,
            format="%.0f",
            key="wi_max_area_m2",
            help="D4: a plan with a room over 100 m² can be solved under a higher cap.",
        )
        st.number_input(
            "Hub to wall, at least (m)",
            min_value=0.0,
            max_value=2.0,
            step=0.1,
            format="%.2f",
            key="wi_hub_wall_clearance_m",
        )
        st.toggle(
            "Link zone rooms through a bathroom or stair",
            key="wi_zone_links_via_excluded",
            help="D5: off by default; slide 3 rule 3 links zone rooms by doors only.",
        )
        st.toggle(
            "Allow cable through bathrooms or other hubs' rooms",
            key="wi_route_through_other_rooms",
            help="D7: off by default; a ring stays in its own hub's rooms.",
        )
        st.button("Reset to Problem statement", key="wi_reset", on_click=_reset_what_ifs)
        st.divider()
        counting = st.toggle(
            "Show J under both counting readings",
            key="wi_counting",
            help="D3: a view only. The design and its submission don't change.",
        )
    c = Constraints(
        max_area_m2=st.session_state["wi_max_area_m2"],
        hub_wall_clearance_m=st.session_state["wi_hub_wall_clearance_m"],
        zone_links_via_excluded=st.session_state["wi_zone_links_via_excluded"],
        route_through_other_rooms=st.session_state["wi_route_through_other_rooms"],
    )
    return c, counting


def what_if_banner(c: Constraints) -> None:
    """The red WHAT-IF banner (#30), above the tabs so every view carries it; nothing for the
    problem statement. The word WHAT-IF carries it, never the colour alone."""
    if changes := what_if_changes(c):
        st.error(
            f"**{WHAT_IF}** · {md_text('; '.join(changes))}. Every result on this page is judged "
            "under these settings and is never written as a submission (C29).",
            icon=":material/science:",
        )


# --------------------------------------------------------------------------- page

st.set_page_config(page_title="HackHive · hub planner", layout="wide")

with st.sidebar:
    choice = pick_plan()
    solution_slot = st.container()  # filled once the plan is loaded
    show_graph = st.toggle("Show room graph", key="show_graph", help="On the Plan tab.")
    st.divider()
    locked_preset(PRESET)
    settings_c, counting = what_if_panel()

st.title("HackHive")
what_if_banner(settings_c)
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
    best_path, side_json = solved(source, key, content, settings_c.model_dump_json())
    side = Sidecar.model_validate_json(side_json)
except Exception as e:  # solve() never raises for a loaded plan; the landing tab never tracebacks
    best_path = None
    side = Sidecar(
        plan_id=plan.plan_id,
        status="infeasible",
        reason=f"{type(e).__name__}: {e}",
        settings=settings_c,
    )
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
    best_tab(source, key, content, plan, G, solution, side, settings_c, counting)
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
        st.markdown(f"**{plan_facts(plan, settings_c)}**")
        st.markdown(f"{plan.m_per_unit:.4f} m/unit · walls {plan.wall_depth_m:.2f} m")
        st.caption("Eligible area · nodes · LB = the fewest hubs any zoning can use.")
