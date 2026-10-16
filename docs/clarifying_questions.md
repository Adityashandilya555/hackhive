---
tags: [questions, sprint-2]
status: open
repo: Adityashandilya555/hackhive
---

# Clarifying questions for Sprint 2

These are the questions whose answers change what Sprint 2 builds (#19-#35, #44, #48, #49). They pick up from #46, which was closed **with no answers recorded**. Each one names the Sprint 2 issue it blocks and the default we keep until it's answered. Our defaults are listed in [`assumptions.md`](assumptions.md).

**When an answer arrives:**
1. Move the question to *Answered* below, with the date and who answered.
2. Update the matching row in `assumptions.md` and in `docs/decision_conventions.md`.
3. Edit the issue it blocks.

## Answered

| # | Question | Answer | Effect |
|---|---|---|---|
| A1 | Node cap: the slides say ≤ 5 nodes per hub, the Honeywell brief says ≤ 20. Which do we build? | **≤ 5 only. The ≤ 20 case isn't needed** (2026-10-16). | `Constraints.max_nodes` stays 5. #28 and #29 are no longer needed (T1). Remove the node-cap options from #26 and #30 (T2, T3). |

## For the organisers (one message)

| # | Question | Blocks | Our default until answered |
|---|---|---|---|
| O1 | **Input file:** can you share one sample input JSON? How are rooms, room ids, doors, open passages and node points given? Are node coordinates in drawing units or metres? | #38 (open), #32, #34 | tolerant loader; anything unreadable is `input_error` |
| O2 | **Retrace in a room with 2+ doors:** is a cable going back along the same line there allowed (counted as overlap), or is it a branch (invalid)? | #21, #23 | allowed, flagged `strict`, still submitted |
| O3 | **Crossings:** which of these count as a crossing? (a) a T-touch: one cable ends on another's middle; (b) the point where two cables stop sharing a line; (c) a corner two cables turn together; (d) a cable passing through another ring's node or hub. | #21, checker | (a) 1, (b) 1, (c) 0, (d) 1 |
| O4 | **Overlap between rings:** does overlap count when two *different* hubs' cables share a line, or only within one ring? | #21 | any rings |
| O5 | **One-node ring:** is hub → node → hub on the same line valid, or does it count as a branch? | #20, #21 | valid, overlap charged |
| O6 | **Hub 0.5 m:** is it measured to walls only, or also to door openings and open passages? Do windows count as walls here? | #20 | walls only (windows count); door gaps only give `strict` |
| O7 | **Zones through an excluded room:** can two rooms of one zone connect through a bathroom (bedroom → bathroom → bedroom)? | #22, #30 | no |
| O8 | **Tie-break:** what is the "assigned-area gap between hubs": largest minus smallest area, or something else? | #22 | largest − smallest |
| O9 | **Bends at nodes:** is a bend counted when a cable arrives at a node in one direction and leaves in another? Is a reversal one bend? | #20, checker | yes; a reversal counts once |
| O10 | **Final J weights:** when and where will the final weights (now 1 / 5 / 1 / 0.5) be published? | #48 | 1 / 5 / 1 / 0.5 |
| O11 | **Limits:** is there a time limit per plan or per batch, and a maximum number of rooms per plan? | #19, #32, #49 | none known |
| O12 | **Big rooms:** can a hidden plan contain one eligible room over 100 m²? If so, what should we submit? | #23 | no submission, sidecar `infeasible` |
| O13 | **Coordinates:** "origin lower-left, y up": keep the input's coordinates ÷ scale, or shift the plan so its lower-left corner is (0, 0)? | #34, #38 | input coordinates ÷ scale, unshifted |
| O14 | **Scoring:** how are rank points given per plan: by position, or by distance from the best design? | #20, #21, #22 | n/a (decides how hard to push J vs. risk) |
| O15 | **Submission:** file names, folder layout and how we submit; the final-presentation picture's format (PNG, SVG or PDF) and whether it's one per plan. | #34, #44 | `<plan_id>.json` per plan; PNG per plan |
| O16 | **Windows on interior walls** (drawn in some ResPlan plans): always obstacles, or ever passable like a door? | #31 | always obstacles |
| O17 | **Passing through other rooms:** may cable pass through another hub's rooms, or through an excluded room such as a bathroom? | #21 | yes, costs only crossings and overlap |

If only a few can be asked: **O1, O2, O3, O4**. They decide whether our designs are valid and how Sprint 2's router (#21) should behave.

## For us (team decisions in Sprint 2)

| # | Question | Issue | Proposal |
|---|---|---|---|
| T1 | With the cap fixed at 5 (A1), do we close #28 (≤ 20 what-if) and #29 (5 vs 20 side by side)? | #28, #29 | close both as not needed |
| T2 | Does the arrangements gallery keep only the hub-count filter? | #26 | yes, drop the node-cap filter |
| T3 | Which constraints stay editable as what-ifs, now that the node cap is fixed? | #30 | area cap, J weights, hub offset |
| T4 | Do we keep submitting `strict`-only designs, or prefer a costlier design with no flags? (Depends on O2-O4.) | #21, #23 | keep submitting until the answers come |
| T5 | Plans with more than 16 rooms in one connected group (or over 5,000 zonings) get **no submission today**. A missing plan scores 0, so is #49's greedy fallback a must for Sprint 2? | #49 | yes, priority |
| T6 | Do we round output coordinates to 4 decimals, as `decision_conventions.md` §1 says (nothing rounds yet), or change the convention? Any rounding must keep every segment exactly axis-aligned. | #34 | round in the writer, then re-check axis alignment |
| T7 | The reviewer checklist shows "✗ k hubs > lower bound" in red even when k is the proven minimum. Reword it (for example "k hubs, proven minimum; LB 2") with the optimality badge? | #24 | yes |
| T8 | #46 was closed with no answers. Who sends O1-O17, and when? Do we reopen #46 to track the replies? | #46 | reopen and send this list as one message |
