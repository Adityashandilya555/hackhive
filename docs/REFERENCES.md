# Reference repos (PS4)

These repos solve **different problems** that share our techniques. They're for **design inspiration only**:
- Never copy or vendor their code; re-implement ideas in our own modules.
- The only exception is ResPlan's loader (MIT), which keeps its license notice.
- **Never copy GPL code** (Freerouting).
- Check every idea against our constraints before using it: axis-aligned only, crossings only through doors/open passages, ≤5 nodes and ≤100 m² per hub, closed non-branching rings, and the single-door in-and-out double run.

Clones: `~/refs/ps4/<folder>/` (shallow; `vtr` and `freerouting` are sparse), outside this repo and never imported. Sourcegraph: http://localhost:7081, repos indexed as `github.com/<owner>/<name>`. Every path below was read on 2026-10-15 at the commit shown.

| Folder | Repo | Commit | License | Stages it informs |
|---|---|---|---|---|
| resplan | m-agour/ResPlan | `e2b78fe` | Code MIT, data CC BY 4.0 | parsing, visualization, batch |
| gerrychain | mggg/GerryChain | `0edc278` | BSD-3-Clause | zoning, validation |
| pyvrp | PyVRP/PyVRP | `abe5313` | MIT | zoning (improvement moves) |
| hgs-cvrp | vidalt/HGS-CVRP | `1a92795` | MIT | zoning (neighbourhoods, Split bound) |
| orthoroute | bbenchoff/OrthoRoute | `9e06f2d` | MIT | grid A* routing, congestion |
| vtr | verilog-to-routing/vtr-verilog-to-routing (sparse `vpr/src/route`) | `5c65c86` | MIT (VTR License) | congestion / overlap removal |
| freerouting | freerouting/freerouting (sparse `…/autoroute`) | `5aa1b24` | **GPL-3.0, never copy** | congestion (pass structure), visualization |
| optiwindnet | DTUWindEnergy/OptiWindNet (GitHub mirror of gitlab.windenergy.dtu.dk/TOPFARM/OptiWindNet) | `44e384c` | MIT | validation, congestion, ring topology |
| pyvisgraph | TaipanRex/pyvisgraph | `9472e0d` | MIT | routing backup, length lower bound |
| house-planner | egmalt/house-planner | `5c91280` | MIT | visualization (UI ideas only) |

URL notes: OrthoRoute's canonical repo is `github.com/bbenchoff/OrthoRoute`; the AllSpice copy is a mirror. GerryChain's `tree.py` is now a package, `gerrychain/tree/`.

---

## resplan: m-agour/ResPlan
1. **Purpose:** a 17k residential floor-plan dataset (vector walls/doors/windows/rooms in metres) with utilities that build a room-connectivity graph.
2. **Maps to:** parsing, visualization, batch testing.
3. **Ideas to adopt**
   - `resplan_utils.py:L251-541` `plan_to_graph`: room nodes carry `geometry`/`type`/`area`. `_contact_score` (L365) attaches each door to exactly the two rooms with the longest contact (L352-397), which matters when ≥3 rooms meet at a door's corner. `via_opening` edges (L417-501) are found by subtracting walls from the shared boundary and requiring a gap ≥ `open_passage_min_factor × wall_depth`. That's our "open passage".
   - L318-323: all living parts are unioned into one `living_0` node. **Check the organisers' JSON before counting nodes**, because this changes the node count.
   - `geometry_to_mask` (L158) / `_poly_to_mask` (L142): polygon → raster mask, a starting point for the ~10 cm occupancy grid.
   - `normalize_keys` (L56) and `get_geometries` (L74) normalise geometry keys and (Multi)Polygon handling. `plot_plan` (L197) gives category colours (L37).
4. **Do NOT adopt**
   - The **`fallback` edges** (L518-541). They connect isolated components to the nearest room through walls, which violates doors-only.
   - `via_window` edges (L371-397). Cables never cross windows. Note that L503-516 re-labels interior `via_window` edges as `via_door`, so verify against the organisers' JSON.
   - `add_adjacency_edges` (L626), which marks wall-sharing rooms as adjacent (not passable), and the `perturb_polygon`/`noise`/`augment_geom` augmentation (L93-127).
   - The pickle format (`ResPlan.zip`). We parse the organisers' per-plan JSON.
