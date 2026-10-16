<!-- Compiled 2026-10-15 ~22:20 from the 7 new organiser files in this folder (received after the issues were filed).
     The text is verbatim; the images are the originals. Check them when a detail matters. -->

# UR Hackathon 2026H2: new slides 3, 6, 7, 8 (+ clean copies of 1, 2, 4)

## Which file is which
| Slide | Title | File |
|---|---|---|
| 1/8 | Project scope and expected results (clean copy) | `Screenshot 2026-10-15 221749.png` |
| 2/8 | Problem statement (1/2): Task and inputs (clean copy) | `Screenshot 2026-10-15 221753.png` |
| **3/8** | **Problem statement (2/2): Rules, ranking and deliverables** (new) | `Screenshot 2026-10-15 221800.png` |
| 4/8 | Scale: from drawing units to metres (clean copy) | `WhatsApp Image 2026-10-15 at 22.18.44 (3).jpeg` |
| **6/8** | **Evaluation: objective and metric definitions** (new) | `WhatsApp Image 2026-10-15 at 22.18.44 (2).jpeg` |
| **7/8** | **Metric definitions with examples** (new) | `WhatsApp Image 2026-10-15 at 22.18.44 (1).jpeg` |
| **8/8** | **Data format: input and output JSON** (new) | `WhatsApp Image 2026-10-15 at 22.18.44.jpeg` |

Slides 1, 2 and 4 match `PS4-slides-transcript.md`. One change: slide 1's ranking line is now legible and reads "**Ranking:** fewest hubs first, then lowest cost J." Slide 5 is unchanged (earlier files). **All 8 slides are now available.**

---

## Slide 3/8: Problem statement (2/2): Rules, ranking and deliverables
*A design must pass every rule before it is ranked*

**Hard rules: a plan is invalid if any is broken**
1. **Coverage:** every eligible room and its node is assigned to exactly one hub; none omitted, split or duplicated.
2. **Capacity:** each hub serves at most 100 m² of eligible-room area and at most 5 nodes.
3. **Zone:** a hub's rooms are connected through doors or open passages only. Windows, wall-only adjacency and the entrance do not count.
4. **Ring:** one closed ring per hub: H → every assigned node once → same H, no branches. Retracing a door to reach a single-door room is allowed; shared line length counts as overlap.
5. **Free space:** cable stays inside the inner boundary, outside walls and windows, runs only horizontally or vertically, and passes between rooms only through door or open-passage gaps.
6. **Hub point:** inside an eligible room assigned to that hub, at least 0.5 m from walls. A hub and a node may share a room.

**Ranking of valid designs:** fewest hubs first, then lowest cost (L + X + O + B)
- **H:** Fewest hubs (always decides first)
- **L:** Cable length, returns included (1/m)
- **X:** Crossings, any rings (5 each)
- **O:** Overlap on the same line (1/m)
- **B:** Bends, all 90° turns (0.5 each)
- **Tie-break:** smaller assigned-area gap between hubs

**Deliverables per plan**
- **Floor-plan overlay:** zones, hub and node IDs, visiting order, full rings with return leg, violations in red
- **JSON** with the same hubs, sequences, polylines and lengths

---

## Slide 6/8: Evaluation: objective and metric definitions
*The checker recomputes every metric from the submitted polylines; teams choose how to optimise*

**Fewest hubs wins. Ties go to the lowest cost J.**
J = L + 5X + O + 0.5B, in metres: cable length, plus 5 per crossing, 1 per metre of overlap, 0.5 per bend. Hard rules on slide 3 apply first.

**Metric definitions (per plan, computed by the checker)**
- **Hubs H:** number of selected hubs; always compared first.
- **Cable length L (1 per m):** sum of polyline lengths in metres over every leg of every ring, return legs included.
- **Crossings X (5 each):** points where two cables meet away from a node or hub, inside one ring or between rings.
- **Overlap O (1 per m):** total length in metres where two legs run along the same line. A retrace on the same line counts; parallel runs do not.
- **Bends B (0.5 each):** direction changes along each closed ring, nodes included. Cable is axis-aligned, so every turn is 90°; a reversal counts once.
- **Tie-break:** equal J is broken by a smaller assigned-area gap between hubs. **Tolerance:** 1 mm. Weights are starting values, fixed after reference runs.

**Scoring across plans**
- Values reported in the JSON are ignored
- Per plan, valid designs are ranked by hub count, then by cost J
- Rank points add up over hidden, unseen plans
- An invalid or missing plan scores zero

**Left for the teams**
- The optimisation model and algorithm
- Any internal cost or weighting they like
- Only the final hub count and cost J are scored

---

## Slide 7/8: Metric definitions with examples
*The checker computes each metric from the submitted polylines. The drawings show what counts and what does not.*

