from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.graph.nodes.common import clean_model_value, json_dumps, model_usage_snapshot
from app.graph.nodes.conversation_history_fetch import fetch_platform_conversation_history
from app.graph.nodes.memory_usage_policy import order_session_state
from app.graph.state import AgentState
from app.prompts.profile_analyzer import build_profile_analyzer_messages
from app.services.memory_store import CustomerMemoryStore
from app.services.customer_payment_state import is_paid_deposit_state, resolved_payment_fact
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


def create_profile_event_extractor_node(
    *,
    trace_logger: TraceLogger,
    memory_store: CustomerMemoryStore | None,
    model_client: ModelClient | None = None,
    compact_memory: Callable[[dict[str, Any]], dict[str, Any]],
    conversation_fetcher: Callable[..., Awaitable[dict[str, Any]]] | None = None,
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
            if state.get("test_isolated") or not _memory_persistence_allowed(state):
                output = {
                    "profile_update": {},
                    "event_updates": [],
                    "saved_memory": {},
                    "memory_error": None,
                    "profile_extraction_skipped": "test_isolated" if state.get("test_isolated") else "memory_persist_not_allowed",
                    "trace": state.get("trace", []),
                }
                span["output_snapshot"] = output
                return output
            profile_update: dict[str, Any] = {}
            event_updates: list[dict[str, Any]] = []
            llm_profile_call: dict[str, Any] | None = None
            conversation_history, conversation_fetch = await _profile_conversation_history(state, conversation_fetcher)
            if model_client and model_client.available:
                llm_profile_call = {
                    "name": "profile_analyzer_model",
                    "input": {"tier": "fast", "conversation_fetch": conversation_fetch},
                }
                try:
                    profile_messages = _profile_messages_for_model(
                        state,
                        conversation_history=conversation_history,
                    )
                    llm_profile_call["input"]["messages"] = profile_messages
                    llm_update = await _profile_update_from_model(model_client, messages=profile_messages)
                    llm_profile_call["usage"] = model_usage_snapshot(model_client)
                    llm_profile_call["raw_json_output"] = llm_update
                    llm_profile_call["output"] = clean_model_value(llm_update, max_string_chars=600)
                    profile_update = _normalize_profile_update(llm_update.get("profile_update", {}))
                    event_updates = _normalize_llm_events(state, llm_update.get("event_updates", []))
                except Exception as exc:
                    llm_profile_call["error"] = f"{type(exc).__name__}: {exc}"
            deterministic_update, deterministic_events = _deterministic_customer_state_updates(state, profile_update)
            profile_update = _merge_profile_updates(profile_update, deterministic_update)
            event_updates = [*event_updates, *deterministic_events]
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
                "profile_conversation_fetch": conversation_fetch,
                "trace": state.get("trace", []),
            }
            if llm_profile_call:
                span["entry"]["tool_calls"] = [llm_profile_call]
            span["output_snapshot"] = output
            return output

    return profile_event_extractor


def _memory_persistence_allowed(state: AgentState) -> bool:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    return bool(request_context.get("memory_persist_allowed"))


