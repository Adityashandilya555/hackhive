# Decision conventions: how hackhive handles every edge case

One page that answers "what do we do when…?" for the PS4 rules. Each row gives:
- the decision;
- its source: an organiser slide, a C-id from the constraint table in `docs/ARCHITECTURE.md` §1, or an ADR in §5;
- where it is enforced;
- the test that pins it.

If code and this page disagree, fix whichever is wrong, then update this page in the same PR.

## Principles
1. **The organisers' slides win.** Slides 1-8 of "UR Hackathon 2026H2" are the rules. The Honeywell PS4 text only fills gaps.
2. **Never submit an invalid plan.** An invalid or missing plan scores zero (slide 6). Every submission passes our checker mirror first. If the best design fails, we submit the walking-skeleton design; if that fails too, we submit nothing and the sidecar says why.
3. **Where the slides are ambiguous:**
   - the **solver avoids** the ambiguous geometry;
   - the **checker mirror counts the stricter way**, so our J is never lower than the organisers' J could be.
   - Ambiguous rows are marked ⚠.
4. **What-ifs are never submitted.** The ≤ 20 node cap, the interior-window reading and changed weights are shown in the app, labelled WHAT-IF, and written only to the sidecar.

## 1. Units, coordinates, ids
| Edge case | Decision | Source | Enforced by · test |
|---|---|---|---|
| Drawing units → metres | `scale = sqrt(inner.area / area)` units per metre, computed once per plan before any limit or clearance check; metres = units ÷ scale. Never use `net_area` | slide 4; C19 | `io.py` · `test_io::scale` |
| Output frame | metres, **origin lower-left, y up**. ResPlan is already y-up (`plot_plan` never inverts y), so the default is `(x, y) ÷ scale`, no flip, no translation. `Constraints.flip_y` / `origin_shift` exist for an organiser input that differs | slide 8; C24; ADR-010 | `io.py`, `contracts.py` · `test_io::frame` |
| Output precision | coordinates rounded to 0.1 mm (4 decimals), below the 1 mm tolerance. After rounding, every segment is exactly axis-aligned: x equal or y equal | slides 6-7 tolerance; C41 | `contracts.Submission` · `test_contracts::axis_exact` |
| Ids | organiser ids are used as given. For ResPlan dev plans we generate:<br>• `PLAN_<id>`;<br>• rooms `R01…`, ordered living → bedroom → kitchen → storage, then polygon index;<br>• nodes `N1…`, in room order;<br>• hubs `H1…`, in output order | slide 8; C46 | `io.py` · `test_io::ids` |
| Input format | unknown (slide 8 shows only the output), so the adapter in `io.py` reads ResPlan-shaped geometry plus an optional `nodes` list | C46; A1 | `io.read_json` |

## 2. Rooms and nodes
| Edge case | Decision | Source | Enforced by · test |
|---|---|---|---|
| Multi-part `living` | each polygon is one room with one node | slide 2; C2 | `io.py` · fixture 576 |
| Organiser gives node points | used exactly as given, never moved and never merged | slide 2; C3, C38 | `io.py`, checker coverage rule |
| ResPlan dev plan, no node points | our generator puts the node at the room's max-clearance point (maximum inscribed circle) and records `clearance_m` | A2 | `io.py` · `test_io::nodes` |
| A living sliver can't fit a point 0.5 m from walls (41 parts in 38 ResPlan plans) | **generator only:** fold it into the adjacent living part (the nearest one if none touches), give it no node, and list it in the sidecar. Never applied to organiser input | C38 (Gate 2) | `io.py` · `test_io::sliver_fold` |
| Any other room with clearance < 0.5 m | keep the node at the max-clearance point and add a warning in the sidecar | A2, A9 | `io.py` |
| Invalid polygon | `make_valid(method="structure")`, then flatten to polygons | slide 2; C20 | `io.py` · `test_io::bowtie` |
| Excluded rooms (bathroom, balcony, stair, garden, pool, parking, land) | no node, no hub, never in `assigned_rooms`. Cables may pass through them (C33) | slide 2; C4 | checker coverage + hub rules |
| Eligible room > 100 m² | no valid design exists: no submission, the sidecar says `infeasible: room > 100 m²` (428 ResPlan plans; the organisers exclude these from the benchmark) | slide 2; C27 | `solve.py` · `test_solve::infeasible` |

