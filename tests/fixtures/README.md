# Test fixtures

## Plans
`plans/plan-<id>.json` are seven floor plans from the **ResPlan** dataset
([m-agour/ResPlan](https://github.com/m-agour/ResPlan) @ `e2b78fe`), each extracted verbatim as the
`.plan` object of a research-vault file:

| id | label | why it's here |
|---|---|---|
| 576 | odd | two living parts, an opening, 6 hubs at cap 5 |
| 1293 | large | 188.7 m², LB 2 but 3 hubs: two vault links were corner-door artefacts (#62) |
| 2814 | interior-window | a window on an interior wall, a zero-door eligible room |
| 3086 | median | a typical plan with one open passage |
| 5088 | small | 50 m², one hub |
| 5924 | gap | 150 m², LB 2 but 3 hubs needed |
| 13344 | nodecap | 7 nodes: 3 hubs at cap 5, 1 at cap 20 |

`raw/plan-<id>-<label>.json` are the original vault files (`label`, `metrics`, `strict_graph_edges`,
`plan`).

**Licence:** ResPlan data is CC BY 4.0 per the repository README (its `croissant.json` says
CC BY-NC-SA 4.0; see `docs/ARCHITECTURE.md` § Sources). Attribution: Abouagour & Garyfallidis,
*ResPlan: A Large-Scale Vector-Graph Dataset of Residential Floor Plans*.

`plan-16552-stray-line.json` is ResPlan plan 16552 trimmed to `inner`, `area`, `wall_depth`,
`bedroom` part 0 and the three walls it touches (coordinates verbatim). Its room minus walls leaves a
1 mm line beside the polygon, which made node placement raise (6 of 17,000 ResPlan plans did).

## Cases
`cases/cases.json` lists every dataset case (`ui.CASES`, #84): `[{case, title, look_for, plan,
expected: {status, k_found?, tags}}]`, where `plan` is relative to `cases/` and `expected` is what
`solve()` gives (`tests/test_cases.py` checks it). A case shown by a committed fixture points at
it (`../plans/plan-<id>.json`); every other case has its own `cases/case-<tag>-<id>.json`, a
**ResPlan** plan (@ `e2b78fe`, CC BY 4.0, attribution as above) written verbatim from a line of
`plans.jsonl`: the smallest plan of the 600-plan scan (seed 5, as in the plan's
§4) that shows the case.

Rebuild with `uv run scripts/make_cases.py <plans.jsonl>` (the dataset isn't committed). It keeps
each case's current plan while it still shows its case, so it is idempotent; the grid-failed case
stays a regression example and #78's PR only re-runs it to flip its `expected`.

**When `solve()` changes** (a routing or ranking win) and `test_case_plan_solves_as_expected`
fails on `expected`, run `uv run scripts/make_cases.py` with **no** plans file. It recomputes
every `expected` from the committed plans, so no dataset is needed. Only a plan that stops
showing its case (as 13693 stopped being strict-only after #101) needs the dataset run to
re-pick it. The test's message says which of the two it is.

## Expected numbers
`expected.json` is keyed by plan id. Each entry keeps the vault's `.metrics` fields `n_nodes`,
`elig_m2`, `lb5`, `lb20`, `min_hubs5`, `min_hubs20`, `n_all5`, `n_all20`, `comp_direct`,
`single_door_elig`, `zero_door_elig` and `e_via_opening`, converted to numbers. Rebuild it from
`raw/` if the vault changes.

**One deliberate difference (#13):** 1293's `min_hubs5`/`min_hubs20` (2 → 3), `n_all5`/`n_all20`
(12 → 6) and `e_via_opening` (0 → 1) follow #9's room graph, not the vault. The vault linked
`bedroom_0`–`bedroom_1` and `living_0`–`bedroom_2` through doors that sit at room corners and join
neither pair, and counted the open living–kitchen passage as a door (#62). Keep this when
rebuilding.

## Slide 8
`slide8_example.json` is the organisers' slide-8 output example, verbatim where the slide shows values:
H1 with its two shown segments. The slide elides the N3→N2 and N2→H1 segments and hub H2
("… H2 has the same structure"), so they are absent here; `reported_metrics` is as printed.
