from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.activity_intro_image import activity_intro_image_url, append_activity_intro_image
from app.graph.nodes.common import model_usage_snapshot
from app.graph.nodes.reply_validation import validate_reply_consistency
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
                    model_messages = reply_messages_for_model(state)
                    payload = await model_client.chat_json(model_messages, tier="reply")
                    model_call["usage"] = model_usage_snapshot(model_client)
                    try:
                        messages = validated_model_messages(payload)
                        validate_reply_consistency(messages, state)
                    except Exception as validation_exc:
                        retry_messages = _reply_retry_messages(model_messages, validation_exc)
                        retry_payload = await model_client.chat_json(retry_messages, tier="reply")
                        model_call["retry"] = {
                            "reason": f"{type(validation_exc).__name__}: {validation_exc}",
                            "usage": model_usage_snapshot(model_client),
                        }
                        messages = validated_model_messages(retry_payload)
                        validate_reply_consistency(messages, state)
                    messages = _filter_unsupported_images(messages, state, warnings)
                    model_call["draft_messages"] = debug_message_contents(messages)
                    model_call["output"] = {"messages": len(messages)}
                messages = append_activity_intro_image(messages, state, warnings)
                for warning in warnings:
                    if isinstance(warning, dict) and warning.get("message") == "activity_intro_image_appended":
                        warning.setdefault("node", "synthesize_reply")
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


def _reply_retry_messages(messages: list[dict[str, Any]], exc: Exception) -> list[dict[str, Any]]:
    repair_hint = _reply_repair_hint(str(exc))
    retry_instruction = (
        "上一次输出没有通过 JSON schema 校验。"
        f"错误：{type(exc).__name__}: {exc}。"
        f"{repair_hint}"
        "请只重新输出严格 JSON 对象，顶层必须包含非空 reply_messages 数组；"
        "不要解释错误，不要输出 markdown，不要输出内部分析。"
    )
    return [*messages, {"role": "user", "content": retry_instruction}]


def _reply_repair_hint(error: str) -> str:
    if "payment_collection_required" in error:
        return "如果文本承诺发送预约金入口或 next_step=send_deposit，必须同时输出 payment_collection；否则删除发入口承诺并调整回复节奏。"
    if "case_context_must_not_use_activity_intro_image" in error:
        return "本轮客户在问效果或案例，且已有 case_facts 案例图片事实。必须回答效果顾虑，并且如输出 image，只能使用 case_facts 里的 image_url；不要输出活动宣传图。"
    if "reply_too_similar" in error:
        return "客户在重复追问同类问题，请换一个角度回答，不要复用上一轮核心话术。"
    if "two_text_required" in error:
        return "这条回复同时包含回答和下一步推进，请改成两条短 text：第一条只回答问题，第二条只轻推一个动作。"
    if "parking_fact_required" in error:
        return "没有停车工具事实时，不要说有停车场或可以停车，只能说需要核对或询问门店/区域。"
    if "business_hours_fact_required" in error:
        return "没有营业时间工具事实时，不要输出具体营业时间。"
    if "store_address_fact_required" in error:
        return "没有门店详情事实时，不要输出具体地址。"
    if "unsupported_store_address_message" in error:
        return "store_address 卡片的 store_id 必须来自本轮门店工具事实或请求里明确确认的门店 ID；没有匹配门店事实时，不要输出 store_address，只能用文字说明暂时没查到并继续确认城市、区域或门店。"
    if "distance_fact_required" in error:
        return "没有距离工具事实时，不要输出最近、几公里或几分钟。"
    if "available_time_fact_required" in error:
        return "available_time 工具失败、超时或没有返回可用 slots 时，不要说有空、可以约、有时间或有名额；只能说明暂时没查到实时档期，并继续确认门店/时间或让门店核对。"
    return ""


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
    activity_url = activity_intro_image_url(state)
    if activity_url:
        urls.add(activity_url)
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
