"""The batch report (#32): solve every plan of a split with the preset, one CSV row per plan, and
print a summary. The submissions and sidecars go next to the CSV (`out/batch.csv` →
`out/batch/`), through the same gate as `hackhive solve`, so each can be re-checked with
`hackhive check`. A large batch is solved on up to 4 worker processes (`plan_workers`, #103), rows in the source's order.
"""

from __future__ import annotations

import csv
import functools
import gzip
import itertools
import json
import re
import statistics
import sys
import time
import zlib
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from hackhive import __version__
from hackhive.cases import case_tags
from hackhive.contracts import Constraints, Provenance
from hackhive.io import InputFormatError, load_plan
from hackhive.parallel import default_workers, map_in_order
from hackhive.solve import Result, _sidecar, solve, submit
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
    "tags",
]
# the shared sample first (#32 HUMAN step), else the full ResPlan export
DEFAULT_SOURCES = (Path("data/sample/test-100.jsonl.gz"), Path("data/resplan/plans.jsonl"))


def _records(source: Path) -> Iterator[tuple[str, dict | None, str]]:
    """`(where, plan, why)` per record of a `.jsonl` / `.jsonl.gz` file (one plan per line) or a
    directory of plan JSON files (the local test-split export), in file order. An unreadable
    record has `plan` None and `why` saying why, so the batch can row it and carry on."""
    if source.is_dir():
        for p in sorted(source.glob("*.json")):
            yield p.stem, *_parse(p.read_bytes())
        return
    opener = gzip.open if source.suffix == ".gz" else open
    i = 0
    try:
        with opener(source, "rb") as f:
            for i, line in enumerate(f, start=1):
                if line.strip():
                    yield f"line{i}", *_parse(line)
    except (OSError, EOFError, zlib.error) as e:  # a cut-off download: keep the rows read
        yield f"line{i + 1}", None, f"unreadable file ({e})"


def _parse(data: bytes) -> tuple[dict | None, str]:
    try:
        raw = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as e:
        return None, f"not UTF-8 ({e})"
    except json.JSONDecodeError as e:
        return None, f"not JSON ({e})"
    return (raw, "") if isinstance(raw, dict) else (None, "not a JSON object")


def plans(source: Path, split: str) -> Iterator[tuple[str, dict | None, str]]:
    """The split's records, `augmented` copies skipped. The split comes from a `split.json`
    (keyed by ResPlan `id`) beside the source; without one, the source is taken as already that
    split. With one, an unreadable record can't be placed in the split, so it is dropped with a
    warning on stderr."""
    split_file = (source if source.is_dir() else source.parent) / "split.json"
    ids = None
    if split_file.is_file():
        ids = set(json.loads(split_file.read_text(encoding="utf-8"))[split])
    for where, raw, why in _records(source):
        if raw is None:
            if ids is not None:
                print(f"skipped {where}: {why}", file=sys.stderr)
                continue
        elif raw.get("augmented"):
            continue
        elif ids is not None and not (isinstance(raw.get("id"), int | str) and raw["id"] in ids):
            continue
        yield where, raw, why


# ponytail: plans per worker before a plan loop goes parallel by default. With fewer, one big
# plan sets the wall time, and one plan at a time (each `solve` with its zone pool, #101) wins:
# the 7 fixtures take 25.2 s serially vs 37.6 s on 4 plan workers (13344 alone ~34 s on one
# core); 40 sample plans take 82.8 s vs 44.7 s. Schedule big plans first if a batch's tail bites.
MIN_PLANS_PER_WORKER = 4


