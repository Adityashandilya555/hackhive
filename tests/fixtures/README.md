# Test fixtures

## Plans
`plans/plan-<id>.json` are seven floor plans from the **ResPlan** dataset
([m-agour/ResPlan](https://github.com/m-agour/ResPlan) @ `e2b78fe`), each extracted verbatim as the
`.plan` object of a research-vault file:

| id | label | why it's here |
|---|---|---|
| 576 | odd | two living parts, an opening, 6 hubs at cap 5 |
| 1293 | large | 188.7 m², area binds the lower bound |
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

## Expected numbers
`expected.json` is keyed by plan id. Each entry keeps the vault's `.metrics` fields `n_nodes`,
`elig_m2`, `lb5`, `lb20`, `min_hubs5`, `min_hubs20`, `n_all5`, `n_all20`, `comp_direct`,
`single_door_elig`, `zero_door_elig` and `e_via_opening`, converted to numbers. Rebuild it from
`raw/` if the vault changes.

## Slide 8
`slide8_example.json` is the organisers' slide-8 output example, verbatim where the slide shows values:
H1 with its two shown segments. The slide elides the N3→N2 and N2→H1 segments and hub H2
("… H2 has the same structure"), so they are absent here; `reported_metrics` is as printed.
