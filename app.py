"""HackHive demo app (#7): pick a plan, see it drawn, and see the organisers' rules, locked.

Run it with `uv run streamlit run app.py`. Later issues add the solution overlay (#12, #17),
the what-if sidebar (#30), the arrangements gallery and the batch leaderboard.
"""

from __future__ import annotations

import streamlit as st

from hackhive.contracts import Constraints, Plan
from hackhive.io import load_plan
from hackhive.ui import (
    DEFAULT_FIXTURE,
    RESPLAN_JSONL,
    fixture_ids,
    fixture_label,
    fixture_path,
    plan_from_bytes,
    plan_summary,
    preset_rows,
    resplan_raw,
)
from hackhive.viz import draw_layers, to_png

PRESET = Constraints.problem_statement()
FIXTURE, RESPLAN, UPLOAD = "Test plans", "ResPlan id", "Upload"


# --------------------------------------------------------------------------- cached work
# Cached on (source, key, content), never on a Figure: cache_data pickles what it returns, and
# PNG bytes and the Plan model both pickle cleanly. The Plan is cached so later overlays reuse it.


@st.cache_data(max_entries=64, show_spinner="Loading the plan…")
def load(source: str, key: str, content: bytes | None = None) -> Plan:
    if source == UPLOAD:
        return plan_from_bytes(content or b"")
    if source == RESPLAN:
        return load_plan(resplan_raw(RESPLAN_JSONL, key))
    return load_plan(fixture_path(key))


@st.cache_data(max_entries=64, show_spinner="Drawing…")
def picture(source: str, key: str, content: bytes | None = None) -> bytes:
    return to_png(draw_layers(load(source, key, content)))


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
    st.divider()
    locked_preset(PRESET)

st.title("HackHive")
if choice is None:
    st.caption("Upload a plan JSON in the sidebar: a ResPlan plan or one of our vault files.")
    st.stop()

source, key, content = choice
try:
    plan = load(source, key, content)
    png = picture(source, key, content)
except (OSError, ValueError) as e:
    st.error(f"Can't draw this plan. {e}")
    st.stop()

st.caption(f"**{plan.plan_id}** · {plan_summary(plan, PRESET)}")
st.image(png, width="stretch")
if plan.warnings:
    with st.expander(f"{len(plan.warnings)} input warning(s)"):
        for w in plan.warnings:
            st.markdown(f"- {w}")
