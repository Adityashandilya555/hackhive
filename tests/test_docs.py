"""#34: the output-format doc and README match what the code writes, and the writer rounds (T6)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import typing
from itertools import pairwise
from pathlib import Path

import jsonschema
import pytest

from hackhive import cli, solve
from hackhive.checker import check_rules, reported_mismatches
from hackhive.contracts import Constraints, Sidecar, Submission
from hackhive.io import load_plan

ROOT = Path(__file__).resolve().parents[1]
PLANS = ROOT / "tests" / "fixtures" / "plans"
DOC = ROOT / "docs" / "output-format.md"
C = Constraints()
PLAN_PATHS = sorted(PLANS.glob("plan-*.json"))


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _example() -> dict:
    """The first ```json block after the `<!-- example: 5924 -->` marker."""
    found = re.search(r"<!-- example: 5924 -->\s*```json\n(.*?)\n```", _doc(), re.S)
    assert found, "docs/output-format.md has no example block after <!-- example: 5924 -->"
    return json.loads(found.group(1))


# --------------------------------------------------------------------------- the example


def test_the_example_parses_and_matches_the_schema():
    """C24, C48: the doc's example is a `Submission` and passes schemas/submission.schema.json."""
    raw = _example()
    Submission.model_validate(raw)
    schema = json.loads((ROOT / "schemas" / "submission.schema.json").read_text("utf-8"))
    jsonschema.validate(raw, schema)


def test_the_example_passes_hackhive_check_on_5924(tmp_path, capsys):
    path = tmp_path / "example.json"
    path.write_text(json.dumps(_example()), encoding="utf-8")
    assert cli.main(["check", str(PLANS / "plan-5924.json"), str(path)]) == 0


# --------------------------------------------------------------------------- T6 rounding


def _decimals(v: float) -> int:
    text = repr(v)
    return len(text.split(".")[1]) if "." in text and "e" not in text else 0


@pytest.mark.parametrize("path", PLAN_PATHS, ids=lambda p: p.stem)
def test_the_writer_rounds_to_4_decimals_and_stays_axis_aligned(path, monkeypatch):
    """T6, C24, C48: ≤ 4 decimals everywhere, every piece exactly horizontal or vertical, the
    reported values still match, no new `error`, and J moves by less than 1e-3."""
    result = solve.solve_file(path, C)
    sub = result.submission
    for h in sub.hubs:
        assert _decimals(h.location.x) <= 4 and _decimals(h.location.y) <= 4
        for s in h.route_segments:
            for x, y in s.polyline:
                assert _decimals(x) <= 4 and _decimals(y) <= 4, (h.hub_id, s.from_, x, y)
            for (ax, ay), (bx, by) in pairwise(s.polyline):
                assert ax == bx or ay == by, (h.hub_id, s.from_, (ax, ay), (bx, by))
    assert reported_mismatches(sub, C) == []
    assert not [v for v in check_rules(load_plan(path, C), sub, C) if v.severity == "error"]

    monkeypatch.setattr(solve, "_snap4", list)  # the writer without rounding: J before T6
    before = solve.solve_file(path, C).sidecar.cost.J
    assert abs(result.sidecar.cost.J - before) < 1e-3


# --------------------------------------------------------------------------- statuses + warnings

# How each warning `solve.submit` can emit starts or reads (decision_conventions; io, rings, C42)
WARNING_MARKERS = [
    "clearance_m < 0.5",  # node clearance below slide 2's 0.5 m
    "folded into",  # C38 living slivers
    "wall_clearance_only",  # hub only 0.51 m from walls
    "recomputed",  # C42 reported-value mismatch
    "WHAT-IF",  # #30
    "isn't a safe file name",  # file_stem notes
    "greedy_zoning",  # #49, planned
]


def _status_literals() -> set[str]:
    found = set(typing.get_args(Sidecar.model_fields["status"].annotation))
    for py in (ROOT / "src" / "hackhive").glob("*.py"):
        found |= set(re.findall(r'status="(\w+)"', py.read_text(encoding="utf-8")))
    return found


def test_every_status_and_warning_is_documented():
    """C24, C43: a status or warning the code can emit and the doc doesn't name fails here."""
    doc = _doc()
    assert {"ok", "submitted_strict_only", "infeasible", "input_error"} <= _status_literals()
    missing = [s for s in sorted(_status_literals()) if f"`{s}`" not in doc]
    missing += [w for w in WARNING_MARKERS if w not in doc]
    assert not missing, f"undocumented in docs/output-format.md: {missing}"


# --------------------------------------------------------------------------- README commands


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "hackhive.cli", *args], capture_output=True, text=True, cwd=ROOT
    )


def test_the_readme_commands_run(tmp_path):
    """C22: the README's solve and check commands work, with the exit codes it lists."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for cmd in ("uv sync", "uv run hackhive solve", "uv run hackhive check", "streamlit run app"):
        assert cmd in readme, cmd
    assert "CC BY 4.0" in readme
    plan = PLANS / "plan-5924.json"
    solved = _run("solve", str(plan), "--out", str(tmp_path))
    assert solved.returncode == 0, solved.stderr
    sub = next(p for p in tmp_path.glob("*.json") if not p.name.endswith(".sidecar.json"))
    checked = _run("check", str(plan), str(sub))
    assert checked.returncode == 0, checked.stdout + checked.stderr
