"""Plain helpers behind the Streamlit app (#7): where plans come from and what the locked preset
says. Nothing here imports streamlit, so all of it is unit-tested without running the app."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx

from hackhive import checker, graph
from hackhive.contracts import (
    Constraints,
    CostWeights,
    HubMetrics,
    HubOut,
    Plan,
    Sidecar,
    Submission,
    Violation,
    lower_bound,
)
from hackhive.io import NODE_CLEARANCE_M, load_plan
from hackhive.solve import Result

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "plans"
RAW_DIR = ROOT / "tests" / "fixtures" / "raw"
SOLUTION_DIR = ROOT / "tests" / "fixtures" / "solutions"  # #11's hand-made submissions
RESPLAN_JSONL = ROOT / "data" / "resplan" / "plans.jsonl"  # optional, never committed
DEFAULT_FIXTURE = "5924"


# --------------------------------------------------------------------------- plan sources


def fixture_ids() -> list[str]:
    """The test plans' ids, in numeric order."""
    ids = [p.stem.removeprefix("plan-") for p in FIXTURE_DIR.glob("plan-*.json")]
    return sorted(ids, key=int)


def fixture_label(plan_id: str) -> str:
    """`5924 · gap`: the id plus why the plan is a fixture (from its vault file name)."""
    for p in RAW_DIR.glob(f"plan-{plan_id}-*.json"):
        return f"{plan_id} · {p.stem.removeprefix(f'plan-{plan_id}-').replace('-', ' ')}"
    return plan_id


def fixture_path(plan_id: str) -> Path:
    return FIXTURE_DIR / f"plan-{plan_id}.json"


def _unwrap(raw: Any) -> dict:
    """A plan object; a research-vault file (`label`, `metrics`, `plan`) is unwrapped."""
    if not isinstance(raw, dict):
        raise ValueError(f"expected a JSON object, got {type(raw).__name__}")
    if isinstance(raw.get("plan"), dict) and "wall" not in raw:
        return raw["plan"]
    return raw


def plan_from_bytes(content: bytes) -> Plan:
    """An uploaded plan JSON (a ResPlan plan or a vault file) as a metric `Plan`.
    Anything unreadable raises ValueError with a message fit to show the user."""
    try:
        raw = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"not a JSON file: {e}") from e
    raw = _unwrap(raw)
    try:
        return load_plan(raw)
    except ValueError:
        raise
    except Exception as e:  # malformed geometry surfaces as many exception types
        raise ValueError(f"not a plan this app can read: {type(e).__name__}: {e}") from e


def resplan_raw(path: Path, plan_id: str | int) -> dict:
    """One plan from a ResPlan JSON-lines file, by its `id`. Raises ValueError if absent."""
    want = str(plan_id).strip()
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            raw = _unwrap(json.loads(line))
            if str(raw.get("id")) == want:
                return raw
    raise ValueError(f"plan {want} is not in {path.name}")


# --------------------------------------------------------------------------- the locked preset


def _coef(w: float, symbol: str) -> str:
    return symbol if w == 1 else f"{w:g}{symbol}"


def cost_formula(w: CostWeights) -> str:
    """The cost formula as slide 6 prints it, written from the weights so it can't drift."""
    terms = [
        _coef(w.length, "L"),
        _coef(w.cross, "X"),
        _coef(w.overlap, "O"),
        _coef(w.bend, "B"),
    ]
    return "J = " + " + ".join(terms)


def preset_rows(c: Constraints) -> list[tuple[str, str]]:
    """The rules as (what, value) rows, for the read-only sidebar panel."""
    return [
        ("Hub area cap", f"{c.max_area_m2:g} m²"),
        ("Nodes per hub", f"{c.max_nodes} nodes"),
        ("Cost", cost_formula(c.cost_weights)),
        ("Hub to wall", f"≥ {c.hub_wall_clearance_m:g} m"),
        ("Tolerance", f"{c.tolerance_m * 1000:g} mm"),
        ("Ranking", "fewest hubs, then lowest J, then area gap"),
    ]


def plan_facts(plan: Plan, c: Constraints) -> str:
    """`150.0 m² · 5 nodes · LB 2`: eligible area, nodes and the hub lower bound (C7)."""
    area = sum(r.area_m2 for r in plan.rooms if r.eligible)
    return f"{area:.1f} m² · {len(plan.nodes)} nodes · LB {lower_bound(area, len(plan.nodes), c)}"


