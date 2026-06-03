from __future__ import annotations

from typing import Any

from app.graph.planner_dispute_signals import model_intent_has_current_trigger as _model_intent_has_current_trigger
from app.graph.planner_intent_meta import (
    dedupe_intents as _dedupe_intents,
    known_info_from_state as _known_info_from_state,
    merge_intent_details as _merge_intent_details,
    missing_info_from_state as _missing_info_from_state,
    must_ask_for_intent as _must_ask_for_intent,
    reply_goal_for_intent as _reply_goal_for_intent,
)
from app.graph.planner_intent_filter import filter_spurious_intents
from app.graph.planner_prompt import planner_messages_for_model, planner_model_tier, should_use_model_planner
from app.graph.planner_rule_intents import detect_intents
from app.graph.planner_store_followup import contextual_followup_intents as _contextual_followup_intents
from app.graph.planner_tool_plan import (
    default_tool_plan as _default_tool_plan,
    needs_default_tool_plan as _needs_default_tool_plan,
    normalize_tool_plan_for_intent as _normalize_tool_plan_for_intent,
)
from app.graph.planner_validation import validated_planner_intents
from app.graph.state import AgentState


def merge_intents(state: AgentState, rule_items: list[dict[str, Any]], model_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    positions: dict[str, int] = {}

    def add(item: dict[str, Any]) -> None:
        intent = str(item.get("intent") or "")
        if not intent:
            return
        if intent in seen:
            existing = merged[positions[intent]]
            merged[positions[intent]] = _merge_intent_details(existing, item)
            return
        seen.add(intent)
        positions[intent] = len(merged)
        merged.append(item)

    for item in rule_items:
        add(item)
    for item in model_items:
        intent = str(item.get("intent") or "")
        if intent in seen or _model_intent_has_current_trigger(state, intent):
            add(item)
    for item in _contextual_followup_intents(state):
        add(item)
    return merged[:3] or _dedupe_intents(rule_items + model_items)


def enrich_intents_with_tool_plan(state: AgentState, intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for item in intents:
        copied = dict(item)
        copied.setdefault("known_info", _known_info_from_state(state, copied))
        copied.setdefault("missing_info", _missing_info_from_state(state, copied))
        copied.setdefault("reply_goal", _reply_goal_for_intent(copied))
        copied.setdefault("should_ask", bool(copied.get("missing_info")) and _must_ask_for_intent(copied))
        if _needs_default_tool_plan(copied.get("skill", ""), copied.get("tool_plan")):
            copied["tool_plan"] = _default_tool_plan(state, copied)
        copied["tool_plan"] = _normalize_tool_plan_for_intent(state, copied)
        enriched.append(copied)
    return enriched
