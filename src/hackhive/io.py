"""Read one plan JSON into metric layers (#4): repair, flatten, scale to metres once per plan.

Slide 4: metres = drawing units ÷ √(inner.area / area). `net_area` is never read (it is 0 in 32%
of ResPlan plans). Nodes, ids and the `Plan` model are #6; doors-to-rooms and open passages are #9.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    shape,
)

from hackhive.contracts import AREA_EPS_M2, Constraints

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


def read_json(path: str | Path) -> dict:
    """One ResPlan plan file. The organiser adapter (#38) plugs in here."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


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


def _positive(plan: dict, key: str) -> float:
    """plan[key] as a finite number > 0, else ValueError naming the plan."""
    v = plan.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
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
    )
