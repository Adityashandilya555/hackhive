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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as MplPath
from matplotlib.transforms import Bbox
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from hackhive import checker
from hackhive.contracts import Plan, Room, Submission, Violation
from hackhive.io import get_geometries

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
# The room-graph view (#12): door links blue, open passages green and dashed, so the two kinds
# never rely on hue alone. Only shown with the toggle, never together with the zone colours.
LINK_DOOR = "#2a78d6"
LINK_OPENING = "#008300"
LINK_OTHER = INK_SECONDARY  # via an excluded room (a what-if setting only)
# The solution overlay (#17). Zones take the validated categorical slots 1-7 in fixed order (the
# dataviz reference palette: every hard check passes; three slots sit below 3:1 contrast, so each
# zone also carries its hub id and a summary row). Slot 8, red, is left out on purpose: red is
# reserved for violations (the status palette), so a zone can never look like an error. Past seven
# hubs the colours repeat with a hatch, never as new hues.
ZONE_COLOURS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6")
ZONE_ALPHA = 0.22
STATUS_CRITICAL = "#d03b3b"  # an `error` violation
STATUS_SERIOUS = "#ec835a"  # a `strict`-only violation
RING_LW = 2.0

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
_CELL_MID = 0.5  # not-a-weight: the middle of a sampling cell


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
    nodes: dict[str, tuple[float, float]] = field(default_factory=dict)  # room id -> node point


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
            nodes={n.room_id: n.xy for n in layers.nodes},
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
            gid="entrance",
            bbox={"boxstyle": "round,pad=0.15", "fc": SURFACE, "ec": "none", "alpha": 0.85},
        )

    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.set_axis_off()
    ax.set_facecolor(SURFACE)
    ax.apply_aspect()  # final data -> display transform, so labels can be measured
    ax.set_autoscale_on(False)  # overlays (nodes, graph, rings) must not move the frame

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
            gid=f"label:{room.id}",
        )
        if renderer is not None:
            _fit(text, room, ax, renderer)
            if room.id in base.nodes:
                _off_node(text, room, base.nodes[room.id], ax, renderer)
    return fig


def _inner_points(poly: Polygon, first: Point | None = None, steps: int = 9) -> list[Point]:
    """A steps × steps sample of points inside the polygon (plus `first`), deepest first: the
    candidates for anything that must move to a clear spot in a room."""
    x0, y0, x1, y1 = poly.bounds
    grid = [
        Point(x0 + (x1 - x0) * (i + _CELL_MID) / steps, y0 + (y1 - y0) * (j + _CELL_MID) / steps)
        for i in range(steps)
        for j in range(steps)
    ]
    inside = [p for p in [*([first] if first is not None else []), *grid] if poly.contains(p)]
    return sorted(inside, key=lambda p: -poly.exterior.distance(p))


def _off_node(text: Any, room: _Room, xy: tuple[float, float], ax: Axes, renderer: Any) -> None:
    """Move a room label that covers its room's node to the deepest spot where it covers it no
    more and still sits inside the room's box; leave it where it is if there is none."""
    nx_, ny_ = ax.transData.transform(xy)
    x0, y0, x1, y1 = room.polygon.bounds
    (bx0, by0), (bx1, by1) = ax.transData.transform([(x0, y0), (x1, y1)])

    def ok() -> bool:
        box = text.get_window_extent(renderer)
        inside = box.x0 >= bx0 and box.x1 <= bx1 and box.y0 >= by0 and box.y1 <= by1
        return inside and not box.padded(4).contains(nx_, ny_)

    if not text.get_window_extent(renderer).padded(4).contains(nx_, ny_):
        return
    before = (text.get_position(), text.get_ha(), text.get_va())
    text.set_ha("center")
    text.set_va("center")
    for p in _inner_points(room.polygon):
        text.set_position((p.x, p.y))
        if ok():
            return
    pos, ha, va = before
    text.set_position(pos)
    text.set_ha(ha)
    text.set_va(va)


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


