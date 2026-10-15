# Organiser spec coverage

Every requirement on the organisers' 8 slides ("UR Hackathon 2026H2", PS4) mapped to:
- the GitHub issues whose **acceptance criteria** cover it;
- the test or check that proves it.

The C-ids refer to the constraint table in `docs/ARCHITECTURE.md` §1. Edge-case decisions are in `docs/decision_conventions.md`.

**How this was checked (2026-10-15).** A script read all 42 issue bodies from GitHub. A spec item counts as covered only if:
- an **open** issue's acceptance criterion tags one of its C-ids; **and**
- that issue's body states the requirement's key phrase.

**Round 2 (2026-10-16).** The slides were re-read against the issue bodies, and each row now carries a **Status**: `covered`, `partial (<why>, <issue>)` or `open question (#46 Qn)`.

Result: **47 covered, 2 partial, 1 open question (2026-10-16).** The first audit (2026-10-15) fixed these issues:
- **#4:** added the slide-4 rule "never use `net_area`; convert once per plan".
- **#11:** rule 4 now checks that segments start and end at the **given** node points (nodes can't move, slide 2), and that a ring never ends at another hub (slides 1-2).
- **#2, #12, #14, #20, #21, #22, #31:** stale wording aligned with slides 3/6/7 (J, area gap, checker names).

Round 2 (2026-10-16) updated #2, #6, #9-#17, #20-#23, #30-#32, #38 and #44 (input adapter, endpoint stubs, gate severity, hub clearance), with follow-ups to #18, #34, #35, #43 and #47, and filed #46 (organiser questions), #47 (these docs), #48 (final J weights) and #49 (greedy zoning over 16 rooms).


## Slide 1: scope and expected results
| # | Requirement (organisers' wording, condensed) | C-ids | Issues | Proven by | Status |
|---|---|---|---|---|---|
| S1.1 | Decide how many hubs and where each goes | C18, C16 | #2, #3, #7, #11, #13, #14, #16, #20, #22, #24, #26, #27, #32, #33 | `test_zoning` min hubs per fixture; `test_rings` hub candidates | covered |
| S1.2 | Which rooms and nodes each hub serves | C9, C1 | #6, #10, #11, #13 | checker rule 1 (`test_checker_rules`) | covered |
| S1.3 | Order in which each hub's nodes are connected | C10 | #11, #14, #15, #20, #25, #28 | `test_rings` ring = [H, perm, H] | covered |
| S1.4 | Physical cable route of every closed ring | C12, C13 | #8, #9, #10, #11, #12, #15, #19, #21 | `test_route_rings`; checker rule 5 | covered |
| S1.5 | One fixed node in every eligible living room, bedroom, kitchen, storage | C1 | #6, #10, #11 | `test_io::nodes`; checker rule 1 | covered |
| S1.6 | Capacity: ≤100 m² and 5 nodes per hub | C5, C6 | #2, #3, #11, #13, #28, #29 | checker rule 2; `test_zoning` | covered |
| S1.7 | Zones: hub's rooms one connected zone; every node exactly one hub | C8, C34, C9 | #9, #11, #13 | checker rules 1, 3; `test_graph` | covered (note: open question #46 Q2) |
| S1.8 | Ring H→N1→…→Nn→same H, closed, non-branching, each node once | C10 | #11, #14, #15, #20, #25, #28 | checker rule 4 | covered |
| S1.9 | Routes horizontal/vertical, through doors/passages, inside inner, never walls/windows/external | C12, C13, C14 | #8, #9, #10, #11, #12, #15, #19, #21, #31 | checker rule 5; `test_route_search` | covered (stub rule: #10, #15) |
| S1.10 | Invalid: open-ended, branches, skipped/repeated nodes, ring ending at another hub | C11, C10 | #11, #14, #15, #20, #25, #28 | checker rule 4 broken fixtures (open ring, repeated node, ring ending at another hub) | covered |
| S1.11 | Prototype processes an unseen ResPlan plan with no manual adjustment | C22 | #5, #7, #16, #18, #19, #32, #33, #34, #35 | `test_solve` on 7 fixtures; batch on unseen test-split plans | covered |
| S1.12 | Visual overlay: assignment, nodes, hubs, order, full routes, returns to hub | C23 | #5, #7, #12, #17, #25, #44 | `test_viz_solution` | covered |
| S1.13 | JSON output supports validation and scoring | C24 | #2, #3, #4, #5, #16, #34 | `test_contracts::slide8_example` | covered |
| S1.14 | Ranking: fewest hubs first, then lowest cost J | C16 | #13, #14, #16, #20, #22, #24, #26, #27, #32, #33 | `test_solve -k rank` | covered |
| S1.15 | Input: one JSON file per plan from the organisers | C21 | #7, #38 | `test_io` (one file per plan); CLI | partial (real organiser sample not verified yet; #38 last criterion, #46 Q1) |

