"""Acceptance tests for #10: the 10 cm label raster painted from a Plan."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.affinity import translate
from shapely.geometry import MultiPolygon, Point, box, mapping
from shapely.ops import unary_union

from hackhive.contracts import Constraints, Opening
from hackhive.graph import build_graph
from hackhive.grid import BLOCKED, LabelGrid, door_gap
from hackhive.io import load_plan, room_by_label
from hackhive.rings import CELL_CENTRE_OFFSET
from hackhive.route import expand, path_to, search

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PLAN_IDS = ("576", "1293", "2814", "3086", "5088", "5924", "13344")


def _plan(pid: str):
    return load_plan(FIXTURES / "plans" / f"plan-{pid}.json")


def _vault_edges(pid: str, kind: str) -> set[frozenset[str]]:
    raw = json.loads(next((FIXTURES / "raw").glob(f"plan-{pid}-*.json")).read_text())
    return {frozenset((e["u"], e["v"])) for e in raw["strict_graph_edges"] if e["type"] == kind}


def _door_pairs(grid: LabelGrid, plan) -> set[frozenset[str]]:
    label = {r.id: r.label for r in plan.rooms}
    return {frozenset(label[grid.room_ids[i]] for i in pair) for pair in grid.door_rooms.values()}


# --------------------------------------------------------------------------- doors (C13)


def test_5924_every_interior_door_joins_exactly_two_rooms():
    plan = _plan("5924")
    grid = LabelGrid.from_plan(plan)
    assert len(grid.door_rooms) + len(grid.exterior_doors) == len(plan.doors) == 8
    for door, pair in grid.door_rooms.items():
        assert len(set(pair)) == 2
        cells = np.argwhere(grid.labels == door)
        assert len(cells), f"door {door} has no cells"
    # the two balcony doors lead outside `inner`: blocked, never a passage
    assert len(grid.exterior_doors) == 2
    assert _door_pairs(grid, plan) == {
        e for e in _vault_edges("5924", "via_door") if not any("balcony" in r for r in e)
    }


@pytest.mark.parametrize("pid", PLAN_IDS)
def test_door_pairs_are_vault_door_edges(pid):
    """Every raster door joins a pair the research graph also links through a door.

    1293's vault graph has 3 more pairs than its 10 doors can join (#9's concern).
    """
    plan = _plan(pid)
    grid = LabelGrid.from_plan(plan)
    vault = {e for e in _vault_edges(pid, "via_door") if not any("balcony" in r for r in e)}
    pairs = _door_pairs(grid, plan)
    assert pairs <= vault
    if pid != "1293":
        assert pairs == vault


@pytest.mark.parametrize("pid", PLAN_IDS)
def test_ids_follow_the_router_convention(pid):
    """Rooms are 0..R-1 and doors R+k (route.py rejects anything else)."""
    grid = LabelGrid.from_plan(_plan(pid))
    rooms = {int(v) for v in np.unique(grid.labels)} - set(grid.door_rooms) - {BLOCKED}
    assert max(rooms) < min(grid.door_rooms)
    search(grid.labels, grid.door_rooms, [tuple(np.argwhere(grid.labels == 0)[0])], 1.0)


# --------------------------------------------------------------------------- blocked cells (C14)


@pytest.mark.parametrize("pid", PLAN_IDS)
def test_window_front_door_and_outside_cells_are_blocked(pid):
    plan = _plan(pid)
    grid = LabelGrid.from_plan(plan)
    xs, ys = grid.centres()
    for g in [*plan.windows, *plan.front_doors]:
        inside = shapely.contains_xy(g, xs, ys)
        assert (grid.labels[inside] == BLOCKED).all()
    outside = ~shapely.contains_xy(plan.inner, xs, ys)
    assert (grid.labels[outside] == BLOCKED).all()
    walls = shapely.contains_xy(unary_union(plan.walls), xs, ys)
    doorish = np.isin(grid.labels, list(grid.door_rooms))
    assert (grid.labels[walls & ~doorish] == BLOCKED).all()


# --------------------------------------------------------------------------- snap (C1, C3)


@pytest.mark.parametrize("pid", PLAN_IDS)
def test_every_node_snaps_into_its_own_room_with_free_l_corners(pid):
    plan = _plan(pid)
    grid = LabelGrid.from_plan(plan)
    for node in plan.nodes:
        own = grid.room_label(node.room_id)
        cell, (a, b) = grid.snap(node.xy, node.room_id)
        assert grid.labels[cell] == own
        cx, cy = grid.to_xy(cell)
        assert abs(cx - node.xy[0]) <= grid.cell_m and abs(cy - node.xy[1]) <= grid.cell_m
        assert a == (cx, node.xy[1]) and b == (node.xy[0], cy)
        for corner in (a, b):
            assert grid.labels[grid.cell_of(corner)] == own


def test_snap_moves_at_most_one_cell_into_the_room():
    plan = _plan("5924")
    grid = LabelGrid.from_plan(plan)
    node = plan.nodes[0]
    cell, _ = grid.snap(node.xy, node.room_id)
    home = grid.cell_of(node.xy)
    assert max(abs(cell[0] - home[0]), abs(cell[1] - home[1])) <= 1


def test_snap_outside_its_room_raises():
    plan = _plan("5924")
    grid = LabelGrid.from_plan(plan)
    far = plan.nodes[1].xy  # a point in another room
    with pytest.raises(ValueError, match=plan.nodes[0].room_id):
        grid.snap(far, plan.nodes[0].room_id)


def test_cell_centres_follow_the_ring_convention():
    """Same lattice as #14's hub candidates: centres at (k + 1/2) * grid_m."""
    grid = LabelGrid.from_plan(_plan("5924"))
    for cell in [(0, 0), (7, 13), (grid.labels.shape[0] - 1, grid.labels.shape[1] - 1)]:
        for v in grid.to_xy(cell):
            k = v / grid.cell_m - CELL_CENTRE_OFFSET
            assert abs(k - round(k)) < 1e-9
        assert grid.cell_of(grid.to_xy(cell)) == cell


