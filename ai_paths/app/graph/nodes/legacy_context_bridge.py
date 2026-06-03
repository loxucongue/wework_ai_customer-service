from __future__ import annotations

from app.graph.nodes.common import dedupe_strings
from app.graph.nodes.image_info import (
    has_actual_image_context,
    has_image_concern,
    known_visible_concerns_from_state,
)
from app.graph.nodes.intent_signals import recent_conversation_text
from app.graph.nodes.legacy_context_guidance import (
    LegacyContextGuidanceCallbacks,
    context_guidance_inline as context_guidance_inline_from_module,
    image_guidance_inline as image_guidance_inline_from_module,
    memory_context_sentence as memory_context_sentence_from_module,
    project_context_source as project_context_source_from_module,
    project_guidance_inline as project_guidance_inline_from_module,
    sanitize_project_direction as sanitize_project_direction_from_module,
)
from app.graph.nodes.legacy_project_context import (
    LegacyProjectContextCallbacks,
    contextual_price_project as contextual_price_project_from_module,
    project_direction_names_from_state as project_direction_names_from_module,
    recent_project_from_state as recent_project_from_module,
)
from app.graph.nodes.pricing_context import canonical_price_project, extract_project, pricing_sql_for_project
from app.graph.nodes.project_kb_context import (
    business_project_slices as business_project_slices_from_module,
    project_direction_name_candidates,
    project_slices_from_tool_results,
)
from app.graph.state import AgentState


def business_project_slices(
    project_slices: list[dict[str, str]], state: AgentState | None = None
) -> list[dict[str, str]]:
    return business_project_slices_from_module(
        project_slices,
        state,
        known_visible_concerns_from_state=known_visible_concerns_from_state,
    )


def context_guidance_callbacks() -> LegacyContextGuidanceCallbacks:
    return LegacyContextGuidanceCallbacks(
        has_actual_image_context=has_actual_image_context,
        has_image_concern=has_image_concern,
        known_visible_concerns_from_state=known_visible_concerns_from_state,
    )


def project_guidance_inline(content: str, project: str) -> str:
    return project_guidance_inline_from_module(content, project)


def context_guidance_inline(state: AgentState, content: str, project: str) -> str:
    return context_guidance_inline_from_module(state, content, project, context_guidance_callbacks())


def image_guidance_inline(state: AgentState, project: str = "") -> str:
    return image_guidance_inline_from_module(state, project, context_guidance_callbacks())


def project_context_source(state: AgentState) -> str:
    return project_context_source_from_module(state, context_guidance_callbacks())


def sanitize_project_direction(direction: str, state: AgentState) -> str:
    return sanitize_project_direction_from_module(direction, state, context_guidance_callbacks())


def memory_context_sentence(state: AgentState) -> str:
    return memory_context_sentence_from_module(state)


def project_context_callbacks() -> LegacyProjectContextCallbacks:
    return LegacyProjectContextCallbacks(
        business_project_slices=business_project_slices,
        canonical_price_project=canonical_price_project,
        dedupe_strings=dedupe_strings,
        extract_project=extract_project,
        has_image_concern=has_image_concern,
        project_direction_name_candidates=project_direction_name_candidates,
        project_slices_from_tool_results=project_slices_from_tool_results,
        recent_conversation_text=recent_conversation_text,
    )


def recent_project_from_state(state: AgentState) -> str:
    return recent_project_from_module(state, project_context_callbacks())


def contextual_price_project(state: AgentState) -> str:
    return contextual_price_project_from_module(state, project_context_callbacks())


def project_direction_names_from_state(state: AgentState) -> list[str]:
    return project_direction_names_from_module(state, project_context_callbacks())


def pricing_sql_from_state(state: AgentState) -> str:
    return pricing_sql_for_project(contextual_price_project(state))
