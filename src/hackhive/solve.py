"""The walking skeleton (#16): one plan in, a gated slide-8 submission and its sidecar out.

`solve(plan, c)` runs the stages in order: room graph → every zoning → the minimum-hub zoning
whose first-cut rings are shortest (Manhattan) → hubs and ring order → label raster → routed
rings → the checker mirror. `submit(result, out_dir)` is the gate (C43): an invalid or missing
plan scores zero, so a submission is written only when the checker finds no `error`; with only
`strict` ones it is written and flagged `submitted_strict_only`. The sidecar is always written.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
from shapely.geometry import Point

from hackhive import __version__
from hackhive.checker import HUB_POINT, Metrics, check_rules, metrics, reported_mismatches
from hackhive.contracts import (
    Bounds,
    Constraints,
    CostBreakdown,
    HubMetrics,
    HubOut,
    Plan,
    Provenance,
    Sidecar,
    Submission,
    Violation,
    lower_bound,
)
from hackhive.graph import build_graph, eligible_graph
from hackhive.grid import LabelGrid
from hackhive.io import InputFormatError, load_plan
from hackhive.rings import Hub, first_ring, manhattan
from hackhive.route import RoutedRing, route_rings
from hackhive.zoning import (
    Infeasible,
    TooLarge,
    Zone,
    Zoning,
    eligible_area_and_nodes,
    enumerate_zonings,
    hub_capable_rooms,
    wall_geometry,
)

HubHook = Callable[[list[Hub]], list[Hub]]

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def file_stem(plan_id: str) -> str:
    """The file name a plan's outputs use. Organiser ids are kept verbatim in the submission, but
    one like `../x`, `A/B` or `A:B` would write outside `out_dir` or crash on Windows (#72
    review): every other character becomes `_`, leading dots go, and an empty result is `plan`."""
    return _UNSAFE.sub("_", plan_id).lstrip(".") or "plan"


def _fold_notes(plan: Plan) -> list[str]:
    """C38: each living sliver folded into a neighbour (generator only), as a readable line."""
    return [
        f"{f.room_id} ({f.area_m2:.1f} m²) folded into {f.into_room_id}: "
        "too narrow for a node at the wall clearance (C38)"
        for f in plan.folded_parts
    ]


def _name_notes(plan_id: str) -> list[str]:
    stem = file_stem(plan_id)
    if stem == plan_id:
        return []
    return [f"plan_id {plan_id!r} isn't a safe file name: files are written as {stem}.*"]


@dataclass(frozen=True)
class Result:
    """One plan's outcome. `submission` is the design (None if none was built); `submit` writes
    it only when `sidecar.status` allows."""

    plan: Plan | None
    submission: Submission | None
    sidecar: Sidecar

    @property
    def submittable(self) -> bool:
        return self.submission is not None and self.sidecar.status in (
            "ok",
            "submitted_strict_only",
        )


# --------------------------------------------------------------------------- stages


def _zone_order(zoning: Zoning) -> list[Zone]:
    """Zones in a fixed order (by their lowest room id), so hub ids H1… are deterministic."""
    return sorted(zoning, key=min)


def _first_cut(zoning: Zoning, plan: Plan, g: nx.Graph, c: Constraints) -> tuple[float, list[Hub]]:
    """Hubs and ring orders for one zoning, and the sum of their Manhattan ring lengths."""
    points = {n.id: n.xy for n in plan.nodes}
    hubs, cost = [], 0.0
    for i, zone in enumerate(_zone_order(zoning), start=1):
        hub = first_ring(zone, plan, g, c, hub_id=f"H{i}")
        at = {**points, hub.hub_id: hub.location}
        cost += sum(manhattan(at[a], at[b]) for a, b in zip(hub.ring, hub.ring[1:], strict=False))
        hubs.append(hub)
    return cost, hubs


def _pick(
    zonings: Sequence[Zoning], plan: Plan, g: nx.Graph, c: Constraints
) -> tuple[Zoning, list[Hub]]:
    """The fewest-hub zoning with the shortest first-cut rings (ties: enumeration order).

    A zoning is skipped when a zone has no node (no ring to build) or no hub candidate; the next
    hub count is tried only if every zoning at this one is skipped."""
    with_nodes = {n.room_id for n in plan.nodes}
    reasons: list[str] = []
    for k in sorted({len(z) for z in zonings}):
        best: tuple[float, Zoning, list[Hub]] | None = None
        for zoning in (z for z in zonings if len(z) == k):
            if any(not (zone & with_nodes) for zone in zoning):
                reasons.append("a zone has no node")
                continue
            try:
                cost, hubs = _first_cut(zoning, plan, g, c)
            except (Infeasible, ValueError) as e:  # ValueError: order_ring past 8 nodes (#28)
                reasons.append(str(e))
                continue
            if best is None or cost < best[0] - 1e-9:
                best = (cost, zoning, hubs)
        if best is not None:
            return best[1], best[2]
    raise Infeasible(f"no zoning can be built: {'; '.join(sorted(set(reasons)))}")


def _hub_point_errors(plan: Plan, hubs: Sequence[Hub], c: Constraints) -> list[Violation]:
    """Rule 6 `error`s that make routing impossible: a hub outside its room's free space.

    Everything else is the checker's call once the rings exist; this only stops a hook (or a
    future placement bug) from crashing the router (#16's forced-failure test)."""
    rooms = {r.id: r for r in plan.rooms}
    walls = wall_geometry(plan)
    out = []
    for h in hubs:
        p = Point(h.location)
        room = rooms.get(h.room_id)
        if room is None or not room.polygon.contains(p) or walls.contains(p):
            out.append(
                Violation(
                    rule=HUB_POINT,
                    severity="error",
                    message=f"{h.hub_id} at ({p.x:.3f}, {p.y:.3f}) is not in "
                    f"{h.room_id}'s free space (in a wall or outside the room)",
                    hub_id=h.hub_id,
                    where=[(p.x, p.y)],
                )
            )
    return out


def _submission(
    plan: Plan, hubs: Sequence[Hub], rings: Sequence[RoutedRing], c: Constraints
) -> tuple[Submission, Metrics]:
    """Slide 8 (C48): rooms start with the hub's own, nodes sorted, segments in ring order."""
    out = []
    for hub, ring in zip(hubs, rings, strict=True):
        out.append(
            HubOut.model_validate(
                {
                    "hub_id": hub.hub_id,
                    "room_id": hub.room_id,
                    "location": {"x": hub.location[0], "y": hub.location[1]},
                    "assigned_rooms": hub.assigned_rooms,
                    "assigned_nodes": sorted(hub.ring[1:-1]),
                    "closed_sequence": hub.ring,
                    "route_segments": [s.model_dump(by_alias=True) for s in ring.segments],
                    "ring_length_m": ring.ring_length_m,
                }
            )
        )
    rooms = {r.id: r for r in plan.rooms}
    areas = {h.hub_id: sum(rooms[r].area_m2 for r in h.assigned_rooms) for h in out}
    m = metrics(out, c, hub_areas=areas)
    sub = Submission(
        plan_id=plan.plan_id,
        units="m",
        hubs=out,
        reported_metrics={"hub_count": m.H, "total_length_m": m.L},
    )
    return sub, m


# --------------------------------------------------------------------------- solve


def _sidecar(plan_id: str, c: Constraints, provenance: Provenance, **fields) -> Sidecar:
    return Sidecar(plan_id=plan_id, settings=c, provenance=provenance, **fields)


def _status(violations: Sequence[Violation]) -> str:
    if any(v.severity == "error" for v in violations):
        return "infeasible"
    return "submitted_strict_only" if violations else "ok"


def _rules_reason(violations: Sequence[Violation]) -> str:
    rules = sorted({v.rule for v in violations if v.severity == "error"})
    return "the design breaks " + ", ".join(f"rule {r}" for r in rules)


def solve(
    plan: Plan,
    c: Constraints | None = None,
    *,
    provenance: Provenance | None = None,
    hub_hook: HubHook | None = None,
) -> Result:
    """Design one plan and check it (#16). Never raises for a plan that loaded: anything that
    stops a design (no zoning, a room over the cap, an unroutable leg) ends in the sidecar.

    `hub_hook` is a test hook: it may change the placed hubs before routing (forced failures).
    """
    c = c or Constraints()
    provenance = provenance or Provenance(version=__version__)
    area, nodes = eligible_area_and_nodes(plan)
    k_lb = lower_bound(sum(area.values()), sum(nodes.values()), c)
    notes = [*plan.warnings, *_fold_notes(plan), *_name_notes(plan.plan_id)]
    base = {"warnings": notes, "folded_parts": list(plan.folded_parts), "frame": plan.frame}

    def no_design(reason: str, violations: Sequence[Violation] = ()) -> Result:
        side = _sidecar(
            plan.plan_id,
            c,
            provenance,
            status="infeasible",
            reason=reason,
            violations=list(violations),
            bounds=Bounds(k_lb=k_lb),
            **base,
        )
        return Result(plan, None, side)

    g = eligible_graph(build_graph(plan, c))
    try:
        zonings = enumerate_zonings(g, area, c, nodes=nodes, hub_capable=hub_capable_rooms(plan, c))
        zoning, hubs = _pick(zonings, plan, g, c)
    except Infeasible as e:
        return no_design(f"infeasible: {e}")
    except TooLarge as e:  # D8: no ResPlan plan needs more (largest group: 9 rooms)
        return no_design(
            f"too large for exact zoning: {e}; the greedy fallback (#49) is in the Backlog"
        )

    if hub_hook is not None:
        hubs = hub_hook(list(hubs))
    blocked = _hub_point_errors(plan, hubs, c)
    if blocked:
        return no_design(_rules_reason(blocked), blocked)
    try:
        grid = LabelGrid.from_plan(plan, cell_m=c.grid_m)
    except ValueError as e:
        return no_design(f"grid failed: {e}")
    try:
        rings = route_rings(grid, plan, hubs, c)
    except ValueError as e:
        return no_design(f"routing failed: {e}")

    sub, m = _submission(plan, hubs, rings, c)
    violations = check_rules(plan, sub, c)
    status = _status(violations)
    hub_nodes = {h.hub_id: len(h.assigned_nodes) for h in sub.hubs}
    rooms = {r.id: r for r in plan.rooms}
    side = _sidecar(
        plan.plan_id,
        c,
        provenance,
        status=status,
        reason=_rules_reason(violations) if status == "infeasible" else None,
        violations=violations,
        bounds=Bounds(
            k_lb=k_lb, k_found=None if status == "infeasible" else m.H, proof="exhaustive"
        ),
        cost=CostBreakdown(H=m.H, L=m.L, X=m.X, O=m.O, B=m.B, J=m.J, area_gap=m.area_gap or 0.0),
        hubs=[
            HubMetrics(
                hub_id=h.hub_id,
                area_m2=sum(rooms[r].area_m2 for r in h.assigned_rooms),
                n_nodes=hub_nodes[h.hub_id],
                L=m.per_hub[h.hub_id].L,
                X=m.per_hub[h.hub_id].X,
                O=m.per_hub[h.hub_id].O,
                B=m.per_hub[h.hub_id].B,
            )
            for h in sub.hubs
        ],
        warnings=[*notes, *(f"{h.hub_id}: {w}" for h in hubs for w in h.warnings)],
        folded_parts=list(plan.folded_parts),
        frame=plan.frame,
    )
    return Result(plan, sub, side)


def solve_file(path: str | Path, c: Constraints | None = None) -> Result:
    """Load and solve one plan file. A plan that can't be read (#38's `InputFormatError`, or any
    other load error) ends as sidecar `input_error`, named after the file's plan id or stem."""
    c = c or Constraints()
    path = Path(path)
    data = path.read_bytes()
    provenance = Provenance(
        input_path=str(path),
        input_digest="sha256:" + hashlib.sha256(data).hexdigest(),
        version=__version__,
    )
    try:
        plan = load_plan(path, c)
    except (InputFormatError, ValueError) as e:
        pid = _plan_id_of(data, path)
        side = _sidecar(
            pid,
            c,
            provenance,
            status="input_error",
            reason=f"input_error: {e}",
            warnings=_name_notes(pid),
        )
        return Result(None, None, side)
    return solve(plan, c, provenance=provenance)


def _plan_id_of(data: bytes, path: Path) -> str:
    """The id an unreadable plan's sidecar is filed under: its own `plan_id`, else the stem."""
    try:
        raw = json.loads(data)
    except ValueError:
        return path.stem
    pid = raw.get("plan_id") if isinstance(raw, dict) else None
    return pid if isinstance(pid, str) and pid else path.stem


# --------------------------------------------------------------------------- the gate


def submit(result: Result, out_dir: str | Path) -> list[Path]:
    """Write `<plan_id>.json` only if the design has no `error` violation, and always
    `<plan_id>.sidecar.json` (C43), named by `file_stem`. The written submission is read back
    and its reported values compared with its polylines: a mismatch is a sidecar warning, never a
    reason to withhold it (C42). Returns the paths written."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = file_stem(result.sidecar.plan_id)
    written = []
    side = result.sidecar
    if result.submittable:
        path = out / f"{stem}.json"
        path.write_text(result.submission.model_dump_json(indent=2) + "\n", encoding="utf-8")
        written.append(path)
        # read it back, as the organisers will, and compare what we report with the cable
        back = Submission.model_validate_json(path.read_text(encoding="utf-8"))
        if mismatches := reported_mismatches(back, side.settings):
            side = side.model_copy(update={"warnings": [*side.warnings, *mismatches]})
    path = out / f"{stem}.sidecar.json"
    path.write_text(side.model_dump_json(indent=2) + "\n", encoding="utf-8")
    written.append(path)
    return written