# --------------------------------------------------------------------------- openings (C13)


def _hand_drawn_opening(plan, pid: str) -> tuple[Opening, str, str]:
    """Until #9: the vault's open passage, drawn where the two rooms meet."""
    (pair,) = _vault_edges(pid, "via_opening")
    a, b = (next(r for r in plan.rooms if r.label == lab) for lab in sorted(pair))
    reach = plan.wall_depth_m * 0.55
    region = a.polygon.buffer(reach).intersection(b.polygon.buffer(reach))
    region = region.difference(unary_union([*plan.walls, *plan.windows]))
    return Opening(id="opening_0", polygon=region), a.id, b.id


def test_3086_rooms_meet_only_through_the_opening():
    plan = _plan("3086")
    opening, ra, rb = _hand_drawn_opening(plan, "3086")
    grid = LabelGrid.from_plan(plan, openings=[opening])
    la, lb = grid.room_label(ra), grid.room_label(rb)
    (op_label,) = [k for k, pair in grid.door_rooms.items() if set(pair) == {la, lb}]
    # keep only the two rooms and their opening, so no detour through another door
    only = np.where(np.isin(grid.labels, [la, lb, op_label]), grid.labels, BLOCKED)
    src = tuple(np.argwhere(only == la)[0])
    dst = tuple(np.argwhere(only == lb)[0])
    path = path_to(search(only, {op_label: (la, lb)}, [src], 1.0), dst)
    assert path, "the opening joins the two rooms"
    cells = expand(path)
    changes = [(p, q) for p, q in zip(cells, cells[1:], strict=False) if only[p] != only[q]]
    assert changes and all(op_label in (only[p], only[q]) for p, q in changes)
    # without the opening the same two rooms don't connect
    bare = LabelGrid.from_plan(plan)
    only = np.where(np.isin(bare.labels, [la, lb]), bare.labels, BLOCKED)
    assert not path_to(search(only, {}, [src], 1.0), dst)


