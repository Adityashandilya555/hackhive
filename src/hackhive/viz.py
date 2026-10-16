"""Drawing (#5 base layer; #12, #17, #25, #26, #29 and #44 build on it).

Everything here draws onto a `matplotlib.figure.Figure` created directly, never through
`pyplot`, so nothing is left in pyplot's global figure list and the Streamlit app (#7) can
render in its worker threads. Coordinates are drawn as given: metres for a `Plan`, any units
for raw layers. The y axis is never inverted, so the origin is lower-left with y up (slide 8).
"""

from __future__ import annotations

import argparse
import io
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry

from hackhive.contracts import Plan

# --------------------------------------------------------------------------- palette
# Neutral inks and fills (the dataviz reference chrome), so the categorical slots stay free for
# the zone colours of #17. Text always wears ink, never a fill colour.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
WALL = "#3a3936"
ELIGIBLE_FILL = "#f4f3ef"
ROOM_EDGE = "#c3c2b7"
EXCLUDED_FILL = "#dcdbd5"  # slide 5: excluded rooms are grey and get no node
DOOR_FILL = "#f2c14e"  # a coloured gap in the wall
WINDOW_FILL = "#cde2fb"
WINDOW_EDGE = "#256abf"
FRONT_DOOR_FILL = "#8a5a2b"  # slide 5's brown entrance bar

ELIGIBLE = ("living", "bedroom", "kitchen", "storage")
EXCLUDED = ("bathroom", "balcony", "stair", "garden", "pool", "parking", "land")
NOT_DRAWN = ("land",)  # the plot outline covers the whole house; it isn't a room
_STRUCTURE = {
    "wall": "walls",
    "door": "doors",
    "window": "windows",
    "front_door": "front_doors",
}
_FIG_WIDTH_IN = 9.0
_LABEL_PT = 7.5
_MIN_LABEL_PT = 5.0  # not-a-weight: smallest label font size, in points


@dataclass(frozen=True)
class _Room:
    id: str
    type: str
    eligible: bool
    polygon: Polygon
    area_m2: float | None  # None when the units aren't metres


@dataclass(frozen=True)
class _Base:
    rooms: list[_Room]
    walls: list[Polygon]
    doors: list[Polygon]
    windows: list[Polygon]
    front_doors: list[Polygon]


# --------------------------------------------------------------------------- input shapes


def _polygons(geoms: Iterable[Any] | None) -> list[Polygon]:
    out: list[Polygon] = []
    for g in geoms or []:
        if isinstance(g, Polygon):
            if not g.is_empty:
                out.append(g)
        elif isinstance(g, BaseGeometry) and hasattr(g, "geoms"):
            out += _polygons(g.geoms)
    return out


def _base(layers: Plan | Any | Mapping[str, Iterable[Any]]) -> _Base:
    """A `Plan` (metres, R-ids), #4's `io.Layers` (metres, labels) or a dict of geometries."""
    if isinstance(layers, Plan):
        rooms = [
            _Room(r.id, r.type, r.eligible, r.polygon, r.area_m2)
            for r in layers.rooms
            if r.type not in NOT_DRAWN
        ]
        return _Base(
            rooms=rooms,
            walls=_polygons(layers.walls),
            doors=_polygons(d.polygon for d in layers.doors),
            windows=_polygons(layers.windows),
            front_doors=_polygons(layers.front_doors),
        )
    if hasattr(layers, "rooms") and hasattr(layers, "m_per_unit"):  # io.Layers, in metres
        by_type, metric = layers.rooms, True
        structure = {k: getattr(layers, k) for k in _STRUCTURE}
    elif isinstance(layers, Mapping):  # any units
        by_type, metric = {t: layers[t] for t in (*ELIGIBLE, *EXCLUDED) if t in layers}, False
        structure = {k: layers.get(k) for k in _STRUCTURE}
    else:
        raise TypeError(f"can't draw {type(layers).__name__}: pass a Plan, Layers or dict")
    rooms = [
        _Room(f"{t}_{i}", t, t in ELIGIBLE, p, p.area if metric else None)
        for t in (*ELIGIBLE, *EXCLUDED)
        if t not in NOT_DRAWN
        for i, p in enumerate(_polygons(by_type.get(t)))
    ]
    return _Base(rooms=rooms, **{name: _polygons(structure[k]) for k, name in _STRUCTURE.items()})


# --------------------------------------------------------------------------- drawing


def _patch(poly: Polygon, **style: Any) -> PathPatch:
    """A polygon with its holes as one patch (matplotlib's Polygon can't do holes)."""
    rings = [poly.exterior, *poly.interiors]
    path = MplPath.make_compound_path(
        *(MplPath(list(r.coords), closed=True) for r in rings if len(r.coords) >= 3)
    )
    return PathPatch(path, **style)


def _label_point(poly: Polygon) -> tuple[Point, str]:
    """Where a room's label goes: its top-left corner (like slide 5) if that's inside, else a
    point guaranteed to be inside. Returns the point and the horizontal alignment."""
    x0, y0, x1, y1 = poly.bounds
    pad = 0.06 * min(x1 - x0, y1 - y0)
    corner = Point(x0 + pad, y1 - pad)
    if poly.contains(corner):
        return corner, "left"
    return poly.representative_point(), "center"


