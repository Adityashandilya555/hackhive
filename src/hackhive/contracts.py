"""Data contracts: the slide-8 submission, our sidecar, the input-side plan models and settings.

This module is the single source of truth for every shape that crosses a stage boundary.
`python -m hackhive.contracts` writes the JSON schemas; `--check` fails if they are stale.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.dataclasses import dataclass
from shapely.geometry import MultiPolygon, Polygon

# --------------------------------------------------------------------------- settings


@dataclass(frozen=True)
class CostWeights:
    """Weights of J = length·L + cross·X + overlap·O + bend·B."""

    length: float
    cross: float
    overlap: float
    bend: float


# slide 6 starting values; update when the organisers publish final weights (#48, #46 Q7)
ORGANISER_WEIGHTS = CostWeights(1.0, 5.0, 1.0, 0.5)


class Constraints(BaseModel):
    """Every tunable limit. The defaults are the organisers' problem statement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_area_m2: float = Field(100, gt=0)
    max_nodes: int = Field(5, gt=0)
    hub_wall_clearance_m: float = Field(0.5, ge=0)
    tolerance_m: float = Field(0.001, gt=0)
    grid_m: float = Field(0.10, gt=0)
    flip_y: bool = False
    origin_shift: tuple[float, float] = (0.0, 0.0)
    interior_windows_as_doors: bool = False
    zone_links_via_excluded: bool = False
    cost_weights: CostWeights = ORGANISER_WEIGHTS

    @classmethod
    def problem_statement(cls) -> Constraints:
        return cls()

    @property
    def is_problem_statement(self) -> bool:
        return self == Constraints.problem_statement()


# Summed polygon areas carry float noise (100 m² can come out as 100.00000000001). Every
# capacity comparison (here, #11 rule 2, #13 zoning) subtracts this before ceil / after ≤.
AREA_EPS_M2 = 1e-6


def lower_bound(area_m2: float, n_nodes: int, c: Constraints) -> int:
    """Fewest hubs any zoning can use: max(⌈area / cap_area⌉, ⌈nodes / cap_nodes⌉) (C7)."""
    by_area = math.ceil((area_m2 - AREA_EPS_M2) / c.max_area_m2)
    return max(by_area, math.ceil(n_nodes / c.max_nodes), 0)


# --------------------------------------------------------------------------- submission (slide 8)


class _Strict(BaseModel):
    # serialize_by_alias: every dump writes slide 8's "from", even without by_alias=True
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class Location(_Strict):
    x: float
    y: float


class RouteSegment(_Strict):
    from_: str = Field(alias="from")
    to: str
    length_m: float
    polyline: list[tuple[float, float]]


class HubOut(_Strict):
    hub_id: str
    room_id: str
    location: Location
    assigned_rooms: list[str]
    assigned_nodes: list[str]
    closed_sequence: list[str]
    route_segments: list[RouteSegment]
    ring_length_m: float


class ReportedMetrics(_Strict):
    hub_count: int
    total_length_m: float


class Submission(_Strict):
    """Slide 8's output, key for key. Metres, origin lower-left, y up."""

    plan_id: str
    units: Literal["m"]
    hubs: list[HubOut]
    reported_metrics: ReportedMetrics


# --------------------------------------------------------------------------- sidecar (ours)


class Violation(_Strict):
    rule: int
    severity: Literal["error", "strict"]
    message: str
    ids: list[str] = []
    hub_id: str | None = None
    where: list[tuple[float, float]] = []  # metres; the overlay draws these points in red


class Bounds(_Strict):
    k_lb: int
    k_found: int | None = None
    # lb_met: k_found == k_lb, optimal without search; exhaustive: proven by full enumeration
    proof: Literal["exhaustive", "lb_met", "greedy", "none"] = "none"


class CostBreakdown(_Strict):
    H: int
    L: float
    X: int
    O: float  # noqa: E741
    B: int
    J: float
    area_gap: float


class HubMetrics(_Strict):
    """One hub's share of the metrics, for the per-hub summary (#17, #44). Slide 8 has no room."""

    hub_id: str
    area_m2: float
    n_nodes: int
    L: float
    X: int
    O: float  # noqa: E741
    B: int


class Provenance(_Strict):
    input_path: str | None = None
    input_digest: str | None = None
    version: str | None = None


class FoldedPart(_Strict):
    room_id: str
    into_room_id: str
    area_m2: float


class Sidecar(_Strict):
    """Everything that is not part of the submission."""

    plan_id: str
    status: Literal["ok", "submitted_strict_only", "infeasible", "input_error"]
    reason: str | None = None  # why there is no submission (infeasible, input_error), in words
    violations: list[Violation] = []
    bounds: Bounds | None = None
    cost: CostBreakdown | None = None
    hubs: list[HubMetrics] = []
    settings: Constraints = Constraints()
    provenance: Provenance = Provenance()
    warnings: list[str] = []
    folded_parts: list[FoldedPart] = []


# --------------------------------------------------------------------------- input side (in metres)


class _Geo(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")


class Room(_Geo):
    id: str
    label: str
    type: str
    eligible: bool
    polygon: Polygon
    area_m2: float


class Door(_Geo):
    id: str
    polygon: Polygon
    rooms: tuple[str, str] | None = None


class Opening(_Geo):
    """An open passage: a shared room edge with no wall."""

    id: str
    polygon: Polygon
    rooms: tuple[str, str] | None = None


class Node(_Geo):
    id: str
    room_id: str
    xy: tuple[float, float]
    clearance_m: float | None = None
    given: bool = False


class Plan(_Geo):
    plan_id: str
    m_per_unit: float
    wall_depth_m: float
    inner: Polygon | MultiPolygon  # ResPlan inners can be multi-part (e.g. plan 1293)
    rooms: list[Room]
    walls: list[Polygon]  # obstacles for the grid (#10); doors are cut out of them
    windows: list[Polygon] = []
    front_doors: list[Polygon] = []  # the entrance: never a room-to-room passage
    doors: list[Door] = []
    openings: list[Opening] = []
    nodes: list[Node] = []
    warnings: list[str] = []
    folded_parts: list[FoldedPart] = []
    # The organiser's node frame (#38): the geometry was moved into it so given nodes never move.
    frame: Literal["identity", "flip_y", "origin_shift"] = "identity"


# --------------------------------------------------------------------------- schema export

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
SCHEMAS: dict[str, type[BaseModel]] = {
    "submission.schema.json": Submission,
    "sidecar.schema.json": Sidecar,
}


def render_schemas() -> dict[str, str]:
    out = {}
    for name, model in SCHEMAS.items():
        schema: dict[str, Any] = model.model_json_schema(by_alias=True)
        out[name] = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m hackhive.contracts")
    p.add_argument("--check", action="store_true", help="fail if the committed schemas are stale")
    args = p.parse_args(argv)
    rendered = render_schemas()
    if args.check:
        stale = [
            n
            for n, text in rendered.items()
            if not (SCHEMA_DIR / n).exists() or (SCHEMA_DIR / n).read_text(encoding="utf-8") != text
        ]
        if stale:
            print(f"stale schemas: {', '.join(stale)}; run python -m hackhive.contracts")
            return 1
        print("schemas up to date")
        return 0
    SCHEMA_DIR.mkdir(exist_ok=True)
    for n, text in rendered.items():
        (SCHEMA_DIR / n).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote schemas/{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
