from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.common import model_usage_snapshot
from app.graph.planner.runtime_plan import planner_handoff
from app.graph.state import AgentState
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


FINAL_REPLY_MODEL_NAMES = [
    "deepseek-v4-flash",
]
FINAL_REPLY_JSON_FORMAT = {"type": "json_object"}
FINAL_REPLY_TEMPERATURE = 0.25


def create_synthesize_reply_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    model_reply_unsafe: Callable[[AgentState, list[dict[str, Any]]], bool],
    postprocess_reply_messages: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]],
    reply_messages_for_model: Callable[[AgentState], list[dict[str, Any]]],
    reply_model_tier: Callable[[AgentState], str],
    should_use_model_reply: Callable[[AgentState], bool],
    validated_model_messages: Callable[[dict[str, Any]], list[dict[str, Any]]],
):
    async def synthesize_reply(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "synthesize_reply",
            {"fact_envelope": state.get("fact_envelope"), "required_tools": state.get("required_tools")},
        ) as span:
            model_call: dict[str, Any] | None = None
            errors = list(state.get("errors", []))
            messages: list[dict[str, Any]] = []
            reply_source = "main_model"
            pending_model_errors: list[dict[str, Any]] = []

            try:
                if not (model_client and model_client.available and should_use_model_reply(state)):
                    raise RuntimeError("reply_synthesizer_model_required")

                tier = reply_model_tier(state)
                model_call = {"name": "reply_synthesizer_model", "input": {"tier": tier, "required": True}}
                attempt_errors: list[str] = []
                for attempt in (1, 2):
                    try:
                        payload = await model_client.chat_json(
                            reply_messages_for_model(state),
                            tier=tier,
                            temperature=FINAL_REPLY_TEMPERATURE,
                            model_names=FINAL_REPLY_MODEL_NAMES,
                            response_format=FINAL_REPLY_JSON_FORMAT,
                        )
                        model_call["usage"] = model_usage_snapshot(model_client)
                        messages = validated_model_messages(payload)
                        model_call["draft_messages"] = debug_message_contents(messages)
                        if messages:
                            messages = postprocess_reply_messages(state, messages)
                            messages = _drop_duplicate_store_address_messages(state, messages)
                            messages = _ensure_book_order_for_created_appointment(state, messages)
                            model_call["postprocessed_messages"] = debug_message_contents(messages)
                        if messages and _store_no_match_reply_needs_fallback(state, messages):
                            messages, reply_source = _safe_visible_fallback_messages(state)
                            state["postprocess_changed"] = True
                            state["postprocess_reasons"] = _unique_reasons(
                                list(state.get("postprocess_reasons", [])) + ["store_no_match_fallback_forced"]
                            )
                            model_call["postprocessed_messages"] = debug_message_contents(messages)
                        if not _has_customer_visible_text(messages):
                            raise RuntimeError("reply_messages_empty_after_postprocess")
                        model_call["output"] = {"messages": len(messages), "attempt": attempt}
                        model_call["attempt_errors"] = list(attempt_errors)
                        break
                    except Exception as exc:
                        attempt_errors.append(f"attempt={attempt}: {type(exc).__name__}: {exc}")
                        if attempt == 2:
                            raise RuntimeError(" | ".join(attempt_errors)) from exc
                        continue
            except Exception as exc:
                model_call = model_call or {"name": "reply_synthesizer_model", "input": {}}
                primary_error = f"{type(exc).__name__}: {exc}"
                model_call["error"] = primary_error
                pending_model_errors.append(
                    {
                        "node": "synthesize_reply",
                        "message": "final_reply_model_failed",
                        "detail": primary_error,
                    }
                )
                messages = []

            if messages and model_reply_unsafe(state, messages):
                errors.append({"node": "synthesize_reply", "message": "final_reply_failed_quality_gate"})

            if not _has_customer_visible_text(messages):
                errors.extend(pending_model_errors)
                errors.append({"node": "synthesize_reply", "message": "customer_visible_reply_unavailable"})
                messages, reply_source = _safe_visible_fallback_messages(state)

            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            recovered_errors = []
            if pending_model_errors and reply_source == "model_failed_handoff":
                errors.extend(error for error in pending_model_errors if error not in errors)
            output = {
                "reply_messages": messages,
                "reply_source": reply_source,
                "postprocess_changed": bool(state.get("postprocess_changed")),
                "postprocess_reasons": state.get("postprocess_reasons", []),
                "errors": errors,
                "recovered_errors": recovered_errors,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return synthesize_reply


def _has_customer_visible_text(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        if not isinstance(message, dict):
            continue
        msg_type = str(message.get("type") or "").strip()
        if msg_type in {"image", "store_address", "book_order"}:
            return True
        if msg_type != "text":
            continue
        content = message.get("content")
        if isinstance(content, dict):
            text = str(content.get("text") or "").strip()
        else:
            text = str(content or "").strip()
        if text:
            return True
    return False


def _drop_duplicate_store_address_messages(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if _current_turn_explicitly_requests_address_resend(state):
        return messages
    sent_store_ids = _recent_sent_store_ids(state)
    if not sent_store_ids:
        return messages
    output: list[dict[str, Any]] = []
    changed = False
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "store_address":
            output.append(message)
            continue
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        store_id = str(content.get("store_id") or content.get("id") or "").strip()
        if store_id and store_id in sent_store_ids:
            changed = True
            continue
        output.append(message)
    if not changed:
        return messages
    state["postprocess_changed"] = True
    state["postprocess_reasons"] = _unique_reasons(
        list(state.get("postprocess_reasons", [])) + ["duplicate_store_address_dropped"]
    )
    for index, message in enumerate(output, start=1):
        if isinstance(message, dict):
            message["order"] = index
    return output


def _ensure_book_order_for_created_appointment(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order_id = _created_appointment_order_id(state)
    if not order_id:
        return messages
    if any(isinstance(message, dict) and message.get("type") == "book_order" for message in messages):
        return messages
    output = [message for message in messages if isinstance(message, dict)]
    output.append({"type": "book_order", "order": len(output) + 1, "content": {"order_id": order_id}})
    state["postprocess_changed"] = True
    state["postprocess_reasons"] = _unique_reasons(
        list(state.get("postprocess_reasons", [])) + ["created_appointment_book_order_added"]
    )
    return output


def _created_appointment_order_id(state: AgentState) -> str:
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    opening = tool_results.get("appointment_opening") if isinstance(tool_results.get("appointment_opening"), dict) else {}
    status = str(opening.get("status") or "").strip()
    order_id = str(opening.get("order_id") or "").strip()
    if status in {"created", "dry_run_created", "reused_open_order"} and order_id:
        return order_id
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    for fact in structured.get("appointment_facts") or []:
        if not isinstance(fact, dict) or fact.get("type") != "appointment_opening":
            continue
        status = str(fact.get("status") or "").strip()
        order_id = str(fact.get("order_id") or "").strip()
        if status in {"created", "dry_run_created", "reused_open_order"} and order_id:
            return order_id
    return ""


def _current_turn_explicitly_requests_address_resend(state: AgentState) -> bool:
    text = str(state.get("normalized_content") or state.get("content") or "").strip()
    if not text:
        return False
    explicit_terms = ("再发", "重新发", "发我", "发给我", "给我发", "发一下", "地址", "位置", "定位", "导航", "路线")
    return any(term in text for term in explicit_terms)


def _recent_sent_store_ids(state: AgentState) -> set[str]:
    result: set[str] = set()
    events = [item for item in state.get("history_events", []) if isinstance(item, dict)]
    for event in reversed(events[-20:]):
        if str(event.get("event_type") or "").strip() != "store_address_sent":
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        store_id = str(facts.get("store_id") or facts.get("id") or "").strip()
        if store_id:
            result.add(store_id)
    return result


def _store_no_match_reply_needs_fallback(state: AgentState, messages: list[dict[str, Any]]) -> bool:
    if not _no_matched_store_fallback_text(state):
        return False
    text_parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "text":
            continue
        content = message.get("content")
        if isinstance(content, dict):
            text = str(content.get("text") or "").strip()
        else:
            text = str(content or "").strip()
        if text:
            text_parts.append(text)
    if not text_parts:
        return True
    combined = "\n".join(text_parts)
    return not ("没查到" in combined and "门店" in combined)


def _unique_reasons(reasons: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        text = str(reason or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _safe_visible_fallback_messages(state: AgentState) -> tuple[list[dict[str, Any]], str]:
    case_messages = _case_request_fallback_messages(state)
    if case_messages:
        return case_messages, "deterministic_case_fallback"

    store_messages = _store_location_fallback_messages(state)
    if store_messages:
        return store_messages, "deterministic_store_fallback"

    handoff = planner_handoff(state)
    reason = str(handoff.get("reason") or "").strip() or "最终回复生成失败，转专业同事继续跟进"
    text = "这边帮您对接同事继续跟进，请您稍等一下。"
    return (
        [
            {"type": "text", "order": 1, "content": {"text": text}},
            {"type": "human_handoff", "order": 2, "content": {"handoff_reason": reason}},
        ],
        "model_failed_handoff",
    )


def _case_request_fallback_messages(state: AgentState) -> list[dict[str, Any]]:
    if not _looks_like_case_image_request(state):
        return []
    text = "可以，我先按同类方向给您核对真实效果参考。您主要想看斑点淡化还是肤色提亮方向？"
    messages: list[dict[str, Any]] = [{"type": "text", "order": 1, "content": {"text": text}}]
    for image_url in _case_image_urls_from_state(state)[:2]:
        messages.append({"type": "image", "order": len(messages) + 1, "content": image_url})
    if len(messages) == 1:
        messages[0]["content"] = {
            "text": "这轮我先不随便拿不匹配的图糊弄您，暂时没有可直接发的同类效果图。您主要想看斑点淡化还是肤色提亮方向？"
        }
    return messages


def _store_location_fallback_messages(state: AgentState) -> list[dict[str, Any]]:
    no_match_text = _no_matched_store_fallback_text(state)
    if no_match_text:
        return [{"type": "text", "order": 1, "content": {"text": no_match_text}}]

    if not _looks_like_generic_store_location_request(state):
        return []
    return [
        {
            "type": "text",
            "order": 1,
            "content": {"text": "您在哪个城市或哪个区？我按您位置给您找近一点的门店。"},
        }
    ]


def _no_matched_store_fallback_text(state: AgentState) -> str:
    status = _store_lookup_status_from_state(state)
    if not status:
        return ""
    if str(status.get("data_authority") or "").strip().lower() != "platform":
        return ""
    if bool(status.get("has_store_facts")) or _has_current_store_facts(state):
        return ""
    source = str(status.get("source") or "")
    no_match = "no_match" in source or bool(status.get("no_store_match_confirmed"))
    if not no_match:
        return ""
    city = str(status.get("city") or "").strip()
    area = str(status.get("area_or_landmark") or "").strip()
    fallback_location = area or city
    prefix = f"{fallback_location}这边" if fallback_location else "这边"
    if area:
        return f"{prefix}目前没查到可直接发您的门店。您有其他常去地点在哪个城市或哪个区？我按那个位置帮您匹配近一点的门店。"
    if city:
        return f"{prefix}目前没查到可直接发您的门店。您有其他常去地点在哪个城市或哪个区？我按那个位置帮您匹配近一点的门店。"
    return "这边目前没查到可直接发您的门店。您有其他常去地点在哪个城市或哪个区？我按那个位置帮您匹配近一点的门店。"


def _store_lookup_status_from_state(state: AgentState) -> dict[str, Any]:
    structured = state.get("structured_facts")
    if isinstance(structured, dict) and isinstance(structured.get("store_lookup_status"), dict):
        return structured["store_lookup_status"]
    fact_envelope = state.get("fact_envelope")
    if isinstance(fact_envelope, dict):
        structured = fact_envelope.get("structured_facts")
        if isinstance(structured, dict) and isinstance(structured.get("store_lookup_status"), dict):
            return structured["store_lookup_status"]
    tool_results = state.get("tool_results")
    lookup = tool_results.get("store_lookup") if isinstance(tool_results, dict) else {}
    if isinstance(lookup, dict):
        return {
            "city": str(lookup.get("city") or ""),
            "area_or_landmark": str(lookup.get("area_or_landmark") or ""),
            "source": str(lookup.get("source") or ""),
            "data_authority": str(lookup.get("data_authority") or ""),
            "has_store_facts": bool(lookup.get("stores")),
            "no_store_match_confirmed": bool(not lookup.get("stores") and not lookup.get("missing") and not lookup.get("platform_error")),
        }
    return {}


def _looks_like_case_image_request(state: AgentState) -> bool:
    text = str(state.get("normalized_content") or "")
    if any(
        term in text
        for term in (
            "效果图",
            "案例",
            "前后对比",
            "对比图",
            "做完效果",
            "客户做完",
            "有图吗",
            "有照片吗",
            "发图",
            "看看效果",
            "看一下效果",
        )
    ):
        return True
    return _planner_text_contains(state, ("case", "effect", "案例", "效果", "前后对比", "效果对比"))


def _looks_like_generic_store_location_request(state: AgentState) -> bool:
    text = str(state.get("normalized_content") or "").strip()
    if not text:
        return False
    if not any(term in text for term in ("门店", "店", "地址", "位置", "在哪里", "在哪")):
        return False
    if any(term in text for term in ("我在", "我住", "附近", "机场", "高铁", "地铁")):
        return False
    if _has_current_store_facts(state):
        return False
    return True


def _planner_text_contains(state: AgentState, terms: tuple[str, ...]) -> bool:
    joined_parts: list[str] = []
    plan = state.get("planner_plan")
    if isinstance(plan, dict):
        joined_parts.append(str(plan))
    for key in ("scene", "intent", "subflow"):
        joined_parts.append(str(state.get(key) or ""))
    joined = " ".join(joined_parts).lower()
    return any(term.lower() in joined for term in terms)


def _has_current_store_facts(state: AgentState) -> bool:
    structured = state.get("structured_facts")
    if isinstance(structured, dict):
        if isinstance(structured.get("recommended_store"), dict):
            return True
        stores = structured.get("store_facts")
        if isinstance(stores, list) and any(isinstance(item, dict) for item in stores):
            return True
    tool_results = state.get("tool_results")
    lookup = tool_results.get("store_lookup") if isinstance(tool_results, dict) else {}
    if isinstance(lookup, dict):
        stores = lookup.get("stores")
        if isinstance(stores, list) and any(isinstance(item, dict) for item in stores):
            return True
    return False


def _case_image_urls_from_state(state: AgentState) -> list[str]:
    urls: list[str] = []
    for case in _case_facts_from_state(state):
        if not isinstance(case, dict):
            continue
        image_url = str(case.get("image_url") or case.get("url") or "").strip()
        if _is_usable_case_image_url(image_url):
            urls.append(image_url)
    return list(dict.fromkeys(urls))


def _case_facts_from_state(state: AgentState) -> list[dict[str, Any]]:
    structured = state.get("structured_facts")
    if isinstance(structured, dict):
        for key in ("case_facts", "case_studies"):
            value = structured.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    fact_envelope = state.get("fact_envelope")
    if isinstance(fact_envelope, dict):
        value = fact_envelope.get("case_facts")
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    tool_results = state.get("tool_results")
    value = tool_results.get("case_studies") if isinstance(tool_results, dict) else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        items = value.get("items") or value.get("cases") or value.get("results")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _is_usable_case_image_url(image_url: str) -> bool:
    if not image_url or not image_url.startswith(("http://", "https://")):
        return False
    lowered = image_url.lower()
    blocked_hosts = ("example.com", "example.cn", "localhost", "127.0.0.1", "picsum.photos", "placehold.co")
    return not any(host in lowered for host in blocked_hosts)
