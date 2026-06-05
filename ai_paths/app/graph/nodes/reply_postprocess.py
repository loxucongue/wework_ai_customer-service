from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app.graph import reply_filters, task_state
from app.graph.nodes.project_kb_context import case_request_lacks_specific_context
from app.graph.nodes.reply_validation import message_content_text
from app.graph.state import AgentState
from app.policies.constants import APPOINTMENT_KEYWORDS


@dataclass(frozen=True)
class ReplyPostprocessCallbacks:
    contextual_price_project: Callable[[AgentState], str]
    has_actual_image_context: Callable[[AgentState], bool]
    has_confirmed_spot_goal: Callable[[AgentState], bool]
    has_known_image_context: Callable[[AgentState], bool]
    has_price_objection: Callable[[str], bool]
    is_redundant_known_goal_question: Callable[[AgentState, str], bool]
    looks_like_store_list_message: Callable[[str], bool]
    renumber_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    should_show_appointment_context: Callable[[AgentState], bool]


def postprocess_reply_messages(
    state: AgentState,
    messages: list[dict[str, Any]],
    callbacks: ReplyPostprocessCallbacks,
) -> list[dict[str, Any]]:
    """Filter repeated or unsafe model messages before returning to customer."""
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    content_text = state.get("normalized_content") or ""
    price_objection = callbacks.has_price_objection(content_text)
    has_available_time_result = bool(state.get("tool_results", {}).get("available_time"))
    sales_strategy = state.get("sales_strategy") if isinstance(state.get("sales_strategy"), dict) else {}
    sales_stage = str(sales_strategy.get("sales_stage") or "")
    ask_policy = str(sales_strategy.get("ask_policy") or "")
    max_text_messages = _max_text_messages_for_reply(state, sales_stage, ask_policy)
    cleaned: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    text_messages: list[str] = []
    handoff_message = _handoff_message_for_state(state)
    if handoff_message is None and _state_allows_model_handoff(state):
        handoff_message = _handoff_message_from_model(messages)

    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("type") == "human_handoff":
            continue
        msg_type = message.get("type") if message.get("type") in {"text", "image"} else "text"
        content = message_content_text(message.get("content"))
        if not content:
            continue
        if msg_type == "text" and callbacks.has_actual_image_context(state) and not intents & {"human_request", "complaint_refund", "after_sales"} and reply_filters.asks_for_duplicate_photo(content):
            continue
        if msg_type == "text" and "price_inquiry" in intents and reply_filters.is_vague_price_deferral(content):
            continue
        if msg_type == "text" and "price_inquiry" in intents and callbacks.has_confirmed_spot_goal(state):
            if any(
                term in content
                for term in [
                    "更关注效果、恢复期还是预算",
                    "更在意效果、恢复期还是预算",
                    "效果、恢复期还是预算",
                    "更关注哪方面",
                    "更关注哪一方面",
                    "斑点本身还是整体肤色",
                ]
            ):
                continue
        if msg_type == "text" and price_objection and reply_filters.is_project_only_after_price_objection(content):
            continue
        if msg_type == "text" and callbacks.is_redundant_known_goal_question(state, content):
            continue
        if msg_type == "text" and not intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"} and not task_state.is_active_appointment_task(state):
            if any(term in content for term in ["哪天方便到店", "方便到店", "到店面诊", "约个面诊", "约个时间到店", "面诊下皮肤状态"]):
                continue
        if msg_type == "text" and not task_state.is_active_appointment_task(state) and not any(term in content_text for term in APPOINTMENT_KEYWORDS):
            if any(term in content for term in ["留个名额", "留一个名额", "想约哪一天", "查当天可预约", "可预约的时间段", "近期面诊时段", "近期可约"]):
                continue
        if msg_type == "text" and not callbacks.should_show_appointment_context(state):
            if any(term in content for term in ["已有预约", "已有预约记录", "预约记录：", "你这边已有预约"]):
                continue
        if msg_type == "text" and has_available_time_result and callbacks.looks_like_store_list_message(content):
            if any(re.search(r"\d{1,2}:\d{2}", str(item.get("content") or "")) for item in cleaned):
                continue
        if msg_type == "text":
            content = reply_filters.repair_appointment_commitment(content)
            normalized = re.sub(r"\s+", "", content)
            if normalized in seen_text:
                continue
            if _is_semantically_redundant(content, text_messages):
                continue
            seen_text.add(normalized)

        cleaned.append({"type": msg_type, "order": len(cleaned) + 1, "content": content})
        if msg_type == "text":
            text_messages.append(content)
        if msg_type == "text" and price_objection and reply_filters.has_budget_or_price_answer(content):
            break
        if msg_type == "text" and "price_inquiry" in intents and reply_filters.asks_daily_single_price(content_text) and re.search(r"\d+\s*元?", content):
            break
        # Keep ordinary customer-facing replies compact; handoff is appended later.
        if msg_type == "text" and len(text_messages) >= max_text_messages:
            break

    if not cleaned:
        return []

    cleaned = reply_filters.sanitize_sensitive_reply_content(
        cleaned,
        intents=intents,
        normalized_content=content_text,
        conversation_history=state.get("conversation_history", []),
        contextual_price_project=callbacks.contextual_price_project(state),
    )
    cleaned = reply_filters.sanitize_customer_visible_messages(cleaned)
    result = reply_filters.attach_asset_images(
        cleaned,
        intents=intents,
        tool_results=state.get("tool_results", {}) or {},
        allow_case_study_image=not case_request_lacks_specific_context(state),
    )
    result = _compact_trailing_question(state, result, ask_policy=ask_policy)
    result = [_normalize_output_message(message) for message in result]
    result = callbacks.renumber_messages(result)
    if handoff_message:
        result.append({"type": "human_handoff", "order": len(result) + 1, "content": handoff_message["content"]})
    return callbacks.renumber_messages(result)


