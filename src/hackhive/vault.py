"""Temporary bridge: read a research-vault fixture into a ``Plan`` and its eligible graph.

The vault files (``tests/fixtures/raw/plan-<id>-<label>.json``) hold the ResPlan plan plus
the room graph the research produced (``strict_graph_edges``). Until #4/#6 (loader) and #9
(room graph) land, zoning (#13) and its CLI read them through here; delete this module and
switch callers to ``io.load_plan`` / ``graph.build_graph`` once those merge.

Rooms keep their vault labels (``<type>_<i>``) as ids so they match the edge list.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import networkx as nx
from shapely.affinity import scale
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid

from hackhive.contracts import Door, Plan, Room

ELIGIBLE = ("living", "bedroom", "kitchen", "storage")
EXCLUDED = ("bathroom", "balcony", "stair", "garden", "pool", "parking", "land")
ZONE_LINKS = ("via_door", "via_opening")  # slide 3 rule 3: windows, walls, entrance don't link


def polygons(g: BaseGeometry | None) -> list[Polygon]:
    """Flatten any geometry to its non-empty polygons."""
    if isinstance(g, Polygon):
        return [] if g.is_empty else [g]
    if isinstance(g, MultiPolygon | GeometryCollection):
        return [p for part in g.geoms for p in polygons(part)]
    return []


def load_vault(path: str | Path) -> tuple[Plan, nx.Graph]:
    """A vault fixture as a metric ``Plan`` and its eligible graph (doors and openings only)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    p = raw["plan"]
    inner = make_valid(shape(p["inner"]))
    s = math.sqrt(p["area"] / inner.area)  # metres per drawing unit (slide 4)

    def metric(key: str) -> list[Polygon]:
        if not p.get(key):
            return []
        return [
            q
            for g in polygons(make_valid(shape(p[key])))
            for q in polygons(scale(g, s, s, origin=(0, 0)))
        ]

    rooms = []
    for kind in ELIGIBLE + EXCLUDED:
        for i, poly in enumerate(metric(kind)):
            label = f"{kind}_{i}"
            rooms.append(
                Room(
                    id=label,
                    label=label,
                    type=kind,
                    eligible=kind in ELIGIBLE,
                    polygon=poly,
                    area_m2=poly.area,
                )
            )
    plan = Plan(
        plan_id=f"PLAN_{p['id']}",
        m_per_unit=s,
        wall_depth_m=p["wall_depth"] * s,
        inner=scale(inner, s, s, origin=(0, 0)),
        rooms=rooms,
        doors=[Door(id=f"door_{i}", polygon=d) for i, d in enumerate(metric("door"))],
        walls=metric("wall"),
        windows=metric("window"),
        front_doors=metric("front_door"),
    )
    eligible = {r.id for r in rooms if r.eligible}
    g = nx.Graph()
    g.add_nodes_from(sorted(eligible))
    for e in raw["strict_graph_edges"]:
        if e["u"] in eligible and e["v"] in eligible and e["type"] in ZONE_LINKS:
            g.add_edge(e["u"], e["v"])
    return plan, g
