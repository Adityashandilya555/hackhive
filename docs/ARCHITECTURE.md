# hackhive architecture (PS4)

For every component, this doc says what it does, the data contract on each edge, the algorithm and its bound, the rules (C-ids) it enforces, the issues that build it, and **which reference repo each idea came from**, including what we chose not to take.

Lead's research notes live in Adi's Obsidian vault (not in this repo); this document summarises them. Board: [HACKHIVE PS4](https://github.com/users/Adityashandilya555/projects/5).

## 1. Overview
For any unseen floor plan, with no manual step, hackhive:
1. places **one fixed node per eligible room**;
2. groups the rooms into the **fewest contiguous hub zones** (≤ 100 m² and ≤ 5 nodes each), proven optimal by exhaustive search;
3. puts each hub inside an eligible room of its zone and orders **one closed ring per hub** exactly;
4. routes **axis-aligned cables that change rooms only through doors and open passages**;
5. outputs a validated JSON plus an overlay;
6. a Streamlit demo shows **every feasible arrangement**, with filters and a few novelty views.

### Sources
- The organisers' slides ("UR Hackathon 2026H2", slides 1, 2, 4, 5; slides 3 and 6-8 are unavailable) **win every conflict**.
- The Honeywell PS4 text fills gaps.
- Adi's answers are recorded as constraints.

### Rules the system must satisfy (C-ids)
| C | Rule | C | Rule |
|---|---|---|---|
| C1 | one fixed node per eligible room (living, bedroom, kitchen, storage) | C20 | invalid polygons are repaired |
| C2 | each eligible polygon is one room with one node | C21 | input: one JSON file per plan |
| C3 | node points are given by the organisers; immovable | C22 | processes an unseen plan with no manual step |
| C4 | excluded rooms (bathroom, balcony, stair, garden, pool, parking, land): no node, no hub | C23 | overlay: zones, **hub and node ids**, numbered order (the last = the return), full rings, **violations in red** |
| C5 | ≤ 100 m² of eligible area per hub | C24 | the output JSON is **slide 8 exactly** (metres, origin lower-left, y up) |
| C6 | ≤ 5 nodes per hub (≤ 20 is an optional what-if) | C25 | per-hub summary: rooms, m², nodes, length, bends |
| C7 | hubs ≥ max(⌈area/100⌉, ⌈nodes/cap⌉) | C26 | "What a reviewer confirms" checklist (slide 5) |
| C8 / C34 | a zone's rooms connect directly through door/opening links; excluded rooms never link | C27 | a room > 100 m² → an infeasible result, not a crash |
| C9 | every node in exactly one hub | C28 | naming: hub / node |
| C10 | ring H → N1 → … → Nk → same H, closed, each node once | C29 | all feasible arrangements, filterable; constraints configurable in the UI |
| C11 | invalid: open ends, branches, skipped/repeated nodes, ending at another hub | C30 | 7 low-effort novelty views |
| C12 | horizontal and vertical runs only | C31 | visible-first delivery |
| C13 | change rooms only through doors and open passages | C33 | cables may cross any room inside the building; overlaps and crossings between rings are cost, not violations |
| C14 | never through walls, windows or the outside; stay inside `inner` | C35 | each sprint ends with a pull-and-run-locally verification |
| C15 | single-door room: in and out through the same door (two parallel runs); the shared length counts as overlap | C36 | sprints carry no dates |
| C16 | rank: fewest hubs H first, then lowest J, then the smaller assigned-area gap | C37 | interior "windows": both readings, switched by a UI checkbox; default = obstacle |
| C17 | **J = L + 5X + O + 0.5B** (slides 3, 6; the weights are starting values, kept as settings) | C38 | the solver serves every given node; our ResPlan node generator folds unfit living slivers and reports them |
| C18 | hub inside an eligible room **assigned to it**, **≥ 0.5 m from walls**; a hub and a node may share a room (slide 3 rule 6) | C39 | a design must pass all six hard rules of slide 3 before it is ranked |
| C19 | metres = units ÷ √(inner.area / area), converted once per plan | C40 | metric semantics (slide 7): X counts inside one ring too; O = (k − 1) × length on the same line; B at vertices, nodes and the hub |
| C41 | tolerance 1 mm | C42 | the checker recomputes from polylines; reported values are ignored |
| C43 | **an invalid or missing plan scores zero** | C45 | a final-presentation visual |
| C46 | the input format is unknown; ids look like `R05`, `N1` | C47 | tie-break: the smaller assigned-area gap (max − min) |
| C48 | slide-8 format details (`location` object, `[x, y]` points, ordering, plan-global ids) | | |

### Assumptions
- **A1** The organiser JSON format is unknown, so the loader is an adapter: ResPlan-shaped geometry + an optional `nodes` field.
- **A2** Our generated nodes sit at the max-clearance point, warned if < 0.5 m.
- **A3** Infeasible plans return reasons.
- **A4** Crossing = two different rings crossing; overlap = shared run length.
- **A6** C23's "crossing" means crossing room boundaries.
- **A7** A hub is ≥ 0.5 m from every node.
- **A8** Ranking is lexicographic: (hubs, cost rounded to 0.1, zone signature).
- **A10** Windows are obstacles by default (C37 adds the other reading).

## 2. Pipeline
Arrows show **data flow**; each edge is labelled with the `contracts.py` model it carries. Every box is a Python module or a one-shot process; nothing is a long-running service. The source is `docs/architecture.mmd`, verified with `npx -y @mermaid-js/mermaid-cli@11.16.0` → `docs/architecture.svg`.

```mermaid
flowchart TB
    planjson[("plan JSON file (organiser file or one ResPlan plan)")]

    subgraph pkg["hackhive package: src/hackhive, in-process library calls; every edge carries a contracts.py model"]
        io["io.py: read · make_valid · scale to metres · nodes<br/>ref: ResPlan loader helpers (MIT copy)"]
        roomgraph["graph.py: strict door / open-passage graph<br/>ref: ResPlan plan_to_graph (re-implemented, strict)"]
        zoning["zoning.py: every contiguous capped zoning, exact<br/>ref: GerryChain contiguity, HGS lower-bound formula"]
        grid["grid.py: 10 cm room-label raster<br/>ref: OrthoRoute portal snapping"]
        cache["route.py PathCache: one-to-all bend-aware search<br/>ref: OrthoRoute seeds and ROI, Freerouting door graph (concept)"]
        rings["rings.py: hub location + exact ring order<br/>ref: PyVRP exact-vs-estimate cost (LB pruning)"]
        solve["solve.py: solve() · enumerate() · rank"]
        routerings["route.py route_rings: occupancy penalty + rip-up passes<br/>ref: VTR PathFinder cost, OrthoRoute commit/clear"]
        metrics["checker.py metrics: H · L · X · O · B · J at 1 mm (slide 7)<br/>ref: OptiWindNet fold and side tests"]
        validate["checker.py rules: slide 3 hard rules 1-6 + submit gate<br/>ref: OptiWindNet validators, GerryChain contiguity"]
        viz["viz.py: overlay · gallery tiles · compare · Pareto<br/>ref: house-planner electric layer, OptiWindNet plotting"]
    end

    out[("PLAN_id.json (slide 8, only if valid) + sidecar.json + overlay / presentation PNG")]
    csv[("batch.csv")]
    cli["hackhive CLI (one-shot process)"]
    batch["hackhive batch (one-shot run over the test split)"]
    app["app.py Streamlit demo (local process, started on demand)"]

    planjson -->|"raw dict"| io
    io -->|"Plan: rooms, doors, nodes in metres"| roomgraph
    roomgraph -->|"G_elig: eligible rooms, door/opening edges"| zoning
    roomgraph -->|"Plan + Opening regions"| grid
    grid -->|"LabelGrid"| cache
    zoning -->|"Zoning list (max 116 per plan)"| rings
    cache -->|"PathCache: pairwise paths"| rings
    rings -->|"Arrangement list: hubs, rings H to H, ranked by H, J, gap"| solve
    solve -->|"best Arrangement"| routerings
    grid -->|"LabelGrid"| routerings
    routerings -->|"Segment polylines"| metrics
    metrics -->|"Metrics: H, L, X, O, B, J, area gap"| solve
    solve -->|"Submission"| validate
    validate -->|"Violation list, gate verdict"| solve
    solve -->|"Submission + Sidecar"| viz
    solve -->|"Submission JSON (gated) + Sidecar"| out
    viz -->|"PNG / SVG"| out

    cli -->|"calls solve()"| solve
    batch -->|"calls solve() per plan"| solve
    batch -->|"one row per plan"| csv
    app -->|"calls solve() and enumerate()"| solve
    app -->|"calls viz"| viz
    app -->|"reads"| csv
```

## 3. What we took from each reference repo
These are design ideas only. **No code is copied** except ResPlan's two loader helpers (MIT, notice kept). **Freerouting is GPL-3.0: concepts only, never code.** The full matrix (69 rows) is summarised here. Evidence is `path:Lstart-Lend @ sha`, read on 2026-10-15.

| Repo | License | Adopt / Adapt / Skip | Component(s) | What we took, in one line |
|---|---|---|---|---|
| m-agour/ResPlan | code MIT · data CC BY 4.0 (`croissant.json` says CC BY-NC-SA 4.0) | 2 / 4 / 4 | io, graph, viz, batch | the `normalize_keys` / `get_geometries` loader helpers (copied); door attribution and open-passage detection (re-implemented strictly); base-plan styling; `split.json` |
| mggg/GerryChain | BSD-3 | 0 / 2 / 2 | zoning, validate | zone contiguity = `nx.is_connected` on the eligible door/opening graph, with fragments in violation messages |
| PyVRP/PyVRP | MIT | 0 / 1 / 4 | rings | exact-vs-estimate cost evaluation → lower-bound early exit when scoring rings |
| vidalt/HGS-CVRP | MIT | 0 / 1 / 4 | zoning | the two-capacity lower-bound formula only |
| bbenchoff/OrthoRoute | MIT | 0 / 5 / 2 | grid, route | cell snapping, multi-source seeding, door-graph ROI, an occupancy penalty and commit/clear bookkeeping |
| verilog-to-routing/vtr | MIT | 1 / 1 / 1 | route | an additive present-occupancy cost; rip-up and keep the best pass |
| DTUWindEnergy/OptiWindNet | MIT (palette CC BY 4.0) | 4 / 11 / 8 | validate, metrics, route, viz, contracts | the validator pattern, ring walk, fold and crossing detection, side-by-side plotting, the "(+k)" badge, a versioned output |
| TaipanRex/pyvisgraph | MIT | 0 / 1 / 1 | tests only | an L1-reweighted visibility-graph lower bound to sanity-check routed lengths |
| egmalt/house-planner | MIT | 2 / 2 / 2 | viz, app, docs | zone palette + grey "no node", ring polylines, the per-hub summary table, the output-format doc style |
| freerouting/freerouting | **GPL-3.0** | 0 / 2 (concept) / 2 | route | the door graph + a door-width check; a per-pass snapshot list (concepts only) |

## 4. Components
Module paths are under `src/hackhive/` unless noted. Owners are GitHub logins. Every evidence line was read at the commit shown.

### 4.1 `contracts.py`: data contracts
- **Responsibility:**
  - `Submission`: slide 8 key for key (`extra="forbid"`; Adi: slide-8 keys only);
  - `Sidecar`: our diagnostics;
  - input models; `Constraints.problem_statement()` (J weights 1 / 5 / 1 / 0.5, hub clearance 0.5 m from walls, tolerance 1 mm, frame settings); `lower_bound()`;
  - JSON Schemas generated to `schemas/submission.schema.json` and `schemas/sidecar.schema.json`.
- **In → out:** n/a → models + schemas.
- **Algorithm:** n/a.
- **Enforces:** C7, C17, C24, C28, C41, C48; the C37 setting.
- **Issues:** #2 (Adityashandilya555), #34 (nexuszephyr, the format doc).
- **Derived from:**
  - slide 8 (the organisers' format), with ADR-010 superseding ADR-006;
  - HGS-CVRP lower-bound line → **Adapt** with two capacities (`Program/Split.cpp:L3-6 @ 1a92795`, MIT).
- **Not taken:** our own output format (ADR-006, superseded); OptiWindNet's versioned/terse encoding.
- **Risks:** the organisers' checker may be strict about keys → the submission carries only slide-8 keys.

### 4.2 `io.py`: read, repair, scale, nodes
- **Responsibility:**
  - read one plan JSON (ResPlan dict or organiser file, A1);
  - `make_valid`; flatten to polygons;
  - scale to metres;
  - one room per polygon with an eligible flag;
  - flag rooms > 100 m²;
  - nodes: given ones (C3) or generated by the maximum inscribed circle (A2);
  - fold unfit living slivers (generator only, C38).
- **In → out:** raw dict → `Layers` → `Plan`.
- **Algorithm:** O(geometry); MIC with 1 cm tolerance.
- **Enforces:** C1-C4, C19-C21, C27, C38.
- **Issues:** #4, #6 (nexuszephyr); #38 Backlog (organiser format).
- **Derived from:**
  - ResPlan `normalize_keys`, `get_geometries` → **Adopt (copy, MIT notice kept)**, plus a flatten fix (`resplan_utils.py:L56-60, L74-82 @ e2b78fe`);
  - the scale formula from slide 4.
- **Not taken:**
  - ResPlan's living union (`resplan_utils.py:L317-325`), which breaks C2;
  - `centroid` (`L84-91`), which can fall outside a room;
  - `geometry_to_mask` (`L142-191`), which needs cv2;
  - augmentation (`L93-122`).
- **Risks:** 886 invalid geometries in ResPlan; 423 rooms with no point 0.5 m from walls; 41 living slivers in 38 plans (folded by our generator only).

### 4.3 `graph.py`: strict room graph
- **Responsibility:**
  - room graph over all rooms;
  - door edges keep the top 2 rooms by contact and store the door ids;
  - open-passage edges come with a region polygon, found by subtracting walls ∪ windows ∪ doors;
  - no window edges, no fallback edges;
  - `eligible_graph()` view (C34);
  - optional interior-window edges (C37).
- **In → out:** `Plan` → `nx.Graph` + `Opening` list.
- **Algorithm:** O(D·R + R²), with R ≈ 10-15 rooms.
- **Enforces:** C8, C13, C14, C34, C37.
- **Issues:** #9, #31 (nexuszephyr).
- **Derived from:**
  - ResPlan door attribution → **Adapt** (top 2, door ids stored, tolerances in wall depths) (`resplan_utils.py:L349-397 @ e2b78fe`, MIT);
  - ResPlan `via_opening` → **Adapt** (subtract windows and doors too; keep the region) (`resplan_utils.py:L437-501 @ e2b78fe`).
- **Not taken:**
  - `via_window` edges, the window→door relabel and `fallback` edges (`resplan_utils.py:L503-541`), which would let cables through windows or walls;
  - `add_adjacency_edges` (`L626-677`).
- **Risks:**
  - Open passages occur in 79% of plans and have no geometry, so a missed one isolates rooms. The safe failure is "unreachable", never "through a wall".
  - Interior windows change the hub count in 141 of 17,000 plans (C37 checkbox).

### 4.4 `zoning.py`: exact contiguous zonings
- **Responsibility:**
  1. every connected room subset within the caps;
  2. every exact cover (all arrangements), per connected component;
  3. the minimum hub count;
  4. a guard above 16 rooms.
- **In → out:** `G_elig`, areas, `Constraints` → `Zoning` list.
- **Algorithm:** a 2ⁿ subset filter + exact-cover recursion on the lowest uncovered room. **Exact.**
  - Measured over all 17,000 ResPlan plans: ≤ 10 rooms, ≤ 92 feasible zones, ≤ 116 zonings, ≤ 16 ms in Python.
  - The minimum equals the lower bound in only 77.7% of plans: star topology around `living` forces one-room zones.
- **Enforces:** C5-C9, C16, C29, C34.
- **Issues:** #13 (Adityashandilya555); #40 Backlog (plans over 16 rooms).
- **Derived from:**
  - GerryChain `contiguous` → **Adapt** as `nx.is_connected` on the eligible subgraph (`gerrychain/constraints/contiguity.py:L133-150`; `gerrychain/graph/graph.py:L2339-2364 @ 0edc278`, BSD-3);
  - HGS `generalSplit` lower-bound line → **Adapt** (`Program/Split.cpp:L3-6 @ 1a92795`, MIT).
- **Not taken:**
  - GerryChain MCMC / `recom` / balanced tree cuts (`gerrychain/proposals/tree_proposals.py:L208-325`);
  - PyVRP / HGS local search (`pyvrp/search/LocalSearch.py:L17 @ abe5313`; `Program/LocalSearch.cpp:L36-69 @ 1a92795`);
  - HGS Split as an upper bound (wrong for star plans);
  - CP-SAT.
  - Exact enumeration is complete and fast, so none of these are needed.
- **Risks:** organiser plans over 16 rooms → branch-and-bound + paging (#40).

### 4.5 `grid.py`: 10 cm room-label raster
- **Responsibility:**
  - an int16 raster: room ids, door/opening ids, −1 blocked;
  - paint order: rooms → walls / windows / front door → doors and openings → outside;
  - the move rule: same label, or door ↔ one of its 2 rooms;
  - snapping points to cells.
- **In → out:** `Plan` + `Opening` list → `LabelGrid`.
- **Algorithm:** O(cells); 7k-122k cells per plan.
- **Enforces:** C12-C14, C33.
- **Issues:** #10 (V1702).
- **Derived from:** OrthoRoute portal snapping → **Adapt** for node, hub and door snapping (`orthoroute/algorithms/manhattan/unified_pathfinder.py:L3377-3444 @ 9e06f2d`, MIT).
- **Not taken:** ResPlan `geometry_to_mask` (cv2 dependency); OrthoRoute's 3D lattice and vias.
- **Risks:** door/room raster gaps → doors are dilated into the wall band, with an assert that every door touches exactly 2 rooms.

### 4.6 `route.py`: bend-aware search, path cache, ring routing
- **Responsibility:**
  - `search`: one-to-all Dijkstra over (cell, heading); a turn costs w_bend; no U-turns;
  - `PathCache`: pairwise paths between nodes and hub candidates, once per plan;
  - `route_rings`: an occupancy penalty + 2 rip-up passes, keeping the best; per-pass snapshots.
- **In → out:** `LabelGrid`, sources → `PathCache`; rings → `Segment` polylines.
- **Algorithm:** O(4·cells·log) per source; ≤ 9 rings.
  - The runtime is not measured yet; it is measured in #19. If slow, the fallback order is: ROI → a 20 cm cost grid → numba → closed-form rectangles (#37).
  - Only 42% of eligible rooms are rectangles, so one grid router is simpler than two.
- **Enforces:** C12-C15, C17, C33.
- **Issues:** #8, #15, #19, #21 (V1702); #36, #37 Backlog.
- **Derived from:**
  - OrthoRoute multi-source seeding → **Adapt** (seed 4 headings) (`unified_pathfinder.py:L2515-2526 @ 9e06f2d`, MIT);
  - OrthoRoute ROI → **Adapt** (the rooms on door-graph paths) (`L1916-1990`);
  - OrthoRoute `node_penalty` and `commit_path` / `clear_path` → **Adapt** (a `Counter` of cell-edges) (`L2392-2488`, `L1066-1102`);
  - VTR congestion cost → **Adapt** (an additive present term only) (`vpr/src/route/route_common.h:L134-150 @ 5c65c86`, MIT);
  - VTR rip-up / restore best → **Adopt** (`vpr/src/route/route_common.cpp:L278-283`; `vpr/src/route/route.cpp:L575-586 @ 5c65c86`);
  - OptiWindNet `_astar_path` → **Adapt** (keep the Manhattan LB next to the routed length) (`optiwindnet/mesh.py:L74-134 @ 44e384c`, MIT);
  - Freerouting expansion rooms joined by doors, and the pass loop → **concepts only, GPL-3.0, no code** (`src/main/java/app/freerouting/autoroute/expansion/ExpansionDoor.java:L10`; `…/pipeline/BatchAutorouter.java:L600-603 @ 5aa1b24`);
  - pyvisgraph visibility graph, reweighted to L1 → **tests only** (`pyvisgraph/vis_graph.py:L59-130`, `pyvisgraph/shortest_path.py:L39-61 @ 9472e0d`, MIT).
- **Not taken:**
  - PathFinder history / `pres_fac` schedule (`vpr/src/route/route.cpp:L449-457`; `unified_pathfinder.py:L1193-1215`). Our capacity is soft, so these go to Backlog #36.
  - OrthoRoute GPU kernels.
  - pyvisgraph as the router: it routes at any angle, which C12 forbids.
- **Risks:**
  - order dependence → try 2-3 ring orders;
  - `w_ov` tuning → tune on the batch;
  - cramped narrow doors → a door-width check.

### 4.7 `rings.py`: hub placement and ring order
- **Responsibility:**
  - hub candidates: one per zone room, ≥ 0.5 m from nodes (A7);
  - the first-cut ring (Sprint 1);
  - the exact joint choice of hub + order: every node cycle (k!/2 ≤ 60), the hub inserted into each edge, a matrix-cost lower bound, a true routed-cost rescore in LB order, stopping at LB ≥ best;
  - Held-Karp for k ≤ 10 (the ≤ 20 what-if).
- **In → out:** zone, `PathCache` → `Hub` (xy, ring `[H, N…, H]`).
- **Algorithm:** exact on the cached paths. ≤ 60 × 5 evaluations per zone at k ≤ 5; ~10⁵ at k = 10.
- **Enforces:** C10, C11, C15, C18; C6 (cap-20 what-if).
- **Issues:** #14, #20, #28 (Adityashandilya555).
- **Derived from:**
  - PyVRP exact `deltaCost` vs shortcut → **Adapt** as the LB early exit (`pyvrp/cpp/CostEvaluator.h:L18-25, L140-153 @ abe5313`, MIT);
  - OptiWindNet `_validate_ringed` one-node-ring rule → **Adapt** (`optiwindnet/validating.py:L18-45 @ 44e384c`, MIT).
- **Not taken:**
  - HGS `move7` 2-opt (`Program/LocalSearch.cpp:L311-333`): arc-additive;
  - PyVRP intra-route 2-opt: absent, since `SwapTails` is inter-route (`pyvrp/cpp/search/SwapTails.h:L8-19`);
  - depot-given models (`pyvrp/Model.py:L358-372`): our hub location is a decision.
- **Risks:** one cached path per pair fixes its headings → per-heading variants if the batch shows bend losses.

### 4.8 `checker.py` (metrics half): the organisers' metrics, mirrored
- **Responsibility:** H, L, X, O, B, J and the assigned-area gap, computed **only from the submitted polylines** (C42) at 1 mm tolerance (C41), exactly as slide 7 defines them (C40):
  - **L:** every leg incl. return legs; a retrace counts each time.
  - **X:** points where two cables meet away from a node or hub, inside one ring or between rings, once per point.
  - **O:** (k − 1) × length on the same line; parallel lines don't count.
  - **B:** at vertices, nodes and the hub; a reversal counts once.
  - Where the slides are ambiguous (T-touch, the divergence end of a shared stretch), it counts strictly (`docs/decision_conventions.md` §6).
  - Used by the solver, the ranking, the batch and the UI, so our J is the organisers' J.
- **In → out:** slide-8 `hubs[]` → `Metrics(H, L, X, O, B, J, area_gap)`.
- **Algorithm:** O(S²) segment pairs for X; a line sweep for O.
- **Enforces:** C15, C16, C17, C40-C42, C47.
- **Issues:** #43 (V1702), with slide 7's eight examples as unit tests.
- **Derived from:**
  - OptiWindNet `_retraced_runs` → **Adapt** (`optiwindnet/crossings.py:L1013-1067 @ 44e384c`, MIT);
  - `_shared_run_swaps_sides` → **Adapt** (`L766-822`);
  - crossing kind names → **Adopt** (`L1168-1240`).
- **Not taken:** `find_routeset_crossings` (`L555-600`: straight segments); `repair_routeset_path` (`optiwindnet/repair.py:L307-345`).

### 4.9 `checker.py` (rules half) and the submit gate
- **Responsibility:**
  - **Slide 3's six hard rules** on the submission + the input plan:
    1. coverage (none omitted, split or duplicated);
    2. capacity;
    3. zone (doors / open passages only; not windows, wall-only adjacency or the entrance);
    4. ring;
    5. free space;
    6. hub point (an assigned eligible room, ≥ 0.5 m from walls; may share a node's room).
  - Returns `Violation(rule, severity, hub_id, msg, where)` and never raises; `where` feeds the red overlay.
  - Builds the slide-5 checklist.
  - **The gate (C43):** a submission is written only if every rule passes. Otherwise fall back to the next-ranked arrangement, then the walking skeleton, then write only the sidecar with the reasons.
- **In → out:** `Plan`, `Submission` → `Violation` list + checklist.
- **Enforces:** C1-C15, C18, C26, C34, C37, C39, C43.
- **Issues:** #11, #23 (nexuszephyr); #16, #22 (Adityashandilya555, gate + fallback).
- **Derived from:**
  - OptiWindNet validators → **Adapt** (a list of violations) (`optiwindnet/validating.py:L96-205 @ 44e384c`, MIT);
  - gated ring checks → **Adopt** (`L172-181`);
  - `_walk_ring` → **Adapt** (`optiwindnet/terse.py:L91-107`);
  - GerryChain `contiguous` / `contiguous_components` → **Adapt** (`gerrychain/constraints/contiguity.py:L133-150, L174-190 @ 0edc278`, BSD-3).
- **Not taken:** `_load_mismatches` as an error (reported values are ignored by the organisers; at most a sidecar warning); GerryChain `_are_reachable`.
- **Risks:** a mirror that is wrong in the lenient direction would submit invalid plans → ambiguous cases count strictly, and the slide-7 tests pin the definitions.

### 4.10 `solve.py` and `cli.py`: orchestration
- **Responsibility:**
  - `solve(plan, c)`: the best arrangement by (H, J, area gap), routed and checked;
  - `enumerate(plan, c, k)`: every arrangement with mirror metrics, ranked;
  - `submit()`: the gate + writers (slide-8 submission + sidecar);
  - the CLI commands `hackhive solve`, `check`, `batch`, `present`.
- **In → out:** `Plan`, `Constraints` → `Result` (`Submission` + `Sidecar`) / `Arrangement` list; files → files.
- **Enforces:** C16, C21, C22, C24, C27, C29, C43, C47, C48.
- **Issues:** #16, #22 (Adityashandilya555).
- **Derived from:** built from scratch.

### 4.11 `viz.py` and `app.py`: overlay and demo
- **Responsibility:**
  - the base layer;
  - the slide-5 overlay: zone colours, hub squares and node dots **with their ids**, a numbered ring with the last number as the return, two-lane folds, **violations in red**;
  - the final-presentation export (`export_presentation`: overlay + summary + J breakdown, PNG/SVG);
  - gallery tiles, a cap 5 vs 20 comparison, the Pareto view, the step-through;
  - the Streamlit app with a locked "Problem statement" preset and what-if settings.
- **In → out:** models → matplotlib figures / PNG bytes.
- **Enforces:** C15, C23, C25, C26, C29, C30, C45.
- **Issues:** #5, #7, #12, #17, #24, #25, #26, #29 and #44 (rohit-nandagopal); #27 (Pareto) and #30 (what-if sidebar) (V1702).
- **Derived from:**
  - house-planner group palette + grey for unassigned → **Adapt** (`src/networks/electric/model.ts:L69-70, L83 @ 5c91280`, MIT);
  - circuit polylines → **Adapt** (`src/networks/electric/ElectricLayer.tsx:L402-417`);
  - per-circuit summary table → **Adopt** (`src/networks/electric/ElectricPanel.tsx:L282-310`);
  - OptiWindNet `compare` → **Adapt** (r×c grid, shared axes) (`optiwindnet/plotting.py:L474-491 @ 44e384c`, MIT);
  - legend-title info box → **Adopt** (`L389-402`);
  - "(+k)" excess feeders → **Adapt** as the optimality badge (`optiwindnet/presenting.py:L141-142`);
  - ResPlan `plot_plan` → **Adapt** without geopandas (`resplan_utils.py:L197-246 @ e2b78fe`).
- **Not taken:**
  - house-planner's React / PHP stack and `CABLE_RESERVE` (+15%, which would break the validator's length agreement);
  - OptiWindNet's SVG renderer and its Glasbey palette (CC BY 4.0);
  - ResPlan's room-type fills;
  - Freerouting's live board events (GPL; no slow intermediate state to show);
  - plotly.

### 4.12 `batch.py`: scale check
- **Responsibility:**
  - solve N plans from the `test` split (keyed by plan `id`; `augmented` excluded);
  - write a CSV (H, LB, L, X, O, B, J, area gap, submitted, rules failed, fallback used, ms, status, reason) and a summary whose headline is the **validity rate (target 100% of feasible plans)**;
  - the app's leaderboard tab reads the CSV.
- **Enforces:** C16, C22, C27.
- **Issues:** #32, #33 (nexuszephyr).
- **Derived from:** ResPlan `split.json` keyed by `id` → **Adopt** (`baselines/task3_edge_classifier.py:L247-253`; `README.md:L102-104 @ e2b78fe`, data CC BY 4.0).
- **Not taken:** ResPlan `baselines/` (ML tasks only).

## 5. Data contracts, testing, decisions
### Data contracts
`contracts.py` (pydantic 2) is the single source of truth; the schemas are **generated** (`schemas/submission.schema.json`, `schemas/sidecar.schema.json`), and CI fails if they're stale.

**Submission** (slide 8, keys only):
- `plan_id`, `units:"m"`;
- `hubs[]`, each with `hub_id`, `room_id`, `location{x,y}`, `assigned_rooms` (hub room first), `assigned_nodes` (sorted), `closed_sequence` (ring order, H … H), `route_segments[]{from, to, length_m, polyline:[[x,y],…]}` (in sequence order, chained), `ring_length_m`;
- `reported_metrics{hub_count, total_length_m}`.
- Metres, origin lower-left, y up; ids plan-global.

**Sidecar:** status, violations, bounds (`k_lb`, `k_found`, `proof`), the J breakdown, area gap, settings, provenance, warnings, folded slivers.

Every edge case is decided in `docs/decision_conventions.md`; the format is documented in `docs/output-format.md` (#34).

### Testing strategy
- **The validator is the oracle.** Every issue's acceptance criteria include "validator passes".
- **Fixtures** (`tests/fixtures/`): 7 ResPlan plans chosen from a profile of all 17,000.

  | Plan | Role |
  |---|---|
  | 5088 | small |
  | 3086 | open passage |
  | 1293 | 2 hubs at the LB |
  | 5924 | star: LB 2, needs 3 |
  | 576 | multi-part living, 2 components, needs 6 |
  | 13344 | 3 hubs at cap 5, 1 at cap 20 |
  | 2814 | interior window |

  Each comes with its expected profile numbers, plus hand-made broken solutions, one per rule.
- **Test pyramid:**
  - **unit:** synthetic rasters for the router; pure functions per module;
  - **integration:** `solve()` on the 7 fixtures → validator green + expected hub counts;
  - **end to end:** the CLI on a fixture file; `hackhive batch` on 20-100 test-split plans.
- **Property checks:**
  - every segment is axis-aligned;
  - every room change is inside a door/opening;
  - every ring is `[H, permutation, H]`;
  - routed length ≥ the Manhattan LB (and ≥ the pyvisgraph L1 LB in tests).
- **Each sprint ends** with a pull-and-run-locally verification issue (#18, #35).

### Decisions (ADRs; 001-009 accepted 2026-10-15; 010 from organiser slides 3/6/7/8; 002/004/005/007 amended)
| ADR | Decision | Rejected alternatives |
|---|---|---|
| 001 | Zoning by exhaustive enumeration of contiguous capped partitions, with a guard above 16 rooms | CP-SAT; HGS / PyVRP / GerryChain local search |
| 002 | Hub location + ring order chosen jointly and exactly (insertion + lower-bound pruning; Held-Karp at k ≤ 10) | 1-median hub then TSP; hub fixed in `living` |
| 003 | One bend-aware router on a 10 cm room-label raster; optimise only after measuring | door graph + closed-form L/Z paths (58% of rooms aren't rectangles); visibility graph (any-angle) |
| 004 | Sequential routing + occupancy penalty + 2 rip-up passes; keep the best | full PathFinder (Backlog) |
| 005 | Single-door double run = a centreline fold, counted as overlap, drawn as two lanes | routing two physical parallel paths |
| 006 | ~~Our own JSON; cost with crossing weight 2.0~~ → **superseded by ADR-010** | n/a |
| 010 | The organisers' contract: the slide-8 submission (keys only) + a sidecar; the checker mirror (six rules + H, L, X, O, B, J at 1 mm, slide-7 tests); rank (H, J, area gap); never submit invalid (gate + fallback) | our own format + exporter; one file with extra keys |
| 007 | Strict room graph (one node per polygon; doors top 2; openings with geometry; windows never connect by default); the interior-window checkbox (C37); sliver folding in our generator only (C38) | ResPlan `plan_to_graph` semantics |
| 008 | The ≤ 20 node-cap mode is a Sprint 2 setting (what-if) | Backlog |
| 009 | Streamlit + matplotlib, no plotly | plotly; React |

## 6. Frontend
| Screen | Shows | Solver call | Why it's in scope |
|---|---|---|---|
| Sidebar | plan picker (fixtures, ResPlan id, upload); a locked **Problem statement** preset; a what-if expander (caps, weights, interior-window checkbox) with a WHAT-IF banner | `Constraints.problem_statement()`, `Constraints(...)` | C29, C37: configurable constraints, clearly separated from the problem statement |
| Best | overlay with ids and red violations (C23), per-hub summary (C25), reviewer checklist from the checker (C26), badge (H vs LB) + J breakdown, ring step-through slider, submission/sidecar download, presentation-image download (C45) | `solve(plan, c)` → `Solution`; `viz.draw_solution(step=i)` | exactly slide 5's "expected solution" plus the reviewer list; the badge and step-through only display solver output |
| Arrangements | gallery of every feasible zoning (H, J, checker ✓) with hub-count / node-cap filters; Pareto (hubs vs J) | `enumerate(plan, c)` → `Arrangement` list | C29, C16: all real arrangements under the PS rules, ranked by the PS ranking |
| Cap 5 vs 20 | two overlays side by side + a metrics table | `solve(c5)`, `solve(c20)` | C6: the two sources' cap conflict, with cap 20 labelled WHAT-IF |
| Batch | leaderboard over the test-split sample: validity rate (target 100%), % at LB, mean J and its L / X / O / B parts | reads `batch.csv` from `hackhive batch` | C22: unseen plans, no manual step |