## Slide 2: task and inputs
| # | Requirement (organisers' wording, condensed) | C-ids | Issues | Proven by | Status |
|---|---|---|---|---|---|
| S2.1 | Geometry in drawing units → metres (slide 4); invalid polygons repaired | C19, C20 | #4, #6 | `test_io::scale`, `test_io::bowtie` | covered |
| S2.2 | Eligible rooms; each polygon is one room with one node | C2 | #4, #6 | `test_io` (576: 2 living rooms) | covered |
| S2.3 | Node points are fixed inputs (0.5 m from walls); cannot move them | C3 | #3, #6, #11 | `test_io::nodes`; checker rule 4 (segments end at the given node points) | covered |
| S2.4 | Excluded rooms: no node and no hub | C4 | #4, #5, #6 | `test_io`; checker rule 6 | covered |
| S2.5 | Benchmark plans: no room over 100 m² | C27 | #4, #16, #23, #32 | `test_solve::infeasible` | covered |
| S2.6 | Both limits at once; hubs ≥ max(ceil(area/100), ceil(nodes/5)) | C7 | #2, #6, #12, #22, #24 | `test_contracts` lower_bound | covered |
| S2.7 | Ring rules + retracing a door to a single-door room allowed, shared length = overlap | C15 | #9, #11, #17, #21, #43 | checker rule 4 + `test_checker_metrics` (O) | covered |

## Slide 3: rules, ranking and deliverables
| # | Requirement (organisers' wording, condensed) | C-ids | Issues | Proven by | Status |
|---|---|---|---|---|---|
| S3.1 | Hard rule 1 Coverage: none omitted, split or duplicated | C1, C9, C39 | #3, #6, #10, #11, #12, #13, #16, #18, #28, #31 | checker rule 1 | covered |
| S3.2 | Hard rule 2 Capacity | C5, C6 | #2, #3, #11, #13, #28, #29 | checker rule 2 | covered |
| S3.3 | Hard rule 3 Zone: doors/passages only; windows, wall-only adjacency, entrance don't count | C8, C34 | #9, #11, #13 | checker rule 3; `test_graph -k front_door` | covered (note: open question #46 Q2) |
| S3.4 | Hard rule 4 Ring (+ single-door retrace) | C10, C15 | #9, #11, #14, #15, #17, #20, #21, #25, #28, #43 | checker rule 4 | covered |
| S3.5 | Hard rule 5 Free space: inside inner, outside walls/windows, axis-aligned, room changes only through gaps | C12, C13, C14 | #8, #9, #10, #11, #12, #15, #19, #21, #31 | checker rule 5 | covered (stub rule: #10, #15) |
| S3.6 | Hard rule 6 Hub point: assigned eligible room, ≥0.5 m from walls; may share a node's room | C18 | #2, #3, #7, #11, #13, #14, #20 | checker rule 6; `test_zoning -k hub_capable` | covered (note: open question #46 Q3) |
| S3.7 | A design must pass every rule before it is ranked | C39 | #3, #11, #12, #16, #18, #28, #31 | `test_solve -k gate` | covered |
| S3.8 | Ranking terms H, L(1/m), X(5), O(1/m), B(0.5) + tie-break smaller assigned-area gap | C16, C17, C47 | #2, #3, #7, #8, #13, #14, #16, #19, #20, #21, #22, #24, #26, #27, #29, #30, #32, #33, #36, #43 | `test_checker_metrics -k j_formula/area_gap`; `test_solve -k rank` | covered |
| S3.9 | Deliverable overlay: zones, hub and node IDs, visiting order, full rings with return leg, violations in red | C23 | #5, #7, #12, #17, #25, #44 | `test_viz_solution` (ids, red violations) | covered |
| S3.10 | Deliverable JSON with the same hubs, sequences, polylines and lengths | C24, C48 | #2, #3, #4, #5, #6, #15, #16, #34 | `test_contracts::slide8_example`; `test_solve` | covered |

