from __future__ import annotations

from typing import Any

from app.graph.nodes.reply_brief_business import (
    apply_case_process_ad_dispute_context,
    apply_image_context,
    apply_multi_recap_context,
    apply_pre_visit_context,
    apply_price_context,
    apply_price_recap_and_memory_context,
    apply_project_context,
    apply_trust_and_misc_context,
    suggested_followup_for_brief,
)
from app.graph.nodes.reply_brief_store_appointment import (
    apply_appointment_context,
    apply_store_context,
    apply_store_recap_context,
)
from app.graph.nodes.reply_brief_types import ReplyBriefCallbacks
from app.graph.state import AgentState


def reply_brief_for_model(state: AgentState, callbacks: ReplyBriefCallbacks) -> dict[str, Any]:
    """Build the factual brief consumed by the final reply model."""
    content = state.get("normalized_content") or ""
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    brief: dict[str, Any] = {
        "customer_message": content,
        "intents": sorted(str(intent) for intent in intent_set if intent),
        "must_answer": [],
        "available_facts": {},
        "answer_first": [],
        "known_facts": [],
        "do_not_say": [
            "系统查询到",
            "知识库显示",
            "我是AI",
            "转人工",
            "包效果",
            "一定有效",
            "我把营业执照发你",
            "发送营业执照",
            "营业执照发你",
        ],
        "follow_up": "",
    }

    apply_multi_recap_context(state, brief, callbacks)
    apply_image_context(state, brief, callbacks)
    apply_pre_visit_context(state, brief, callbacks)
    apply_price_context(state, brief, callbacks)
    apply_project_context(state, brief, callbacks)
    apply_case_process_ad_dispute_context(state, brief, callbacks)
    apply_store_context(state, brief, callbacks)
    apply_store_recap_context(state, brief, callbacks)
    apply_appointment_context(state, brief, callbacks)
    apply_trust_and_misc_context(state, brief, callbacks)
    apply_price_recap_and_memory_context(state, brief, callbacks)

    brief["must_answer"] = callbacks.dedupe_strings([str(item).strip() for item in brief["must_answer"] if str(item).strip()])[:8]
    brief["answer_first"] = callbacks.dedupe_strings([str(item).strip() for item in brief["answer_first"] if str(item).strip()])[:3]
    brief["known_facts"] = callbacks.dedupe_strings([str(item).strip() for item in brief["known_facts"] if str(item).strip()])[:10]
    if not brief["follow_up"]:
        brief["follow_up"] = suggested_followup_for_brief(state, callbacks)
    return brief
