"""The batch report (#32): solve every plan of a split with the preset, one CSV row per plan, and
print a summary. The submissions and sidecars go next to the CSV (`out/batch.csv` →
`out/batch/`), through the same gate as `hackhive solve`, so each can be re-checked with
`hackhive check`. Runs sequentially (parallel execution is out of scope).
"""

from __future__ import annotations

import csv
import gzip
import json
import statistics
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from hackhive.contracts import Constraints
from hackhive.io import InputFormatError, load_plan
from hackhive.solve import Result, solve, submit
from hackhive.zoning import eligible_area_and_nodes

COLUMNS = [
    "plan_id",
    "n_nodes",
    "area_m2",
    "k_lb",
    "H",
    "L",
    "X",
    "O",
    "B",
    "J",
    "area_gap",
    "submitted",
    "rules_failed",
    "fallback_used",
    "ms",
    "status",
    "reason",
]
# the shared sample first (#32 HUMAN step), else the full ResPlan export
DEFAULT_SOURCES = (Path("data/sample/test-100.jsonl.gz"), Path("data/resplan/plans.jsonl"))


def _records(source: Path) -> Iterator[dict]:
    """Raw plans from a `.jsonl` / `.jsonl.gz` file (one plan per line) or a directory of plan
    JSON files (the local test-split export), in file order."""
    if source.is_dir():
        for p in sorted(source.glob("*.json")):
            yield json.loads(p.read_text(encoding="utf-8"))
        return
    opener = gzip.open if source.suffix == ".gz" else open
    with opener(source, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def plans(source: Path, split: str) -> Iterator[dict]:
    """The split's plans, `augmented` copies skipped. The split comes from a `split.json` (keyed
    by ResPlan `id`) beside the source; without one, the source is taken as already that split."""
    split_file = (source if source.is_dir() else source.parent) / "split.json"
    ids = None
    if split_file.is_file():
        ids = set(json.loads(split_file.read_text(encoding="utf-8"))[split])
    for raw in _records(source):
        if raw.get("augmented"):
            continue
        if ids is None or raw.get("id") in ids:
            yield raw


def _row(raw: dict, c: Constraints, out_dir: Path) -> dict:
    t0 = time.perf_counter()
    try:
        plan = load_plan(raw, c)
    except (InputFormatError, ValueError) as e:
        row = dict.fromkeys(COLUMNS, "")
        pid = raw.get("plan_id") or f"PLAN_{raw.get('id')}"
        row.update(plan_id=pid, submitted=False, status="input_error", reason=f"input_error: {e}")
        row["ms"] = round((time.perf_counter() - t0) * 1000)
        return row
    result: Result = solve(plan, c)
    submit(result, out_dir)
    ms = round((time.perf_counter() - t0) * 1000)
    side = result.sidecar
    area, nodes = eligible_area_and_nodes(plan)
    cost = side.cost
    row = {
        "plan_id": side.plan_id,
        "n_nodes": sum(nodes.values()),
        "area_m2": round(sum(area.values()), 4),
        "k_lb": side.bounds.k_lb if side.bounds else "",
        "submitted": result.submittable,
        "rules_failed": ";".join(str(r) for r in sorted({v.rule for v in side.violations})),
        "fallback_used": any(w.endswith("wall_clearance_only") for w in side.warnings),
        "ms": ms,
        "status": side.status,
        "reason": side.reason or "",
    }
    for k in ("H", "L", "X", "O", "B", "J", "area_gap"):
        v = getattr(cost, k) if cost is not None else ""
        row[k] = round(v, 4) if isinstance(v, float) else v
    return row


def run(source: Path, split: str, n: int, out: Path, c: Constraints | None = None) -> list[dict]:
    """Solve the first `n` plans of `split` and write `out` (CSV). A plan that fails ends as its
    own row (`infeasible` / `input_error`); the batch always continues."""
    c = c or Constraints()
    out.parent.mkdir(parents=True, exist_ok=True)
    out_dir = out.parent / out.stem
    rows = []
    for raw in plans(Path(source), split):
        if len(rows) >= n:
            break
        rows.append(_row(raw, c, out_dir))
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return rows


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.1f}%)" if n else "0/0"


def summary(rows: list[dict]) -> str:
    """The issue's summary. "Feasible" = every plan that loaded and isn't `infeasible`, so a
    plan our solver fails on counts against the validity rate unless its reason says why."""
    loaded = [r for r in rows if r["status"] != "input_error"]
    clean = [r for r in rows if r["status"] == "ok" and r["submitted"]]
    strict = [r for r in rows if r["status"] == "submitted_strict_only" and r["submitted"]]
    done = clean + strict
    infeasible = [r for r in rows if r["status"] == "infeasible"]
    feasible = len(loaded) - len(infeasible)
    lines = [
        f"plans: {len(rows)} · loaded {len(loaded)} · infeasible {len(infeasible)}"
        f" · input_error {len(rows) - len(loaded)}",
        f"valid submissions: {_pct(len(done), feasible)} of feasible plans"
        f" (clean {len(clean)}, submitted_strict_only {len(strict)})",
        f"at LB (H == k_lb): {_pct(sum(r['H'] == r['k_lb'] for r in done), len(done))}",
    ]
    if done:
        mean = {k: statistics.fmean(r[k] for r in done) for k in ("J", "L", "X", "O", "B")}
        lines.append(
            f"mean J {mean['J']:.3f} = L {mean['L']:.3f} · X {mean['X']:.3f}"
            f" · O {mean['O']:.3f} · B {mean['B']:.3f}"
        )
    reasons = Counter(r["reason"] for r in rows if r["status"] in ("infeasible", "input_error"))
    lines += [f"  {k}× {why}" for why, k in reasons.most_common()]
    ms = sorted(r["ms"] for r in rows)
    if ms:
        p95 = ms[min(len(ms) - 1, round(0.95 * (len(ms) - 1)))]
        lines.append(f"time per plan: p50 {statistics.median(ms):.0f} ms · p95 {p95} ms")
    return "\n".join(lines)


def default_source() -> Path | None:
    return next((p for p in DEFAULT_SOURCES if p.exists()), None)
