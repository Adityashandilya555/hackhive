"""Marks the suite's slowest tests `slow`, so `pytest -m "not slow"` is a quick loop while
iterating. The full suite still runs last, before every PR, and on CI (AGENTS.md).

The list lives here, by test id, instead of as decorators, so the test files other open PRs edit
stay untouched. Each entry took over ~10 s on a 4-core runner (`pytest --durations`); re-measure
and update the list when a test gets much faster or slower. A stale id only means a test runs in
the quick loop, never that it is skipped by the full run.
"""

from __future__ import annotations

import pytest

SLOW = {
    # one big case plan (15333), solved for its expected tags
    "tests/test_cases.py::test_case_plan_solves_as_expected[strict_only]",
    # make_cases.py run twice on a small set (idempotence)
    "tests/test_cases.py::test_script_picks_fixtures_then_the_scan_and_is_idempotent",
    "tests/test_cases.py::test_script_repicks_a_named_plan_that_no_longer_shows_its_case",
    "tests/test_cases.py::test_script_without_a_plans_file_refreshes_expected",
    # every arrangement of 13344 (57 zonings), and 5924's enumerated twice
    "tests/test_solve.py::test_enumerate_13344_lists_every_zoning",
    "tests/test_solve.py::test_enumerate_is_sorted_by_hubs_j_area_gap_signature",
    "tests/test_solve.py::test_enumerate_is_deterministic",
    "tests/test_solve.py::test_solve_submits_enumerate_first_fully_routed[5924]",
    # 13344 solved (~28 s): the quick loop never solves it
    "tests/test_solve.py::test_each_fixture_solves_to_a_valid_submission[13344]",
    "tests/test_solve.py::test_the_writer_follows_slide_8[13344]",
    "tests/test_badge.py::test_badge_follows_the_real_proof[13344]",
    "tests/test_counting_readings.py::test_lenient_never_costs_more_on_a_fixture[13344]",
    "tests/test_ui.py::test_case_tags_every_tag_has_an_id_and_a_look_for",
    # the exact ring search over every zone of the fixtures
    "tests/test_rings.py::test_exact_best_ring_never_costs_more_than_first_ring",
    "tests/test_rings.py::test_exact_best_ring_is_a_closed_ring_over_the_zone_with_a_rule_6_hub",
    # the batch over 3 fixtures, run twice
    "tests/test_batch.py::test_two_runs_give_the_same_csv_apart_from_ms",
}


# Tests that read 13344's shared solve (`fixture_solves`). Under `pytest -n auto --dist
# loadgroup` they run on one worker, so 13344 (~28 s alone, ~110 s on a busy runner) is solved
# once instead of once per worker.
SHARED_13344 = {
    "tests/test_solve.py::test_each_fixture_solves_to_a_valid_submission[13344]",
    "tests/test_solve.py::test_the_writer_follows_slide_8[13344]",
    "tests/test_badge.py::test_badge_follows_the_real_proof[13344]",
    "tests/test_counting_readings.py::test_lenient_never_costs_more_on_a_fixture[13344]",
    "tests/test_ui.py::test_case_tags_every_tag_has_an_id_and_a_look_for",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.nodeid in SLOW:
            item.add_marker(pytest.mark.slow)
        if item.nodeid in SHARED_13344:
            item.add_marker(pytest.mark.xdist_group("fixture-13344"))