# --------------------------------------------------------------------------- debug PNG


def test_debug_png_cli(tmp_path):
    out = tmp_path / "3086-grid.png"
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "hackhive.grid",
            str(FIXTURES / "plans" / "plan-3086.json"),
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # the picture is painted from #9's graph, so 3086's open kitchen shows as a passage
    assert "4 doors, 1 opening" in r.stdout


def test_default_cell_size_is_the_preset_grid():
    grid = LabelGrid.from_plan(_plan("5088"))
    assert grid.cell_m == Constraints().grid_m
    assert grid.labels.dtype == np.int16
    assert Point(grid.to_xy((0, 0))).x < Point(grid.to_xy((0, 1))).x


# --------------------------------------------------------------------------- door width (review)


def _straight_crossings(grid: LabelGrid):
    """Every straight room -> door cells -> room run along a row or column, as two centres."""
    labels = grid.labels
    rows, cols = labels.shape
    for door, pair in grid.door_rooms.items():
        for r, c in np.argwhere(labels == door):
            for dr, dc in ((0, 1), (1, 0)):
                if 0 <= r - dr < rows and 0 <= c - dc < cols and labels[r - dr, c - dc] == door:
                    continue  # start each run at its first door cell
                rr, cc = r, c
                while 0 <= rr < rows and 0 <= cc < cols and labels[rr, cc] == door:
                    rr, cc = rr + dr, cc + dc
                a, b = (r - dr, c - dc), (rr, cc)
                if not (
                    0 <= a[0] < rows and 0 <= a[1] < cols and 0 <= b[0] < rows and 0 <= b[1] < cols
                ):
                    continue
                if {int(labels[a]), int(labels[b])} == set(pair):
                    yield door, grid.to_xy(a), grid.to_xy(b)


@pytest.mark.parametrize("pid", PLAN_IDS)
def test_no_legal_door_crossing_cuts_real_wall(pid):
    """Doors may reach through the wall band, but never sideways along it (#11 rule 5)."""
    from shapely.geometry import LineString

    plan = _plan(pid)
    grid = LabelGrid.from_plan(plan)
    solid = unary_union(plan.walls).difference(unary_union([d.polygon for d in plan.doors]))
    cuts = [
        (door, a, b, LineString([a, b]).intersection(solid).length)
        for door, a, b in _straight_crossings(grid)
    ]
    bad = [c for c in cuts if c[3] > 1e-6]
    assert cuts, "every fixture has door crossings"
    assert not bad, f"{len(bad)} crossings cut wall, worst {max(c[3] for c in bad):.3f} m"


# --------------------------------------------------------------------------- #9's graph decides


def _graph_plan(pid: str):
    plan = _plan(pid)
    build_graph(plan)
    return plan


@pytest.mark.parametrize("pid", PLAN_IDS)
def test_passages_join_the_rooms_build_graph_names(pid):
    """Door.rooms and Opening.rooms (filled by #9's build_graph) decide each passage's rooms;
    the raster only checks them. Openings default to plan.openings."""
    plan = _graph_plan(pid)
    grid = LabelGrid.from_plan(plan)
    named = {d.id: d.rooms for d in plan.doors} | {o.id: o.rooms for o in plan.openings}
    assert grid.passage_ids == [*(d.id for d in plan.doors), *(o.id for o in plan.openings)]
    n = len(grid.room_ids)
    for label, pair in grid.door_rooms.items():
        pid_ = grid.passage_ids[label - n]
        assert {grid.room_ids[i] for i in pair} == set(named[pid_]), pid_
    for door in grid.exterior_doors:  # only a door whose other room lies outside `inner`
        assert any(not (grid.labels == grid.room_label(r)).any() for r in named[door]), door
    assert len(grid.door_rooms) + len(grid.exterior_doors) == len(named)