# Where a node's id may sit around its dot (offset in points, alignment), tried in this order.
_NODE_LABEL_SPOTS = (
    ((5, 4), "left", "bottom"),
    ((5, -4), "left", "top"),
    ((-5, 4), "right", "bottom"),
    ((-5, -4), "right", "top"),
    ((0, 8), "center", "bottom"),
    ((0, -8), "center", "top"),
    ((14, 0), "left", "center"),
    ((-14, 0), "right", "center"),
    ((0, 20), "center", "bottom"),
    ((0, -20), "center", "top"),
)


def _avoid(ax: Axes, renderer: Any) -> list[Any]:
    """Display boxes overlays must keep clear of: room labels, the "Entrance" tag, node and hub
    markers."""
    if renderer is None:
        return []
    texts = [
        t.get_window_extent(renderer)
        for t in ax.texts
        if (t.get_gid() or "").startswith("label:") or t.get_gid() == "entrance"
    ]
    dots = [
        ln.get_window_extent(renderer)
        for ln in ax.lines
        if (ln.get_gid() or "").startswith(("node:", "hub:"))
    ]
    return texts + dots


def draw_nodes(plan: Plan, ax: Axes) -> None:
    """Every node as an ink dot with its id (N1…), on top of the base layer (#12). Each id goes
    to the first spot around its dot that covers no room label, the "Entrance" tag, any node
    dot or another id."""
    for n in plan.nodes:
        ax.plot(
            [n.xy[0]],
            [n.xy[1]],
            marker="o",
            ms=6,
            mfc=INK,
            mec=SURFACE,
            mew=1.2,
            ls="none",
            zorder=7,
            gid=f"node:{n.id}",
        )
    renderer = _renderer(ax.figure)
    taken = _avoid(ax, renderer)
    for n in plan.nodes:
        for k, (offset, ha, va) in enumerate(_NODE_LABEL_SPOTS):
            label = ax.annotate(
                n.id,
                n.xy,
                xytext=offset,
                textcoords="offset points",
                ha=ha,
                va=va,
                fontsize=_LABEL_PT,
                fontweight="bold",
                color=INK,
                zorder=7,
                gid=f"node-label:{n.id}",
                bbox={"boxstyle": "round,pad=0.12", "fc": SURFACE, "ec": "none", "alpha": 0.85},
            )
            if renderer is None:
                break
            box = label.get_window_extent(renderer)
            if k == len(_NODE_LABEL_SPOTS) - 1 or not any(box.overlaps(t) for t in taken):
                taken.append(box)
                break
            label.remove()


def _anchor(geom: BaseGeometry) -> Point:
    """A point on a door or passage: its centroid when that's inside it, else a point that is."""
    c = geom.centroid
    return c if geom.contains(c) else geom.representative_point()


def _clear_point(room: Room, ax: Axes, renderer: Any) -> Point:
    """A point well inside the room that isn't under its own label: the room's label falls
    back to `representative_point()` in odd shapes, so the hollow point can't just use it."""
    poly = room.polygon
    fallback = poly.representative_point()
    label = next(
        (t for t in ax.texts if t.get_gid() == f"label:{room.id}"),
        None,
    )
    if renderer is None or label is None:
        return fallback
    box = label.get_window_extent(renderer).padded(4)
    inside = _inner_points(poly, first=fallback)
    for p in inside:
        if not box.contains(*ax.transData.transform((p.x, p.y))):
            return p
    return fallback


_KNOWN_LINKS = {
    "door": ({"color": LINK_DOOR, "ls": "-"}, "through a door"),
    "opening": ({"color": LINK_OPENING, "ls": "--"}, "open passage"),
    "via_excluded": ({"color": LINK_OTHER, "ls": ":"}, "via an excluded room"),
}


