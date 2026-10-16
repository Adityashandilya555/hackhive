"""Read one plan JSON into metric layers (#4), then into the `Plan` with ids and fixed nodes (#6).

Slide 4: metres = drawing units ÷ √(inner.area / area). `net_area` is never read (it is 0 in 32%
of ResPlan plans). `python -m hackhive.io <plan.json>` prints the plan's facts. Doors-to-rooms and
open passages are #9.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import shapely
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    mapping,
    shape,
)
from shapely.geometry.base import BaseGeometry

from hackhive.contracts import (
    AREA_EPS_M2,
    Constraints,
    Door,
    FoldedPart,
    Frame,
    Node,
    Plan,
    Room,
    lower_bound,
)

# --------------------------------------------------------------------------- copied from ResPlan
# Source: https://github.com/m-agour/ResPlan/blob/e2b78fe/resplan_utils.py
#   normalize_keys (L56-60) and get_geometries (L74-82), commit e2b78fe.
# Our changes: get_geometries recurses into nested Multi*/GeometryCollection members (a
# MultiPolygon inside a GeometryCollection comes back as its polygons, C2), and the type hints use
# builtin generics for ruff. Behaviour is otherwise unchanged.
#
# MIT License
#
# Copyright (c) 2025 The ResPlan Authors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


def normalize_keys(plan: dict[str, Any]) -> dict[str, Any]:
    """Normalize common key typos / variations in-place (balacony→balcony)."""
    if "balacony" in plan and "balcony" not in plan:
        plan["balcony"] = plan.pop("balacony")
    return plan


def get_geometries(geom_data: Any) -> list[Any]:
    """Safely extract individual geometries from single/multi/collections."""
    if geom_data is None:
        return []
    if isinstance(geom_data, (Polygon, LineString, Point)):
        return [] if geom_data.is_empty else [geom_data]
    if isinstance(geom_data, (MultiPolygon, MultiLineString, GeometryCollection)):
        # our fix: recurse, so nested collections are flattened too
        return [p for g in geom_data.geoms for p in get_geometries(g)]
    return []


# --------------------------------------------------------------------------- ours

ELIGIBLE = ("living", "bedroom", "kitchen", "storage")
EXCLUDED = ("bathroom", "balcony", "stair", "garden", "pool", "parking", "land")
_LAYERS = ("inner", "wall", "door", "window", "front_door")


@dataclass(frozen=True)
class Layers:
    """One plan's geometry in metres (frame per `Constraints.flip_y` / `origin_shift`)."""

    inner: Polygon | MultiPolygon
    wall: list[Polygon]
    door: list[Polygon]
    window: list[Polygon]
    front_door: list[Polygon]
    rooms: dict[str, list[Polygon]]  # every type in ELIGIBLE + EXCLUDED; [] when absent
    m_per_unit: float
    wall_depth_m: float
    infeasible_reasons: list[str]
    # geometry keys that weren't read (e.g. a misspelt room type); #6 / #38 fail loudly on them,
    # since a dropped eligible room breaks coverage (slide 3 rule 1)
    unknown_keys: list[str]
    # the drawing-units → metres frame used above, for points that come with the plan (organiser
    # nodes, #6), so they get exactly the same scale, flip and shift as the geometry. It's a
    # closure, so Layers can't be pickled (e.g. by st.cache_data): cache the Plan instead.
    to_metres: Callable[[np.ndarray], np.ndarray]


