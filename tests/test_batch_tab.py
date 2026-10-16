"""Acceptance tests for #33: the Batch tab reads #32's CSV (never solving in Streamlit) and shows
its rows, summary cards, a histogram of H − LB and the plans per case tag."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from hackhive import ui
from hackhive.ui import (
    BATCH_COLUMNS,
    BATCH_COMMAND,
    batch_summary,
    find_batch_csv,
    hub_gap_counts,
    open_target,
    plans_by_tag,
    read_batch,
)
from hackhive.viz import draw_gap_histogram

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")
SMALL = ROOT / "tests" / "fixtures" / "batch" / "batch-small.csv"
TIMEOUT = 120


# --------------------------------------------------------------------------- the CSV


def test_the_fixture_has_32s_columns():
    """#32's spec lists the columns; `tags` (#83's case tags) is the one optional extra."""
    rows = read_batch(SMALL)
    assert len(rows) == 9
    assert set(BATCH_COLUMNS) <= set(rows[0])


def test_the_columns_are_32s_own_list():
    """One list: the tab reads exactly what `hackhive batch` writes (#106 review)."""
    from hackhive import batch

    assert tuple(batch.COLUMNS) == BATCH_COLUMNS


def test_the_tab_reads_what_hackhive_batch_writes(tmp_path):
    """End to end: #32's runner on a fixture, read back by the tab's reader."""
    from hackhive import batch
    from hackhive.contracts import Constraints

    plans = tmp_path / "plans"
    plans.mkdir()
    src = ROOT / "tests" / "fixtures" / "plans" / "plan-5088.json"
    (plans / src.name).write_bytes(src.read_bytes())
    out = tmp_path / "out" / "batch.csv"
    written = batch.run(plans, "test", 5, out, Constraints())
    rows = read_batch(out)
    assert [r["plan_id"] for r in rows] == [w["plan_id"] for w in written] == ["PLAN_5088"]
    assert rows[0]["submitted"] is True and rows[0]["J"] == pytest.approx(written[0]["J"])
    assert batch_summary(rows).valid_pct == 100


def test_read_batch_types_the_values():
    rows = {r["plan_id"]: r for r in read_batch(SMALL)}
    ok = rows["PLAN_5924"]
    assert ok["submitted"] is True and isinstance(ok["J"], float) and isinstance(ok["H"], int)
    big = rows["PLAN_BIG"]
    assert big["submitted"] is False and big["H"] is None and big["J"] is None
    assert big["status"] == "infeasible" and "120.0 m²" in big["reason"]


def test_a_csv_without_32s_columns_says_which_are_missing(tmp_path):
    bad = tmp_path / "batch.csv"
    bad.write_text("plan_id,J\nPLAN_1,3.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing column.*k_lb"):
        read_batch(bad)


def test_find_batch_csv_prefers_the_fresh_run(tmp_path, monkeypatch):
    fresh, sample = tmp_path / "out" / "batch.csv", tmp_path / "sample.csv"
    monkeypatch.setattr(ui, "BATCH_PATHS", (fresh, sample))
    assert find_batch_csv() is None
    sample.write_text("x", encoding="utf-8")
    assert find_batch_csv() == sample
    fresh.parent.mkdir()
    fresh.write_text("x", encoding="utf-8")
    assert find_batch_csv() == fresh


# --------------------------------------------------------------------------- the cards


def test_batch_summary_counts_validity_lb_and_mean_j():
    s = batch_summary(read_batch(SMALL))
    assert s.n == 9 and (s.ok, s.strict_only, s.infeasible, s.input_error) == (7, 0, 1, 1)
    # #32's definition (batch.summary): of the feasible plans, i.e. loaded and not infeasible
    assert s.feasible == 7 and s.valid_pct == pytest.approx(100.0)
    assert s.at_lb_pct == pytest.approx(100 * 2 / 7)  # 3086 and 5088, of the 7 submitted
    js = [r["J"] for r in read_batch(SMALL) if r["submitted"]]
    assert s.mean_J == pytest.approx(sum(js) / len(js))


def test_batch_summary_of_nothing_submitted_has_no_means():
    s = batch_summary([r for r in read_batch(SMALL) if not r["submitted"]])
    assert s.feasible == 0 and s.valid_pct is None and s.at_lb_pct is None and s.mean_J is None


