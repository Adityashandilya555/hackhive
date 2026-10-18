# Smart Farming and Irrigation Decisions: an OWL Ontology on SOSA

**CSE4007 Semantic Web: Continuous Assessment, Option 1 (Protégé ontology), Case 1A**
Name: ____________  Reg. No.: ____________  Tools: Protégé 5.6 + HermiT; owlready2 0.51 / rdflib 7.6 for scripted checks

> Draft: replace the blanks, add your own Protégé screenshots where marked, and re-word the text in your own voice
> before you submit. Every number below comes from `output/reasoner-output.txt`.

---

## 1. Problem, purpose and scope

A farm has several fields, each growing a crop on a particular soil and watered by a particular irrigation
system. Soil-moisture sensors in the fields and a weather station report readings. Today the farm manager reads
raw numbers and decides by eye which fields to water. Two problems follow: (i) the same moisture value means
different things for different crops (26 % is fine for ragi, too dry for paddy), and (ii) readings are stored
per sensor, not per field, so linking a reading to a crop needs manual look-ups.

**Purpose.** A shared, machine-processable model that links observations → sensors → fields → crops and
equipment, and lets a reasoner **classify fields that need irrigation attention** with an explanation.

**In scope.** One farm, its fields, crop *kinds*, soil types, irrigation systems, soil-moisture sensors,
a weather station, observations and irrigation events. **Out of scope.** Agronomic models (evapotranspiration),
time-series analytics, prices, labour, and the history of sensor deployments.

**Reuse.** Sensors, observations, features of interest, actuators and actuations are not re-invented: they come
from the W3C/OGC **SOSA** ontology, imported unchanged. Only farm-specific terms are new (`sf:` namespace,
`http://example.org/smartfarm#`).

## 2. Competency questions

