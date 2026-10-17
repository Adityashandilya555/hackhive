"""Acceptance tests for #43: the organisers' metrics L, X, O, B and J (slides 6-7).

The eight `slide7` tests reproduce slide 7's worked examples; the rest pin the strict readings in
docs/decision_conventions.md §6, the 1 mm tolerance, J and the area gap.
"""

import ast
import json
import math
from pathlib import Path

import pytest

from hackhive import checker
from hackhive.checker import (
    area_gap,
    clean_polyline,
    count_bends,
    crossing_points,
    metrics,
    overlap_length,
)
from hackhive.contracts import Constraints, CostWeights, HubOut, Submission

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
TOL = Constraints().tolerance_m


def approx(x):
    return pytest.approx(x, abs=1e-9)


def ring(hub_xy, legs, hub_id="H1", length_m=0.0, ring_length_m=0.0) -> HubOut:
    """A closed ring H → N1 → … → Nk → H whose legs are the given polylines."""
    nodes = [f"{hub_id}N{i}" for i in range(1, len(legs))]
    seq = [hub_id, *nodes, hub_id]
    return HubOut(
        hub_id=hub_id,
        room_id="R01",
        location={"x": hub_xy[0], "y": hub_xy[1]},
        assigned_rooms=["R01"],
        assigned_nodes=nodes,
        closed_sequence=seq,
        route_segments=[
            {"from": a, "to": b, "length_m": length_m, "polyline": leg}
            for a, b, leg in zip(seq[:-1], seq[1:], legs, strict=True)
        ],
        ring_length_m=ring_length_m,
    )


def ends(*legs):
    return [p for leg in legs for p in (leg[0], leg[-1])]


# --------------------------------------------------------------------------- slide 7: the eight


def test_slide7_L_rectangle():
    legs = [
        [(0, 0), (4, 0)],
        [(4, 0), (4, 2)],
        [(4, 2), (1, 2)],
        [(1, 2), (0, 2), (0, 0)],
    ]
    m = metrics([ring((0, 0), legs)])
    assert (m.L, m.X, m.O, m.B) == (approx(12), 0, approx(0), 4)
    assert m.J == approx(14)  # 12 + 0.5 · 4


def test_slide7_L_out_and_back():
    assert metrics([ring((0, 0), [[(0, 0), (3, 0)], [(3, 0), (0, 0)]])]).L == approx(6)


def test_slide7_X_cross_in_open():
    a, b = [(0, 2), (4, 2)], [(2, 0), (2, 4)]
    assert len(crossing_points([a, b], ends(a, b))) == 1


def test_slide7_X_meet_at_node():
    a, b = [(0, 0), (2, 0)], [(2, 0), (2, 3)]
    assert crossing_points([a, b], ends(a, b)) == []


def test_slide7_O_same_line():
    assert overlap_length([[(0, 0), (5, 0)], [(3, 0), (5, 0), (5, 2)]]) == approx(2)


def test_slide7_O_parallel_runs():
    assert overlap_length([[(0, 0), (5, 0)], [(0, 0.2), (5, 0.2)]]) == approx(0)


def test_slide7_B_s_shape():
    assert count_bends([[(0, 0), (2, 0), (2, 2), (4, 2)]], closed=False) == 2


def test_slide7_B_straight_through_node():
    assert count_bends([[(0, 0), (2, 0)], [(2, 0), (5, 0)]], closed=False) == 0


# --------------------------------------------------------------------------- strict cases, tolerance


def test_one_node_out_and_back_j():
    m = metrics([ring((0, 0), [[(0, 0), (3, 0)], [(3, 0), (0, 0)]])])
    assert (m.L, m.O, m.B, m.X) == (approx(6), approx(3), 2, 0)
    assert m.J == approx(10)


def test_tolerance_same_point():
    assert clean_polyline([(0, 0), (1, 0), (1.0005, 0), (2, 0)]) == [(0, 0), (2, 0)]
    assert count_bends([[(0, 0), (1, 0.0005), (2, 0)]], closed=False) == 0
    h, v1, v2 = [(0, 2), (4, 2)], [(2, 0), (2, 4)], [(2.0005, 0), (2.0005, 4)]
    assert len(crossing_points([h, v1, v2], ends(h, v1, v2))) == 1


def test_tolerance_same_line():
    assert overlap_length([[(0, 0), (3, 0)], [(1, 0.0005), (3, 0.0005)]]) == approx(2)
    assert overlap_length([[(0, 0), (3, 0)], [(1, 0.002), (3, 0.002)]]) == approx(0)


def test_t_touch_counts_strict():
    a, b = [(0, 0), (4, 0)], [(1, 3), (2, 0), (3, 3)]  # b's bend vertex lies on a's interior
    assert len(crossing_points([a, b], ends(a, b))) == 1


def test_divergence_ends_are_no_crossing():
    # slide 7's overlap example: 2 m on the same line costs O = 2 m, and where the cables part
    # they don't cross (only a "+" or a T-touch is a meeting, decision_conventions §6)
    a, b = [(0, 0), (6, 0)], [(2, 3), (2, 0), (4, 0), (4, 3)]
    assert overlap_length([a, b]) == approx(2)
    assert crossing_points([a, b], ends(a, b)) == []


@pytest.mark.parametrize("reading", checker.READINGS)
def test_split_below_a_node_is_no_crossing(reading):
    # both legs run the last 4.4 cm into N1 on one line and part 4.4 cm away from it (a node a
    # few cm off the grid row, as in a fabricated 3-hub plan): overlap, not a crossing
    legs = [[(0, -3), (0, 0.044)], [(0, 0.044), (0, 0), (2, 0), (2, -3), (0, -3)]]
    m = metrics([ring((0, -3), legs)], reading=reading)
    assert (m.X, m.O) == (0, approx(0.044))


