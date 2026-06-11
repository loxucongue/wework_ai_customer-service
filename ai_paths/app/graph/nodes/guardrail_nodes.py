from __future__ import annotations

import re
from typing import Any, Callable

from app.graph.nodes.common import dedupe_strings
from app.graph.planner_dispute_signals import (
    complaint_terms,
    has_effect_dispute,
    has_recent_complaint_context,
    severe_after_sales_terms,
)
from app.graph.planner_general_signals import is_identity_question
from app.graph.state import AgentState
from app.policies.constants import HUMAN_KEYWORDS
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


def has_minor_signal(content: str) -> bool:
    if not content:
        return False
    if any(term in content for term in ["未成年", "未满18", "不满18", "未满十八", "不满十八"]):
        return True
    match = re.search(r"(?<!\d)(1[0-7])\s*岁", content)
    if match:
        return True
    return any(term in content for term in ["十七岁", "十六岁", "十五岁", "十四岁", "十三岁", "十二岁"])


def is_image_following_complaint(state: AgentState) -> bool:
    content = (state.get("normalized_content") or "").strip()
    image_info = state.get("image_info") or {}
    if not image_info.get("has_image") and content != "[图片]":
        return False
    return has_recent_complaint_context(state)
