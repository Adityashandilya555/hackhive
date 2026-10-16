# Output format

`hackhive solve <plan.json> --out <dir>` writes up to two files per plan, named by the plan id (unsafe
characters become `_`):

- `<plan_id>.json`: the **submission**, exactly slide 8's keys. Written only when the design has no
  `error` violation (C43): an invalid or missing plan scores zero.
- `<plan_id>.sidecar.json`: **everything else** (status, violations, bounds, the arrangement
  used, cost, settings, provenance, warnings). Always written.

The JSON schemas of both are committed in `schemas/` (`python -m hackhive.contracts --check` fails if
they are stale). Edge cases are decided in [`decision_conventions.md` §8](decision_conventions.md#8-output-and-submission),
including the gate that walks down the ranked arrangements.

## The submission (slide 8)

Metres, origin lower-left, y up. Unknown keys are rejected (`extra="forbid"`).

| Key | Value |
|---|---|
| `plan_id` | the plan's id, verbatim |
| `units` | always `"m"` |
| `hubs[]` | one entry per hub, ids `H1`, `H2`, … |
| `hubs[].hub_id` | the hub's id |
| `hubs[].room_id` | the eligible room the hub sits in |
| `hubs[].location` | an object `{"x": …, "y": …}` |
| `hubs[].assigned_rooms` | the hub's zone: its own room first, then the others by id |
| `hubs[].assigned_nodes` | the ring's nodes, sorted by id |
| `hubs[].closed_sequence` | the ring order, starting and ending with the hub id |
| `hubs[].route_segments[]` | one per consecutive pair of `closed_sequence`, in that order |
| `…route_segments[].from`, `.to` | the pair's ids |
| `…route_segments[].length_m` | the polyline's length |
| `…route_segments[].polyline` | corner points `[x, y]`; starts at the `from` point, ends at the `to` point |
| `hubs[].ring_length_m` | the sum of the ring's `length_m`, return leg included |
| `reported_metrics.hub_count` | the number of hubs |
| `reported_metrics.total_length_m` | the sum of every `ring_length_m` |

The organisers recompute every length and metric from the polylines and ignore the reported ones
(C42). Ours are always recomputed from the written polylines; a mismatch is a sidecar warning.

### Rounding (T6)

Every number in the submission is written with **at most 4 decimals** (0.1 mm, inside the checker's
1 mm tolerance). Along a polyline, a coordinate within 1 mm of the previous vertex's takes that vertex's
rounded value, so both ends of every segment snap to the same number and each segment stays
**exactly** horizontal or vertical. Lengths are then recomputed from the rounded points.

### Example (fixture 5924)

This is `hackhive solve tests/fixtures/plans/plan-5924.json`'s submission. `tests/test_docs.py`
checks that it parses, matches `schemas/submission.schema.json` and passes `hackhive check` against
`tests/fixtures/plans/plan-5924.json`. It is `enumerate()`'s first-ranked arrangement (#22); regenerate it
when the solver's choice changes.

```json
{
  "plan_id": "PLAN_5924",
  "units": "m",
  "hubs": [
    {
      "hub_id": "H1",
      "room_id": "R01",
      "location": {"x": 13.45, "y": 9.75},
      "assigned_rooms": ["R01", "R02", "R05"],
      "assigned_nodes": ["N1", "N2", "N5"],
      "closed_sequence": ["H1", "N2", "N1", "N5", "H1"],
      "route_segments": [
        {
          "from": "H1",
          "to": "N2",
          "length_m": 7.8059,
          "polyline": [
            [13.45, 9.75],
            [9.35, 9.75],
            [9.35, 12.55],
            [10.2322, 12.55],
            [10.2322, 12.5263]
          ]
        },
        {
          "from": "N2",
          "to": "N1",
          "length_m": 11.6765,
          "polyline": [
            [10.2322, 12.5263],
            [10.25, 12.5263],
            [10.25, 12.65],
            [9.25, 12.65],
            [9.25, 8.75],
            [12.95, 8.75],
            [12.95, 5.8442],
            [12.9792, 5.8442]
          ]
        },
        {
          "from": "N1",
          "to": "N5",
          "length_m": 7.3635,
          "polyline": [
            [12.9792, 5.8442],
            [12.9792, 5.85],
            [13.35, 5.85],
            [13.35, 9.65],
            [16.45, 9.65],
            [16.45, 9.7188],
            [16.4681, 9.7188]
          ]
        },
        {
          "from": "N5",
          "to": "H1",
          "length_m": 3.0493,
          "polyline": [
            [16.4681, 9.7188],
            [16.4681, 9.75],
            [13.45, 9.75]
          ]
        }
      ],
      "ring_length_m": 29.8952
    },
    {
      "hub_id": "H2",
      "room_id": "R03",
      "location": {"x": 2.25, "y": 5.95},
      "assigned_rooms": ["R03"],
      "assigned_nodes": ["N3"],
      "closed_sequence": ["H2", "N3", "H2"],
      "route_segments": [
        {
          "from": "H2",
          "to": "N3",
          "length_m": 0.3754,
          "polyline": [
            [2.25, 5.95],
            [2.25, 6.15],
            [2.4196, 6.15],
            [2.4196, 6.1442]
          ]
        },
        {
          "from": "N3",
          "to": "H2",
          "length_m": 0.4246,
          "polyline": [
            [2.4196, 6.1442],
            [2.45, 6.1442],
            [2.45, 5.95],
            [2.25, 5.95]
          ]
        }
      ],
      "ring_length_m": 0.8
    },
    {
      "hub_id": "H3",
      "room_id": "R04",
      "location": {"x": 2.15, "y": 11.85},
      "assigned_rooms": ["R04"],
      "assigned_nodes": ["N4"],
      "closed_sequence": ["H3", "N4", "H3"],
      "route_segments": [
        {
          "from": "H3",
          "to": "N4",
          "length_m": 0.422,
          "polyline": [
            [2.15, 11.85],
            [2.35, 11.85],
            [2.35, 11.6602],
            [2.3178, 11.6602]
          ]
        },
        {
          "from": "N4",
          "to": "H3",
          "length_m": 0.378,
          "polyline": [
            [2.3178, 11.6602],
            [2.3178, 11.65],
            [2.15, 11.65],
            [2.15, 11.85]
          ]
        }
      ],
      "ring_length_m": 0.8
    }
  ],
  "reported_metrics": {
    "hub_count": 3,
    "total_length_m": 31.4952
  }
}
```

## The sidecar (ours)

| Key | Value |
|---|---|
| `plan_id` | the plan's id, or the file stem when the plan couldn't be read |
| `status` | see below |
| `reason` | why there is no submission, in words (`infeasible`, `input_error`) |
| `frame` | the organiser node frame the geometry was moved into (`identity`, `flip_y`, `origin_shift`), or null when no plan loaded |
| `violations[]` | the checker mirror's findings: `rule`, `severity` (`error` or `strict`), `message`, `ids`, `hub_id`, `where` (points drawn in red) |
| `bounds` | `k_lb` (the hub lower bound), `k_found` (the submitted design's hub count), `proof`, see below |
| `arrangement` | which ranked arrangement was submitted (null when nothing was): `rank` (1 = the best-ranked; null for the walking skeleton's first cut), `signature` (its zones, e.g. `R01+R02+R05\|R03\|R04`), `skipped[]` (each better-ranked one, best first, as `#<rank> <signature>: <why not>`) |
| `cost` | `H`, `L`, `X`, `O`, `B`, `J` and `area_gap` of the submitted design, under the strict counting reading (see [Metrics](#metrics-and-j)) |
| `hubs[]` | per hub: `area_m2`, `n_nodes`, `L`, `X`, `O`, `B` |
| `settings` | every constraint used; anything other than the problem statement is a WHAT-IF |
| `provenance` | `input_path`, `input_digest` (sha256), `version` |
| `warnings[]` | readable lines, see below |
| `folded_parts[]` | living slivers folded into a neighbour (generated nodes only) |

### Proof

`bounds.proof` says what is proven about `k_found`:

| `proof` | When |
|---|---|
| `lb_met` | `k_found` equals the lower bound `k_lb`, so no design can use fewer hubs |
| `exhaustive` | every zoning was enumerated and none uses fewer hubs than `k_found` |
| `none` | nothing was submitted (`k_found` is null), or a design with fewer hubs exists but had only `strict` or `error` violations, or couldn't be built, so the gate moved up a hub count |
| `greedy` | not emitted yet: reserved for the greedy zoning fallback (#49) |

### Status

| `status` | When | Submission written? |
|---|---|---|
| `ok` | an arrangement passes every rule of the checker mirror: the first clean one in rank order, even at more hubs, but at most one hub count above the first design with no `error` | yes |
| `submitted_strict_only` | no clean arrangement in that range; the best-ranked one whose only violations are `strict` (a stricter reading of an unclear rule, e.g. a hub 0.5 m from the walls but closer to a door) | yes, flagged |
| `infeasible` | no design could be built (no zoning, a room over the area cap, routing failed) or the design has an `error` violation; `reason` says which | no |
| `input_error` | the plan file can't be read or fails the organiser-format check (#38) | no |

A WHAT-IF run (settings other than the problem statement, #30) never writes a submission, whatever
its status.

### Warnings

Each is a line in `warnings[]`; none of them stops a submission.

| Warning | When | Submission written? |
|---|---|---|
| `wall_clearance_only` (written `H<n>: wall_clearance_only`) | the hub is ≥ 0.5 m from walls but no spot in its room is ≥ 0.5 m from the whole room edge; the checker also lists it as a `strict` rule-6 violation | yes (`submitted_strict_only`) |
| `… node N<k> clearance 0.42 m < 0.5 m` | a node point (given or generated) sits closer than 0.5 m to a wall | yes |
| `… folded into …: too narrow for a node at the wall clearance (C38)` | a living sliver too narrow for a node was merged into a neighbour (generated nodes only) | yes |
| `greedy_zoning` | not emitted yet: reserved for the greedy zoning fallback (#49), which would also set `proof: greedy` | (n/a) |
| `WHAT-IF: …` | the settings leave the problem statement | no (WHAT-IF) |
| `frame: …` | organiser geometry was moved into the given nodes' frame (#38) | yes |
| `plan_id '…' isn't a safe file name` | files are written under a sanitised name | yes |
| `… lies NN% outside inner` | an eligible room sticks out of the plan outline by more than 20 % | yes |
| `reported … recomputed …` | a reported length disagrees with its polyline by more than 1 mm (C42) | yes |

## Metrics and J

J = L + 5X + O + 0.5B, in metres: L the total cable length (return legs included), X the crossings, O
the overlapping length, B the bends. The weights are the organisers' starting values, kept as settings
(#48). Designs rank by fewest hubs H, then lowest J, then the smaller assigned-area gap (max − min of
the hubs' assigned eligible area). Each arrangement is scored under both readings of the unclear
counting rules (strict and lenient, D3) and ranked by its **worse** J. The exact counting rules (what counts as a crossing, overlap or bend)
are in [`decision_conventions.md` §6](decision_conventions.md#6-metrics-our-checker-mirror-slides-6-7).