def test_co_routed_corner_is_not_a_divergence():
    # a single-door retrace that turns a corner: both cables arrive and leave (3, 0) together
    m = metrics([ring((0, 0), [[(0, 0), (3, 0), (3, 2)], [(3, 2), (3, 0), (0, 0)]])])
    assert (m.L, m.X, m.O, m.B) == (approx(10), 0, approx(5), 4)
    assert m.J == approx(17)


def test_x_inside_one_ring():
    # a figure-8 ring crosses itself at (2, 0) (slide 6: X counts inside one ring)
    legs = [[(0, 0), (4, 0), (4, 2)], [(4, 2), (2, 2), (2, -2)], [(2, -2), (0, -2), (0, 0)]]
    assert metrics([ring((0, 0), legs)]).X == 1


def test_foreign_cable_through_node_counts():
    # H2's cable runs straight through H1's node N1 at (5, 5): only the node's own legs meet free
    a = ring((0, 5), [[(0, 5), (5, 5)], [(5, 5), (5, 8), (0, 8), (0, 5)]], hub_id="H1")
    b = ring((5, 0), [[(5, 0), (5, 3), (5, 10), (8, 10)], [(8, 10), (8, 0), (5, 0)]], hub_id="H2")
    assert metrics([a, b]).X == 2  # (5, 5) and (5, 8), as with the cable shifted off the node
    shifted = ring((4, 0), [[(4, 0), (4, 10), (8, 10)], [(8, 10), (8, 0), (4, 0)]], hub_id="H2")
    assert metrics([a, shifted]).X == 2


def test_once_per_point():
    a, b, c = [(0, 2), (4, 2)], [(2, 0), (2, 4)], [(0, 0), (4, 4)]
    assert len(crossing_points([a, b, c], ends(a, b, c))) == 1


def test_end_touch_no_overlap():
    assert overlap_length([[(0, 0), (2, 0)], [(2, 0), (4, 0)]]) == approx(0)


def test_cleanup():
    raw = [(0, 0), (0, 0), (1, 0), (2, 0), (2, 0.0004), (2, 3)]
    assert clean_polyline(raw) == [(0, 0), (2, 0), (2, 3)]
    assert clean_polyline([(0, 0), (3, 0), (1, 0)]) == [(0, 0), (3, 0), (1, 0)]  # reversal kept
    noisy = ring((0, 0), [[(0, 0), (1, 0), (1, 0), (3, 0)], [(3, 0), (2, 0), (0, 0)]])
    clean = ring((0, 0), [[(0, 0), (3, 0)], [(3, 0), (0, 0)]])
    assert (metrics([noisy]).L, metrics([noisy]).B) == (metrics([clean]).L, metrics([clean]).B)


def _two_crossing_rings():
    h1 = ring((0, 2), [[(0, 2), (4, 2)], [(4, 2), (0, 2)]], hub_id="H1")
    h2 = ring((2, 0), [[(2, 0), (2, 4)], [(2, 4), (2, 0)]], hub_id="H2")
    return [h1, h2]


def test_j_formula():
    m = metrics(_two_crossing_rings())
    assert (m.H, m.L, m.X, m.O, m.B) == (2, approx(16), 1, approx(8), 4)
    assert m.J == approx(m.L + 5 * m.X + m.O + 0.5 * m.B) == approx(31)
    custom = Constraints(cost_weights=CostWeights(2, 1, 3, 4))
    assert metrics(_two_crossing_rings(), custom).J == approx(2 * 16 + 1 + 3 * 8 + 4 * 4)
    # per hub: L and B are each ring's own; the crossing is credited to both rings
    assert sum(t.L for t in m.per_hub.values()) == approx(m.L)
    assert sum(t.B for t in m.per_hub.values()) == m.B
    assert m.per_hub["H1"].X == m.per_hub["H2"].X == 1


def test_reported_values_ignored():
    legs = [[(0, 0), (3, 0)], [(3, 0), (0, 0)]]
    honest = metrics([ring((0, 0), legs)])
    garbage = ring((0, 0), legs, length_m=-1, ring_length_m=1e9)
    sub = Submission(
        plan_id="P",
        units="m",
        hubs=[garbage],
        reported_metrics={"hub_count": -1, "total_length_m": 1e9},
    )
    assert metrics(sub.hubs) == honest


def test_nan_coordinate_rejected():
    hub = ring((0, 0), [[(0, 0), (math.nan, 0)], [(3, 0), (0, 0)]])  # pydantic accepts NaN
    with pytest.raises(ValueError):
        metrics([hub])


def test_hub_without_legs():
    empty = ring((0, 0), [[(0, 0)]]).model_copy(update={"route_segments": []})
    m = metrics([empty])
    assert (m.L, m.X, m.O, m.B) == (0, 0, 0, 0)


def test_area_gap():
    assert area_gap([70, 60, 40]) == approx(30)
    assert area_gap([70]) == 0.0
    hubs = _two_crossing_rings()
    assert metrics(hubs).area_gap is None
    assert metrics(hubs, hub_areas={"H1": 70, "H2": 40}).area_gap == approx(30)


def test_slide8_example_metrics():
    sub = Submission.model_validate(json.loads((FIXTURES / "slide8_example.json").read_text()))
    assert metrics(sub.hubs).L == approx(4.8 + 7.1)


def test_no_weight_literals():
    tree = ast.parse(Path(checker.__file__).read_text(encoding="utf-8"))
    found = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and type(n.value) in (int, float)
        and n.value in (5.0, 0.5, 0.001)
    ]
    assert found == []
