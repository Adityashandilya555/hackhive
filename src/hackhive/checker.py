"""The organisers' checker, mirrored (slides 3, 6, 7).

Metrics half (#43): H, L, X, O, B, J and the assigned-area gap, recomputed only from the submitted
polylines at the 1 mm tolerance; reported lengths are never read (C42). Where the slides are
ambiguous we count strictly (docs/decision_conventions.md §6). Rules half (#11): slide 3's six
hard rules, slide 5's reviewer checklist, and `python -m hackhive.checker <plan> <submission>`.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import networkx as nx
import numpy as np
import shapely
from pydantic import ValidationError
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from hackhive.contracts import (
    AREA_EPS_M2,
    Constraints,
    HubOut,
    Node,
    Plan,
    Room,
    Submission,
    Violation,
    lower_bound,
)
from hackhive.graph import (
    OUTDOOR,
    build_graph,
    eligible_graph,
    passage_doors,
    retrace_links,
    single_door_rooms,
    window_id,
)
from hackhive.grid import door_gap
from hackhive.io import get_geometries, load_plan
from hackhive.zoning import wall_geometry

Point = tuple[float, float]
_DEFAULT = Constraints()  # frozen, so safe as a shared default
TOL = _DEFAULT.tolerance_m

# --------------------------------------------------------------------------- geometry helpers


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _cross(u: Point, v: Point) -> float:
    return u[0] * v[1] - u[1] * v[0]


def _dot(u: Point, v: Point) -> float:
    return u[0] * v[0] + u[1] * v[1]


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _is_straight(a: Point, b: Point, c: Point, tol: float) -> bool:
    """True if a → b → c keeps its direction: b lies within tol of line ac and is not a reversal."""
    ac = _sub(c, a)
    n = math.hypot(*ac)
    if n <= tol:
        return False  # c is back at a: a reversal
    return abs(_cross(ac, _sub(b, a))) / n <= tol and _dot(_sub(b, a), _sub(c, b)) > 0


def _same_dir(u: Point, v: Point, tol: float) -> bool:
    return _dot(u, v) > 0 and abs(_cross(u, v)) <= tol * max(math.hypot(*u), math.hypot(*v))


def _dist_to_segment(p: Point, a: Point, b: Point) -> float:
    ab = _sub(b, a)
    t = max(0.0, min(1.0, _dot(_sub(p, a), ab) / _dot(ab, ab)))
    return _dist(p, (a[0] + t * ab[0], a[1] + t * ab[1]))


def _check_finite(polylines: Iterable[Sequence[Point]]) -> None:
    for line in polylines:
        for x, y in line:
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError(f"non-finite coordinate ({x}, {y})")


# --------------------------------------------------------------------------- L


def segment_length(polyline: Sequence[Point]) -> float:
    """Polyline length in metres (fills `length_m`)."""
    return sum(_dist(a, b) for a, b in pairwise(polyline))


def ring_length(hub: HubOut) -> float:
    """One ring's length, return leg included (fills `ring_length_m`)."""
    return sum(segment_length(s.polyline) for s in hub.route_segments)


# --------------------------------------------------------------------------- cleanup and B


def clean_polyline(polyline: Sequence[Point], tol: float = TOL) -> list[Point]:
    """Merge points closer than tol and drop vertices that keep direction; reversals stay.

    The first and last points are kept exactly (they are the leg's node or hub points).
    """
    out: list[Point] = []
    last = len(polyline) - 1
    for i, p in enumerate(polyline):
        p = (float(p[0]), float(p[1]))
        if out and _dist(out[-1], p) < tol:
            if i == last and len(out) > 1:
                out[-1] = p
            continue
        while len(out) >= 2 and _is_straight(out[-2], out[-1], p, tol):
            out.pop()
        out.append(p)
    return out


def count_bends(
    polylines: Sequence[Sequence[Point]], closed: bool, tol: float = TOL, *, at_joins: bool = True
) -> int:
    """B for one cable made of consecutive legs (slide 7; decision_conventions §6 B row).

    +1 at every direction change inside a leg, at every node (last direction in vs first out) and,
    if closed, at the hub. A reversal counts once; straight through counts 0. Zero-length legs are
    skipped. Without ``at_joins`` (the lenient reading, #30 D3), turns at nodes and the hub don't
    count, only those inside a leg.
    """
    legs = [p for p in (clean_polyline(line, tol) for line in polylines) if len(p) >= 2]
    bends = sum(len(p) - 2 for p in legs)
    if not at_joins:
        return bends
    pairs = list(pairwise(legs))
    if closed and legs:
        pairs.append((legs[-1], legs[0]))
    for prev, nxt in pairs:
        b = prev[-1]
        a = _sub(b, _sub(prev[-1], prev[-2]))  # virtual points keep a broken chain comparable
        c = (b[0] + nxt[1][0] - nxt[0][0], b[1] + nxt[1][1] - nxt[0][1])
        bends += not _is_straight(a, b, c, tol)
    return bends


# --------------------------------------------------------------------------- O


def _cluster(values: Sequence[float], tol: float) -> dict[float, float]:
    """Map each value to its cluster's first value; a new cluster starts more than tol away."""
    rep: dict[float, float] = {}
    first: float | None = None
    for v in sorted(values):
        if first is None or v - first > tol:
            first = v
        rep[v] = first
    return rep


@dataclass(frozen=True)
class Stretch:
    """A same-line stretch that k ≥ 2 leg pieces cover (O counts (k - 1) · length of it)."""

    axis: str  # "h" (y fixed) or "v" (x fixed)
    at: float  # the fixed coordinate
    lo: float
    hi: float
    k: int
    owners: tuple[str, ...]  # the rings involved, sorted

    @property
    def length(self) -> float:
        return self.hi - self.lo

    @property
    def ends(self) -> tuple[Point, Point]:
        if self.axis == "h":
            return (self.lo, self.at), (self.hi, self.at)
        return (self.at, self.lo), (self.at, self.hi)


