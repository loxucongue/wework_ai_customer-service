from __future__ import annotations

import re
from typing import Any

from app.graph import reply_filters
from app.graph.nodes.common import looks_garbled_text, renumber_messages
from app.graph.runtime_context import contextual_price_project
from app.graph.nodes.reply_validation import message_content_text
from app.graph.planner.runtime_plan import planner_handoff, planner_task_views
from app.graph.state import AgentState


def postprocess_reply_messages(
    state: AgentState,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    state["postprocess_changed"] = False
    state["postprocess_reasons"] = []
    original_messages = [dict(message) for message in messages if isinstance(message, dict)]
    reasons: list[str] = []
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
        if looks_garbled_text(content) or "\ufffd" in content:
            reasons.append("garbled_removed")
            continue

        if msg_type == "text":
            content = content.strip()
            normalized = re.sub(r"\s+", "", content)
            if not normalized or normalized in seen_text:
                reasons.append("dedupe_or_limit")
                continue
            seen_text.add(normalized)

        payload: Any = {"text": content} if msg_type == "text" else content
        cleaned.append({"type": msg_type, "order": len(cleaned) + 1, "content": payload})
        if len(cleaned) >= 2:
            if len(messages) > len(cleaned):
                reasons.append("dedupe_or_limit")
            break

    if not cleaned:
        state["postprocess_changed"] = bool(original_messages)
        state["postprocess_reasons"] = ["all_messages_removed"] if original_messages else []
        return []

    before_sensitive = _message_fingerprint(cleaned)
    cleaned = reply_filters.sanitize_sensitive_reply_content(
        cleaned,
        task_types=task_types,
        normalized_content=content_text,
        conversation_history=conversation_history,
        contextual_price_project=contextual_price_project(state),
    )
    if _message_fingerprint(cleaned) != before_sensitive:
        reasons.append("sensitive_sanitized")

    before_visible = _message_fingerprint(cleaned)
    cleaned = reply_filters.sanitize_customer_visible_messages(cleaned)
    if _message_fingerprint(cleaned) != before_visible:
        reasons.append("customer_visible_sanitized")

    cleaned = renumber_messages(cleaned)

    handoff_message = _handoff_message_for_state(state)
    if handoff_message:
        cleaned.append({"type": "human_handoff", "order": len(cleaned) + 1, "content": handoff_message})
        reasons.append("handoff_appended")

    cleaned = renumber_messages(cleaned)
    changed = _message_fingerprint(cleaned) != _message_fingerprint(original_messages)
    state["postprocess_changed"] = changed
    state["postprocess_reasons"] = _unique_reasons(reasons) if changed else []
    return cleaned


def _handoff_message_for_state(state: AgentState) -> dict[str, Any] | None:
    handoff = planner_handoff(state)
    if handoff.get("needed"):
        reason = str(handoff.get("reason") or "").strip() or "当前问题需要专业同事继续协助核对"
        return {"handoff_reason": reason}

    assist_reason = _professional_assist_reason(state)
    if not assist_reason:
        return None
    reason = assist_reason or "当前问题需要专业同事继续协助核对"
    return {"handoff_reason": reason}


def _professional_assist_reason(state: AgentState) -> str:
    for source in _professional_assist_sources(state):
        if not isinstance(source, dict) or str(source.get("status") or "").strip() != "requested":
            continue
        reason = str(source.get("reason") or source.get("required_internal_action") or "").strip()
        if reason:
            return reason[:180]
    return ""


def _professional_assist_sources(state: AgentState) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []

    structured_facts = state.get("structured_facts")
    if isinstance(structured_facts, dict) and isinstance(structured_facts.get("professional_assist"), dict):
        sources.append(structured_facts["professional_assist"])

    fact_envelope = state.get("fact_envelope")
    if isinstance(fact_envelope, dict):
        envelope_structured = fact_envelope.get("structured_facts")
        if isinstance(envelope_structured, dict) and isinstance(envelope_structured.get("professional_assist"), dict):
            sources.append(envelope_structured["professional_assist"])

    tool_results = state.get("tool_results")
    if isinstance(tool_results, dict) and isinstance(tool_results.get("professional_assist"), dict):
        sources.append(tool_results["professional_assist"])

    return sources


def _message_fingerprint(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    fingerprint: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("type") or "")
        content = message.get("content")
        if isinstance(content, dict):
            text = str(content.get("text") or content.get("url") or content.get("handoff_reason") or "").strip()
        else:
            text = str(content or "").strip()
        fingerprint.append((message_type, text))
    return fingerprint


def _unique_reasons(reasons: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if not reason or reason in seen:
            continue
        seen.add(reason)
        ordered.append(reason)
    return ordered
