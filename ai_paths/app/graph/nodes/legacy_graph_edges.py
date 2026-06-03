from __future__ import annotations

from typing import Any

from langgraph.graph import END, START


LEGACY_NODE_ORDER = (
    "normalize_input",
    "image_understanding",
    "hard_guardrails",
    "load_memory",
    "load_customer_context",
    "planner_brain",
    "execute_actions",
    "synthesize_reply",
    "profile_event_extractor",
)


LEGACY_EDGE_ORDER = (
    (START, "normalize_input"),
    ("normalize_input", "image_understanding"),
    ("image_understanding", "hard_guardrails"),
    ("hard_guardrails", "load_memory"),
    ("load_memory", "load_customer_context"),
    ("load_customer_context", "planner_brain"),
    ("planner_brain", "execute_actions"),
    ("execute_actions", "synthesize_reply"),
    ("synthesize_reply", "profile_event_extractor"),
    ("profile_event_extractor", END),
)


def add_legacy_nodes_and_edges(graph: Any, nodes: dict[str, Any]) -> None:
    for name in LEGACY_NODE_ORDER:
        graph.add_node(name, nodes[name])

    for from_node, to_node in LEGACY_EDGE_ORDER:
        graph.add_edge(from_node, to_node)
