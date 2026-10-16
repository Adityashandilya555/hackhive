"""The per-plan tasks of the dataset scripts (`scripts/make_cases.py`, `scripts/grid_scan.py`),
run by `parallel.map_in_order` (#103). They live in the package, not the scripts: macOS and
Windows start workers with spawn, which imports a task by module name, and a script loaded by
path (as `tests/test_cases.py` loads `make_cases`) has none."""

from __future__ import annotations

import json
import sys

from hackhive import ui
from hackhive.graph import build_graph
from hackhive.grid import LabelGrid
from hackhive.io import load_plan
from hackhive.solve import solve


def case_facts(text: str, solve_workers: int | None = None) -> ui.CaseFacts | None:
    """A plan's `ui.CaseFacts` from its JSON text (a `plans.jsonl` line or a plan file), or None
    when it isn't JSON or doesn't load: a malformed plan is skipped, never a stop. In a pool,
    `solve_workers=1` (no zone pool inside a plan worker)."""
    try:
        plan = load_plan(ui._unwrap(json.loads(text)))
    except Exception as e:  # malformed dataset plans fail in many ways; skip, don't stop
        print(f"skip: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    return ui.case_facts(plan, solve(plan, workers=solve_workers))


def grid_check(
    raw: dict, solve_plan: bool = False, solve_workers: int | None = None
) -> tuple[str | None, str | None, str | None]:
    """`grid_scan.py`'s check of one ResPlan plan (#78): its load error, its raster error and,
    with `solve_plan`, its `solve` status line; None where there is none."""
    pid = str(raw.get("id"))
    try:
        plan = load_plan(raw)
    except ValueError as e:
        return str(e), None, None
    build_graph(plan)
    try:
        LabelGrid.from_plan(plan)
        failed = None
    except ValueError as e:
        failed = str(e)
    if not solve_plan:
        return None, failed, None
    side = solve(plan, workers=solve_workers).sidecar
    return (
        None,
        failed,
        f"{pid}\tsolve: {side.status}" + (f" ({side.reason})" if side.reason else ""),
    )
