"""Hub placement and ring order: a first cut on Manhattan distance (#14), and the routed choice
over a lower-bound shortlist (#20; #28 adds Held-Karp).

Grid convention (shared with #10's raster): cell k spans ``[k, k + 1) · grid_m`` on each axis,
anchored at the plan origin, and its centre sits half a cell in, so a hub placed on a centre
needs no routing stub.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import networkx as nx
import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from hackhive.contracts import Constraints, Plan
from hackhive.zoning import HUB_MARGIN_M, Infeasible, wall_geometry

if TYPE_CHECKING:  # grid imports this module, so these are for type hints only
    from hackhive.checker import RuleContext
    from hackhive.grid import LabelGrid

Point2 = tuple[float, float]

CELL_CENTRE_OFFSET = 0.5  # not-a-weight: a cell centre is half a cell in from its edge
NODE_KEEP_OFF_M = 0.10  # a hub may share a node's room but never its point (conventions §4)
MAX_BRUTE_FORCE_NODES = 8  # 8! = 40,320 orders; beyond that is #28's Held-Karp
# best_ring (#20): every ring order is bounded up to this many nodes (6!/2 = 360; the cap is 5);
# past it (the ≤ 20-node what-if, #28) only first_ring's order is
MAX_ORDER_NODES = 6
SHORTLIST_ORDERS = 2  # the ring orders with the lowest bound that are routed (#20)
# legs routed per zone: (spot, order) pairs = this // (k + 1), first_ring's own included, so a
# one-node ring tries 24 spots (~10 ms each) and a five-node ring 8 (~0.35 s each)
ROUTE_BUDGET_LEGS = 48
# spots of one room and order on the shortlist sit ≥ this far apart (L∞): a hub a few cells
# along can turn a crossing ring into a clean one (5924's one-node rings: J 7.9 → 4.0)
SPOT_SPACING_M = 0.2
LEG_SLOP_M = 0.01  # per leg: what squaring and cleaning a polyline can change its length by


@dataclass(frozen=True)
class Candidate:
    room_id: str
    xy: Point2
    wall_clearance_only: bool = False  # only >= 0.51 m from walls, not from the room outline


@dataclass(frozen=True)
class Hub:
    hub_id: str
    room_id: str
    location: Point2
    ring: list[str]  # [hub_id, node ids in visiting order, hub_id]
    assigned_rooms: list[str]  # the zone, hub's room first (slide 8)
    warnings: list[str] = field(default_factory=list)


def manhattan(a: Point2, b: Point2) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def manhattan_inf(a: Point2, b: Point2) -> float:
    """The L∞ distance: the larger of the two axis offsets."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def hub_candidates(zone: Collection[str], plan: Plan, c: Constraints) -> list[Candidate]:
    """Grid cell centres in the zone's rooms where a hub may go.

    First choice: >= hub clearance + 1 cm from both the room's whole outline and the wall
    geometry, and >= 0.10 m from every node point. Only if the zone has none: the same
    distance from wall geometry alone (open passages and door gaps don't count), tagged
    ``wall_clearance_only``.
    """
    clearance = c.hub_wall_clearance_m + HUB_MARGIN_M
    rooms = sorted((r for r in plan.rooms if r.id in zone and r.eligible), key=lambda r: r.id)
    nodes = np.array([n.xy for n in plan.nodes]) if plan.nodes else np.empty((0, 2))

    # ResPlan rooms can run under walls (1293's living_0), so the outline alone isn't enough:
    # every candidate must also clear the wall geometry itself (rule 6).
    keep_out = wall_geometry(plan).buffer(clearance)
    strict = [
        Candidate(r.id, xy)
        for r in rooms
        for xy in _centres_in(r.polygon.buffer(-clearance).difference(keep_out), nodes, c.grid_m)
    ]
    if strict:
        return strict
    return [
        Candidate(r.id, xy, wall_clearance_only=True)
        for r in rooms
        for xy in _centres_in(r.polygon.difference(keep_out), nodes, c.grid_m)
    ]


