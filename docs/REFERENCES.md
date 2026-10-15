# Reference repos (PS4)

These repos solve **different problems** that share our techniques. They're for **design inspiration only**:
- Never copy or vendor their code; re-implement ideas in our own modules.
- The only exception is ResPlan's loader (MIT), which keeps its license notice.
- **Never copy GPL code** (Freerouting).
- Check every idea against our constraints before using it: axis-aligned only, crossings only through doors/open passages, ≤5 nodes and ≤100 m² per hub, closed non-branching rings, and the single-door in-and-out double run.

Clones: `~/refs/ps4/<folder>/` (shallow; `vtr` and `freerouting` are sparse), outside this repo and never imported. Sourcegraph: http://localhost:7081, repos indexed as `github.com/<owner>/<name>`. Freerouting is **not indexed** (2 GB history, and its ideas are covered by VTR and OrthoRoute), so search its local sparse clone with `rg`. Every path below was read on 2026-10-15 at the commit shown. The kickoff research (2026-10-15) corrected several claims; corrections are applied in place, and the per-idea Adopt / Adapt / Skip verdicts are in `docs/ARCHITECTURE.md` §3-4.

| Folder | Repo | Commit | License | Stages it informs |
|---|---|---|---|---|
| resplan | m-agour/ResPlan | `e2b78fe` | Code MIT, data CC BY 4.0 | parsing, visualization, batch |
| gerrychain | mggg/GerryChain | `0edc278` | BSD-3-Clause | zoning, validation |
| pyvrp | PyVRP/PyVRP | `abe5313` | MIT | zoning (improvement moves) |
| hgs-cvrp | vidalt/HGS-CVRP | `1a92795` | MIT | zoning (neighbourhoods, Split bound) |
| orthoroute | bbenchoff/OrthoRoute | `9e06f2d` | MIT | grid A* routing, congestion |
| vtr | verilog-to-routing/vtr-verilog-to-routing (sparse `vpr/src/route`) | `5c65c86` | MIT (VTR License) | congestion / overlap removal |
| freerouting | freerouting/freerouting (sparse `…/autoroute`) | `5aa1b24` | **GPL-3.0, never copy** | optional, not in Sourcegraph: congestion (pass structure), visualization |
| optiwindnet | DTUWindEnergy/OptiWindNet (GitHub mirror of gitlab.windenergy.dtu.dk/TOPFARM/OptiWindNet) | `44e384c` | MIT | validation, congestion, ring topology |
| pyvisgraph | TaipanRex/pyvisgraph | `9472e0d` | MIT | routing backup, length lower bound |
| house-planner | egmalt/house-planner | `5c91280` | MIT | visualization (UI ideas only) |

URL notes: OrthoRoute's canonical repo is `github.com/bbenchoff/OrthoRoute`; the AllSpice copy is a mirror. GerryChain's `tree.py` is now a package, `gerrychain/tree/`.

---

## resplan: m-agour/ResPlan
1. **Purpose:** a 17k residential floor-plan dataset (vector walls/doors/windows/rooms in **canvas units**, not metres: metres per unit = √(`area` ÷ `inner.area`), per plan) with utilities that build a room-connectivity graph.
2. **Maps to:** parsing, visualization, batch testing.
3. **Ideas to adopt**
   - `resplan_utils.py:L251-541` `plan_to_graph`: room nodes carry `geometry`/`type`/`area`. `_contact_score` (L365) scores candidate rooms by contact length. It always keeps the top two but **can keep a third** (≥ 25% of the best), which makes triangles (L374-397); we keep only the top two. `via_opening` edges (L417-501): the main test intersects the two rooms' outlines grown by 0.55·wall_depth, subtracts **walls only**, and keeps a piece whose bbox long side ≥ 2·wall_depth (L449-470); a second test checks the shared boundary (L476-501). That's our "open passage", but we must also subtract windows and doors, or window gaps become "openings".
   - L317-325: all living parts are unioned into one `living_0` node. **Check the organisers' JSON before counting nodes**, because this changes the node count.
   - `normalize_keys` (L56) and `get_geometries` (L74) normalise geometry keys and (Multi)Polygon handling. `plot_plan` (L197; imports geopandas at L226) and category colours (L36-48): keep only the wall/door/window styling, since room fill is the zone colour.
   - `split.json` is keyed by plan **`id`** (train 13,053 · val 1,632 · test 1,632 · augmented 683). `baselines/` is ML-only (room labelling, generation, plan-to-graph): skip it.
