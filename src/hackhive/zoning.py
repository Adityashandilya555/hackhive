"""Zoning: every feasible way to group eligible rooms into hub zones (#13, ADR-001).

A zone is a set of eligible rooms that is connected in the eligible graph (doors and open
passages only, slide 3 rule 3), holds at most ``c.max_area_m2`` of room area and
``c.max_nodes`` nodes (rule 2), and contains a room that can host a hub (rule 6).
A zoning covers every eligible room exactly once. Enumeration is exact: per connected
component, recurse on the lowest uncovered room, then take the cross product.
"""

from __future__ import annotations

import argparse
import itertools
import math
import sys
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path

import networkx as nx
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from hackhive.contracts import AREA_EPS_M2, Constraints, Plan, lower_bound

Zone = frozenset[str]
Zoning = tuple[Zone, ...]

MAX_COMPONENT_ROOMS = 16  # beyond this, solve falls back to greedy zoning (#49, #40)
# Rooms alone don't bound the work: a 16-room grid of doors has ~1M zonings, and #20/#22
# route and rank every one. ResPlan's maximum is 116, so this only stops pathological plans.
MAX_ZONINGS = 5_000
HUB_MARGIN_M = 0.01  # 1 cm above the rule-6 clearance, so float noise can't break it


class TooLarge(Exception):
    """A connected component has more rooms than exact enumeration handles."""


class Infeasible(Exception):
    """No zoning satisfies the hard rules."""


def eligible_area_and_nodes(plan: Plan) -> tuple[dict[str, float], dict[str, int]]:
    """Each eligible room's m² and how many nodes sit in it (0 for a folded sliver)."""
    area = {r.id: r.area_m2 for r in plan.rooms if r.eligible}
    nodes = dict.fromkeys(area, 0)
    for n in plan.nodes:
        nodes[n.room_id] += 1
    return area, nodes


def feasible_zones(
    g: nx.Graph,
    area: Mapping[str, float],
    c: Constraints,
    nodes: Mapping[str, int] | None = None,
    hub_capable: Collection[str] | None = None,
) -> list[Zone]:
    """Every connected room subset within the area and node caps that can host a hub."""
    order = {room: i for i, room in enumerate(sorted(g.nodes))}
    n_nodes = nodes if nodes is not None else dict.fromkeys(g.nodes, 1)
    missing = sorted(set(g.nodes) - set(n_nodes))
    if missing:
        raise ValueError(f"no node count for room(s) {', '.join(missing)}")
    zones: list[Zone] = []
    for root in sorted(g.nodes):
        # grow connected sets whose lowest room is ``root`` (each set found once)
        seen: set[Zone] = set()
        stack = [frozenset([root])]
        while stack:
            zone = stack.pop()
            if zone in seen:
                continue
            seen.add(zone)
            if _fits(zone, area, n_nodes, c):
                if hub_capable is None or not zone.isdisjoint(hub_capable):
                    zones.append(zone)
            else:
                continue  # caps only grow with the zone
            for room in zone:
                for nb in g.neighbors(room):
                    if nb not in zone and order[nb] > order[root]:
                        stack.append(zone | {nb})
    return sorted(zones, key=lambda z: (len(z), sorted(z)))


def enumerate_zonings(
    g: nx.Graph,
    area: Mapping[str, float],
    c: Constraints,
    nodes: Mapping[str, int] | None = None,
    hub_capable: Collection[str] | None = None,
) -> list[Zoning]:
    """All feasible zonings, fewest zones first, then in a fixed order.

    Raises ``Infeasible`` if no zoning exists, and ``TooLarge`` for a component with more
    than ``MAX_COMPONENT_ROOMS`` rooms or more than ``MAX_ZONINGS`` zonings in total.
    """
    for room in sorted(g.nodes):
        if area[room] > c.max_area_m2 + AREA_EPS_M2:
            raise Infeasible(f"room {room} is {area[room]:.1f} m² > {c.max_area_m2:g} m²")

    per_component: list[list[Zoning]] = []
    for comp in sorted((sorted(cc) for cc in nx.connected_components(g)), key=lambda cc: cc[0]):
        if len(comp) > MAX_COMPONENT_ROOMS:
            raise TooLarge(f"component of {len(comp)} rooms > {MAX_COMPONENT_ROOMS}")
        sub = g.subgraph(comp)
        zones = feasible_zones(sub, area, c, nodes, hub_capable)
        uncovered = set(comp) - {room for z in zones for room in z}
        if uncovered:
            raise Infeasible(f"no hub-capable room can serve {', '.join(sorted(uncovered))}")
        covers = _exact_covers(comp, zones, MAX_ZONINGS)
        if not covers:
            raise Infeasible(f"no zoning covers {', '.join(comp)}")
        per_component.append(covers)
    total = math.prod(len(covers) for covers in per_component)
    if total > MAX_ZONINGS:
        raise TooLarge(f"{total} zonings > {MAX_ZONINGS}")

    zonings = [
        tuple(sorted((z for part in combo for z in part), key=lambda z: min(z)))
        for combo in itertools.product(*per_component)
    ]
    return sorted(zonings, key=lambda zg: (len(zg), [sorted(z) for z in zg]))


