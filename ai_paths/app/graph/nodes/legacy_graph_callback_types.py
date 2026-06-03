from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.state import AgentState


@dataclass(frozen=True)
class LegacyGraphWiringCallbacks:
    available_slot_list: Callable[[Any], list[str]]
    canonical_price_project: Callable[[str], str]
    compact_memory: Callable[..., Any]
    contextual_price_project: Callable[[AgentState], str]
    debug_message_contents: Callable[..., Any]
    extract_city: Callable[[str], str]
    extract_price_digits: Callable[[str], list[str]]
    extract_project: Callable[[str], str]
    forced_reply_satisfies_hard_instruction: Callable[..., bool]
    has_appointment_change_or_cancel: Callable[[str], bool]
    has_appointment_record_query: Callable[[str], bool]
    has_store_inquiry: Callable[[str], bool]
    is_broad_price_category: Callable[[str], bool]
    json_dumps: Callable[..., str]
    known_visible_concerns: Callable[[AgentState], list[str]]
    merge_kb_result: Callable[..., Any]
    model_reply_unsafe: Callable[..., bool]
    needs_project_price_followup: Callable[[AgentState], bool]
    planned_kb_searches: Callable[[AgentState], list[dict[str, Any]]]
    postprocess_reply_messages: Callable[..., Any]
    pricing_sql_from_state: Callable[[AgentState], str]
    project_direction_names: Callable[[AgentState], list[str]]
    project_price_followup_queries: Callable[[AgentState], list[str]]
    recent_assistant_replies: Callable[..., list[str]]
    reply_brief: Callable[[AgentState], dict[str, Any]]
    reply_model_tier: Callable[..., str]
    should_drop_planner_notes_for_skill_output: Callable[[AgentState, str], bool]
    should_suspend_active_task: Callable[[AgentState], bool]
    should_use_model_reply: Callable[[AgentState], bool]
    skill_output: Callable[[AgentState, str], dict[str, Any]]
    store_query_from_state: Callable[[AgentState], str]
    validated_model_messages: Callable[..., Any]
    with_action_planning_notes: Callable[[AgentState, dict[str, Any], str], dict[str, Any]]
    without_appointment_intents: Callable[[AgentState], AgentState]
