from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Callable

from app.graph.state import AgentState


def is_single_store_fact_query(state: AgentState) -> bool:
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    if intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        return False
    lookup = state.get("tool_results", {}).get("store_lookup") or {}
    if not isinstance(lookup, dict):
        return False
    stores = lookup.get("stores")
    if not isinstance(stores, list) or len(stores) != 1:
        return False
    content = str(state.get("normalized_content") or "")
    fact_terms = [
        "地址",
        "导航",
        "路线",
        "怎么过去",
        "停车",
        "停车场",
        "关门",
        "开门",
        "闭店",
        "停业",
        "还开",
        "还营业",
        "营业时间",
        "几点开",
        "几点关",
    ]
    return any(term in content for term in fact_terms)


def rejects_more_questions(content: str) -> bool:
    return any(
        term in content
        for term in [
            "别一直问",
            "不要一直问",
            "直接说",
            "你判断",
            "你直接判断",
            "我不懂项目",
            "就说先看哪个",
            "你就说",
        ]
    )


def asks_followup_question(text: str) -> bool:
    if "？" in text or "?" in text:
        return True
    followup_terms = [
        "告诉我",
        "方便的话",
        "如果方便",
        "可以先确认",
        "可以告诉我",
        "预算范围",
        "是否需要",
        "需要我帮你",
        "要不要",
        "可以吗",
        "哪一点",
        "哪方面",
    ]
    return any(term in text for term in followup_terms)


def time_text_variants(time_text: str, dedupe_strings: Callable[[list[str]], list[str]]) -> list[str]:
    text = str(time_text or "").strip()
    variants = [text]
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if match:
        hour = int(match.group(1))
        minute = match.group(2)
        if minute == "00":
            variants.append(f"{hour}点")
            if hour > 12:
                variants.append(f"{hour - 12}点")
                variants.append(f"下午{hour - 12}点")
                variants.append(f"晚上{hour - 12}点")
            else:
                variants.append(f"上午{hour}点")
        else:
            variants.append(f"{hour}:{minute}")
            if hour > 12:
                variants.append(f"{hour - 12}:{minute}")
    return dedupe_strings(variants)


def too_similar_to_recent_assistant_reply(
    state: AgentState,
    text: str,
    recent_assistant_replies: Callable[..., list[str]],
) -> bool:
    normalized = normalize_reply_for_similarity(text)
    if len(normalized) < 18:
        return False
    for recent in recent_assistant_replies(state, limit=4):
        recent_norm = normalize_reply_for_similarity(recent)
        if len(recent_norm) < 18:
            continue
        if normalized == recent_norm:
            return True
        ratio = SequenceMatcher(None, normalized, recent_norm).ratio()
        if ratio >= 0.92:
            return True
    return False


def normalize_reply_for_similarity(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())

