from __future__ import annotations

from typing import Any, Callable

from app.graph.message_cards import append_store_address_card
from app.graph.message_send_policy import suppress_repeated_action_messages
from app.graph.message_sanitizer import normalize_store_address_card_ids, sanitize_unsupported_placeholder_text
from app.graph.nodes.common import model_usage_snapshot
from app.graph.state import AgentState
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


def create_synthesize_reply_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    reply_messages_for_model: Callable[[AgentState], list[dict[str, Any]]],
    should_use_model_reply: Callable[[AgentState], bool],
    validated_model_messages: Callable[[dict[str, Any]], list[dict[str, Any]]],
    schedule_background_task: Callable[[AgentState], Any] | None = None,
):
    async def synthesize_reply(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "synthesize_reply",
            {"fact_envelope": state.get("fact_envelope"), "required_tools": state.get("required_tools")},
        ) as span:
            errors = list(state.get("errors", []))
            warnings = list(state.get("warnings", []))
            messages: list[dict[str, Any]] = []
            reply_source = "main_model"
            model_call: dict[str, Any] | None = None

            try:
                planner_decision = str(state.get("planner_decision") or "").strip()
                planner_messages = _normalize_planner_reply_messages(state.get("planner_reply_messages"))
                if planner_decision == "no_reply":
                    reply_source = "planner_no_reply"
                    model_call = {
                        "name": "planner_direct_reply",
                        "input": {"decision": planner_decision, "messages": 0},
                        "output": {"messages": 0},
                    }
                elif planner_decision == "direct_reply" and planner_messages:
                    messages = planner_messages
                    reply_source = "planner_direct_reply"
                    model_call = {
                        "name": "planner_direct_reply",
                        "input": {"decision": planner_decision, "messages": len(planner_messages)},
                        "output": {"messages": len(messages)},
                    }
                else:
                    if not (model_client and model_client.available and should_use_model_reply(state)):
                        raise RuntimeError("reply_synthesizer_model_required")
                    model_call = {"name": "reply_synthesizer_model", "input": {"tier": "reply", "required": True}}
                    payload = await model_client.chat_json(reply_messages_for_model(state), tier="reply")
                    model_call["usage"] = model_usage_snapshot(model_client)
                    messages = validated_model_messages(payload)
                    messages = _filter_unsupported_images(messages, state, warnings)
                    model_call["draft_messages"] = debug_message_contents(messages)
                    model_call["output"] = {"messages": len(messages)}
                messages = sanitize_unsupported_placeholder_text(messages, state, warnings)
                messages = append_store_address_card(messages, state)
                messages = normalize_store_address_card_ids(messages, state, warnings)
                messages = suppress_repeated_action_messages(messages, state)
            except Exception as exc:
                model_call = model_call or {"name": "reply_synthesizer_model", "input": {}}
                primary_error = f"{type(exc).__name__}: {exc}"
                model_call["error"] = primary_error
                errors.append(
                    {
                        "node": "synthesize_reply",
                        "message": "final_reply_failed",
                        "detail": primary_error,
                    }
                )
                messages = []

            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            output = {
                "reply_messages": messages,
                "reply_source": reply_source,
                "postprocess_changed": False,
                "postprocess_reasons": [],
                "errors": errors,
                "warnings": warnings,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            _schedule_profile_event_background(schedule_background_task, {**state, **output})
            return output

    return synthesize_reply


def _filter_unsupported_images(
    messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[Any],
) -> list[dict[str, Any]]:
    allowed_urls = _case_image_urls(state)
    filtered: list[dict[str, Any]] = []
    removed_urls: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "image":
            filtered.append(item)
            continue
        url = _message_url(item.get("content"))
        if url and url in allowed_urls:
            filtered.append(item)
        else:
            removed_urls.append(url or "")
    if removed_urls:
        warnings.append(
            {
                "node": "synthesize_reply",
                "message": "unsupported_image_removed",
                "detail": {"removed_urls": removed_urls},
            }
        )
    return _renumber(filtered)


def _case_image_urls(state: AgentState) -> set[str]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    urls: set[str] = set()
    for item in structured.get("case_facts") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("image_url") or "").strip()
        if url:
            urls.add(url)
    return urls


def _message_url(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("url") or content.get("image_url") or "").strip()
    return str(content or "").strip()


def _renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(messages, start=1):
        result.append({**item, "order": index})
    return result


def _normalize_planner_reply_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    messages: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        message_type = str(item.get("type") or "text").strip()
        content = item.get("content")
        if message_type == "text":
            if isinstance(content, dict):
                text = str(content.get("text") or "").strip()
            else:
                text = str(content or item.get("text") or "").strip()
            if text:
                messages.append({"type": "text", "order": int(item.get("order") or index), "content": {"text": text}})
            continue
        if message_type == "payment_collection":
            remark = str(content.get("remark") or "").strip() if isinstance(content, dict) else ""
            messages.append(
                {
                    "type": "payment_collection",
                    "order": int(item.get("order") or index),
                    "content": {"amount": 10, "remark": remark},
                }
            )
            continue
        if message_type == "human_handoff":
            reason = str(content.get("handoff_reason") if isinstance(content, dict) else content or "").strip()
            if reason:
                messages.append({"type": "human_handoff", "order": int(item.get("order") or index), "content": {"handoff_reason": reason}})
            continue
        if message_type == "store_address":
            store_id = str(content.get("store_id") if isinstance(content, dict) else content or "").strip()
            if store_id:
                messages.append({"type": "store_address", "order": int(item.get("order") or index), "content": {"store_id": store_id}})
    return messages


def _schedule_profile_event_background(
    schedule_background_task: Callable[[AgentState], Any] | None,
    state: AgentState,
) -> None:
    if not schedule_background_task:
        return
    try:
        schedule_background_task(state)
    except RuntimeError:
        return
