<!-- Compiled 2026-10-15 from the 7 WhatsApp screenshots in this folder (phone captures of the organisers' slide deck
     "UR Hackathon 2026H2 | Problem statement", 8 slides). Text is transcribed verbatim where legible; [illegible] / [?] mark
     unsure words. The images stay in this folder as the originals, so check them when a detail matters. -->

# UR Hackathon 2026H2: problem statement slides (transcript)

## Which file is which
| Slide | Title | Screenshot(s) |
|---|---|---|
| 1/8 | Project scope and expected results | `WhatsApp Image 2026-10-15 at 19.37.37 (2).jpeg` |
| 2/8 | Problem statement (1/2): Task and inputs | `WhatsApp Image 2026-10-15 at 19.37.39 (1).jpeg` |
| 3/8 | (probably "Problem statement (2/2)") | **not in this folder** |
| 4/8 | Scale: from drawing units to metres | `WhatsApp Image 2026-10-15 at 19.37.38 (1).jpeg` |
| 5/8 | Expected solution: illustrative schematic | `…19.37.37.jpeg`, `…19.37.37 (1).jpeg`, `…19.37.38.jpeg`, `…19.37.39.jpeg` (4 captures of the same slide) |
| 6/8, 7/8, 8/8 | n/a | **not in this folder** |

Footer on every slide: "Research exercise: results are not a building-services design." · "UR Hackathon 2026H2 | Problem statement | N/8"

---

## Slide 1/8: Project scope and expected results
*Joint optimization of hub placement and closed-ring cable routing on ResPlan floor plans*

**The system decides**
1. How many hubs, and where each one goes
2. Which rooms and nodes each hub serves
3. The order in which each hub's nodes are connected
4. The physical cable route of every closed ring

**Rules every design must meet**
- **Nodes:** one fixed node in every eligible living room, bedroom, kitchen and storage room.
- **Capacity:** each hub serves at most 100 m² of assigned eligible-room area and 5 nodes.
- **Zones:** a hub's rooms form one connected zone; every node belongs to exactly one hub.
- **Ring:** H → N1 → … → Nn → same H. Closed and non-branching, each node once.
- **Routes:** horizontal and vertical runs, between rooms only through doors and open passages, inside the inner boundary, never through walls, windows or external areas.
- **Invalid:** open-ended routes, branches, skipped or repeated nodes, a ring ending at another hub.

**Expected outcome**
- A working prototype that processes an unseen ResPlan plan with no manual adjustment.
- **Visual overlay:** room-to-hub assignment, nodes, hubs, connection order and full ring routes. It must show routes crossing only at doors or open passages, avoiding walls and windows, and returning to the starting hub.
- **JSON output:** supports validation and scoring.
- **Ranking:** fewest hubs first, then lowest cost [rest illegible].

**Major work elements**
- ResPlan geometry, scale conversion and eligible-room identification
- Capacity-constrained hub placement and room assignment
- Contiguous zone generation from door and open-passage links
- Closed-ring node sequencing
- Obstacle-aware axis-aligned routing through doors and open passages
- Wall, window and external-area avoidance
- Optimization of hub count, cable length, crossings, overlap and bends
- Design validation and floor-plan visualization

**Required skills:** Python · Computational geometry · Graph algorithms · Optimization and pathfinding · Travelling-salesperson methods · Data visualization

**Resources**
- **Dataset:** ResPlan, 17,000 residential plans (Kaggle and GitHub). kaggle.com/datasets/resplan/resplan
- **Input:** one JSON file per plan from the organisers.

---

## Slide 2/8: Problem statement (1/2): Task and inputs
*Joint optimization of hub placement and closed-ring cabling on ResPlan floor plans*

**Steps:** 1 **Place hubs** (choose how many, and where) → 2 **Assign rooms** (each room + its node to one hub) → 3 **Order nodes** (one closed sequence per hub) → 4 **Route cable** (axis-aligned, through doors)

**Given for every plan**
- **Geometry in drawing units.** Convert to metres with the scale on slide 4; invalid polygons are repaired.
- **Eligible rooms:** living, bedroom, kitchen, storage. Each polygon is one room with one node.
- **Node points are fixed inputs:** one arbitrary interior point per room (0.5 m from walls). Teams cannot move them.
- **Excluded:** bathroom, balcony, stair, garden, pool, parking, land. No node and no hub.
- **Benchmark plans:** the organisers keep only plans a reference solver can solve (no room over 100 m²).

**Capacity of each hub**
- **≤ 100 m²** of assigned eligible-room area
- **≤ 5 nodes** assigned to the hub
- Both limits apply at once. Hubs needed ≥ max( ceil(area/100), ceil(nodes/5) )

**Required ring per hub:** H → N1 → N2 → … → Nn → H
- Starts and ends at the same hub
- Visits every assigned node exactly once
- No branches; never ends at another hub
- Every node belongs to exactly one ring
- Retracing a door to reach a single-door room is allowed; shared line length counts as overlap

