"""Acceptance tests for #8: bend-aware path search on a label raster."""

import math
import random

import numpy as np
import pytest

from hackhive.contracts import Constraints
from hackhive.route import bend_weight_cells, bends, expand, path_length, path_to, search

BLOCKED = -1


def _room(h: int, w: int, label: int = 1) -> np.ndarray:
    return np.full((h, w), label, dtype=np.int16)


def _split(door_cols=range(8, 11), door_id=100) -> tuple[np.ndarray, dict]:
    """Room 1 (rows 0-4), a wall row 5 with a door, room 2 (rows 6-10)."""
    labels = np.full((11, 20), 1, dtype=np.int16)
    labels[5, :] = BLOCKED
    labels[6:, :] = 2
    for c in door_cols:
        labels[5, c] = door_id
    return labels, {door_id: (1, 2)}


def test_open_raster_one_bend():
    res = search(_room(20, 20), {}, [(0, 0)], w_bend=0.5)
    path = path_to(res, (10, 10))
    assert path[0] == (0, 0) and path[-1] == (10, 10)
    assert path_length(path) == 20
    assert bends(path) == 1
    assert res.cost((10, 10)) == pytest.approx(20.5)


def test_straight_line_has_no_bend():
    path = path_to(search(_room(5, 30), {}, [(2, 0)], w_bend=0.5), (2, 25))
    assert path == [(2, 0), (2, 25)]
    assert bends(path) == 0


def test_detour_around_a_wall():
    labels = _room(11, 11)
    labels[1:10, 5] = BLOCKED  # a wall with gaps at rows 0 and 10
    path = path_to(search(labels, {}, [(5, 0)], w_bend=0.5), (5, 10))
    assert path_length(path) == 20  # up 5, across 10, down 5
    assert bends(path) == 2


def test_bend_weight_trades_length_for_fewer_turns():
    """Two routes: 10 steps with 4 bends, or 14 steps with 2 bends."""
    labels = np.full((7, 9), BLOCKED, dtype=np.int16)
    labels[3, :] = 1  # main corridor, row 3
    labels[3, 4] = BLOCKED  # blocked in the middle
    labels[2, 3:6] = 1  # short detour: one row up around the block (4 bends)
    labels[0:4, 0] = 1  # long detour: up the left edge,
    labels[0, :] = 1  # across the top,
    labels[0:4, 8] = 1  # and down the right edge (2 bends)
    labels[2, 0:3] = BLOCKED
    labels[2, 6:9] = BLOCKED
    labels[2, 0] = labels[2, 8] = 1
    cheap_bends = path_to(search(labels, {}, [(3, 0)], w_bend=0.1), (3, 8))
    dear_bends = path_to(search(labels, {}, [(3, 0)], w_bend=5.0), (3, 8))
    assert bends(cheap_bends) == 4 and path_length(cheap_bends) == 10
    assert bends(dear_bends) == 2 and path_length(dear_bends) == 14


def test_crosses_wall_only_through_door():
    labels, door_rooms = _split()
    path = path_to(search(labels, door_rooms, [(0, 0)], w_bend=0.5), (10, 0))
    cells = expand(path)
    wall_cells = [c for c in cells if c[0] == 5]
    assert wall_cells, "the path must cross the wall row"
    assert all(labels[c] == 100 for c in wall_cells)


def test_wall_only_adjacency_does_not_connect_rooms():
    """Rooms 1 and 2 touch directly (no wall, no door): still no passage."""
    labels = np.full((10, 10), 1, dtype=np.int16)
    labels[5:, :] = 2
    res = search(labels, {}, [(0, 0)], w_bend=0.5)
    assert math.isinf(res.cost((9, 9)))
    assert path_to(res, (9, 9)) == []


def test_door_only_links_its_own_rooms():
    labels, _ = _split()
    res = search(labels, {100: (1, 3)}, [(0, 0)], w_bend=0.5)
    assert math.isinf(res.cost((10, 0)))


def test_unreachable_behind_wall():
    labels, _ = _split(door_cols=())
    res = search(labels, {}, [(0, 0)], w_bend=0.5)
    assert math.isinf(res.cost((10, 0)))
    assert path_to(res, (10, 0)) == []


def test_blocked_target_and_source():
    labels, door_rooms = _split()
    res = search(labels, door_rooms, [(0, 0)], w_bend=0.5)
    assert math.isinf(res.cost((5, 0)))
    with pytest.raises(ValueError):
        search(labels, door_rooms, [(5, 0)], w_bend=0.5)


def test_source_is_its_own_path():
    res = search(_room(3, 3), {}, [(1, 1)], w_bend=0.5)
    assert path_to(res, (1, 1)) == [(1, 1)]
    assert res.cost((1, 1)) == 0


def test_multi_source_picks_nearest():
    res = search(_room(1, 20), {}, [(0, 0), (0, 19)], w_bend=0.5)
    assert path_to(res, (0, 17)) == [(0, 19), (0, 17)]


@pytest.mark.parametrize("seed", range(25))
def test_random_rasters_axis_aligned_and_at_least_manhattan(seed):
    rng = random.Random(seed)
    h, w = rng.randint(5, 25), rng.randint(5, 25)
    labels = np.where(
        np.array([[rng.random() < 0.25 for _ in range(w)] for _ in range(h)]), BLOCKED, 1
    ).astype(np.int16)
    free = [tuple(map(int, c)) for c in np.argwhere(labels != BLOCKED)]
    src = rng.choice(free)
    res = search(labels, {}, [src], w_bend=rng.choice([0.0, 0.5, 2.0]))
    for tgt in rng.sample(free, min(10, len(free))):
        path = path_to(res, tgt)
        if not path:
            assert math.isinf(res.cost(tgt))
            continue
        assert path[0] == src and path[-1] == tgt
        for a, b in zip(path, path[1:], strict=False):
            assert a[0] == b[0] or a[1] == b[1]  # every step axis-aligned
        assert path_length(path) >= abs(src[0] - tgt[0]) + abs(src[1] - tgt[1])
        assert all(labels[c] != BLOCKED for c in expand(path))
        assert res.cost(tgt) == pytest.approx(path_length(path) + res.w_bend * bends(path))