def overlap_stretches(
    legs: Sequence[Sequence[Point]], owners: Sequence[str], tol: float = TOL
) -> list[Stretch]:
    """Every same-line stretch covered by k ≥ 2 pieces: the one computation behind O
    (`_overlaps` sums (k - 1) · length over these) and the overlay's ×k stretches (#17).
    Adjacent runs with the same k and rings are merged."""
    lines: dict[tuple[str, float], list[tuple[float, float, str]]] = {}
    raw: list[tuple[str, float, float, float, str]] = []
    for leg, owner in zip(legs, owners, strict=True):
        for a, b in pairwise(leg):
            if abs(a[1] - b[1]) <= tol:
                raw.append(("h", (a[1] + b[1]) / 2, a[0], b[0], owner))
            elif abs(a[0] - b[0]) <= tol:
                raw.append(("v", (a[0] + b[0]) / 2, a[1], b[1], owner))
    for axis in ("h", "v"):
        pieces = [r for r in raw if r[0] == axis]
        rep = _cluster([r[1] for r in pieces], tol)
        for _, c, s, e, owner in pieces:
            lines.setdefault((axis, rep[c]), []).append((min(s, e), max(s, e), owner))

    out: list[Stretch] = []
    for (axis, at), intervals in sorted(lines.items()):
        rep = _cluster([v for lo, hi, _ in intervals for v in (lo, hi)], tol)
        events = sorted(
            (rep[v], d, owner)
            for lo, hi, owner in intervals
            if rep[lo] != rep[hi]
            for v, d in ((lo, 1), (hi, -1))
        )
        active: Counter[str] = Counter()
        prev = None
        for pos, d, owner in events:
            k = sum(active.values())
            if prev is not None and k >= 2 and pos > prev:
                who = tuple(sorted(active))
                last = out[-1] if out else None
                if (
                    last is not None
                    and (last.axis, last.at, last.k, last.owners) == (axis, at, k, who)
                    and last.hi == prev
                ):
                    out[-1] = Stretch(axis, at, last.lo, pos, k, who)
                else:
                    out.append(Stretch(axis, at, prev, pos, k, who))
            active[owner] += d
            if not active[owner]:
                del active[owner]
            prev = pos
    return out


def _overlaps(
    legs: Sequence[Sequence[Point]], owners: Sequence[str], tol: float
) -> tuple[float, dict[str, float]]:
    """Total O and each owner's credit (every owner on a shared stretch gets its full (k-1)·len).
    Derived from `overlap_stretches`, so O and the overlay's ×k stretches are one computation."""
    total = 0.0
    credit: dict[str, float] = {o: 0.0 for o in owners}
    for st in overlap_stretches(legs, owners, tol):
        total += (st.k - 1) * st.length
        for o in st.owners:
            credit[o] += (st.k - 1) * st.length
    return total, credit


def _own_overlap(
    legs: Sequence[Sequence[Point]], owners: Sequence[str], owner: str, tol: float
) -> float:
    """O of one ring's legs on their own: its overlap with itself (the lenient reading, D3)."""
    mine = [leg for leg, o in zip(legs, owners, strict=True) if o == owner]
    return _overlaps(mine, [owner] * len(mine), tol)[0]


def overlap_length(polylines: Sequence[Sequence[Point]], tol: float = TOL) -> float:
    """O (slide 7): on each line, a stretch covered by k pieces adds (k − 1) × length.

    Lines within tol are the same line; parallel lines further apart and collinear pieces that
    only touch at an endpoint add 0. k counts pieces, so a leg retracing itself counts too.
    """
    legs = [clean_polyline(p, tol) for p in polylines]
    return _overlaps(legs, [str(i) for i in range(len(legs))], tol)[0]


# --------------------------------------------------------------------------- X


def _meet(p1: Point, q1: Point, p2: Point, q2: Point, tol: float) -> list[Point]:
    """Where two straight pieces meet: a crossing, a T or corner touch, or both ends of a shared
    collinear stretch (the divergence points)."""
    d1 = _sub(q1, p1)
    n1 = math.hypot(*d1)
    u = (d1[0] / n1, d1[1] / n1)
    if abs(_cross(u, _sub(p2, p1))) <= tol and abs(_cross(u, _sub(q2, p1))) <= tol:
        t2, s2 = sorted((_dot(_sub(p2, p1), u), _dot(_sub(q2, p1), u)))
        lo, hi = max(0.0, t2), min(n1, s2)
        if hi < lo - tol:
            return []
        at = [lo, hi] if hi - lo > tol else [(lo + hi) / 2]
        return [(p1[0] + u[0] * t, p1[1] + u[1] * t) for t in at]
    pts = [e for e in (p1, q1) if _dist_to_segment(e, p2, q2) <= tol]
    pts += [e for e in (p2, q2) if _dist_to_segment(e, p1, q1) <= tol]
    d2 = _sub(q2, p2)
    den = _cross(d1, d2)
    if den:
        t = _cross(_sub(p2, p1), d2) / den
        s = _cross(_sub(p2, p1), d1) / den
        if 0 < t < 1 and 0 < s < 1:
            pts.append((p1[0] + t * d1[0], p1[1] + t * d1[1]))
    return pts


def _leaving(leg: Sequence[Point], i: int, p: Point, tol: float) -> list[Point]:
    """Directions in which the cable of piece i leaves point p (p lies on that piece)."""
    a, b = leg[i], leg[i + 1]
    if _dist(p, a) <= tol:
        out = [_sub(b, a)]
        if i > 0:
            out.append(_sub(leg[i - 1], a))
        return out
    if _dist(p, b) <= tol:
        out = [_sub(a, b)]
        if i + 2 < len(leg):
            out.append(_sub(leg[i + 2], b))
        return out
    return [_sub(b, a), _sub(a, b)]


def _coincide(da: list[Point], db: list[Point], tol: float) -> bool:
    return all(any(_same_dir(x, y, tol) for y in db) for x in da) and all(
        any(_same_dir(x, y, tol) for y in da) for x in db
    )


def _pass_through(da: list[Point], db: list[Point], tol: float) -> bool:
    """Both cables run through the point and cross there: two ways out each, none shared, and
    alternating around it (A, B, A, B). Not a T-touch, an end, or a shared stretch's end."""
    if len(da) != 2 or len(db) != 2 or any(_same_dir(x, y, tol) for x in da for y in db):
        return False
    around = sorted(
        [(math.atan2(d[1], d[0]), "a") for d in da] + [(math.atan2(d[1], d[0]), "b") for d in db]
    )
    return all(around[i][1] != around[i + 1][1] for i in range(3))


