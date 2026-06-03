from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.state import AgentState


@dataclass(frozen=True)
class ActionCallbacks:
    appointment_query_from_state: Callable[[str, dict[str, Any], AgentState], dict[str, Any]]
    canonical_price_project: Callable[[str], str]
    contextual_price_project: Callable[[AgentState], str]
    extract_project: Callable[[str], str]
    has_appointment_change_or_cancel: Callable[[str], bool]
    has_appointment_record_query: Callable[[str], bool]
    has_store_inquiry: Callable[[str], bool]
    is_broad_price_category: Callable[[str], bool]
    json_dumps: Callable[[Any], str]
    merge_kb_result: Callable[[dict[str, Any], str, dict[str, Any]], None]
    needs_project_price_followup: Callable[[list[dict[str, Any]], dict[str, Any], AgentState], bool]
    planned_kb_searches: Callable[[dict[str, Any], AgentState | None], list[dict[str, str]]]
    pricing_sql_from_state: Callable[[AgentState], str]
    project_price_followup_queries: Callable[[dict[str, Any]], list[str]]
    safe_query_from_state: Callable[[AgentState, Any], str]
    should_drop_planner_notes_for_skill_output: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], bool]
    should_suspend_active_task: Callable[[AgentState, dict[str, Any], list[dict[str, Any]]], bool]
    skill_output: Callable[[str, str, dict[str, Any], AgentState], dict[str, Any]]
    store_query_from_state: Callable[[str, AgentState], str]
    with_action_planning_notes: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