| # | Competency question | How it is answered |
|---|---|---|
| CQ1 | Which fields need irrigation attention, and from which reading? | Reasoner: defined class `FieldNeedingAttention` |
| CQ2 | Which crop is grown in each field, on which soil type? | Asserted facts (`grows`, `hasSoilType`) |
| CQ3 | Which sensor made each soil-moisture reading, and which field does it describe? | Reasoner: property chain fills `hasFeatureOfInterest` |
| CQ4 | Which fields use water-efficient (drip) irrigation? | Reasoner: `WaterEfficientField` |
| CQ5 | What is the latest soil-moisture reading for each field? | SPARQL aggregate over `sosa:resultTime` (needs CQ3's inferred link) |
| CQ6 | Which irrigation events were applied, with which system and how much water? | Asserted facts |
| CQ7 | Who manages the farm each field belongs to? | Asserted facts + inverse `hasField`/`partOfFarm` |
| CQ8 | Which fields have no soil-moisture sensor on record? | Closed-world SPARQL check; contrasted with OWL's open world (§6) |

All eight are run by `scripts/reason.py` as SPARQL queries over the asserted + inferred graph.

## 3. The model

### 3.1 Classes (24 new, plus SOSA's)

- **SOSA specialisations:** `Field`, `Farm` ⊑ `sosa:FeatureOfInterest`; `SoilMoistureSensor`, `WeatherStation` ⊑ `sosa:Sensor`;
  `SoilMoistureObservation`, `WeatherObservation` ⊑ `sosa:Observation`; `IrrigationSystem` ⊑ `sosa:Actuator`
  (with `DripIrrigation`, `SprinklerIrrigation`, `FloodIrrigation`); `IrrigationEvent` ⊑ `sosa:Actuation`.
- **Domain classes:** `Crop` (with `WaterIntensiveCrop`, `ModerateWaterCrop`, `DroughtTolerantCrop`), `SoilType`, `FarmManager`.
- **Defined classes (≡, necessary and sufficient):** `LowMoistureObservation`, `CriticallyDryObservation`,
  `FieldNeedingAttention`, `HighWaterDemandField`, `WaterEfficientField`, `MonitoredField`, `UnmonitoredField`.

*[Screenshot 1: Protégé class hierarchy, asserted vs inferred]*

### 3.2 Object properties (9 new, plus SOSA's `madeBySensor`, `hasFeatureOfInterest`, `observedProperty`, `madeByActuator`, `observes`)

| Property | Domain → Range | Characteristics and why |
|---|---|---|
| `partOfFarm` | Field → Farm | **Functional**: a field belongs to one farm in this scope |
| `hasField` | Farm → Field | **inverseOf** `partOfFarm`, so either direction can be asserted |
| `managedBy` | Farm → FarmManager | — |
| `grows` | Field → Crop | not functional: intercropping is possible |
| `hasSoilType` | Field → SoilType | **Functional**: one dominant soil type is recorded |
| `hasIrrigationSystem` | Field → IrrigationSystem | — |
| `installedIn` | Sensor → Field | **Functional**: current deployment only |
| `hasSensor` | Field → Sensor | **inverseOf** `installedIn` |
| `irrigates` | IrrigationEvent → Field | — |

**Property chain:** `sosa:madeBySensor ∘ installedIn ⊑ sosa:hasFeatureOfInterest`.

### 3.3 Datatype properties (7)

`soilMoisturePercent` (xsd:decimal, **functional**, sub-property of `sosa:hasSimpleResult`), `airTemperatureC`,
`rainfallMm` (both sub-properties of `sosa:hasSimpleResult`), `areaHectares` (functional), `waterVolumeLitres`,
`serialNumber` (xsd:string, functional), `startTime` (xsd:dateTime). Times use `sosa:resultTime`.

### 3.4 Restrictions (16) and other axioms

- `Field ⊑ ∃partOfFarm.Farm ⊓ ∃hasSoilType.SoilType`; `Farm ⊑ ∃managedBy.FarmManager`
- `SoilMoistureSensor ⊑ ∃observes.{SoilMoisture}`; `SoilMoistureObservation ⊑ ∃observedProperty.{SoilMoisture}`
- `IrrigationEvent ⊑ ∃irrigates.Field ⊓ ∃madeByActuator.IrrigationSystem`
- `LowMoistureObservation ≡ SoilMoistureObservation ⊓ ∃soilMoisturePercent.decimal[< 30.0]`
- `CriticallyDryObservation ≡ SoilMoistureObservation ⊓ ∃soilMoisturePercent.decimal[< 18.0]`
- `FieldNeedingAttention ≡ Field ⊓ ( ∃isFeatureOfInterestOf.CriticallyDryObservation ⊔ (∃grows.WaterIntensiveCrop ⊓ ∃isFeatureOfInterestOf.LowMoistureObservation) )`
- `WaterEfficientField ≡ Field ⊓ ∃hasIrrigationSystem.DripIrrigation`; `HighWaterDemandField ≡ Field ⊓ ∃grows.WaterIntensiveCrop`
- `MonitoredField ≡ Field ⊓ ∃hasSensor.SoilMoistureSensor`; `UnmonitoredField ≡ Field ⊓ ¬∃hasSensor.SoilMoistureSensor`
- **Disjointness:** {Farm, Field, Crop, SoilType, FarmManager, Sensor, Observation, Actuation, IrrigationSystem};
  {Drip, Sprinkler, Flood}; {WaterIntensive, ModerateWater, DroughtTolerant}. `AllDifferent` on the soil types.

### 3.5 Individuals (fictional)

Nandi Valley Farm managed by Kavya Rao; fields **A** (paddy, black clay, flood), **B** (ragi, red loam, sprinkler),
**C** (tomato, sandy loam, drip DR1), **D** (sugarcane, red loam, drip DR2), **E** (ragi, newly leased, no sensor);
sensors SM_A–SM_D and weather station WS1; observations ObsA1 = 28.5 %, ObsB1 = 26.0 %, ObsC1 = 14.2 %,
ObsD1 = 41.0 %, plus air temperature 31.4 °C and rainfall 0 mm; irrigation event IE1 (1800 L on Field C via DR1).
33 named individuals in total.

*[Screenshot 2: individuals with property assertions for FieldA and ObsA1]*

## 4. Key modelling decisions (and alternatives considered)

1. **Reuse SOSA rather than invent `Sensor`/`Observation`.** It gives standard meaning that other farm or IoT
   systems already understand. *Alternative:* own classes, simpler but not interoperable.
2. **Crops are kinds, modelled as individuals of crop categories** (`PaddyRice a WaterIntensiveCrop`), not one
   individual per plant. The farm reasons about *what is grown*, not single plants. *Alternative:* crops as classes
   (`PaddyRice ⊑ Crop`); then `grows` would need class-level punning or `∃grows.PaddyRice`, which is harder to query.
3. **Water demand as disjoint categories, thresholds as datatype facets.** OWL 2 DL cannot compare two data
   values (reading vs a per-crop threshold), so thresholds are fixed in the class definitions (30 % / 18 %) and
   crops are grouped by demand. *Alternative:* SWRL rules with per-crop thresholds, which are more flexible but
   need a rule engine and are not decidable OWL.
4. **Readings name only the sensor; the field is inferred** with a property chain. This matches how sensors
   report data and avoids inconsistent duplicate links. *Cost:* if a sensor is moved, old readings would be
   re-attributed. That is acceptable because deployment history is out of scope (see §7).
5. **Functional properties where the domain really has one value** (`partOfFarm`, `hasSoilType`, `installedIn`,
   `soilMoisturePercent`, `serialNumber`). They turn data-entry errors into detectable inconsistencies.
   `grows` is deliberately *not* functional (intercropping).
6. **Irrigation systems as SOSA actuators, irrigation events as actuations**, so watering is recorded with the
   same pattern as sensing.

## 5. Results: reasoning and consistency

HermiT reports the base model **consistent**. New facts after reasoning:

| Inferred | Individuals / axiom | Why |
|---|---|---|
| `hasFeatureOfInterest` | ObsA1→FieldA, ObsB1→FieldB, ObsC1→FieldC, ObsD1→FieldD | property chain |
| `LowMoistureObservation` | ObsA1, ObsB1, ObsC1 | value < 30 |
| `CriticallyDryObservation` | ObsC1 | value < 18 |
| `FieldNeedingAttention` | **FieldA**, **FieldC** | A: paddy + 28.5 %; C: 14.2 % |
| `HighWaterDemandField` | FieldA, FieldD | water-intensive crop |
| `WaterEfficientField` | FieldC, FieldD | drip irrigation |
| `MonitoredField` | FieldA–D | inverse of `installedIn` |
| class subsumption | `CriticallyDryObservation ⊑ LowMoistureObservation` | facet [<18] ⊂ [<30] |

The key result: **FieldB is *not* flagged** although its reading (26 %) is lower than FieldA's (28.5 %), because
ragi is drought-tolerant. This is exactly the crop-aware judgement the manager wanted.

*[Screenshot 3: Protégé, FieldNeedingAttention with inferred instances highlighted]*

**Consistency tests** (`tests/`):

| Test | Change | Expected | HermiT |
|---|---|---|---|
| `inconsistent_two_farms.ttl` | FieldB also `partOfFarm` HillsideFarm, declared different from Nandi Valley | functional property violated | inconsistent ✓ |
| `inconsistent_two_system_types.ttl` | DR1 also typed `SprinklerIrrigation` | disjoint classes violated | inconsistent ✓ |
| `owa_closure.ttl` | FieldE `hasSensor max 0 SoilMoistureSensor` | FieldE becomes `UnmonitoredField` | ✓ |

*[Screenshot 4: Protégé inconsistency explanation for the DR1 test]*

## 6. Open-world assumption

Field E has no sensor on record. The SPARQL query CQ8 (closed-world, `FILTER NOT EXISTS`) lists FieldE, but the
reasoner does **not** classify FieldE as `UnmonitoredField`. Under the open-world assumption a missing fact is
unknown, not false: a sensor may exist that has not been recorded. Only after the explicit closure axiom in
`tests/owa_closure.ttl` does HermiT infer `UnmonitoredField`. For the same reason the reasoner never concludes that
a field *does not* need attention. It can only say that it is not known to need it.

## 7. Limitations

- **No "latest reading" in OWL.** A field stays flagged if *any* reading is low. Recency is handled outside OWL
  (SPARQL `MAX(?t)`, CQ5). A real system would keep only the current reading or use rules or SPARQL.
- **Fixed thresholds** (30 % / 18 %) ignore soil texture (sandy soils hold less water). Per-crop or per-soil
  thresholds would need SWRL or application logic.
- **No deployment history:** `installedIn` is functional and current-only. Moving a sensor would mis-attribute old
  readings. SSN's `ssn:Deployment` pattern would fix this.
- Weather data is recorded but not yet used in classification (e.g. suppress irrigation after rain).
- Small, fictional dataset: it demonstrates the reasoning, not agronomic validity.

## 8. Reproducibility

See `README.md`: open `ontology/smartfarm.ttl` in Protégé → HermiT → Start reasoner; or run
`python scripts/reason.py --tests` (exit code 0 = all expectations met).

## References and acknowledgement

- Haller, A., Janowicz, K., Cox, S. et al. *Semantic Sensor Network Ontology (SSN/SOSA)*, W3C Recommendation, 2017.
  https://www.w3.org/TR/vocab-ssn/ (ontology file reused unchanged, W3C licence).
- Protégé, Stanford University, https://protege.stanford.edu/ · HermiT OWL Reasoner, University of Oxford.
- Lamy, J.-B. *Owlready2*, https://owlready2.readthedocs.io/ · rdflib, https://rdflib.readthedocs.io/
- AI assistance: Claude (Anthropic) was used to draft the ontology, test script and this report. I verified the
  results in Protégé and can explain and modify every axiom. *(Edit this line to describe your own use honestly.)*