def test_bend_weight_is_converted_to_cells():
    """A 5 m L-shaped leg (3 m + 2 m) costs 5 m + 0.5 m per J, i.e. 55 cells at 10 cm."""
    c = Constraints()
    w = bend_weight_cells(c)
    assert w == pytest.approx(5.0)
    res = search(_room(40, 40), {}, [(0, 0)], w_bend=w)
    assert res.cost((30, 20)) * c.grid_m == pytest.approx(5.0 + 0.5)


def test_bend_weight_scales_with_the_length_weight():
    """If #48 doubles the length weight, a bend is worth half as many steps."""
    from hackhive.contracts import CostWeights

    c = Constraints(cost_weights=CostWeights(2.0, 5.0, 1.0, 0.5))
    assert bend_weight_cells(c) == pytest.approx(2.5)


def test_room_and_door_ids_must_not_collide():
    """Rohit's repro: a door id equal to a room id would join rooms with no door."""
    labels = np.full((4, 4), 1, dtype=np.int16)
    labels[2:, :] = 2  # rooms 1 and 2 touch, no wall, no door
    with pytest.raises(ValueError, match="door id"):
        search(labels, {1: (2, 7)}, [(0, 0)], w_bend=0.5)


def test_bad_inputs_rejected():
    with pytest.raises(ValueError, match="outside"):
        search(_room(3, 3), {}, [(-1, 2)], w_bend=0.5)
    with pytest.raises(ValueError, match="outside"):
        search(_room(3, 3), {}, [(0, 3)], w_bend=0.5)
    with pytest.raises(ValueError, match="w_bend"):
        search(_room(3, 3), {}, [(0, 0)], w_bend=-1)


def _bfs(labels: np.ndarray, src) -> dict:
    """Plain 4-neighbour BFS distances over one label (the w_bend = 0 optimum)."""
    from collections import deque

    h, w = labels.shape
    dist = {src: 0}
    q = deque([src])
    while q:
        r, c = q.popleft()
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            n = (r + dr, c + dc)
            if 0 <= n[0] < h and 0 <= n[1] < w and labels[n] != BLOCKED and n not in dist:
                dist[n] = dist[(r, c)] + 1
                q.append(n)
    return dist


@pytest.mark.parametrize("seed", range(25))
def test_random_rasters_shortest_when_bends_are_free(seed):
    rng = random.Random(1000 + seed)
    h, w = rng.randint(5, 20), rng.randint(5, 20)
    labels = np.where(
        np.array([[rng.random() < 0.3 for _ in range(w)] for _ in range(h)]), BLOCKED, 1
    ).astype(np.int16)
    free = [tuple(map(int, c)) for c in np.argwhere(labels != BLOCKED)]
    src = rng.choice(free)
    res = search(labels, {}, [src], w_bend=0.0)
    ref = _bfs(labels, src)
    for cell in free:
        assert res.cost(cell) == ref.get(cell, math.inf)


@pytest.mark.parametrize("seed", range(25))
def test_random_rooms_change_only_through_their_doors(seed):
    """Vertical rooms 0..k-1 split by wall columns, each wall with one random door."""
    rng = random.Random(2000 + seed)
    h, n_rooms, width = rng.randint(5, 15), rng.randint(2, 4), rng.randint(3, 6)
    labels = np.zeros((h, n_rooms * (width + 1) - 1), dtype=np.int16)
    door_rooms = {}
    for k in range(n_rooms):
        labels[:, k * (width + 1) : k * (width + 1) + width] = k
        if k < n_rooms - 1:
            wall = k * (width + 1) + width
            labels[:, wall] = BLOCKED
            door = n_rooms + k  # doors are R + k
            for r in rng.sample(range(h), rng.randint(1, 3)):
                labels[r, wall] = door
            door_rooms[door] = (k, k + 1)
    src = (rng.randrange(h), 0)
    res = search(labels, door_rooms, [src], w_bend=rng.choice([0.0, 5.0]))
    target = (rng.randrange(h), labels.shape[1] - 1)
    path = path_to(res, target)
    assert path, "every room is reachable through the doors"
    cells = expand(path)
    for a, b in zip(cells, cells[1:], strict=False):
        la, lb = int(labels[a]), int(labels[b])
        if la != lb:  # a room change must be room <-> one of its own doors
            door, room = (la, lb) if la in door_rooms else (lb, la)
            assert door in door_rooms and room in door_rooms[door]


def test_numpy_integer_cells_are_accepted():
    """Cells straight from np.argwhere (np.int64) must work like plain ints (#10, #15)."""
    labels = _room(5, 5)
    src, dst = tuple(np.argwhere(labels == 1)[0]), tuple(np.argwhere(labels == 1)[-1])
    path = path_to(search(labels, {}, [src], w_bend=1.0), dst)
    assert path[0] == (0, 0) and path[-1] == (4, 4)
    assert path_length(path) == 8 and bends(path) == 1
    assert all(type(v) is int for cell in path for v in cell)