def min_hubs(zonings: Iterable[Zoning]) -> int:
    """Fewest zones over all zonings."""
    return min(len(z) for z in zonings)


def wall_geometry(plan: Plan) -> BaseGeometry:
    """What rule 6 measures from: walls minus door gaps, windows, and everything outside ``inner``.

    A room's own outline is not part of it, so open passages and door gaps never count as walls.
    """
    doors = unary_union([d.polygon for d in plan.doors] + list(plan.front_doors))
    walls = unary_union(plan.walls).difference(doors)
    outside = box(*plan.inner.buffer(1.0).bounds).difference(plan.inner)
    return unary_union([walls, *plan.windows, outside])


def hub_capable_rooms(plan: Plan, c: Constraints) -> set[str]:
    """Eligible rooms with a point >= hub clearance + 1 cm from ``wall_geometry`` (rule 6, C18).

    A narrow room open to a passage can still host a hub.
    """
    keep_out = wall_geometry(plan).buffer(c.hub_wall_clearance_m + HUB_MARGIN_M)
    return {
        r.id for r in plan.rooms if r.eligible and r.polygon.difference(keep_out).area > AREA_EPS_M2
    }


def _fits(zone: Zone, area: Mapping[str, float], nodes: Mapping[str, int], c: Constraints) -> bool:
    return (
        sum(area[r] for r in zone) <= c.max_area_m2 + AREA_EPS_M2
        and sum(nodes[r] for r in zone) <= c.max_nodes
    )


def _exact_covers(rooms: list[str], zones: list[Zone], limit: int) -> list[Zoning]:
    """Every way to cover ``rooms`` exactly once, recursing on the lowest uncovered room.

    Raises ``TooLarge`` as soon as more than ``limit`` covers are found.
    """
    order = {room: i for i, room in enumerate(rooms)}
    by_lowest: dict[str, list[Zone]] = {}
    for z in zones:
        by_lowest.setdefault(min(z, key=order.__getitem__), []).append(z)

    out: list[Zoning] = []
    chosen: list[Zone] = []

    def recurse(uncovered: frozenset[str]) -> None:
        if not uncovered:
            out.append(tuple(chosen))
            if len(out) > limit:
                raise TooLarge(f"more than {limit} zonings")
            return
        lowest = min(uncovered, key=order.__getitem__)
        for z in by_lowest.get(lowest, []):
            if z <= uncovered:
                chosen.append(z)
                recurse(uncovered - z)
                chosen.pop()

    recurse(frozenset(rooms))
    return out


def main(argv: list[str] | None = None) -> int:
    """``python -m hackhive.zoning <plan.json>`` → ``min hubs k (LB m) · N zonings``."""
    from hackhive.graph import build_graph, eligible_graph
    from hackhive.io import load_plan

    for stream in (sys.stdout, sys.stderr):  # "·" and "²" would break cp1252 on Windows
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m hackhive.zoning")
    p.add_argument("plan", type=Path)
    args = p.parse_args(argv)
    c = Constraints()
    try:
        plan = load_plan(args.plan, c)
        g = eligible_graph(build_graph(plan, c))
    except (OSError, ValueError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    area, nodes = eligible_area_and_nodes(plan)
    lb = lower_bound(sum(area.values()), sum(nodes.values()), c)
    try:
        zonings = enumerate_zonings(g, area, c, nodes=nodes, hub_capable=hub_capable_rooms(plan, c))
    except (Infeasible, TooLarge) as e:
        print(f"{type(e).__name__}: {e} (LB {lb})", file=sys.stderr)
        return 1
    print(f"min hubs {min_hubs(zonings)} (LB {lb}) · {len(zonings)} zonings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