def room_counts(plan: Plan) -> str:
    """`8 rooms (5 eligible)`; the plot outline (`land`) isn't a room."""
    rooms = [r for r in plan.rooms if r.type != "land"]
    return f"{len(rooms)} rooms ({sum(r.eligible for r in rooms)} eligible)"


# --------------------------------------------------------------------------- reviewer panel


def solution_paths(plan_id: str) -> list[Path]:
    """The hand-made solution fixtures (#11) for a test plan: `<id>-valid.json` first, then the
    broken variants by name. Empty when the plan has none. The id is matched as plain text,
    never as a glob or a path, because an uploaded plan chooses its own id (#69 review)."""
    if not plan_id:
        return []
    prefix = f"{plan_id}-"
    paths = [p for p in SOLUTION_DIR.glob("*.json") if p.name.startswith(prefix)]
    return sorted(paths, key=lambda p: (p.stem != f"{plan_id}-valid", p.name))


def solution_label(path: Path) -> str:
    """`5088-hub-0.3m-from-wall.json` → `hub 0.3m from wall`."""
    return path.stem.split("-", 1)[1].replace("-", " ")


def load_submission(path: Path) -> Submission:
    """A slide-8 submission file. Raises ValueError (pydantic's ValidationError is one)."""
    try:
        return Submission.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"can't read {path.name}: {e}") from e


def submission_mismatch(sub: Submission, plan: Plan) -> str | None:
    """A warning when the submission names another plan (the checker CLI warns too), else None."""
    if sub.plan_id == plan.plan_id:
        return None
    return f"This submission is for {sub.plan_id}, but the plan is {plan.plan_id}."


_MARKDOWN_SPECIALS = "\\`*_[]$~<>#|"


def md_text(text: str) -> str:
    """Text shown literally in Streamlit markdown: every character that could start markup, a
    LaTeX span or end a `:red[...]` span early is backslash-escaped (#69 review)."""
    return "".join(f"\\{ch}" if ch in _MARKDOWN_SPECIALS else ch for ch in text)


def checklist_line(line: str) -> str:
    """One line of `checker.checklist`, verbatim; a ✗ line is also coloured red."""
    text = md_text(line)
    return f":red[{text}]" if line.startswith("✗") else text


def violation_line(v: Violation) -> str:
    """One violation as coloured markdown: red ✗ for an error, orange ⚠ for a strict-only one.
    The rule number and the severity are always written out, so colour never carries it alone.
    The checker's own text (hub id, message) is escaped, so it can't end the span early."""
    colour, mark = ("red", "✗") if v.severity == "error" else ("orange", "⚠")
    hub = f" · {md_text(v.hub_id)}" if v.hub_id else ""
    return f":{colour}[{mark} **Rule {v.rule}**{hub} · {v.severity}: {md_text(v.message)}]"


def verdict_line(verdict: str) -> str:
    """`checker.verdict` with a mark and a colour: ✓ valid, ⚠ valid (strict only), ✗ invalid."""
    if verdict == "valid":
        return f":green[✓ **{verdict}**]"
    if verdict == "invalid":
        return f":red[✗ **{verdict}**]"
    return f":orange[⚠ **{verdict}**]"


def metrics_line(m: checker.Metrics | None) -> str:
    """The checker CLI's metrics line, from the one shared formatter (`checker.metrics_text`)."""
    return md_text(checker.metrics_text(m))


# --------------------------------------------------------------------------- per-hub summary (#17)


def hub_metrics(plan: Plan, sub: Submission, m: checker.Metrics) -> list[HubMetrics]:
    """Each hub's share of the metrics, in submission order: the eligible m² and nodes it's
    assigned, and its L, X, O, B from the checker mirror (`Metrics.per_hub`). The same rows a
    sidecar's `hubs[]` holds (#16), so the summary and the sidecar can't disagree."""
    rooms = {r.id: r for r in plan.rooms}
    out = []
    for h in sub.hubs:
        per = m.per_hub[h.hub_id]
        area = checker.assigned_area(h, rooms)
        out.append(
            HubMetrics(
                hub_id=h.hub_id,
                area_m2=area,
                n_nodes=len(set(h.assigned_nodes)),
                L=per.L,
                X=per.X,
                O=per.O,
                B=per.B,
            )
        )
    return out


