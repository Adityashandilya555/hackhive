"""Build the #11 rule fixtures in this folder from hand-picked coordinates (metres).

    uv run python tests/fixtures/solutions/make_fixtures.py [out_dir]

`test_checker_rules.py` regenerates them into a temp folder and checks the committed files
match. Node points are written out verbatim (as `io.load_plan` places them) so the output is the
same on every platform; `write()` checks them against `load_plan` first. Reported lengths are rounded to 1 µm and summed with `math.fsum`, so they
don't depend on the Python version (3.12 changed `sum()` of floats; CI runs 3.11).
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

from shapely.geometry import MultiPolygon, box, mapping

from hackhive.checker import segment_length
from hackhive.io import load_plan

HERE = Path(__file__).resolve().parent
PLAN_5088 = HERE.parent / "plans" / "plan-5088.json"

N1, N2, N3 = (
    (1.9168004848969091, 4.1252094467026),
    (8.139631124543094, 5.929384811987724),
    (4.982010914708966, 7.239747515909592),
)
HUB = (3.05, 5.55)

# --------------------------------------------------------------------------- synthetic plans
# drawing units, 10 units = 1 m, wall_depth 2.0 (like tests/test_graph.py's `synthetic`)

SYN_ENTRANCE = {
    "id": "SYN_ENTRANCE",
    "area": 100.0,
    "wall_depth": 2.0,
    "inner": mapping(box(0, 0, 100, 100)),
    "bedroom": mapping(box(0, 0, 40, 40)),
    "kitchen": mapping(box(42, 0, 82, 40)),
    "bathroom": mapping(box(0, 42, 84, 62)),
    "wall": mapping(
        MultiPolygon(
            [
                box(40, -2, 42, 10),
                box(40, 20, 42, 40),
                box(0, 40, 10, 42),
                box(18, 40, 60, 42),
                box(68, 40, 84, 42),
                box(82, 0, 84, 40),
            ]
        )
    ),
    "door": mapping(MultiPolygon([box(10, 40, 18, 42), box(60, 40, 68, 42)])),
    "front_door": mapping(box(40, 10, 42, 20)),
}
SYN_OVERCAP = {
    "id": "SYN_OVERCAP",
    "area": 200.0,
    "wall_depth": 2.0,
    "inner": mapping(box(0, 0, 200, 100)),
    "living": mapping(box(0, 0, 99, 100)),
    "bedroom": mapping(box(101, 0, 200, 100)),
    "wall": mapping(MultiPolygon([box(99, 0, 101, 40), box(99, 50, 101, 100)])),
    "door": mapping(box(99, 40, 101, 50)),
}
SYN_OPEN = {
    "id": "SYN_OPEN",
    "area": 100.0,
    "wall_depth": 2.0,
    "inner": mapping(box(0, 0, 100, 100)),
    "living": mapping(box(0, 0, 50, 100)),
    "kitchen": mapping(box(50, 0, 100, 100)),
    "wall": mapping(MultiPolygon([box(49, 0, 51, 20), box(49, 80, 51, 100)])),
}
SYN_NODES = {
    "SYN_ENTRANCE": {"N1": (2.0, 2.0), "N2": (6.200000000000001, 2.0)},
    "SYN_OVERCAP": {"N1": (4.95, 5.0), "N2": (15.05, 5.0)},
}

# --------------------------------------------------------------------------- builders


def total(lengths) -> float:
    """A reported length sum, the same on every Python version."""
    return round(math.fsum(lengths), 6)


def leg(a: str, b: str, *points) -> dict:
    pts = [[float(x), float(y)] for x, y in points]
    return {"from": a, "to": b, "length_m": round(segment_length(pts), 6), "polyline": pts}


def hub(hub_id, room_id, loc, rooms, nodes, sequence, legs) -> dict:
    return {
        "hub_id": hub_id,
        "room_id": room_id,
        "location": {"x": float(loc[0]), "y": float(loc[1])},
        "assigned_rooms": list(rooms),
        "assigned_nodes": list(nodes),
        "closed_sequence": list(sequence),
        "route_segments": list(legs),
        "ring_length_m": total(s["length_m"] for s in legs),
    }


def submission(plan_id: str, *hubs) -> dict:
    return {
        "plan_id": plan_id,
        "units": "m",
        "hubs": list(hubs),
        "reported_metrics": {
            "hub_count": len(hubs),
            "total_length_m": total(h["ring_length_m"] for h in hubs),
        },
    }


def replace_leg(hub_dict: dict, a: str, b: str, new: dict) -> dict:
    """A copy of the hub with the a→b segment replaced (the ring length recomputed)."""
    h = copy.deepcopy(hub_dict)
    h["route_segments"] = [
        new if (s["from"], s["to"]) == (a, b) else s for s in h["route_segments"]
    ]
    h["ring_length_m"] = total(s["length_m"] for s in h["route_segments"])
    return h


# --------------------------------------------------------------------------- 5088


def _h1_to_n3(start=HUB):
    return leg("H1", "N3", start, (3.05, 4.6), (4.6, 4.6), (4.6, N3[1]), N3)


N3_N2 = leg("N3", "N2", N3, (5.3, N3[1]), (5.3, 4.4), (N2[0], 4.4), N2)
N2_N1 = leg("N2", "N1", N2, (8.6, N2[1]), (8.6, 4.0), (N1[0], 4.0), N1)
N1_H1 = leg("N1", "H1", N1, (N1[0], 5.55), HUB)

VALID = submission(
    "PLAN_5088",
    hub(
        "H1",
        "R01",
        HUB,
        ["R01", "R02", "R03"],
        ["N1", "N2", "N3"],
        ["H1", "N3", "N2", "N1", "H1"],
        [_h1_to_n3(), N3_N2, N2_N1, N1_H1],
    ),
)
TWO_H1 = hub(
    "H1",
    "R01",
    HUB,
    ["R01", "R03"],
    ["N1", "N3"],
    ["H1", "N3", "N1", "H1"],
    [_h1_to_n3(), leg("N3", "N1", N3, (5.3, N3[1]), (5.3, 4.0), (N1[0], 4.0), N1), N1_H1],
)
TWO_H2 = hub(
    "H2",
    "R02",
    (7.5, 5.5),
    ["R02"],
    ["N2"],
    ["H2", "N2", "H2"],
    [
        leg("H2", "N2", (7.5, 5.5), (N2[0], 5.5), N2),
        leg("N2", "H2", N2, (8.6, N2[1]), (8.6, 5.2), (7.5, 5.2), (7.5, 5.5)),
    ],
)
TWO_HUBS = submission("PLAN_5088", TWO_H1, TWO_H2)


def _variant(**changes) -> dict:
    """The valid 5088 submission with H1's fields changed."""
    s = copy.deepcopy(VALID)
    s["hubs"][0].update(copy.deepcopy(changes))
    h = s["hubs"][0]
    h["ring_length_m"] = total(seg["length_m"] for seg in h["route_segments"])
    s["reported_metrics"]["total_length_m"] = h["ring_length_m"]
    return s