def first_ring(
    zone: Collection[str], plan: Plan, g: nx.Graph, c: Constraints, hub_id: str = "H1"
) -> Hub:
    """Place the zone's hub and order its ring on Manhattan distance.

    The hub goes in the zone room with the most door/opening links inside the zone (tie: the
    larger room, then the id), at the candidate closest to the zone's nodes in total.
    """
    zone = frozenset(zone)
    nodes = sorted((n for n in plan.nodes if n.room_id in zone), key=lambda n: n.id)
    if not nodes:
        raise ValueError(f"zone {sorted(zone)} has no nodes to visit")
    cands = hub_candidates(zone, plan, c)
    if not cands:
        raise Infeasible(f"no hub candidate in zone {', '.join(sorted(zone))}")

    area = {r.id: r.polygon.area for r in plan.rooms}
    inside = g.subgraph(zone)

    def links(room: str) -> int:
        return sum(1 for nb in inside.neighbors(room) if nb != room) if room in inside else 0

    room = min({cand.room_id for cand in cands}, key=lambda r: (-links(r), -area[r], r))
    # to the µm, so equal totals tie on xy whatever the float sum order (3086's hub moved a cell
    # between Python 3.11 and 3.12+, whose sum() is compensated; #79 CI)
    best = min(
        (cand for cand in cands if cand.room_id == room),
        key=lambda cand: (round(sum(manhattan(cand.xy, n.xy) for n in nodes), 6), cand.xy),
    )

    points = {hub_id: best.xy, **{n.id: n.xy for n in nodes}}
    ring = order_ring(hub_id, [n.id for n in nodes], lambda a, b: manhattan(points[a], points[b]))
    warnings = ["wall_clearance_only"] if best.wall_clearance_only else []
    assigned = [room, *sorted(zone - {room})]
    return Hub(hub_id, room, best.xy, ring, assigned, warnings)


def order_ring(hub: str, nodes: Sequence[str], dist: Callable[[str, str], float]) -> list[str]:
    """The cheapest closed ring [hub, …, hub] over ``nodes`` by brute force over all k! orders.

    Both directions of each cycle are tried, so ``dist`` may be asymmetric (routed costs with
    node bends, #20). Ties go to the lexicographically smallest order (deterministic).
    """
    if not nodes:
        raise ValueError("a ring needs at least one node")
    if len(nodes) > MAX_BRUTE_FORCE_NODES:
        raise ValueError(f"{len(nodes)} nodes > {MAX_BRUTE_FORCE_NODES}: use Held-Karp (#28)")
    best: tuple[str, ...] | None = None
    best_cost = math.inf
    for perm in itertools.permutations(sorted(nodes)):
        cost = dist(hub, perm[0]) + dist(perm[-1], hub)
        cost += sum(dist(a, b) for a, b in itertools.pairwise(perm))
        if cost < best_cost - 1e-9:
            best, best_cost = perm, cost
    assert best is not None
    return [hub, *best, hub]


def _centres_in(region: BaseGeometry, nodes: np.ndarray, grid_m: float) -> list[Point2]:
    """Grid cell centres inside ``region`` that are >= 0.10 m from every node."""
    if region.is_empty:
        return []
    x0, y0, x1, y1 = region.bounds
    kx = np.arange(math.floor(x0 / grid_m), math.ceil(x1 / grid_m) + 1)
    ky = np.arange(math.floor(y0 / grid_m), math.ceil(y1 / grid_m) + 1)
    xs, ys = np.meshgrid((kx + CELL_CENTRE_OFFSET) * grid_m, (ky + CELL_CENTRE_OFFSET) * grid_m)
    xs, ys = xs.ravel(), ys.ravel()
    keep = shapely.contains_xy(region, xs, ys)
    if len(nodes):
        d = np.hypot(xs[:, None] - nodes[None, :, 0], ys[:, None] - nodes[None, :, 1])
        keep &= d.min(axis=1) >= NODE_KEEP_OFF_M
    return [(float(x), float(y)) for x, y in zip(xs[keep], ys[keep], strict=True)]


# --------------------------------------------------------------------------- best_ring (#20)


@dataclass(frozen=True, order=True)
class RingCost:
    """A routed ring's cost, compared in this order: validity first (the plan scores zero if a
    flag stands), then J (#93 review's ranking, per ring)."""

    errors: int  # this hub's rule-4-6 `error` flags (check_rules)
    strict: int  # this hub's rule-4-6 `strict` flags: valid only if the organisers agree
    J: float  # checker.metrics on the routed polylines, stubs and junction bends included


@dataclass
class _Routing:
    """What every ring of one zone is routed and checked with: the zone's grid (D7), the
    single-door nodes, the checker's rule context (its free space is rule 5's, so stubs and
    checker agree) and the legs routed so far (`route._prepare`'s memo)."""

    grid: LabelGrid
    retrace_ok: frozenset[str]
    rules: RuleContext
    memo: dict = field(default_factory=dict)


def _routing(zone: Collection[str], plan: Plan, grid: LabelGrid, c: Constraints) -> _Routing:
    from hackhive.checker import RuleContext  # checker imports grid, which imports us
    from hackhive.route import single_door_nodes, zone_grid

    retrace_ok = single_door_nodes(plan, c)  # its build_graph fills the openings rule 5 needs
    return _Routing(zone_grid(grid, plan, zone, c), retrace_ok, RuleContext(plan, c))