def rooms_text(hub: HubOut, plan: Plan) -> str:
    """`R01, R03 (living, kitchen)`: a hub's rooms and their types, for the table and the line."""
    rooms = {r.id: r for r in plan.rooms}
    ids = list(dict.fromkeys(hub.assigned_rooms))
    kinds = ", ".join(rooms[r].type if r in rooms else "?" for r in ids)
    return f"{', '.join(ids)} ({kinds})"


def hub_summary_line(row: HubMetrics, hub: HubOut, plan: Plan) -> str:
    """`H1: R01, R03 (living, kitchen) · 34.0 m² · 2 nodes · 12.40 m, 8 bends`, plus the
    overlap and crossings when there are any."""
    nodes = f"{row.n_nodes} node{'' if row.n_nodes == 1 else 's'}"
    extra = (f" · O {row.O:.2f} m" if row.O else "") + (f" · X {row.X}" if row.X else "")
    return (
        f"{hub.hub_id}: {rooms_text(hub, plan)} · {row.area_m2:.1f} m² · {nodes} · "
        f"{row.L:.2f} m, {row.B} bends{extra}"
    )


def hub_table(plan: Plan, sub: Submission, m: checker.Metrics) -> list[dict[str, Any]]:
    """The per-hub summary as table rows (C25): rooms, m², nodes, length, bends, overlap and
    crossings, one row per hub in submission order."""
    rows = []
    for row, hub in zip(hub_metrics(plan, sub, m), sub.hubs, strict=True):
        rows.append(
            {
                "Hub": hub.hub_id,
                "Rooms": rooms_text(hub, plan),
                "m²": round(row.area_m2, 1),
                "Nodes": row.n_nodes,
                "L (m)": round(row.L, 3),
                "Bends": row.B,
                "Overlap O (m)": round(row.O, 3),
                "Crossings X": row.X,
            }
        )
    return rows


def step_caption(sub: Submission, step: int) -> str:
    """`Step 3 of 9 · H1: N2 → N1`: what the step-through slider (#25) has just drawn."""
    order = [(h.hub_id, s) for h in sub.hubs for s in h.route_segments]
    total = len(order)
    if total == 0:  # the slider is disabled: nothing to walk through (#88 review)
        return "No cable to step through"
    if step <= 0:
        return f"Step 0 of {total} · hubs and nodes only"
    if step >= total:
        return f"Step {total} of {total} · the full design"
    hub_id, seg = order[step - 1]
    back = ", back to its hub" if seg.to == hub_id else ""
    return f"Step {step} of {total} · {hub_id}: {seg.from_} → {seg.to}{back}"


# --------------------------------------------------------------------------- case tags and banners (#83)

Q2_URL = "https://github.com/Adityashandilya555/hackhive/blob/main/docs/clarifying_questions.md"
_ROOM_OVER_CAP = re.compile(r"room (\S+) is ([\d.]+) m² > ([\d.]+) m²")


@dataclass(frozen=True)
class CaseTag:
    id: str
    label: str
    look_for: str  # one line: what to look at in the drawing


def case_tags(plan: Plan, G: nx.Graph, result: Result) -> list[CaseTag]:
    """What makes this plan a special case, from the plan, its room graph and solve()'s result.
    The design tags (one-node ring, hub counts) appear only when there is a design."""
    tags: list[CaseTag] = []
    E = graph.eligible_graph(G)
    if any(kind == "opening" for *_, kind in G.edges(data="kind")):
        tags.append(CaseTag("open_passage", "open passage", "cable crosses a wall-less gap"))
    if any(G.degree(r) > 1 for r in graph.single_door_rooms(G) if r in E):
        tags.append(
            CaseTag(
                "en_suite",
                "en-suite single-door",
                "a room whose only other door is its own bathroom: retracing in is legal",
            )
        )
    sub, bounds = result.submission, result.sidecar.bounds
    if sub is not None and any(len(h.assigned_nodes) == 1 for h in sub.hubs):
        look = "hub → node → hub: out-and-back, or a thin two-lane loop when the node is far (#81)"
        tags.append(CaseTag("one_node_ring", "one-node ring", look))
    if sub is not None and bounds is not None and bounds.k_found is not None:
        extra = bounds.k_found - bounds.k_lb
        if extra == 0:
            tags.append(CaseTag("lb_met", "hubs = lower bound", "no zoning can use fewer hubs"))
        elif bounds.proof == "exhaustive":
            tags.append(
                CaseTag(
                    "above_lb",
                    f"hubs > lower bound (+{extra}, proven minimum)",
                    "door links force extra zones; every zoning was checked",
                )
            )
        if bounds.k_found >= 4:
            tags.append(CaseTag("many_hubs", "4+ hubs", "many rings share the doors"))
    if nx.number_connected_components(E) > 1:
        tags.append(
            CaseTag("disconnected", "disconnected groups", "rooms no door joins need own hubs")
        )
    if any(E.degree(r) == 0 for r in E):
        tags.append(
            CaseTag(
                "isolated",
                "isolated eligible room",
                "no door or open passage to another eligible room: it needs its own hub",
            )
        )
    if sum(r.type == "living" and r.eligible for r in plan.rooms) > 1:
        tags.append(CaseTag("multi_living", "multi-part living", "each living part has a node"))
    if any(n.clearance_m is not None and n.clearance_m < NODE_CLEARANCE_M for n in plan.nodes):
        tags.append(
            CaseTag(
                "low_clearance",
                f"node clearance < {NODE_CLEARANCE_M:g} m",
                "a node close to a wall",
            )
        )
    if plan.folded_parts:
        tags.append(CaseTag("folded", "folded sliver", "a living sliver merged into its neighbour"))
    return tags