## 3. Zones
| Edge case | Decision | Source | Enforced by · test |
|---|---|---|---|
| What links two zone rooms | only a **door** or an **open passage** between them. **Windows, wall-only adjacency and the entrance (front door) do not count** | slide 3 rule 3; C8/C34 | `graph.py` · fixture 2814, `test_graph::front_door` |
| Two eligible rooms linked only through an excluded room | not linked: excluded rooms never connect a zone | C34 | `graph.eligible_graph` |
| Window drawn on an interior wall (ResPlan quirk, e.g. plan 2814) | **an obstacle**. The checkbox "treat as doors" is a what-if only, never submitted (it would break slide 3 rules 3 and 5) | C14, C37 | `graph.py` · fixture 2814 |
| Doorless eligible room | its own zone, with hub and node in the same room | C34 | `zoning.py` · `test_zoning::doorless` |
| A zone with no room that can host a hub (see §4) | not a feasible zone; enumeration skips it. Measured over ResPlan: this **never** raises the minimum hub count, but makes 36 plans impossible (sliver rooms) → no submission + sidecar | slide 3 rule 6; C18 | `zoning.py` · `test_zoning::hub_capable` |
| Disconnected eligible graph | zones are found per connected component | C34 | `zoning.py` · fixture 576 |
| Coverage | every eligible room **and its node** in exactly one hub: none omitted, split or duplicated. A node always goes with its room | slide 3 rule 1; C1/C9 | checker rule 1 |
| A room without a node in the input | still assigned to exactly one hub (room coverage); there's just no node to visit | slide 3 rule 1 | checker rule 1 |
| More than 16 eligible rooms in one component | branch-and-bound for the minimum; arrangements paged; UI note | ADR-001 | `zoning.py` (Backlog) |

## 4. Hubs
| Edge case | Decision | Source | Enforced by · test |
|---|---|---|---|
| Where a hub may sit | inside an eligible room **assigned to that hub** (`room_id ∈ assigned_rooms`), **≥ 0.5 m from walls**. We measure to the room polygon's boundary (conservative: door edges count too) and keep a 1 cm margin (≥ 0.51 m) so rounding can't break it | slide 3 rule 6; C18 | `rings.hub_candidates` · `test_checker::hub_point` |
| Hub and node in the same room | allowed | slide 3 rule 6 | n/a |
| Hub on a node point | **never**: we keep the hub ≥ 0.10 m (one grid cell) from every node point. That avoids zero-length legs and ambiguous meetings at a shared point ⚠ | convention | `rings.py` · `test_rings::hub_off_nodes` |
| Hub on another ring's cable | the router never routes through a hub or node point that isn't the leg's own endpoint ⚠ | convention (X ambiguity, §6) | `route.py` · `test_route::foreign_points` |
| How many hubs | the minimum over all valid zonings (proven by exhaustive search). The lower bound max(⌈area/100⌉, ⌈nodes/5⌉) is reported but isn't always reachable (77.7% of plans) | slides 2, 3; C7, C16 | `zoning.py`, badge |

## 5. Rings and routes
| Edge case | Decision | Source | Enforced by · test |
|---|---|---|---|
| Ring shape | `closed_sequence` = [H, every assigned node once, H]. The `route_segments` are in the same order: segment i goes `from` sequence[i] `to` sequence[i+1] | slide 3 rule 4; C10 | checker rule 4 |
| Leg endpoints | the first polyline point = the `from` location and the last point = the `to` location, within 1 mm; consecutive legs chain | slide 8; C41 | checker · `test_checker::chain` |
| Free space | each polyline stays inside `inner`, outside walls and windows, horizontal/vertical only, and changes rooms only through door or open-passage gaps | slide 3 rule 5; C12-C14 | `grid.py` move rule; checker rule 5 |
| The entrance (front door) | not a passage: cable never leaves `inner` | slide 3 rules 3, 5 | `grid.py` (front door painted blocked) |
| Cable through another zone's room or an excluded room | allowed (the rules don't forbid it); it costs only through X and O | C33 | n/a |
| **Single-door room** | both are legal, and the router takes whichever gives the **lower J**:<br>(a) a **same-line retrace** through the door: O += shared length, and ⚠ the divergence point may count as a crossing;<br>(b) **two parallel runs** on different lines (≥ 1 grid cell apart, so ≫ 1 mm): O = 0 and X = 0, but more bends and a little more L | slide 3 rule 4; slide 7; C15; ADR-005 (amended) | `route.py` · `test_route::single_door_choice` |
| One-node ring H → N → H | same choice as a single-door room. Out-and-back on one straight line of length d: L = 2d, O = d, B = 2 (a reversal at N and at H), so J = 3d + 1. A thin rectangle loop: L ≈ 2d + 0.2, O = 0, B = 4, so J ≈ 2d + 2.2. **The rectangle wins for d > 1.2 m.** | slides 6-7 | `rings.py` / `route.py` · `test_route::one_node_ring` |
| Door narrower than the lanes needed | pack the lanes at 1 grid cell; if they don't fit, fall back to a same-line retrace (legal, costs O) | ADR-005 | `route.py` |

