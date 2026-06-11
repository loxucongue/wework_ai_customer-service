from __future__ import annotations

from app.graph.nodes.common import dedupe_strings
from app.graph.nodes.image_info import has_image_concern
from app.graph.nodes.intent_signals import recent_conversation_text
from app.graph.nodes.project_kb_context import (
    business_project_slices,
    project_direction_name_candidates,
    project_slices_from_state,
)
from app.graph.nodes.pricing_context import canonical_price_project, extract_project, pricing_sql_for_project
from app.graph.planner.runtime_plan import planner_project_hints
from app.graph.state import AgentState


def recent_project_from_state(state: AgentState) -> str:
    text = recent_conversation_text(state)
    project = extract_project(text)
    return canonical_price_project(project)


def contextual_price_project(state: AgentState) -> str:
    content = str(state.get("normalized_content") or "")
    direct = canonical_price_project(extract_project(content))
    if direct:
        return direct

    for hint in planner_project_hints(state):
        value = canonical_price_project(hint)
        if value:
            return value

    return recent_project_from_state(state)


def project_direction_names_from_state(state: AgentState) -> list[str]:
    names: list[str] = []
    for candidate in project_direction_name_candidates(str(state.get("normalized_content") or "")):
        if candidate and candidate not in names:
            names.append(candidate)
    for slice_item in project_slices_from_state(state):
        title = str(slice_item.get("title") or "").strip()
        if title and title not in names:
            names.append(title)
    return dedupe_strings(names)[:8]


def pricing_sql_from_state(state: AgentState) -> str:
    return pricing_sql_for_project(contextual_price_project(state))
