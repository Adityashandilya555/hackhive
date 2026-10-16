# Solution fixtures (#11)

Hand-made slide-8 submissions for the checker's six hard rules. `make_fixtures.py` builds every
file here from hand-picked waypoints (metres) and the node points `io.load_plan` places (rounded to
1 µm); `tests/test_checker_rules.py` regenerates them and checks nothing has drifted. If node
placement changes, rerun `uv run python tests/fixtures/solutions/make_fixtures.py` and commit the
result. 5088's are checked against `tests/fixtures/plans/plan-5088.json`; the
`syn-*-zone.json` ones against their `syn-*-plan.json` (raw plans in drawing units, 10 units = 1 m).

| File | Breaks |
|---|---|
| `5088-valid.json` | nothing (1 hub; L 24.331, B 12, J 30.331) |
| `5088-two-hubs-valid.json` | nothing (2 hubs; checklist line 5 ✗: 2 hubs > lower bound 1) |
| `5088-omitted-room.json` | rule 1: R03 in no hub, and N3's room isn't in H1 |
| `5088-duplicated-node.json` | rule 1: N2 assigned twice |
| `syn-overcap-zone.json` | rule 2: 198.0 m² in one hub (`syn-overcap-plan.json`) |
| `syn-entrance-zone.json` | rule 3: bedroom and kitchen meet only at the entrance (`syn-entrance-plan.json`) |
| `5088-open-ring.json` | rule 4: the ring never returns to H1 |
| `5088-repeated-node.json` | rule 4: N3 visited twice |
| `5088-ring-ends-at-other-hub.json` | rule 4: H1's ring ends at H2 |
| `5088-off-node-5cm.json` | rule 4: H1→N3 ends 5 cm from N3 |
| `5088-diagonal.json` | rule 5: a diagonal piece |
| `5088-wall-crossing.json` | rule 5: through the living/bedroom wall, outside a door |
| `5088-window-crossing.json` | rule 5: through the kitchen's window and out of `inner` |
| `5088-hub-0.3m-from-wall.json` | rule 6 `error`: the hub is 0.3 m from the living's window |
| `5088-hub-in-unassigned-room.json` | rule 6 `error`: H2 sits in R01, which it isn't assigned |
| `5088-retrace-single-door.json` | nothing: the retrace out of the single-door kitchen is legal (scored as O) |
| `5088-retrace-two-door.json` | rule 4 `strict`: 1.00 m retraced inside the two-door bedroom (#46 Q5) |
| `5088-duplicate-hub-id.json` | rule 1: two hubs called H1 (#68 review) |
| `5088-hub-in-single-door-room.json` | nothing: the hub shares the single-door kitchen, so the ring leaves and re-enters on one line (legal; #68 review) |
| `syn-open-plan.json` | (a plan) living and kitchen joined by one open passage, for rule 6's `strict` |
