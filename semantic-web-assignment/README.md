# Smart Farming and Irrigation Decisions: OWL ontology (CSE4007, Option 1, Case 1A)

A small OWL 2 ontology that classifies farm fields needing irrigation attention, built in
Protégé style on top of the open W3C/OGC **SOSA** sensor ontology. The **HermiT** reasoner
infers the classifications and checks consistency.

Nothing was built from scratch:

| Re-used open-source component | Used for | Licence |
|---|---|---|
| [W3C/OGC SOSA ontology](https://www.w3.org/TR/vocab-ssn/) (`ontology/sosa.ttl`, unchanged) | Sensor, Observation, FeatureOfInterest, Actuator, Actuation, observedProperty, madeBySensor, resultTime … | W3C Software & Document License (see `ontology/SOSA-NOTICE.md`) |
| [Protégé Desktop](https://protege.stanford.edu/) | Editing and viewing the ontology, running HermiT | BSD-2 |
| [HermiT](http://www.hermit-reasoner.com/) (bundled in Protégé and in owlready2) | OWL 2 DL reasoning and consistency checks | LGPL |
| [owlready2](https://owlready2.readthedocs.io/) + [rdflib](https://rdflib.readthedocs.io/) | Scripted, reproducible reasoning and SPARQL competency-question queries | LGPL / BSD-3 |

Only the case-specific layer (`ontology/smartfarm.ttl`) is new.

## Folder layout

```
ontology/
  smartfarm.ttl        case ontology (classes, properties, restrictions, fictional individuals)
  sosa.ttl             W3C SOSA, imported by smartfarm.ttl (third-party, unchanged)
  catalog-v001.xml     lets Protégé resolve the SOSA import from the local file
  SOSA-NOTICE.md       source and licence of sosa.ttl
tests/
  inconsistent_two_farms.ttl         must make the model inconsistent (functional property)
  inconsistent_two_system_types.ttl  must make the model inconsistent (disjoint classes)
  owa_closure.ttl                    open-world demo: closing Field E makes it "unmonitored"
scripts/reason.py      runs HermiT, prints inferences, answers the 8 competency questions
output/
  reasoner-output.txt     saved output of `python scripts/reason.py --tests`
  smartfarm-merged.owl    SOSA + case merged into one RDF/XML file (no imports)
  smartfarm-inferred.ttl  asserted + inferred triples after reasoning
report/REPORT.md       report draft (problem, competency questions, modelling choices, results, limitations)
```

## Open and reason in Protégé (the main tool for Option 1)

1. Install Protégé 5.6+ (it bundles HermiT).
2. **File → Open…** and pick `ontology/smartfarm.ttl`. Protégé reads `catalog-v001.xml` from the same
   folder, so the SOSA import loads from `ontology/sosa.ttl` without internet.
   (Alternative: open `output/smartfarm-merged.owl`, which has everything in one file.)
3. **Reasoner → HermiT → Start reasoner.** No red classes means the model is consistent.
4. Look at the inferences (yellow background):
   - **Entities → Classes**, select `FieldNeedingAttention` → *Instances*: `FieldA`, `FieldC`.
   - Select `CriticallyDryObservation`: it is now under `LowMoistureObservation` (inferred subclass).
   - **Individuals → `ObsA1`** → *Object property assertions*: `hasFeatureOfInterest FieldA`
     (inferred through the property chain `madeBySensor o installedIn`).
5. Competency questions: **Window → Tabs → SPARQL Query**, then paste a query from `scripts/reason.py`
   (`COMPETENCY_QUESTIONS`). The SPARQL tab may only see asserted triples, so check the inferred
   ones in the **DL Query** tab (e.g. type `FieldNeedingAttention`, tick *Instances*) or run the script below.
6. Consistency tests: add the one assertion from a `tests/` file by hand, e.g. give `DR1` the extra type
   `SprinklerIrrigation`, then **Reasoner → Synchronize reasoner**. Protégé reports the inconsistency and
   **Explain** shows why (disjoint classes). Undo the change afterwards.

## Reproduce from the command line (any OS, needs Java 8+)

```bash
cd semantic-web-assignment
python -m pip install -r requirements.txt
python scripts/reason.py            # base model: consistency, inferences, CQ answers, writes output/
python scripts/reason.py --tests    # also runs every file in tests/ and checks the expected verdict
```

The script exits with code 0 only if the base model is consistent and every test gives its
expected result. Saved output: `output/reasoner-output.txt`.

## Expected results (verified)

| Inference | Result |
|---|---|
| Consistency (base model) | consistent |
| `LowMoistureObservation` | ObsA1 (28.5 %), ObsB1 (26.0 %), ObsC1 (14.2 %) |
| `CriticallyDryObservation` | ObsC1 |
| `FieldNeedingAttention` | FieldA (paddy + low moisture), FieldC (critically dry) — **not** FieldB (ragi is drought-tolerant) |
| `WaterEfficientField` | FieldC, FieldD (drip) |
| `HighWaterDemandField` | FieldA, FieldD |
| `MonitoredField` | FieldA–D |
| `UnmonitoredField` | nobody (open world) — FieldE only after `tests/owa_closure.ttl` |
| Property chain | ObsA1→FieldA, ObsB1→FieldB, ObsC1→FieldC, ObsD1→FieldD |
| Class subsumption | `CriticallyDryObservation ⊑ LowMoistureObservation` |
| `tests/inconsistent_*.ttl` | inconsistent, as expected |

## Viva prompts to practise ("predict, then change")

- Change `ObsB1`'s moisture to `16.0` → predict FieldB becomes `FieldNeedingAttention` (critically dry).
- Re-type `Ragi` as `WaterIntensiveCrop` → inconsistent (crop categories are disjoint). Remove its old type first → FieldB and FieldE become `HighWaterDemandField`, and FieldB also needs attention.
- Delete `SM_C installedIn FieldC` → ObsC1 loses its field, so FieldC is no longer flagged or `MonitoredField`.

## Acknowledgement

All farms, people, devices and readings are fictional. The SOSA ontology is by the W3C/OGC Spatial Data
on the Web Working Group. AI assistance (Claude) was used to draft the ontology, script and report.
The author must check every axiom and result in Protégé and be able to explain and change them.
