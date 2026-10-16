"""One plan in, a gated slide-8 submission and its sidecar out (#16, #22).

`enumerate(plan, c)` ranks every arrangement: each zoning with each zone's routed choice of hub
spot and ring order (`rings.best_ring`, once per zone however many zonings share it), its rings
routed alone and scored together by the checker mirror under both counting readings, sorted by
`rank_key` (H, the worse J, area gap, signature). `solve(plan, c)` fully routes (`route`: the
rip-up passes) and checks the arrangements in rank order, one hub count at a time, and submits the
first clean one, else the best strict-only one; the walking skeleton's first cut (#16) is the last
candidate. `submit(result, out_dir)` is the gate (C43): an invalid or missing plan scores zero, so
a submission is written only when the checker finds no `error`; with only `strict` ones it is
written and flagged `submitted_strict_only`. The sidecar is always written.

`enumerate` shadows the builtin in this module, as the spec names it: use `builtins.enumerate`.
"""

from __future__ import annotations

import argparse
import builtins
import dataclasses
import functools
import hashlib
import json
import re
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
from shapely.geometry import Point

from hackhive import __version__
from hackhive.checker import (
    HUB_POINT,
    READINGS,
    Metrics,
    area_gap,
    check_rules,
    metrics,
    reported_mismatches,
    segment_length,
)
from hackhive.contracts import (
    ArrangementUsed,
    Bounds,
    Constraints,
    CostBreakdown,
    HubMetrics,
    HubOut,
    Plan,
    Provenance,
    RouteSegment,
    Sidecar,
    Submission,
    Violation,
    lower_bound,
)
from hackhive.graph import build_graph, eligible_graph
from hackhive.grid import LabelGrid
from hackhive.io import InputFormatError, load_plan
from hackhive.parallel import map_in_order
from hackhive.rings import Hub, best_ring_routed, first_ring, manhattan
from hackhive.route import RoutedRing, Routing, route_passes, route_rings
from hackhive.what_if import what_if_note
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
    it only when `sidecar.status` allows and the settings are the problem statement: a WHAT-IF
    design is shown, never submitted (#30, C29)."""

    plan: Plan | None
    submission: Submission | None
    sidecar: Sidecar
    graph: nx.Graph | None = None  # solve()'s room graph, reused for case tags (#32)

    @property
    def submittable(self) -> bool:
        return (
            self.submission is not None
            and self.sidecar.status in ("ok", "submitted_strict_only")
            and self.sidecar.settings.is_problem_statement
        )

    @property
    def what_if(self) -> bool:
        """The settings leave the problem statement (#30): never a submission."""
        return not self.sidecar.settings.is_problem_statement


# --------------------------------------------------------------------------- stages


def _zone_order(zoning: Zoning) -> list[Zone]:
    """Zones in a fixed order (by their lowest room id), so hub ids H1… are deterministic."""
    return sorted(zoning, key=min)


def _first_cut(zoning: Zoning, plan: Plan, g: nx.Graph, c: Constraints) -> tuple[float, list[Hub]]:
    """Hubs and ring orders for one zoning, and the sum of their Manhattan ring lengths."""
    points = {n.id: n.xy for n in plan.nodes}
    hubs, cost = [], 0.0
    for i, zone in builtins.enumerate(_zone_order(zoning), start=1):
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


DECIMALS = 4  # T6 (#34): 0.1 mm, well inside the checker's 1 mm tolerance
SAME_M = 1e-9  # float noise only (13.450000000000001), never the checker's 1 mm


def _rounded(polyline: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """T6 (#34): every vertex to `DECIMALS`. A coordinate equal (up to float noise) to the
    previous vertex's takes that vertex's rounded value, so each segment stays exactly
    axis-aligned; a real sub-mm jog keeps its own value, so it never drifts along the run (#102
    review). A jog under half a step rounds onto the previous point and is dropped."""
    out: list[tuple[float, float]] = []
    for i, (x, y) in builtins.enumerate(polyline):
        rx, ry = round(x, DECIMALS), round(y, DECIMALS)
        if out:
            px, py = polyline[i - 1]
            rx = out[-1][0] if abs(x - px) <= SAME_M else rx
            ry = out[-1][1] if abs(y - py) <= SAME_M else ry
            if (rx, ry) == out[-1] and (len(out) > 1 or i < len(polyline) - 1):
                continue
        out.append((rx, ry))
    return out


def _hub_out(hub: Hub, segments: Sequence[RouteSegment], ring_length_m: float = 0.0) -> HubOut:
    """Slide 8 (C48): rooms start with the hub's own, nodes sorted, segments in ring order."""
    return HubOut.model_validate(
        {
            "hub_id": hub.hub_id,
            "room_id": hub.room_id,
            "location": {"x": hub.location[0], "y": hub.location[1]},
            "assigned_rooms": hub.assigned_rooms,
            "assigned_nodes": sorted(hub.ring[1:-1]),
            "closed_sequence": hub.ring,
            "route_segments": [s.model_dump(by_alias=True) for s in segments],
            "ring_length_m": ring_length_m,
        }
    )


def _submission(
    plan: Plan, hubs: Sequence[Hub], rings: Sequence[RoutedRing], c: Constraints
) -> tuple[Submission, Metrics]:
    """Coordinates are rounded (T6) and every length recomputed from the rounded polylines."""
    out = []
    for hub, ring in zip(hubs, rings, strict=True):
        segments = []
        for s in ring.segments:
            poly = _rounded(s.polyline)
            segments.append(
                s.model_copy(
                    update={"polyline": poly, "length_m": round(segment_length(poly), DECIMALS)}
                )
            )
        x, y = hub.location
        rounded_hub = dataclasses.replace(hub, location=(round(x, DECIMALS), round(y, DECIMALS)))
        ring_m = round(sum(s.length_m for s in segments), DECIMALS)  # the written lengths' sum
        out.append(_hub_out(rounded_hub, segments, ring_m))
    rooms = {r.id: r for r in plan.rooms}
    areas = {h.hub_id: sum(rooms[r].area_m2 for r in h.assigned_rooms) for h in out}
    m = metrics(out, c, hub_areas=areas)
    sub = Submission(
        plan_id=plan.plan_id,
        units="m",
        hubs=out,
        reported_metrics={
            "hub_count": m.H,
            "total_length_m": round(sum(h.ring_length_m for h in out), DECIMALS),
        },
    )
    return sub, m


# --------------------------------------------------------------------------- ranking (#22)

_ZONE_HUB = "H"  # a zone's hub id while memoised; each zoning renames it H1… in zone order


@dataclass(frozen=True)
class Arrangement:
    """One zoning, each zone with its routed choice of hub spot and ring order (#20), costed with
    every ring routed alone (pass 0; `route` runs the rip-up passes)."""

    zoning: Zoning
    hubs: list[Hub]  # H1… in zone order
    rings: list[list[RouteSegment]]  # each hub's ring routed alone, in hub order
    J_by_reading: dict[str, float]  # checker.metrics' J under each counting reading (D3)
    area_gap: float  # max − min of the hubs' assigned eligible area (C47)
    signature: str  # the zone signature: the last tie-break, for determinism only

    @property
    def H(self) -> int:
        return len(self.hubs)

    @property
    def J(self) -> float:
        """The worse reading's J (D3): ranking by it submits the design whose worse J is lowest."""
        return max(self.J_by_reading.values())


def rank_key(a: Arrangement) -> tuple[int, float, float, str]:
    """Fewest hubs, then J to 1e-6 m (float noise never decides), then the smaller area gap, then
    the zone signature (slides 3, 6; C16, C47; decision_conventions §7)."""
    return (a.H, round(a.J, 6), a.area_gap, a.signature)


def signature(zoning: Zoning) -> str:
    """The zone signature: each zone's rooms, zones in hub order, e.g. "bedroom_0+living_0|…"."""
    return "|".join("+".join(sorted(zone)) for zone in _zone_order(zoning))


def _renamed(hub: Hub, segments: list[RouteSegment], hub_id: str) -> tuple[Hub, list[RouteSegment]]:
    """A memoised zone ring under the hub id its zoning gives it."""

    def swap(stop: str) -> str:
        return hub_id if stop == hub.hub_id else stop

    return (
        dataclasses.replace(hub, hub_id=hub_id, ring=[swap(s) for s in hub.ring]),
        [s.model_copy(update={"from_": swap(s.from_), "to": swap(s.to)}) for s in segments],
    )


MIN_POOL_ZONES = 4  # fewer zones to route are routed inline: starting workers costs ~1 s


def _zone_ring(
    zone: Zone, plan: Plan, g: nx.Graph, grid: LabelGrid, c: Constraints
) -> tuple[Hub, list[RouteSegment]] | None:
    """A zone's `best_ring` and its ring routed alone (pass 0), or None if the zone can't be built
    (no node, no hub spot, a ring that can't be ordered or routed). Module-level, so a worker
    process can import it (`parallel.map_in_order`)."""
    try:
        hub, segments = best_ring_routed(zone, plan, g, grid, c, hub_id=_ZONE_HUB)
    except (Infeasible, ValueError):  # ValueError: no node, > 8 nodes
        return None
    return None if segments is None else (hub, segments)


def _ranker(
    plan: Plan, g: nx.Graph, grid: LabelGrid, c: Constraints, workers: int | None = None
) -> Callable[[Sequence[Zoning]], list[Arrangement]]:
    """Ranks zonings into arrangements. Each zone's ring is built once for the ranker's life (so
    `solve` can rank one hub count at a time), and a call's new zones are built in parallel
    (`workers` as in `parallel.map_in_order`): 13344's 21 three-hub zones take 32 s on one core,
    11 s on four (#22 review). A zoning with a zone that can't be built is left out.
    """
    area = {r.id: r.area_m2 for r in plan.rooms}
    memo: dict[Zone, tuple[Hub, list[RouteSegment]] | None] = {}

    def rank(zonings: Sequence[Zoning]) -> list[Arrangement]:
        todo = sorted({z for zoning in zonings for z in zoning} - memo.keys(), key=sorted)
        build = functools.partial(_zone_ring, plan=plan, g=g, grid=grid, c=c)
        pool = workers if len(todo) >= MIN_POOL_ZONES else 1
        memo.update(zip(todo, map_in_order(build, todo, pool), strict=True))
        out = []
        for zoning in zonings:
            built = [memo[zone] for zone in _zone_order(zoning)]
            if any(b is None for b in built):
                continue
            named = [_renamed(*b, f"H{i}") for i, b in builtins.enumerate(built, start=1)]
            hubs = [hub for hub, _ in named]
            rings = [segs for _, segs in named]
            outs = [_hub_out(hub, segs) for hub, segs in named]
            J = {reading: metrics(outs, c, reading=reading).J for reading in READINGS}
            gap = area_gap(sum(area[r] for r in hub.assigned_rooms) for hub in hubs)
            out.append(Arrangement(zoning, hubs, rings, J, gap, signature(zoning)))
        return sorted(out, key=rank_key)

    return rank


def _zonings(plan: Plan, g: nx.Graph, c: Constraints) -> list[Zoning]:
    area, nodes = eligible_area_and_nodes(plan)
    return enumerate_zonings(g, area, c, nodes=nodes, hub_capable=hub_capable_rooms(plan, c))


def enumerate(
    plan: Plan, c: Constraints | None = None, k: int | None = None, *, workers: int | None = None
) -> list[Arrangement]:
    """Every arrangement (C29), or only those with `k` hubs, ranked by `rank_key`: for each
    zoning, each zone's `best_ring` (once per zone, in parallel over `workers`), its ring routed
    alone, and the zoning's totals by the checker mirror under both counting readings (D3),
    crossings and overlaps between its rings included. J is pass 0's (before the rip-up passes).
    Raises `Infeasible` or `TooLarge` as `enumerate_zonings` does, and ValueError if the grid
    can't be built.

    ponytail: every zone's best_ring is routed, ~1.2 s a zone on one core (13344: 63 zones). A
    lower bound can't prune it: `rings.shortlist`'s sits 18-38 m under 13344's three-hub J (#22
    review). Speed up the corner scoring (`route._best_corners`) if the gallery needs it faster.
    """
    c = c or Constraints()
    g = eligible_graph(build_graph(plan, c))
    zonings = [z for z in _zonings(plan, g, c) if k is None or len(z) == k]
    return _ranker(plan, g, LabelGrid.from_plan(plan, cell_m=c.grid_m), c, workers)(zonings)


def route(arrangement: Arrangement, plan: Plan, c: Constraints | None = None) -> Routing:
    """An arrangement's full geometry on demand: every ring after the rip-up passes (#80)."""
    c = c or Constraints()
    return route_passes(LabelGrid.from_plan(plan, cell_m=c.grid_m), plan, arrangement.hubs, c)


def zoning_count(plan: Plan, c: Constraints | None = None) -> int:
    """How many zonings `enumerate` would rank (at most one arrangement each): cheap, it builds
    no ring, so the gallery can say what ranking will cost before it starts (#26). 0 when the
    plan has no zoning."""
    c = c or Constraints()
    try:
        return len(_zonings(plan, eligible_graph(build_graph(plan, c)), c))
    except (Infeasible, TooLarge):
        return 0


def arrangement_submission(plan: Plan, a: Arrangement, c: Constraints | None = None) -> Submission:
    """An arrangement as a slide-8 submission with its cached rings (pass 0, each routed alone):
    what the gallery's tile draws and checks (#26). Never written as a submission."""
    c = c or Constraints()
    outs = [_hub_out(hub, segs) for hub, segs in zip(a.hubs, a.rings, strict=True)]
    m = metrics(outs, c)
    return Submission(
        plan_id=plan.plan_id,
        units="m",
        hubs=outs,
        reported_metrics={"hub_count": m.H, "total_length_m": m.L},
    )


def routed_submission(plan: Plan, a: Arrangement, c: Constraints | None = None) -> Submission:
    """An arrangement fully routed (`route`: the rip-up passes), as the Best tab shows it when
    the gallery opens it (#26): the same geometry `solve` would submit for it."""
    c = c or Constraints()
    return _submission(plan, a.hubs, route(a, plan, c).rings, c)[0]


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


def _proof(k_found: int | None, k_lb: int, fewest: int) -> str:
    """What `k_found` is proven to be: `lb_met` at the lower bound; `exhaustive` at the fewest
    hubs any zoning uses (every zoning was enumerated); else `none`: a count with fewer hubs had
    only strict-only designs, or zonings that couldn't be built (#22 review)."""
    if k_found == k_lb:
        return "lb_met"
    return "exhaustive" if k_found == fewest else "none"


def _why_not(violations: Sequence[Violation]) -> str:
    """Why a design with these flags wasn't the first choice ("" if clean)."""
    if _status(violations) == "infeasible":
        return _rules_reason(violations)
    rules = sorted({v.rule for v in violations})
    return "strict only: " + ", ".join(f"rule {r}" for r in rules) if rules else ""


@dataclass(frozen=True)
class _Design:
    """One candidate, fully routed and checked; `submission` is None when its hubs can't be
    routed (a hub in a wall, an unroutable leg)."""

    rank: int | None  # 1 = enumerate()'s first; None: the walking skeleton
    signature: str
    hubs: list[Hub]
    submission: Submission | None
    metrics: Metrics | None
    violations: list[Violation]
    why_not: str  # "" when clean

    @property
    def label(self) -> str:
        return f"#{self.rank} {self.signature}" if self.rank else f"skeleton {self.signature}"


def _design(
    plan: Plan, grid: LabelGrid, rank: int | None, sig: str, hubs: list[Hub], c: Constraints
) -> _Design:
    blocked = _hub_point_errors(plan, hubs, c)
    if blocked:
        return _Design(rank, sig, hubs, None, None, blocked, _rules_reason(blocked))
    try:
        rings = route_rings(grid, plan, hubs, c)
    except ValueError as e:
        return _Design(rank, sig, hubs, None, None, [], f"routing failed: {e}")
    sub, m = _submission(plan, hubs, rings, c)
    violations = check_rules(plan, sub, c)
    return _Design(rank, sig, hubs, sub, m, violations, _why_not(violations))


def solve(
    plan: Plan,
    c: Constraints | None = None,
    *,
    provenance: Provenance | None = None,
    hub_hook: HubHook | None = None,
    workers: int | None = None,
) -> Result:
    """Design one plan and check it (#16, #22). Never raises for a plan that loaded: anything
    that stops a design (no zoning, a room over the cap, an unroutable leg) ends in the sidecar.

    The candidates are `enumerate`'s arrangements in rank order, ranked one hub count at a time
    (H is the first key, so a later count is ranked only if an earlier one has no clean design),
    then the walking skeleton's first cut (#16). Each is fully routed (the rip-up passes) and
    checked; the first with no violation is submitted, else the best-ranked with only `strict`
    ones (`submitted_strict_only`), else none: the best-ranked failure is reported (C43). Once a
    design with no `error` is found, a clean one is looked for at most one hub count above it
    (Adi, #22 review), so a plan where everything is strict-only doesn't route every zoning.

    `hub_hook` is a test hook: it may change each candidate's hubs before routing (forced
    failures); it's called once per candidate, in rank order. `workers` builds the zones in
    parallel (`parallel.map_in_order`); a test that monkeypatches the ring builders passes 1.
    """
    c = c or Constraints()
    provenance = provenance or Provenance(version=__version__)
    area, nodes = eligible_area_and_nodes(plan)
    k_lb = lower_bound(sum(area.values()), sum(nodes.values()), c)
    notes = [*plan.warnings, *_fold_notes(plan), *_name_notes(plan.plan_id)]
    if note := what_if_note(c):  # #30: labelled, and `Result.submittable` keeps it unwritten
        notes.insert(0, note)
    G = build_graph(plan, c)
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
        return Result(plan, None, side, G)

    def designed(d: _Design, skipped: Sequence[_Design]) -> Result:
        sub, m, violations = d.submission, d.metrics, d.violations
        status = _status(violations)
        k_found = None if status == "infeasible" else m.H
        hub_nodes = {h.hub_id: len(h.assigned_nodes) for h in sub.hubs}
        rooms = {r.id: r for r in plan.rooms}
        used = ArrangementUsed(
            rank=d.rank, signature=d.signature, skipped=[f"{s.label}: {s.why_not}" for s in skipped]
        )
        side = _sidecar(
            plan.plan_id,
            c,
            provenance,
            status=status,
            reason=_rules_reason(violations) if status == "infeasible" else None,
            violations=violations,
            bounds=Bounds(k_lb=k_lb, k_found=k_found, proof=_proof(k_found, k_lb, fewest)),
            arrangement=None if status == "infeasible" else used,
            cost=CostBreakdown(
                H=m.H, L=m.L, X=m.X, O=m.O, B=m.B, J=m.J, area_gap=m.area_gap or 0.0
            ),
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
            warnings=[*notes, *(f"{h.hub_id}: {w}" for h in d.hubs for w in h.warnings)],
            folded_parts=list(plan.folded_parts),
            frame=plan.frame,
        )
        return Result(plan, sub, side, G)

    g = eligible_graph(G)
    try:
        zonings = _zonings(plan, g, c)
    except Infeasible as e:
        return no_design(f"infeasible: {e}")
    except TooLarge as e:  # D8: no ResPlan plan needs more (largest group: 9 rooms)
        return no_design(
            f"too large for exact zoning: {e}; the greedy fallback (#49) is in the Backlog"
        )
    try:
        grid = LabelGrid.from_plan(plan, cell_m=c.grid_m)
    except ValueError as e:
        return no_design(f"grid failed: {e}")

    fewest = min(len(z) for z in zonings)
    rank = _ranker(plan, g, grid, c, workers)
    no_skeleton: list[str] = []
    usable_at: int | None = None  # H of the first design with no `error`

    def candidates() -> Iterator[tuple[int | None, str, list[Hub]]]:
        n = 0
        for k in sorted({len(z) for z in zonings}):
            if usable_at is not None and k > usable_at + 1:
                return  # a clean design is looked for one hub count up, no further
            for a in rank([z for z in zonings if len(z) == k]):
                n += 1
                yield n, a.signature, a.hubs
        try:
            zoning, hubs = _pick(zonings, plan, g, c)
        except Infeasible as e:
            no_skeleton.append(f"infeasible: {e}")
            return
        yield None, signature(zoning), hubs

    tried: list[_Design] = []
    for n, sig, hubs in candidates():
        d = _design(plan, grid, n, sig, hubs if hub_hook is None else hub_hook(list(hubs)), c)
        if d.submission is not None and not d.violations:
            return designed(d, tried)
        tried.append(d)
        if usable_at is None and d.submission is not None and _status(d.violations) != "infeasible":
            usable_at = len(d.hubs)
    for i, d in builtins.enumerate(tried):
        if d.submission is not None and _status(d.violations) != "infeasible":
            return designed(d, tried[:i])
    if not tried:
        return no_design(no_skeleton[0])
    first = tried[0]
    if first.submission is None:
        return no_design(first.why_not, first.violations)
    return designed(first, [])  # status infeasible: shown with its violations, never written


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


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    """``python -m hackhive.solve --list <plan.json>``: every arrangement, ranked (#22). J is pass
    0's, each ring routed alone: `solve()` submits after the rip-up passes, so its J can differ."""
    p = argparse.ArgumentParser(prog="python -m hackhive.solve")
    help_ = "print the ranked table (J: pass 0, before the rip-up passes)"
    p.add_argument("--list", action="store_true", required=True, help=help_)
    p.add_argument("plan", type=Path)
    args = p.parse_args(argv)
    c = Constraints()
    try:
        ranked = enumerate(load_plan(args.plan, c), c)
    except (OSError, ValueError, Infeasible, TooLarge) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"{'#':>3} {'H':>2} {'J pass 0':>9} {'strict':>9} {'lenient':>9} {'area gap':>8}  zones")
    for i, a in builtins.enumerate(ranked, start=1):
        strict, lenient = a.J_by_reading["strict"], a.J_by_reading["lenient"]
        print(
            f"{i:>3} {a.H:>2} {a.J:>9.3f} {strict:>9.3f} {lenient:>9.3f} {a.area_gap:>8.2f}  {a.signature}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