def _three_rooms_at_one_door() -> dict:
    """A door whose kitchen side is one room but whose other side is split between a bedroom
    and a storage room (1 unit = 0.1 m): raster contact alone can't tell which two it joins."""
    return {
        "id": "SYN",
        "area": 100.0,
        "wall_depth": 2.0,
        "inner": mapping(box(0, 0, 100, 100)),
        "bedroom": mapping(box(0, 0, 40, 15)),
        "storage": mapping(box(0, 15, 40, 40)),
        "kitchen": mapping(box(42, 0, 82, 40)),
        "wall": mapping(MultiPolygon([box(40, -2, 42, 10), box(40, 20, 42, 42)])),
        "door": mapping(box(40, 10, 42, 20)),
    }


def test_door_rooms_beat_raster_contact():
    plan = load_plan(_three_rooms_at_one_door())
    kitchen, storage = (room_by_label(plan, x).id for x in ("kitchen_0", "storage_0"))
    with pytest.raises(ValueError, match="3\\+ rooms"):  # contact alone is ambiguous
        LabelGrid.from_plan(plan)
    plan.doors[0].rooms = (kitchen, storage)
    grid = LabelGrid.from_plan(plan)
    (pair,) = grid.door_rooms.values()
    assert {grid.room_ids[i] for i in pair} == {kitchen, storage}


def test_a_named_room_the_door_doesnt_reach_is_an_error():
    plan = _graph_plan("5924")
    door = next(d for d in plan.doors if d.rooms)
    far = max(
        (r for r in plan.rooms if r.eligible and r.id not in door.rooms),
        key=lambda r: r.polygon.distance(door.polygon),
    )
    door.rooms = (door.rooms[0], far.id)
    with pytest.raises(ValueError, match=f"{door.id}.*{far.id}"):
        LabelGrid.from_plan(plan)


def test_a_named_room_the_plan_lacks_is_an_error():
    plan = _graph_plan("5924")
    door = next(d for d in plan.doors if d.rooms)
    door.rooms = (door.rooms[0], "no_such_room")
    with pytest.raises(ValueError, match=f"{door.id}.*no_such_room"):
        LabelGrid.from_plan(plan)


def test_doors_are_painted_over_openings():
    """An opening band next to a door never takes the door's cells (576's living–storage band
    touches door_8's gap): doors are painted last."""
    plan = _graph_plan("5924")
    door = next(d for d in plan.doors if d.rooms)
    gap = door_gap(door.polygon, plan.wall_depth_m, Constraints().grid_m)
    x0, y0, x1, y1 = gap.bounds  # 5924's doors are axis-aligned
    half = ((x1 - x0) / 2, 0.0) if x1 - x0 > y1 - y0 else (0.0, (y1 - y0) / 2)
    # a band over the door's far half, like 576's living–storage band at door_8
    band = Opening(id="opening_0", polygon=translate(gap, *half), rooms=door.rooms)
    grid = LabelGrid.from_plan(plan, openings=[band])
    xs, ys = grid.centres()
    in_gap = shapely.contains_xy(gap, xs, ys) & (grid.labels != BLOCKED)
    assert in_gap.any()
    assert (grid.labels[in_gap] == grid.passage_ids.index(door.id) + len(grid.room_ids)).all()


def test_3086_rooms_meet_through_build_graphs_opening():
    plan = _graph_plan("3086")
    (opening,) = plan.openings
    grid = LabelGrid.from_plan(plan)
    la, lb = (grid.room_label(r) for r in opening.rooms)
    op_label = grid.passage_ids.index(opening.id) + len(grid.room_ids)
    assert set(grid.door_rooms[op_label]) == {la, lb}
    only = np.where(np.isin(grid.labels, [la, lb, op_label]), grid.labels, BLOCKED)
    src, dst = tuple(np.argwhere(only == la)[0]), tuple(np.argwhere(only == lb)[0])
    assert path_to(search(only, {op_label: (la, lb)}, [src], 1.0), dst)


# --------------------------------------------------------------------------- thin and outside (#78)