def read_json(path: str | Path) -> dict:
    """One plan file, ResPlan- or organiser-shaped; `load_plan` normalises it (#38)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- organiser input (#38)


class InputFormatError(ValueError):
    """The input can't be read without guessing. `solve.submit` turns it into sidecar status
    `input_error` (#16): an unreadable plan gets no submission, never a guessed one."""


# The organisers' input format is unknown until their sample arrives (#46 Q1). Every key the
# adapter reads is named here, so adopting the real format is a change to this table only.
ORGANISER_KEYS = {
    "plan_id": "plan_id",  # used verbatim in the output (slide 8)
    "area": "area",  # m², the scale's other half (slide 4)
    "wall_depth": "wall_depth",  # drawing units
    "inner": "inner",  # GeoJSON (Multi)Polygon, drawing units
    "walls": "walls",
    "doors": "doors",
    "windows": "windows",
    "front_door": "front_door",
    "rooms": "rooms",  # list of {id, type, polygon}
    "room_id": "id",
    "room_type": "type",
    "room_polygon": "polygon",
    "nodes": "nodes",  # list of {id, x, y}, drawing units
    "node_id": "id",
    "node_room_id": "room_id",  # when present, authoritative (checked against the geometry)
    "node_x": "x",
    "node_y": "y",
}
_ORGANISER_REQUIRED = ("area", "inner", "wall_depth", "rooms")
_RESPLAN_REQUIRED = ("area", "inner")


def detect_format(raw: Any) -> Literal["resplan", "organiser", "unknown"]:
    """By key presence: a `rooms` list means the organisers' shape; otherwise ResPlan's layer keys
    (inner, wall, door, window, front_door) mean ResPlan, whose own checks then name any odd
    key; otherwise a `nodes` list or a `plan_id` means the organisers' shape."""
    if not isinstance(raw, dict):
        return "unknown"
    k = ORGANISER_KEYS
    if isinstance(raw.get(k["rooms"]), list):
        return "organiser"
    if any(key in raw for key in _LAYERS):
        return "resplan"
    if isinstance(raw.get(k["nodes"]), list) or k["plan_id"] in raw:
        return "organiser"
    return "unknown"


def normalise(raw: Any) -> tuple[dict, dict[str, str] | None]:
    """Either input shape as a ResPlan-shaped dict, plus the organiser's room ids by label.

    ResPlan input is returned as is (ids None). Organiser input becomes one ResPlan key per room
    type (polygons in input order, so `<type>_<i>` labels map back to the organiser's ids) and
    a `nodes` list. Raises InputFormatError on an unknown shape or a missing required key.
    """
    fmt = detect_format(raw)
    if fmt == "unknown":
        raise InputFormatError(
            "unknown input format: expected ResPlan keys (inner, area, wall, room types) "
            f"or an organiser {ORGANISER_KEYS['rooms']!r} / {ORGANISER_KEYS['nodes']!r} list"
        )
    if fmt == "resplan":
        _require(raw, {k: k for k in _RESPLAN_REQUIRED}, raw.get("id"))
        return raw, None

    k = ORGANISER_KEYS
    pid = raw.get(k["plan_id"])
    _require(raw, {name: k[name] for name in _ORGANISER_REQUIRED}, pid)
    out: dict[str, Any] = {
        "id": pid,
        "plan_id": pid,
        "area": raw[k["area"]],
        "wall_depth": raw[k["wall_depth"]],
        "inner": raw[k["inner"]],
        "wall": raw.get(k["walls"]),
        "door": raw.get(k["doors"]),
        "window": raw.get(k["windows"]),
        "front_door": raw.get(k["front_door"]),
    }
    by_type: dict[str, list] = {}
    ids: dict[str, str] = {}
    for room in raw[k["rooms"]]:
        kind, rid, poly = (
            room.get(k["room_type"]),
            room.get(k["room_id"]),
            room.get(k["room_polygon"]),
        )
        if kind not in ELIGIBLE + EXCLUDED:
            raise InputFormatError(f"plan {pid}: room {rid} has unknown type {kind!r}")
        if rid is None or str(rid) in ids.values():
            raise InputFormatError(f"plan {pid}: room ids must be present and unique ({rid!r})")
        parts = _polygons(poly)
        if len(parts) != 1:
            raise InputFormatError(f"plan {pid}: room {rid} is not one polygon")
        ids[f"{kind}_{len(by_type.get(kind, []))}"] = str(rid)
        by_type.setdefault(kind, []).append(parts[0])
    for kind, polys in by_type.items():
        out[kind] = mapping(MultiPolygon(polys))
    if raw.get(k["nodes"]) is not None:
        out["nodes"] = [
            {
                "id": n.get(k["node_id"]),
                "room_id": n.get(k["node_room_id"]),
                "x": n.get(k["node_x"]),
                "y": n.get(k["node_y"]),
            }
            for n in raw[k["nodes"]]
        ]
    return out, ids


def _require(raw: dict, keys: dict[str, str], pid: Any) -> None:
    missing = [name for name, key in keys.items() if raw.get(key) is None]
    if missing:
        raise InputFormatError(f"plan {pid}: missing {', '.join(missing)}")


def _repaired(g: Polygon) -> list[Polygon]:
    """g itself if valid, else its non-empty polygon parts after a "structure" repair (C20).

    A repair can split one polygon into several (a bow-tie gives two).
    """
    if g.is_valid:
        return [g]
    fixed = shapely.make_valid(g, method="structure", keep_collapsed=False)
    return [p for p in get_geometries(fixed) if isinstance(p, Polygon) and not p.is_empty]


def _polygons(geojson: Any) -> list[Polygon]:
    """Flatten to polygons in input order, repairing only the invalid ones (C20).

    Each part is repaired on its own: repairing a whole MultiPolygon merges overlapping parts
    (plan 1293's doors go 10 → 9), and one polygon is one room or door (C2).
    """
    if not geojson:
        return []
    return [
        q for g in get_geometries(shape(geojson)) if isinstance(g, Polygon) for q in _repaired(g)
    ]


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _positive(plan: dict, key: str) -> float:
    """plan[key] as a finite number > 0, else ValueError naming the plan."""
    v = plan.get(key)
    if not _is_number(v) or v <= 0:
        raise ValueError(f"plan {plan.get('id')}: {key} must be a finite number > 0, got {v!r}")
    return float(v)


def read_layers(raw: dict, c: Constraints | None = None) -> Layers:
    """Repair, flatten and scale one ResPlan plan dict to metres, once (slide 4, C19-C21, C27).

    Frame (slide 8, C24): by default (x, y) · m_per_unit, i.e. scaled about the origin with no flip
    and no translation (ResPlan is already y-up). `c.flip_y` gives y' = (y_max − y) · m, with y_max
    the inner boundary's max y in drawing units. `c.origin_shift` is then subtracted **in metres**
    (its units aren't specified; #38 owns the organiser frame check).

    Polygons are repaired before scaling and again after it: scaling can make a valid input
    self-intersect through float rounding (plan 1293 wall[5], plan 13344 land[0]).

    Raises ValueError naming the plan id if `area` or `wall_depth` is missing, non-finite or ≤ 0,
    or `inner` is empty. The caller's dict is not modified.
    """
    c = c or Constraints()
    # net_area is never copied, so nothing below can read it (slide 4)
    plan = normalize_keys({k: raw[k] for k in raw if k != "net_area"})
    pid = plan.get("id")

    inner_parts = _polygons(plan.get("inner"))
    inner_units = shapely.union_all(inner_parts) if inner_parts else Polygon()
    if inner_units.is_empty or inner_units.area <= 0:
        raise ValueError(f"plan {pid}: inner is empty")
    m = math.sqrt(_positive(plan, "area") / inner_units.area)
    wall_depth = _positive(plan, "wall_depth")

    y_max = inner_units.bounds[3]
    shift = np.asarray(c.origin_shift, dtype=float)

    def to_metres(xy: np.ndarray) -> np.ndarray:
        out = xy * m
        if c.flip_y:
            out[:, 1] = (y_max - xy[:, 1]) * m
        return out - shift

    def scaled(geojson: Any) -> list[Polygon]:
        return [q for p in _polygons(geojson) for q in _repaired(shapely.transform(p, to_metres))]

    inner = shapely.transform(inner_units, to_metres)
    if not inner.is_valid:
        inner = shapely.make_valid(inner, method="structure", keep_collapsed=False)

    rooms = {t: scaled(plan.get(t)) for t in ELIGIBLE + EXCLUDED}
    too_big = any(p.area > c.max_area_m2 + AREA_EPS_M2 for t in ELIGIBLE for p in rooms[t])
    return Layers(
        inner=inner,
        wall=scaled(plan.get("wall")),
        door=scaled(plan.get("door")),
        window=scaled(plan.get("window")),
        front_door=scaled(plan.get("front_door")),
        rooms=rooms,
        m_per_unit=m,
        wall_depth_m=wall_depth * m,
        infeasible_reasons=[f"room > {c.max_area_m2:g} m²"] if too_big else [],
        unknown_keys=[
            k
            for k, v in plan.items()
            # "nodes" (organiser points, maybe a GeoJSON MultiPoint) is read by load_plan
            if k not in (*_LAYERS, *ELIGIBLE, *EXCLUDED, "nodes")
            and isinstance(v, dict)
            and "type" in v
        ],
        to_metres=to_metres,
    )


# --------------------------------------------------------------------------- the Plan (#6)

NODE_CLEARANCE_M = 0.5  # slide 2: node clearance, 0.5 m from walls
# An eligible room further than this past `inner` gets a warning: `inner` may not match the rooms
# (#78). ResPlan's 16,317 train/val/test plans: at most 16% past it, except 8 with a stray room
# drawn 70-100% outside the house (16067, 12536, 8137, …); its 683 augmented plans: 541 past 20%.
# Only a node outside `inner` rejects the plan: no cable can reach it (rule 5).
ROOM_OUTSIDE_INNER_WARN = 0.20
_GAP_EPS_M = 1e-6  # closes float-sized gaps when a folded sliver's union isn't one polygon
_MIC_TOL_M = 1e-4  # max clearance to 0.1 mm; points that close to it count as tied


def _mic(g: Any) -> tuple[tuple[float, float], float]:
    """g's max-clearance point and its distance to g's boundary (any parts: the largest circle).

    Many rooms have a plateau of equally clear points (a rectangle's centre line), and GEOS picks
    a different one on macOS arm64 than on Linux (up to 0.73 m apart, #38), so take the plateau's
    centre: a function of the geometry alone, the same on every platform.
    """
    line = shapely.maximum_inscribed_circle(g, _MIC_TOL_M)
    r = line.length
    top = g.buffer(-(r - _MIC_TOL_M)) if r > _MIC_TOL_M else shapely.Point()
    if top.is_empty:
        p = shapely.Point(line.coords[0])
    else:
        p = top.centroid if top.contains(top.centroid) else top.point_on_surface()
    return (float(p.x), float(p.y)), float(g.boundary.distance(p))


def _free(room: Polygon, obstacles: Any) -> Any:
    """The room minus walls and windows. The overlay can leave a float-sized line beside the
    polygon (16552), which maximum_inscribed_circle and boundary reject, so only polygons stay."""
    free = room.difference(obstacles)
    if isinstance(free, GeometryCollection):
        free = MultiPolygon([g for g in get_geometries(free) if isinstance(g, Polygon)])
    return free


def _node_spot(room: Polygon, obstacles: Any) -> tuple[tuple[float, float], float]:
    """The max-clearance point of the room's free space (the room minus walls and windows) and
    its clearance, measured to the room outline (door gaps count, #46 Q3) and to those walls.

    ResPlan rooms can run under walls (1293 living_0), so the bare outline isn't enough (slide 2:
    nodes 0.5 m from walls). A room entirely under walls keeps a node at its own max-clearance
    point with clearance 0, so it's flagged rather than dropped.
    """
    free = _free(room, obstacles)
    if free.is_empty:
        return _mic(room)[0], 0.0
    return _mic(free)


def _clearance(room: Polygon, obstacles: Any, xy: tuple[float, float]) -> float:
    """A given node's distance to its room's free-space edge; 0 if it lies under a wall."""
    free, p = _free(room, obstacles), Point(xy)
    return free.boundary.distance(p) if free.covers(p) else 0.0


def _one_polygon(g: Any) -> Polygon | None:
    """g as a single Polygon, closing float-sized gaps once; None if it stays several parts."""
    if not isinstance(g, Polygon):
        g = g.buffer(_GAP_EPS_M, join_style="mitre").buffer(-_GAP_EPS_M, join_style="mitre")
    return g if isinstance(g, Polygon) and not g.is_empty else None


def _fold_slivers(
    parts: list[Polygon], obstacles: Any
) -> tuple[dict[int, Polygon], list[tuple[int, int, float]]]:
    """Fold living parts with no free-space point NODE_CLEARANCE_M clear into a neighbour (C38).

    Generator only, judged on the same free space as the node (`_node_spot`). A sliver folds into
    the living part it shares the longest boundary with, else into the nearest one. It stays its
    own room (its node then gets a clearance warning) if no living part has room for a node, or
    if the union isn't a single polygon. Returns the surviving parts by input index and the
    folds as (sliver index, receiver index, sliver m²).
    """
    clear = [_node_spot(p, obstacles)[1] >= NODE_CLEARANCE_M for p in parts]
    receivers = [i for i, ok in enumerate(clear) if ok]
    kept = dict(enumerate(parts))
    folds = []
    for s in (i for i, ok in enumerate(clear) if not ok and receivers):
        sliver = parts[s]
        score = {
            i: (sliver.boundary.intersection(kept[i].boundary).length, -sliver.distance(kept[i]))
            for i in receivers
        }
        into = max(receivers, key=score.__getitem__)
        union = _one_polygon(kept[into].union(sliver))
        if union is not None:
            kept[into] = union
            del kept[s]
            folds.append((s, into, sliver.area))
    return kept, folds


def _given_points(nodes: Any, pid: Any) -> list[tuple[str | None, str | None, float, float]]:
    """Organiser node points as (id or None, room id or None, x, y) in drawing units.

    Accepts `[x, y]` or `{"x", "y", "id"?, "room_id"?}` items, or a GeoJSON MultiPoint. The format
    and the drawing units are assumptions until #46 Q1 is answered; #38 owns the organiser format.
    """
    multipoint = isinstance(nodes, dict) and nodes.get("type") == "MultiPoint"
    items = nodes.get("coordinates") if multipoint else nodes
    if not isinstance(items, list):
        raise ValueError(f"plan {pid}: nodes must be a list or a GeoJSON MultiPoint: {nodes!r}")
    out = []
    for it in items:
        if isinstance(it, dict):
            nid, rid, xy = it.get("id"), it.get("room_id"), (it.get("x"), it.get("y"))
        elif isinstance(it, list | tuple) and len(it) == 2:
            nid, rid, xy = None, None, tuple(it)
        else:
            raise ValueError(f"plan {pid}: node {it!r} is not [x, y] or {{x, y}}")
        if not all(_is_number(v) for v in xy):
            raise ValueError(f"plan {pid}: node {it!r} needs finite x and y")
        out.append(
            (
                None if nid is None else str(nid),
                None if rid is None else str(rid),
                float(xy[0]),
                float(xy[1]),
            )
        )
    return out


NODE_SNAP_M = 0.10  # a given node this close outside an eligible room is assigned to it (#38)


def _pick_frame(xy: np.ndarray, rooms: list[Room], inner: BaseGeometry, pid: Any) -> Frame:
    """The first of identity, flip_y, origin_shift under which every given node is in an
    eligible room or <= NODE_SNAP_M from one (#38). Raises InputFormatError if none works.

    The test maps the nodes back into the geometry's frame; the caller then moves the
    *geometry* into the nodes' frame (`_to_node_frame`), so given nodes never move (slide 2)."""
    eligible = [r for r in rooms if r.eligible]
    x0, y0, _, y1 = inner.bounds
    flipped = xy.copy()
    flipped[:, 1] = y0 + y1 - xy[:, 1]
    frames = (("identity", xy), ("flip_y", flipped), ("origin_shift", xy + [x0, inner.bounds[1]]))

    def misses(pts: np.ndarray) -> list[Point]:
        out = []
        for x, y in pts:
            p = Point(x, y)
            if not any(r.polygon.distance(p) <= NODE_SNAP_M for r in eligible):
                out.append(p)
        return out

    for name, pts in frames:
        if not misses(pts):
            return name
    bad = misses(xy)
    other = next((r.label for r in rooms if not r.eligible and r.polygon.covers(bad[0])), None)
    where = f"one lies in the excluded room {other}" if other else "e.g. " + bad[0].wkt
    raise InputFormatError(f"plan {pid}: frame: {len(bad)} nodes outside rooms ({where})")


def _to_node_frame(frame: Frame, inner: BaseGeometry) -> Callable[[BaseGeometry], BaseGeometry]:
    """Moves geometry into the given nodes' frame: the inverse of what `_pick_frame` applied to
    the nodes to test them (flip_y is its own inverse; origin_shift subtracts the corner)."""
    x0, y0, _, y1 = inner.bounds

    def move(xy: np.ndarray) -> np.ndarray:
        out = xy.copy()
        if frame == "flip_y":
            out[:, 1] = y0 + y1 - xy[:, 1]
        elif frame == "origin_shift":
            out -= [x0, y0]
        return out

    def apply(g: BaseGeometry) -> BaseGeometry:
        if frame == "identity":
            return g
        moved = shapely.transform(g, move)
        return moved if moved.is_valid else shapely.make_valid(moved)

    return apply


def _given_nodes(
    pts: list[tuple[str | None, str | None, float, float]],
    xy_m: np.ndarray,
    rooms: list[Room],
    obstacles: Any,
    pid: Any,
) -> tuple[list[Node], list[str]]:
    """Organiser nodes, at their given (scaled) points, never moved, merged or dropped (slide 2,
    C3). A node's own `room_id`, when given, is authoritative: the node must be inside that
    eligible room or <= NODE_SNAP_M from it, else InputFormatError. Otherwise it goes to the
    eligible room that covers it (the lowest R-id on a shared edge), or to the nearest one if it
    sits <= 10 cm outside.

    A room with none or several gets a warning: legal (decision_conventions §3), but it likely
    means our rooms differ from the organisers' (e.g. a repaired room split in two)."""
    eligible = [r for r in rooms if r.eligible]
    by_id = {r.id: r for r in eligible}
    placed = []
    notes: list[str] = []
    for (nid, rid, x, y), (mx, my) in zip(pts, xy_m, strict=True):
        p = Point(mx, my)
        if rid is not None:
            room = by_id.get(rid)
            if room is None:
                raise InputFormatError(f"plan {pid}: node {nid} names room {rid}, no eligible room")
            if room.polygon.distance(p) > NODE_SNAP_M:
                raise InputFormatError(
                    f"plan {pid}: node {nid} is said to be in {rid} but lies "
                    f"{room.polygon.distance(p):.2f} m outside it"
                )
        else:
            room = next((r for r in eligible if r.polygon.covers(p)), None)
        if room is None or not room.polygon.covers(p):
            if room is None:
                room = min(eligible, key=lambda r: (r.polygon.distance(p), rooms.index(r)))
            notes.append(
                f"given node {nid or (x, y)} is {room.polygon.distance(p) * 100:.1f} cm outside "
                f"{room.id} ({room.label}): assigned to it, not moved"
            )
        placed.append((rooms.index(room), nid, (float(mx), float(my)), room))
    placed.sort(key=lambda t: t[0])  # room order, then input order
    nodes = [
        Node(
            id=nid or f"N{k}",
            room_id=room.id,
            xy=xy,
            clearance_m=_clearance(room.polygon, obstacles, xy),
            given=True,
        )
        for k, (_, nid, xy, room) in enumerate(placed, start=1)
    ]
    ids = [n.id for n in nodes]
    if len(set(ids)) != len(ids):
        raise ValueError(f"plan {pid}: duplicate node ids {sorted(ids)}")
    warnings = notes
    for r in eligible:
        k = sum(n.room_id == r.id for n in nodes)
        if k != 1:
            what = "no given node" if k == 0 else f"{k} given nodes in one room"
            warnings.append(f"{r.id} ({r.label}): {what} (slide 2: one per room)")
    return nodes, warnings


def load_plan(path_or_raw: str | Path | dict, c: Constraints | None = None) -> Plan:
    """The metric `Plan`: rooms with ids, doors, and one fixed node per eligible room (#6).

    Rooms: eligible first (living → bedroom → kitchen → storage, each in polygon order), then the
    excluded ones in `EXCLUDED` order, numbered `R01…` after any sliver folds. `label` is
    `<type>_<i>`, for debug only. Nodes: the organiser's `nodes` when the plan has them
    (`given=True`, never moved, nothing folded); otherwise one per eligible room at the
    max-clearance point of its free space (`N1…` in room order). Clearance is measured to the
    room outline (door gaps included, #46 Q3) and to any wall or window inside the room. Raises
    ValueError naming the plan on unknown geometry keys or on a given node that no eligible room
    covers.
    """
    c = c or Constraints()
    raw = path_or_raw if isinstance(path_or_raw, dict) else read_json(path_or_raw)
    raw, given_ids = normalise(raw)
    layers = read_layers(raw, c)
    pid = raw.get("id")
    if pid is None and raw.get("plan_id") is None:
        raise ValueError("plan has neither plan_id nor id")
    if layers.unknown_keys:
        raise ValueError(f"plan {pid}: unknown geometry keys {layers.unknown_keys}")

    given = raw.get("nodes") is not None
    obstacles = shapely.union_all([*layers.wall, *layers.window])  # what nodes keep clear of
    parts = {t: dict(enumerate(layers.rooms[t])) for t in ELIGIBLE + EXCLUDED}
    folds: list[tuple[int, int, float]] = []
    if not given:
        parts["living"], folds = _fold_slivers(layers.rooms["living"], obstacles)
    ordered = [(t, i, p) for t in ELIGIBLE + EXCLUDED for i, p in sorted(parts[t].items())]
    if given_ids is not None and {f"{t}_{i}" for t, i, _ in ordered} != set(given_ids):
        raise InputFormatError(f"plan {pid}: a room didn't survive repair as one polygon")
    rooms = [
        Room(
            id=given_ids[f"{t}_{i}"] if given_ids is not None else f"R{n:02d}",
            label=f"{t}_{i}",
            type=t,
            eligible=t in ELIGIBLE,
            polygon=p,
            area_m2=p.area,
        )
        for n, (t, i, p) in enumerate(ordered, start=1)
    ]
    room_id = {r.label: r.id for r in rooms}

    inner, walls, windows, front_doors = layers.inner, layers.wall, layers.window, layers.front_door
    door_polys = layers.door
    frame: Frame = "identity"
    if given:
        pts = _given_points(raw["nodes"], pid)
        xy_m = layers.to_metres(np.array([[x, y] for *_, x, y in pts], dtype=float).reshape(-1, 2))
        frame = _pick_frame(xy_m, rooms, inner, pid)
        if frame != "identity":  # move the geometry into the nodes' frame; nodes never move
            move = _to_node_frame(frame, inner)
            rooms = [r.model_copy(update={"polygon": move(r.polygon)}) for r in rooms]
            inner, obstacles = move(inner), move(obstacles)
            walls, windows = [move(p) for p in walls], [move(p) for p in windows]
            front_doors, door_polys = [move(p) for p in front_doors], [move(p) for p in door_polys]
        nodes, warnings = _given_nodes(pts, xy_m, rooms, obstacles, pid)
        if frame != "identity":
            warnings.insert(0, f"frame: {frame} (geometry moved into the given nodes' frame)")
    else:
        nodes, warnings = [], []
        for k, r in enumerate((r for r in rooms if r.eligible), start=1):
            xy, clearance = _node_spot(r.polygon, obstacles)
            nodes.append(Node(id=f"N{k}", room_id=r.id, xy=xy, clearance_m=clearance, given=False))
    room_of = {r.id: r for r in rooms}
    reach = inner.buffer(c.tolerance_m)
    for n in nodes:
        if not reach.covers(Point(n.xy)):
            r = room_of[n.room_id]
            raise InputFormatError(
                f"plan {pid}: node {n.id} of {r.id} ({r.label}) lies outside inner, "
                "so no cable can reach it"
            )
    for r in rooms:
        out = 1 - r.polygon.intersection(inner).area / r.area_m2 if r.area_m2 else 0
        if r.eligible and out > ROOM_OUTSIDE_INNER_WARN:
            warnings.append(f"{r.id} ({r.label}) lies {out:.0%} outside inner")
    warnings += [
        f"{room_of[n.room_id].id} ({room_of[n.room_id].label}): node {n.id} clearance "
        f"{n.clearance_m:.2f} m < {NODE_CLEARANCE_M} m"
        for n in nodes
        if n.clearance_m < NODE_CLEARANCE_M
    ]
    return Plan(
        plan_id=str(raw["plan_id"]) if raw.get("plan_id") is not None else f"PLAN_{pid}",
        m_per_unit=layers.m_per_unit,
        wall_depth_m=layers.wall_depth_m,
        inner=inner,
        rooms=rooms,
        walls=walls,
        windows=windows,
        front_doors=front_doors,
        doors=[Door(id=f"door_{i}", polygon=p) for i, p in enumerate(door_polys)],
        openings=[],
        nodes=nodes,
        warnings=[*layers.infeasible_reasons, *warnings],
        folded_parts=[
            FoldedPart(room_id=f"living_{s}", into_room_id=room_id[f"living_{into}"], area_m2=a)
            for s, into, a in folds
        ],
        frame=frame,
    )


def room_by_label(plan: Plan, label: str) -> Room:
    """The room whose debug label (`<type>_<i>`) matches. Tests and debug output only: never use
    it in the solver or the writer."""
    for r in plan.rooms:
        if r.label == label:
            return r
    raise KeyError(f"plan {plan.plan_id}: no room labelled {label!r}")


def main(argv: list[str] | None = None) -> int:
    """`python -m hackhive.io <plan.json>`: id · m/unit · eligible m² · nodes · LB(5)."""
    for stream in (sys.stdout, sys.stderr):  # "·" and "²" would break cp1252 on Windows
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m hackhive.io", description="Print a plan's facts.")
    p.add_argument("plan", type=Path, help="one plan JSON file")
    args = p.parse_args(argv)
    c = Constraints()
    try:
        raw = read_json(args.plan)
        plan = load_plan(raw, c)
    except (OSError, ValueError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    area = sum(r.area_m2 for r in plan.rooms if r.eligible)
    lb = lower_bound(area, len(plan.nodes), c)
    print(
        f"{raw.get('id', plan.plan_id)} · {plan.m_per_unit:.4f} m/unit · {area:.1f} m² eligible"
        f" · {len(plan.nodes)} nodes · LB {lb}"
    )
    for w in plan.warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