def _cost(hub: Hub, plan: Plan, c: Constraints, r: _Routing) -> RingCost:
    from hackhive.checker import hub_rules, metrics  # checker imports grid, which imports us
    from hackhive.contracts import HubOut
    from hackhive.route import Stop, _route_ring

    nodes = {n.id: n for n in plan.nodes}
    stops = [
        Stop(hub.hub_id, hub.location, hub.room_id),
        *(Stop(n, nodes[n].xy, nodes[n].room_id) for n in hub.ring[1:-1]),
    ]
    segments, _ = _route_ring(r.grid, stops, c, r.retrace_ok, r.rules.space.free, None, r.memo)
    out = HubOut.model_validate(
        {
            "hub_id": hub.hub_id,
            "room_id": hub.room_id,
            "location": {"x": hub.location[0], "y": hub.location[1]},
            "assigned_rooms": hub.assigned_rooms,
            "assigned_nodes": sorted(hub.ring[1:-1]),
            "closed_sequence": hub.ring,
            "route_segments": [s.model_dump(by_alias=True) for s in segments],
            "ring_length_m": 0.0,
        }
    )
    # rules 1-3 are the zoning's, the same for every ring of the zone; rules 4-6 are the ring's
    flags = [v for v in hub_rules(out, r.rules) if v.rule >= 4]
    return RingCost(
        sum(v.severity == "error" for v in flags),
        sum(v.severity == "strict" for v in flags),
        metrics([out], c).J,
    )


def ring_cost(hub: Hub, plan: Plan, grid: LabelGrid, c: Constraints) -> RingCost:
    """The hub's ring routed alone on its zone's grid (`route.route_ring`, D7), and its cost."""
    return _cost(hub, plan, c, _routing(hub.assigned_rooms, plan, grid, c))


def _orders(nodes: Sequence[str]) -> list[tuple[str, ...]]:
    """Every closed ring order over ``nodes`` once: a ring and its reverse are the same cable, so
    only the direction whose first node sorts first (k!/2 orders; one for k ≤ 2)."""
    return [p for p in itertools.permutations(sorted(nodes)) if len(p) < 2 or p[0] <= p[-1]]


