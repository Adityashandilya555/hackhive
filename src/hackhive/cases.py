"""What makes a plan a special case (#83): tag ids shared by the app and the batch CSV (#32).
Kept out of `ui` so a UI text change never changes the CSV."""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from hackhive import graph
from hackhive.contracts import Plan
from hackhive.io import NODE_CLEARANCE_M
from hackhive.solve import Result


@dataclass(frozen=True)
class CaseTag:
    id: str
    label: str
    look_for: str  # one line: what to look at in the drawing


def case_tags(plan: Plan, G: nx.Graph, result: Result) -> list[CaseTag]:
    """What makes this plan a special case, from the plan, its room graph and solve()'s result.
    The design tags (one-node ring, hub counts) appear only when there is a design."""
    tags: list[CaseTag] = []
    E = graph.eligible_graph(G)
    if any(kind == "opening" for *_, kind in G.edges(data="kind")):
        tags.append(CaseTag("open_passage", "open passage", "cable crosses a wall-less gap"))
    if any(G.degree(r) > 1 for r in graph.single_door_rooms(G) if r in E):
        tags.append(
            CaseTag(
                "en_suite",
                "en-suite single-door",
                "a room whose only other door is its own bathroom: retracing in is legal",
            )
        )
    sub, bounds = result.submission, result.sidecar.bounds
    if sub is not None and any(len(h.assigned_nodes) == 1 for h in sub.hubs):
        look = "hub → node → hub: out-and-back, or a thin two-lane loop when the node is far (#81)"
        tags.append(CaseTag("one_node_ring", "one-node ring", look))
    if sub is not None and bounds is not None and bounds.k_found is not None:
        extra = bounds.k_found - bounds.k_lb
        if extra == 0:
            tags.append(CaseTag("lb_met", "hubs = lower bound", "no zoning can use fewer hubs"))
        elif bounds.proof == "exhaustive":
            tags.append(
                CaseTag(
                    "above_lb",
                    f"hubs > lower bound (+{extra}, proven minimum)",
                    "door links force extra zones; every zoning was checked",
                )
            )
        if bounds.k_found >= 4:
            tags.append(CaseTag("many_hubs", "4+ hubs", "many rings share the doors"))
    if nx.number_connected_components(E) > 1:
        tags.append(
            CaseTag("disconnected", "disconnected groups", "rooms no door joins need own hubs")
        )
    if any(E.degree(r) == 0 for r in E):
        tags.append(
            CaseTag(
                "isolated",
                "isolated eligible room",
                "no door or open passage to another eligible room: it needs its own hub",
            )
        )
    if sum(r.type == "living" and r.eligible for r in plan.rooms) > 1:
        tags.append(CaseTag("multi_living", "multi-part living", "each living part has a node"))
    if any(n.clearance_m is not None and n.clearance_m < NODE_CLEARANCE_M for n in plan.nodes):
        tags.append(
            CaseTag(
                "low_clearance",
                f"node clearance < {NODE_CLEARANCE_M:g} m",
                "a node close to a wall",
            )
        )
    if plan.folded_parts:
        tags.append(CaseTag("folded", "folded sliver", "a living sliver merged into its neighbour"))
    return tags
