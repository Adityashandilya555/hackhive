# AGENTS.md: hackhive

Conventions for every coding agent (Claude Code, Codex, …) working in this repo.

## What we're building (PS4)
The organisers' slides (all 8) win every conflict with the Honeywell text. The full map is `docs/ARCHITECTURE.md`; every edge case is decided in `docs/decision_conventions.md`.

- **Input:** one floor plan per JSON file (ResPlan-derived): rooms, doors, walls, windows, plus the organisers' **fixed node points**.
  - Geometry is in drawing units: convert once per plan with metres = units ÷ √(inner.area / area).
  - Repair invalid polygons with `make_valid`; never use `net_area`.
  - The organisers' JSON goes through the #38 adapter: detect format, frame check, fail loudly with `input_error`, never guess.
- **Nodes:** one per eligible room (living, bedroom, kitchen, storage), and each polygon is its own room.
  - Organiser nodes are used as given and never moved or merged.
  - For ResPlan dev plans we generate a point at max clearance (≥ 0.5 m from walls when possible), and fold living slivers that can't fit one.
  - Excluded rooms (bathroom, balcony, stair, garden, pool, parking, land) get no node and no hub.
- **Hubs:** we choose how many and where. A hub sits inside an eligible room **assigned to it**; a hub and a node may share a room (slide 3 rule 6). **≥ 0.5 m from walls** is the rule (`error` if broken); we prefer ≥ 0.5 m from the whole room edge (`strict` if only that fails). We still keep it off the node points.
- **Zones:** eligible rooms grouped into zones, each connected **directly** through doors / open passages. Windows, wall-only adjacency and the entrance don't count, and excluded rooms never link two zone rooms (slide 3 rule 3).
  - Each hub serves **≤ 100 m²** of eligible-room area and **≤ 5 nodes**.
  - ≤ 20 nodes is an optional what-if setting, never the default and never submitted.
- **Rings:** one closed, non-branching ring per hub: H → N1 → … → Nk → H.
- **Routes:** axis-aligned only.
  - They change rooms only through doors and open passages, never cross walls or windows, and never leave `inner`.
  - Each leg joins the exact node or hub point to the grid with an axis-aligned L-stub; incoming and outgoing stubs at a node use different corners.
  - By default a ring stays in its own hub's rooms: never through an excluded room (bathroom, stair) or another hub's room (D7 in `docs/clarifying_questions.md`). This is always possible, because a zone is connected by its own doors. Allowing it is a labelled WHAT-IF, never the default. Overlaps and crossings (between rings or inside one ring) are cost, not violations.
  - In a **single-door room** the cable goes in and back out through the same door, either retracing the same line (that length counts as overlap) or as two parallel runs on different lines (no overlap, more bends). The router picks the lower J.
  - Windows drawn on interior walls are obstacles; a UI checkbox can show them as doors (WHAT-IF, never submitted).
- **Ranking (slides 3, 6, 7):** fewest hubs H first, then the lowest **J = L + 5X + O + 0.5B** (metres; weights are the organisers' starting values, kept as settings), then the smaller assigned-area gap (max − min). The organisers' checker recomputes every metric from our polylines at 1 mm tolerance; our `checker.py` mirrors it.
- **Output (slide 8):** exactly the organisers' submission JSON (`plan_id`, `units:"m"`, `hubs[]`, `reported_metrics`; metres, origin lower-left, y up), plus our `<plan_id>.sidecar.json` for everything else.
  - **An invalid or missing plan scores zero**, so a submission is written when it has no `error` violation; strict-only designs are written and flagged `submitted_strict_only`; otherwise only the sidecar is written.
  - Also: an overlay with zones, hub and node ids, numbered ring order, full rings with the return leg, and violations in red; and a final-presentation image per plan.
- **Frontend:** a Streamlit demo with a locked "Problem statement" preset, labelled what-ifs, an arrangements gallery and a batch leaderboard.

Pipeline stages: parsing → room graph → zoning → hub + ring order → grid routing → overlap / crossing handling → checker mirror + submit gate → visualization. Use one module per stage under `src/hackhive/` (map in `docs/ARCHITECTURE.md`).

## Stack and versions
Python **≥3.11**, managed with **uv**. `uv.lock` pins exact versions; commit it.

| Layer | Tool |
|---|---|
| Data input | `json` + **pydantic 2** (typed rooms/doors/walls/windows) |
| Geometry | **shapely ≥ 2.1** (polygons, areas, `maximum_inscribed_circle`, containment checks) |
| Grid | **numpy 2**, 10 cm room-label raster (add **numba** only if routing is measurably slow) |
| Graphs | **networkx 3** (room–door graph, contiguity, components) |
| Zoning | exhaustive enumeration of contiguous capped zonings (no CP-SAT; ADR-001) |
| Ring order | exact: `itertools.permutations` for ≤ 5 nodes, Held-Karp for ≤ 10 (the ≤ 20 what-if) |
| Routing | custom bend-aware Dijkstra/A* with `heapq` on (cell, heading); occupancy penalty + rip-up passes |
| Validation | **pytest 8** + **jsonschema 4**; `checker.py` mirrors the organisers' checker (slide 7's examples are unit tests) |
| Visualization | **matplotlib 3** + **streamlit** demo (no plotly) |
| Batch | **tqdm** + **pandas 2** over 50–100 ResPlan plans |
| Lint/format | **ruff** |

Checks before any PR: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`.

## Issues are specs
Every issue uses `.github/ISSUE_TEMPLATE/spec.yml`: Context · Spec · How (with `HUMAN:` steps) · References (verified `path:Lstart-Lend @ sha`) · Acceptance criteria (Given/When/Then) · Test plan · Out of scope · Depends on · Environment variables. Labels come from `.github/labels.yml` (sync: `uv run scripts/sync_labels.py`). Size `L` means ~3 days; split anything bigger.

## Workflow
- Branch `<issue#>-<slug>` from an up-to-date `main` (`git switch main && git pull --ff-only`).
- One issue per PR. Title `#<n> <issue title>`. Body: `Closes #<n>`, summary, test evidence (commands + output), env vars added (or "none"). Tick only the criteria you verified.
- Conventional commits: `feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`.
- **Tests first**, written from the acceptance criteria. Show them failing, then make them pass.
- Never push to `main`, never force-push, never merge. Pass `--repo Adityashandilya555/hackhive` to `gh`.

## Secrets and env vars
Secrets live only in platform env vars, never in git, issues or chat. If the code ever reads an env var, `.env.example` lists it with a comment naming the platform that holds it.

## Reference repos
`docs/REFERENCES.md` maps each reference repo to a pipeline stage, with ideas to adopt and not adopt.
- **Design inspiration only.** Never copy or vendor their code; re-implement ideas in our modules. The **only exception is ResPlan's loader** (`resplan_utils.py`, MIT): keep its license notice in the copied file.
- **Never copy GPL code** (Freerouting).
- Check every adopted idea against our constraints: axis-aligned, doors/open passages only, ≤5 nodes and ≤100 m² per hub, closed rings, single-door double run.
- The clones live in `~/refs/ps4/`, outside this repo. Never import from them.
