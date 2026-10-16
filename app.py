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
from hackhive.checker import check_rules, metrics
from hackhive.contracts import Constraints, Plan, Sidecar
from hackhive.io import load_plan
from hackhive.solve import (
    Result,
    arrangement_submission,
    file_stem,
    routed_submission,
    solve,
    submit,
    zoning_count,
)
from hackhive.solve import enumerate as enumerate_arrangements
from hackhive.ui import (
    CASES_SOURCE,
    DEFAULT_FIXTURE,
    RESPLAN_JSONL,
    best_by_hub_count,
    case_check,
    case_option_label,
    case_options,
    case_plan_path,
    case_rows,
    case_tags,
    checklist_line,
    filter_by_hubs,
    fixture_ids,
    fixture_label,
    fixture_path,
    hub_counts,
    hub_table,
    load_submission,
    md_text,
    metrics_line,
    optimality_badge,
    page_count,
    page_of,
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
    tile_caption,
    tile_verdict,
    verdict_line,
    violation_line,
)
from hackhive.viz import (
    draw_graph,
    draw_layers,
    draw_nodes,
    draw_solution,
    draw_tile,
    label_what_if,
    presentation_png,
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
TILE_DPI = 130  # a gallery tile fills a quarter of the tab without blurring (#26)
ARRANGEMENT_COPY = ".arrangement-"  # a gallery arrangement, routed for the Best tab (#26)
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
    elif source == CASES_SOURCE:
        plan = load_plan(case_plan_path(_case(key)))
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
    out = SOLVED_DIR / _ident(source, key, content, settings)
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


@st.cache_data(max_entries=64, show_spinner="Drawing the presentation image…")
def presentation(
    source: str, key: str, content: bytes | None, solution: str, side_json: str
) -> bytes:
    """`viz.presentation_png` of solve()'s design (#44): the same bytes `hackhive present`
    writes."""
    plan, _ = load(source, key, content)
    side = Sidecar.model_validate_json(side_json)
    return presentation_png(plan, Result(plan, load_submission(Path(solution)), side))


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


@st.cache_data(max_entries=16, show_spinner="Ranking every arrangement: each zone is routed once…")
def arrangements(source: str, key: str, content: bytes | None, settings: str) -> list:
    """#22's `enumerate`: every arrangement, ranked by `rank_key`, under the active settings."""
    plan, _ = load(source, key, content)
    return enumerate_arrangements(plan, Constraints.model_validate_json(settings))


@st.cache_data(max_entries=512, show_spinner=False)
def tile(
    source: str, key: str, content: bytes | None, settings: str, rank: int
) -> tuple[bytes, str]:
    """One gallery tile (#26): its PNG and its caption (H, J, length, bends, the checker's
    verdict on the cached rings)."""
    plan, _ = load(source, key, content)
    c = Constraints.model_validate_json(settings)
    a = arrangements(source, key, content, settings)[rank - 1]
    sub = arrangement_submission(plan, a, c)
    verdict = tile_verdict(check_rules(plan, sub, c))
    return to_png(draw_tile(plan, a, c), dpi=TILE_DPI), tile_caption(
        rank, a, metrics(sub.hubs, c), verdict
    )


@st.cache_data(max_entries=64, show_spinner="Routing the arrangement…")
def opened(source: str, key: str, content: bytes | None, settings: str, rank: int) -> str:
    """A gallery arrangement fully routed (the rip-up passes), as a file the Best tab draws and
    checks like any other (#26). A display copy, never a submission."""
    plan, _ = load(source, key, content)
    a = arrangements(source, key, content, settings)[rank - 1]
    sub = routed_submission(plan, a, Constraints.model_validate_json(settings))
    out = SOLVED_DIR / _ident(source, key, content, settings) / "arrangements"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{file_stem(plan.plan_id)}{ARRANGEMENT_COPY}{rank}.json"
    path.write_text(sub.model_dump_json(indent=2), encoding="utf-8")
    return str(path)


def _ident(source: str, key: str, content: bytes | None, settings: str) -> str:
    """One plan under one set of settings: solve()'s folder for it (`solved`, with the gallery's
    `arrangements/` inside) and what the gallery's state is keyed on."""
    raw = f"{source}\0{key}\0{settings}\0".encode() + (content or b"")
    return hashlib.sha256(raw).hexdigest()[:16]


# --------------------------------------------------------------------------- sidebar


def pick_plan() -> tuple[str, str, bytes | None] | None:
    """The chosen plan as (source, key, content), or None while an upload is pending."""
    st.header("Plan")
    sources = [FIXTURE, CASES_SOURCE, *([RESPLAN] if RESPLAN_JSONL.exists() else []), UPLOAD]
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
    if source == CASES_SOURCE:  # #85: one small plan per dataset case (#84)
        rows = {r["case"]: r for r in case_rows()}
        case = st.selectbox(
            "Case",
            case_options(list(rows.values())),
            format_func=lambda c: case_option_label(rows[c]),
            key="case",
            help="Every case the dataset produces, grouped: designs, warnings, no design.",
        )
        return source, case, None
    if source == RESPLAN:
        plan_id = st.text_input("ResPlan plan id", value=DEFAULT_FIXTURE, key="resplan_id")
        return source, plan_id.strip(), None
    up = st.file_uploader("Plan JSON", type=["json"], key="upload")
    if up is None:
        return None
    return source, up.name, up.getvalue()


def _case(case_id: str) -> dict:
    """A `cases.json` row by its case id (#85)."""
    return next(r for r in case_rows() if r["case"] == case_id)


def case_callout(case_id: str, side: Sidecar) -> None:
    """#85: what the case shows, and its expected status and hub count against what solve() gives
    now (green when they match)."""
    row = _case(case_id)
    st.info(f"This case shows: {md_text(row['look_for'])}")
    kind, text = case_check(row, side)
    getattr(st, kind)(md_text(text))


def _solution_name(path: Path) -> str:
    """By where the file is, not a captured path: a widget may keep an older run's format_func."""
    if SOLVED_DIR not in path.parents:
        return solution_label(path)
    if ARRANGEMENT_COPY in path.name:  # opened from the gallery (#26)
        return f"arrangement #{path.stem.rsplit('-', 1)[-1]} (gallery)"
    return BEST_WHAT_IF if path.name.endswith(WHAT_IF_COPY) else BEST


def pick_solution(plan: Plan, best: Path | None, opened: Path | None = None) -> Path | None:
    """The solution to show: solve()'s design first (#16), an arrangement opened from the
    gallery (#26), then the plan's hand-made fixtures (#11), valid first. None only when solve()
    wrote no submission, nothing is opened and there are no fixtures."""
    fixtures = solution_paths(plan.plan_id.removeprefix("PLAN_"))
    solutions = [*([best] if best else []), *([opened] if opened else []), *fixtures]
    if not solutions:
        return None
    names = [str(p) for p in solutions]
    if st.session_state.get("solution_options") != names:  # another plan: start at its best,
        st.session_state.pop("solution", None)  # never keep the last plan's file selected
        st.session_state["solution_options"] = names
    if opened is not None and st.session_state.pop("arr_open_pending", False):
        st.session_state["solution"] = opened  # "Open" on a tile shows it straight away
    return st.selectbox(
        "Solution",
        solutions,
        format_func=_solution_name,
        key="solution",
        help="solve()'s design for this plan, an arrangement opened from the gallery (#26), "
        "then, for 5088, the checker's hand-made submissions (#11): the valid one and one broken "
        "variant per rule.",
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
    is_best: bool = True,
) -> None:
    """Slide 5's expected solution: the status banner (#83), the overlay, the per-hub summary
    (C25) and the reviewer checklist (C26), all from the submission and the checker mirror,
    judged under `c`; with `counting`, J under both readings of the counting rules (D3, #30).
    The banner and the badge describe solve()'s design, so they show only with it (`is_best`);
    another design (a gallery arrangement, a hand-made file) says what it is instead (#26)."""
    settings = c.model_dump_json()
    if is_best or path is None:
        banner(side, plan)
        if badge := optimality_badge(side, plan, G, c):  # #24: proof of the hub count, never J
            getattr(st, badge.kind)(badge.text)
            if badge.explain:
                with st.expander("Why more hubs than the lower bound?"):
                    st.markdown(badge.explain)
    else:
        st.info(
            f"Showing {_solution_name(path)}, not the design solve() submitted. The checklist "
            "on the right judges this one.",
            icon=":material/compare:",
        )
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
        if path.is_relative_to(SOLVED_DIR):  # solve()'s design, not a hand-made fixture
            what_if = "" if side.settings.is_problem_statement else ".what-if"
            st.download_button(
                "Download presentation image",
                # drawn on click, off every Best-tab load (#107 review); cached after that
                lambda: presentation(source, key, content, str(path), side.model_dump_json()),
                file_name=f"{file_stem(side.plan_id)}{what_if}.presentation.png",
                mime="image/png",
            )
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


# --------------------------------------------------------------------------- arrangements (#26)


def _rank_now(ident: str) -> None:
    st.session_state["arr_for"] = ident
    for k in ("arr_k", "arr_page"):
        st.session_state.pop(k, None)


def _open_arrangement(ident: str, rank: int) -> None:
    st.session_state["arr_open"] = (ident, rank)
    st.session_state["arr_open_pending"] = True
    st.toast(f"Arrangement #{rank} is on the Best tab, fully routed and checked.")


def arrangements_tab(
    source: str, key: str, content: bytes | None, plan: Plan, c: Constraints
) -> None:
    """Every arrangement (#22's `enumerate`), ranked by `rank_key` (C16, C29): the best J per hub
    count, a hub-count filter, tiles 12 to a page with H, J and the checker's verdict, and
    "Open" to see one fully routed in the Best tab. Ranking routes every zone, so it starts on
    request and is cached per plan and settings."""
    settings = c.model_dump_json()
    ident = _ident(source, key, content, settings)
    n = zoning_count(plan, c)
    if st.session_state.get("arr_for") != ident:
        st.caption(
            f"Up to {n} arrangements (one per zoning) to rank. Ranking routes every zone once, "
            "about a second each on one core (13344: about 2 minutes), then it's cached."
        )
        st.button(
            "Rank every arrangement",
            key="arr_go",
            on_click=_rank_now,
            args=(ident,),
            disabled=n == 0,
        )
        return
    try:
        arrs = arrangements(source, key, content, settings)
    except Exception as e:  # a plan solve() handles but enumerate() can't: say so, no traceback
        st.error(f"Can't rank the arrangements. {type(e).__name__}: {e}")
        return
    if not arrs:
        st.info(
            "No arrangement could be built: no zoning has a hub spot and a route in every zone."
        )
        return
    st.subheader("Best J per hub count")
    st.dataframe(best_by_hub_count(arrs), hide_index=True)
    ks = st.multiselect(
        "Hub count (all when empty)",
        hub_counts(arrs),
        key="arr_k",
        on_change=lambda: st.session_state.pop("arr_page", None),
    )
    shown = filter_by_hubs(arrs, ks)
    pages = page_count(len(shown))
    page = st.number_input("Page", 1, pages, key="arr_page") if pages > 1 else 1
    st.caption(
        f"{len(shown)} of {len(arrs)} arrangements · page {page} of {pages} · ranked by hubs, then "
        "J (D3's worse reading), then the area gap · rings routed alone (Open routes them fully)"
    )
    tiles = page_of(shown, page)
    for start in range(0, len(tiles), 4):
        for col, (rank, _) in zip(st.columns(4), tiles[start : start + 4], strict=False):
            with col:
                try:
                    png, caption = tile(source, key, content, settings, rank)
                except Exception as e:  # one tile's error never hides the others
                    st.error(f"#{rank}: {type(e).__name__}: {e}")
                    continue
                st.image(png, width="stretch")
                st.caption(caption)
                st.button(
                    "Open", key=f"arr_open_{rank}", on_click=_open_arrangement, args=(ident, rank)
                )


# --------------------------------------------------------------------------- page


def page() -> None:
    """The page. Only Streamlit's run of this script calls it (`__name__ == "__main__"`): a
    worker process of #22's pool imports this file as `__mp_main__` (macOS and Windows spawn,
    and `parallel`'s fork server does too), and must then define functions only, never re-run
    the app and its solve (#26)."""
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
    # solve() never raises for a loaded plan; the landing tab never tracebacks
    except Exception as e:
        best_path = None
        side = Sidecar(
            plan_id=plan.plan_id,
            status="infeasible",
            reason=f"{type(e).__name__}: {e}",
            settings=settings_c,
        )
    opened_path = None
    if (req := st.session_state.get("arr_open")) and req[0] == _ident(
        source, key, content, settings_c.model_dump_json()
    ):
        try:
            opened_path = Path(opened(source, key, content, settings_c.model_dump_json(), req[1]))
        except Exception as e:  # routing one arrangement failed: say so, keep the page
            st.error(f"Can't route arrangement #{req[1]}. {type(e).__name__}: {e}")
    with solution_slot:
        solution = pick_solution(plan, Path(best_path) if best_path else None, opened_path)

    st.caption(f"**{plan.plan_id}** · {room_counts(plan)}")
    try:
        best_sub = load_submission(Path(best_path)) if best_path else None
    except ValueError:  # a stale cached path (temp dir cleaned): the design tags just drop out
        best_sub = None
    if tags := case_tags(plan, G, Result(plan, best_sub, side)):
        st.markdown(tag_chips(tags), help="\n".join(f"{t.label}: {t.look_for}" for t in tags))
    best, plan_tab, arr_tab = st.tabs(["Best", "Plan", "Arrangements"])
    with best:
        if source == CASES_SOURCE:
            case_callout(key, side)
        is_best = solution is None or (best_path is not None and Path(best_path) == solution)
        best_tab(source, key, content, plan, G, solution, side, settings_c, counting, is_best)
    with arr_tab:
        arrangements_tab(source, key, content, plan, settings_c)
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


if __name__ == "__main__":
    page()
