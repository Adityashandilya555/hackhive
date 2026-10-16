"""Acceptance tests for #32: `hackhive batch` over a split, the CSV report and its summary."""

import csv
import gzip
import json
from pathlib import Path

from shapely.geometry import MultiPolygon, box, mapping

from hackhive import batch, cli
from hackhive.checker import report
from hackhive.contracts import Constraints, Submission
from hackhive.io import load_plan

PLANS = Path(__file__).resolve().parent / "fixtures" / "plans"
FIXTURE_IDS = [5088, 3086, 2814]  # 3 fixtures, as the issue's test plan asks
C = Constraints()


def _over_cap() -> dict:
    """A 12 × 10 m bedroom (1 unit = 0.1 m): no hub may serve it (C27)."""
    return {
        "id": 900001,
        "area": 150.0,
        "wall_depth": 2.0,
        "inner": mapping(box(0, 0, 150, 100)),
        "bedroom": mapping(box(0, 0, 120, 100)),
        "kitchen": mapping(box(122, 0, 150, 100)),
        "wall": mapping(MultiPolygon([box(120, 0, 122, 40), box(120, 50, 122, 100)])),
        "door": mapping(box(120, 40, 122, 50)),
    }


def _sample(tmp_path: Path) -> Path:
    """A gzipped JSONL source with its `split.json`: 3 fixtures + 1 infeasible plan in test, one
    plan in train and one augmented copy, both of which the batch must skip."""
    raws = [json.loads((PLANS / f"plan-{i}.json").read_text("utf-8")) for i in FIXTURE_IDS]
    train = {**raws[0], "id": 800001}
    augmented = {**raws[1], "id": 800002, "augmented": True}
    records = [raws[0], _over_cap(), *raws[1:], train, augmented]  # infeasible one 2nd
    src = tmp_path / "plans.jsonl.gz"
    with gzip.open(src, "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    split = {"train": [800001], "test": [*FIXTURE_IDS, 900001, 800002]}
    (tmp_path / "split.json").write_text(json.dumps(split), encoding="utf-8")
    return src


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_a_run_writes_the_listed_columns_and_prints_the_summary(tmp_path, capsys):
    """C22: the run completes, the CSV has exactly the issue's columns, the summary prints."""
    out = tmp_path / "out" / "batch.csv"
    code = cli.main(
        [
            "batch",
            "--split",
            "test",
            "--n",
            "20",
            "--plans",
            str(_sample(tmp_path)),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    with out.open(newline="", encoding="utf-8") as f:
        assert next(csv.reader(f)) == batch.COLUMNS
    assert len(_rows(out)) == 4  # train and augmented skipped
    text = capsys.readouterr().out
    for words in ("valid", "clean", "submitted_strict_only", "at LB", "mean J", "p50", "p95"):
        assert words in text


def test_an_infeasible_plan_is_a_row_and_the_batch_continues(tmp_path):
    """C27: the 120 m² room gives `status=infeasible` with its reason; later plans still run."""
    out = tmp_path / "batch.csv"
    rows = batch.run(_sample(tmp_path), "test", 20, out, C)
    bad = next(r for r in rows if r["plan_id"] == "PLAN_900001")
    assert bad["status"] == "infeasible" and bad["reason"] and bad["submitted"] is False
    assert len(rows) == 4  # the plans after it still ran
    assert {r["status"] for r in rows if r is not bad} <= {"ok", "submitted_strict_only"}


def test_two_runs_give_the_same_csv_apart_from_ms(tmp_path):
    """C16, #103: deterministic, inline or on 2 worker processes, so only the timing column may
    differ, and the rows keep the source's order."""
    src = _sample(tmp_path)
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    batch.run(src, "test", 20, a, C, workers=1)
    batch.run(src, "test", 20, b, C, workers=2)
    strip = [{k: v for k, v in r.items() if k != "ms"} for r in _rows(a)]
    assert strip == [{k: v for k, v in r.items() if k != "ms"} for r in _rows(b)]


def test_every_feasible_plan_has_a_submission_that_passes_the_checker(tmp_path):
    """C43: each feasible row's written submission is valid under `hackhive check`'s report."""
    src = _sample(tmp_path)
    out = tmp_path / "batch.csv"
    rows = batch.run(src, "test", 20, out, C)
    for r in rows:
        if r["status"] == "infeasible":
            assert r["reason"]
            continue
        assert r["submitted"] is True
        sub = Submission.model_validate_json(
            (out.parent / "batch" / f"{r['plan_id']}.json").read_text("utf-8")
        )
        pid = int(r["plan_id"].removeprefix("PLAN_"))
        plan = load_plan(PLANS / f"plan-{pid}.json", C)
        assert report(plan, sub, C).verdict != "invalid"


def test_n_limits_the_plans_taken(tmp_path):
    rows = batch.run(_sample(tmp_path), "test", 2, tmp_path / "b.csv", C)
    assert [r["plan_id"] for r in rows] == ["PLAN_5088", "PLAN_900001"]


def test_a_directory_of_plan_files_is_a_source_too(tmp_path):
    """The local test-split export (`data/resplan/test/`) is one plan file per plan."""
    d = tmp_path / "plans"
    d.mkdir()
    for i in FIXTURE_IDS[:2]:
        (d / f"plan-{i}.json").write_bytes((PLANS / f"plan-{i}.json").read_bytes())
    rows = batch.run(d, "test", 20, tmp_path / "b.csv", C)
    assert sorted(r["plan_id"] for r in rows) == ["PLAN_3086", "PLAN_5088"]


def test_a_plan_worker_solves_inline_and_a_serial_run_keeps_the_zone_pool(tmp_path, monkeypatch):
    """#103: in a pool each plan calls `solve(..., workers=1)`, so 4 plan workers don't each
    start 4 zone workers; one process at a time keeps `solve()`'s default (#101)."""
    seen = []

    def inline(fn, items, workers=None):
        seen.append(fn.keywords["solve_workers"])
        return [fn(x) for x in items]

    monkeypatch.setattr(batch, "map_in_order", inline)
    src = _sample(tmp_path)
    batch.run(src, "test", 2, tmp_path / "a.csv", C, workers=2)
    batch.run(src, "test", 2, tmp_path / "b.csv", C, workers=1)
    assert seen == [1, None]


def test_a_small_batch_runs_one_plan_at_a_time_by_default():
    """#103: plan workers only once each gets 4 plans; fewer, and one big plan sets the wall time,
    so one plan at a time with `solve`'s zone pool wins (7 fixtures: 25.2 s vs 37.6 s). An
    explicit `workers` is used as given, at most one per plan."""
    from hackhive.parallel import default_workers

    assert batch.plan_workers(None, 7) == 1
    assert batch.plan_workers(None, 8) == min(default_workers(), 2)
    assert batch.plan_workers(None, 40) == default_workers()
    assert batch.plan_workers(2, 3) == 2
    assert batch.plan_workers(4, 2) == 2
