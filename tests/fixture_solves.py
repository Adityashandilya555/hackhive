"""Each committed fixture plan solved once per test session: solving is the suite's slowest step
(13344 takes ~28 s on 4 cores), and a dozen test files used to solve the same seven plans again.

`solved("5924")` is `solve_file` on `fixtures/plans/plan-5924.json` under the problem statement.
`Result` is frozen and no test changes it, so every test that reads an unmodified fixture's design
under the default settings shares one. A test that changes the plan, the settings or the solver
(a monkeypatch, a hub hook, a what-if, a determinism check) solves its own.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from hackhive.solve import Result, solve_file

PLANS = Path(__file__).resolve().parent / "fixtures" / "plans"


@cache
def solved(plan_id: str) -> Result:
    return solve_file(PLANS / f"plan-{plan_id}.json")


def solved_path(path: Path) -> Result | None:
    """`solved` for a path that is one of the fixture plans, else None (solve it yourself)."""
    path = Path(path).resolve()
    if path.parent == PLANS and path.name.startswith("plan-"):
        return solved(path.stem.removeprefix("plan-"))
    return None
