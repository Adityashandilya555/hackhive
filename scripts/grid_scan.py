"""Paint the raster for a random sample of ResPlan plans and list every plan it rejects (#78).

    uv run python scripts/grid_scan.py ../../data/resplan/plans.jsonl --n 500 --seed 11
    uv run python scripts/grid_scan.py ../../data/resplan/plans.jsonl --ids 366,509 --solve

A plan is rejected at load (bad input, such as rooms outside `inner`) or by the raster (a door
or passage it can't paint), each with its reason. `--solve` also runs `solve` and prints each
plan's status. Exit code 1 if the raster fails any plan. Independent of the batch report (#32).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

from hackhive.graph import build_graph
from hackhive.grid import LabelGrid
from hackhive.io import load_plan
from hackhive.solve import solve


def _kind(msg: str) -> str:
    """The message without its plan, room, passage ids and numbers, to count alike reasons."""
    msg = msg.split(": ", 1)[1]
    return re.sub(r"\b(R\d+|door_\d+|opening_\d+|\w+_\d+)\b|\d+%", "#", msg)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("plans", type=Path, help="plans.jsonl (one ResPlan plan per line)")
    p.add_argument("--n", type=int, default=500)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--ids", help="comma-separated plan ids to scan instead of a sample")
    p.add_argument("--solve", action="store_true", help="also solve each plan, print its status")
    args = p.parse_args(argv)

    lines = args.plans.read_text().splitlines()
    if args.ids:
        want = set(args.ids.split(","))
        raws = [r for r in map(json.loads, lines) if str(r.get("id")) in want]
    else:
        raws = [json.loads(line) for line in random.Random(args.seed).sample(lines, args.n)]
    rejected: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    for raw in raws:
        pid = str(raw.get("id"))
        try:
            plan = load_plan(raw)
        except ValueError as e:
            rejected.append((pid, str(e)))
            continue
        build_graph(plan)
        try:
            LabelGrid.from_plan(plan)
        except ValueError as e:
            failed.append((pid, str(e)))
        if args.solve:
            side = solve(plan).sidecar
            print(f"{pid}\tsolve: {side.status}" + (f" ({side.reason})" if side.reason else ""))
    for what, rows in (("input error", rejected), ("grid failed", failed)):
        for pid, msg in rows:
            print(f"{pid}\t{what}: {msg}")
    for what, rows in (("input error", rejected), ("grid failed", failed)):
        print(f"{len(rows)} {what}: {dict(Counter(_kind(m) for _, m in rows))}")
    print(f"{len(raws)} plans")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
