"""The strict room graph (#9, ADR-007): rooms linked only by doors and open passages.

Slide 3 rule 3: windows, wall-only adjacency and the entrance never link rooms. Nodes are every
room id (eligible and excluded) with `type`, `label` and `eligible` attributes. Edges:
`kind="door"` (`door_ids`), `kind="opening"` (`region`, `opening_ids`; open passages have no
geometry of their own in ResPlan, so they are derived), only with
`Constraints.zone_links_via_excluded`, `kind="via_excluded"` (`via`), and, only with
`Constraints.interior_windows_as_doors` (#31, WHAT-IF), `kind="window_door"` (`door_ids` naming
the windows, `window_<i>` by their place in `plan.windows`).
`python -m hackhive.graph <plan.json>` prints the edges and the eligible-graph components.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Iterable
from itertools import combinations
from pathlib import Path
from typing import Any

import networkx as nx
from shapely.geometry import Polygon
from shapely.ops import unary_union

from hackhive.contracts import Constraints, Door, Opening, Plan, Room
from hackhive.io import get_geometries, load_plan

# Plot-level layers: `land` covers the whole plot and overlaps every room, so these would win door
# probes and fake openings. They stay graph nodes but never connect anything (ResPlan's own
# plan_to_graph leaves them out).
OUTDOOR = ("garden", "pool", "parking", "land")
VIA_TYPES = ("bathroom", "stair")  # what `zone_links_via_excluded` may pass through (#46 Q2)
# A door into one of these that leads nowhere else is no second way out of a room, for the
# single-door retrace rule (decided 2026-10-16, Rohit; bathrooms only, balconies still count)
DEAD_END_TYPES = ("bathroom",)
# A window with one of these on a side is on an outer wall, never an interior one (#31)
NOT_INTERIOR = ("balcony", *OUTDOOR)

DOOR_REACH = 0.6  # × wall depth: rooms this close to a door are its candidates (spec)
SIDE_SHARE = 0.10  # a side's room must fill ≥ 10% of its probe strip
NEAR_SQUARE = 1.5  # long/short below this: the door has no clear long side, so try both pairs
OPENING_GROW = 0.55  # × wall depth: each room is grown this much to meet its neighbour (spec)
OPENING_MIN_LONG = 2  # × wall depth: a kept piece's bbox long side (spec)
OPENING_MIN_AREA = 0.25  # × wall depth²: a kept piece's area (spec)

Record = tuple[str, tuple[str, str], Any]  # (kind, (room id, room id), payload)


def _strip(a: tuple[float, float], b: tuple[float, float], centre, depth: float) -> Polygon:
    """A probe strip of `depth` outward from edge a–b, away from `centre`."""
    ex, ey = b[0] - a[0], b[1] - a[1]
    n = math.hypot(ex, ey)
    nx_, ny_ = -ey / n, ex / n
    if ((a[0] + b[0]) / 2 - centre.x) * nx_ + ((a[1] + b[1]) / 2 - centre.y) * ny_ < 0:
        nx_, ny_ = -nx_, -ny_
    return Polygon(
        [a, b, (b[0] + nx_ * depth, b[1] + ny_ * depth), (a[0] + nx_ * depth, a[1] + ny_ * depth)]
    )


def _side_pairs(door: Polygon, depth: float) -> tuple[list[tuple[Polygon, Polygon]], bool]:
    """Probe strips outward from two opposite edges of the door's minimum rotated rectangle: the
    long pair (the wall faces), plus the short pair when the door is near-square."""
    rect = door.minimum_rotated_rectangle
    if not isinstance(rect, Polygon):
        return [], False
    p = list(rect.exterior.coords)[:4]
    c = rect.centroid
    pairs = [
        (_strip(p[0], p[1], c, depth), _strip(p[2], p[3], c, depth)),
        (_strip(p[1], p[2], c, depth), _strip(p[3], p[0], c, depth)),
    ]
    lengths = [math.dist(p[0], p[1]), math.dist(p[1], p[2])]
    if lengths[1] > lengths[0]:
        pairs.reverse()
        lengths.reverse()
    near_square = lengths[0] < NEAR_SQUARE * lengths[1]
    return (pairs if near_square else pairs[:1]), near_square


def _side_room(strip: Polygon, candidates: list[Room]) -> tuple[Room | None, float]:
    """The candidate filling most of the strip, with its share; None below SIDE_SHARE."""
    shares = [(r.polygon.intersection(strip).area / strip.area, r) for r in candidates]
    share, room = max(shares, key=lambda t: t[0], default=(0.0, None))
    return (room, share) if share >= SIDE_SHARE else (None, share)


def _door_pair(
    door: Door, candidates: list[Room], wd: float
) -> tuple[tuple[str, str] | None, list[str]]:
    """The two rooms a door links: one per side of the wall (the "top 2 by contact", measured
    per side, so a corner sliver of a third room can't steal the door, as on 1293 door_9)."""
    pairs, near_square = _side_pairs(door.polygon, wd)
    best: tuple[float, Room, Room] | None = None
    for s1, s2 in pairs:
        (r1, f1), (r2, f2) = _side_room(s1, candidates), _side_room(s2, candidates)
        if r1 and r2 and r1.id != r2.id and (best is None or min(f1, f2) > best[0]):
            best = (min(f1, f2), r1, r2)
    warnings = (
        [f"{door.id}: near-square door, its sides picked by room contact"] if near_square else []
    )
    if best is None:
        return None, [*warnings, f"{door.id}: no room found on both sides, so it links nothing"]
    return (best[1].id, best[2].id), warnings


def _long_side(p: Polygon) -> float:
    x0, y0, x1, y1 = p.bounds
    return max(x1 - x0, y1 - y0)


def _connectors(plan: Plan) -> tuple[list[Record], list[str]]:
    """Every room-to-room connector, doors first, then open passages between room pairs that no
    door links. The one place connectors are attributed (#31 adds windows here)."""
    wd = plan.wall_depth_m
    rooms = [r for r in plan.rooms if r.type not in OUTDOOR]
    order = {r.id: i for i, r in enumerate(plan.rooms)}

    def pair(u: str, v: str) -> tuple[str, str]:
        return (u, v) if order[u] < order[v] else (v, u)

    records: list[Record] = []
    warnings: list[str] = []
    reach = {r.id: r.polygon.buffer(DOOR_REACH * wd) for r in rooms}
    for d in plan.doors:
        candidates = [r for r in rooms if reach[r.id].intersects(d.polygon)]
        rooms_of_door, warn = _door_pair(d, candidates, wd)
        warnings += warn
        if rooms_of_door:
            records.append(("door", pair(*rooms_of_door), d.id))

    linked = {rec[1] for rec in records}
    # the entrance is subtracted too, or rooms meeting only at it would pass (slide 3 rule 3)
    blocked = unary_union(
        [*plan.walls, *plan.windows, *(d.polygon for d in plan.doors), *plan.front_doors]
    )
    grow = OPENING_GROW * wd
    # Keep only the band along the shared open edge: ResPlan rooms overlap and wrap around each
    # other (576's living_0 covers storage_0; 1293's living_0 rings the kitchen inside the wall
    # band), so drop the strips that hug walls, windows and doors, and the inside of any overlap.
    # Otherwise the raster (#10) paints the "opening" over a room or a door's side (#62 review).
    # Trimming also shortens the real band by up to `grow` at each end, so its ends are grown
    # back (inside the untrimmed gap); else a passage would need 3.1·wd of open edge, not 2·wd
    # (13344's living–kitchen band: 0.81 m, trimmed 0.58 m).
    near_blocked = blocked.buffer(grow)
    grown = {r.id: r.polygon.buffer(grow) for r in rooms}
    for a, b in combinations(rooms, 2):
        if pair(a.id, b.id) in linked or not grown[a.id].intersects(grown[b.id]):
            continue
        band = (
            grown[a.id]
            .intersection(grown[b.id])
            .difference(blocked)
            .difference(a.polygon.intersection(b.polygon).buffer(-grow))
        )
        gap = band.intersection(band.difference(near_blocked).buffer(grow))
        kept = [
            p
            for p in get_geometries(gap)
            if isinstance(p, Polygon)
            and _long_side(p) >= OPENING_MIN_LONG * wd
            and p.area >= OPENING_MIN_AREA * wd**2
        ]
        if kept:
            records.append(("opening", pair(a.id, b.id), kept))
    return records, warnings


def window_id(i: int) -> str:
    """The id a window gets as a door (#31): its place in `plan.windows`."""
    return f"window_{i}"


def interior_windows(plan: Plan) -> list[Door]:
    """The windows on interior walls, as doors (#31, ADR-007's ResPlan reading): a window whose
    `buffer(DOOR_REACH · wall depth)` touches exactly two rooms, both interior (no balcony or
    outdoor layer), picked by the same per-side top-2 as a door. Each comes back with `rooms`."""
    wd = plan.wall_depth_m
    rooms = [r for r in plan.rooms if r.type not in OUTDOOR]
    every = [r for r in plan.rooms if r.type != "land"]  # a garden on the far side: exterior
    out = []
    for i, w in enumerate(plan.windows):
        reach = w.buffer(DOOR_REACH * wd)
        touching = [r for r in every if r.polygon.intersects(reach)]
        if len(touching) != 2 or any(r.type in NOT_INTERIOR for r in touching):
            continue
        door = Door(id=window_id(i), polygon=w)
        ids = {r.id for r in touching}
        pair, _ = _door_pair(door, [r for r in rooms if r.id in ids], wd)
        if pair is not None:
            door.rooms = pair
            out.append(door)
    return out


def passage_doors(plan: Plan) -> list[Door]:
    """Every door cable may cross: the plan's doors, then any interior windows read as doors
    (#31; `build_graph` fills them only with the setting on). The grid and rule 5 share it."""
    return [*plan.doors, *plan.window_doors]


def _link_via_excluded(G: nx.Graph) -> None:
    """Link eligible rooms that both connect to the same bathroom or stair (never replacing a
    door or opening edge). Only with `Constraints.zone_links_via_excluded` (#46 Q2)."""
    for x, data in list(G.nodes(data=True)):
        if data["type"] not in VIA_TYPES:
            continue
        rooms = sorted(n for n in G.neighbors(x) if G.nodes[n]["eligible"])
        for a, b in combinations(rooms, 2):
            if not G.has_edge(a, b):
                G.add_edge(a, b, kind="via_excluded", via=[x])
            elif G.edges[a, b]["kind"] == "via_excluded":
                G.edges[a, b]["via"].append(x)


def build_graph(plan: Plan, c: Constraints | None = None) -> nx.Graph:
    """The room graph over every room (C8, C13, C14, C34). Also fills `plan.openings` (one
    `Opening` per kept piece), `Door.rooms` for every door that links two rooms,
    `plan.window_doors` (#31: empty unless `c.interior_windows_as_doors`) and `plan.warnings`;
    safe to call again on the same plan, with any settings."""
    c = c or Constraints()
    G = nx.Graph()
    G.add_nodes_from(
        (r.id, {"type": r.type, "label": r.label, "eligible": r.eligible}) for r in plan.rooms
    )
    records, warnings = _connectors(plan)
    doors = {d.id: d for d in plan.doors}
    for d in plan.doors:
        d.rooms = None
    openings: list[Opening] = []
    for kind, (u, v), payload in records:
        if kind == "door":
            doors[payload].rooms = (u, v)
            if G.has_edge(u, v):
                G.edges[u, v]["door_ids"].append(payload)
            else:
                G.add_edge(u, v, kind="door", door_ids=[payload])
        else:
            ids = [f"opening_{len(openings) + k}" for k in range(len(payload))]
            openings += [
                Opening(id=i, polygon=p, rooms=(u, v)) for i, p in zip(ids, payload, strict=True)
            ]
            G.add_edge(u, v, kind="opening", region=unary_union(payload), opening_ids=ids)
    plan.window_doors = interior_windows(plan) if c.interior_windows_as_doors else []
    for w in plan.window_doors:  # a pair a door or opening links already keeps that edge
        u, v = w.rooms
        if not G.has_edge(u, v):
            G.add_edge(u, v, kind="window_door", door_ids=[w.id])
        elif G.edges[u, v]["kind"] == "window_door":
            G.edges[u, v]["door_ids"].append(w.id)
    if c.zone_links_via_excluded:
        _link_via_excluded(G)
    plan.openings = openings
    plan.warnings += [w for w in warnings if w not in plan.warnings]
    return G


def eligible_graph(G: nx.Graph) -> nx.Graph:
    """The eligible rooms only, as a read-only subgraph view (C34)."""
    return G.subgraph(n for n, eligible in G.nodes(data="eligible") if eligible)


def _dead_end(G: nx.Graph, room: str, nb: str) -> bool:
    """Whether `nb` is a dead end off `room`: a DEAD_END_TYPES room whose only link is `room`."""
    return G.nodes[nb].get("type") in DEAD_END_TYPES and set(G.neighbors(nb)) == {room}


def retrace_links(G: nx.Graph, room: str) -> list[str]:
    """The rooms `room` links to that count as ways out for the single-door retrace rule
    (slide 3 rule 4): every neighbour except a dead-end bathroom (2026-10-16). If every link is
    such a dead end, they all count, so a room behind one bathroom keeps its one door."""
    links = list(G.neighbors(room))
    real = [nb for nb in links if not _dead_end(G, room, nb)]
    return real or links


def single_door_rooms(G: nx.Graph) -> frozenset[str]:
    """Rooms with exactly one way out (`retrace_links`): cable may go in and back out through
    that door on one line, which is legal (slide 3 rule 4) and scored as overlap. The one
    definition the checker (rule 4) and the router share."""
    return frozenset(r for r in G.nodes if len(retrace_links(G, r)) == 1)


def _edge_lines(plan: Plan, G: nx.Graph) -> Iterable[str]:
    order = {r.id: i for i, r in enumerate(plan.rooms)}
    lines = []
    for u, v, d in G.edges(data=True):
        u, v = sorted((u, v), key=order.__getitem__)
        extra = {
            "door": d.get("door_ids"),
            "window_door": d.get("door_ids"),
            "via_excluded": d.get("via"),
        }.get(d["kind"])
        text = f"{u} {G.nodes[u]['label']} — {v} {G.nodes[v]['label']} · {d['kind']}"
        if extra:
            text += (" via " if d["kind"] == "via_excluded" else " ") + ", ".join(extra)
        lines.append((order[u], order[v], text))
    return (text for *_, text in sorted(lines))


def main(argv: list[str] | None = None) -> int:
    """`python -m hackhive.graph <plan.json>`: one line per edge, then the eligible components."""
    for stream in (sys.stdout, sys.stderr):  # "—" and "·" would break cp1252 on Windows
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m hackhive.graph", description="Print a room graph.")
    p.add_argument("plan", type=Path, help="one plan JSON file")
    args = p.parse_args(argv)
    try:
        plan = load_plan(args.plan)
        G = build_graph(plan)
    except (OSError, ValueError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    for line in _edge_lines(plan, G):
        print(line)
    n = nx.number_connected_components(eligible_graph(G))
    print(f"{n} component{'' if n == 1 else 's'}")
    for w in plan.warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