5. **Sourcegraph:** `repo:^github\.com/m-agour/ResPlan$ file:resplan_utils\.py via_opening OR _contact_score`
6. **License:** code MIT (the only repo we may copy from: loader parts, keep the notice); data CC BY 4.0 (attribute ResPlan in README/outputs).

## gerrychain: mggg/GerryChain
1. **Purpose:** MCMC sampling of contiguous, population-balanced graph partitions (redistricting).
2. **Maps to:** zoning (contiguous partition), validation (contiguity checks).
3. **Ideas to adopt**
   - `gerrychain/constraints/contiguity.py:L10` `_are_reachable`: a BFS that only steps to neighbours in the same part, which is a cheap contiguity check. `single_flip_contiguous` (L54) re-checks only the parts a move touched. `contiguous` (L133) and `contiguous_components` (L174) give the validator.
   - `gerrychain/tree/bipartition_tree.py`: `_calc_pops` (L516) computes subtree sums, and `find_balanced_edge_cuts_memoization` (L622) finds spanning-tree edges whose cut yields a part within bounds. Swap "population" for (area, node count) with **hard upper bounds**.
   - `gerrychain/proposals/tree_proposals.py:L208` `recom`: merge two adjacent parts, draw a spanning tree, re-split. Use it as an improvement move to try to free a hub.
4. **Do NOT adopt:** the MCMC chain / acceptance functions (`chain.py`, `accept.py`) and ensembles. Plans have ~8 eligible rooms, so exact enumeration of connected partitions is feasible, and random spanning trees are only a fallback. Don't use ε-population *balance* either: our limits are hard caps, not balance.
5. **Sourcegraph:** `repo:^github\.com/mggg/GerryChain$ file:constraints/contiguity\.py _are_reachable`
6. **License:** BSD-3-Clause.

## pyvrp: PyVRP/PyVRP
1. **Purpose:** a state-of-the-art vehicle-routing solver (HGS-based, C++ core with Python API).
2. **Maps to:** zoning (improving zones after the first split).
3. **Ideas to adopt**
   - `pyvrp/cpp/search/Relocate.h`, `Swap.h`, `SwapTails.h`: operator shapes. *Relocate* moves one room to an adjacent hub, and *Swap* exchanges two rooms. Re-check contiguity after each move (GerryChain `_are_reachable`).
   - `pyvrp/search/neighbourhood.py:L12` `compute_neighbours`: granular neighbourhoods. For us, that means only consider moves between door-adjacent zones.
   - `pyvrp/PenaltyManager.py`: adaptive penalty for capacity violations, which lets the search pass through slightly infeasible zonings.
   - `pyvrp/search/LocalSearch.py:L17` `LocalSearch`: the loop that applies operators until no improvement.
4. **Do NOT adopt:** the full iterated local search / genetic loop, time windows, pickups, optional clients, multi-depot, the C++ build. Their distance is a matrix; ours is routed cable cost. The brief's `docs/examples` doesn't exist at this commit (only `docs/source`).
5. **Sourcegraph:** `repo:^github\.com/PyVRP/PyVRP$ file:pyvrp/cpp/search/(Relocate|Swap)\.h`
6. **License:** MIT.

## hgs-cvrp: vidalt/HGS-CVRP
1. **Purpose:** the original Hybrid Genetic Search for the capacitated VRP (Vidal).
2. **Maps to:** zoning (local-search neighbourhoods, a fast upper bound on the hub count).
3. **Ideas to adopt**
   - `Program/LocalSearch.cpp:L36-69`: the move dispatch order (`move1-3` RELOCATE, `move4-6` SWAP, `move7` 2-OPT, `move8-9` 2-OPT*), first-improvement and restart. `LocalSearch.h:L169` `swapStar` finds the best swap between two routes with re-insertion.
   - `LocalSearch.h:L181` `run(indiv, penaltyCapacityLS, …)`: capacity as a penalty during local search.
   - `Program/Split.cpp`: split one giant tour into capacity-feasible routes. For us, Split a door-graph DFS order of rooms into ≤100 m² / ≤5-node chunks for a quick hub-count **upper bound**. Contiguity isn't guaranteed, so check it.