## Slide 4: scale
| # | Requirement (organisers' wording, condensed) | C-ids | Issues | Proven by | Status |
|---|---|---|---|---|---|
| S4.1 | scale = sqrt(inner.area/area); convert once per plan before any limit or clearance check; never net_area | C19 | #4, #6 | `test_io::scale` (net_area never read) | covered |

## Slide 5: expected solution
| # | Requirement (organisers' wording, condensed) | C-ids | Issues | Proven by | Status |
|---|---|---|---|---|---|
| S5.1 | Overlay legend: hub colour per zone, node, numbered cable, last number = return, excluded room, door gaps | C23 | #5, #7, #12, #17, #25, #44 | `test_viz_base`, `test_viz_solution` | covered |
| S5.2 | Single-door room: in and back out through the same door (two parallel runs) | C15 | #9, #11, #17, #21, #43 | `test_route -k single_door_choice`; `test_viz_solution` (submitted polylines) | covered |
| S5.3 | Per-hub summary: rooms, m², nodes, length, bends | C25 | #17, #44 | `test_viz_solution` summary rows | covered |
| S5.4 | What a reviewer confirms (5-line checklist incl. hubs = lower bound) | C26 | #11, #12, #16, #18, #24, #26 | `checker.checklist`; app checklist | covered |

## Slide 6: evaluation
| # | Requirement (organisers' wording, condensed) | C-ids | Issues | Proven by | Status |
|---|---|---|---|---|---|
| S6.1 | J = L + 5X + O + 0.5B in metres | C17 | #2, #3, #7, #8, #19, #20, #21, #24, #26, #27, #29, #30, #33, #36, #43 | `test_checker_metrics -k j_formula` | covered |
| S6.2 | Metric definitions H, L, X, O, B (computed by the checker) | C40 | #3, #18, #20, #21, #43 | `test_checker_metrics` | covered (note: strict X, open question #46 Q4) |
| S6.3 | Tie-break smaller assigned-area gap; tolerance 1 mm; weights are starting values | C47, C41 | #2, #3, #7, #15, #22, #26, #43 | `test_checker_metrics -k tolerance/area_gap` | covered (notes: area gap #46 Q6; final weights #46 Q7 / #48) |
| S6.4 | Values reported in the JSON are ignored (checker recomputes from polylines) | C42 | #3, #23, #43 | `test_checker_metrics` (reported values unread); `test_solve -k reported` | covered |
| S6.5 | Invalid or missing plan scores zero | C43 | #3, #16, #18, #22, #23, #30, #31, #32, #33, #35 | `test_solve -k gate`; batch validity 100% | covered (gate severity: #11, #16, #22, #23) |

