# hackhive

PS4 of UR Hackathon 2026H2: for a floor plan, place the fewest network hubs (≤ 100 m² and ≤ 5 nodes each, door-connected zones), order one closed cable ring per hub, route it axis-aligned through doors only, and write the organisers' slide-8 submission. The output is documented key for key in [`docs/output-format.md`](docs/output-format.md); team conventions are in [`AGENTS.md`](AGENTS.md).

## Install

Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/):

```
uv sync
uv run pytest -q
```

## Solve a plan

```
uv run hackhive solve tests/fixtures/plans/plan-5924.json --out out/
```

Writes `out/<plan_id>.json` (the submission, only if it passes our checker mirror), `out/<plan_id>.sidecar.json` (always) and `out/<plan_id>.png` (the overlay).

Exit codes: **0** a submission was written (also when strict-only), **1** only the sidecar was, **3** the plan file doesn't exist, **2** a usage error.

What-if flags (labelled WHAT-IF; never written as a submission, exit 0 when the what-if design has no `error` under its own settings, else 1):

| Flag | What it changes |
|---|---|
| `--max-area M2` | the hub area cap (problem statement: 100 m²) |
| `--hub-clearance M` | the hub-to-wall distance (problem statement: 0.5 m) |
| `--zone-links-via-excluded` | a bathroom or stair may link two zone rooms (D5) |
| `--route-through-other-rooms` | cable may cross bathrooms or other hubs' rooms (D7) |

Every arrangement, ranked (#22: fewest hubs, then the worse J of the two counting readings, then the area gap):

```
uv run python -m hackhive.solve --list tests/fixtures/plans/plan-5924.json
```

It prints one row per arrangement: H, J (pass 0, each ring routed alone), J under each reading, the area gap and the zones. `solve` submits the first of them that passes the checker mirror after the rip-up passes.

## Check a submission

```
uv run hackhive check tests/fixtures/plans/plan-5924.json out/PLAN_5924.json
```

Recomputes every metric from the polylines and checks slide 3's six rules. Exit codes: **0** valid (strict-only too; each strict reading printed as `STRICT (rule n, #46 Qm): …`), **1** any `error`, **4** strict-only with `--fail-on-strict`, **3** a file can't be read, **2** a usage error.

## Batch

```
uv run hackhive batch --plans tests/fixtures/plans --out out/batch.csv
```

Solves every plan (by default up to 100 of `--split test`), writes one CSV row per plan, and writes the designs beside the CSV (#32).

## The app

```
uv run streamlit run app.py
```

A Streamlit demo: the locked "Problem statement" preset, labelled what-ifs, the overlay and the checker's checklist.

## Data

The development plans come from **ResPlan** ([github.com/m-agour/ResPlan](https://github.com/m-agour/ResPlan)), licensed **CC BY 4.0** ([creativecommons.org/licenses/by/4.0](https://creativecommons.org/licenses/by/4.0/)). The fixtures in `tests/fixtures/plans/` are derived from it. Its `croissant.json` says CC BY-NC-SA 4.0, so we treat the data as non-commercial (`docs/REFERENCES.md`).