def _profile_messages_for_model(
    state: AgentState,
    *,
    conversation_history: list[str] | None = None,
) -> list[dict[str, Any]]:
    payload = {
        "content": state.get("normalized_content"),
        "conversation_history": conversation_history if conversation_history is not None else state.get("conversation_history", [])[-50:],
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
    return build_profile_analyzer_messages(payload, json_dumps=json_dumps)


async def _profile_update_from_model(
    model_client: ModelClient,
    *,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    result = await model_client.chat_json(
        messages,
        tier="fast",
        temperature=0.2,
    )
    return result if isinstance(result, dict) else {}


async def _profile_conversation_history(
    state: AgentState,
    conversation_fetcher: Callable[..., Awaitable[dict[str, Any]]] | None,
) -> tuple[list[str], dict[str, Any]]:
    return await fetch_platform_conversation_history(
        state,
        conversation_fetcher,
        limit=50,
        fallback_limit=50,
    )


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


def _deterministic_customer_state_updates(
    state: AgentState,
    model_update: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    orders = customer_context.get("orders") if isinstance(customer_context.get("orders"), list) else []
    existing_basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    stored = existing_basic.get("deposit_state")
    if isinstance(stored, dict):
        existing_state = str(stored.get("status") or stored.get("deposit_state") or "")
        existing_source = str(stored.get("source") or "")
    else:
        existing_state = str(stored or "")
        existing_source = "customer_memory" if existing_state else ""
    payment = resolved_payment_fact(
        orders=orders,
        image_info=state.get("image_info"),
        existing_state=existing_state,
        existing_source=existing_source,
        existing_fact=stored,
    )
    existing_order_state = existing_basic.get("order_state") if isinstance(existing_basic.get("order_state"), dict) else {}
    if payment and not payment.get("order_id") and existing_order_state.get("order_id"):
        payment["order_id"] = existing_order_state.get("order_id")
        payment["order_no"] = existing_order_state.get("order_no")
        payment["store_id"] = existing_order_state.get("store_id")
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    order_result = tool_results.get("create_work_order") if isinstance(tool_results.get("create_work_order"), dict) else {}
    mobile_result = tool_results.get("add_customer_mobile") if isinstance(tool_results.get("add_customer_mobile"), dict) else {}
    plan_result = tool_results.get("create_order_plan") if isinstance(tool_results.get("create_order_plan"), dict) else {}
    model_basic = model_update.get("basic_info") if isinstance(model_update.get("basic_info"), dict) else {}
    customer_name = str(model_basic.get("customer_name") or existing_basic.get("customer_name") or "").strip()
    phone = str(model_basic.get("phone") or existing_basic.get("phone") or "").strip()

    basic_info: dict[str, Any] = {}
    portrait: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    if payment:
        basic_info["deposit_state"] = {
            "status": payment.get("deposit_state"),
            "source": payment.get("source"),
            "amount": payment.get("amount") or payment.get("prepay_paid"),
            "order_id": payment.get("order_id"),
            "order_no": payment.get("order_no"),
            "updated_at": payment.get("updated_at"),
        }
        if is_paid_deposit_state(payment.get("deposit_state")):
            portrait["deposit_state"] = "deposit_paid"
        if is_paid_deposit_state(payment.get("deposit_state")) and not is_paid_deposit_state(existing_state):
            events.append(
                _state_event(
                    state,
                    event_type="deposit_payment_confirmed",
                    summary="预约金支付状态已确认",
                    facts={
                        "deposit_state": payment.get("deposit_state"),
                        "source": payment.get("source"),
                        "amount": payment.get("amount") or payment.get("prepay_paid"),
                        "order_id": payment.get("order_id"),
                    },
                )
            )

    if order_result:
        basic_info["order_state"] = {
            "status": order_result.get("status"),
            "order_id": order_result.get("order_id"),
            "order_no": order_result.get("order_no"),
            "store_id": order_result.get("store_id"),
            "category_id": order_result.get("category_id"),
            "prepay_required": order_result.get("prepay_required"),
            "source": order_result.get("source"),
        }
    if customer_name or phone or mobile_result:
        basic_info["registration_state"] = {
            "customer_name_collected": bool(customer_name),
            "phone_collected": bool(phone),
            "mobile_sync_status": mobile_result.get("status") or "not_requested",
            "updated_at": payment.get("updated_at") if payment else "",
        }
    if plan_result:
        basic_info["appointment_state"] = {
            "status": "confirmed" if plan_result.get("status") in {"created", "reused"} else str(plan_result.get("status") or "unknown"),
            "order_id": plan_result.get("order_id"),
            "store_id": plan_result.get("store_id"),
            "store_name": plan_result.get("store_name"),
            "appointment_time": plan_result.get("appointment_time"),
            "source": plan_result.get("source"),
        }
        if plan_result.get("status") in {"created", "reused"}:
            events.append(
                _state_event(
                    state,
                    event_type="appointment_confirmed",
                    summary="客户到店排期已创建",
                    facts={
                        "order_id": plan_result.get("order_id"),
                        "store_id": plan_result.get("store_id"),
                        "store_name": plan_result.get("store_name"),
                        "appointment_time": plan_result.get("appointment_time"),
                        "source": plan_result.get("source"),
                    },
                )
            )
    update = {"basic_info": _drop_empty_mapping(basic_info), "portrait": _drop_empty_mapping(portrait)}
    return _drop_empty_mapping(update), events


def _merge_profile_updates(base: dict[str, Any], authoritative: dict[str, Any]) -> dict[str, Any]:
    output = dict(base) if isinstance(base, dict) else {}
    for section in ("portrait", "basic_info"):
        incoming = authoritative.get(section) if isinstance(authoritative.get(section), dict) else {}
        if incoming:
            current = output.get(section) if isinstance(output.get(section), dict) else {}
            output[section] = {**current, **incoming}
    if authoritative.get("lifecycle_stage"):
        output["lifecycle_stage"] = authoritative["lifecycle_stage"]
    return output


def _state_event(
    state: AgentState,
    *,
    event_type: str,
    summary: str,
    facts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_id": f"evt_{state.get('request_id', 'unknown')}_{event_type}",
        "event_time": "",
        "event_type": event_type,
        "stage": str(state.get("sop_stage") or ""),
        "summary": summary,
        "facts": _drop_empty_mapping(facts),
        "impact": "后续按结构化支付、订单和预约事实推进，不重复收款或编造预约结果。",
        "confidence": 1.0,
    }


def _drop_empty_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


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
        "order_state",
        "registration_state",
        "appointment_state",
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