## Slide 7: metric examples
| # | Requirement (organisers' wording, condensed) | C-ids | Issues | Proven by | Status |
|---|---|---|---|---|---|
| S7.1 | L examples: 4+2+3+3 = 12 m; retrace 3+3 = 6 m | C40 | #3, #18, #20, #21, #43 | `-k slide7` (L = 12, L = 6) | covered |
| S7.2 | X examples: meet in the open X=1; meet at a node X=0 | C40 | #3, #18, #20, #21, #43 | `-k slide7` (X = 1, X = 0) | covered (note: strict X, open question #46 Q4) |
| S7.3 | O examples: 2 m same line O=2; parallel runs O=0 | C40 | #3, #18, #20, #21, #43 | `-k slide7` (O = 2, O = 0) | covered |
| S7.4 | B examples: two turns B=2; straight through B=0 | C40 | #3, #18, #20, #21, #43 | `-k slide7` (B = 2, B = 0) | covered |
| S7.5 | Tolerance 1 mm: same point / same line; each metric per plan summed over rings | C41 | #2, #3, #7, #15, #43 | `test_checker_metrics -k tolerance` | covered |

## Slide 8: data format
| # | Requirement (organisers' wording, condensed) | C-ids | Issues | Proven by | Status |
|---|---|---|---|---|---|
| S8.1 | One input file per plan (format not shown) | C21, C46 | #3, #6, #7, #38 | `test_io`; #38 adapter when the format is published | partial (real organiser sample not verified yet; #38 last criterion, #46 Q1) |
| S8.2 | One output file per plan in the slide-8 format (keys, metres, origin lower-left, y up) | C24, C48 | #2, #3, #4, #5, #6, #15, #16, #34 | `test_contracts`; `test_io::frame` | open question (#46 Q10: output origin) |
| S8.3 | A visualisation for the final presentation | C45 | #3, #35, #44 | `test_viz_presentation` | covered |

## Open questions
Sent to the organisers in #46; until an answer arrives, the safe default in `docs/decision_conventions.md` stays in force.
- **Q1** Input format: a sample input JSON; node coordinates in drawing units or metres?
- **Q2** May zone rooms link through an excluded room?
- **Q3** Hub 0.5 m: from wall geometry only, or from the whole room boundary?
- **Q4** Does a T-junction, or the end of a shared stretch, count as X?
- **Q5** Is a same-line retrace in a room with ≥ 2 doors allowed (overlap) or a branch (invalid)?
- **Q6** Is the assigned-area gap max − min, or something else?
- **Q7** When and where are the final J weights published?
- **Q8** Is there a time limit, or a maximum room count per plan?
- **Q9** Bends at nodes: last incoming vs first outgoing direction, reversal once?
- **Q10** Output frame: input frame ÷ scale, or translated to a (0, 0) lower-left?

## Where the organisers' rules live in code
| Organiser artefact | Our module | Issue(s) |
|---|---|---|
| Slide 3 hard rules 1-6 | `checker.check_rules` | #11 |
| Slide 6/7 metrics H, L, X, O, B, J (1 mm) | `checker.metrics` (the slide-7 examples are tests) | #43 |
| "Invalid or missing plan scores zero" | `solve.submit` gate + fallback | #16, #22, #23 |
| Slide 8 output format | `contracts.Submission` (keys only) + writer | #2, #16 |
| Slide 3 overlay deliverable + slide 5 legend / summary / checklist | `viz.draw_solution`, app Best tab | #5, #12, #17 |
| Slide 8 final-presentation visual | `viz.export_presentation` | #44 |
| Slide 4 scale | `io.read_layers` | #4 |
| Input adapter (slide 8 "one input file per plan", format not shown) | `io.read_json` / `detect_format` | #38 |
| Endpoint stubs (fixed node points joined axis-aligned) | `grid.snap` + `route.route_ring` | #10, #15 |
| Gate severity (`error` vs `strict`) | `checker.Violation.severity` + `solve.submit` | #11, #16, #22 |
| Final J weights | `contracts.ORGANISER_WEIGHTS` | #48 |
| Large plans (> 16 rooms) | greedy zoning | #49 |

The informational items (slide 6 "left for the teams"; "rank points add up over hidden plans") need no issue. The batch over unseen test-split plans (#32) is our proxy for the hidden plans.
