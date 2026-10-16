"""Write tests/fixtures/organiser/guess-5924.json: plan 5924 in a *guessed* organiser shape (#38).

The organisers haven't shown their input format (#46 Q1). This rewrites the ResPlan fixture
into a plausible one, a `rooms` list with ids, a `nodes` list and a slide-8-style `plan_id`,
only to exercise the adapter's mechanism. Run: uv run python scripts/make_organiser_guess.py
"""

import json
from pathlib import Path

from shapely.geometry import mapping

from hackhive.io import ELIGIBLE, EXCLUDED, _polygons, load_plan

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tests" / "fixtures" / "plans" / "plan-5924.json"
OUT = ROOT / "tests" / "fixtures" / "organiser" / "guess-5924.json"
# input order on purpose differs from load_plan's R-numbering, so ids are visibly verbatim
ORDER = ("bedroom", "kitchen", "living", "storage", "bathroom", "balcony")


def main() -> None:
    raw = json.loads(SRC.read_text(encoding="utf-8"))
    plan = load_plan(raw)
    m = plan.m_per_unit
    rooms, ids = [], {}
    for kind in ORDER:
        for i, poly in enumerate(_polygons(raw.get(kind))):
            rid = f"R{len(rooms) + 1:02d}"
            ids[f"{kind}_{i}"] = rid
            rooms.append({"id": rid, "type": kind, "polygon": mapping(poly)})
    assert set(ids) >= {r.label for r in plan.rooms if r.type in ELIGIBLE}
    label_of = {r.id: r.label for r in plan.rooms}
    nodes = [
        {"id": f"N{k}", "room_id": ids[label_of[n.room_id]], "x": n.xy[0] / m, "y": n.xy[1] / m}
        for k, n in enumerate(sorted(plan.nodes, key=lambda n: ids[label_of[n.room_id]]), 1)
    ]
    guess = {
        "plan_id": "PLAN_5924",
        "area": raw["area"],
        "wall_depth": raw["wall_depth"],
        "inner": raw["inner"],
        "walls": raw["wall"],
        "doors": raw["door"],
        "windows": raw["window"],
        "front_door": raw["front_door"],
        "rooms": rooms,
        "nodes": nodes,
    }
    assert not set(EXCLUDED) & set(guess)
    OUT.write_text(json.dumps(guess, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rooms)} rooms, {len(nodes)} nodes")


if __name__ == "__main__":
    main()