Bottom line: "Dataset: ResPlan (17,000 residential plans, Kaggle / GitHub)."

---

## Slide 4/8: Scale: from drawing units to metres
*ResPlan coordinates are drawing units, not metres. Two values stored in every plan give the conversion.*

**Example plan: 200 × 180 drawing units.** 200 units ≈ 11.1 m; 180 units ≈ 9.9 m. Rooms: Bedroom 1, Kitchen, Bath, Bedroom 2, Living. "inner (shaded): inner.area = 36,000 units², area = 110 m²". Legend: "Dark bars: walls. Shaded: inner. Dashed: outside inner." Balcony drawn outside inner.

**`inner.area` (drawing units²):** Area of the plan's inner polygon: everything inside the outer walls, meaning all rooms and the partition walls between them. Balconies and outdoor areas are outside it. Example: 36,000 units².

**`area` (m²):** Floor area of that same footprint in square metres. It is a single number stored in the plan. Example: 110 m². Do not use `net_area`: it is 0 in 32% of plans.

**Scale = drawing units per metre**
- `scale = sqrt( inner.area / area )` = sqrt(36,000 / 110) ≈ 18.1 units per metre
- Convert once per plan, before any limit or clearance check.
  - Length: metres = units ÷ scale (90 units ≈ 5.0 m)
  - Area: m² = units² ÷ scale² (100 m² ≈ 32,700 units² here)
  - Clearance: 0.5 m ≈ 9.0 units here
- Code: `scale = (plan["inner"].area / plan["area"]) ** 0.5`

---

## Slide 5/8: Expected solution: illustrative schematic
*Invented 6-room layout, not a ResPlan plan. Two zones, each with one hub and one ring that returns to it.*

**Layout:**
- **Blue zone:** Bedroom 1 (15 m²), Bedroom 3 (15 m²) and Living (40 m²), with hub **H1** in the living area and nodes N1–N3.
- **Orange zone:** Bedroom 2 (16 m²), Kitchen (16 m²) and Storage (15 m²), with hub **H2** and nodes N4–N6.
- **Excluded:** Bathroom ("no node"), drawn in grey.
- The Entrance is on the bottom wall.

Cables are drawn as horizontal and vertical runs only, with circled numbers giving the visiting order. Annotation in Bedroom 2: "Single-door room: cable goes in and back out through the same door (two parallel runs)."

**How to read**
- ■ Hub (one colour per zone)
- ● Node (fixed point in each eligible room)
- → Cable; numbers give visiting order
- ○ Last number is the return to the hub
- ▭ Excluded room; gaps in walls are doors

**Per-hub summary**
- **H1:** Living, Bedrooms 1 and 3. 71 m² | 3 nodes | 23.8 m, 8 bends
- **H2:** Bedroom 2, Kitchen, Storage. 47 m² | 3 nodes | 25.2 m, 12 bends

**What a reviewer confirms**
- ✓ Each node once; ring ends at its own hub
- ✓ ≤ 100 m² and ≤ 5 nodes per hub
- ✓ Each zone connected through doors only
- ✓ Axis-aligned; no wall or window crossed
- ✓ 2 hubs = lower bound (118 m², 6 nodes)

---

## Compiler notes (not from the slides)

### The slides vs `PS4-problem-statement.md` (Honeywell PS4 text)
| Topic | Slides (UR Hackathon 2026H2) | PS4 text |
|---|---|---|
| Names | hub / node | control panel / device |
| Device cap | **≤ 5 nodes** per hub | **≤ 20 devices** per panel |
| Area cap | ≤ 100 m² | ≤ 100 m² (same) |
| Route geometry | axis-aligned (horizontal and vertical runs) | not stated |
| Single-door room | retracing its door is allowed; shared length counts as overlap | not stated |
| Ranking | fewest hubs first, then lowest cost | "optimization of panel count, cable length, crossings, overlaps, bends" (no order) |
| Node positions | **given** by the organisers (interior point 0.5 m from walls); teams can't move them | "fixed device placement within eligible rooms" is listed as a work element |
| Input | one JSON file per plan from the organisers | "an unseen ResPlan floor plan" |
| Multi-part rooms | each polygon is one room with one node | not stated |
| Benchmark set | only plans with no room over 100 m² | not stated |

The kickoff must ask Adi which source wins, especially for the node cap (5 vs 20). The slides are the more specific and look like the event's own deck. Slide 3/8 ("Problem statement 2/2") is missing and probably holds the output/JSON format and the cost formula; ask Adi for it.

### Matches with the verified dataset facts (`~/HACKHIVE/vault/40-Research/dataset-integrity.md`)
- The slides' `scale = sqrt(inner.area / area)` (units per metre) is the inverse of our metres-per-unit √(area ÷ inner.area). Same conversion.
- "Do not use `net_area`" matches our finding that `net_area` is unreliable.