def plan_workers(workers: int | None, n_plans: int) -> int:
    """How many processes a plan loop (this batch, `make_cases.py`, `grid_scan.py`) solves
    `n_plans` plans on (#103): an explicit `workers`, at most one per plan; by default up to
    `parallel.default_workers()` once each gets `MIN_PLANS_PER_WORKER` plans, else 1. With more
    than one, each plan's `solve` runs with `workers=1`, so pools never nest."""
    if workers is None:
        workers = min(default_workers(), n_plans // MIN_PLANS_PER_WORKER)
    return max(1, min(workers, n_plans))


def _input_error(
    pid: str, reason: str, detail: str, c: Constraints, out_dir: Path, t0: float
) -> dict:
    """An `input_error` row and its sidecar, both with `reason` (a fixed kind, grouped in the
    summary); the detail goes to the sidecar's warnings."""
    prov = Provenance(version=__version__)
    side = _sidecar(pid, c, prov, status="input_error", reason=reason, warnings=[detail])
    submit(Result(None, None, side), out_dir)
    row = dict.fromkeys(COLUMNS, "")
    row.update(plan_id=pid, submitted=False, status="input_error", reason=reason)
    row["ms"] = round((time.perf_counter() - t0) * 1000)
    return row


def _row(
    where: str,
    raw: dict | None,
    why: str,
    c: Constraints,
    out_dir: Path,
    solve_workers: int | None = None,
) -> dict:
    """One record's CSV row. In a pool, `solve_workers=1`: a plan worker never starts `solve`'s
    own zone pool (#103)."""
    t0 = time.perf_counter()
    if raw is None:
        return _input_error(where, f"input_error: {why.split(' (')[0]}", why, c, out_dir, t0)
    try:
        plan = load_plan(raw, c)
    except (InputFormatError, ValueError) as e:
        pid = raw.get("plan_id") or f"PLAN_{raw.get('id')}"
        # io's messages start `plan <id>: ` and may end `, got <value>`: drop both to group them
        kind = re.sub(r"^plan \S+: ", "", str(e)).split(", got ")[0]
        return _input_error(str(pid), f"input_error: {kind}", str(e), c, out_dir, t0)
    result: Result = solve(plan, c, workers=solve_workers)
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
        "tags": _tags(plan, c, result),
    }
    for k in ("H", "L", "X", "O", "B", "J", "area_gap"):
        v = getattr(cost, k) if cost is not None else ""
        row[k] = round(v, 4) if isinstance(v, float) else v
    return row


def _record_row(
    rec: tuple[str, dict | None, str], c: Constraints, out_dir: Path, solve_workers: int | None
) -> dict:
    """`_row` on one `plans()` record; module-level so a worker process can import it (#103)."""
    return _row(*rec, c, out_dir, solve_workers)


def _tags(plan, c: Constraints, result: Result) -> str:
    """`cases.case_tags` ids (stable keys for #33, unlike the labels). A tag failure must not cost
    the plan's row, so it leaves the column empty."""
    try:
        return ";".join(t.id for t in case_tags(plan, result.graph, result))
    except Exception as e:  # noqa: BLE001
        print(f"{result.sidecar.plan_id}: no case tags ({e})", file=sys.stderr)
        return ""


def run(
    source: Path,
    split: str,
    n: int,
    out: Path,
    c: Constraints | None = None,
    *,
    workers: int | None = None,
) -> list[dict]:
    """Solve the first `n` plans of `split` and write `out` (CSV). A plan that fails ends as its
    own row (`infeasible` / `input_error`); the batch always continues. `workers`: processes
    (`plan_workers`: by default up to 4 once there are 4 plans each, else one plan at a
    time, each `solve` with its zone pool); the rows keep the source's order either way."""
    c = c or Constraints()
    out.parent.mkdir(parents=True, exist_ok=True)
    out_dir = out.parent / out.stem
    recs = list(itertools.islice(plans(Path(source), split), n))
    workers = plan_workers(workers, len(recs))
    row = functools.partial(
        _record_row, c=c, out_dir=out_dir, solve_workers=1 if workers > 1 else None
    )
    rows = map_in_order(row, recs, workers)
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
    ms = sorted(r["ms"] for r in loaded)
    if ms:
        p95 = ms[min(len(ms) - 1, round(0.95 * (len(ms) - 1)))]
        lines.append(f"time per plan: p50 {statistics.median(ms):.0f} ms · p95 {p95} ms")
    return "\n".join(lines)


def default_source() -> Path | None:
    return next((p for p in DEFAULT_SOURCES if p.exists()), None)