def shortlist(
    zone: Collection[str],
    plan: Plan,
    g: nx.Graph,
    grid: LabelGrid,
    c: Constraints,
    hub_id: str = "H1",
    *,
    first: Hub | None = None,
) -> list[tuple[float, Hub]]:
    """The (spot, order) pairs `best_ring` routes, as (lower bound, hub), lowest bound first.

    Every hub candidate (`hub_candidates`) and every ring order (k!/2) is bounded by the sum of
    its legs' cached costs (`route.PathCache` on the zone's grid: steps and bends inside a leg,
    in J metres), less what each leg's two stubs can save (the L1 offset of an exact point from
    its cell centre, plus `LEG_SLOP_M`). Crossings, overlaps and bends at nodes and the hub only
    add to J, so the bound never exceeds the routed J. They also dominate it (a crossing is 5),
    so the bound can't prune the full space; the shortlist is the `SHORTLIST_ORDERS` orders with
    the lowest bound, each with its lowest-bound spot in every zone room, and first_ring's own
    pair (so `best_ring` never loses to it). The rest of the budget (`ROUTE_BUDGET_LEGS` legs,
    so `// (k + 1)` pairs) is filled round-robin:
    every (order, room)'s lowest-bound spot, then each one's next spot at least `SPOT_SPACING_M`
    from those already taken, and so on, since a spot a few cells along can avoid a crossing.
    """
    from hackhive.route import PathCache

    zone = frozenset(zone)
    first = first or first_ring(zone, plan, g, c, hub_id=hub_id)
    r = _routing(zone, plan, grid, c)
    nodes = {n.id: n for n in plan.nodes if n.room_id in zone}
    w = c.cost_weights.length
    try:
        node_cells = {nid: r.grid.snap(n.xy, n.room_id)[0] for nid, n in nodes.items()}
    except ValueError:  # routing would fail on this node anyway: let route_rings say why
        return [(-math.inf, first)]
    spots: list[Candidate] = []
    spot_cells = {}
    for cand in hub_candidates(zone, plan, c):
        try:
            spot_cells[cand] = r.grid.snap(cand.xy, cand.room_id)[0]
        except ValueError:
            continue
        spots.append(cand)

    def slack(xy: Point2, cell: tuple[int, int]) -> float:
        cx, cy = r.grid.to_xy(cell)
        return w * (abs(xy[0] - cx) + abs(xy[1] - cy))

    cache = PathCache.cached(r.grid, node_cells, c, targets=spot_cells)
    node_slack = {nid: slack(n.xy, node_cells[nid]) for nid, n in nodes.items()}
    spot_slack = np.array([slack(s.xy, spot_cells[s]) for s in spots])
    hub_leg = {  # a hub leg's bound to node n, per spot (inf where unreachable)
        nid: np.array([cache.cost_m(nid, s) for s in spots]) - spot_slack - node_slack[nid]
        for nid in nodes
    }

    def leg(a: str, b: str) -> float:
        return cache.cost_m(a, b) - node_slack[a] - node_slack[b]

    def bounds(order: Sequence[str]) -> np.ndarray:
        inner = sum(leg(a, b) for a, b in itertools.pairwise(order))
        slop = w * LEG_SLOP_M * (len(order) + 1)
        return inner + hub_leg[order[0]] + hub_leg[order[-1]] - slop

    first_order = tuple(first.ring[1:-1])
    orders = _orders(list(nodes)) if len(nodes) <= MAX_ORDER_NODES else [first_order]
    per_order = {o: bounds(o) for o in orders} if spots else {}
    ranked = sorted(per_order, key=lambda o: (float(per_order[o].min()), o))
    rooms = sorted({s.room_id for s in spots})
    picked: dict[tuple[tuple[str, ...], Candidate], float] = {}
    at = [i for i, s in enumerate(spots) if (s.room_id, s.xy) == (first.room_id, first.location)]
    if at:
        picked[first_order, spots[at[0]]] = float(bounds(first_order)[at[0]])
    else:  # its spot can't snap: route it anyway, first, so route_rings reports why
        wc = "wall_clearance_only" in first.warnings
        picked[first_order, Candidate(first.room_id, first.location, wc)] = -math.inf
    queues = []  # per (order, room): its spots, lowest bound first
    for order in ranked[:SHORTLIST_ORDERS]:
        lb = per_order[order]
        for room in rooms:
            idx = [i for i, s in enumerate(spots) if s.room_id == room and math.isfinite(lb[i])]
            queues.append((order, lb, sorted(idx, key=lambda i: (lb[i], spots[i].xy))))
    budget = max(1, ROUTE_BUDGET_LEGS // (len(nodes) + 1))
    taken: dict[tuple[tuple[str, ...], str], list[Point2]] = {}
    while len(picked) < budget and any(q for _, _, q in queues):
        for order, lb, queue in queues:
            while queue and len(picked) < budget:
                spot = spots[queue.pop(0)]
                near = taken.setdefault((order, spot.room_id), [])
                if any(manhattan_inf(spot.xy, xy) < SPOT_SPACING_M - 1e-9 for xy in near):
                    continue
                near.append(spot.xy)
                picked.setdefault((order, spot), float(lb[spots.index(spot)]))
                break

    def hub(order: tuple[str, ...], spot: Candidate) -> Hub:
        warnings = ["wall_clearance_only"] if spot.wall_clearance_only else []
        assigned = [spot.room_id, *sorted(zone - {spot.room_id})]
        return Hub(hub_id, spot.room_id, spot.xy, [hub_id, *order, hub_id], assigned, warnings)

    keyed = sorted(picked.items(), key=lambda kv: (kv[1], kv[0][0], kv[0][1].xy))
    return [(lb, hub(order, spot)) for (order, spot), lb in keyed]


def best_ring(
    zone: Collection[str],
    plan: Plan,
    g: nx.Graph,
    grid: LabelGrid,
    c: Constraints,
    hub_id: str = "H1",
    *,
    prune: bool = True,
) -> Hub:
    """The zone's hub spot and ring order with the lowest routed cost (`RingCost`: rule-4-6 flags,
    then J of the ring `route.route_ring` writes, stubs and junction bends included) over
    `shortlist`. Pairs are routed in lower-bound order; with `prune`, routing stops once a
    flag-free ring's J is ≤ the next bound, which changes nothing (the bound never exceeds J).
    In practice the shortlist, not pruning, bounds the work: on the 7 fixtures J sits ≥ 2 m
    above every bound, and pruning skips 2 of 360 routings (#95 review).
    Never worse than first_ring, whose pair is on the shortlist; a pair that can't be routed is
    skipped, and first_ring's hub comes back if none can.
    """
    first = first_ring(zone, plan, g, c, hub_id=hub_id)
    r = _routing(zone, plan, grid, c)
    best: tuple[RingCost, Hub] | None = None
    for lb, hub in shortlist(zone, plan, g, grid, c, hub_id, first=first):
        # a later pair costs J ≥ its bound ≥ lb and ≥ 0 flags: it can't beat a flag-free best
        if prune and best is not None and best[0] <= RingCost(0, 0, lb):
            break
        try:
            cost = _cost(hub, plan, c, r)
        except ValueError:  # this pair can't be routed (a stub through a wall, a cut-off leg)
            continue
        if best is None or cost < best[0]:
            best = (cost, hub)
    return first if best is None else best[1]
