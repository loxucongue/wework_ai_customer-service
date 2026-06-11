from __future__ import annotations

import re
from typing import Any

from app.graph.reply_assets import attach_asset_images
from app.graph.reply_compliance_filters import (
    allows_specific_project_names,
    has_sensitive_external_terms,
    is_license_doc_request,
    sanitize_license_promise,
    sanitize_unasked_project_names,
)
from app.graph.reply_internal_sanitizer import (
    has_internal_reply_leak as _has_internal_reply_leak,
    sanitize_customer_visible_messages as _sanitize_customer_visible_messages,
)


def sanitize_sensitive_reply_content(
    messages: list[dict[str, Any]],
    *,
    task_types: set[str],
    normalized_content: str,
    conversation_history: list[Any],
    contextual_price_project: str,
) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    allow_project_names = allows_specific_project_names(
        normalized_content,
        conversation_history,
        task_types=task_types,
        contextual_price_project=contextual_price_project,
    )
    license_doc_request = is_license_doc_request(normalized_content)

    for message in messages:
        if (license_doc_request or "trust_issue" in task_types) and isinstance(message, dict) and message.get("type") == "image":
            continue
        if not isinstance(message, dict) or message.get("type") != "text":
            sanitized.append(message)
            continue

        content = _message_text(message.get("content"))
        content = sanitize_license_promise(content, strict=license_doc_request or "trust_issue" in task_types)
        if has_sensitive_external_terms(content) or not allow_project_names:
            content = sanitize_unasked_project_names(content, allow_project_names=allow_project_names)
        content = content.strip()
        if not content:
            continue

        copied = dict(message)
        copied["content"] = _with_message_text(message.get("content"), content)
        sanitized.append(copied)
    return sanitized


def has_internal_reply_leak(text: str) -> bool:
    return _has_internal_reply_leak(text)


def sanitize_customer_visible_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _sanitize_customer_visible_messages(messages)


__all__ = [
    "attach_asset_images",
    "has_internal_reply_leak",
    "sanitize_customer_visible_messages",
    "sanitize_sensitive_reply_content",
]


def _message_text(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return str(content or "").strip()


def _with_message_text(original: Any, text: str) -> Any:
    if isinstance(original, dict):
        copied = dict(original)
        copied["text"] = text
        return copied
    return {"text": text}