def _with_h1(new_h1: dict) -> dict:
    s = copy.deepcopy(VALID)
    s["hubs"][0] = new_h1
    s["reported_metrics"]["total_length_m"] = new_h1["ring_length_m"]
    return s


H1 = VALID["hubs"][0]
FIVE_088 = {
    "5088-valid.json": VALID,
    "5088-two-hubs-valid.json": TWO_HUBS,
    "5088-omitted-room.json": _variant(assigned_rooms=["R01", "R02"]),
    "5088-duplicated-node.json": _variant(assigned_nodes=["N1", "N2", "N3", "N2"]),
    "5088-open-ring.json": _variant(
        closed_sequence=["H1", "N3", "N2", "N1"],
        route_segments=H1["route_segments"][:3],
    ),
    "5088-repeated-node.json": _variant(
        closed_sequence=["H1", "N3", "N2", "N1", "N3", "H1"],
        route_segments=[
            *H1["route_segments"][:3],
            leg("N1", "N3", N1, (N1[0], 4.75), (4.75, 4.75), (4.75, N3[1]), N3),
            leg("N3", "H1", N3, (N3[0], 8.0), (3.05, 8.0), HUB),
        ],
    ),
    "5088-ring-ends-at-other-hub.json": submission(
        "PLAN_5088",
        {
            **replace_leg(
                TWO_H1, "N1", "H1", leg("N1", "H2", N1, (N1[0], 3.9), (7.5, 3.9), (7.5, 5.5))
            ),
            "closed_sequence": ["H1", "N3", "N1", "H2"],
        },
        TWO_H2,
    ),
    "5088-off-node-5cm.json": _with_h1(
        replace_leg(
            H1,
            "H1",
            "N3",
            leg(
                "H1",
                "N3",
                HUB,
                (3.05, 4.6),
                (4.6, 4.6),
                (4.6, N3[1] - 0.05),
                (N3[0], N3[1] - 0.05),
            ),
        )
    ),
    "5088-diagonal.json": _with_h1(replace_leg(H1, "N1", "H1", leg("N1", "H1", N1, HUB))),
    "5088-wall-crossing.json": _with_h1(
        replace_leg(
            H1,
            "N2",
            "N1",
            leg("N2", "N1", N2, (6.6, N2[1]), (6.6, 5.0), (2.5, 5.0), (2.5, 3.9), (N1[0], 3.9), N1),
        )
    ),
    "5088-window-crossing.json": _with_h1(
        replace_leg(
            H1,
            "N3",
            "N2",
            leg(
                "N3", "N2", N3, (5.3, N3[1]), (5.3, 9.45), (5.6, 9.45), (5.6, 4.4), (N2[0], 4.4), N2
            ),
        )
    ),
    "5088-hub-0.3m-from-wall.json": _variant(
        location={"x": 3.05, "y": 6.83},
        route_segments=[
            _h1_to_n3(start=(3.05, 6.83)),
            N3_N2,
            N2_N1,
            leg("N1", "H1", N1, (N1[0], 6.83), (3.05, 6.83)),
        ],
    ),
    "5088-hub-in-unassigned-room.json": submission(
        "PLAN_5088",
        TWO_H1,
        {
            **hub(
                "H2",
                "R01",
                (4.9, 4.4),
                ["R02"],
                ["N2"],
                ["H2", "N2", "H2"],
                [
                    leg("H2", "N2", (4.9, 4.4), (N2[0], 4.4), N2),
                    leg("N2", "H2", N2, (8.6, N2[1]), (8.6, 4.2), (4.9, 4.2), (4.9, 4.4)),
                ],
            )
        },
    ),
    "5088-retrace-single-door.json": _with_h1(
        replace_leg(H1, "N3", "N2", leg("N3", "N2", N3, (4.6, N3[1]), (4.6, 4.4), (N2[0], 4.4), N2))
    ),
    "5088-retrace-two-door.json": _with_h1(
        replace_leg(
            H1,
            "N2",
            "N1",
            leg("N2", "N1", N2, (N2[0], N2[1] - 1), (8.6, N2[1] - 1), (8.6, 4.0), (N1[0], 4.0), N1),
        )
    ),
}

