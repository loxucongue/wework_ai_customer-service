from __future__ import annotations

import re
from typing import Any

from app.graph import reply_filters
from app.graph.nodes.common import looks_garbled_text, renumber_messages
from app.graph.runtime_context import contextual_price_project
from app.graph.nodes.reply_validation import message_content_order_id, message_content_text
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
    special_messages: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    text_count = 0
    image_count = 0

    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("type") == "human_handoff":
            continue
        if message.get("type") == "book_order":
            order_id = message_content_order_id(message.get("content"))
            if order_id:
                special_messages.append({"type": "book_order", "order": 0, "content": {"order_id": order_id}})
            continue

        msg_type = message.get("type") if message.get("type") in {"text", "image"} else "text"
        content = message_content_text(message.get("content"))
        if not content:
            continue
        if looks_garbled_text(content) or "\ufffd" in content:
            reasons.append("garbled_removed")
            continue

        if msg_type == "text":
            if text_count >= 2:
                reasons.append("text_limit")
                continue
            content = content.strip()
            normalized = re.sub(r"\s+", "", content)
            if not normalized or normalized in seen_text:
                reasons.append("dedupe_or_limit")
                continue
            seen_text.add(normalized)
        elif msg_type == "image":
            if image_count >= 1:
                reasons.append("image_limit")
                continue

        payload: Any = {"text": content} if msg_type == "text" else content
        cleaned.append({"type": msg_type, "order": len(cleaned) + 1, "content": payload})
        if msg_type == "text":
            text_count += 1
        elif msg_type == "image":
            image_count += 1

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

    if not _has_visible_image(cleaned):
        case_image = _case_image_message_for_state(state, cleaned)
        if case_image:
            cleaned.append(case_image)
            reasons.append("case_image_appended")

    cleaned = renumber_messages(cleaned)

    handoff_message = _handoff_message_for_state(state)
    if handoff_message:
        cleaned.append({"type": "human_handoff", "order": len(cleaned) + 1, "content": handoff_message})
        reasons.append("handoff_appended")
    else:
        book_order_message = _book_order_message_for_state(state, special_messages)
        if book_order_message:
            cleaned.append(book_order_message)
            reasons.append("book_order_appended")

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


def _book_order_message_for_state(
    state: AgentState,
    model_messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for message in model_messages:
        order_id = message_content_order_id(message.get("content"))
        if order_id:
            return {"type": "book_order", "order": 0, "content": {"order_id": order_id}}

    tool_results = state.get("tool_results")
    opening = tool_results.get("appointment_opening") if isinstance(tool_results, dict) else {}
    if not isinstance(opening, dict) or opening.get("status") not in {"created", "dry_run_created"}:
        return None
    order_id = str(opening.get("order_id") or "").strip()
    if not order_id:
        push = opening.get("appointment_push")
        if isinstance(push, dict):
            order_id = str(push.get("order_id") or "").strip()
    if not order_id:
        return None
    return {"type": "book_order", "order": 0, "content": {"order_id": order_id}}


def _has_visible_image(messages: list[dict[str, Any]]) -> bool:
    return any(isinstance(message, dict) and message.get("type") == "image" for message in messages)


def _case_image_message_for_state(state: AgentState, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not _looks_like_case_or_effect_turn(state):
        return None
    recent_urls = set(_recent_image_urls_from_state(state))
    for image_url in _case_image_urls_from_state(state):
        if image_url and image_url not in recent_urls:
            return {"type": "image", "order": len(messages) + 1, "content": image_url}
    return None


def _looks_like_case_or_effect_turn(state: AgentState) -> bool:
    text = str(state.get("normalized_content") or "")
    if any(term in text for term in ("效果图", "案例", "对比图", "做完效果", "恢复后", "客户做完", "图片上的客户")):
        return True
    for view in planner_task_views(state):
        if not isinstance(view, dict):
            continue
        joined = " ".join(str(view.get(key) or "").lower() for key in ("type", "subtype", "policy_hint", "scene", "subflow"))
        if "case" in joined or "effect" in joined:
            return True
    return False


def _case_image_urls_from_state(state: AgentState) -> list[str]:
    urls: list[str] = []
    for case in _case_facts_from_state(state):
        if not isinstance(case, dict):
            continue
        image_url = str(case.get("image_url") or "").strip()
        if image_url and image_url.startswith(("http://", "https://")):
            urls.append(image_url)
    return list(dict.fromkeys(urls))


def _case_facts_from_state(state: AgentState) -> list[dict[str, Any]]:
    sources: list[Any] = []
    structured_facts = state.get("structured_facts")
    if isinstance(structured_facts, dict):
        sources.append(structured_facts.get("case_facts"))
    fact_envelope = state.get("fact_envelope")
    if isinstance(fact_envelope, dict):
        envelope_structured = fact_envelope.get("structured_facts")
        if isinstance(envelope_structured, dict):
            sources.append(envelope_structured.get("case_facts"))
    results: list[dict[str, Any]] = []
    for source in sources:
        if isinstance(source, list):
            results.extend(item for item in source if isinstance(item, dict))
    return results


def _recent_image_urls_from_state(state: AgentState) -> list[str]:
    urls: list[str] = []
    for key in ("recent_image_urls",):
        value = state.get(key)
        if isinstance(value, list):
            urls.extend(str(item).strip() for item in value if str(item).strip())
    for message in state.get("conversation_history") or []:
        if not isinstance(message, dict):
            continue
        if str(message.get("type") or message.get("msgtype") or "").lower() not in {"image", "图片"}:
            continue
        content = message.get("content")
        if isinstance(content, dict):
            url = str(content.get("url") or content.get("image_url") or "").strip()
        else:
            url = str(content or "").strip()
        if url:
            urls.append(url)
    return list(dict.fromkeys(urls))


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
            text = str(
                content.get("text")
                or content.get("url")
                or content.get("handoff_reason")
                or content.get("order_id")
                or ""
            ).strip()
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
