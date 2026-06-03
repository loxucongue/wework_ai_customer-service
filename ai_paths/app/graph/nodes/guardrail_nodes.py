from __future__ import annotations

import re
from typing import Any, Callable

from app.graph.nodes.common import dedupe_strings
from app.graph.state import AgentState
from app.policies.constants import COMPLAINT_KEYWORDS, HUMAN_KEYWORDS, SEVERE_AFTER_SALES_KEYWORDS
from app.services.trace_logger import TraceLogger


def create_hard_guardrails_node(*, trace_logger: TraceLogger) -> Callable[[AgentState], Any]:
    async def hard_guardrails(state: AgentState) -> dict[str, Any]:
        content = state.get("normalized_content") or ""
        with trace_logger.node(state, "hard_guardrails", {"content": content}) as span:
            hit_terms = [word for word in HUMAN_KEYWORDS if word in content]
            if is_identity_question(content):
                hit_terms = [term for term in hit_terms if term not in {"真人", "人工", "客服接待"}]
            if has_minor_signal(content):
                hit_terms.append("未成年")
            hit_terms.extend(severe_after_sales_terms(content))
            hit_terms.extend(complaint_terms(content))
            if has_effect_dispute(content):
                hit_terms.append("效果纠纷")
            if is_image_following_complaint(state):
                hit_terms.append("效果纠纷")
            result = {
                "blocked": bool(hit_terms),
                "terms": dedupe_strings(hit_terms),
                "action": "professional_assist" if hit_terms else "",
            }
            output = {"guardrail_result": result, "trace": state.get("trace", [])}
            span["output_snapshot"] = output
            return output

    return hard_guardrails


def complaint_terms(content: str) -> list[str]:
    if not content:
        return []
    if is_identity_question(content):
        return []
    soft_trust_markers = ["是不是", "会不会", "怕", "担心", "感觉", "不靠谱", "靠不靠谱"]
    if any(prefix in content for prefix in ["是不是", "会不会", "怕", "担心"]) and not any(
        hard in content for hard in ["我要投诉", "要求退款", "退钱", "维权", "曝光", "起诉"]
    ):
        return []
    terms = [word for word in COMPLAINT_KEYWORDS if word in content]
    if terms and any(marker in content for marker in soft_trust_markers) and not any(
        hard in content for hard in ["我要投诉", "要求退款", "退钱", "维权", "曝光", "起诉", "骗我钱", "骗钱"]
    ):
        return []
    if "骗人" in content and not any(prefix in content for prefix in soft_trust_markers):
        terms.append("骗人")
    return dedupe_strings(terms)


def has_effect_dispute(content: str) -> bool:
    if not content:
        return False
    if any(prefix in content for prefix in ["会不会", "怕", "担心", "有没有可能"]) and any(
        word in content for word in ["没效果", "没用", "被坑"]
    ):
        return False
    past_context = any(word in content for word in ["做了", "做完", "做的", "花了", "丢了", "付了", "买了"])
    if any(word in content for word in ["一点用都没", "没有用", "没用", "白做"]) and past_context:
        return True
    if "没效果" in content and past_context:
        return True
    if any(word in content for word in ["没有淡", "没淡"]) and any(word in content for word in ["斑", "色沉", "痘印"]) and past_context:
        return True
    if any(word in content for word in ["花了", "丢了", "花"]) and any(word in content for word in ["没效果", "没用", "没有淡", "没淡", "一点用都没"]):
        return True
    return False


def has_minor_signal(content: str) -> bool:
    if not content:
        return False
    if any(term in content for term in ["未成年", "未满18", "不满18", "未满十八", "不满十八"]):
        return True
    match = re.search(r"(?<!\d)(1[0-7])\s*岁", content)
    if match:
        return True
    return any(term in content for term in ["十七岁", "十六岁", "十五岁", "十四岁", "十三岁", "十二岁"])


def is_identity_question(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["你是真人", "是AI", "是 ai", "机器人", "不是人", "客服是真人", "别骗我"]) and any(
        term in content for term in ["真人", "AI", "ai", "机器人", "骗"]
    )


def is_image_following_complaint(state: AgentState) -> bool:
    content = (state.get("normalized_content") or "").strip()
    image_info = state.get("image_info") or {}
    if not image_info.get("has_image") and content != "[图片]":
        return False
    return has_recent_complaint_context(state)


def severe_after_sales_terms(content: str) -> list[str]:
    if not content:
        return []
    return [word for word in SEVERE_AFTER_SALES_KEYWORDS if word in content and not is_negated_symptom(content, word)]


def has_recent_complaint_context(state: AgentState) -> bool:
    text = recent_conversation_text(state)
    if not text:
        return False
    return bool(complaint_terms(text) or has_effect_dispute(text))


def is_negated_symptom(content: str, symptom: str) -> bool:
    negations = ["没有", "没", "无", "不", "未", "并不", "不是"]
    for prefix in negations:
        if f"{prefix}{symptom}" in content:
            return True
    index = content.find(symptom)
    if index < 0:
        return False
    left = content[max(0, index - 4) : index]
    return any(neg in left for neg in negations)


def recent_conversation_text(state: AgentState, limit: int = 6) -> str:
    history = state.get("conversation_history") or []
    return "\n".join(str(item) for item in history[-limit:])