def _room_label(room: _Room) -> str:
    kind = room.type.replace("_", " ")
    if not room.eligible:
        return f"{kind}\nno node"
    if room.area_m2 is None:
        return f"{room.id}\n{kind}" if not room.id.startswith(room.type) else room.id
    return f"{room.id}\n{kind} · {room.area_m2:.0f} m²"


def draw_layers(layers: Plan | Any | Mapping[str, Iterable[Any]], ax: Axes | None = None) -> Figure:
    """The plan base layer (#5): rooms (eligible light, excluded grey with "no node"), walls
    dark, doors as coloured gaps, windows, the entrance; equal aspect, axes off, y up.

    `layers` is a `Plan` (metres, room ids `R01…`), #4's `io.Layers` or a dict of geometry
    lists keyed by layer / room type (any units; rooms are labelled `<type>_<i>`). Draws into
    `ax` if given, else into a new figure. Raises ValueError if there is nothing to draw.
    """
    base = _base(layers)
    if not base.rooms and not base.walls:
        raise ValueError("nothing to draw: no rooms and no walls")

    if ax is None:
        everything = [r.polygon for r in base.rooms] + base.walls
        x0 = min(g.bounds[0] for g in everything)
        y0 = min(g.bounds[1] for g in everything)
        x1 = max(g.bounds[2] for g in everything)
        y1 = max(g.bounds[3] for g in everything)
        aspect = (y1 - y0) / (x1 - x0) if x1 > x0 else 1.0
        fig = Figure(figsize=(_FIG_WIDTH_IN, max(3.0, min(14.0, _FIG_WIDTH_IN * aspect))))
        FigureCanvasAgg(fig)  # a renderer for measuring labels, without pyplot
        fig.patch.set_facecolor(SURFACE)
        ax = fig.add_subplot()
    fig = ax.figure

    for room in base.rooms:
        fill = ELIGIBLE_FILL if room.eligible else EXCLUDED_FILL
        ax.add_patch(
            _patch(room.polygon, facecolor=fill, edgecolor=ROOM_EDGE, lw=0.6, zorder=1)
        ).set_gid(f"room:{room.id}")
    for wall in base.walls:
        ax.add_patch(_patch(wall, facecolor=WALL, edgecolor=WALL, lw=0.3, zorder=2))
    for window in base.windows:
        ax.add_patch(_patch(window, facecolor=WINDOW_FILL, edgecolor=WINDOW_EDGE, lw=0.6, zorder=3))
    for door in base.doors:
        ax.add_patch(_patch(door, facecolor=DOOR_FILL, edgecolor="none", zorder=3))
    for front in base.front_doors:
        ax.add_patch(_patch(front, facecolor=FRONT_DOOR_FILL, edgecolor="none", zorder=3))
        c = front.centroid
        ax.text(
            c.x,
            c.y,
            "Entrance",
            fontsize=_LABEL_PT - 1,
            color=INK_SECONDARY,
            ha="center",
            va="center",
            zorder=5,
            bbox={"boxstyle": "round,pad=0.15", "fc": SURFACE, "ec": "none", "alpha": 0.85},
        )

    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.set_axis_off()
    ax.set_facecolor(SURFACE)
    ax.apply_aspect()  # final data -> display transform, so labels can be measured

    renderer = _renderer(fig)
    for room in base.rooms:
        p, ha = _label_point(room.polygon)
        text = ax.text(
            p.x,
            p.y,
            _room_label(room),
            fontsize=_LABEL_PT,
            color=INK if room.eligible else INK_SECONDARY,
            ha=ha,
            va="top" if ha == "left" else "center",
            zorder=4,
            clip_on=True,
            linespacing=1.15,
        )
        if renderer is not None:
            _fit(text, room, ax, renderer)
    return fig


def _renderer(fig: Figure) -> Any | None:
    get = getattr(fig.canvas, "get_renderer", None)
    return get() if get is not None else None


def _fit(text: Any, room: _Room, ax: Axes, renderer: Any) -> None:
    """Shrink a room label until it fits inside the room's box; if even the smallest size
    doesn't fit, fall back to the short form (the id, or "no node" for an excluded room)."""
    x0, y0, x1, y1 = room.polygon.bounds
    (dx0, dy0), (dx1, dy1) = ax.transData.transform([(x0, y0), (x1, y1)])
    room_w, room_h = 0.85 * abs(dx1 - dx0), 0.85 * abs(dy1 - dy0)

    def fits() -> bool:
        box = text.get_window_extent(renderer)
        return box.width <= room_w and box.height <= room_h

    for size in (_LABEL_PT, 6.5, 5.5):
        text.set_fontsize(size)
        if fits():
            return
    text.set_text(room.id if room.eligible else "no node")
    text.set_fontsize(_MIN_LABEL_PT)


def to_png(fig: Figure, dpi: int = 150) -> bytes:
    """The figure as PNG bytes. The figure is cleared afterwards so its memory is released."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    fig.clear()
    return buf.getvalue()


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    """`python -m hackhive.viz <plan.json> <out.png>`: draw a plan's base layer."""
    from hackhive.io import load_plan

    p = argparse.ArgumentParser(prog="python -m hackhive.viz", description=main.__doc__)
    p.add_argument("plan", type=Path, help="one plan JSON file")
    p.add_argument("out", type=Path, help="where to write the PNG")
    args = p.parse_args(argv)
    try:
        plan = load_plan(args.plan)
    except (OSError, ValueError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(to_png(draw_layers(plan)))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