def _two_rooms(**extra) -> dict:
    """A living room under a bedroom (1 unit = 0.1 m), meeting at y = 5 m, walled off except
    for x in 4-6 m."""
    return {
        "id": "THIN",
        "area": 100.0,
        "wall_depth": 2.0,
        "inner": mapping(box(0, 0, 100, 100)),
        "living": mapping(box(0, 0, 100, 50)),
        "bedroom": mapping(box(0, 50, 100, 100)),
        "wall": mapping(MultiPolygon([box(0, 49, 40, 51), box(60, 49, 100, 51)])),
        **extra,
    }


def test_thin_opening_paints_the_cells_it_crosses_and_passes_rule_5():
    """A 3 cm passage on the shared edge covers no cell centre: the cells it crosses are
    painted, never past its ends, and the route through them passes rule 5 (C13, C14)."""
    from hackhive.checker import _free_space, _rule5_leg, clean_polyline

    plan = load_plan(_two_rooms())
    living, bedroom = (room_by_label(plan, x).id for x in ("living_0", "bedroom_0"))
    strip = box(4.0, 4.985, 6.0, 5.015)
    plan.openings = [Opening(id="opening_0", polygon=strip, rooms=(living, bedroom))]
    grid = LabelGrid.from_plan(plan)
    (label,) = grid.door_rooms
    xs, ys = grid.centres()
    here = grid.labels == label
    assert here.sum() == 2 * 20  # the two rows it crosses, 2 m of them
    assert (xs[here] > 4.0).all() and (xs[here] < 6.0).all()  # never along, into the wall
    assert set(np.round(ys[here], 2)) == {4.95, 5.05}

    src, dst = grid.cell_of((2.0, 2.0)), grid.cell_of((8.0, 8.0))
    path = path_to(search(grid.labels, grid.door_rooms, [src], 1.0), dst)
    assert path
    pts = clean_polyline([grid.to_xy(cell) for cell in expand(path)])
    c = Constraints()
    assert _rule5_leg(pts, _free_space(plan, c), c, None, "leg") == []


def _house_and_balconies() -> dict:
    """A house (`inner`) with two balconies 0.3 m beyond it, one wall depth apart: #9's graph
    finds a 2 cm open strip between them, wholly outside `inner` (366, 5676, … in ResPlan)."""
    return {
        "id": "BALC",
        "area": 50.0,
        "wall_depth": 2.0,
        "inner": mapping(box(0, 0, 100, 50)),
        "living": mapping(box(0, 0, 100, 50)),
        "balcony": mapping(MultiPolygon([box(0, 53, 49, 70), box(51, 53, 100, 70)])),
    }


def test_a_passage_wholly_outside_inner_is_exterior():
    plan = _graph_plan_from(_house_and_balconies())
    (opening,) = plan.openings
    assert opening.polygon.intersection(plan.inner).area == 0
    grid = LabelGrid.from_plan(plan)
    assert grid.exterior_doors == [opening.id]
    assert not grid.door_rooms


def test_a_passage_outside_inner_between_rooms_inside_it_is_an_error():
    """Never drop a real link: both rooms lie inside `inner`, so a door outside it is wrong."""
    raw = _two_rooms(door=mapping(box(40, 105, 60, 107)))
    plan = load_plan({**raw, "inner": mapping(box(0, 0, 100, 100))})
    plan.doors[0].rooms = tuple(r.id for r in plan.rooms[:2])
    with pytest.raises(
        ValueError, match=r"door_0 lies outside inner, but both its rooms \(R01, R02\)"
    ):
        LabelGrid.from_plan(plan)


def test_a_door_drawn_twice_names_the_door_over_it():
    door = box(40, 49, 60, 51)
    plan = _graph_plan_from(_two_rooms(door=mapping(MultiPolygon([door, door.buffer(-0.01)]))))
    with pytest.raises(ValueError, match=r"door_0 has no cells of its own \(door_1 over it\)"):
        LabelGrid.from_plan(plan)


def _graph_plan_from(raw: dict):
    plan = load_plan(raw)
    build_graph(plan)
    return plan
