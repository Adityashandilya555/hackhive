---
tags: [assumptions, sprint-1]
status: living
repo: Adityashandilya555/hackhive
---

# Our assumptions (beyond the organisers' rules), after Sprint 1

The organisers' rules are slides 1-8 (`~/Desktop/HACKHIVE-inputs/`, summarised in `AGENTS.md`). This file lists **everything else**: what we decided ourselves, plus the parts of the slides we had to interpret. It describes the code **as built at the end of Sprint 1** (`main` @ `b48523b`, after #61, #67, #69, #70, #72 and #73).

**Status key**
- **ours**: our decision; the slides don't say.
- **⚠ Qx**: our default until the organisers answer question Qx in [`clarifying_questions.md`](clarifying_questions.md).
- **Dx**: decided by us on 2026-10-16 (decision Dx there), usually with the other reading as a labelled **WHAT-IF** toggle in the app.
- **clarified**: answered; no longer an open point.
- **not built yet**: decided, but the code doesn't do it yet (the issue that will).

`docs/decision_conventions.md` stays the detailed source for every edge case; this file is the short list to review.

## 0. Sources and scope

| # | Assumption | Status | Where |
|---|---|---|---|
| 0.1 | When the slides and the Honeywell brief disagree, **the slides win**: axis-aligned routes, single-door retrace allowed, fewest hubs first, given node points, one JSON per plan. The brief only fills gaps. | ours (Adi, 2026-10-15) | `AGENTS.md` |
| 0.2 | **Node cap = 5 per hub.** The brief's "≤ 20 devices" case is **not needed**: no ≤ 20 mode, no 5-vs-20 comparison. | clarified (D1) | `Constraints.max_nodes = 5`; #28, #29 close (T1) |
| 0.3 | The J weights 1 / 5 / 1 / 0.5 are the slides' starting values. **Default = the organisers' weights**, to be read from settings, not code. Sidebar sliders change them as a WHAT-IF. The submitted design is **always picked with the official weights**. Today they're one constant (`contracts.ORGANISER_WEIGHTS`) with no CLI or UI control. | D2, not built yet (#30, #48) | `contracts.py` |

## 1. Input and units

