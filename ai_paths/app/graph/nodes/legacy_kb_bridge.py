from __future__ import annotations

from typing import Any

from app.graph.nodes.action_queries import ActionQueryCallbacks, safe_query_from_state as safe_query_from_action_queries
from app.graph.nodes.common import dedupe_strings, looks_bad_text
from app.graph.nodes.image_info import has_image_concern
from app.graph.nodes.intent_signals import recent_conversation_text
from app.graph.nodes.kb_slice_parsing import pricing_rows_from_kb
from app.graph.nodes.legacy_kb_planning import (
    LegacyKbPlanningCallbacks,
    needs_project_price_followup as needs_project_price_followup_from_module,
    planned_kb_searches as planned_kb_searches_from_module,
    project_price_followup_queries as project_price_followup_queries_from_module,
)
from app.graph.nodes.legacy_project_context import (
    LegacyProjectContextCallbacks,
    contextual_price_project,
    recent_project_from_state,
)
from app.graph.nodes.legacy_tool_results import merge_kb_result as merge_kb_result_from_tool_results
from app.graph.nodes.pricing_context import (
    canonical_price_project,
    extract_project,
    is_broad_price_category,
)
from app.graph.nodes.project_kb_context import (
    business_project_slices,
    project_direction_name_candidates,
    project_slices_from_tool_results,
)
from app.graph.nodes.legacy_flow_utils import extract_price_digits
from app.graph.state import AgentState


def _legacy_project_context_callbacks() -> LegacyProjectContextCallbacks:
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


def _contextual_price_project(state: AgentState) -> str:
    return contextual_price_project(state, callbacks=_legacy_project_context_callbacks())


def _recent_project_from_state(state: AgentState) -> str:
    return recent_project_from_state(state, callbacks=_legacy_project_context_callbacks())


def safe_query_from_state(state: AgentState, skill: str) -> str:
    return safe_query_from_action_queries(
        state,
        skill,
        callbacks=ActionQueryCallbacks(
            canonical_price_project=canonical_price_project,
            contextual_price_project=_contextual_price_project,
            extract_price_digits=extract_price_digits,
            extract_project=extract_project,
        ),
    )


def _legacy_kb_planning_callbacks() -> LegacyKbPlanningCallbacks:
    return LegacyKbPlanningCallbacks(
        canonical_price_project=canonical_price_project,
        contextual_price_project=_contextual_price_project,
        dedupe_strings=dedupe_strings,
        extract_project=extract_project,
        is_broad_price_category=is_broad_price_category,
        looks_bad_text=looks_bad_text,
        pricing_rows_from_kb=pricing_rows_from_kb,
        project_direction_name_candidates=project_direction_name_candidates,
        project_slices_from_tool_results=project_slices_from_tool_results,
        recent_project_from_state=_recent_project_from_state,
        safe_query_from_state=safe_query_from_state,
    )


def planned_kb_searches(action: dict[str, Any], state: AgentState | None = None) -> list[dict[str, str]]:
    return planned_kb_searches_from_module(action, state, callbacks=_legacy_kb_planning_callbacks())


def needs_project_price_followup(
    actions: list[dict[str, Any]], tool_results: dict[str, Any], state: AgentState
) -> bool:
    return needs_project_price_followup_from_module(actions, tool_results, state, callbacks=_legacy_kb_planning_callbacks())


def project_price_followup_queries(tool_results: dict[str, Any]) -> list[str]:
    return project_price_followup_queries_from_module(tool_results, callbacks=_legacy_kb_planning_callbacks())


def merge_kb_result(tool_results: dict[str, Any], kb_name: str, dumped: dict[str, Any]) -> None:
    merge_kb_result_from_tool_results(tool_results, kb_name, dumped)