def _crossings(
    legs: Sequence[Sequence[Point]],
    owners: Sequence[str],
    terminals: Sequence[Point],
    tol: float,
    *,
    lenient: bool = False,
) -> list[tuple[Point, set[str]]]:
    """X's points, each with the rings that meet there. Where two cables that share a line part
    is no crossing under either reading (slide 7's overlap example costs O alone). ``lenient``
    (#30 D3): only where two cables pass through each other, so a T-touch is none either."""
    pieces = [(li, i) for li, leg in enumerate(legs) for i in range(len(leg) - 1)]
    # every point _meet returns lies within 2·tol of both pieces, so a pair whose boxes, grown by
    # 3·tol, don't touch meets nowhere: skipping it changes nothing, in the same order (#20)
    ends = np.array([(*legs[li][i], *legs[li][i + 1]) for li, i in pieces]).reshape(-1, 4)
    lo = np.minimum(ends[:, :2], ends[:, 2:]) - 3 * tol
    hi = np.maximum(ends[:, :2], ends[:, 2:]) + 3 * tol
    found: list[tuple[Point, set[str]]] = []
    for n, (la, ia) in enumerate(pieces):
        near = np.all((lo[n + 1 :] <= hi[n]) & (hi[n + 1 :] >= lo[n]), axis=1)
        for m in np.flatnonzero(near).tolist():
            lb, ib = pieces[n + 1 + m]
            A, B = legs[la], legs[lb]
            for p in _meet(A[ia], A[ia + 1], B[ib], B[ib + 1], tol):
                # free only where both legs end (they meet at their shared node or hub); a foreign
                # cable through a node or hub counts (strict, decision_conventions principle 3)
                if (
                    any(_dist(p, t) <= tol for t in terminals)
                    and any(_dist(p, e) <= tol for e in (A[0], A[-1]))
                    and any(_dist(p, e) <= tol for e in (B[0], B[-1]))
                ):
                    continue
                # cables that arrive and leave p together don't meet there: the shared vertex of
                # consecutive pieces, or a co-routed corner of a retrace (#43 decided ambiguity)
                da, db = _leaving(A, ia, p, tol), _leaving(B, ib, p, tol)
                if _coincide(da, db, tol):
                    continue
                # a shared way out = the end of a shared stretch: the cables only part, and
                # sharing one side of p they can't also cross there (slide 7: overlap is O)
                if any(_same_dir(x, y, tol) for x in da for y in db):
                    continue
                if lenient and not _pass_through(da, db, tol):
                    continue
                for q, who in found:
                    if _dist(p, q) <= tol:
                        who |= {owners[la], owners[lb]}
                        break
                else:
                    found.append((p, {owners[la], owners[lb]}))
    return found


def crossing_points(
    polylines: Sequence[Sequence[Point]], terminals: Sequence[Point], tol: float = TOL
) -> list[Point]:
    """X's points (slides 6-7): where cables cross or meet, once per point.

    A meeting at a terminal (node or hub point) is free only when both legs end there; a foreign
    cable through a node or hub counts. Strict (decision_conventions §6 ⚠ #46 Q4): a T-touch
    counts; where cables that share a line part (a divergence end) or turn a corner together is
    no crossing, as slide 7's overlap example counts that stretch as O alone. Each polyline is
    one leg.
    """
    legs = [clean_polyline(p, tol) for p in polylines]
    return [p for p, _ in _crossings(legs, [str(i) for i in range(len(legs))], terminals, tol)]


# --------------------------------------------------------------------------- J, area gap, metrics


def area_gap(areas: Iterable[float]) -> float:
    """Tie-break: max − min of the hubs' assigned eligible areas; 0 for one hub (C47, #46 Q6)."""
    a = list(areas)
    return max(a) - min(a) if len(a) > 1 else 0.0


@dataclass(frozen=True)
class HubTotals:
    L: float
    X: int
    O: float  # noqa: E741
    B: int


@dataclass(frozen=True)
class Metrics:
    """The organisers' metrics for one submission.

    `per_hub`: L and B are each ring's own and sum to the totals. X and O credit every point or
    stretch to every ring involved, so per-hub X and O can sum to more than the totals.
    """

    H: int
    L: float
    X: int
    O: float  # noqa: E741
    B: int
    J: float
    area_gap: float | None
    per_hub: dict[str, HubTotals]


READINGS = ("strict", "lenient")  # D3: how the unclear counting rules are read (#30)


def metrics(
    hubs: Sequence[HubOut],
    c: Constraints = _DEFAULT,
    *,
    hub_areas: Mapping[str, float] | None = None,
    reading: str = "strict",
) -> Metrics:
    """H, L, X, O, B, J (and the area gap if `hub_areas` is given) from the polylines only.

    `reading` (D3, #30's counting-rules toggle): "strict" is our mirror (decision_conventions
    §6). "lenient" reads the three unclear rules the other way: X counts only cables passing
    through each other (no T-touch, no shared stretch's ends), O only a ring's overlap with
    itself, and B only turns inside a leg (none at a node or the hub). L is the same.
    """
    if reading not in READINGS:
        raise ValueError(f"reading must be one of {READINGS}, got {reading!r}")
    lenient = reading == "lenient"
    tol, w = c.tolerance_m, c.cost_weights
    raw = [s.polyline for h in hubs for s in h.route_segments]
    _check_finite(raw)
    _check_finite([[(h.location.x, h.location.y)] for h in hubs])

    legs: list[list[Point]] = []
    owners: list[str] = []
    per_L: dict[str, float] = {}
    per_B: dict[str, int] = {}
    for h in hubs:
        ring = [clean_polyline(s.polyline, tol) for s in h.route_segments]
        legs += ring
        owners += [h.hub_id] * len(ring)
        per_L[h.hub_id] = sum(segment_length(p) for p in ring)
        per_B[h.hub_id] = count_bends(ring, closed=True, tol=tol, at_joins=not lenient)

    terminals = [p for leg in legs if leg for p in (leg[0], leg[-1])]
    terminals += [(h.location.x, h.location.y) for h in hubs]
    if lenient:  # each ring's overlap with itself only
        per_O = {o: _own_overlap(legs, owners, o, tol) for o in dict.fromkeys(owners)}
        O = sum(per_O.values())  # noqa: E741
    else:
        O, per_O = _overlaps(legs, owners, tol)  # noqa: E741
    points = _crossings(legs, owners, terminals, tol, lenient=lenient)
    L, X, B = sum(per_L.values()), len(points), sum(per_B.values())
    per_hub = {
        h.hub_id: HubTotals(
            L=per_L[h.hub_id],
            X=sum(h.hub_id in who for _, who in points),
            O=per_O.get(h.hub_id, 0.0),  # a hub with no legs owns no pieces
            B=per_B[h.hub_id],
        )
        for h in hubs
    }
    return Metrics(
        H=len(hubs),
        L=L,
        X=X,
        O=O,
        B=B,
        J=w.length * L + w.cross * X + w.overlap * O + w.bend * B,
        area_gap=None if hub_areas is None else area_gap(hub_areas.values()),
        per_hub=per_hub,
    )