4. **Do NOT adopt**
   - The **`fallback` edges** (L518-541). They connect isolated components to the nearest room through walls, which violates doors-only.
   - `via_window` edges, which come from the same loop as doors (the windows tuple at L372). Cables never cross windows. Note that L503-516 re-labels interior `via_window` edges as `via_door`, so verify against the organisers' JSON.
   - `add_adjacency_edges` (L626), which marks wall-sharing rooms as adjacent (not passable), and the `perturb_polygon`/`noise`/`augment_geom` augmentation (L93-127).
   - `geometry_to_mask` (L158) / `_poly_to_mask` (L142): they need `cv2`, imported at module level (L23). Never import `resplan_utils` itself.
   - `centroid` (L84): the largest part's centroid can fall outside the room.
   - The pickle format (`ResPlan.zip`). We parse the organisers' per-plan JSON.
5. **Sourcegraph:** `repo:^github\.com/m-agour/ResPlan$ file:resplan_utils\.py via_opening OR _contact_score`
6. **License:** code MIT (the only repo we may copy from: loader parts, keep the notice); data CC BY 4.0 per `LICENSE`, but `croissant.json` says CC BY-NC-SA 4.0. Attribute ResPlan, and treat the data as non-commercial.

## gerrychain: mggg/GerryChain
1. **Purpose:** MCMC sampling of contiguous, population-balanced graph partitions (redistricting).
2. **Maps to:** zoning (contiguous partition), validation (contiguity checks).
3. **Ideas to adopt**
   - `gerrychain/constraints/contiguity.py:L10` `_are_reachable`: a BFS that only steps to neighbours in the same part, which is a cheap contiguity check. `single_flip_contiguous` (L54) re-checks only the parts a move touched. `contiguous` (L133, which calls `gerrychain/graph/graph.py:L2339 is_node_set_connected`, i.e. `nx.is_connected` on the networkx backend) and `contiguous_components` (L174) give the validator. `_are_reachable` serves the incremental `single_flip_contiguous` only.
   - `gerrychain/tree/bipartition_tree.py`: `_calc_pops` (L516) computes subtree sums, and `find_balanced_edge_cuts_memoization` (L622) finds spanning-tree edges whose cut yields a part within bounds. Swap "population" for (area, node count) with **hard upper bounds**.
   - `gerrychain/proposals/tree_proposals.py:L208` `recom`: merge two adjacent parts, draw a spanning tree, re-split.
   - **Verdict after research: Skip** the tree cuts and `recom`. Exhaustive enumeration (≤ 116 zonings per plan, ≤ 16 ms) is exact and lists every arrangement.
4. **Do NOT adopt:** the MCMC chain / acceptance functions (`chain.py`, `accept.py`) and ensembles. Plans have ~8 eligible rooms, so exact enumeration of connected partitions is feasible, and random spanning trees are only a fallback. Don't use ε-population *balance* either: our limits are hard caps, not balance.
5. **Sourcegraph:** `repo:^github\.com/mggg/GerryChain$ file:constraints/contiguity\.py _are_reachable`
6. **License:** BSD-3-Clause.

## pyvrp: PyVRP/PyVRP
1. **Purpose:** a state-of-the-art vehicle-routing solver (HGS-based, C++ core with Python API).
2. **Maps to:** zoning (improving zones after the first split).
3. **Ideas to adopt** (verdict after research: the operators, `LocalSearch` and `PenaltyManager` are **Skip**, because exact enumeration replaces local search. PyVRP has **no intra-route 2-opt**: `SwapTails` is inter-route only. The useful idea is `pyvrp/cpp/CostEvaluator.h:L18-25, L140-153`, exact vs shortcut cost evaluation, which becomes our lower-bound early exit when scoring rings.)
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
   - `Program/Split.cpp`: split one giant tour into capacity-feasible routes. **Wrong as an upper bound for us**: in star-shaped plans (the norm in ResPlan), consecutive chunks of a DFS order aren't contiguous. Keep only the lower-bound line `Program/Split.cpp:L3-6` (max of the capacity ratios).
4. **Do NOT adopt:** the genetic population, crossover, `Individual`/`Population` (overkill when rings have ≤5 nodes and zoning is small enough to enumerate), CVRPLIB I/O, duration limits.
5. **Sourcegraph:** `repo:^github\.com/vidalt/HGS-CVRP$ file:Program/LocalSearch\.cpp bool LocalSearch::move`
6. **License:** MIT.

## orthoroute: bbenchoff/OrthoRoute
1. **Purpose:** a GPU-accelerated Manhattan PCB autorouter for KiCad, using PathFinder negotiated congestion.
2. **Maps to:** grid A* routing, congestion / overlap removal.
3. **Ideas to adopt** (`orthoroute/algorithms/manhattan/unified_pathfinder.py`, 11.6k lines; read only these)
   - `EdgeAccountant` (L1009): `compute_overuse` (L1104), `update_history` (L1193, history cost with decay and cap), `update_present_ema` (L1258), and `update_costs` (L1268). This is a clean, Python-sized PathFinder cost bookkeeping.
   - `_plan_portals` (L3336) / `_plan_portal_for_pad` (L3377) / `_get_portal_seeds` (L3446): per-pad escape cells snapped to the lattice, seeded into the search (heap seeding at L2515-2526). These are **not** region connectors: "each door as a portal" (routes change rooms only through door cells) is our own adaptation.
   - `ROIExtractor.extract_roi_geometric` (L1916): limit the search to a corridor around source/target. `SimpleDijkstra.find_path_roi` (L2392) is the plain-Dijkstra baseline; it has no heading/bend state, so we add (cell, heading).