## 6. Metrics (our checker mirror; slides 6-7)
| Edge case | Decision | Source | Test (slide 7 examples marked §7) |
|---|---|---|---|
| L | the sum of polyline lengths over every leg of every ring, return legs included. A retraced stretch counts each time | slides 6-7 | §7: rectangle 4+2+3+3 → **L = 12**; out-and-back 3 m → **L = 6** |
| X, cables crossing in the open | +1 per point, inside one ring or between rings | slides 6-7 | §7: plus-shaped crossing → **X = 1** |
| X, legs meeting at a node or hub | 0 | slide 7 | §7: legs meet at a node → **X = 0** |
| X, T-touch (a vertex on another cable's interior), away from nodes and hubs | counts 1 ⚠ (strict; the router avoids it) | slide 6 "meet" | `test_checker::t_touch` |
| X, the ends of a shared same-line stretch where the cables diverge | each divergence point away from a node or hub counts 1 ⚠ (strict reading of "points where two cables meet"); so a retrace is chosen only if it's still cheaper | slide 6 | `test_checker::divergence` |
| X, many cables through one point | once per point | slide 7 | `test_checker::once_per_point` |
| O | for every stretch covered by k legs (any rings) on the same line: (k − 1) × length. Lines within 1 mm are the same line; parallel lines further apart don't count | slides 6-7 | §7: 2 m shared → **O = 2**; parallel runs → **O = 0** |
| O, collinear segments touching only at an endpoint | 0 | slide 7 | `test_checker::end_touch` |
| B | +1 at every polyline vertex where the direction changes, at every node (incoming vs outgoing direction) and at the hub (closing the ring). A reversal counts once | slides 6-7 | §7: S-shape → **B = 2**; straight through a node → **B = 0** |
| Duplicate or collinear points | points within 1 mm are merged and collinear vertices are dropped before counting B | slide 7 tolerance | `test_checker::cleanup` |
| J | J = L + 5X + O + 0.5B (weights are settings, defaulting to the organisers' starting values) | slide 6; C17 | `test_checker::j_formula` |
| Reported values | `ring_length_m`, `length_m` and `reported_metrics` are filled by the mirror (consistent), but the organisers' checker ignores them and so does our ranking | slide 6; C42 | `test_checker::ignores_reported` |

## 7. Ranking
| Edge case | Decision | Source | Enforced by |
|---|---|---|---|
| Order | valid designs only (C39). Fewest hubs H, then lowest J, then the **smaller assigned-area gap**, then our zone signature (internal determinism only) | slides 3, 6; C16, C47 | `metrics.rank_key` |
| "Assigned-area gap" | **max − min of the hubs' assigned eligible area**; a single hub has gap 0 (Adi, 2026-10-15) | slides 3, 6; C47 | `metrics.area_gap` · `test_metrics::area_gap` |
| Equal J | compared after rounding to 1e-6 m, so float noise never decides | convention | `metrics.rank_key` |

## 8. Output and submission
| Edge case | Decision | Source | Enforced by · test |
|---|---|---|---|
| Submission file | exactly the slide-8 keys (`extra="forbid"`). Diagnostics, violations, bounds, settings and provenance go in a **sidecar** `<plan_id>.sidecar.json` (Adi, 2026-10-15) | slide 8; C24; ADR-010 | `contracts.Submission` · `test_contracts::slide8_example` |
| `units` | always `"m"` | slide 8 | `contracts.py` |
| `location` vs `polyline` points | `location` is an object `{"x": …, "y": …}`; polyline points are arrays `[x, y]` | slide 8; C48 | `contracts.Submission` · `test_contracts::slide8_example` |
| `assigned_rooms` order | the hub's `room_id` first, then the other rooms by id (slide 8: R05, R01, R04) | slide 8; C48 | `solve.write_submission` |
| `assigned_nodes` vs `closed_sequence` | `assigned_nodes` sorted by id (N1, N2, N3); `closed_sequence` is the ring order, starting and ending with the hub id (H1, N1, N3, N2, H1) | slide 8; C48 | `solve.write_submission` · checker rule 4 |
| `route_segments` | one per consecutive pair of `closed_sequence`, in that order (count = len − 1); `from`/`to` are those ids; the polyline starts at the `from` point, ends at the `to` point, and the next segment starts where this one ended (within 1 mm) | slide 8; C48 | checker · `test_checker::chain` |
| Polyline vertices | corner points only (collinear points removed); coordinates in metres, 4 decimals | slide 8; C41 | `contracts.py` |
| `length_m`, `ring_length_m`, `total_length_m` | computed by the mirror from the polylines (slide 8's own numbers check out: 4.8 = 2.4 + 2.4; 7.1 = 1.0 + 1.0 + 5.1; 23.8 + 25.2 = 49.0); ignored by the organisers' checker, but always consistent | slides 6, 8; C42, C48 | mirror |
| Id scope | hub, node and room ids are plan-global (H1, H2…; N1…N6; R01…), never per-hub | slide 8; C48 | `io.py`, `solve.py` |
| `reported_metrics` | `hub_count` = number of hubs; `total_length_m` = the sum of `ring_length_m` | slide 8 | mirror |
| A design that fails the checker mirror | never written. Fall back to the walking-skeleton design; if that fails too, write no submission and a sidecar `status: infeasible` with the reasons | slide 6; C43 | `solve.submit` · `test_solve::gate` |
| Overlay | zones, hub and node IDs, visiting order, full rings with the return leg; **violations in red** (from the mirror); per-hub summary | slide 3; C23 | `viz.py` |
| Final-presentation visual | a per-plan PNG/SVG export with the overlay + summary + J breakdown | slide 8; C45 | `viz.export_presentation` |
