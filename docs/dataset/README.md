# ResPlan: the floor plans we build on

Everything we know about the dataset hackhive is developed and tested on. Numbers were measured over
**all 17,000 plans** (profile and integrity checks 2026-10-15; schema and provenance re-checked
2026-10-17). The organisers' benchmark plans are ResPlan-derived (slide 1: "Dataset: ResPlan,
17,000 residential plans"), but they come as their own per-plan JSON with fixed node points
(slide 2), so treat this as the development set, not the benchmark.

## Files here
| File | What it is |
|---|---|
| `README.md` | this page |
| [`profile-summary.txt`](profile-summary.txt) | the profile's raw summary numbers (the tables below come from it) |
| [`profile-per-plan.csv`](profile-per-plan.csv) | one row per plan, 48 columns (legend below): use it to pick plans with a given property |

## Source and provenance
- **ResPlan** by Abouagour & Garyfallidis: [m-agour/ResPlan](https://github.com/m-agour/ResPlan) @
  `e2b78fe`, also on [Kaggle](https://www.kaggle.com/datasets/resplan/resplan) (the link in the
  problem statement).
- **Our copy:** `ResPlan.pkl`, 258,453,658 bytes, CRC-32 `ee6b441e`, byte-identical to the pickle
  inside `ResPlan.zip` @ `e2b78fe`. That is the **geometry-only** release. ResPlan's README says the
  Kaggle release ships the same geometry with `plan["graph"]` precomputed; we don't use that graph
  (we build our own strict room graph, `docs/ARCHITECTURE.md`).
- **Decoded to JSON** by [`scripts/resplan_to_jsonl.py`](../../scripts/resplan_to_jsonl.py):
  - a restricted unpickler: only `shapely.io.from_wkb`, numpy's scalar and `dtype` may load, so a
    tampered pickle aborts instead of running code. **Never `pickle.load` `ResPlan.pkl` directly.**
  - geometries become GeoJSON (`shapely.geometry.mapping`), numpy numbers become plain numbers, and
    an `index` field (the plan's position in the pickle, 0-based) is added. Nothing else changes.
  - output `plans.jsonl`: one plan per line, 17,000 lines, 639,678,580 bytes, SHA-256
    `ea135dc8e17c83a100211c3efb8a06beb3a5d35530dd0f0cdc9408d83a21fbf7`. Re-running the script
    on the same pickle gives this exact file (checked 2026-10-17, ~11 s).
  - JSONL against the pickle, every field of every plan: **0 mismatches**.
- Size per plan: median 34 KB, p99 100 KB, max 565 KB.

### Getting the plans
```sh
# ResPlan.zip from github.com/m-agour/ResPlan @ e2b78fe, unzipped into data/resplan/
uv run --no-project --with shapely --with numpy python -I scripts/resplan_to_jsonl.py \
    data/resplan/ResPlan.pkl data/resplan/plans.jsonl
curl -L -o data/resplan/split.json https://raw.githubusercontent.com/m-agour/ResPlan/e2b78fe/split.json
```
`data/` is git-ignored. With `data/resplan/plans.jsonl` and its `split.json` in place,
`uv run hackhive batch --out out/batch.csv` runs the `test` split. `hackhive solve` takes one plan
per file: any single line of `plans.jsonl` saved as `<name>.json` works (the fixtures in
`tests/fixtures/plans/` are such lines, pretty-printed).

## Licence and attribution
ResPlan's `LICENSE` puts the data (`ResPlan.pkl`, `split.json`, `croissant.json`) under
**CC BY 4.0**, while its `croissant.json` says **CC BY-NC-SA 4.0**. We follow the stricter terms:
attribution, non-commercial, share-alike. Attribution: Abouagour & Garyfallidis, *ResPlan: A
Large-Scale Vector-Graph Dataset of Residential Floor Plans*, github.com/m-agour/ResPlan. Our only
changes are the format (pickle → GeoJSON) and the added `index`.

The licence covers the authors' annotations, taxonomy, graph construction, scale conversion,
curation and splits; ResPlan asserts no rights over the spatial arrangements themselves. Its
`TAKEDOWN.md` lets rights holders have plans removed from later releases (listed in its
`REMOVED.md`), so check upstream before redistributing a plan.

## One plan
A JSON object. Geometry values are GeoJSON `Polygon` / `MultiPolygon` objects in **drawing units**
(each plan on a ~256-unit canvas, at its own scale), y-up as ResPlan plots them.

| Key | Plans with it | Type | Meaning | hackhive |
|---|---|---|---|---|
| `id` | 17,000 | int, unique, 0 … 999,398 | the ResPlan plan id; `split.json` lists these | `plan_id` = `PLAN_<id>` |
| `index` | 17,000 | int, 0 … 16,999 | position in the pickle (= line in `plans.jsonl`); added by us | not read |
| `area` | 17,000 | float, m² | floor area of the `inner` footprint | the scale (below) |
| `inner` | 17,000 | MultiPolygon | everything inside the outer walls: rooms and the partitions between them. Balconies and outdoor areas are outside it | the building boundary cable must stay in |
| `wall_depth` | 17,000 | float, units | wall thickness: 0.18 / 0.23 / 0.30 m at p5 / p50 / p95 | metres via the scale |
| `net_area` | 17,000 | float or int | **unreliable**: 0 in 32% of plans; its ratio to room area runs from 0 to 2.2 M | dropped on load, never read (slide 4) |
| `wall` | 17,000 | (Multi)Polygon | walls | obstacles |
| `door` | 17,000 | (Multi)Polygon | interior doors | the only way between rooms, with open passages |
| `front_door` | 16,995 | Polygon | the entrance (missing in 5 plans) | never a zone link (slide 3) |
| `window` | 16,950 | (Multi)Polygon | windows (`null` in 1 plan) | obstacles, also on interior walls (A10) |
| `living` | 17,000 | Polygon 16,738 · MultiPolygon 261 · `null` 1 | eligible room | each polygon is its own room with one node |
| `bedroom` | 16,997 | (Multi)Polygon | eligible room | node |
| `kitchen` | 16,875 | (Multi)Polygon | eligible room | node |
| `storage` | 1,781 | (Multi)Polygon | eligible room | node |
| `bathroom` | 17,000 | (Multi)Polygon | excluded room | no node, no hub |
| `balcony` | 13,066 | (Multi)Polygon | excluded, outside `inner` | no node, no hub |
| `stair` | 685 | (Multi)Polygon | excluded room | no node, no hub |
| `garden`, `parking`, `land` | 17,000 | (Multi)Polygon | excluded, mostly empty | no node, no hub |
| `pool` | 16,999 | (Multi)Polygon | excluded, mostly empty | no node, no hub |
| `neighbor` | 17,000 | list of 2 geometries | not documented upstream | ignored |
| `area_change_sqft` | 683 | float | present on exactly the 683 `augmented` plans | ignored |

ResPlan has **no node points**. The organisers' JSON gives them (slide 2: fixed, one per eligible
room, 0.5 m from walls); for ResPlan dev plans hackhive generates one per eligible room at its
max-clearance point.

### Units
**Metres per unit = √(`area` ÷ `inner.area`)**, per plan. Slide 4's `scale = sqrt(inner.area / area)`
is the same conversion in units per metre. Convert once per plan, before any limit or clearance
check.

| Measure | p5 | p50 | p95 | max |
|---|---|---|---|---|
| metres per unit | 0.04 | 0.05 | 0.08 | 0.17 |
| plan extent, max(w, h) | 9.9 m | 14.0 m | 21.7 m | 42.7 m |
| grid cells at 10 cm (w·h) | 7.2k | 13.8k | 30.5k | 122k |
| wall thickness | 0.18 m | 0.23 m | 0.30 m | 0.66 m |
| door width (median door per plan) | 0.86 m | 1.00 m | 1.21 m | 2.67 m |

The narrowest doors: p1 0.41 m, p5 0.63 m.

### Geometry quality
- **886 invalid geometries in 862 plans.** Repair with `shapely.make_valid` on load; hackhive
  repairs before and after scaling, since scaling can make a valid polygon self-intersect (plans
  1293, 13344).
- **50,118 empty geometries**, nearly all in the optional layers (pool, garden, parking). Normal.
- `living` is a MultiPolygon in **261 plans** (2 parts: 256, 3: 4, 4: 1). Per slide 2 each polygon
  is a room with its own node, which changes the node count in exactly those 261 plans. The smallest
  living parts are slivers: 41 parts < 2 m² in 38 plans; hackhive folds those it can't fit a node in.
- Room shapes (sample of 3,400 plans): eligible rooms 42% rectangles, 54% rectilinear, 3.6% with
  diagonal edges; walls with diagonal edges 2.5%. Only 10 of the 3,400 plans have all rooms
  rectangular.

### Splits
`split.json` (from ResPlan) maps four keys to plan **`id`s** (not `index`). They are disjoint and
cover all 17,000 plans.

| Key | Plans |
|---|---|
| `train` | 13,053 |
| `val` | 1,632 |
| `test` | 1,632 (`hackhive batch`'s default) |
| `augmented` | 683: rotations, flips and scales of 667 originals |

## Profile (strict room graph, all 17,000 plans)
**Method.** A copy of ResPlan's `resplan_utils.py` @ `e2b78fe` (MIT) with one node per `living`
part. The room graph is strict: windows and doors are subtracted before the open-passage test, so
window gaps never count as openings. Only `via_door` and `via_opening` edges are kept; `fallback`
edges (through walls) are dropped, and ResPlan's `add_adjacency_edges` is never called. Zoning is
exact: every connected room subset with ≤ 100 m² and ≤ cap nodes, then every exact cover of the
eligible rooms by such subsets.

### Nodes and area
| Measure | Result |
|---|---|
| Nodes per plan (one per polygon) | 2: 12 · 3: 1,751 · 4: 7,342 · 5: 5,825 · 6: 1,609 · 7: 368 · 8: 82 · 9: 10 · 10: 1 (**max 10**) |
| Eligible area per plan | p5 50 · p50 95 · p95 184 · max 612 m² |
| Plans over 100 m² of eligible area | **7,548 (44%)**: they need ≥ 2 hubs on area alone |
| Largest eligible room | p50 41 · p95 79 · max 465 m²; **428 plans have a room > 100 m²**, so they're out of the benchmark (slide 2); **16,572 remain** |
| Smallest eligible room | p1 0.83 m², p5 2.8 m²; **509 plans have a room < 2 m²** |
| Rooms with no point ≥ 0.5 m from walls | **423 rooms in 370 plans** (198 even at 0.25 m) |

### Doors, openings and windows
| Measure | Result |
|---|---|
| Doors per plan | p50 ≈ 6, range 1–19 |
| Windows per plan | p5 3 · p50 7 · p95 12 · max 23 |
| Front doors | 1 in 16,995 plans; 0 in 5 |
| `via_door` edges | 108,880, in every plan |
| `via_opening` edges (open passages) | **15,663, in 13,511 plans (79%)**: open passages have no geometry of their own, so the parser derives them |
| `fallback` edges (through walls, dropped) | 1,069, in 983 plans |

Interior walls drawn with **window** polygons exist: plan 2814's kitchen is reachable only through
one. With windows as obstacles (A10) such a room becomes a doorless one-room zone; reading them as
doors would change the exact hub count in 141 plans (0.8%).

### Contiguity
| Measure | Result |
|---|---|
| Single-door eligible rooms | **29,338**. Per plan: 0: 511 · 1: 6,075 · 2: 8,153 · 3: 2,094 · 4: 160 · 5: 7, so **97% of plans have one** |
| Eligible rooms with no door or opening | **320 rooms in 315 plans**: each is its own zone, hub and node in the same room |
| Eligible-room components (direct links only, slide 3 rule 3) | 1: 16,563 · 2: 426 · 3: 10 · 4: 1 |
| Components if excluded rooms could link | 1: 16,585 · 2: 405 · 3: 9 · 4: 1 (direct-only splits **23** more plans) |

### Hub counts and zonings
| Measure | Result |
|---|---|
| Lower bound LB = max(⌈area/100⌉, ⌈nodes/5⌉) (slide 2) | 1: 9,383 · 2: 7,003 · 3: 503 · 4: 81 · 5: 25 · 6: 3 · 7: 2 |
| Which part of LB binds (cap 5) | area 16,931 · nodes **69** |
| **Exact minimum hubs, cap 5** (16,572 benchmark plans) | 1: 9,217 · 2: 3,813 · 3: 2,190 · 4: 826 · 5: 356 · 6: 134 · 7: 34 · 8: 1 · 9: 1; infeasible: 0 |
| Exact minimum − LB, cap 5 | 0: **12,871 (77.7%)** · 1: 2,368 · 2: 884 · 3: 373 · 4: 73 · 5: 2 · 6: 1 |
| Exact minimum, cap 20 | differs from cap 5 in **66 plans** |
| Feasible zones per plan | p50 11 · p90 20 · p99 34 · max 80 (cap 5) / 92 (cap 20) |
| All feasible zonings per plan | p50 8 · p90 16 · p99 30 · **max 115 (cap 5) / 116 (cap 20)** |
| Zonings at the minimum hub count | p50 1 · p90 4 · p99 7 · max 16 |
| Time (Python, one core) | graph p50 5 ms, max 63 ms; exact enumeration p50 < 1 ms, max 16 ms |

The LB is often out of reach: in 22% of benchmark plans contiguity forces more hubs, mostly because
`living` is the hallway (a star), so rooms that don't fit in its zone become one-room zones.

**Hub point (slide 3 rule 6).** A zone needs a room whose largest inscribed circle has radius
≥ 0.5 m. 360 benchmark plans have an eligible room that can't host a hub; the exact minimum hub
count changes in **none**; **36 plans (0.22%) become impossible**, all with near-zero sliver rooms.

### Representative plans
These seven are the committed fixtures, `tests/fixtures/plans/plan-<id>.json`; their designs are in
[`../presentations/`](../presentations/).

| Plan | Why | Nodes | Eligible m² | LB (5) | Exact min (5) | Exact min (20) | Zonings (5) |
|---|---|---|---|---|---|---|---|
| 5088 | small, 1 hub | 3 | 50.0 | 1 | 1 | 1 | 4 |
| 3086 | median; an open passage; single-door rooms | 4 | 95.0 | 1 | 1 | 1 | 8 |
| 1293 | ≥ 2 hubs | 5 | 188.7 | 2 | 2 | 2 | 12 |
| 5924 | star around living: LB 2, needs 3 | 5 | 150.0 | 2 | 3 | 3 | 7 |
| 576 | 2 living parts; 2 components; LB 2, needs 6 | 8 | 193.6 | 2 | 6 | 6 | 10 |
| 13344 | the node cap binds: 3 hubs at cap 5, 1 at cap 20 | 7 | 91.5 | 2 | 3 | 1 | 57 |
| 2814 | kitchen reachable only through an interior "window" | 4 | 97.2 | 1 | 2 | 2 | 4 |

The table is the 2026-10-15 profile. The solver's own numbers have since moved for some plans
(e.g. 1293 now needs 3 hubs: two of the profile's links were corner-door artefacts, #62); see
`tests/fixtures/README.md`.

## `profile-per-plan.csv` columns
Areas in m², lengths in m. `5` / `20` suffixes are the node cap.

| Column | Meaning |
|---|---|
| `id`, `index` | as in the plan |
| `invalid` | invalid geometries in the plan (repaired) |
| `m_per_unit` | the scale |
| `w_m`, `h_m` | `inner`'s bounding box |
| `inner_m2` | the plan's `area` |
| `living_parts` | polygons in `living` |
| `n_nodes`, `n_nodes_union` | eligible polygons; the same with `living` unioned into one |
| `elig_m2`, `max_room_m2`, `min_room_m2`, `min_living_part_m2` | eligible-room areas |
| `lb5`, `lb20`, `binds5` | the lower bound, and whether area or nodes sets it at cap 5 |
| `nodegen_fail`, `nodegen_fail_025` | eligible rooms with no point 0.5 m / 0.25 m from their boundary |
| `n_doors`, `n_windows`, `n_front_doors` | counts of polygons |
| `door_w_min`, `door_w_med`, `wall_m` | narrowest and median door width; wall thickness |
| `e_via_door`, `e_via_opening`, `e_via_window`, `e_fallback`, `e_direct` | room-graph edges by type (`e_via_window` is always 0: windows are obstacles) |
| `single_door_elig`, `zero_door_elig` | eligible rooms with exactly one / no door or opening |
| `comp_direct`, `comp_via_excl` | eligible-room components with direct links only / with excluded rooms allowed to link |
| `lb5_contig`, `max_comp_nodes` | LB summed per component; nodes in the largest component |
| `min_hubs5`, `min_hubs20` | exact minimum hubs; **-1** = a room over 100 m² (out of the benchmark), **-2** = no zoning |
| `n_min5`, `n_all5`, `n_zones5` (and `…20`) | zonings at the minimum, all zonings, feasible zones |
| `trunc5`, `trunc20` | 1 if enumeration was cut short (never) |
| `ms_graph`, `ms_enum` | timing |
