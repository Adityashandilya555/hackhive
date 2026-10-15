"""The organisers' checker, mirrored (slides 3, 6, 7).

Metrics half (#43): H, L, X, O, B, J and the assigned-area gap, recomputed only from the submitted
polylines at the 1 mm tolerance; reported lengths are never read (C42). Where the slides are
ambiguous we count strictly (docs/decision_conventions.md §6). The hard rules (#11) go below.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from hackhive.contracts import Constraints, HubOut

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


def count_bends(polylines: Sequence[Sequence[Point]], closed: bool, tol: float = TOL) -> int:
    """B for one cable made of consecutive legs (slide 7; decision_conventions §6 B row).

    +1 at every direction change inside a leg, at every node (last direction in vs first out) and,
    if closed, at the hub. A reversal counts once; straight through counts 0. Zero-length legs are
    skipped.
    """
    legs = [p for p in (clean_polyline(line, tol) for line in polylines) if len(p) >= 2]
    bends = sum(len(p) - 2 for p in legs)
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


def _overlaps(
    legs: Sequence[Sequence[Point]], owners: Sequence[str], tol: float
) -> tuple[float, dict[str, float]]:
    """Total O and each owner's credit (every owner on a shared stretch gets its full (k-1)·len)."""
    lines: dict[tuple[str, float], list[tuple[float, float, str]]] = {}
    raw: list[tuple[str, float, float, float, str]] = []
    for leg, owner in zip(legs, owners, strict=True):
        for a, b in pairwise(leg):
            if abs(a[1] - b[1]) <= tol:
                raw.append(("h", (a[1] + b[1]) / 2, a[0], b[0], owner))
            elif abs(a[0] - b[0]) <= tol:
                raw.append(("v", (a[0] + b[0]) / 2, a[1], b[1], owner))
            # diagonal pieces are a rule-5 violation (#11); they take no part in O
    for axis in ("h", "v"):
        pieces = [r for r in raw if r[0] == axis]
        rep = _cluster([r[1] for r in pieces], tol)
        for _, c, s, e, owner in pieces:
            lines.setdefault((axis, rep[c]), []).append((min(s, e), max(s, e), owner))

    total = 0.0
    credit: dict[str, float] = {o: 0.0 for o in owners}
    for intervals in lines.values():
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
                total += (k - 1) * (pos - prev)
                for o in active:
                    credit[o] += (k - 1) * (pos - prev)
            active[owner] += d
            if not active[owner]:
                del active[owner]
            prev = pos
    return total, credit


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


def _crossings(
    legs: Sequence[Sequence[Point]], owners: Sequence[str], terminals: Sequence[Point], tol: float
) -> list[tuple[Point, set[str]]]:
    pieces = [(li, i) for li, leg in enumerate(legs) for i in range(len(leg) - 1)]
    found: list[tuple[Point, set[str]]] = []
    for n, (la, ia) in enumerate(pieces):
        for lb, ib in pieces[n + 1 :]:
            A, B = legs[la], legs[lb]
            for p in _meet(A[ia], A[ia + 1], B[ib], B[ib + 1], tol):
                if any(_dist(p, t) <= tol for t in terminals):
                    continue
                # cables that arrive and leave p together don't meet there: the shared vertex of
                # consecutive pieces, or a co-routed corner of a retrace (#43 decided ambiguity)
                if _coincide(_leaving(A, ia, p, tol), _leaving(B, ib, p, tol), tol):
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
    """X's points (slides 6-7): where cables cross or meet away from every terminal, once per point.

    Strict (decision_conventions §6 ⚠ #46 Q4): a T-touch and each divergence end of a shared
    same-line stretch count. Each polyline is one leg.
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


def metrics(
    hubs: Sequence[HubOut],
    c: Constraints = _DEFAULT,
    *,
    hub_areas: Mapping[str, float] | None = None,
) -> Metrics:
    """H, L, X, O, B, J (and the area gap if `hub_areas` is given) from the polylines only."""
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
        per_B[h.hub_id] = count_bends(ring, closed=True, tol=tol)

    terminals = [p for leg in legs if leg for p in (leg[0], leg[-1])]
    terminals += [(h.location.x, h.location.y) for h in hubs]
    O, per_O = _overlaps(legs, owners, tol)  # noqa: E741
    points = _crossings(legs, owners, terminals, tol)
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
