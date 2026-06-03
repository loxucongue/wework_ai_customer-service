from __future__ import annotations

from typing import Any

from app.graph import reply_filters
from app.graph.nodes.reply_quality_appointment import (
    claims_unavailable_preferred_time_available,
    forced_reply_satisfies_hard_instruction,
)
from app.graph.nodes.reply_quality_general import check_general_trust_image, check_project_store_dispute
from app.graph.nodes.reply_quality_rules import (
    check_final_intent_rules,
    check_forbidden_and_context,
    check_store_appointment_price,
)
from app.graph.nodes.reply_quality_types import ReplyQualityCallbacks
from app.graph.state import AgentState


def model_reply_unsafe(state: AgentState, messages: list[dict[str, Any]], callbacks: ReplyQualityCallbacks) -> bool:
    text = "\n".join(str(message.get("content") or "") for message in messages)
    if reply_filters.has_internal_reply_leak(text):
        return True
    if any(term in text for term in ["客户偏好", "客户想", "客户问", "客户提到", "客户表示"]):
        return True
    intents = {item.get("intent") for item in state.get("intents", [])}
    content = state.get("normalized_content") or ""
    project = callbacks.extract_project(content)
    image_info = state.get("image_info") or {}
    known_visible = callbacks.known_visible_concerns_from_state(state)

    checks = [
        lambda: check_general_trust_image(state, text, intents, content, project, image_info, known_visible, callbacks),
        lambda: check_project_store_dispute(state, text, intents, content, project, image_info, known_visible, callbacks),
        lambda: check_forbidden_and_context(
            state,
            text,
            intents,
            content,
            project,
            image_info,
            known_visible,
            len(messages),
            callbacks,
        ),
        lambda: check_store_appointment_price(state, text, intents, content, project, image_info, known_visible, callbacks),
        lambda: check_final_intent_rules(state, text, intents, content, project, image_info, known_visible, callbacks),
    ]
    for check in checks:
        decision = check()
        if decision is not None:
            return bool(decision)
    return False


def forced_reply_safe(messages: list[dict[str, Any]], payload: dict[str, Any], callbacks: ReplyQualityCallbacks) -> bool:
    return forced_reply_satisfies_hard_instruction(messages, payload, callbacks)
