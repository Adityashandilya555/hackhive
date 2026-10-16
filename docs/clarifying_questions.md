---
tags: [questions, sprint-2]
status: open
repo: Adityashandilya555/hackhive
---

# Clarifying questions for Sprint 2

This file picks up from #46, which was closed **with no answers recorded**. Rule for what we ask: **only ask the organisers what a what-if toggle, a safe default or robust scoring can't cover.** Everything else is decided below, and the app shows the other reading as a labelled WHAT-IF. Our defaults are listed in [`assumptions.md`](assumptions.md).

**When an answer arrives:**
1. Move it to *Decided*, with the date and who answered.
2. Update the matching row in `assumptions.md` and `docs/decision_conventions.md`.
3. Edit the issue it affects.

## Decided (Adi, 2026-10-16)

| # | Point | Decision | Why we don't ask |
|---|---|---|---|
| D1 | Node cap: slides say ≤ 5, the Honeywell brief says ≤ 20 | **5 only.** No ≤ 20 mode, no 5-vs-20 view. | Settled. Close #28 and #29. |
| D2 | J weights (1 / 5 / 1 / 0.5) are "starting values, fixed after reference runs" (slide 6) | **Hard-coded for now:** `ORGANISER_WEIGHTS` holds the organisers' values (Adi, 2026-10-16), and every metric reads them through `c.cost_weights`. A settings file and WHAT-IF sliders come **later**; even then, the submitted design is always picked with the official weights. | When final weights are published, #48 changes the one constant. |
| D3 | Unclear counting rules: T-touch and split points as crossings, overlap between different rings, bends at nodes (old O3, O4, O9) | **Robust scoring:** score each design under both readings and submit the one whose worse J is lowest. The app gets a "counting rules" toggle that shows both. | Whichever way the organisers count, we aren't surprised. |
| D4 | An eligible room > 100 m² (old O12) | **Ignore.** No submission, and the sidecar says why. The area-cap slider (#30) shows such plans solved under a higher cap, as a WHAT-IF. | 2.1% of ResPlan, and slide 2 says the benchmark excludes them. |
| D5 | Zone link through a bathroom or stair (old O7) | **Default: no link.** A WHAT-IF toggle allows it (`zone_links_via_excluded`, #30). | The default is valid under either reading. |
| D6 | Windows on interior walls (old O16) | **Default: obstacle.** A WHAT-IF checkbox treats them as doors (#31). | The default is valid under either reading. |
| D7 | Cable through a bathroom or another hub's room (old O17) | **Default: forbidden.** A ring stays in its own hub's rooms, which is always possible because a zone is connected by its own doors. A WHAT-IF toggle allows it. Router: #21; toggle: #30. | Valid under either reading. Measured: **0 m** of 4,071 m of cable in 124 solved plans goes through one today, so forbidding it costs nothing, and it stays safe when #21 starts rerouting. |
| D8 | #49 greedy zoning for > 16 connected rooms | **Backlog.** The "too large" sidecar stays as the safety net. | Measured: **0 of 16,999** plans have > 16 connected eligible rooms (largest: 9). |
| D9 | Assigned-area gap (old O8) | Largest − smallest hub area. | Only breaks ties of equal J. |
| D10 | Output frame "origin lower-left" (old O13) | Write in the frame of the organisers' own node points (#38 moves the geometry into it). | Their checker matches our cables to their node points, so their frame is right by construction. O1's sample confirms it. |
| D11 | When final weights are published (old O10) | Adopt them via D2 (#48). | They'll announce them. |
| D12 | Plan 576 (6 hubs vs lower bound 2) | Keep as the showcase for "hub count above the bound, and proven minimal". | n/a |

## For the organisers (one message)

| # | Question | Why it can't be a toggle | Blocks |
|---|---|---|---|
| Q1 | **Input file:** one sample input JSON. How are rooms, room ids, doors, open passages and node points given? Are node coordinates in drawing units or metres? | If the hidden plans don't load, every plan scores 0. | #38, #32, #34 |
| Q2 | **Retracing** (was #46 Q5): slide 3 allows going back along the same line only to reach a single-door room. (a) Is it allowed in a room with 2+ doors, or in a one-node ring (hub → node → hub), with the shared length counted as overlap? Or is that a branch (invalid)? (b) Our reading: a door into a bathroom that opens nowhere else is no second way out, so a bedroom with an en-suite bathroom counts as single-door (Rohit, 2026-10-16, #77). Is that right? | It decides validity. Measured: **122 of 124** of our designs (98%) contain such a retrace (332 rule-4 flags). | #21, #23 |
| Q3 | **Final run:** do we get the hidden input files and submit one output per plan, or do you run our code? Is there a time limit per plan? What file names and folder layout do you expect, and what format for the presentation picture? How are rank points given per plan? | It decides packaging, search time (#19) and how hard to push J. | #19, #32, #34, #44 |
| Q4 | **Hub clearance (rule 6):** is the 0.5 m measured to walls only, or also to door openings and open passages? Do windows count as walls? | It decides validity for the few hubs that can't keep clear of door gaps. Our first choice already avoids them. | #20 |

## For us (team decisions in Sprint 2)

| # | Question | Decision |
|---|---|---|
| T1 | Close #28 (≤ 20 what-if) and #29 (5 vs 20 side by side)? | **Yes** (D1). Also update `rings.py`'s error that still points to "Held-Karp (#28)". |
| T2 | Gallery filters (#26)? | Hub count only. |
| T3 | Which WHAT-IFs does #30 offer? | Area cap, hub offset (J weights later, D2), counting rules (D3), zone link through bathroom/stair (D5), interior windows as doors (D6, #31), allow cable through bathrooms/other hubs' rooms (D7). |
| T4 | Keep submitting `strict`-only designs? | **Yes, as the fallback.** A missing plan scores 0 for sure, but strict-only isn't guaranteed valid (Q2). #21's target is **zero** retraces outside single-door rooms. |
| T5 | **New P0:** 11% of plans get no design because the grid misses thin doors and open passages. | **New issue, fixed first** (draft below). |
| T6 | Round output to 4 decimals? | Yes, in the writer: snap both ends of a segment to the same rounded coordinate, with a test that every segment stays exactly axis-aligned (#34). |
| T7 | "✗ k hubs > lower bound" shows red even when k is proven minimal. | Show ✓ with "proven minimum" (#24). |
| T8 | Reopen #46 and send Q1-Q4? | **Yes**, one sender. Log every answer here the same day. |

### T5: draft issue (to file after approval)

**[S2-00] Paint thin doors and open passages the 10 cm grid misses**
- **Problem:** `LabelGrid.from_plan` paints a door gap or open-passage strip only on the cells whose **centre** lies inside it (`src/hackhive/grid.py:L97-103 @ b48523b`). A gap or strip thinner than a cell can cover no centre, so it raises `"… covers no cell centre"`. `solve` turns that into `infeasible: grid failed`, and the plan scores 0.
- **Size:** 16 of 150 sampled plans (11%), plus 1 `"touches no room"`.
- **Fix:** when a passage covers no cell centre, paint the cells it **intersects** (never across a window or the front door, never outside `inner`). The routes must still pass checker rule 5.
- **Done when:**
  - all 17 sampled plans get a design;
  - the batch (#32) shows zero `grid failed`;
  - `test_grid` gets a thin-gap fixture.

## How the numbers were measured (2026-10-16, `main` @ `b48523b`)
- **Room size and connectivity:** a scan of all 16,999 readable ResPlan plans (`load_plan` + `build_graph`, one read error). It counted the largest group of eligible rooms connected by doors or open passages, per plan:

  | Largest group (rooms) | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
  |---|---|---|---|---|---|---|---|---|---|
  | Plans | 1 | 21 | 1,900 | 7,410 | 5,719 | 1,546 | 325 | 70 | 7 |

  A 2,000-plan sample (seed 7) found an eligible room > 100 m² in 2.1%.
- **Solve outcomes:** `solve()` on 150 random plans (seed 11) gave:
  - 2 `ok`;
  - 122 `submitted_strict_only`, every strict flag being a rule-4 retrace;
  - 26 with no design: 17 grid failures, 7 rooms > 100 m², 2 routing failures.
- **Cable through bathrooms, stairs or other hubs' rooms:** 0 m in those 124 designs (and in the 7 fixtures).
