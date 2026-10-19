# Notes from Adi: node caps, frontend and novelty (chat, 2026-10-15)

## Verbatim
> what are the contradictions in frontend when we are building i would like to have all possible configurations on the plan where constraints can be defined + some novelty which can be adopted as far as this 5 vs 20 we can have both with filters on the frontend to see what everyone likes and lets say we can do with less then make the <20 optional for later if it is hard and focus on less than 20 what i am looking at is that all possible solutions beautifully placed on the frontend. novelty could be to show all arrangements etc with filter of 2, 3, or 4 something like that. make workflow research such that it sticks to the problem statement and still is able to adapt something novel which can impress judges and does not take much effort to build on the user interface.

## Decisions (restated; ask Adi if any reading is wrong)
1. **Node cap: support both 5 and 20**, switchable with a filter in the frontend.
   - **≤5 is the primary target**: it must work end to end.
   - **≤20 is optional**: build it only if it's cheap, otherwise put it in Backlog.
2. **Constraints are configurable in the frontend**: caps and the other problem constraints, plus "all possible configurations on the plan".
3. **Show all feasible solutions, beautifully.** The novelty: an "all arrangements" view with filters, e.g. by hub count (2 / 3 / 4).
4. **Novelty must stick to the problem statement**, impress judges, and be **low-effort UI work** on top of what the solver already produces.

## Round 2 (chat, 2026-10-15)
> ok add these extras … also i want sprint to be decided in such a manner that we get the working stuff first which we can see and verify in that order and proper

5. **The novelty set is chosen:** all seven of these, in priority order.
   1. Arrangements gallery with a hub-count filter (2 / 3 / 4…) and the node-cap filter
   2. Optimality badge ("k hubs = lower bound")
   3. Live reviewer checklist (validator-driven)
   4. Ring step-through (draw the cable segment by segment)
   5. Pareto view (hub count vs cable length / bends)
   6. Side-by-side: node cap 5 vs 20 on the same plan
   7. Batch leaderboard (hubs vs lower bound, length, bends over N plans)

   If one turns out bigger than M or outside the problem statement, move it to Backlog and say why.
6. **Sprints deliver visible, verifiable results first, in order:**
   - each step must be visible in the app (or as a validator check) and checked before the next step starts;
   - working end-to-end comes before optimal;
   - optimal comes before extras.

## Round 3: answers to the definitive questions (chat, 2026-10-15)
7. **Source precedence: the slides win.** When the organisers' deck (`PS4-slides-transcript.md`) and the Honeywell text (`PS4-problem-statement.md`) disagree, follow the slides. That means:
   - horizontal/vertical routes only;
   - single-door retrace allowed, with the shared length counted as overlap;
   - **fewest hubs first, then lowest cost**;
   - node points given by the organisers;
   - one JSON file per plan.

   The Honeywell text only fills gaps. The node cap is as decided in point 1: ≤5 primary, ≤20 optional.
8. **Timeline: short sprints, about 2–3 days each.** Sprint 1 runs from day 1 to day 3 after the kickoff, Sprint 2 from day 4 to day 6.
9. **Slides 3, 6, 7 and 8 are not available.** Design our own output JSON (+ JSON Schema) and cost function as **settings that can be replaced** with the organisers' versions if they turn up. Stay close to the slides' wording: per-hub area, nodes, length and bends; ranking by fewest hubs, then cost made of length, bends, crossings and overlap.
10. **Hub placement:** a hub sits **inside one of the eligible rooms of its own zone** (living, bedroom, kitchen, storage). It's **never** placed in an excluded room (bathroom, balcony, stair, garden, pool, parking, land; slide 2: "No node and no hub") or outside the inner boundary.