4. **Do NOT adopt:** the GPU/CUDA/Metal kernels (`pathfinder/cuda_*.py`, `metal_dijkstra.py`), multi-layer `Lattice3D` and vias (`via_accounting.py`, `hdi_stack.py`, `layer_peeling.py`), pad escape planning, KiCad plugin code.
5. **Sourcegraph:** `repo:^github\.com/bbenchoff/OrthoRoute$ file:unified_pathfinder\.py def update_history`
6. **License:** MIT.

## vtr: verilog-to-routing/vtr-verilog-to-routing (sparse: `vpr/src/route`)
1. **Purpose:** an FPGA place-and-route toolchain. VPR's router is the reference PathFinder implementation.
2. **Maps to:** congestion / overlap removal.
3. **Ideas to adopt**
   - `vpr/src/route/route_common.h:L134-150` `get_single_rr_cong_cost`: **cost = base_cost × acc_cost × pres_cost**, with `pres_cost = 1 + pres_fac × (overuse + 1)` when adding the route would exceed capacity. Use it per grid cell (or cell edge) with capacity 1 cable.
   - `route_common.cpp:L228` `pathfinder_update_acc_cost_and_overuse_info`: after each iteration, history `acc_cost += acc_fac × overuse`.
   - `route.cpp:L449-457`: the per-iteration `pres_fac` schedule (start `initial_pres_fac`, multiply by `pres_fac_mult`); L402 is only a reset after a `first_iter_pres_fac` reroute. The schedule and history are Backlog only: our capacity is soft (overlap is a cost), so we use sequential routing + rip-up (ADR-004). `route_common.cpp:L278` `pathfinder_update_cost_from_route_tree` handles rip-up / re-add occupancy.
   - Our twist: door cells need capacity > 1 (several rings pass), and a single-door room's double run is **one ring's own fold**: a same-line retrace counts as overlap (slide 7), two parallel runs don't.
4. **Do NOT adopt:** timing-driven costs and budgets (`route_budgets.*`), router lookahead maps, parallel/decomposed netlist routers, RR-graph generation, FPGA segment statistics.
5. **Sourcegraph:** `repo:^github\.com/verilog-to-routing/vtr-verilog-to-routing$ file:vpr/src/route/route_common\.h get_single_rr_cong_cost`
6. **License:** MIT (VTR License; bundled third-party tools keep their own licenses).

## freerouting: freerouting/freerouting (sparse: `src/main/java/app/freerouting/autoroute`)
1. **Purpose:** a Java PCB autorouter (Specctra DSN) with batch passes, maze search and rip-up.
2. **Maps to:** congestion (pass structure), visualization (live progress).
3. **Ideas to adopt (concepts only, never code)**
   - `autoroute/pipeline/BatchAutorouter.java:L600` `runBatchLoop`: repeated passes. Route everything, rip up conflicts, re-route at higher cost, then an optimisation pass (`pipeline/BatchOptimizer.java`).
   - `autoroute/pipeline/AutoroutePassRunner.java:L97,L231,L506` call `fireBoardUpdatedEvent` (defined at `pipeline/NamedAlgorithm.java:L113-118`; another call at `pipeline/AutorouteBatchLoop.java:L179`) after each pass (`events/BoardUpdatedEvent.java`). Verdict: skip the live view. Our zoning is instant and the ring step-through gives the effect; at most `route_all` returns a per-pass snapshot list.
   - `autoroute/expansion/ExpansionDoor.java:L10`: expansion "doors" connect free-space rooms, which is conceptually close to our door-only crossings. `autoroute/maze/MazeRipupResolver.java:L219` `enterThroughSmallDoor` is a **clearance check** (does the trace fit the door), not the door model.
4. **Do NOT adopt:** **any code (GPL-3.0)**, Specctra DSN I/O, vias and layers, push-and-shove (`maze/MazeTraceShover.java`), fanout (`pipeline/BatchFanout.java`).
5. **Search (not in Sourcegraph):** `rg -n fireBoardUpdatedEvent ~/refs/ps4/freerouting/src/main/java/app/freerouting/autoroute`
6. **License:** **GPL-3.0**, read only, never copy.

