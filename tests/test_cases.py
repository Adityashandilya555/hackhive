"""The committed case library (#84): one small plan per dataset case, and what `solve()` gives."""

from __future__ import annotations

import importlib.util
import json
import shutil
from functools import cache
from pathlib import Path

import pytest
from fixture_solves import solved_path

from hackhive import ui
from hackhive.io import load_plan
from hackhive.solve import solve

ROWS = ui.case_rows()
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_cases.py"
MAX_BYTES = 2_000_000


def _script():
    spec = importlib.util.spec_from_file_location("make_cases", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def _facts(path: Path) -> ui.CaseFacts:
    if (result := solved_path(path)) is None:  # a case plan, not one of the shared fixtures
        plan = load_plan(path)
        result = solve(plan)
    return ui.case_facts(result.plan, result)


def test_every_listed_plan_exists_and_cases_are_known():
    order = [c.id for c in ui.CASES]
    listed = [r["case"] for r in ROWS]
    assert set(listed) <= set(order) and len(set(listed)) == len(listed)
    assert listed == sorted(listed, key=order.index)  # one row per case, in catalogue order
    for row in ROWS:
        assert row["title"] and row["look_for"], row["case"]
        assert ui.case_plan_path(row).is_file(), row["plan"]


def test_every_case_has_a_plan():
    assert {c.id for c in ui.CASES} <= {r["case"] for r in ROWS}


def _params() -> list:
    """One param per row. The grid-failed plan (2332, also `folded`'s) is a degenerate shape that
    makes shapely warn `divide by zero` / `invalid value encountered in buffer`: harmless, so
    silenced there only (#104 review)."""
    regression = {r["plan"] for r in ROWS if r["case"] in ui.REGRESSION_CASES}
    quiet = pytest.mark.filterwarnings("ignore:.*encountered in buffer:RuntimeWarning")
    return [
        pytest.param(r, id=r["case"], marks=quiet if r["plan"] in regression else ()) for r in ROWS
    ]


# strict-only (15333) is the slow row, ~60-90 s: solve() routes every arrangement at H and H + 1
# looking for a clean design (#101). Speeding up `route._best_corners` is the fix, not this test.
@pytest.mark.parametrize("row", _params())
def test_case_plan_solves_as_expected(row):
    facts = _facts(ui.case_plan_path(row))
    if row["case"] not in ui.REGRESSION_CASES:  # e.g. grid failed, once #78 paints it (#84)
        case = next(c for c in ui.CASES if c.id == row["case"])
        assert case.match(facts), (
            f"{row['plan']} no longer shows {row['case']}: re-pick it with the dataset, "
            f"`{_script().DATASET_HINT}`"
        )
    assert facts.expected() == row["expected"], (
        f"solve() changed {row['case']}'s expected; the plan still shows the case, so refresh "
        "cases.json with `uv run scripts/make_cases.py` (no dataset needed)"
    )


def test_library_is_under_2_mb():
    assert sum(p.stat().st_size for p in ui.CASE_DIR.glob("*.json")) < MAX_BYTES


def _snapshot(d: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())}


