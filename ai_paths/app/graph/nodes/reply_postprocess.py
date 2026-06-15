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
    recent_assistant = _recent_assistant_texts_for_dedupe(state)
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
            if _contains_blocked_placeholder_url(content):
                reasons.append("placeholder_url_removed")
                continue
            content = _sanitize_unbacked_case_image_promise(state, content)
            normalized = re.sub(r"\s+", "", content)
            if not normalized or normalized in seen_text:
                reasons.append("dedupe_or_limit")
                continue
            if _too_similar_to_recent_reply(normalized, recent_assistant):
                reasons.append("recent_reply_dedupe")
                continue
            seen_text.add(normalized)
        elif msg_type == "image":
            if image_count >= 1:
                reasons.append("image_limit")
                continue
            if not _is_usable_case_image_url(content):
                reasons.append("invalid_image_url_removed")
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
        if _is_usable_case_image_url(image_url):
            urls.append(image_url)
    return list(dict.fromkeys(urls))


def _is_usable_case_image_url(image_url: str) -> bool:
    if not image_url or not image_url.startswith(("http://", "https://")):
        return False
    lowered = image_url.lower()
    blocked_hosts = ("example.com", "example.cn", "localhost", "127.0.0.1")
    if any(host in lowered for host in blocked_hosts):
        return False
    return True


def _contains_blocked_placeholder_url(text: str) -> bool:
    lowered = (text or "").lower()
    return any(host in lowered for host in ("example.com", "example.cn", "localhost", "127.0.0.1"))


def _sanitize_unbacked_case_image_promise(state: AgentState, text: str) -> str:
    if _case_image_urls_from_state(state):
        return text
    content = str(text or "")
    if not any(term in content for term in ("发你看", "发您看", "发图", "效果图", "对比图")):
        return content
    replacements = {
        "我先发你看同类效果对比": "我先按同类方向帮你找参考",
        "我先发您看同类效果对比": "我先按同类方向帮您找参考",
        "先发你看同类效果对比": "先按同类方向帮你找参考",
        "先发您看同类效果对比": "先按同类方向帮您找参考",
        "下面发图": "先帮你找参考",
        "发你看": "帮你找参考",
        "发您看": "帮您找参考",
    }
    for source, target in replacements.items():
        content = content.replace(source, target)
    content = content.replace("我先按同类方向帮你找参考，方便", "方便")
    if not _current_query_has_visible_concern(state):
        content = re.sub(r"你主要是[^。！？!?]{0,80}[。！？!?]?", "", content).strip()
    return content


def _current_query_has_visible_concern(state: AgentState) -> bool:
    query = str(state.get("normalized_content") or "")
    if any(term in query for term in ("斑", "黑色素", "色沉", "痘印", "毛孔", "细纹", "皱纹", "肤色", "敏感", "红")):
        return True
    image_info = state.get("image_info")
    if isinstance(image_info, dict) and image_info.get("visible_concerns"):
        return True
    return False


def _recent_assistant_texts_for_dedupe(state: AgentState) -> list[str]:
    texts: list[str] = []
    for item in (state.get("conversation_history") or [])[-8:]:
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("direction") or "").lower()
            if role not in {"assistant", "staff", "bot"}:
                continue
            content = item.get("content")
            text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
        else:
            raw = str(item or "").strip()
            if not raw.startswith(("小贝：", "客服：", "AI回复：")):
                continue
            text = raw.split("：", 1)[-1].strip()
        normalized = re.sub(r"\s+", "", text)
        if normalized:
            texts.append(normalized)
    return texts[-4:]


def _too_similar_to_recent_reply(normalized: str, recent_replies: list[str]) -> bool:
    if len(normalized) < 18:
        return False
    for recent in recent_replies:
        if len(recent) < 18:
            continue
        if normalized == recent or normalized in recent or recent in normalized:
            return True
        overlap = len(set(normalized) & set(recent)) / max(1, len(set(normalized)))
        if overlap >= 0.88 and abs(len(normalized) - len(recent)) <= max(12, int(len(normalized) * 0.25)):
            return True
    return False


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