| Metric | Definition | Counts | Does not count |
|---|---|---|---|
| **Cable length (L)** | Total length of every leg of every ring, including the leg back to the hub, measured along the polyline in metres. | 4 + 2 + 3 + 3 = 12 m, return leg included | (also counts) A retraced stretch counts each time it is run: 3 m out + 3 m back = 6 m |
| **Crossings (X)** | A point where two cables meet away from a node or hub. It counts inside one ring and between rings, once per point. | cables meet in the open, X = 1 | legs meet at a node, X = 0 |
| **Overlap (O)** | Length where cable lies on top of other cable on the same line, such as a retrace. A stretch covered by k legs counts (k − 1) times its length. | 2 m on the same line, O = 2 m | parallel runs on different lines, O = 0 |
| **Bends (B)** | A point where the cable changes direction, at a vertex, a node or the hub. All turns are 90°, and a reversal counts once. | each turn is one bend, B = 2 | straight through, B = 0 |

**Tolerance 1 mm:** points closer than that are the same point, and lines within 1 mm of each other are the same line. Each metric is counted per plan, summed over all rings.

---

## Slide 8/8: Data format: input and output JSON
*One input file per plan; one output file per plan from each team; and a visualizations for final presentation*

**Output file (team submits)**
```json
{
  "plan_id": "PLAN_0123", "units": "m",
  "hubs": [
    {
      "hub_id": "H1", "room_id": "R05",
      "location": {"x": 5.2, "y": 3.6},
      "assigned_rooms": ["R05", "R01", "R04"],
      "assigned_nodes": ["N1", "N2", "N3"],
      "closed_sequence": ["H1","N1","N3","N2","H1"],
      "route_segments": [
        {"from":"H1","to":"N1","length_m":4.8,
         "polyline":[[5.2,3.6],[7.6,3.6],[7.6,1.2]]},
        {"from":"N1","to":"N3","length_m":7.1,
         "polyline":[[7.6,1.2],[6.6,1.2],[6.6,2.2],[1.5,2.2]]},
        ... N3 to N2 and N2 to H1 follow
      ],
      "ring_length_m": 23.8
    },
    ... H2 has the same structure
  ],
  "reported_metrics": {"hub_count": 2, "total_length_m": 49.0}
}
```
*Abridged: H1 is the ring on slide 5. Output in metres (units ÷ scale), origin lower-left, y up.*

The **input** file's format is **not shown** on the slide; only the output box is visible.

---

## Compiler notes (not from the slides): what's new against what was filed
The filed plan is described by `~/HACKHIVE/repos/hackhive/AGENTS.md` (branch `docs/architecture`) and ADR-006.

| Topic | Filed plan assumed | Slides now say |
|---|---|---|
| Output JSON | own format, `schema_version`, `hubs[].ring`, `segments`… (ADR-006); organiser exporter "when it arrives" | **exact format, slide 8**: `plan_id`, `units:"m"`, `hubs[]` {`hub_id`, `room_id`, `location{x,y}`, `assigned_rooms`, `assigned_nodes`, `closed_sequence`, `route_segments[]{from,to,length_m,polyline}`, `ring_length_m`}, `reported_metrics{hub_count,total_length_m}`; metres, **origin lower-left, y up** |
| Cost | replaceable weights, crossing default **2.0** (ADR-006) | **J = L + 5X + O + 0.5B**, so a crossing costs **5**; weights may be re-fixed after reference runs, so keep them as settings defaulting to 1 / 5 / 1 / 0.5 |
| Tie-break | `rank_key = (k, cost, zone_signature)` | **smaller assigned-area gap between hubs** |
| Hub point | AGENTS.md: "≥ 0.5 m from nodes" | **≥ 0.5 m from walls**; a hub and a node **may share** a room |
| Zone links | doors / open passages | doors / open passages only; **windows, wall-only adjacency and the entrance don't count** |
| Scoring | n/a | the checker **recomputes everything from polylines**, so reported values are ignored; **invalid or missing plan = 0**; rank points summed over hidden plans; tolerance 1 mm |
| Metric details | ours | crossings count **inside one ring too**, once per point, not at nodes or hubs; overlap = (k − 1) × length for k legs on the same line, **parallel runs on different lines don't count**; bends count **at nodes and the hub too**, and a reversal counts once; retraced length counts in L each time |
| Overlay | zones, hubs, numbered rings, routes, per-hub summary, checklist | must also show **hub and node IDs** and **violations in red** |
| Deliverable | demo app | plus **"a visualization for the final presentation"** |
| Coordinates | ResPlan units → metres | metres = units ÷ scale; output origin lower-left, y up. `~/refs/ps4/resplan/resplan_utils.py` (@ `e2b78fe`) plots without `invert_yaxis`, which suggests ResPlan is already y-up; **verify, and make a flip a setting** |
| Input format | organiser JSON adapter | **still unknown** (slide 8 shows only the output). Room ids look like `R05`, node ids like `N1` |

**Strategy point, for the J-optimising router:** slide 7 says parallel runs on different lines don't count as overlap, and slide 5 draws the single-door retrace as two parallel runs. So in a single-door room, "same-line retrace" (O = length) and "parallel runs ≥ 1 mm apart" (O = 0, but each turn adds 0.5) are both legal. The router should pick whichever gives the lower J.
