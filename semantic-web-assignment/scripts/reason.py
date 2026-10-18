"""Run the HermiT reasoner over the Smart Farming ontology and answer the competency questions.

Usage (from the semantic-web-assignment folder):

    python scripts/reason.py                         # base model: consistency, inferences, CQs
    python scripts/reason.py --extra tests/owa_closure.ttl
    python scripts/reason.py --tests                 # base model + every file in tests/

owlready2 bundles HermiT (needs Java on PATH). rdflib merges the Turtle files first, so the
SOSA import is resolved from ontology/sosa.ttl and nothing is fetched from the web.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import owlready2
import rdflib
from rdflib.namespace import OWL

ROOT = Path(__file__).resolve().parent.parent
ONTOLOGY = ROOT / "ontology"
OUTPUT = ROOT / "output"
TESTS = ROOT / "tests"
SF = "http://example.org/smartfarm#"

PREFIXES = """
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
PREFIX sosa: <http://www.w3.org/ns/sosa/>
PREFIX sf:   <http://example.org/smartfarm#>
"""

COMPETENCY_QUESTIONS = {
    "CQ1 Which fields need irrigation attention, and from which reading?": """
        SELECT DISTINCT ?field ?crop ?moisture WHERE {
          ?field a sf:FieldNeedingAttention ; sf:grows ?crop .
          ?obs sosa:hasFeatureOfInterest ?field ; sf:soilMoisturePercent ?moisture .
        } ORDER BY ?field""",
    "CQ2 Which crop is grown in each field, on which soil type?": """
        SELECT DISTINCT ?field ?crop ?soil WHERE {
          ?field a sf:Field ; sf:grows ?crop ; sf:hasSoilType ?soil .
        } ORDER BY ?field""",
    "CQ3 Which sensor made each soil-moisture reading, and which field does it describe?": """
        SELECT DISTINCT ?obs ?sensor ?field WHERE {
          ?obs a sf:SoilMoistureObservation ; sosa:madeBySensor ?sensor .
          OPTIONAL { ?obs sosa:hasFeatureOfInterest ?field }
        } ORDER BY ?obs""",
    "CQ4 Which fields use water-efficient (drip) irrigation?": """
        SELECT DISTINCT ?field ?system WHERE {
          ?field a sf:WaterEfficientField ; sf:hasIrrigationSystem ?system .
          ?system a sf:DripIrrigation .
        } ORDER BY ?field""",
    "CQ5 What is the latest soil-moisture reading for each field?": """
        SELECT DISTINCT ?field (MAX(?t) AS ?latest) WHERE {
          ?obs a sf:SoilMoistureObservation ; sosa:hasFeatureOfInterest ?field ; sosa:resultTime ?t .
        } GROUP BY ?field ORDER BY ?field""",
    "CQ6 Which irrigation events were applied, with which system and how much water?": """
        SELECT DISTINCT ?event ?field ?system ?litres WHERE {
          ?event a sf:IrrigationEvent ; sf:irrigates ?field ;
                 sosa:madeByActuator ?system ; sf:waterVolumeLitres ?litres .
        }""",
    "CQ7 Who manages the farm each field belongs to?": """
        SELECT DISTINCT ?field ?farm ?manager WHERE {
          ?field sf:partOfFarm ?farm . ?farm sf:managedBy ?manager .
        } ORDER BY ?field""",
    "CQ8 Which fields have no soil-moisture sensor on record (closed-world SPARQL check)?": """
        SELECT DISTINCT ?field WHERE {
          ?field a sf:Field .
          FILTER NOT EXISTS { ?field sf:hasSensor ?s . ?s a sf:SoilMoistureSensor }
        } ORDER BY ?field""",
}

# Classes whose members are reported before/after reasoning.
WATCHED_CLASSES = [
    "LowMoistureObservation",
    "CriticallyDryObservation",
    "FieldNeedingAttention",
    "HighWaterDemandField",
    "WaterEfficientField",
    "MonitoredField",
    "UnmonitoredField",
]


def short(term) -> str:
    text = str(term)
    for ns, prefix in ((SF, "sf:"), ("http://www.w3.org/ns/sosa/", "sosa:")):
        if text.startswith(ns):
            return prefix + text[len(ns) :]
    return text


def merged_graph(extra: list[Path]) -> rdflib.Graph:
    """Merge SOSA, the case ontology and any extra test files; drop owl:imports."""
    graph = rdflib.Graph()
    for path in [ONTOLOGY / "sosa.ttl", ONTOLOGY / "smartfarm.ttl", *extra]:
        graph.parse(path, format="turtle")
    graph.remove((None, OWL.imports, None))
    return graph


def load_world(graph: rdflib.Graph, tmpdir: Path) -> tuple[owlready2.World, owlready2.Ontology]:
    rdfxml = tmpdir / "merged.owl"
    graph.serialize(rdfxml, format="xml")
    world = owlready2.World()
    onto = world.get_ontology(rdfxml.as_uri()).load()
    return world, onto


def members(world: owlready2.World, name: str) -> set[str]:
    cls = world[SF + name]
    return {short(i.iri) for i in cls.instances()} if cls is not None else set()


def class_members(world: owlready2.World) -> dict[str, set[str]]:
    return {name: members(world, name) for name in WATCHED_CLASSES}


def feature_of_interest_links(world: owlready2.World) -> set[tuple[str, str]]:
    graph = world.as_rdflib_graph()
    foi = rdflib.URIRef("http://www.w3.org/ns/sosa/hasFeatureOfInterest")
    return {(short(s), short(o)) for s, o in graph.subject_objects(foi)}


def subclass_pairs(world: owlready2.World) -> set[tuple[str, str]]:
    pairs = set()
    for cls in world.classes():
        if not cls.iri.startswith(SF):
            continue
        for parent in cls.is_a:
            if isinstance(parent, owlready2.ThingClass):
                pairs.add((short(cls.iri), short(parent.iri)))
    return pairs


def run(extra: list[Path], write_outputs: bool) -> bool:
    """Reason over the model; return True when it is consistent."""
    label = ", ".join(p.name for p in extra) or "base model"
    print(f"\n=== {label} ===")
    with tempfile.TemporaryDirectory() as tmp:
        graph = merged_graph(extra)
        print(f"Asserted triples (SOSA + case + extras): {len(graph)}")
        world, onto = load_world(graph, Path(tmp))

        before_members = class_members(world)
        before_foi = feature_of_interest_links(world)
        before_sub = subclass_pairs(world)

        try:
            with onto:
                owlready2.sync_reasoner_hermit(world, infer_property_values=True, debug=0)
        except owlready2.OwlReadyInconsistentOntologyError:
            print("Consistency: INCONSISTENT (HermiT found a contradiction)")
            return False
        print("Consistency: consistent (HermiT)")

        print("\nInferred class memberships (new after reasoning):")
        for name, now in class_members(world).items():
            new = sorted(now - before_members[name])
            print(f"  sf:{name:<26} {', '.join(new) if new else '-'}")

        print("\nInferred sosa:hasFeatureOfInterest links (property chain):")
        for obs, field in sorted(feature_of_interest_links(world) - before_foi):
            print(f"  {obs} -> {field}")

        print("\nInferred subclass axioms:")
        for child, parent in sorted(subclass_pairs(world) - before_sub):
            print(f"  {child} rdfs:subClassOf {parent}")

        inferred = world.as_rdflib_graph()
        print("\nCompetency questions (SPARQL over asserted + inferred graph):")
        for question, query in COMPETENCY_QUESTIONS.items():
            rows = list(inferred.query(PREFIXES + query))
            print(f"\n  {question}")
            for row in rows:
                print("    " + " | ".join(short(v) if v is not None else "-" for v in row))
            if not rows:
                print("    (no answers)")

        if write_outputs:
            OUTPUT.mkdir(exist_ok=True)
            graph.serialize(OUTPUT / "smartfarm-merged.owl", format="xml")
            out = rdflib.Graph()
            for prefix, ns in (("sf", SF), ("sosa", "http://www.w3.org/ns/sosa/")):
                out.bind(prefix, ns)
            for triple in inferred:
                out.add(triple)
            out.serialize(OUTPUT / "smartfarm-inferred.ttl", format="turtle")
            print("\nWrote output/smartfarm-merged.owl and output/smartfarm-inferred.ttl")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--extra", type=Path, action="append", default=[], help="extra Turtle file")
    parser.add_argument("--tests", action="store_true", help="also run every file in tests/")
    args = parser.parse_args()

    ok = run(args.extra, write_outputs=not args.extra)
    if args.tests:
        for path in sorted(TESTS.glob("*.ttl")):
            expect_inconsistent = path.name.startswith("inconsistent_")
            consistent = run([path], write_outputs=False)
            verdict = "as expected" if consistent != expect_inconsistent else "UNEXPECTED"
            print(f"--> {path.name}: {'consistent' if consistent else 'inconsistent'} ({verdict})")
            ok = ok and consistent != expect_inconsistent
    return 0 if ok else 1


if __name__ == "__main__":
    # HermiT runs in a JVM; give it room for SOSA + the case data.
    owlready2.reasoning.JAVA_MEMORY = 1000
    sys.exit(main())
