from __future__ import annotations

from typing import Any

from app.graph.nodes.reply_quality_types import ReplyQualityCallbacks
from app.graph.state import AgentState


def claims_unavailable_preferred_time_available(state: AgentState, text: str, callbacks: ReplyQualityCallbacks) -> bool:
    active_task = state.get("active_task") or {}
    if not isinstance(active_task, dict):
        return False
    known_slots = active_task.get("known_slots") if isinstance(active_task.get("known_slots"), dict) else {}
    preferred_time = str(known_slots.get("visit_time") or "").strip()
    if not preferred_time:
        return False
    available = state.get("tool_results", {}).get("available_time") or {}
    if not isinstance(available, dict):
        return False
    slot_list = callbacks.available_slot_list(available.get("slots") or {})
    if not slot_list or preferred_time in slot_list:
        return False
    variants = callbacks.time_text_variants(preferred_time)
    if not any(variant and variant in text for variant in variants):
        return False
    negative_terms = ["暂时没看到", "暂时没有", "没有看到", "没看到", "暂不可", "不可约", "不能约", "不在", "没有空", "没有可约", "没有空位"]
    if any(term in text for term in negative_terms):
        return False
    positive_terms = ["可以", "可约", "有空", "空位", "能约", "能预约", "直接到店", "确认好了", "已经确认", "确认了", "约好了", "预约成功"]
    return any(term in text for term in positive_terms)


def forced_reply_satisfies_hard_instruction(messages: list[dict[str, Any]], payload: dict[str, Any], callbacks: ReplyQualityCallbacks) -> bool:
    hard_instruction = str(payload.get("hard_instruction") or "")
    if not hard_instruction:
        return False
    facts = payload.get("fact_brief", {}).get("available_facts", {})
    if not isinstance(facts, dict) or facts.get("preferred_time_available") is not False:
        return False
    text = "\n".join(str(message.get("content") or "") for message in messages if isinstance(message, dict))
    if "..." in text or "…" in text or "等等" in text:
        return False
    preferred_time = str(facts.get("customer_preferred_time") or "").strip()
    slots = facts.get("available_time_slots") if isinstance(facts.get("available_time_slots"), list) else []
    if preferred_time and preferred_time not in text:
        return False
    if not any(term in text for term in ["暂时没看到", "没有看到", "没看到", "暂不可", "不在"]):
        return False
    if slots and not any(str(slot) in text for slot in slots[:3]):
        return False
    return not claims_unavailable_preferred_time_available_from_facts(facts, text, callbacks)


def claims_unavailable_preferred_time_available_from_facts(facts: dict[str, Any], text: str, callbacks: ReplyQualityCallbacks) -> bool:
    preferred_time = str(facts.get("customer_preferred_time") or "").strip()
    slots = facts.get("available_time_slots") if isinstance(facts.get("available_time_slots"), list) else []
    if not preferred_time or not slots or preferred_time in slots:
        return False
    variants = callbacks.time_text_variants(preferred_time)
    if not any(variant and variant in text for variant in variants):
        return False
    negative_terms = ["暂时没看到", "暂时没有", "没有看到", "没看到", "暂不可", "不可约", "不能约", "不在", "没有空", "没有可约", "没有空位"]
    if any(term in text for term in negative_terms):
        return False
    positive_terms = ["可以", "可约", "有空", "空位", "能约", "能预约", "直接到店", "确认好了", "已经确认", "确认了", "约好了", "预约成功"]
    return any(term in text for term in positive_terms)