def draw_graph(plan: Plan, G: Any, ax: Axes) -> None:
    """The room graph (#9) between node points: one line per door, routed through the door's
    gap (blue), one per open passage, through the passage (green, dashed), and `via_excluded`
    links dotted grey. A room with no node (excluded) is reached at a hollow point inside it,
    clear of its label. Any other edge kind is drawn dash-dotted and named in the legend, never
    silently. Adds a "Room graph" legend."""
    node_at = {n.room_id: Point(n.xy) for n in plan.nodes}
    rooms = {r.id: r for r in plan.rooms}
    doors = {d.id: d.polygon for d in plan.doors}
    renderer = _renderer(ax.figure)
    hollow = {
        r: _clear_point(rooms[r], ax, renderer)
        for e in G.edges
        for r in e
        if r not in node_at and r in rooms
    }

    def at(room_id: str) -> Point:
        return node_at.get(room_id) or hollow.get(room_id) or rooms[room_id].polygon.centroid

    seen: dict[str, tuple[dict[str, Any], str]] = {}
    for u, v, d in G.edges(data=True):
        kind = str(d.get("kind"))
        style, name = _KNOWN_LINKS.get(kind, ({"color": LINK_OTHER, "ls": "-."}, kind))
        seen[kind] = (style, name)
        if kind == "door":
            vias = [(i, _anchor(doors[i])) for i in d.get("door_ids", []) if i in doors]
        elif kind == "opening":
            vias = [("opening", _anchor(d["region"]))]
        elif kind == "via_excluded":
            vias = [("via", None)]
        else:
            vias = [(kind, None)]
        for tag, via in vias:
            pts = [at(u), *([via] if via is not None else []), at(v)]
            ax.plot(
                [p.x for p in pts],
                [p.y for p in pts],
                lw=1.6,
                zorder=6,
                solid_capstyle="round",
                gid=f"edge:{u}-{v}:{tag}",
                **style,
            )
    for r, p in sorted(hollow.items()):
        ax.plot(
            [p.x],
            [p.y],
            marker="o",
            ms=5,
            mfc=SURFACE,
            mec=INK_SECONDARY,
            ls="none",
            zorder=7,
            gid=f"hollow:{r}",
        )

    order = [*_KNOWN_LINKS, *sorted(k for k in seen if k not in _KNOWN_LINKS)]
    shown = [Line2D([], [], lw=1.6, label=seen[k][1], **seen[k][0]) for k in order if k in seen]
    if hollow:
        shown.append(
            Line2D(
                [], [], marker="o", ms=5, mfc=SURFACE, mec=INK_SECONDARY, ls="none", label="no node"
            )
        )
    if not shown:
        return
    ax.legend(
        handles=shown,
        title="Room graph",
        loc="upper center",
        bbox_to_anchor=(0.5, 0.0),  # not-a-weight: centred under the axes
        ncol=len(shown),
        frameon=False,
        fontsize=_LABEL_PT,
        title_fontsize=_LABEL_PT,
        labelcolor=INK_SECONDARY,
    )


def _place_label(
    ax: Axes, xy: tuple[float, float], text: str, taken: list[Any], renderer: Any, **style: Any
) -> Any:
    """`text` next to `xy`, at the first of `_NODE_LABEL_SPOTS` that overlaps nothing in `taken`
    (the last spot if all do); its box is added to `taken`."""
    for k, (offset, ha, va) in enumerate(_NODE_LABEL_SPOTS):
        label = ax.annotate(
            text, xy, xytext=offset, textcoords="offset points", ha=ha, va=va, **style
        )
        if renderer is None:
            return label
        box = label.get_window_extent(renderer)
        if k == len(_NODE_LABEL_SPOTS) - 1 or not any(box.overlaps(t) for t in taken):
            taken.append(box)
            return label
        label.remove()
    raise AssertionError("unreachable")


