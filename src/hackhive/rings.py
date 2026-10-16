"""Hub placement and ring order, first cut (#14; #20 makes it exact, #28 adds Held-Karp).

Grid convention (shared with #10's raster): cell k spans ``[k, k + 1) · grid_m`` on each axis,
anchored at the plan origin, and its centre sits half a cell in, so a hub placed on a centre
needs no routing stub.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from hackhive.contracts import Constraints, Plan
from hackhive.zoning import HUB_MARGIN_M, Infeasible, wall_geometry

Point2 = tuple[float, float]

CELL_CENTRE_OFFSET = 0.5  # not-a-weight: a cell centre is half a cell in from its edge
NODE_KEEP_OFF_M = 0.10  # a hub may share a node's room but never its point (conventions §4)
MAX_BRUTE_FORCE_NODES = 8  # 8! = 40,320 orders; beyond that is #28's Held-Karp


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
    best = min(
        (cand for cand in cands if cand.room_id == room),
        key=lambda cand: (sum(manhattan(cand.xy, n.xy) for n in nodes), cand.xy),
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
