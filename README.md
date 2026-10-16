# hackhive

PS4 floor-plan network planner: given a floor plan and its fixed node points, it chooses the hubs,
groups the rooms into zones, and routes one closed, axis-aligned cable ring per hub. It writes the
organisers' slide-8 submission plus a sidecar that explains the design.

- What we build and the rules: [`AGENTS.md`](AGENTS.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Output files, statuses, warnings and J: [`docs/output-format.md`](docs/output-format.md)
- Every edge case: [`docs/decision_conventions.md`](docs/decision_conventions.md)

## Install

Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/):

```sh
git clone https://github.com/Adityashandilya555/hackhive.git
cd hackhive
uv sync
```

## Run

Design one plan. This writes `PLAN_5924.json` (the submission, only when the design is valid),
`PLAN_5924.sidecar.json` and an overlay `PLAN_5924.png` into `out/`:

```sh
uv run hackhive solve tests/fixtures/plans/plan-5924.json --out out/
```

Check a submission against its plan (slide 3's rules, recomputing every metric from the polylines).
Exit 0 means valid; `--fail-on-strict` exits 4 when only strict readings fail:

```sh
uv run hackhive check tests/fixtures/plans/plan-5924.json out/PLAN_5924.json
```

Solve many plans into a CSV report, with each design beside it. `--plans` takes a directory of plan
JSON files or a `.jsonl(.gz)`; this runs on the committed fixtures:

```sh
uv run hackhive batch --plans tests/fixtures/plans --n 3 --out out/batch.csv
```

Without `--plans`, `batch` reads `data/sample/test-100.jsonl.gz`, else `data/resplan/plans.jsonl`,
and keeps the `--split` (default `test`) listed in a `split.json` beside it. `data/` is not committed:
for a full run, put ResPlan (below) there as `data/resplan/plans.jsonl`, one plan per line, with its
`split.json` beside it.

`solve --help` lists the what-if flags (area cap, hub clearance, D5, D7). A what-if run is labelled
WHAT-IF and never writes a submission.

## The app

```sh
uv run streamlit run app.py
```

A Streamlit demo: pick a plan, see it drawn with the organisers' rules locked, the solver's best
design with its overlay and summary, and labelled what-ifs. The arrangements gallery (#26) and the
batch leaderboard (#33) are still to come.

## Checks

What every PR runs:

```sh
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```

## Data attribution

Floor plans come from **ResPlan** ([m-agour/ResPlan](https://github.com/m-agour/ResPlan)), data
licensed **CC BY 4.0** (its `croissant.json` says CC BY-NC-SA 4.0, so we treat it as non-commercial).
The plans in `tests/fixtures/` are derived from it. `src/hackhive/io.py`'s loader helpers are copied
from ResPlan's MIT-licensed `resplan_utils.py` and keep its notice.
