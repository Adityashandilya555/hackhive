# AGENTS.md: hackhive

Conventions for every coding agent (Claude Code, Codex, …) working in this repo.

## What we're building (PS4)
The organisers' slides win every conflict with the Honeywell text. The full map is `docs/ARCHITECTURE.md`.

- **Input:** one floor plan per JSON file (ResPlan-derived): rooms, doors, walls, windows, plus the organisers' **fixed node points**.
  - Geometry is in drawing units: convert once per plan with metres = units ÷ √(inner.area / area).
  - Repair invalid polygons with `make_valid`; never use `net_area`.
- **Nodes:** one per eligible room (living, bedroom, kitchen, storage), and each polygon is its own room.
  - Organiser nodes are used as given and never moved or merged.
  - For ResPlan dev plans we generate a point at max clearance (≥ 0.5 m from walls when possible), and fold living slivers that can't fit one.
  - Excluded rooms (bathroom, balcony, stair, garden, pool, parking, land) get no node and no hub.
- **Hubs:** we choose how many and where. A hub sits inside an eligible room of its own zone, ≥ 0.5 m from nodes.
- **Zones:** eligible rooms grouped into zones, each connected **directly** through doors / open passages (excluded rooms never link two zone rooms).
  - Each hub serves **≤ 100 m²** of eligible-room area and **≤ 5 nodes**.
  - ≤ 20 nodes is an optional what-if setting, never the default.
- **Rings:** one closed, non-branching ring per hub: H → N1 → … → Nk → H.
- **Routes:** axis-aligned only.
  - They change rooms only through doors and open passages, never cross walls or windows, and never leave `inner`.
  - They may pass through any room, including other zones and excluded rooms. Overlaps and crossings between rings are cost, not violations.
  - In a **single-door room** the cable goes in and back out through the same door as two parallel runs; the shared length counts as overlap.
  - Windows drawn on interior walls are obstacles by default; a UI checkbox switches to treating them as doors (WHAT-IF).
- **Ranking:** fewest hubs first, then lowest cost (cable length, bends, crossings, overlap; replaceable weights).
- **Output:** our own versioned JSON (+ a generated JSON Schema) and a visual overlay (coloured zones, hubs, numbered ring order with the return to the hub, routes, per-hub summary, reviewer checklist). The organisers' format and cost formula aren't published, so both are replaceable settings.
- **Frontend:** a Streamlit demo with a locked "Problem statement" preset, labelled what-ifs, an arrangements gallery and a batch leaderboard.

Pipeline stages: parsing → room graph → zoning → hub + ring order → grid routing → overlap / crossing handling → validation → visualization. Use one module per stage under `src/hackhive/` (map in `docs/ARCHITECTURE.md`).

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
| Validation | **pytest 8** + **jsonschema 4** |
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
