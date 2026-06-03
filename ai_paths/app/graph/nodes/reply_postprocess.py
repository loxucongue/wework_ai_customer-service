from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app.graph import reply_filters, task_state
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
    cleaned: list[dict[str, Any]] = []
    seen_text: set[str] = set()

    for message in messages:
        if not isinstance(message, dict):
            continue
        msg_type = message.get("type") if message.get("type") in {"text", "image"} else "text"
        content = str(message.get("content") or "").strip()
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
            seen_text.add(normalized)

        cleaned.append({"type": msg_type, "order": len(cleaned) + 1, "content": content})
        if msg_type == "text" and price_objection and reply_filters.has_budget_or_price_answer(content):
            break
        if msg_type == "text" and "price_inquiry" in intents and reply_filters.asks_daily_single_price(content_text) and re.search(r"\d+\s*元?", content):
            break
        if len(cleaned) >= 3:
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
    return callbacks.renumber_messages(cleaned)


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