def _zone_style(i: int) -> tuple[str, str | None]:
    """Hub i's colour, and a hatch once the seven slots are used up (never a new hue)."""
    return ZONE_COLOURS[i % len(ZONE_COLOURS)], (None if i < len(ZONE_COLOURS) else "//")


# where along a leg its number may sit, tried in this order (fractions of the leg's length)
_MIDWAY = 0.5  # not-a-weight: half-way along a leg
_ORDER_SPOTS = (_MIDWAY, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.12, 0.88)
_ORDER_R = 9  # px: half the side of a number's circle, for the overlap test
_MARK_R = 7  # px: half the side of a ✗ violation marker's box


def segment_count(solution: Submission) -> int:
    """How many steps `draw_solution`'s step-through has: every ring's segments (#25)."""
    return sum(len(h.route_segments) for h in solution.hubs)


def draw_solution(
    plan: Plan,
    solution: Submission,
    violations: Sequence[Violation] = (),
    step: int | None = None,
    ax: Axes | None = None,
) -> Figure:
    """The slide-5 overlay (#17) on the base layer:

    - each hub's rooms tinted in its zone colour; its hub as a square labelled H1…;
    - every node with its id; each ring in its zone colour, drawn from the **submitted polylines
      exactly** (no offset lanes), with its visit order circled on each leg, the last number on
      the return leg;
    - a same-line stretch that k ≥ 2 legs share (`checker.overlap_stretches`, the stretches O
      counts) drawn once, dashed, marked ×k; real parallel runs are just two lines;
    - every violation in red (orange when only `strict`) at its `where`, labelled with its rule,
      and a hub-point violation turns that hub's square red;
    - a "How to read" legend with only what is drawn.

    `step` (#25's step-through) counts segments across every ring, in hub order then visit
    order: step 0 shows only the hubs, nodes and zones; each step adds one segment and
    highlights it (labelled "↩ back to H1" when it closes a ring); step N, or None, is the full
    overlay. Violations belong to the whole design, so they appear only at the full step.
    """
    fig = draw_layers(plan, ax)
    ax = fig.axes[0] if ax is None else ax
    renderer = _renderer(fig)
    rooms = {r.id: r for r in plan.rooms}
    hubs = list(solution.hubs)
    colour = {h.hub_id: _zone_style(i) for i, h in enumerate(hubs)}
    order = [(h.hub_id, j, s) for h in hubs for j, s in enumerate(h.route_segments, start=1)]
    full = step is None or step >= len(order)
    shown = order if full else order[: max(step, 0)]
    newest = None if full or not shown else shown[-1]
    if not full:  # a violation is about the whole design: none until every segment is drawn
        violations = ()

    # zones
    for h in hubs:
        c, hatch = colour[h.hub_id]
        for rid in dict.fromkeys(h.assigned_rooms):
            if rid in rooms:
                ax.add_patch(
                    _patch(
                        rooms[rid].polygon,
                        facecolor=c,
                        alpha=ZONE_ALPHA,
                        edgecolor="none",
                        hatch=hatch,
                        zorder=1.5,
                        gid=f"zone:{h.hub_id}:{rid}",
                    )
                )

    # legs, and the same-line stretches they share
    legs: list[tuple[str, int, list[tuple[float, float]]]] = []
    for hub_id, j, s in shown:
        pts = checker.clean_polyline(s.polyline)
        if len(pts) >= 2:
            legs.append((hub_id, j, pts))
    if newest is not None:  # the step's new segment: a wide halo under it, same coordinates
        hub_id, j, seg = newest
        pts = checker.clean_polyline(seg.polyline)
        ax.plot(
            [p[0] for p in pts],
            [p[1] for p in pts],
            color=colour[hub_id][0],
            lw=RING_LW * 4,
            alpha=0.3,
            solid_capstyle="round",
            zorder=4.8,
            gid=f"current:{hub_id}:{j}",
        )
    stretches = checker.overlap_stretches([p for _, _, p in legs], [o for o, _, _ in legs])
    tol = checker.TOL
    cut = unary_union([LineString(st.ends).buffer(tol, cap_style="flat") for st in stretches])
    for hub_id, j, pts in legs:
        line = LineString(pts)
        rest = line.difference(cut) if stretches else line
        for piece in get_geometries(rest):
            if isinstance(piece, LineString) and piece.length > 2 * tol:
                xs, ys = piece.xy
                ax.plot(
                    xs,
                    ys,
                    color=colour[hub_id][0],
                    lw=RING_LW,
                    solid_capstyle="round",
                    zorder=5,
                    gid=f"ring:{hub_id}:{j}",
                )
    for i, st in enumerate(stretches):
        (x0, y0), (x1, y1) = st.ends
        ax.plot(
            [x0, x1],
            [y0, y1],
            color=colour[st.owners[0]][0] if len(st.owners) == 1 else INK,
            lw=RING_LW,
            ls="--",
            zorder=5,
            gid=f"overlap:{i}",
        )
        ax.annotate(
            f"×{st.k}",
            ((x0 + x1) / 2, (y0 + y1) / 2),
            xytext=(0, 6),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=_LABEL_PT - 1,
            color=INK,
            zorder=8,
            gid=f"overlap-label:{i}",
            bbox={"boxstyle": "round,pad=0.12", "fc": SURFACE, "ec": "none", "alpha": 0.9},
        )

    # hubs (red when the hub point itself breaks rule 6), then nodes, then the hub ids
    bad_hub = {
        v.hub_id for v in violations if v.rule == checker.HUB_POINT and v.severity == "error"
    }
    for h in hubs:
        red = h.hub_id in bad_hub
        ax.plot(
            [h.location.x],
            [h.location.y],
            marker="s",
            ms=10,
            mfc=STATUS_CRITICAL if red else colour[h.hub_id][0],
            mec=STATUS_CRITICAL if red else INK,
            mew=1.4,
            ls="none",
            zorder=9,
            gid=f"hub:{h.hub_id}",
        )
    draw_nodes(plan, ax)
    # one list of everything drawn so far that later labels and numbers must keep clear of;
    # every annotation below appends to it in draw order
    taken = _avoid(ax, renderer) + [
        t.get_window_extent(renderer)
        for t in ax.texts
        if renderer is not None
        and (t.get_gid() or "").startswith(("node-label:", "overlap-label:"))
    ]
    for h in hubs:
        _place_label(
            ax,
            (h.location.x, h.location.y),
            h.hub_id,
            taken,
            renderer,
            fontsize=_LABEL_PT + 1,
            fontweight="bold",
            color=INK,
            zorder=9,
            gid=f"hub-label:{h.hub_id}",
            bbox={"boxstyle": "round,pad=0.15", "fc": SURFACE, "ec": "none", "alpha": 0.9},
        )

    # the step closed a ring: say so at its hub
    if newest is not None and newest[2].to == newest[0]:
        h = next(h for h in hubs if h.hub_id == newest[0])
        _place_label(
            ax,
            (h.location.x, h.location.y),
            f"↩ back to {h.hub_id}",
            taken,
            renderer,
            fontsize=_LABEL_PT,
            fontweight="bold",
            color=INK,
            zorder=9,
            gid=f"return-label:{h.hub_id}",
            bbox={
                "boxstyle": "round,pad=0.2",
                "fc": SURFACE,
                "ec": colour[h.hub_id][0],
                "lw": 1.2,
            },
        )

    # violations, at their `where`, before the numbers so the numbers move clear of them
    labelled: dict[tuple[float, float], list[Violation]] = {}
    for i, v in enumerate(violations):
        pts = [tuple(p) for p in v.where]
        if not pts and v.hub_id:
            hub = next((h for h in hubs if h.hub_id == v.hub_id), None)
            pts = [(hub.location.x, hub.location.y)] if hub else []
        if not pts:
            continue
        red = STATUS_CRITICAL if v.severity == "error" else STATUS_SERIOUS
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        ax.plot(
            xs,
            ys,
            color=red,
            marker="X",
            ms=8,
            mec=SURFACE,
            mew=0.8,
            lw=1.6,
            ls=":" if len(pts) == 2 else "none",
            zorder=11,
            gid=f"violation:{i}",
        )
        for x, y in pts:  # each ✗ marker (not the dotted line between two of them)
            dx, dy = ax.transData.transform((x, y))
            taken.append(Bbox.from_extents(dx - _MARK_R, dy - _MARK_R, dx + _MARK_R, dy + _MARK_R))
        labelled.setdefault((round(pts[0][0], 2), round(pts[0][1], 2)), []).append(v)
    for i, (xy, vs) in enumerate(labelled.items()):
        errors = [v for v in vs if v.severity == "error"]
        rules = sorted({v.rule for v in vs})
        mark = "✗" if errors else "⚠"
        word = "rule" if len(rules) == 1 else "rules"
        _place_label(
            ax,
            xy,
            f"{mark} {word} {', '.join(map(str, rules))}",
            taken,
            renderer,
            fontsize=_LABEL_PT,
            fontweight="bold",
            color=STATUS_CRITICAL if errors else STATUS_SERIOUS,
            zorder=12,
            gid=f"violation:{i}:label",
            bbox={"boxstyle": "round,pad=0.15", "fc": SURFACE, "ec": "none", "alpha": 0.9},
        )

    # visit order: one circled number per leg, at the first spot along it clear of every label
    # and of the numbers already placed (a circle's box is about 2 × _ORDER_R px)
    for hub_id, j, pts in legs:
        line = LineString(pts)
        best = line.interpolate(_MIDWAY, normalized=True)
        for f in _ORDER_SPOTS:
            p = line.interpolate(f, normalized=True)
            dx, dy = ax.transData.transform((p.x, p.y))
            box = Bbox.from_extents(dx - _ORDER_R, dy - _ORDER_R, dx + _ORDER_R, dy + _ORDER_R)
            if not any(box.overlaps(t) for t in taken):
                best = p
                break
        mark = ax.annotate(
            str(j),
            (best.x, best.y),
            ha="center",
            va="center",
            fontsize=_LABEL_PT - 1,
            fontweight="bold",
            color=INK,
            zorder=10,
            gid=f"order:{hub_id}:{j}",
            bbox={"boxstyle": "circle,pad=0.25", "fc": SURFACE, "ec": colour[hub_id][0], "lw": 1.3},
        )
        if renderer is not None:
            taken.append(mark.get_window_extent(renderer))

    # how to read it: only what is drawn
    first = ZONE_COLOURS[0]
    handles: list[Any] = [
        Patch(
            facecolor=first, alpha=ZONE_ALPHA, edgecolor="none", label="zone · one colour per hub"
        ),
        Line2D([], [], marker="s", ms=8, mfc=first, mec=INK, ls="none", label="hub H1…"),
        Line2D([], [], marker="o", ms=5, mfc=INK, mec=SURFACE, ls="none", label="node N1…"),
    ]
    if legs:
        handles += [
            Line2D([], [], color=first, lw=RING_LW, label="ring, in its zone's colour"),
            Line2D(
                [],
                [],
                marker="o",
                ms=8,
                mfc=SURFACE,
                mec=first,
                ls="none",
                label="visit order (last = the return leg)",
            ),
        ]
    if newest is not None:
        handles.append(
            Line2D([], [], color=first, lw=RING_LW * 4, alpha=0.3, label="this step's segment")
        )
    if stretches:
        handles.append(
            Line2D([], [], color=INK, lw=RING_LW, ls="--", label="same line, ×k legs (overlap)")
        )
    if any(v.severity == "error" for v in violations):
        handles.append(
            Line2D(
                [],
                [],
                marker="X",
                ms=8,
                color=STATUS_CRITICAL,
                ls="none",
                label="✗ violation (rule n)",
            )
        )
    if any(v.severity == "strict" for v in violations):
        handles.append(
            Line2D(
                [],
                [],
                marker="X",
                ms=8,
                color=STATUS_SERIOUS,
                ls="none",
                label="⚠ strict-only (rule n)",
            )
        )
    ax.legend(
        handles=handles,
        title="How to read",
        loc="upper center",
        bbox_to_anchor=(0.5, 0.0),  # not-a-weight: centred under the axes
        ncol=3,
        frameon=False,
        fontsize=_LABEL_PT,
        title_fontsize=_LABEL_PT + 1,
        labelcolor=INK_SECONDARY,
    )
    return fig