4. **Do NOT adopt:** the genetic population, crossover, `Individual`/`Population` (overkill when rings have ≤5 nodes and zoning is small enough to enumerate), CVRPLIB I/O, duration limits.
5. **Sourcegraph:** `repo:^github\.com/vidalt/HGS-CVRP$ file:Program/LocalSearch\.cpp bool LocalSearch::move`
6. **License:** MIT.

## orthoroute: bbenchoff/OrthoRoute
1. **Purpose:** a GPU-accelerated Manhattan PCB autorouter for KiCad, using PathFinder negotiated congestion.
2. **Maps to:** grid A* routing, congestion / overlap removal.
3. **Ideas to adopt** (`orthoroute/algorithms/manhattan/unified_pathfinder.py`, 11.6k lines; read only these)
   - `EdgeAccountant` (L1009): `compute_overuse` (L1104), `update_history` (L1193, history cost with decay and cap), `update_present_ema` (L1258), and `update_costs` (L1268). This is a clean, Python-sized PathFinder cost bookkeeping.
   - `_plan_portals` (L3336) / `_plan_portal_for_pad` (L3377) / `_get_portal_seeds` (L3446): fixed entry cells ("portals") seeded into the search. Model **each door as a portal**: routes may only change rooms through door cells.
   - `ROIExtractor.extract_roi_geometric` (L1916): limit the search to a corridor around source/target. `SimpleDijkstra.find_path_roi` (L2392) is the plain-Dijkstra baseline.
4. **Do NOT adopt:** the GPU/CUDA/Metal kernels (`pathfinder/cuda_*.py`, `metal_dijkstra.py`), multi-layer `Lattice3D` and vias (`via_accounting.py`, `hdi_stack.py`, `layer_peeling.py`), pad escape planning, KiCad plugin code.
5. **Sourcegraph:** `repo:^github\.com/bbenchoff/OrthoRoute$ file:unified_pathfinder\.py def update_history`
6. **License:** MIT.

## vtr: verilog-to-routing/vtr-verilog-to-routing (sparse: `vpr/src/route`)
1. **Purpose:** an FPGA place-and-route toolchain. VPR's router is the reference PathFinder implementation.
2. **Maps to:** congestion / overlap removal.
3. **Ideas to adopt**
   - `vpr/src/route/route_common.h:L134-150` `get_single_rr_cong_cost`: **cost = base_cost × acc_cost × pres_cost**, with `pres_cost = 1 + pres_fac × (overuse + 1)` when adding the route would exceed capacity. Use it per grid cell (or cell edge) with capacity 1 cable.
   - `route_common.cpp:L228` `pathfinder_update_acc_cost_and_overuse_info`: after each iteration, history `acc_cost += acc_fac × overuse`.
   - `route.cpp:L402-454`: the `pres_fac` schedule (start `initial_pres_fac`, multiply by `pres_fac_mult` each iteration). `route_common.cpp:L278` `pathfinder_update_cost_from_route_tree` handles rip-up / re-add occupancy.
   - Our twist: door cells need capacity > 1 (several rings pass), and a single-door room's double run is **one ring's own fold**, not an overlap.
4. **Do NOT adopt:** timing-driven costs and budgets (`route_budgets.*`), router lookahead maps, parallel/decomposed netlist routers, RR-graph generation, FPGA segment statistics.
5. **Sourcegraph:** `repo:^github\.com/verilog-to-routing/vtr-verilog-to-routing$ file:vpr/src/route/route_common\.h get_single_rr_cong_cost`
6. **License:** MIT (VTR License; bundled third-party tools keep their own licenses).

## freerouting: freerouting/freerouting (sparse: `src/main/java/app/freerouting/autoroute`)
1. **Purpose:** a Java PCB autorouter (Specctra DSN) with batch passes, maze search and rip-up.
2. **Maps to:** congestion (pass structure), visualization (live progress).
3. **Ideas to adopt (concepts only, never code)**
   - `autoroute/pipeline/BatchAutorouter.java:L600` `runBatchLoop`: repeated passes. Route everything, rip up conflicts, re-route at higher cost, then an optimisation pass (`pipeline/BatchOptimizer.java`).
   - `autoroute/pipeline/AutoroutePassRunner.java:L97,L231,L506` call `fireBoardUpdatedEvent` after each pass (`events/BoardUpdatedEvent.java`). For the streamlit demo, yield a snapshot per pass and redraw.
   - `autoroute/maze/MazeRipupResolver.java:L219` `enterThroughSmallDoor`: their expansion "doors" connect free-space rooms, which is conceptually close to our door-only crossings.
