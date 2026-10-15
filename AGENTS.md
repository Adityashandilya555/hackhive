# AGENTS.md: hackhive

Conventions for every coding agent (Claude Code, Codex, …) working in this repo.

## What we're building (PS4)
- **Input:** one floor plan per JSON file (ResPlan-derived): rooms, doors, walls, windows.
- **Nodes:** one per eligible room (living, bedroom, kitchen, storage), at a point guaranteed to be inside the room.
- **Zones:** rooms grouped into contiguous hub zones. Rooms connect only through doors and open passages. Each hub serves **≤100 m²** of eligible-room area and **≤5 nodes**.
- **Rings:** one closed, non-branching ring per hub: H → N1 → … → Nk → H.
- **Routes:** axis-aligned only. They pass between rooms only through doors and open passages, never cross walls or windows, and never leave the building. In a **single-door room** the cable goes in and back out through the same door as two parallel runs.
- **Ranking:** fewest hubs first, then lowest cost (cable length, bends, crossings, overlaps).
- **Output:** a visual overlay (colored zones, hubs, numbered ring order, routes) plus JSON for validation.

Pipeline stages: parsing → zoning → ring order → grid A* routing → congestion/overlap removal → validation → visualization. Use one module per stage under `src/hackhive/`.

## Stack and versions
Python **≥3.11**, managed with **uv**. `uv.lock` pins exact versions; commit it.

| Layer | Tool |
|---|---|
| Data input | `json` + **pydantic 2** (typed rooms/doors/walls/windows) |
| Geometry | **shapely 2** (polygons, areas, `point_on_surface`, containment checks) |
| Grid | **numpy 2**, ~10 cm occupancy grid (add **numba** only if A* is measurably slow) |
| Graphs | **networkx 3** (room–door graph, contiguity, components) |
| Zoning | `itertools` exact search; **OR-Tools CP-SAT 9** as a fallback only |
| Ring order | `itertools.permutations` (exact TSP, ≤5 nodes) |
| Routing | custom A* with `heapq`: heading-aware bend cost, congestion penalties |
| Validation | **pytest 8** + **jsonschema 4** |
| Visualization | **matplotlib 3**; optional **streamlit** demo |
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
