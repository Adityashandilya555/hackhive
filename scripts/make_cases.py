"""Pick one small ResPlan plan per dataset case and write the case library (#84).

    uv run scripts/make_cases.py ~/HACKHIVE/data/resplan/plans.jsonl   # pick (needs the dataset)
    uv run scripts/make_cases.py                                        # refresh `expected` only

Without `plans.jsonl` there's no scan (step 3): every named plan that still shows its case is
kept and its `expected` recomputed, which is all a `solve()` change needs, unless a plan stops
showing its case. Writes `tests/fixtures/cases/cases.json`, one row per case in `ui.CASES` with its plan and what
`solve()` gives, and a verbatim `case-<tag>-<id>.json` for each case that no committed fixture
(`tests/fixtures/plans/`) shows. Each case's plan is, in order:

1. the plan `cases.json` already names, if its file exists and it still shows the case: a
   `solve()` change can move a plan out of its case (#101 made 13693 clean, no longer
   strict-only). The grid-failed plan is kept regardless, so #78 can flip its status without
   losing the regression example (`ui.REGRESSION_CASES`);
2. the smallest fixture that shows the case;
3. the smallest plan of the scan that shows it: the same 600 plans as the plan's §4 scan
   (`random.Random(5).sample`, `--n 600 --seed 5`; `--n 0` scans every plan).

"Smallest" is the plan's file (or JSON line) size, then its id. `expected` is recomputed on
every run, so running it twice changes nothing. Exit code 1 if a case is left without a plan.

The scan solves its plans on `--workers` processes (default: `hackhive.batch.plan_workers`,
up to 4; #103), in ordered chunks of 4 × workers, and applies the picks in input order: the same picks as
one process.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import random
import sys
from pathlib import Path

from hackhive import scans, ui
from hackhive.batch import plan_workers
from hackhive.parallel import map_in_order

_SOLVED: dict[Path, ui.CaseFacts | None] = {}
DATASET_HINT = "uv run scripts/make_cases.py ~/HACKHIVE/data/resplan/plans.jsonl"


def _facts(path: Path) -> ui.CaseFacts | None:
    """A plan file's case facts, or None when it doesn't load (an `input_error` is no case here).
    A file is solved once per run, however many cases name it."""
    key = path.resolve()
    if key not in _SOLVED:
        _SOLVED[key] = scans.case_facts(key.read_text(encoding="utf-8"))
    return _SOLVED[key]


def _id(line: str) -> str:
    """A JSON line's plan id, or "" for a line that isn't a plan (the scan's task skips it)."""
    try:
        return str(ui._unwrap(json.loads(line)).get("id"))
    except Exception:  # not JSON, or not an object
        return ""


def _rel(path: Path, base: Path) -> str:
    return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()


def _scan(plans: Path, n: int, seed: int) -> list[str]:
    """The scan's JSON lines, smallest first (then by id). Streams the file twice, so the
    full dataset never sits in memory; the sample is `random.Random(seed).sample(lines, n)`."""
    with plans.open(encoding="utf-8") as f:
        total = sum(1 for line in f if line.strip())
    keep = (
        set(range(total))
        if n <= 0 or n >= total
        else set(random.Random(seed).sample(range(total), n))
    )
    picked: list[tuple[int, str, str]] = []
    with plans.open(encoding="utf-8") as f:
        i = 0
        for line in f:
            if not line.strip():
                continue
            if i in keep:
                line = line.strip()
                picked.append((len(line), _id(line), line))
            i += 1
    picked.sort(key=lambda t: (t[0], int(t[1]) if t[1].isdigit() else 0, t[1]))
    return [line for *_, line in picked]


def build(
    plans: Path | None,
    *,
    out: Path = ui.CASE_DIR,
    fixtures: Path = ui.FIXTURE_DIR,
    n: int = 600,
    seed: int = 5,
    workers: int | None = None,
) -> list[str]:
    """Write `out/cases.json` and the picked case plans; return the cases left without a plan."""
    out.mkdir(parents=True, exist_ok=True)
    index = out / "cases.json"
    old = {r["case"]: r["plan"] for r in ui.case_rows(index)} if index.exists() else {}
    picks: dict[str, tuple[Path, ui.CaseFacts]] = {}

    for case in ui.CASES:  # 1. keep what cases.json already names, if it still shows the case
        if case.id in old and (path := out / old[case.id]).exists():
            facts = _facts(path)
            if facts is not None and (case.match(facts) or case.id in ui.REGRESSION_CASES):
                picks[case.id] = (path, facts)

    def take(path: Path, facts: ui.CaseFacts) -> None:
        for case in ui.CASES:
            if case.id not in picks and case.match(facts):
                picks[case.id] = (path, facts)

    # 2. the committed fixtures, smallest first
    for path in sorted(fixtures.glob("plan-*.json"), key=lambda p: (p.stat().st_size, p.name)):
        if len(picks) == len(ui.CASES):
            break
        if (facts := _facts(path)) is not None:
            take(path, facts)

    # 3. the scan, smallest first; each plan picked is written verbatim, named for its first case
    if plans is not None and len(picks) < len(ui.CASES):
        lines = _scan(plans, n, seed)
        size = 4 * plan_workers(workers, len(lines))
        for start in range(0, len(lines), size):
            chunk = lines[start : start + size]
            print(f"[{start + 1}-{start + len(chunk)}/{len(lines)}]", file=sys.stderr)
            procs = plan_workers(workers, len(chunk))  # > 1: no zone pool inside a plan worker
            task = functools.partial(scans.case_facts, solve_workers=1 if procs > 1 else None)
            for line, facts in zip(chunk, map_in_order(task, chunk, procs), strict=True):
                missing = [c for c in ui.CASES if c.id not in picks]
                if not missing:
                    break
                first = (
                    None if facts is None else next((c for c in missing if c.match(facts)), None)
                )
                if first is None:
                    continue
                raw = ui._unwrap(json.loads(line))
                path = out / f"case-{first.id.replace('_', '-')}-{raw.get('id')}.json"
                path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
                take(path, facts)
            if len(picks) == len(ui.CASES):
                break

    rows = []
    for case in ui.CASES:
        if case.id not in picks:
            continue
        path, facts = picks[case.id]
        title, look_for = case.describe(facts)
        rows.append(
            {
                "case": case.id,
                "title": title,
                "look_for": look_for,
                "plan": _rel(path, out),
                "expected": facts.expected(),
            }
        )
    index.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    used = {(out / r["plan"]).resolve() for r in rows}
    for stale in out.glob("case-*.json"):  # plans no case names any more
        if stale.resolve() not in used:
            stale.unlink()
    return [c.id for c in ui.CASES if c.id not in picks]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "plans",
        type=Path,
        nargs="?",
        help="plans.jsonl (one ResPlan plan per line); omit to refresh `expected` with no scan",
    )
    p.add_argument("--n", type=int, default=600, help="plans in the scan (0: all)")
    p.add_argument("--seed", type=int, default=5)
    p.add_argument("--out", type=Path, default=ui.CASE_DIR)
    p.add_argument("--fixtures", type=Path, default=ui.FIXTURE_DIR)
    p.add_argument("--workers", type=int, help="processes for the scan (default: up to 4)")
    args = p.parse_args(argv)
    missing = build(
        args.plans,
        out=args.out,
        fixtures=args.fixtures,
        n=args.n,
        seed=args.seed,
        workers=args.workers,
    )
    rows = ui.case_rows(args.out / "cases.json")
    size = sum(f.stat().st_size for f in args.out.glob("*.json"))
    print(f"{len(rows)} cases, {size / 1e6:.2f} MB in {args.out}")
    for row in rows:
        print(f"  {row['case']:<16} {row['plan']:<40} {row['expected']['status']}")
    if missing:
        print(f"no plan for: {', '.join(missing)}", file=sys.stderr)
        if args.plans is None:
            print(f"re-pick them with the dataset: {DATASET_HINT}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
