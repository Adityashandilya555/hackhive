"""Paint the raster for a random sample of ResPlan plans and list every plan it rejects (#78).

    uv run python scripts/grid_scan.py ../../data/resplan/plans.jsonl --n 500 --seed 11
    uv run python scripts/grid_scan.py ../../data/resplan/plans.jsonl --ids 366,509 --solve

A plan is rejected at load (bad input, such as rooms outside `inner`) or by the raster (a door
or passage it can't paint), each with its reason. `--solve` also runs `solve` and prints each
plan's status. Exit code 1 if the raster fails any plan. Independent of the batch report (#32).
Plans are checked on `--workers` processes (default: `hackhive.batch.plan_workers`; #103),
printed in sample order.
"""

from __future__ import annotations

import argparse
import functools
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

from hackhive.batch import plan_workers
from hackhive.parallel import map_in_order
from hackhive.scans import grid_check


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
    p.add_argument("--workers", type=int, help="processes (default: up to 4; 1: one at a time)")
    args = p.parse_args(argv)

    lines = args.plans.read_text().splitlines()
    if args.ids:
        want = set(args.ids.split(","))
        raws = [r for r in map(json.loads, lines) if str(r.get("id")) in want]
    else:
        raws = [json.loads(line) for line in random.Random(args.seed).sample(lines, args.n)]
    rejected: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    procs = plan_workers(args.workers, len(raws))  # > 1: no zone pool inside a plan worker
    check = functools.partial(
        grid_check, solve_plan=args.solve, solve_workers=1 if procs > 1 else None
    )
    for raw, (load_error, grid_error, solved) in zip(
        raws, map_in_order(check, raws, procs), strict=True
    ):
        pid = str(raw.get("id"))
        if load_error is not None:
            rejected.append((pid, load_error))
        if grid_error is not None:
            failed.append((pid, grid_error))
        if solved is not None:
            print(solved)
    for what, rows in (("input error", rejected), ("grid failed", failed)):
        for pid, msg in rows:
            print(f"{pid}\t{what}: {msg}")
    for what, rows in (("input error", rejected), ("grid failed", failed)):
        print(f"{len(rows)} {what}: {dict(Counter(_kind(m) for _, m in rows))}")
    print(f"{len(raws)} plans")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