def test_a_feasible_plan_without_a_submission_counts_against_validity():
    """A loaded, not-infeasible plan without a submission counts against validity (#32's
    definition): here an `ok` row whose submission wasn't written."""
    rows = read_batch(SMALL)
    rows[0] = {**rows[0], "submitted": False}
    s = batch_summary(rows)
    assert s.feasible == 7 and s.valid_pct == pytest.approx(100 * 6 / 7)


def test_hub_gap_counts_h_minus_lb_of_submitted_plans():
    assert hub_gap_counts(read_batch(SMALL)) == {0: 2, 1: 4, 4: 1}


def test_gap_histogram_draws_one_bar_per_gap():
    fig = draw_gap_histogram({0: 2, 1: 4, 4: 1})
    bars = [p for p in fig.axes[0].patches if p.get_gid() and p.get_gid().startswith("gap:")]
    assert sorted(b.get_gid() for b in bars) == ["gap:0", "gap:1", "gap:4"]
    assert {b.get_gid(): b.get_height() for b in bars} == {"gap:0": 2, "gap:1": 4, "gap:4": 1}


# --------------------------------------------------------------------------- tags and opening


def test_plans_by_tag_from_the_tags_column():
    tags = plans_by_tag(read_batch(SMALL))
    assert tags["en-suite single-door"] == ["PLAN_1293", "PLAN_2814", "PLAN_3086", "PLAN_5088"]
    assert tags["4+ hubs"] == ["PLAN_576"]


def test_plans_by_tag_without_the_column_is_empty(tmp_path):
    rows = read_batch(SMALL)
    for r in rows:
        r.pop("tags")
    assert plans_by_tag(rows) == {}


def test_open_target_finds_a_test_plan_or_resplan(monkeypatch):
    assert open_target("PLAN_5924") == ("Test plans", "5924")
    monkeypatch.setattr(ui, "RESPLAN_JSONL", Path("/nowhere/plans.jsonl"))
    assert open_target("PLAN_123456") is None  # not a test plan, and no ResPlan file here
    monkeypatch.setattr(ui, "RESPLAN_JSONL", SMALL)  # any existing file stands in for it
    assert open_target("PLAN_123456") == ("ResPlan id", "123456")


# --------------------------------------------------------------------------- the tab


def _run(at: AppTest | None = None) -> AppTest:
    at = at or AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _batch_tab(at: AppTest):
    return next(t for t in at.main.tabs if t.label == "Batch")


def test_the_tab_shows_the_rows_and_the_cards(monkeypatch):
    monkeypatch.setattr(ui, "BATCH_PATHS", (SMALL,))
    tab = _batch_tab(_run())
    (table,) = [d.value for d in tab.dataframe if "plan_id" in d.value.columns]
    assert len(table) == 9
    cards = {m.label: m.value for m in tab.metric}
    assert cards["Plans"] == "9"
    assert cards["Valid submissions"] == "100.0%"  # 7 of the 7 feasible plans
    assert cards["Hubs at the lower bound"] == "28.6%"
    assert cards["Mean J (submitted)"].startswith(f"{batch_summary(read_batch(SMALL)).mean_J:.2f}")
    assert len(tab.image) == 1  # the H − LB histogram


def test_without_a_csv_the_tab_shows_the_command(monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "BATCH_PATHS", (tmp_path / "out" / "batch.csv",))
    tab = _batch_tab(_run())
    assert not tab.error and not tab.dataframe
    assert any(BATCH_COMMAND in c.value for c in tab.code)


def test_a_broken_csv_is_an_error_message_not_a_traceback(monkeypatch, tmp_path):
    bad = tmp_path / "batch.csv"
    bad.write_text(json.dumps({"not": "a csv"}), encoding="utf-8")
    monkeypatch.setattr(ui, "BATCH_PATHS", (bad,))
    tab = _batch_tab(_run())
    assert any("missing column" in e.value for e in tab.error)


def test_opening_a_row_switches_the_plan(monkeypatch):
    monkeypatch.setattr(ui, "BATCH_PATHS", (SMALL,))
    at = _run()
    _batch_tab(at).selectbox(key="batch_open").set_value("PLAN_13344")
    _run(at)
    _batch_tab(at).button(key="batch_open_go").click()
    _run(at)
    assert at.sidebar.selectbox(key="fixture").value == "13344"
    assert any("PLAN_13344" in c.value for c in at.main.caption)