## optiwindnet: DTUWindEnergy/OptiWindNet
1. **Purpose:** optimising wind-farm collector-cable networks (capacitated trees and rings, obstacle-aware).
2. **Maps to:** validation, congestion / overlap removal, ring topology.
3. **Ideas to adopt**
   - `optiwindnet/validating.py:L18` `_validate_ringed`: **ring-shape + capacity violation checks** returning a list of messages. `validate_topology(S, capacity)` (L96) and `validate_routeset` (L237) follow the same pattern; our validator should return violations, not raise.
   - `optiwindnet/crossings.py:L1013` `_retraced_runs`: finds corridors a polyline traverses twice and treats a **fold** (turning back along the same corridor) as never crossing. That's exactly our single-door in-and-out double run. But `_retraced_runs` is **skipped for closed routes** (`crossings.py:L1098-1101`), so we apply the fold idea to rings ourselves. `_shared_run_swaps_sides` (L766) is the side test we adapt for crossings. `find_geometric_crossings` (L1168) gives the crossing kinds; `find_routeset_crossings` (L555) compares straight segments only, so skip it.
   - `optiwindnet/repair.py:L307` `repair_routeset_path`: it **moves one node between paths** (leaf/hook splits only); it doesn't swap segments. Skip it: it would change zone membership after zoning.
   - `optiwindnet/mesh.py:L90` `_astar_path`: A* over a prebuilt adjacency with a Euclidean heuristic (Shapely is used elsewhere, e.g. the STRtree at L61-71). We keep its idea of storing the straight-line distance next to the routed one.
   - Also useful: `optiwindnet/terse.py:L91-107` `_walk_ring` (ring check), `validating.py:L208` `_load_mismatches` (stated vs recomputed), `plotting.py:L389-402` (info box) and `L474-491` (`compare`), `presenting.py:L141-142` (the "(+k)" excess-feeders badge). Its Glasbey palette (`themes.py`) is CC BY 4.0, not MIT.
4. **Do NOT adopt:** wind-farm specifics (turbines, substations, gates/feeders, cable-type tables), the MILP models (`optiwindnet/MILP/`), any-angle straight edges, the CDT mesh as the primary router (ours is an axis-aligned grid).
5. **Sourcegraph:** `repo:^github\.com/DTUWindEnergy/OptiWindNet$ file:optiwindnet/(validating|crossings)\.py _validate_ringed OR _retraced_runs`
6. **License:** MIT.

## pyvisgraph: TaipanRex/pyvisgraph
1. **Purpose:** visibility graphs and shortest paths around polygon obstacles.
2. **Maps to:** grid A* routing (backup), cable-length lower bound.
3. **Ideas to adopt**
   - `pyvisgraph/vis_graph.py:L59` `VisGraph.build`, `L108` `shortest_path`, and `L100` `update` (add origin/destination without a rebuild).
   - `pyvisgraph/visible_vertices.py:L38` `visible_vertices`, with `edge_in_polygon` (L140) and `point_in_polygon` (L151).
   - The visibility shortest path is a **lower bound** on axis-aligned length, useful for sanity-checking A* and reporting the gap. Its weights are Euclidean, hard-coded at `shortest_path.py:L54`; reweight the edges to L1 for a tighter bound. Polygons can't have holes, so the outside of `inner` must be split into frame pieces.
4. **Do NOT adopt:** as the router itself (edges are any-angle, not axis-aligned), the multiprocessing build, pickle save/load. It's been unmaintained since 2018.
5. **Sourcegraph:** `repo:^github\.com/TaipanRex/pyvisgraph$ file:visible_vertices\.py def visible_vertices`
6. **License:** MIT.

## house-planner: egmalt/house-planner
1. **Purpose:** a self-hosted house planner (React/TS + PHP) with 2D/3D views and utility network layers (electrical, water, sewer).
2. **Maps to:** visualization (UI ideas only).
3. **Ideas to adopt**
   - `src/networks/electric/ElectricLayer.tsx:L187` `ElectricLayer`: draws points and circuits as a layer over the plan. `docs/screenshots/networks.png` shows the look.
   - `src/networks/electric/model.ts`: palette per circuit + grey for unassigned (L69-70, L83). `ElectricPanel.tsx:L282-310` is a per-circuit summary table, matching our per-hub summary.
   - Do not adopt `CABLE_RESERVE` (L21, +15% slack): it would make our reported length disagree with the validator. Point kinds (L23) and circuit kinds (L53) are electrical-domain only.
   - `docs/plan-format.md` (in Russian) shows how a plan file is documented: version, "the schema wins", units first, inline examples. That's the pattern for our `docs/output-format.md`.
4. **Do NOT adopt:** the React/TypeScript/PHP stack (our overlay is matplotlib + streamlit), 3D, priced bill of materials, sewer/water/heating networks.
5. **Sourcegraph:** `repo:^github\.com/egmalt/house-planner$ file:src/networks/electric/ ElectricLayer`
6. **License:** MIT.
