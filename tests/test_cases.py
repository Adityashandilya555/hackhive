"""The committed case library (#84): one small plan per dataset case, and what `solve()` gives."""

from __future__ import annotations

import importlib.util
import json
import shutil
from functools import cache
from pathlib import Path

import pytest

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
    plan = load_plan(path)
    return ui.case_facts(plan, solve(plan))


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


@pytest.mark.parametrize("row", ROWS, ids=[r["case"] for r in ROWS])
def test_case_plan_solves_as_expected(row):
    facts = _facts(ui.case_plan_path(row))
    assert facts.expected() == row["expected"]
    if row["case"] != "grid_failed":  # stays a regression example once #78 paints it (#84)
        case = next(c for c in ui.CASES if c.id == row["case"])
        assert case.match(facts), f"{row['plan']} no longer shows {row['case']}"


def test_library_is_under_2_mb():
    assert sum(p.stat().st_size for p in ui.CASE_DIR.glob("*.json")) < MAX_BYTES


def _snapshot(d: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())}


def test_script_picks_fixtures_then_the_scan_and_is_idempotent(tmp_path):
    fixtures, out = tmp_path / "plans", tmp_path / "cases"
    fixtures.mkdir()
    shutil.copy(ui.fixture_path("5088"), fixtures)
    raw_5924 = json.loads(ui.fixture_path("5924").read_text(encoding="utf-8"))
    jsonl = tmp_path / "plans.jsonl"
    jsonl.write_text('{"id": 1}\n' + json.dumps(raw_5924) + "\n", encoding="utf-8")
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