# --------------------------------------------------------------------------- synthetic zones

E1, E2 = SYN_NODES["SYN_ENTRANCE"]["N1"], SYN_NODES["SYN_ENTRANCE"]["N2"]
O1, O2 = SYN_NODES["SYN_OVERCAP"]["N1"], SYN_NODES["SYN_OVERCAP"]["N2"]
SYNTHETIC = {
    "syn-entrance-plan.json": SYN_ENTRANCE,
    "syn-entrance-zone.json": submission(
        "PLAN_SYN_ENTRANCE",
        hub(
            "H1",
            "R01",
            (3.0, 3.0),
            ["R01", "R02"],
            ["N1", "N2"],
            ["H1", "N1", "N2", "H1"],
            [
                leg("H1", "N1", (3, 3), (3, 2), E1),
                leg("N1", "N2", E1, (1.2, 2), (1.2, 5.4), (6.6, 5.4), (6.6, 2), E2),
                leg("N2", "H1", E2, (6.2, 5.0), (1.6, 5.0), (1.6, 3.0), (3, 3)),
            ],
        ),
    ),
    "syn-overcap-plan.json": SYN_OVERCAP,
    "syn-overcap-zone.json": submission(
        "PLAN_SYN_OVERCAP",
        hub(
            "H1",
            "R01",
            (3.0, 3.0),
            ["R01", "R02"],
            ["N1", "N2"],
            ["H1", "N1", "N2", "H1"],
            [
                leg("H1", "N1", (3, 3), (4.95, 3), O1),
                leg("N1", "N2", O1, (4.95, 4.2), (15.05, 4.2), O2),
                leg("N2", "H1", O2, (15.05, 4.7), (3, 4.7), (3, 3)),
            ],
        ),
    ),
    "syn-open-plan.json": SYN_OPEN,
}


def _check_nodes() -> None:
    """The verbatim node points must be the ones load_plan places (within 1 µm)."""
    expected = {
        PLAN_5088: {"N1": N1, "N2": N2, "N3": N3},
        **{
            SYNTHETIC[f"syn-{k}-plan.json"]["id"]: v
            for k, v in (
                ("entrance", SYN_NODES["SYN_ENTRANCE"]),
                ("overcap", SYN_NODES["SYN_OVERCAP"]),
            )
        },
    }
    for source, nodes in expected.items():
        plan = load_plan(source if isinstance(source, Path) else _raw_by_id(source))
        got = {n.id: n.xy for n in plan.nodes}
        for nid, xy in nodes.items():
            assert math.dist(got[nid], xy) < 1e-6, (source, nid, got[nid], xy)


def _raw_by_id(plan_id: str) -> dict:
    return next(v for v in SYNTHETIC.values() if v.get("id") == plan_id)


def write(out: Path) -> list[Path]:
    """Write every fixture into `out`; returns the paths written."""
    _check_nodes()
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, obj in {**FIVE_088, **SYNTHETIC}.items():
        path = out / name
        path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8", newline="\n")
        written.append(path)
    return written


if __name__ == "__main__":
    for p in write(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE):
        print(p.name)