def tag_chips(tags: list[CaseTag]) -> str:
    """The tags as one line of markdown chips; the `look_for` goes in the help line instead."""
    return " ".join(f":blue-badge[{md_text(t.label)}]" for t in tags)


def _infeasible_line(reason: str, plan: Plan | None) -> str:
    if m := _ROOM_OVER_CAP.search(reason):
        room, area, cap = m.groups()
        kind = next((r.type for r in plan.rooms if r.id == room), "") if plan else ""
        return (
            f"Room {md_text(room)} {kind} is {area} m² \\> {cap} m²: no hub can serve it. "
            "Try the area-cap what-if (#30)."
        )
    if reason.startswith("grid failed"):
        return f"A door or passage is too thin for the grid (#78). {md_text(reason)}"
    if reason.startswith("routing failed"):
        return f"No route inside the free space. {md_text(reason)}"
    if "hub-capable" in reason:
        return f"No room can hold a hub this far from walls. {md_text(reason)}"
    if reason.startswith("too large"):
        return f"Too many rooms for exact zoning. {md_text(reason)}"
    return md_text(reason)


def status_banner(side: Sidecar, plan: Plan | None) -> tuple[str, str]:
    """(`success` | `warning` | `error`, markdown): one banner per sidecar status (C43)."""
    reason = side.reason or ""
    if side.status == "ok":
        return "success", "✓ **ok**: a clean design, no violations."
    if side.status == "submitted_strict_only":
        strict = [v.rule for v in side.violations if v.severity == "strict"]
        parts = []
        if n := strict.count(4):
            parts.append(
                f"{n} retrace{'s' * (n != 1)} outside a single-door room (rule 4, [Q2]({Q2_URL}))"
            )
        if n := strict.count(6):
            parts.append(
                f"{n} hub{'s' * (n != 1)} closer than {Constraints.problem_statement().hub_wall_clearance_m:g} m to a door or passage edge "
                f"(rule 6, [Q4]({Q2_URL}))"
            )
        return "warning", (
            f"⚠ **submitted_strict_only**: {'; '.join(parts) or 'strict readings only'}. "
            "Valid only if the organisers allow it."
        )
    if side.status == "infeasible":
        return "error", f"✗ **infeasible**: {_infeasible_line(reason, plan)}"
    return "error", f"✗ **input_error**: {md_text(reason)}"


# --------------------------------------------------------------------------- what-ifs (#30)


def readings_rows(sub: Submission, c: Constraints) -> list[dict[str, Any]]:
    """One design's metrics under both readings of D3's unclear counting rules (`checker.metrics`
    `reading`): ours (strict) and the lenient one, as table rows. L is the same in both."""
    rows = []
    for reading, name in (("strict", "strict (ours)"), ("lenient", "lenient")):
        m = checker.metrics(sub.hubs, c, reading=reading)
        rows.append(
            {
                "Reading": name,
                "L (m)": round(m.L, 3),
                "X": m.X,
                "O (m)": round(m.O, 3),
                "B": m.B,
                "J": round(m.J, 3),
            }
        )
    return rows
