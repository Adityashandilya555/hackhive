"""Plain helpers behind the Streamlit app (#7): where plans come from and what the locked preset
says. Nothing here imports streamlit, so all of it is unit-tested without running the app."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx

from hackhive import batch, checker, graph
from hackhive.cases import CaseTag, case_tags  # noqa: F401  (re-exported for app.py)
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


# --------------------------------------------------------------------------- optimality badge

# the proofs that make H the fewest hubs possible (#24); greedy and none prove nothing
PROOFS = ("exhaustive", "lb_met")


def proven_min_hubs(side: Sidecar) -> int | None:
    """The hub count solve() proved minimal (`bounds.proof` is `lb_met` or `exhaustive`, set
    per plan by `solve._proof`, #22), or None when unproven."""
    b = side.bounds
    return b.k_found if b is not None and b.proof in PROOFS else None


@dataclass(frozen=True)
class Badge:
    kind: str  # the Streamlit call: success (✓) or error (✗, only for an unproven H > LB)
    text: str
    explain: str | None = None  # why H > LB, for the expander; None when H = LB


def _why_above_lb(G: nx.Graph, lb: int) -> str:
    """Why no split into `lb` zones exists, from the same checks as `case_tags`' `isolated` and
    `disconnected` tags; the living-room star is the fallback when neither applies (#98 review)."""
    E = graph.eligible_graph(G)
    lone = sum(E.degree(r) == 0 for r in E)
    groups = nx.number_connected_components(E) - lone
    why = []
    if lone:
        why.append(
            f"{lone} eligible room{' has' if lone == 1 else 's have'} no door or open passage "
            f"to another eligible room, so {'it needs its' if lone == 1 else 'each needs its'} "
            "own hub."
        )
    if groups > 1:
        why.append(f"The other rooms form {groups} groups that no door joins: each needs own hubs.")
    zones = f"{lb} zone{'' if lb == 1 else 's'}"
    if not why:
        return (
            "when rooms open only onto the living room (a star around it), no split into "
            f"{zones} fits both caps."
        )
    return f"no split into {zones} exists here. " + " ".join(why)


def optimality_badge(side: Sidecar, plan: Plan, G: nx.Graph, c: Constraints) -> Badge | None:
    """The Best tab's badge (#24, C7, T7) from the sidecar's bounds and cost and the room graph
    `G`; None without a design. "optimal" is written only under a proof and refers to the hub
    count, never to J. J is solve()'s design, not a hand-made file picked in the Solution box."""
    b, m = side.bounds, side.cost
    if b is None or b.k_found is None or m is None:
        return None
    k, lb = b.k_found, b.k_lb
    area = sum(r.area_m2 for r in plan.rooms if r.eligible)
    hubs = f"{k} hub{'' if k == 1 else 's'}"
    cost = f"solve()'s design: J = {m.J:.2f} (L {m.L:.2f} m · X {m.X} · O {m.O:.2f} m · B {m.B})"
    if k == lb:
        return Badge(
            "success", f"✓ {hubs} = lower bound ({area:.1f} m², {len(plan.nodes)} nodes) · {cost}"
        )
    if proven_min_hubs(side) == k:
        return Badge(
            "success",
            f"✓ proven minimum · {hubs} · optimal (exhaustive search) · LB {lb} (+{k - lb})"
            f" · {cost} · optimal refers to the hub count only",
            f"The lower bound {lb} counts only area (≤ {c.max_area_m2:g} m²) and nodes "
            f"(≤ {c.max_nodes}) per hub. Each zone must also be connected through its own doors: "
            f"{_why_above_lb(G, lb)} Every zoning was checked, so {k} is the fewest possible.",
        )
    return Badge("error", f"✗ {hubs} > lower bound {lb} (+{k - lb}) · not proven minimal · {cost}")


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


# --------------------------------------------------------------------------- case library (#84)

CASE_DIR = ROOT / "tests" / "fixtures" / "cases"
CASES_JSON = CASE_DIR / "cases.json"
# kept as regression examples once a fix makes their plan stop showing the case (#84, #78)
REGRESSION_CASES = frozenset({"grid_failed"})


@dataclass(frozen=True)
class CaseFacts:
    """What one solved plan shows: the sidecar's status and reason, its hub counts and tags."""

    status: str
    reason: str
    k_lb: int | None
    k_found: int | None
    tags: tuple[CaseTag, ...]

    def tag(self, tag_id: str) -> CaseTag | None:
        return next((t for t in self.tags if t.id == tag_id), None)

    def expected(self) -> dict[str, Any]:
        """`cases.json`'s `expected`: the status, the hub count when there's a design, the tags."""
        out: dict[str, Any] = {"status": self.status}
        if self.k_found is not None:
            out["k_found"] = self.k_found
        out["tags"] = [t.id for t in self.tags]
        return out


def case_facts(plan: Plan, result: Result) -> CaseFacts:
    """A plan's `CaseFacts` from `solve(plan)`'s result, with the tags the app's chips show."""
    side = result.sidecar
    b = side.bounds
    k_found = b.k_found if b is not None and result.submission is not None else None
    tags = case_tags(plan, graph.build_graph(plan), result)
    k_lb = b.k_lb if b is not None else None
    return CaseFacts(side.status, side.reason or "", k_lb, k_found, tuple(tags))


@dataclass(frozen=True)
class Case:
    """One dataset case. A tag case takes its title and `look_for` from the tag (`case_tags`)
    unless it names its own; a status case always names them."""

    id: str
    match: Callable[[CaseFacts], bool]
    title: str | None = None
    look_for: str | None = None
    tag: str | None = None

    def describe(self, facts: CaseFacts) -> tuple[str, str]:
        t = facts.tag(self.tag) if self.tag else None
        title = self.title or (t.label if t else self.id)
        return title, self.look_for or (t.look_for if t else "")


def _tag_case(tag_id: str, **kw: Any) -> Case:
    return Case(tag_id, lambda f: f.tag(tag_id) is not None, tag=tag_id, **kw)


def _above_lb(extra: int) -> Case:
    def match(f: CaseFacts) -> bool:
        return f.tag("above_lb") is not None and f.k_found - f.k_lb == extra

    return Case(f"above_lb_{extra}", match, tag="above_lb")


# Every case in the 600-plan scan (plan §4): one per status or no-design reason, one per tag.
# `too large` (D8: no ResPlan plan needs it) and `input_error` (no plan to solve) aren't here,
# nor `routing failed`: no plan of the scan fails routing any more (§4's 12025 now solves; 412856
# and 432441 are `input_error` since #78). Add it back when a plan does.
CASES: tuple[Case, ...] = (
    Case(
        "ok",
        lambda f: f.status == "ok",
        "A clean design",
        "green ✓: every rule met, nothing to explain",
    ),
    Case(
        "strict_only",
        lambda f: f.status == "submitted_strict_only",
        "Strict-only design",
        "orange banner: valid only if the organisers allow the strict readings (Q2, Q4)",
    ),
    Case(
        "room_over_cap",
        lambda f: f.status == "infeasible" and bool(_ROOM_OVER_CAP.search(f.reason)),
        "A room over 100 m²",
        "red banner names the room and its m²; the Plan tab still draws (try the area-cap what-if)",
    ),
    Case(
        "grid_failed",
        lambda f: f.status == "infeasible" and f.reason.startswith("grid failed"),
        "Grid failed",
        "a door or passage too thin for the 10 cm grid (#78)",
    ),
    Case(
        "no_hub_capable",
        lambda f: f.status == "infeasible" and "hub-capable" in f.reason,
        "No hub-capable room",
        f"rooms too thin for a hub {Constraints().hub_wall_clearance_m:g} m from every wall",
    ),
    _tag_case("open_passage"),
    _tag_case("en_suite"),
    _tag_case("one_node_ring"),
    _tag_case("lb_met"),
    _above_lb(1),
    _above_lb(2),
    _above_lb(3),
    _above_lb(4),
    _tag_case("many_hubs"),
    _tag_case("disconnected"),
    _tag_case("isolated"),
    _tag_case("multi_living"),
    _tag_case("low_clearance"),
    _tag_case("folded"),
)


def case_rows(path: Path = CASES_JSON) -> list[dict[str, Any]]:
    """`cases.json`: `[{case, title, look_for, plan, expected: {status, k_found?, tags}}]`."""
    return json.loads(path.read_text(encoding="utf-8"))


def case_plan_path(row: dict[str, Any], path: Path = CASES_JSON) -> Path:
    """A row's plan file: `plan` is relative to `cases.json`'s folder."""
    return (path.parent / row["plan"]).resolve()


# --------------------------------------------------------------------------- "Cases" picker (#85)

CASES_SOURCE = "Cases"
# the picker's groups, in order, by the case's expected sidecar status
CASE_GROUPS = {
    "designs": ("ok",),
    "warnings": ("submitted_strict_only",),
    "no design": ("infeasible", "input_error"),
}


def case_group(row: dict[str, Any]) -> str:
    status = row["expected"]["status"]
    return next(g for g, statuses in CASE_GROUPS.items() if status in statuses)


def case_options(rows: list[dict[str, Any]]) -> list[str]:
    """The case ids for the picker: designs, then warnings, then no design; within a group, in
    `cases.json`'s (catalogue) order."""
    order = list(CASE_GROUPS)
    return [r["case"] for r in sorted(rows, key=lambda r: order.index(case_group(r)))]


def case_option_label(row: dict[str, Any]) -> str:
    """`no design · A room over 100 m²`."""
    return f"{case_group(row)} · {row['title']}"


def _status_and_hubs(status: str, k: int | None) -> str:
    return status + ("" if k is None else f", {k} hub{'' if k == 1 else 's'}")


def case_check(row: dict[str, Any], side: Sidecar) -> tuple[str, str]:
    """(`success` | `error`, text): the case's expected status and hub count against what
    `solve()` gives now, green when both match (#85). Without a design only the status counts."""
    want = row["expected"]
    k_want = want.get("k_found")
    k_got = side.bounds.k_found if side.bounds is not None and k_want is not None else None
    ok = side.status == want["status"] and k_got == k_want
    text = (
        f"Expected {_status_and_hubs(want['status'], k_want)}; "
        f"got {_status_and_hubs(side.status, k_got)}"
    )
    return ("success", f"✓ {text}") if ok else ("error", f"✗ {text}")


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


# --------------------------------------------------------------------------- batch leaderboard (#33)

# #32's CSV, read only: the app never solves a batch (research note frontend-novelty, feature 7).
# The fresh run first, then a committed sample if #32 adds one.
BATCH_PATHS: tuple[Path, ...] = (
    ROOT / "out" / "batch.csv",
    ROOT / "data" / "sample" / "batch-test-100.csv",
)
BATCH_COMMAND = "uv run hackhive batch --split test --n 100 --out out/batch.csv"
# #32's columns, read from `hackhive batch` itself so the two never drift (#106 review). `tags`
# (#83's case-tag labels, ";"-joined) is an optional extra.
BATCH_COLUMNS = tuple(batch.COLUMNS)
OPTIONAL_COLUMNS = ("tags",)  # #108's case-tag ids; a CSV from before it still loads
# `cases.case_tags` ids (what #108 writes) → what the tab shows. The +k of "hubs > lower bound"
# lives in the plan's own label, not its id, so the batch groups every above-the-bound plan.
TAG_LABELS = {
    "open_passage": "open passage",
    "en_suite": "en-suite single-door",
    "one_node_ring": "one-node ring",
    "lb_met": "hubs = lower bound",
    "above_lb": "hubs > lower bound",
    "many_hubs": "4+ hubs",
    "disconnected": "disconnected groups",
    "isolated": "isolated eligible room",
    "multi_living": "multi-part living",
    "low_clearance": f"node clearance < {NODE_CLEARANCE_M:g} m",
    "folded": "folded sliver",
}


def tag_label(tag_id: str) -> str:
    """A case-tag id's label; an id the tab doesn't know yet is shown as is."""
    return TAG_LABELS.get(tag_id, tag_id)


_INTS = ("n_nodes", "k_lb", "H", "X", "B", "ms")
_FLOATS = ("area_m2", "L", "O", "J", "area_gap")


def find_batch_csv() -> Path | None:
    """The first batch CSV that exists in `BATCH_PATHS`, or None."""
    return next((p for p in BATCH_PATHS if p.is_file()), None)


def _number(text: str, kind: type) -> int | float | None:
    text = text.strip()
    if not text:
        return None
    return kind(float(text)) if kind is int else kind(text)


def read_batch(path: Path) -> list[dict[str, Any]]:
    """#32's batch CSV as typed rows: numbers as int / float (None when empty, as for an
    infeasible plan), `submitted` as a bool. Raises ValueError naming any missing column or a
    value that isn't a number, so the tab can say what's wrong."""
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        have = reader.fieldnames or []
        missing = [c for c in BATCH_COLUMNS if c not in OPTIONAL_COLUMNS and c not in have]
        if missing:
            raise ValueError(f"{path.name}: missing column(s) {', '.join(missing)} (#32's CSV)")
        rows = []
        for line, raw in enumerate(reader, start=2):
            row: dict[str, Any] = dict(raw)
            try:
                for name in _INTS:
                    row[name] = _number(raw[name], int)
                for name in _FLOATS:
                    row[name] = _number(raw[name], float)
            except ValueError as e:
                raise ValueError(f"{path.name} line {line}: {e}") from e
            row["submitted"] = raw["submitted"].strip().lower() in ("true", "1", "yes")
            rows.append(row)
    return rows


@dataclass(frozen=True)
class BatchSummary:
    """The Batch tab's cards (C16, C17, C22, C43)."""

    n: int
    ok: int
    strict_only: int
    infeasible: int
    input_error: int
    feasible: int  # loaded and not infeasible: #32's denominator (`batch.summary`)
    valid_pct: float | None  # feasible plans with a submission (ok or strict-only); None if none
    at_lb_pct: float | None  # submitted plans whose hub count is the lower bound
    mean_J: float | None  # over submitted plans


def batch_summary(rows: list[dict[str, Any]]) -> BatchSummary:
    """Counts by status, the validity %, the % at the lower bound and the mean J. Validity is
    #32's: submitted plans of the feasible ones (loaded and not infeasible), so the card and
    `hackhive batch`'s summary give one number."""
    status = [r["status"] for r in rows]
    sub = [r for r in rows if r["submitted"]]
    feasible = len(rows) - status.count("input_error") - status.count("infeasible")
    at_lb = [r for r in sub if r["H"] is not None and r["H"] == r["k_lb"]]
    js = [r["J"] for r in sub if r["J"] is not None]
    return BatchSummary(
        n=len(rows),
        ok=status.count("ok"),
        strict_only=status.count("submitted_strict_only"),
        infeasible=status.count("infeasible"),
        input_error=status.count("input_error"),
        feasible=feasible,
        valid_pct=100 * len(sub) / feasible if feasible else None,
        at_lb_pct=100 * len(at_lb) / len(sub) if sub else None,
        mean_J=sum(js) / len(js) if js else None,
    )


def hub_gap_counts(rows: list[dict[str, Any]]) -> dict[int, int]:
    """How many submitted plans use the lower bound's hub count (0), one more (1), …: H − LB."""
    out: dict[int, int] = {}
    for r in rows:
        if r["submitted"] and r["H"] is not None and r["k_lb"] is not None:
            gap = r["H"] - r["k_lb"]
            out[gap] = out.get(gap, 0) + 1
    return dict(sorted(out.items()))


def plans_by_tag(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Plan ids per case-tag id (#83; #108 writes `cases.case_tags` ids), from the optional `tags`
    column; empty without it. `tag_label` gives each id's label."""
    out: dict[str, list[str]] = {}
    for r in rows:
        for tag in filter(None, (t.strip() for t in (r.get("tags") or "").split(";"))):
            out.setdefault(tag, []).append(r["plan_id"])
    return {tag: sorted(ids, key=_id_order) for tag, ids in sorted(out.items())}


def _id_order(plan_id: str) -> tuple[int, str]:
    tail = plan_id.removeprefix("PLAN_")
    return (int(tail), "") if tail.isdigit() else (1 << 62, tail)


def open_target(plan_id: str) -> tuple[str, str] | None:
    """Where the app can open a batch row's plan: ("Test plans", id) for a fixture, else
    ("ResPlan id", id) when the ResPlan file is here; None when neither can."""
    key = plan_id.removeprefix("PLAN_")
    if key in fixture_ids():
        return "Test plans", key
    if RESPLAN_JSONL.exists():
        return "ResPlan id", key
    return None


# --------------------------------------------------------------------------- arrangements (#26)

ARRANGEMENTS_PER_PAGE = 12


def hub_counts(arrangements: Sequence[Any]) -> list[int]:
    """The hub counts present, ascending: the gallery filter's options."""
    return sorted({a.H for a in arrangements})


def filter_by_hubs(arrangements: Sequence[Any], ks: Collection[int]) -> list[tuple[int, Any]]:
    """(rank, arrangement) for those with a hub count in `ks` (all when empty), in rank order;
    the rank is the 1-based place in the full, `rank_key`-sorted list."""
    return [(n, a) for n, a in enumerate(arrangements, start=1) if not ks or a.H in ks]


def page_count(n: int, per: int = ARRANGEMENTS_PER_PAGE) -> int:
    return max(1, -(-n // per))


def page_of(items: Sequence[Any], page: int, per: int = ARRANGEMENTS_PER_PAGE) -> list[Any]:
    """Page `page` (1-based) of `items`; a page past the end gives the last one."""
    page = min(max(page, 1), page_count(len(items), per))
    return list(items[(page - 1) * per : page * per])


def best_by_hub_count(arrangements: Sequence[Any]) -> list[dict[str, Any]]:
    """The gallery header: per hub count, how many arrangements and the best J (the ranking's J,
    D3's worse reading). It replaces #27's Pareto view: fewest hubs always ranks first."""
    rows = []
    for k in hub_counts(arrangements):
        same = [a for a in arrangements if a.H == k]
        rows.append(
            {"Hubs": k, "Arrangements": len(same), "Best J": round(min(a.J for a in same), 3)}
        )
    return rows


def tile_verdict(violations: Sequence[Violation]) -> str:
    """The checker's word on a tile: ✓ valid, ⚠ strict only, ✗ invalid (the mark and the word,
    never colour alone)."""
    if any(v.severity == "error" for v in violations):
        return "✗ invalid"
    return "⚠ strict only" if violations else "✓ valid"


def tile_caption(rank: int, a: Any, m: checker.Metrics, verdict: str) -> str:
    """`#3 · H 3 · J 98.24 · 52.1 m · 40 bends · ✓ valid`: the order key's H and J first (C16,
    C17), then length and bends from the checker mirror, then its verdict."""
    return f"#{rank} · H {a.H} · J {a.J:.2f} · {m.L:.1f} m · {m.B} bends · {verdict}"
