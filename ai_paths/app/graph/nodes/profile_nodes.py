from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.common import clean_model_value, json_dumps, model_usage_snapshot
from app.graph.nodes.memory_usage_policy import order_session_state
from app.graph.state import AgentState
from app.prompts.profile_analyzer import build_profile_analyzer_messages
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


def create_profile_event_extractor_node(
    *,
    trace_logger: TraceLogger,
    memory_store: CustomerMemoryStore | None,
    model_client: ModelClient | None = None,
    compact_memory: Callable[[dict[str, Any]], dict[str, Any]],
) -> Callable[[AgentState], Any]:
    async def profile_event_extractor(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "profile_event_extractor",
            {
                "content": state.get("normalized_content"),
                "image_info": state.get("image_info"),
                "planner_decision": state.get("planner_decision"),
                "planner_stage": state.get("planner_stage"),
                "planner_sub_rule_id": state.get("planner_sub_rule_id"),
            },
        ) as span:
            if state.get("test_isolated"):
                output = {
                    "profile_update": {},
                    "event_updates": [],
                    "saved_memory": {},
                    "memory_error": None,
                    "profile_extraction_skipped": "test_isolated",
                    "trace": state.get("trace", []),
                }
                span["output_snapshot"] = output
                return output
            profile_update: dict[str, Any] = {}
            event_updates: list[dict[str, Any]] = []
            llm_profile_call: dict[str, Any] | None = None
            if model_client and model_client.available:
                llm_profile_call = {"name": "profile_analyzer_model", "input": {"tier": "fast"}}
                try:
                    llm_update = await _profile_update_from_model(state, model_client)
                    llm_profile_call["usage"] = model_usage_snapshot(model_client)
                    llm_profile_call["output"] = clean_model_value(llm_update, max_string_chars=600)
                    profile_update = _normalize_profile_update(llm_update.get("profile_update", {}))
                    event_updates = _normalize_llm_events(state, llm_update.get("event_updates", []))
                except Exception as exc:
                    llm_profile_call["error"] = f"{type(exc).__name__}: {exc}"
            memory_error = None
            saved_memory = {}
            if memory_store:
                try:
                    saved_memory = memory_store.save_update(
                        str(state.get("customer_id") or "unknown"),
                        profile_update=profile_update,
                        event_updates=event_updates,
                    )
                except Exception as exc:
                    memory_error = f"{type(exc).__name__}: {exc}"
            output = {
                "profile_update": profile_update,
                "event_updates": event_updates,
                "saved_memory": compact_memory(saved_memory),
                "memory_error": memory_error,
                "trace": state.get("trace", []),
            }
            if llm_profile_call:
                span["entry"]["tool_calls"] = [llm_profile_call]
            span["output_snapshot"] = output
            return output

    return profile_event_extractor


async def _profile_update_from_model(state: AgentState, model_client: ModelClient) -> dict[str, Any]:
    payload = {
        "content": state.get("normalized_content"),
        "conversation_history": state.get("conversation_history", [])[-8:],
        "reply_messages": state.get("reply_messages", []),
        "customer_profile": state.get("customer_profile", {}),
        "customer_basic_info": state.get("customer_basic_info", {}),
        "history_events": state.get("history_events", [])[-12:],
        "order_session": order_session_state(state),
        "planner_decision": state.get("planner_decision", ""),
        "planner_stage": state.get("planner_stage", ""),
        "planner_sub_rule_id": state.get("planner_sub_rule_id", ""),
        "fact_envelope": state.get("fact_envelope", {}),
        "tool_results": state.get("tool_results", {}),
    }
    result = await model_client.chat_json(
        build_profile_analyzer_messages(payload, json_dumps=json_dumps),
        tier="fast",
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return result if isinstance(result, dict) else {}


def _normalize_profile_update(incoming: Any) -> dict[str, Any]:
    if not isinstance(incoming, dict):
        return {}
    merged: dict[str, Any] = {}
    portrait = _allowed_portrait_update(incoming.get("portrait"))
    if portrait:
        merged["portrait"] = portrait
    basic_info = _allowed_basic_update(incoming.get("basic_info"))
    if basic_info:
        merged["basic_info"] = basic_info
    lifecycle = str(incoming.get("lifecycle_stage") or "").strip()
    if lifecycle:
        merged["lifecycle_stage"] = lifecycle[:40]
    return clean_model_value(merged, max_string_chars=500)


def _allowed_portrait_update(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "summary",
        "customer_type_tags",
        "decision_stage",
        "deposit_state",
        "main_objection",
        "next_sales_strategy",
        "intent_level",
        "trust_level",
        "concerns",
        "style_tags",
    }
    result: dict[str, Any] = {}
    for key in allowed_keys:
        item = value.get(key)
        if item in ("", None, [], {}):
            continue
        result[key] = item
    return result


def _allowed_basic_update(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "city",
        "area_or_landmark",
        "preferred_store_id",
        "preferred_store_name",
        "intent_date",
        "intent_time",
        "customer_name",
        "phone",
        "deposit_state",
    }
    result: dict[str, Any] = {}
    for key in allowed_keys:
        item = value.get(key)
        if item in ("", None, [], {}):
            continue
        result[key] = item
    return result


def _normalize_llm_events(state: AgentState, events: Any) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, event in enumerate(events[:4], start=1):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "customer_psychology_update").strip()
        summary = str(event.get("summary") or "").strip()
        if not summary:
            continue
        normalized.append(
            {
                "event_id": f"evt_{state.get('request_id', 'unknown')}_llm_profile_{index}",
                "event_time": "",
                "event_type": event_type[:80],
                "stage": str(state.get("sop_stage") or ""),
                "summary": summary[:240],
                "facts": clean_model_value(event.get("facts") if isinstance(event.get("facts"), dict) else {}, max_string_chars=240),
                "impact": str(event.get("impact") or "后续回复应参考客户心理画像和预约金状态推进。")[:240],
                "confidence": _event_confidence(event.get("confidence")),
            }
        )
    return normalized


def _event_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.72
    return max(0.0, min(confidence, 1.0))