# --------------------------------------------------------------------------- rules (#11)

COVERAGE, CAPACITY, ZONE, RING, FREE_SPACE, HUB_POINT = range(1, 7)  # slide 3's rule numbers


@dataclass(frozen=True)
class _Context:
    """What the rules read from the plan, built once per `check_rules` call."""

    plan: Plan
    c: Constraints
    rooms: dict[str, Room]
    nodes: dict[str, Node]
    graph: nx.Graph  # the full room graph (#9), for zones
    eligible: nx.Graph  # its eligible view
    single_door: frozenset[str]  # graph.single_door_rooms: where a same-line retrace is legal
    hub_walls: BaseGeometry  # zoning.wall_geometry: what rule 6 measures from


@dataclass(frozen=True)
class _FreeSpace:
    """Rule 5's geometry: where cable may run, and where it may change rooms."""

    free: BaseGeometry  # inner minus walls, windows and the entrance, plus passages, grown by tol
    passages: BaseGeometry  # door gaps (as the router's raster has them) and open passages
    walls: BaseGeometry
    windows: BaseGeometry
    entrance: BaseGeometry
    inner: BaseGeometry
    room_zones: list[BaseGeometry]  # every room but the outdoor layers, grown by tol


def _v(
    rule: int,
    message: str,
    hub_id: str | None = None,
    where: Iterable[Sequence[float]] = (),
    ids: Iterable[str] = (),
    severity: str = "error",
) -> Violation:
    return Violation(
        rule=rule,
        severity=severity,
        message=message,
        ids=list(dict.fromkeys(ids)),
        hub_id=hub_id,
        where=[(float(p[0]), float(p[1])) for p in where],
    )


def _rep(room: Room) -> Point:
    p = room.polygon.representative_point()
    return (p.x, p.y)


def _hub_where(hub: HubOut, ctx: _Context) -> list[Point]:
    """The hub's location, or its room's representative point if the location isn't finite."""
    x, y = hub.location.x, hub.location.y
    if math.isfinite(x) and math.isfinite(y):
        return [(x, y)]
    room = ctx.rooms.get(hub.room_id)
    return [_rep(room)] if room else []


def _free_space(plan: Plan, c: Constraints) -> _FreeSpace:
    """Rule 5's geometry; `build_graph` must have filled `plan.openings` (and, for #31's
    WHAT-IF, `plan.window_doors`) first: a window read as a door is a passage, never a crossing.

    Cable crosses a wall only inside `grid.door_gap` (the door stretched across the wall band),
    exactly where the router's raster lets it, so the two agree (#60 review).
    """
    gaps = [door_gap(d, plan, c.grid_m) for d in passage_doors(plan)]
    # #31: with `interior_windows_as_doors` (WHAT-IF), build_graph read some windows as doors
    as_doors = {w.id for w in plan.window_doors}
    shut = [w for i, w in enumerate(plan.windows) if window_id(i) not in as_doors]
    never = unary_union([*shut, *plan.front_doors])
    walls = unary_union(plan.walls)
    passages = (
        unary_union([*gaps, *(o.polygon for o in plan.openings)])
        .difference(never)
        .intersection(plan.inner)
    )
    free = plan.inner.difference(walls).difference(never).union(passages).buffer(c.tolerance_m)
    return _FreeSpace(
        free=free,
        passages=passages,
        walls=walls,
        windows=unary_union(shut),
        entrance=unary_union(plan.front_doors),
        inner=plan.inner,
        room_zones=[r.polygon.buffer(c.tolerance_m) for r in plan.rooms if r.type not in OUTDOOR],
    )