def lacks_price_answer_for_price_question(state: AgentState, text: str) -> bool:
    content = state.get("normalized_content") or ""
    if not any(term in content for term in ["多少钱", "多少", "价格", "费用", "预算", "贵不贵"]):
        return False
    if reply_filters.has_budget_or_price_answer(text):
        return False
    if has_no_price_fact_phrase(text):
        return False
    return True


def has_no_price_fact_phrase(text: str) -> bool:
    return any(
        term in text
        for term in [
            "没查到",
            "没有查到",
            "暂时没查到",
            "暂时没有查到",
            "暂未查到",
            "没有明确价格",
            "没有查到明确",
            "不乱报",
            "价格表没看到",
            "不能拿别的项目价格代替",
        ]
    )


def _handoff_message_from_model(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "human_handoff":
            continue
        reason = message_content_text(message.get("content"))
        if reason:
            return {"type": "human_handoff", "content": {"handoff_reason": reason}}
    return None


def _state_allows_model_handoff(state: AgentState) -> bool:
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    skills = {item.get("skill") for item in state.get("intents", []) if isinstance(item, dict)}
    route_result = state.get("route_result") or {}
    return bool(
        intents & {"human_request", "complaint_refund"}
        or "handoff" in skills
        or route_result.get("need_human") is True
        or route_result.get("subflow") == "HUMAN_HANDOFF"
    )


def _handoff_message_for_state(state: AgentState) -> dict[str, Any] | None:
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    skills = {item.get("skill") for item in state.get("intents", []) if isinstance(item, dict)}
    if not (intents & {"human_request", "complaint_refund"} or "handoff" in skills):
        return None
    if "complaint_refund" in intents:
        reason = "客户涉及投诉、退款、费用争议或效果不满，需要专业同事协助核对处理。"
    else:
        reason = "客户当前问题需要专业同事协助确认。"
    return {"type": "human_handoff", "content": {"handoff_reason": reason}}


def _max_text_messages_for_reply(state: AgentState, sales_stage: str, ask_policy: str) -> int:
    content = str(state.get("normalized_content") or "").strip()
    if ask_policy == "no_ask":
        return 1
    if sales_stage in {"collect_info", "service_recovery"}:
        return 2
    if sales_stage in {"store_paving", "quote", "close_order"}:
        if any(term in content for term in ["地址", "导航", "停车", "营业时间", "几点", "怎么去", "路线"]):
            return 2
        return 1
    return 1


def _is_semantically_redundant(content: str, existing: list[str]) -> bool:
    candidate = _normalize_semantic_text(content)
    if not candidate:
        return True
    for item in existing:
        previous = _normalize_semantic_text(item)
        if not previous:
            continue
        if candidate == previous:
            return True
        if candidate in previous and len(candidate) >= max(12, int(len(previous) * 0.65)):
            return True
        if previous in candidate and len(previous) >= max(12, int(len(candidate) * 0.65)):
            return True
    return False


def _normalize_semantic_text(text: str) -> str:
    normalized = str(text or "")
    normalized = re.sub(r"[，。！？、,.!\?\s]", "", normalized)
    for filler in ["小贝", "这边", "可以的", "按你这个情况看", "如果方便的话", "我这边", "给你说一下", "换个说法哈", "换个说法"]:
        normalized = normalized.replace(filler, "")
    return normalized


def _compact_trailing_question(
    state: AgentState,
    messages: list[dict[str, Any]],
    *,
    ask_policy: str,
) -> list[dict[str, Any]]:
    if ask_policy != "no_ask":
        return messages
    text_messages = [item for item in messages if item.get("type") == "text"]
    if len(text_messages) <= 1:
        return messages
    kept: list[dict[str, Any]] = []
    dropped_question = False
    for item in messages:
        if item.get("type") != "text":
            kept.append(item)
            continue
        text = message_content_text(item.get("content"))
        if not dropped_question and _looks_like_followup_question(text):
            dropped_question = True
            continue
        kept.append(item)
    return kept


def _looks_like_followup_question(text: str) -> bool:
    content = str(text or "").strip()
    if not content:
        return False
    if any(term in content for term in ["？", "?"]):
        return True
    return any(
        term in content
        for term in [
            "方便",
            "哪天",
            "哪家",
            "要不要",
            "想不想",
            "可以吗",
            "要吗",
            "吗",
        ]
    )


def _normalize_output_message(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict):
        return message
    msg_type = str(message.get("type") or "text")
    content = message.get("content")
    if msg_type == "text":
        text = message_content_text(content)
        return {**message, "content": {"text": text}}
    if msg_type == "image":
        url = message_content_text(content)
        return {**message, "content": {"url": url}}
    return message