WHAT_IF_GID = "what-if-label"


def label_what_if(fig: Figure, changes: Sequence[str]) -> Figure:
    """A red WHAT-IF band above the drawing naming every change from the problem statement (#30),
    so a what-if picture can't pass for a submitted design. Nothing for the preset (no changes).
    The text is white on red, and the word WHAT-IF carries it, never the colour alone."""
    if changes:
        ax = fig.axes[0]  # on the drawing's own top edge: the figure's has empty space above it
        ax.text(
            _MIDWAY,  # the drawing's horizontal centre
            1.01,
            "WHAT-IF: " + "; ".join(changes) + " · never submitted",
            ha="center",
            va="bottom",
            fontsize=_LABEL_PT + 1,
            fontweight="bold",
            color=SURFACE,
            gid=WHAT_IF_GID,
            bbox={"boxstyle": "square,pad=0.4", "fc": STATUS_CRITICAL, "ec": "none"},
            transform=ax.transAxes,
            zorder=20,
            clip_on=False,
        )
    return fig


def to_png(fig: Figure, dpi: int = 150) -> bytes:
    """The figure as PNG bytes. The figure is cleared afterwards so its memory is released."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    fig.clear()
    return buf.getvalue()


# --------------------------------------------------------------------------- presentation (#44)

_SLIDE_IN = (16.0, 9.0)  # slide 8's 16:9 frame, in inches
_SLIDE_DPI = 200


def hub_count_badge(bounds: Any) -> str:
    """`6 hubs, proven minimal against a lower bound of 2`: H against the lower bound, from the
    sidecar's `bounds` (#22's proof). #24's badge wording can replace this text."""
    h, lb = bounds.k_found, bounds.k_lb
    hubs = f"{h} hub{'' if h == 1 else 's'}"
    if h == lb:
        return f"{hubs}, proven minimal: equal to the lower bound"
    if bounds.proof == "exhaustive":
        return f"{hubs}, proven minimal against a lower bound of {lb}"
    return f"{hubs} against a lower bound of {lb}"