def _rule1_coverage(sub: Submission, ctx: _Context) -> list[Violation]:
    """Every eligible room and every node in exactly one hub; a node's room in its hub (C1, C9).
    Hub ids are unique and never a node's id, or `closed_sequence` is ambiguous (#68 review)."""
    out: list[Violation] = []
    for hid, k in Counter(h.hub_id for h in sub.hubs).items():
        where = [p for h in sub.hubs if h.hub_id == hid for p in _hub_where(h, ctx)]
        if k > 1:
            out.append(_v(COVERAGE, f"hub id {hid} is used by {k} hubs", hid, where, [hid]))
        if hid in ctx.nodes:
            out.append(_v(COVERAGE, f"hub id {hid} is also a node id", hid, where, [hid]))
    room_hubs: dict[str, list[str]] = {}
    node_hubs: dict[str, list[str]] = {}
    for hub in sub.hubs:
        for rid in hub.assigned_rooms:
            room_hubs.setdefault(rid, []).append(hub.hub_id)
        for nid in hub.assigned_nodes:
            node_hubs.setdefault(nid, []).append(hub.hub_id)
        for rid in dict.fromkeys(hub.assigned_rooms):
            room = ctx.rooms.get(rid)
            if room is None:
                out.append(
                    _v(
                        COVERAGE,
                        f"{rid} isn't a room of the plan",
                        hub.hub_id,
                        _hub_where(hub, ctx),
                        [rid],
                    )
                )
            elif not room.eligible:
                msg = f"{rid} ({room.label}) is an excluded {room.type}: it can't be assigned"
                out.append(_v(COVERAGE, msg, hub.hub_id, [_rep(room)], [rid]))
        for nid in dict.fromkeys(hub.assigned_nodes):
            node = ctx.nodes.get(nid)
            if node is None:
                out.append(
                    _v(
                        COVERAGE,
                        f"{nid} isn't a node of the plan",
                        hub.hub_id,
                        _hub_where(hub, ctx),
                        [nid],
                    )
                )
            elif node.room_id not in hub.assigned_rooms:
                msg = f"{nid}'s room {node.room_id} isn't one of {hub.hub_id}'s rooms"
                out.append(_v(COVERAGE, msg, hub.hub_id, [node.xy], [nid, node.room_id]))
    for room in (r for r in ctx.plan.rooms if r.eligible):
        hubs = room_hubs.get(room.id, [])
        if len(hubs) != 1:
            what = (
                "is in no hub" if not hubs else f"is assigned {len(hubs)} times ({', '.join(hubs)})"
            )
            out.append(
                _v(
                    COVERAGE,
                    f"{room.id} ({room.label}) {what}",
                    None,
                    [_rep(room)],
                    [room.id, *hubs],
                )
            )
    for node in ctx.plan.nodes:
        hubs = node_hubs.get(node.id, [])
        if len(hubs) != 1:
            what = (
                "is in no hub" if not hubs else f"is assigned {len(hubs)} times ({', '.join(hubs)})"
            )
            out.append(_v(COVERAGE, f"{node.id} {what}", None, [node.xy], [node.id, *hubs]))
    return out


def assigned_area(hub: HubOut, rooms: Mapping[str, Room]) -> float:
    """A hub's assigned eligible area in m²: each listed room once, unknown and excluded rooms
    left out (rule 1 reports those). The one definition behind rule 2, the area gap and the
    app's per-hub summary."""
    return sum(
        rooms[r].area_m2
        for r in dict.fromkeys(hub.assigned_rooms)
        if r in rooms and rooms[r].eligible
    )


def _rule2_capacity(hub: HubOut, ctx: _Context) -> list[Violation]:
    """At most `max_area_m2` of assigned eligible area and `max_nodes` nodes per hub (C5, C6)."""
    c = ctx.c
    rooms = [ctx.rooms[r] for r in dict.fromkeys(hub.assigned_rooms) if r in ctx.rooms]
    rooms = [r for r in rooms if r.eligible]
    area = assigned_area(hub, ctx.rooms)
    out = []
    if area - AREA_EPS_M2 > c.max_area_m2:
        msg = f"{area:.1f} m² of eligible rooms (limit {c.max_area_m2:g} m²)"
        out.append(_v(CAPACITY, msg, hub.hub_id, _hub_where(hub, ctx), [r.id for r in rooms]))
    n_nodes = len(set(hub.assigned_nodes))
    if n_nodes > c.max_nodes:
        msg = f"{n_nodes} nodes (limit {c.max_nodes})"
        out.append(
            _v(CAPACITY, msg, hub.hub_id, _hub_where(hub, ctx), sorted(set(hub.assigned_nodes)))
        )
    return out


def _rule3_zone(hub: HubOut, ctx: _Context) -> list[Violation]:
    """The hub's eligible rooms are connected through doors and open passages only (#9's graph:
    windows, wall-only adjacency and the entrance never link rooms; C8, C34)."""
    rooms = [r for r in dict.fromkeys(hub.assigned_rooms) if r in ctx.eligible]
    if len(rooms) < 2:
        return []
    zone = ctx.eligible.subgraph(rooms)
    if nx.is_connected(zone):
        return []
    parts = " | ".join(
        ", ".join(sorted(p)) for p in sorted(map(sorted, nx.connected_components(zone)))
    )
    msg = f"zone isn't connected through doors or open passages: {parts}"
    return [_v(ZONE, msg, hub.hub_id, [_rep(ctx.rooms[r]) for r in rooms], rooms)]


def _ring_shape(hub: HubOut, ctx: _Context) -> Violation | None:
    """One rule-4 error when the ring's shape is broken, else None (then rules 4-6 check it).

    Broken: a non-finite coordinate; `closed_sequence` not [hub, each assigned node once, the same
    hub] (open, repeated, ending at another hub); segments not following it; a segment with
    fewer than 2 points."""
    h, seq = hub.hub_id, hub.closed_sequence

    def broken(msg: str) -> Violation:
        return _v(RING, msg, h, _hub_where(hub, ctx), [h])

    points = [
        (hub.location.x, hub.location.y),
        *(p for s in hub.route_segments for p in s.polyline),
    ]
    if not all(math.isfinite(x) and math.isfinite(y) for x, y in points):
        return broken("non-finite coordinate in the location or a polyline")
    if len(seq) < 3 or seq[0] != h:
        return broken(f"closed_sequence must start at {h} and visit its nodes: {seq}")
    if seq[-1] != h:
        return broken(f"the ring ends at {seq[-1]}, not at its own hub {h}")
    middle = seq[1:-1]
    repeated = sorted(n for n, k in Counter(middle).items() if k > 1)
    if repeated:
        return broken(f"{', '.join(repeated)} visited more than once")
    missing = sorted(set(hub.assigned_nodes) - set(middle))
    extra = sorted(set(middle) - set(hub.assigned_nodes))
    if missing or extra:
        gaps = [f"misses {', '.join(missing)}"] * bool(missing) + [
            f"visits {', '.join(extra)}"
        ] * bool(extra)
        return broken(f"closed_sequence {' and '.join(gaps)}")
    if [(s.from_, s.to) for s in hub.route_segments] != list(pairwise(seq)):
        return broken("route_segments don't follow closed_sequence")
    short = [f"{s.from_}→{s.to}" for s in hub.route_segments if len(s.polyline) < 2]
    if short:
        return broken(f"{', '.join(short)} has fewer than 2 points")
    return None