4. **Do NOT adopt:** **any code (GPL-3.0)**, Specctra DSN I/O, vias and layers, push-and-shove (`maze/MazeTraceShover.java`), fanout (`pipeline/BatchFanout.java`).
5. **Sourcegraph:** `repo:^github\.com/freerouting/freerouting$ file:autoroute/pipeline/AutoroutePassRunner\.java fireBoardUpdatedEvent`
6. **License:** **GPL-3.0**, read only, never copy.

## optiwindnet: DTUWindEnergy/OptiWindNet
1. **Purpose:** optimising wind-farm collector-cable networks (capacitated trees and rings, obstacle-aware).
2. **Maps to:** validation, congestion / overlap removal, ring topology.
3. **Ideas to adopt**
   - `optiwindnet/validating.py:L18` `_validate_ringed`: **ring-shape + capacity violation checks** returning a list of messages. `validate_topology(S, capacity)` (L96) and `validate_routeset` (L237) follow the same pattern; our validator should return violations, not raise.
   - `optiwindnet/crossings.py:L1013` `_retraced_runs`: finds corridors a polyline traverses twice and treats a **fold** (turning back along the same corridor) as never crossing. That's exactly our single-door in-and-out double run. `find_geometric_crossings` (L1168) and `find_routeset_crossings` (L555) handle crossing detection.
   - `optiwindnet/repair.py:L307` `repair_routeset_path(S', A, ringed=False)`: repair crossings by swapping path segments, with a `ringed` mode.
   - `optiwindnet/mesh.py:L90` `_astar_path`: A* around Shapely obstacles on a mesh.
4. **Do NOT adopt:** wind-farm specifics (turbines, substations, gates/feeders, cable-type tables), the MILP models (`optiwindnet/MILP/`), any-angle straight edges, the CDT mesh as the primary router (ours is an axis-aligned grid).
5. **Sourcegraph:** `repo:^github\.com/DTUWindEnergy/OptiWindNet$ file:optiwindnet/(validating|crossings)\.py _validate_ringed OR _retraced_runs`
6. **License:** MIT.

## pyvisgraph: TaipanRex/pyvisgraph
1. **Purpose:** visibility graphs and shortest paths around polygon obstacles.
2. **Maps to:** grid A* routing (backup), cable-length lower bound.
3. **Ideas to adopt**
   - `pyvisgraph/vis_graph.py:L59` `VisGraph.build`, `L108` `shortest_path`, and `L100` `update` (add origin/destination without a rebuild).
   - `pyvisgraph/visible_vertices.py:L38` `visible_vertices`, with `edge_in_polygon` (L140) and `point_in_polygon` (L151).
   - The visibility shortest path is a **lower bound** on axis-aligned length, useful for sanity-checking A* and reporting the gap.
4. **Do NOT adopt:** as the router itself (edges are any-angle, not axis-aligned), the multiprocessing build, pickle save/load. It's been unmaintained since 2018.
5. **Sourcegraph:** `repo:^github\.com/TaipanRex/pyvisgraph$ file:visible_vertices\.py def visible_vertices`
6. **License:** MIT.

## house-planner: egmalt/house-planner
1. **Purpose:** a self-hosted house planner (React/TS + PHP) with 2D/3D views and utility network layers (electrical, water, sewer).
2. **Maps to:** visualization (UI ideas only).
3. **Ideas to adopt**
   - `src/networks/electric/ElectricLayer.tsx:L187` `ElectricLayer`: draws points and circuits as a layer over the plan. `docs/screenshots/networks.png` shows the look.
   - `src/networks/electric/model.ts`: point kinds (L23), circuit kinds (L53), `CABLE_RESERVE` (L21, a slack factor on cable length, which could feed our reported length). `ElectricPanel.tsx` is a per-circuit summary panel, matching our per-hub summary.
   - `docs/plan-format.md` shows how a plan file is documented.
4. **Do NOT adopt:** the React/TypeScript/PHP stack (our overlay is matplotlib + streamlit), 3D, priced bill of materials, sewer/water/heating networks.
5. **Sourcegraph:** `repo:^github\.com/egmalt/house-planner$ file:src/networks/electric/ ElectricLayer`
6. **License:** MIT.