def draw_presentation(plan: Plan, result: Any) -> Figure:
    """The slide-8 presentation image (#44), 16:9: #17's overlay on the left; on the right the
    per-hub summary, the hub-count badge, the J breakdown and the assigned-area gap. `result` is
    a `solve.Result` with a submission; every number is recomputed by the checker mirror."""
    from hackhive.ui import hub_metrics  # ui imports solve, which viz must not load at import

    sub, side = result.submission, result.sidecar
    c = side.settings
    m = checker.metrics(sub.hubs, c)
    rows = hub_metrics(plan, sub, m)
    fig = Figure(figsize=_SLIDE_IN, facecolor=SURFACE)
    FigureCanvasAgg(fig)
    left, right = fig.subplots(1, 2, width_ratios=[3, 2])
    draw_solution(plan, sub, violations=side.violations, ax=left)
    if result.what_if:
        from hackhive.what_if import what_if_changes

        label_what_if(fig, what_if_changes(c))
    right.set_axis_off()
    right.text(0, 1, side.plan_id, fontsize=18, fontweight="bold", color=INK, va="top")
    right.text(
        0,
        0.93,
        hub_count_badge(side.bounds),
        fontsize=12,
        color=INK,
        va="top",
        gid="presentation:badge",
    )
    table = right.table(
        cellText=[
            [
                h.hub_id,
                ", ".join(dict.fromkeys(h.assigned_rooms)),
                f"{r.area_m2:.1f}",
                str(r.n_nodes),
                f"{r.L:.2f}",
                str(r.B),
            ]
            for r, h in zip(rows, sub.hubs, strict=True)
        ],
        colLabels=["Hub", "Rooms", "m²", "Nodes", "Ring (m)", "Bends"],
        colWidths=[0.09, 0.37, 0.11, 0.12, 0.16, 0.12],
        bbox=[0, 0.4, 1, 0.45],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    w = c.cost_weights
    right.text(
        0,
        0.33,
        f"J = {w.length:g}·L + {w.cross:g}·X + {w.overlap:g}·O + {w.bend:g}·B\n"
        f"L {m.L:.3f} m · X {m.X} · O {m.O:.3f} m · B {m.B}\nJ {m.J:.3f}",
        fontsize=12,
        color=INK,
        va="top",
        linespacing=1.6,
        gid="presentation:J",
    )
    gap = checker.area_gap(r.area_m2 for r in rows)
    right.text(
        0,
        0.08,
        f"Assigned-area gap (max − min): {gap:.1f} m²",
        fontsize=12,
        color=INK_SECONDARY,
        va="top",
        gid="presentation:gap",
    )
    return fig


def export_presentation(plan: Plan, result: Any, out_dir: str | Path) -> list[Path]:
    """Write `<plan_id>.presentation.png` (dpi 200) and `.svg` into `out_dir` (#44). Nothing is
    written when there is no design to show; the paths written are returned."""
    if result.submission is None:
        return []
    from hackhive.solve import file_stem

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{file_stem(result.sidecar.plan_id)}.presentation"
    png, svg = out / f"{stem}.png", out / f"{stem}.svg"
    png.write_bytes(presentation_png(plan, result))
    fig = draw_presentation(plan, result)
    fig.savefig(svg, facecolor=fig.get_facecolor())
    fig.clear()
    return [png, svg]


def presentation_png(plan: Plan, result: Any) -> bytes:
    """`draw_presentation` as PNG bytes at dpi 200: the exported file and the app's download."""
    fig = draw_presentation(plan, result)
    buf = io.BytesIO()  # no bbox_inches="tight": it would break the 16:9 frame
    fig.savefig(buf, format="png", dpi=_SLIDE_DPI, facecolor=fig.get_facecolor())
    fig.clear()
    return buf.getvalue()


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    """`python -m hackhive.viz <plan.json> <out.png> [--solution <submission.json>]`: draw a
    plan's base layer, or with a submission its full overlay (#17), violations in red."""
    from hackhive.io import load_plan

    p = argparse.ArgumentParser(prog="python -m hackhive.viz", description=main.__doc__)
    p.add_argument("plan", type=Path, help="one plan JSON file")
    p.add_argument("out", type=Path, help="where to write the PNG")
    p.add_argument("--solution", type=Path, help="a slide-8 submission to overlay")
    args = p.parse_args(argv)
    try:
        plan = load_plan(args.plan)
        sub = (
            Submission.model_validate_json(args.solution.read_text(encoding="utf-8"))
            if args.solution
            else None
        )
    except (OSError, ValueError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if sub is not None and sub.plan_id != plan.plan_id:
        print(
            f"warning: the submission is for {sub.plan_id}, the plan is {plan.plan_id}",
            file=sys.stderr,
        )
    if sub is None:
        fig = draw_layers(plan)
    else:
        fig = draw_solution(plan, sub, violations=checker.check_rules(plan, sub))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(to_png(fig))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