def _rule4_endpoints(hub: HubOut, ctx: _Context) -> list[Violation]:
    """Each segment starts at its `from` point and ends at its `to` point, within tol: the hub's
    location or the node's point in the plan (nodes can't move, slide 2)."""
    tol = ctx.c.tolerance_m
    out = []
    for s in hub.route_segments:
        for pid, pt, verb in ((s.from_, s.polyline[0], "starts"), (s.to, s.polyline[-1], "ends")):
            if pid == hub.hub_id:
                target = (hub.location.x, hub.location.y)
            elif pid in ctx.nodes:
                target = ctx.nodes[pid].xy
            else:
                out.append(_v(RING, f"{pid} isn't a node of the plan", hub.hub_id, [pt], [pid]))
                continue
            d = _dist(pt, target)
            if d > tol:
                msg = f"{s.from_}→{s.to} {verb} {d * 100:.1f} cm from {pid}"
                out.append(_v(RING, msg, hub.hub_id, [pt, target], [pid]))
    return out


def _same_line_overlap(
    p: Point, q: Point, r: Point, s: Point, tol: float
) -> tuple[Point, Point] | None:
    """The stretch two axis-aligned pieces share on one line (longer than tol), else None."""
    for fixed, along in ((1, 0), (0, 1)):  # horizontal pieces (y fixed), then vertical (x fixed)
        if all(abs(u[fixed] - p[fixed]) <= tol for u in (q, r, s)):
            lo = max(min(p[along], q[along]), min(r[along], s[along]))
            hi = min(max(p[along], q[along]), max(r[along], s[along]))
            if hi - lo > tol:
                at = p[fixed]
                return ((lo, at), (hi, at)) if along == 0 else ((at, lo), (at, hi))
    return None


def _shared_stretches(
    a: Sequence[Point], b: Sequence[Point] | None, tol: float
) -> list[tuple[Point, Point]]:
    """Same-line stretches that pieces of a and b share; with b None, a's pieces with each other."""
    pa = list(pairwise(a))
    pb = pa if b is None else list(pairwise(b))
    return [
        st
        for i, (p, q) in enumerate(pa)
        for j, (r, s) in enumerate(pb)
        if b is not None or j > i
        if (st := _same_line_overlap(p, q, r, s, tol))
    ]


def _dead_ends(hub: HubOut, ctx: _Context) -> list[BaseGeometry]:
    """The single-door rooms (`graph.single_door_rooms`) this ring must enter, because its hub
    or one of its nodes is there, each with its one way out (door gap or open passage), grown
    by tol."""
    tol = ctx.c.tolerance_m
    doors = {d.id: d for d in passage_doors(ctx.plan)}
    rooms = {hub.room_id, *(ctx.nodes[n].room_id for n in hub.closed_sequence if n in ctx.nodes)}
    zones = []
    for rid in sorted(rooms):
        if rid not in ctx.single_door:
            continue
        (way_out,) = retrace_links(ctx.graph, rid)
        link = ctx.graph.edges[rid, way_out]
        parts = [ctx.rooms[rid].polygon]
        if link["kind"] == "opening":
            parts.append(link["region"])
        for d in link.get("door_ids", []):
            parts.append(door_gap(doors[d], ctx.plan, ctx.c.grid_m))
        zones.append(unary_union(parts).buffer(tol))
    return zones


def _rule4_retraces(hub: HubOut, ctx: _Context) -> list[Violation]:
    """`strict` for a same-line retrace within one ring (#46 Q5). A retrace into a single-door
    room is legal and scored as O (#43, slide 3 rule 4), so these are exempt: any stretch two
    legs share around a node in a single-door room (`graph.single_door_rooms`: a dead-end
    bathroom door doesn't count, 2026-10-16), and any stretch that touches a single-door room
    (or its passage) holding the ring's hub or one of its nodes, whichever legs share it (a hub
    there must leave and re-enter through the same door, #68 review). Overlaps between
    different rings are cost only."""
    tol = ctx.c.tolerance_m
    legs = [clean_polyline(s.polyline, tol) for s in hub.route_segments]
    labels = [f"{s.from_}→{s.to}" for s in hub.route_segments]
    seq = hub.closed_sequence
    dead_ends = _dead_ends(hub, ctx)
    out = []
    for i in range(len(legs)):
        for j in range(i, len(legs)):
            node = ctx.nodes.get(seq[j]) if j == i + 1 else None
            if node is not None and node.room_id in ctx.single_door:
                continue
            for a, b in _shared_stretches(legs[i], legs[j] if j != i else None, tol):
                stretch = shapely.LineString([a, b])
                if any(zone.intersects(stretch) for zone in dead_ends):
                    continue
                what = labels[i] if i == j else f"{labels[i]} and {labels[j]}"
                msg = f"{what} retrace {_dist(a, b):.2f} m of the same line (#46 Q5)"
                out.append(_v(RING, msg, hub.hub_id, [a, b], [labels[i], labels[j]], "strict"))
    return out


def _rule5_leg(
    pts: Sequence[Point], space: _FreeSpace, c: Constraints, hub_id: str | None, label: str
) -> list[Violation]:
    """Rule 5 on one polyline: horizontal or vertical pieces only, inside free space, and room
    changes only inside door gaps or open passages (C12-C14)."""
    tol = c.tolerance_m
    out = []
    for p, q in pairwise(pts):
        if abs(p[0] - q[0]) > tol and abs(p[1] - q[1]) > tol:
            out.append(_v(FREE_SPACE, f"{label} has a diagonal piece", hub_id, [p, q], [label]))
    line = shapely.LineString(pts)
    if not line.within(space.free):
        outside = line.difference(space.free)
        crossed = [
            name
            for name, geom in (
                ("a wall", space.walls),
                ("a window", space.windows),
                ("the entrance", space.entrance),
            )
            if outside.intersects(geom)
        ]
        causes = [f"crosses {' and '.join(crossed)}"] if crossed else []
        if not outside.difference(space.inner).is_empty:
            causes.append("leaves inner")
        msg = f"{label} {' and '.join(causes) or 'leaves free space'} ({outside.length:.3f} m outside free space)"
        out.append(
            _v(FREE_SPACE, msg, hub_id, map(tuple, shapely.get_coordinates(outside)), [label])
        )
    for piece in get_geometries(line.difference(space.passages)):
        if piece.length <= tol or any(zone.covers(piece) for zone in space.room_zones):
            continue
        msg = f"{label} changes room outside a door or open passage"
        out.append(_v(FREE_SPACE, msg, hub_id, map(tuple, shapely.get_coordinates(piece)), [label]))
    return out


