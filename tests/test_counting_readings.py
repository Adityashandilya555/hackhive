"""Acceptance tests for #30's counting-rules toggle (D3): J under both readings of slides 6-7.

`strict` is our checker mirror (decision_conventions §6). `lenient` reads the three unclear
rules the other way: a T-touch or the end of a shared stretch is no crossing, overlap counts only
inside one ring, and a turn at a node or the hub is no bend. Same design, two numbers.
"""

from pathlib import Path

import pytest
from fixture_solves import solved

from hackhive.checker import metrics
from hackhive.contracts import Constraints, HubOut, Submission

PLANS = Path(__file__).resolve().parent / "fixtures" / "plans"
C = Constraints()


def approx(x):
    return pytest.approx(x, abs=1e-9)


def ring(hub_xy, legs, hub_id="H1") -> HubOut:
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
            {"from": a, "to": b, "length_m": 0.0, "polyline": leg}
            for a, b, leg in zip(seq[:-1], seq[1:], legs, strict=True)
        ],
        ring_length_m=0.0,
    )


def both(hubs):
    return metrics(hubs, C), metrics(hubs, C, reading="lenient")


def test_strict_is_the_default():
    hubs = [ring((0, 0), [[(0, 0), (3, 0)], [(3, 0), (0, 0)]])]
    assert metrics(hubs, C) == metrics(hubs, C, reading="strict")


def test_unknown_reading_is_rejected():
    with pytest.raises(ValueError, match="reading"):
        metrics([ring((0, 0), [[(0, 0), (3, 0)], [(3, 0), (0, 0)]])], C, reading="loose")


def test_a_plus_crossing_counts_under_both():
    a = ring((0, 2), [[(0, 2), (4, 2)], [(4, 2), (4, 6), (0, 6), (0, 2)]], hub_id="H1")
    b = ring((2, 0), [[(2, 0), (2, 4)], [(2, 4), (8, 4), (8, 0), (2, 0)]], hub_id="H2")
    strict, lenient = both([a, b])
    assert strict.X == lenient.X == 2  # (2, 2) and (4, 4): two real crossings


def test_a_t_touch_counts_only_strict():
    # H2's bend vertex (2, 0) lies on H1's straight run (the checker's own T-touch shape)
    a = ring((0, 0), [[(0, 0), (4, 0)], [(4, 0), (4, -2), (0, -2), (0, 0)]], hub_id="H1")
    b = ring((1, 3), [[(1, 3), (2, 0), (3, 3)], [(3, 3), (1, 3)]], hub_id="H2")
    strict, lenient = both([a, b])
    assert (strict.X, lenient.X) == (1, 0)


def test_a_shared_stretch_between_rings():
    # H2 runs 2 m along H1's line: strict counts the 2 m and both divergence ends
    a = ring((0, 0), [[(0, 0), (6, 0)], [(6, 0), (6, -3), (0, -3), (0, 0)]], hub_id="H1")
    b = ring((2, 3), [[(2, 3), (2, 0), (4, 0), (4, 3)], [(4, 3), (2, 3)]], hub_id="H2")
    strict, lenient = both([a, b])
    assert (strict.O, strict.X) == (approx(2), 2)
    assert (lenient.O, lenient.X) == (approx(0), 0)  # not one ring's own overlap; no crossing
    assert strict.L == lenient.L


def test_a_rings_own_retrace_is_overlap_under_both():
    hubs = [ring((0, 0), [[(0, 0), (3, 0)], [(3, 0), (0, 0)]])]
    strict, lenient = both(hubs)
    assert strict.O == lenient.O == approx(3)


def test_turns_at_nodes_and_the_hub_count_only_strict():
    # a rectangle: four turns, all at the hub or a node, none inside a leg
    legs = [[(0, 0), (4, 0)], [(4, 0), (4, 2)], [(4, 2), (0, 2)], [(0, 2), (0, 0)]]
    strict, lenient = both([ring((0, 0), legs)])
    assert (strict.B, lenient.B) == (4, 0)
    # a bend inside a leg counts under both
    strict, lenient = both([ring((0, 0), [[(0, 0), (3, 0), (3, 2)], [(3, 2), (0, 2), (0, 0)]])])
    assert lenient.B == 2 and strict.B == 4


def test_j_follows_the_weights_under_both():
    a = ring((0, 0), [[(0, 0), (6, 0)], [(6, 0), (6, -3), (0, -3), (0, 0)]], hub_id="H1")
    b = ring((2, 3), [[(2, 3), (2, 0), (4, 0), (4, 3)], [(4, 3), (2, 3)]], hub_id="H2")
    w = C.cost_weights
    for m in both([a, b]):
        assert m.J == approx(w.length * m.L + w.cross * m.X + w.overlap * m.O + w.bend * m.B)
        assert sum(t.L for t in m.per_hub.values()) == approx(m.L)
        assert sum(t.B for t in m.per_hub.values()) == m.B


@pytest.mark.parametrize("plan_id", ["576", "5924", "13344"])
def test_lenient_never_costs_more_on_a_fixture(plan_id):
    """Every lenient term is a subset of the strict one, so its J is never higher."""
    sub: Submission = solved(plan_id).submission
    strict, lenient = both(sub.hubs)
    assert lenient.L == approx(strict.L)
    assert lenient.X <= strict.X and lenient.O <= strict.O + 1e-9 and lenient.B <= strict.B
    assert lenient.J <= strict.J + 1e-9
