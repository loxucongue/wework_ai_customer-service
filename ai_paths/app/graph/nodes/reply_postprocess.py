from __future__ import annotations

import re
from typing import Any

from app.graph import reply_filters
from app.graph.nodes.common import renumber_messages
from app.graph.runtime_context import contextual_price_project
from app.graph.nodes.reply_validation import message_content_text
from app.graph.planner.runtime_plan import planner_handoff, planner_task_views
from app.graph.state import AgentState


def postprocess_reply_messages(
    state: AgentState,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    task_types = {
        str(view.get("type") or "").strip()
        for view in planner_task_views(state)
        if isinstance(view, dict) and str(view.get("type") or "").strip()
    }
    content_text = str(state.get("normalized_content") or "")
    conversation_history = state.get("conversation_history", [])
    cleaned: list[dict[str, Any]] = []
    seen_text: set[str] = set()

    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("type") == "human_handoff":
            continue

        msg_type = message.get("type") if message.get("type") in {"text", "image"} else "text"
        content = message_content_text(message.get("content"))
        if not content:
            continue

        if msg_type == "text":
            content = content.strip()
            normalized = re.sub(r"\s+", "", content)
            if not normalized or normalized in seen_text:
                continue
            seen_text.add(normalized)

        payload: Any = {"text": content} if msg_type == "text" else content
        cleaned.append({"type": msg_type, "order": len(cleaned) + 1, "content": payload})
        if len(cleaned) >= 2:
            break

    if not cleaned:
        return []

    cleaned = reply_filters.sanitize_sensitive_reply_content(
        cleaned,
        task_types=task_types,
        normalized_content=content_text,
        conversation_history=conversation_history,
        contextual_price_project=contextual_price_project(state),
    )
    cleaned = reply_filters.sanitize_customer_visible_messages(cleaned)
    cleaned = reply_filters.attach_asset_images(
        cleaned,
        intents=task_types,
        fact_envelope=state.get("fact_envelope", {}) or {},
    )
    cleaned = renumber_messages(cleaned)

    handoff_message = _handoff_message_for_state(state)
    if handoff_message:
        cleaned.append({"type": "human_handoff", "order": len(cleaned) + 1, "content": handoff_message})

    return renumber_messages(cleaned)


def _handoff_message_for_state(state: AgentState) -> dict[str, Any] | None:
    handoff = planner_handoff(state)
    if not handoff.get("needed"):
        return None
    reason = str(handoff.get("reason") or "").strip() or "当前问题需要专业同事继续协助核对"
    return {"handoff_reason": reason}
