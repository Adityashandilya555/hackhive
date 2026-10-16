"""Acceptance tests for #7: the Streamlit shell, its plan picker and the locked preset."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from hackhive.contracts import ORGANISER_WEIGHTS, Constraints, CostWeights
from hackhive.ui import (
    DEFAULT_FIXTURE,
    RESPLAN_JSONL,
    cost_formula,
    fixture_ids,
    plan_from_bytes,
    preset_rows,
    resplan_raw,
)

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")
FIXTURES = ROOT / "tests" / "fixtures"
TIMEOUT = 60  # the first draw of a plan takes a second or two on a cold laptop


def _run(at: AppTest | None = None) -> AppTest:
    at = at or AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _sidebar_text(at: AppTest) -> str:
    sb = at.sidebar
    parts = [m.value for m in sb.markdown] + [c.value for c in sb.caption]
    parts += [h.value for h in sb.subheader] + [h.value for h in sb.header]
    parts += [w.label for w in sb.number_input]
    return "\n".join(parts)


def _main_text(at: AppTest) -> str:
    return "\n".join([t.value for t in at.main.title] + [c.value for c in at.main.caption])


def _images(at: AppTest) -> list:
    return list(at.main.image)


def _upload(at: AppTest, name: str, content: bytes) -> AppTest:
    at.sidebar.radio(key="source").set_value("Upload")
    _run(at)
    at.sidebar.file_uploader(key="upload").set_value((name, content, "application/json"))
    return _run(at)


# --------------------------------------------------------------------------- the app


def test_starts_with_fixture_5924_drawn():
    at = _run()
    assert "Problem statement" in _sidebar_text(at)
    assert "PLAN_5924" in _main_text(at)
    assert len(_images(at)) == 2  # Best's design (solve, #16) and the Plan tab's drawing
    assert not at.error


def test_best_tab_offers_the_presentation_image():
    at = _run()
    labels = [b.proto.label for b in at.main.get("download_button")]
    assert "Download presentation image" in labels  # #44: viz.presentation_png's bytes


def test_problem_statement_is_locked_at_the_organisers_values():
    at = _run()
    # the locked panel's own inputs; #30's What-if expander adds editable ones beside it
    locked = {w.key: w for w in at.sidebar.number_input if w.key.startswith("ps_")}
    assert locked["ps_max_area_m2"].value == 100
    assert locked["ps_max_nodes"].value == 5
    assert locked["ps_hub_wall_clearance_m"].value == pytest.approx(0.5)
    assert locked["ps_tolerance_mm"].value == pytest.approx(1)
    assert len(locked) == 4 and all(w.disabled for w in locked.values())
    text = _sidebar_text(at)
    assert "J = L + 5X + O + 0.5B" in text
    assert "100 m²" in text and "5 nodes" in text


def test_picking_another_fixture_draws_it():
    at = _run()
    at.sidebar.selectbox(key="fixture").set_value("13344")
    _run(at)
    assert "PLAN_13344" in _main_text(at)
    assert len(_images(at)) == 2  # Best's design (solve, #16) and the Plan tab's drawing


def test_uploaded_fixture_is_drawn():
    at = _upload(_run(), "plan-576.json", (FIXTURES / "plans" / "plan-576.json").read_bytes())
    assert "PLAN_576" in _main_text(at)
    assert len(_images(at)) == 2  # Best's design (solve, #16) and the Plan tab's drawing
    assert not at.error


def test_uploaded_vault_file_is_unwrapped_and_drawn():
    raw = (FIXTURES / "raw" / "plan-2814-interior-window.json").read_bytes()
    at = _upload(_run(), "plan-2814-interior-window.json", raw)
    assert "PLAN_2814" in _main_text(at)
    assert len(_images(at)) == 2  # Best's design (solve, #16) and the Plan tab's drawing


@pytest.mark.parametrize(
    "content",
    [b"not json at all", b"[1, 2, 3]", json.dumps({"id": 1, "wall": "nope"}).encode()],
)
def test_bad_upload_shows_an_error_and_never_crashes(content):
    at = _upload(_run(), "bad.json", content)
    assert at.error
    assert not _images(at)


def test_resplan_choice_only_when_the_dataset_is_there():
    at = _run()
    options = at.sidebar.radio(key="source").options
    assert ("ResPlan id" in options) == RESPLAN_JSONL.exists()


# --------------------------------------------------------------------------- helpers (hackhive.ui)


def test_fixture_ids_lists_all_seven_with_5924_the_default():
    ids = fixture_ids()
    assert len(ids) == 7 and DEFAULT_FIXTURE == "5924" and DEFAULT_FIXTURE in ids
    assert ids == sorted(ids, key=int)


def test_cost_formula_comes_from_the_weights():
    assert cost_formula(ORGANISER_WEIGHTS) == "J = L + 5X + O + 0.5B"
    assert cost_formula(CostWeights(1.0, 10.0, 2.0, 1.0)) == "J = L + 10X + 2O + B"


def test_preset_rows_follow_the_constraints():
    rows = dict(preset_rows(Constraints.problem_statement()))
    assert rows["Hub area cap"] == "100 m²"
    assert rows["Nodes per hub"] == "5 nodes"
    assert rows["Cost"] == "J = L + 5X + O + 0.5B"
    assert rows["Hub to wall"] == "≥ 0.5 m"
    assert rows["Tolerance"] == "1 mm"


def test_plan_from_bytes_accepts_a_plan_and_a_vault_file():
    plan = plan_from_bytes((FIXTURES / "plans" / "plan-5088.json").read_bytes())
    assert plan.plan_id == "PLAN_5088"
    vault = plan_from_bytes((FIXTURES / "raw" / "plan-5088-small.json").read_bytes())
    assert vault.plan_id == "PLAN_5088" and len(vault.rooms) == len(plan.rooms)


@pytest.mark.parametrize("content", [b"\xff\xfe", b"{", b'"a string"', b"{}"])
def test_plan_from_bytes_raises_valueerror_on_junk(content):
    with pytest.raises(ValueError):
        plan_from_bytes(content)


def test_resplan_raw_finds_a_plan_by_id(tmp_path):
    lines = [
        json.loads((FIXTURES / "plans" / f"plan-{i}.json").read_text(encoding="utf-8"))
        for i in ("576", "5924")
    ]
    path = tmp_path / "plans.jsonl"
    path.write_text("\n".join(json.dumps(p) for p in lines) + "\n", encoding="utf-8")
    assert str(resplan_raw(path, "5924")["id"]) == "5924"
    assert str(resplan_raw(path, 576)["id"]) == "576"
    with pytest.raises(ValueError, match="not in"):
        resplan_raw(path, "999999")
