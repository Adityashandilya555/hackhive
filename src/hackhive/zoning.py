"""Zoning: every feasible way to group eligible rooms into hub zones (#13, ADR-001).

A zone is a set of eligible rooms that is connected in the eligible graph (doors and open
passages only, slide 3 rule 3), holds at most ``c.max_area_m2`` of room area and
``c.max_nodes`` nodes (rule 2), and contains a room that can host a hub (rule 6).
A zoning covers every eligible room exactly once. Enumeration is exact: per connected
component, recurse on the lowest uncovered room, then take the cross product.
"""

from __future__ import annotations

import itertools
from collections.abc import Collection, Iterable, Mapping

import networkx as nx

from hackhive.contracts import AREA_EPS_M2, Constraints

Zone = frozenset[str]
Zoning = tuple[Zone, ...]

MAX_COMPONENT_ROOMS = 16  # beyond this, solve falls back to greedy zoning (#49, #40)


class TooLarge(Exception):
    """A connected component has more rooms than exact enumeration handles."""


class Infeasible(Exception):
    """No zoning satisfies the hard rules."""


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

    Raises ``Infeasible`` if no zoning exists and ``TooLarge`` for a component with more
    than ``MAX_COMPONENT_ROOMS`` rooms.
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
        covers = _exact_covers(comp, zones)
        if not covers:
            raise Infeasible(f"no zoning covers {', '.join(comp)}")
        per_component.append(covers)

    zonings = [
        tuple(sorted((z for part in combo for z in part), key=lambda z: min(z)))
        for combo in itertools.product(*per_component)
    ]
    return sorted(zonings, key=lambda zg: (len(zg), [sorted(z) for z in zg]))


def min_hubs(zonings: Iterable[Zoning]) -> int:
    """Fewest zones over all zonings."""
    return min(len(z) for z in zonings)


def _fits(zone: Zone, area: Mapping[str, float], nodes: Mapping[str, int], c: Constraints) -> bool:
    return (
        sum(area[r] for r in zone) <= c.max_area_m2 + AREA_EPS_M2
        and sum(nodes[r] for r in zone) <= c.max_nodes
    )


def _exact_covers(rooms: list[str], zones: list[Zone]) -> list[Zoning]:
    """Every way to cover ``rooms`` exactly once, recursing on the lowest uncovered room."""
    order = {room: i for i, room in enumerate(rooms)}
    by_lowest: dict[str, list[Zone]] = {}
    for z in zones:
        by_lowest.setdefault(min(z, key=order.__getitem__), []).append(z)

    out: list[Zoning] = []
    chosen: list[Zone] = []

    def recurse(uncovered: frozenset[str]) -> None:
        if not uncovered:
            out.append(tuple(chosen))
            return
        lowest = min(uncovered, key=order.__getitem__)
        for z in by_lowest.get(lowest, []):
            if z <= uncovered:
                chosen.append(z)
                recurse(uncovered - z)
                chosen.pop()

    recurse(frozenset(rooms))
    return out