def _rule6_hub_point(hub: HubOut, ctx: _Context) -> list[Violation]:
    """`error` unless the hub sits in an eligible room it's assigned, at least the hub clearance
    from wall geometry; `strict` if only its room's edge (a door or open passage) is closer than
    the clearance (#46 Q3). A hub may share a node's room (C18)."""
    c, tol = ctx.c, ctx.c.tolerance_m
    clearance = c.hub_wall_clearance_m
    loc = (hub.location.x, hub.location.y)
    p = shapely.Point(loc)
    room = ctx.rooms.get(hub.room_id)

    def fail(msg: str, severity: str = "error") -> list[Violation]:
        return [_v(HUB_POINT, msg, hub.hub_id, [loc], [hub.room_id], severity)]

    if room is None:
        return fail(f"room_id {hub.room_id} isn't a room of the plan")
    if not room.eligible:
        return fail(f"{room.id} ({room.label}) is an excluded {room.type}")
    if hub.room_id not in hub.assigned_rooms:
        return fail(f"{room.id} isn't one of {hub.hub_id}'s assigned rooms")
    if not room.polygon.buffer(tol).covers(p):
        return fail(f"the location isn't inside {room.id} ({room.label})")
    d_wall = p.distance(ctx.hub_walls)
    if d_wall < clearance - tol:
        return fail(
            f"{d_wall:.3f} m from walls, windows or the outside (clearance {clearance:g} m)"
        )
    d_edge = p.distance(room.polygon.exterior)
    if d_edge < clearance - tol:
        msg = f"{d_edge:.3f} m from {room.id}'s edge, a door or open passage (clearance {clearance:g} m, #46 Q3)"
        return fail(msg, "strict")
    return []


def check_rules(plan: Plan, submission: Submission, c: Constraints = _DEFAULT) -> list[Violation]:
    """Slide 3's six hard rules on a submission and its plan (C39).

    Never raises: anything that passed validation comes back as violations. Reported lengths
    (`length_m`, `ring_length_m`, `reported_metrics`) are never read (C42), so wrong or negative
    ones break nothing. A hub whose ring shape is broken (`_ring_shape`) gets one rule-4 error
    and skips the rest of rules 4-6 (no cascades); rules 1-3 don't depend on the ring. `error`
    is a certain slide break; `strict` a conservative reading (#46 Q3, Q5).
    """
    ctx = RuleContext(plan, c)
    out = _rule1_coverage(submission, ctx.ctx)
    for hub in submission.hubs:
        out += hub_rules(hub, ctx)
    return sorted(out, key=lambda v: (v.rule, v.hub_id or "", v.message))


class RuleContext:
    """What `check_rules` reads from a plan, built once: the room graph (#9) and its views, and
    rule 5's free space on first use. `hub_rules` with one context checks many candidate rings of
    the same plan without rebuilding either (#20)."""

    def __init__(self, plan: Plan, c: Constraints = _DEFAULT) -> None:
        graph = build_graph(plan, c)  # also fills plan.openings, which rule 5 needs
        self.ctx = _Context(
            plan=plan,
            c=c,
            rooms={r.id: r for r in plan.rooms},
            nodes={n.id: n for n in plan.nodes},
            graph=graph,
            eligible=eligible_graph(graph),
            single_door=single_door_rooms(graph),
            hub_walls=wall_geometry(plan),
        )
        self._space: _FreeSpace | None = None

    @property
    def space(self) -> _FreeSpace:
        if self._space is None:
            self._space = _free_space(self.ctx.plan, self.ctx.c)
        return self._space


def hub_rules(hub: HubOut, ctx: RuleContext) -> list[Violation]:
    """Rules 2-6 for one hub (`check_rules` without rule 1's plan-wide coverage), unsorted."""
    c = ctx.ctx.c
    out = _rule2_capacity(hub, ctx.ctx) + _rule3_zone(hub, ctx.ctx)
    if broken := _ring_shape(hub, ctx.ctx):
        return [*out, broken]
    out += _rule4_endpoints(hub, ctx.ctx) + _rule4_retraces(hub, ctx.ctx)
    for s in hub.route_segments:
        out += _rule5_leg(s.polyline, ctx.space, c, hub.hub_id, f"{s.from_}→{s.to}")
    return out + _rule6_hub_point(hub, ctx.ctx)


# --------------------------------------------------------------------------- checklist and CLI

_CHECKLIST = (
    ((COVERAGE, RING), "each node once, ring ends at its own hub"),
    ((CAPACITY,), "≤ {area:g} m² and ≤ {nodes} nodes per hub"),
    ((ZONE,), "each zone connected through doors and open passages only"),
    ((FREE_SPACE,), "axis-aligned, no wall or window crossed"),
)
# not on slide 5's list, so shown only when rule 6 has a violation: no error hides behind 5 ✓
_HUB_LINE = ((HUB_POINT,), "each hub in an assigned eligible room, ≥ {clearance:g} m from walls")


def _check_line(
    rules: tuple[int, ...], text: str, violations: Sequence[Violation], c: Constraints
) -> str:
    errors = sum(v.rule in rules and v.severity == "error" for v in violations)
    strict = sum(v.rule in rules and v.severity == "strict" for v in violations)
    text = text.format(area=c.max_area_m2, nodes=c.max_nodes, clearance=c.hub_wall_clearance_m)
    return (
        ("✗ " if errors else "✓ ")
        + text
        + (f" (strict: {strict})" if strict and not errors else "")
    )