def _small_set(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    """A fixtures folder with 5088, and a scan of a plan that doesn't load, a line that isn't
    JSON (both skipped), then 5924 (raw, returned)."""
    fixtures, out = tmp_path / "plans", tmp_path / "cases"
    fixtures.mkdir()
    shutil.copy(ui.fixture_path("5088"), fixtures)
    raw_5924 = json.loads(ui.fixture_path("5924").read_text(encoding="utf-8"))
    jsonl = tmp_path / "plans.jsonl"
    jsonl.write_text('{"id": 1}\nnot json\n' + json.dumps(raw_5924) + "\n", encoding="utf-8")
    return jsonl, out, fixtures, raw_5924


def test_script_picks_fixtures_then_the_scan_and_is_idempotent(tmp_path):
    jsonl, out, fixtures, raw_5924 = _small_set(tmp_path)
    make_cases = _script()

    missing = make_cases.build(jsonl, out=out, fixtures=fixtures, n=0)
    first = _snapshot(out)
    rows = {r["case"]: r for r in ui.case_rows(out / "cases.json")}
    assert rows["ok"]["plan"] == "../plans/plan-5088.json"
    assert rows["one_node_ring"]["plan"] == "case-one-node-ring-5924.json"
    assert rows["above_lb_1"]["plan"] == "case-one-node-ring-5924.json"
    assert json.loads(first["case-one-node-ring-5924.json"]) == raw_5924  # verbatim
    assert "grid_failed" in missing and "ok" not in missing

    assert make_cases.build(jsonl, out=out, fixtures=fixtures, n=0) == missing
    assert _snapshot(out) == first


def test_script_repicks_a_named_plan_that_no_longer_shows_its_case(tmp_path):
    """A `solve()` change can move a plan out of its case (#101 made 13693 clean, no longer
    strict-only): the next run picks another plan instead of keeping one that fails
    `test_case_plan_solves_as_expected`."""
    jsonl, out, fixtures, _ = _small_set(tmp_path)
    make_cases = _script()
    make_cases.build(jsonl, out=out, fixtures=fixtures, n=0)
    index = out / "cases.json"
    rows = ui.case_rows(index)
    for row in rows:
        if row["case"] == "one_node_ring":
            row["plan"] = "../plans/plan-5088.json"  # 5088's one ring has 3 nodes
    index.write_text(json.dumps(rows), encoding="utf-8")

    make_cases.build(jsonl, out=out, fixtures=fixtures, n=0)
    rows = {r["case"]: r for r in ui.case_rows(index)}
    assert rows["one_node_ring"]["plan"] == "case-one-node-ring-5924.json"


def test_script_without_a_plans_file_refreshes_expected(tmp_path):
    """#104 review: after a `solve()` change, `make_cases.py` with no `plans.jsonl` keeps every
    named plan that still shows its case and recomputes its `expected`: no dataset needed."""
    jsonl, out, fixtures, _ = _small_set(tmp_path)
    make_cases = _script()
    missing = make_cases.build(jsonl, out=out, fixtures=fixtures, n=0)
    fresh = _snapshot(out)
    index = out / "cases.json"
    rows = ui.case_rows(index)
    rows[0]["expected"]["tags"].append("stale")  # what a solve() change leaves behind
    index.write_text(json.dumps(rows), encoding="utf-8")

    assert make_cases.build(None, out=out, fixtures=fixtures, n=0) == missing
    assert _snapshot(out) == fresh


def test_script_on_two_workers_writes_the_same_library(tmp_path):
    """#103: the scan solved on 2 worker processes picks the same plans and writes the same bytes
    as one process; the plan that doesn't load and the line that isn't JSON are skipped."""
    jsonl, _, fixtures, _ = _small_set(tmp_path)
    make_cases = _script()
    one, two = tmp_path / "one", tmp_path / "two"
    missing = make_cases.build(jsonl, out=one, fixtures=fixtures, n=0, workers=1)
    assert make_cases.build(jsonl, out=two, fixtures=fixtures, n=0, workers=2) == missing
    assert _snapshot(two) == _snapshot(one)


def test_scan_workers_solve_inline(tmp_path, monkeypatch):
    """#103: in a pool each scanned plan calls `solve(..., workers=1)`; one process at a time
    keeps `solve()`'s zone pool (#101)."""
    jsonl, out, fixtures, _ = _small_set(tmp_path)
    make_cases = _script()
    seen = []

    def inline(fn, items, workers=None):
        seen.append(fn.keywords["solve_workers"])
        return [fn(x) for x in items]

    monkeypatch.setattr(make_cases, "map_in_order", inline)
    make_cases.build(jsonl, out=out, fixtures=fixtures, n=0, workers=2)
    assert seen == [1]  # the small set fits one chunk of 2 workers
    seen.clear()
    make_cases.build(jsonl, out=tmp_path / "again", fixtures=fixtures, n=0, workers=1)
    assert seen and set(seen) == {None}  # one plan per call, so nothing is solved past a pick
