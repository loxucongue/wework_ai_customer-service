from __future__ import annotations

import json
import re
from typing import Any


def dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def looks_bad_text(text: str) -> bool:
    return text.count("?") >= 2 and not any("\u4e00" <= ch <= "\u9fff" for ch in text)


def looks_garbled_text(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    question_count = value.count("?") + value.count("？")
    if question_count < 3:
        return False
    cjk_count = sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")
    alnum_count = sum(1 for ch in value if ch.isalnum())
    visible_count = sum(1 for ch in value if not ch.isspace())
    if visible_count == 0:
        return False
    if cjk_count == 0 and alnum_count <= question_count:
        return True
    return question_count / visible_count >= 0.35


def clean_model_text(text: str, *, max_chars: int | None = None) -> str:
    """Remove obvious mojibake lines before they enter facts, memory, or prompts."""
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or looks_garbled_text(line):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    if looks_garbled_text(cleaned):
        return ""
    if max_chars is not None and max_chars >= 0:
        return cleaned[:max_chars]
    return cleaned


def clean_model_value(value: Any, *, max_string_chars: int | None = None) -> Any:
    if isinstance(value, str):
        return clean_model_text(value, max_chars=max_string_chars)
    if isinstance(value, list):
        cleaned_list: list[Any] = []
        for item in value:
            cleaned = clean_model_value(item, max_string_chars=max_string_chars)
            if cleaned in ("", [], {}):
                continue
            cleaned_list.append(cleaned)
        return cleaned_list
    if isinstance(value, dict):
        cleaned_dict: dict[str, Any] = {}
        for key, item in value.items():
            cleaned = clean_model_value(item, max_string_chars=max_string_chars)
            if cleaned in ("", [], {}):
                continue
            cleaned_dict[key] = cleaned
        return cleaned_dict
    return value


def model_usage_snapshot(model_client: Any | None) -> dict[str, Any]:
    usage = getattr(model_client, "last_usage", None) if model_client else None
    if not isinstance(usage, dict):
        return {}
    raw_usage = usage.get("usage") if isinstance(usage.get("usage"), dict) else {}
    return {
        "provider": usage.get("provider", ""),
        "model": usage.get("model", ""),
        "tier": usage.get("tier", ""),
        "fallback_index": usage.get("fallback_index", 0),
        "fallback_errors": usage.get("fallback_errors", []),
        "prompt_tokens": raw_usage.get("prompt_tokens", 0),
        "completion_tokens": raw_usage.get("completion_tokens", 0),
        "total_tokens": raw_usage.get("total_tokens", 0),
    }


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_ASSISTANT_PREFIX_RE = re.compile(r"^(小贝|客服|助理|AI回复|系统)\s*[:：]?\s*")


def recent_assistant_replies(state: dict[str, Any], limit: int = 4) -> list[str]:
    replies: list[str] = []
    for item in reversed(state.get("conversation_history") or []):
        text = str(item).strip()
        if not text:
            continue
        if _ASSISTANT_PREFIX_RE.match(text):
            cleaned = _ASSISTANT_PREFIX_RE.sub("", text).strip()
            if cleaned:
                replies.append(cleaned[:300])
        if len(replies) >= limit:
            break
    return list(reversed(replies))


def renumber_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for message in messages:
        content = message.get("content")
        if isinstance(content, dict):
            content_text = str(content.get("text") or content.get("handoff_reason") or "").strip()
        else:
            content_text = str(content or "").strip()
        key = (str(message.get("type") or ""), content_text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(message)
    for index, message in enumerate(deduped, start=1):
        message["order"] = index
    return deduped