| # | Assumption | Status | Where |
|---|---|---|---|
| 1.1 | The organisers' input format isn't published. `io.read_json` accepts ResPlan-shaped JSON or a guessed organiser shape (a `rooms` list, a `nodes` list, `plan_id`); every key it reads is in `io.ORGANISER_KEYS`. Anything else fails loudly (`input_error`), never by guessing. | ⚠ Q1 | `io.py` (#38) |
| 1.2 | **Frame check:** given nodes must fall in eligible rooms. We try identity, then `flip_y`, then `origin_shift`, and move the **geometry** into the nodes' frame. Given nodes never move, and the output is written in that frame, not transformed back, which is the organisers' own frame by construction. | D10 (Q1's sample confirms) | `io.py`, `Plan.frame` |
| 1.3 | ResPlan is already y-up, so dev plans use no flip and no shift: metres = units ÷ scale. | ours | `io.py` |
| 1.4 | A given node ≤ 10 cm outside its room is kept where it is and assigned to the nearest eligible room (or the room it names), with a warning. Further away, or naming an unknown room, it's `input_error`. A node's own `room_id`, when given, is authoritative. | ours | `io.py` |
| 1.5 | Ids: organiser ids are used as given. For ResPlan dev plans we generate `PLAN_<id>`, rooms `R01…` (living → bedroom → kitchen → storage), nodes `N1…`, hubs `H1…`, all plan-global. | ours | `io.py`, `solve.py` |
| 1.6 | Invalid polygons are repaired with `make_valid(method="structure")` and flattened to polygons. Slide 2 says "repaired" but not how. | ours | `io.py` |

## 2. Rooms and nodes (ResPlan dev plans only; organiser nodes are given)

| # | Assumption | Status | Where |
|---|---|---|---|
| 2.1 | Our generated node is the room's **max-clearance point in its free space** (the room minus walls and windows). When many points tie (a rectangle's centre line), it's the **middle** of that plateau, so it's the same on every platform. | ours | `io._mic` (#61) |
| 2.2 | A node with < 0.5 m clearance is kept, with a warning. A living-room sliver that can't fit a 0.5 m point is folded into the next living part and gets no node. Generator only, never applied to organiser input. | ours | `io.py` |
| 2.3 | An eligible room **> 100 m²** means no valid design exists: no submission, and the sidecar says `infeasible`. Slide 2 says the benchmark excludes such plans (2.1% of ResPlan). The area-cap slider shows them solved under a higher cap, as a WHAT-IF. | D4 | `solve.py` |

## 3. Zones

| # | Assumption | Status | Where |
|---|---|---|---|
| 3.1 | Two rooms linked **only through an excluded room** (bedroom → bathroom → bedroom) are **not** linked. The setting `zone_links_via_excluded` (bathroom and stair) exists, default off; #30 makes it a WHAT-IF toggle. | D5 | `graph.eligible_graph` |
| 3.2 | A window drawn on an interior wall (a ResPlan quirk) is an **obstacle**. The WHAT-IF checkbox "Treat windows on interior walls as doors" (`interior_windows_as_doors`) reads a window touching exactly two interior rooms (no balcony or outdoor layer) as a `window_door`; never submitted. | D6 (#31) | `graph.py`, #31 |
| 3.3 | A doorless eligible room is its own zone, with its own hub. | ours | `zoning.py` |
| 3.4 | A zone must contain a room that can host a hub (a point ≥ 0.51 m from walls). Otherwise that zoning is skipped, and a plan with no such zoning gets no submission. | ours | `zoning.py`, `solve.enumerate` |
| 3.5 | A zoning is skipped when one of its zones has no node, because there is no ring to build (also when a zone's ring can't be ordered or routed). | ours | `solve.enumerate` |
| 3.6 | Exact zoning handles **≤ 16 rooms** per connected group and **≤ 5,000 zonings**. Above that: no submission, and a "too large" sidecar. **No ResPlan plan gets there** (largest group: 9 rooms, out of 16,999 plans), so #49 goes to Backlog. | D8 | `zoning.py`, `solve.py` |
| 3.7 | An opening thinner than a 10 cm cell paints its two rooms' cells it crosses (never past its ends). A passage (almost) wholly outside `inner` (the 2 cm strip between two balconies: most of T5's 11%) is exterior, like a balcony door, unless both its rooms lie inside `inner`. A door's across side is the stretch that reaches further into its two rooms, so a door narrower than the wall is deep is never stretched along the wall (1 of 3,142 doors in a 500-plan sample); a door narrower than a cell fails the plan, never widened. A passage the grid still can't paint (a door drawn twice or on a window, an opening inside a nested room or toward a stair behind a wall) fails the plan with `grid failed` and says why; it is never dropped to fit: **1.4% of a 500-plan sample** (was 8.8%), and no plan that had a design lost it. | fixed (#78) | `grid.py` |

## 4. Hubs

| # | Assumption | Status | Where |
|---|---|---|---|
| 4.1 | Hub count = the minimum over all valid zonings, proven by exhaustive search. The lower bound max(⌈area/100⌉, ⌈nodes/5⌉) is only reported (not always reachable). `bounds.proof` is `lb_met` when the count equals it, `exhaustive` when it's the fewest hubs any zoning uses, and `none` when the gate moved past a count with fewer hubs (strict-only designs there, or zonings that couldn't be built) (#22). | ours | `zoning.py`, `solve._proof`, sidecar `bounds` |
| 4.2 | Hub spots are 10 cm grid-cell centres **≥ 0.51 m** (1 cm margin) from both the room's whole outline and the walls. Only if a zone has none, the spot only needs ≥ 0.51 m from walls, tagged `wall_clearance_only`. | ⚠ Q4 | `rings.hub_candidates` |
| 4.3 | The checker gives `error` below 0.5 m from walls (windows and the outside count as walls), and only `strict` below 0.5 m from a door gap or open-passage edge. | ⚠ Q4 | `checker.py` rule 6 |
| 4.4 | A hub is never on a node point: we keep it ≥ 0.10 m away. | ours | `rings.NODE_KEEP_OFF_M` |
| 4.5 | **First cut (Sprint 1), now only the last fallback:** the hub goes in the zone room with the most door links, at the spot closest to the zone's nodes in total. Among minimum-hub zonings, the one with the shortest Manhattan rings. Since #22, `solve()` ranks every arrangement by routed J and uses this walking skeleton only if every ranked one has an `error`. | ours, exact choice is #20, #22 | `rings.first_ring`, `solve._pick` |
| 4.6 | **Ranking (#22):** every zoning, each zone with its `best_ring` (#20), its rings routed alone and scored together; ranked by (H, J, area gap, zone signature), where J is the **worse of the strict and lenient counting readings** (D3). The rip-up passes (#80) run only on the arrangements `solve()` tries. Zones are built in parallel over up to 4 worker processes (13344: 34 s on one core, ~12 s on four). | slides 3, 6; D3 | `solve.enumerate`, `solve.rank_key`, `parallel.map_in_order` |

## 5. Rings and routes

| # | Assumption | Status | Where |
|---|---|---|---|
| 5.1 | Ring order: brute force over every order on **Manhattan** distance, for up to 8 nodes. Routed costs come with #20. | ours | `rings.order_ring` |
| 5.2 | Routes are found on a **10 cm grid** with a bend-aware shortest path, one leg at a time. | ours | `grid.py`, `route.py` |
| 5.3 | An off-grid node or hub joins the grid with an axis-aligned **L-stub**. A node's incoming and outgoing stubs use different corners, except in a single-door room, where the same corner (a retrace) is allowed when J is lower. | ours | `route.route_ring` (#15, #70) |
| 5.4 | A stub never takes a corner whose L crosses a wall; the check uses the checker's own free-space geometry. | ours | `route.py` (#70) |
| 5.5 | Corner choice is exhaustive up to 4⁵ combinations, then one greedy pass per stop. | ours | `route.MAX_CORNER_COMBOS` (#70) |
| 5.6 | A run pinned at both ends to lines < 1 mm apart gets a **sub-mm jog**, so every piece is exactly horizontal or vertical. | ours | `route._square_up` (#70) |
| 5.7 | **By default a ring stays in its own hub's rooms**, never through an excluded room (bathroom, stair) or another hub's room. That's always possible, because a zone is connected by its own doors. A WHAT-IF setting (`route_through_other_rooms`) allows it. `route_rings` blocks every other room on the raster, and every door/opening that doesn't join two of the ring's rooms (with `zone_links_via_excluded`, the bathroom/stair that joins the zone counts as one of them). A ring with no way inside its rooms fails as `routing failed`. J is unchanged on the fixtures except 13344 (+0.3: its ring left an opening's band inside its own living room). | D7, built (#79; app toggle #30) | `route.py` |
| 5.8 | The entrance (front door) is not a passage: cable never leaves `inner` through it. | ours | `grid.py` |
| 5.9 | Legs can share a corridor. Retraces outside single-door rooms are flagged rule-4 `strict` and still submitted. Rip-up passes (#80), crossings in the search (#82) and parallel runs (#81) remove most: on the 7 fixtures, 6 flags are left, five 0.10 m one-node out-and-backs (Q2(a)) and one 0.30 m at a two-door node in 13344. Before them: **122 of 124** sampled designs (332 flags). #21's target is **zero**. | ⚠ Q2, partly built (#80, #81, #82) | `route.py`, checker rule 4 |
| 5.9a | A node in a single-door room inside a longer ring (hub → N1 in a single-door room → N2): today the in and out legs share one line through the door (a legal retrace, costs O and maybe X). The router also routes two parallel runs ≥ 1 cell apart and keeps them when J is lower (2 m deep: J 34.5 retraced vs 28.0 parallel). A door into a dead-end bathroom is no second way out (#77). | ⚠ Q2, built (#81) | `decision_conventions.md` §5, `graph.single_door_rooms` (#77) |
| 5.10 | One-node ring (hub → node → hub): out-and-back on one line, or a thin rectangle loop when that's lower J (the node more than 1.2 m away). Until Q2(a) is answered, checker rule 4 flags the out-and-back `strict` when the node's room has two ways out. | ⚠ Q2, built (#81) | `decision_conventions.md` §5 |

## 6. How we read the metrics (our checker mirror)

| # | Assumption | Status | Where |
|---|---|---|---|
| 6.1 | X: a **T-touch** counts 1; a point where two cables **stop sharing a line** counts 0 (slide 7's overlap example counts that stretch as O alone; it counted 1 until 2026-10-17); a corner two cables **turn together** counts 0; another ring's cable through a **node or hub point** counts 1. Planned: **robust scoring**, where each design is scored under both readings and we submit the one whose worse J is lowest, with an app toggle that shows both. | D3, robust scoring not built yet | `checker.py` (#43) |
| 6.2 | O counts between **any** rings, not just within one: (k − 1) × length for k legs on the same line. The other reading goes into robust scoring (6.1). | D3 | `checker.py` |
| 6.3 | B: +1 at a node when the incoming and outgoing directions differ, and at the hub closing the ring. A reversal counts once. The other reading goes into robust scoring (6.1). | D3 | `checker.py` |
| 6.4 | Assigned-area gap = **largest − smallest** hub area (0 for one hub). | D9 | `metrics.area_gap` |
| 6.5 | The checklist line "k hubs > lower bound" shows ✗ even when k is the proven minimum. | ours, see T7 | `checker.checklist` |

## 7. Output and the submit gate

| # | Assumption | Status | Where |
|---|---|---|---|
| 7.1 | The submission has exactly slide 8's keys. Everything else (status, violations, bounds, warnings, frame) goes in `<plan_id>.sidecar.json`. | ours (Adi, 2026-10-15) | `contracts.py`, `solve.py` |
| 7.2 | **Gate:** no `error` → submit (`ok`); only `strict` flags → **still submit**, marked `submitted_strict_only`; any `error` → no submission (`infeasible`); unreadable input → `input_error`. Strict-only is the fallback, **not guaranteed valid** (Q2). `solve()` takes the **first clean arrangement in rank order, even at more hubs**, before the best strict-only one (Adi, 2026-10-16, #22), but looks **at most one hub count above** the first design with no `error` (#22 review), so a plan where everything is strict-only doesn't route every zoning; the sidecar's `arrangement` names the one used and why better-ranked ones weren't. | ours (T4) | `solve.solve`, `solve.submit` |
| 7.3 | Coordinates are written as **full floats**, not rounded. `decision_conventions.md` §1 says 4 decimals, but nothing rounds yet. | ours, see T6 | `solve.py` |
| 7.4 | `assigned_rooms` starts with the hub's room; `assigned_nodes` are sorted; `closed_sequence` is the ring order; reported lengths come from our mirror (the organisers ignore them). | ours | `solve.py` |
| 7.5 | What-ifs (edited weights, via-excluded links, interior windows as doors) are **never** written as a submission. | ours | #30, #31 |