def checklist(
    violations: Sequence[Violation],
    metrics: Metrics | None,
    lower_bound: int,
    area_m2: float,
    n_nodes: int,
    c: Constraints = _DEFAULT,
    proven_min: int | None = None,
) -> list[str]:
    """Slide 5's "What a reviewer confirms": 5 lines, each starting ✓ or ✗ (C26).

    A line is ✗ only for an `error` among its rules; with only `strict` ones it stays ✓ and says
    how many. Line 5 compares H with the lower bound (a ranking fact, not a violation);
    `metrics` is None when they couldn't be computed. Rule 6 isn't on slide 5's list, so a sixth
    line appears only when it has a violation (#68 review): no error hides behind 5 ✓.
    `proven_min` is the hub count solve() proved minimal (#24, T7): H > LB at that count is ✓
    "proven minimum". The checker alone proves nothing, so without it H > LB stays ✗.
    """
    out = [_check_line(rules, text, violations, c) for rules, text in _CHECKLIST]
    facts = f"({area_m2:.1f} m², {n_nodes} nodes)"
    if metrics is None:
        out.append(f"✗ hubs vs lower bound {lower_bound} {facts}: not computed")
    else:
        h = metrics.H
        op = "=" if h == lower_bound else (">" if h > lower_bound else "<")
        proven = h > lower_bound and h == proven_min
        tick = "✓" if h == lower_bound or proven else "✗"
        line = f"{tick} {h} hub{'' if h == 1 else 's'} {op} lower bound {lower_bound} {facts}"
        out.append(line + (" · proven minimum" if proven else ""))
    rules, text = _HUB_LINE
    if any(v.rule in rules for v in violations):
        out.append(_check_line(rules, text, violations, c))
    return out


def verdict(violations: Sequence[Violation]) -> str:
    """`invalid` on any error, `valid (strict only)` with only strict ones (#16's
    `submitted_strict_only`), else `valid`."""
    if any(v.severity == "error" for v in violations):
        return "invalid"
    return "valid (strict only)" if violations else "valid"


def metrics_text(m: Metrics | None) -> str:
    """`H 1 · L 24.805 m · X 0 · O 0.000 m · B 12 · J 30.805`: the one format the CLI and the
    app's reviewer panel (#12) both print."""
    if m is None:
        return "not computed (non-finite coordinates)"
    return f"H {m.H} · L {m.L:.3f} m · X {m.X} · O {m.O:.3f} m · B {m.B} · J {m.J:.3f}"


def reported_mismatches(sub: Submission, c: Constraints = _DEFAULT) -> list[str]:
    """Reported values that disagree with the polylines by more than the tolerance (#23).

    The organisers ignore `length_m`, `ring_length_m` and `reported_metrics` and recompute them
    (C42), so a mismatch breaks no rule: it's a warning that our writer (or a hand edit) wrote
    something else than the cable drawn. Non-finite coordinates are rule 4's, not this."""
    tol = c.tolerance_m
    out: list[str] = []

    def differ(reported: float, recomputed: float) -> bool:
        return math.isfinite(recomputed) and abs(reported - recomputed) > tol

    total = 0.0
    for h in sub.hubs:
        ring = 0.0
        for s in h.route_segments:
            length = segment_length(s.polyline) if len(s.polyline) >= 2 else 0.0
            ring += length
            if differ(s.length_m, length):
                out.append(
                    f"reported {h.hub_id} {s.from_}→{s.to} length_m {s.length_m:.3f} m, "
                    f"recomputed {length:.3f} m (the organisers recompute it, C42)"
                )
        total += ring
        if differ(h.ring_length_m, ring):
            out.append(
                f"reported {h.hub_id} ring_length_m {h.ring_length_m:.3f} m, "
                f"recomputed {ring:.3f} m (the organisers recompute it, C42)"
            )
    rm = sub.reported_metrics
    if rm.hub_count != len(sub.hubs):
        out.append(f"reported hub_count {rm.hub_count}, the submission has {len(sub.hubs)} hubs")
    if differ(rm.total_length_m, total):
        out.append(
            f"reported total_length_m {rm.total_length_m:.3f} m, recomputed {total:.3f} m "
            "(the organisers recompute it, C42)"
        )
    return out


@dataclass(frozen=True)
class Report:
    """Everything a reviewer sees: what the CLI prints and the app's panel renders (#12)."""

    violations: list[Violation]
    metrics: Metrics | None  # None when a coordinate isn't finite (rule 4 reports it)
    lower_bound: int
    lines: list[str]  # `checklist`
    verdict: str


def report(
    plan: Plan, submission: Submission, c: Constraints = _DEFAULT, proven_min: int | None = None
) -> Report:
    """The rules, the metrics, the checklist and the verdict for one submission; `proven_min` as
    in `checklist`."""
    violations = check_rules(plan, submission, c)
    rooms = {r.id: r for r in plan.rooms}
    hub_areas = {h.hub_id: assigned_area(h, rooms) for h in submission.hubs}
    try:
        m = metrics(submission.hubs, c, hub_areas=hub_areas)
    except ValueError:  # a non-finite coordinate: rule 4 reports it
        m = None
    area = sum(r.area_m2 for r in plan.rooms if r.eligible)
    lb = lower_bound(area, len(plan.nodes), c)
    return Report(
        violations=violations,
        metrics=m,
        lower_bound=lb,
        lines=checklist(violations, m, lb, area, len(plan.nodes), c, proven_min),
        verdict=verdict(violations),
    )


def main(argv: list[str] | None = None) -> int:
    """`python -m hackhive.checker <plan.json> <submission.json>`: the checklist, every violation
    and the verdict. Exit 0 if valid (strict-only too), 1 if invalid, 2 if an input can't be read."""
    for stream in (sys.stdout, sys.stderr):  # "✓", "✗", "²" and "·" would break cp1252 on Windows
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(
        prog="python -m hackhive.checker", description="Check a submission."
    )
    p.add_argument("plan", type=Path, help="the plan JSON file")
    p.add_argument("submission", type=Path, help="the slide-8 submission JSON file")
    args = p.parse_args(argv)
    c = _DEFAULT
    try:
        plan = load_plan(args.plan, c)
        sub = Submission.model_validate_json(args.submission.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as e:
        print(f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}", file=sys.stderr)
        return 2
    if sub.plan_id != plan.plan_id:
        print(
            f"warning: the submission is for {sub.plan_id}, the plan is {plan.plan_id}",
            file=sys.stderr,
        )
    r = report(plan, sub, c)
    for line in r.lines:
        print(line)
    print(f"metrics: {metrics_text(r.metrics)}")
    for v in r.violations:
        print(f"rule {v.rule} [{v.severity}] {v.hub_id or 'plan'}: {v.message}")
    print(r.verdict)
    return 1 if r.verdict == "invalid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
