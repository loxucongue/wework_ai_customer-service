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


def looks_suspected_short_mojibake(text: str) -> bool:
    """Flag short damaged text without guessing the customer's intended words."""
    value = str(text or "").strip()
    if not value or len(value) > 4:
        return False
    if "\ufffd" in value:
        return True
    return bool(re.fullmatch(r"[琛浜鏄][?？]", value))


def repair_mojibake_text(text: str) -> tuple[str, dict[str, Any]]:
    value = str(text or "")
    info: dict[str, Any] = {"applied": False}
    if not value or not _looks_like_utf8_as_gbk_mojibake(value):
        return value, info

    best_text = value
    best_source = ""
    best_score = _readability_score(value)
    for source_encoding in ("gbk", "gb18030", "cp936"):
        try:
            candidate = value.encode(source_encoding, errors="replace").decode("utf-8", errors="replace")
        except (LookupError, UnicodeError):
            continue
        candidate = _normalize_repaired_mojibake(candidate)
        score = _readability_score(candidate)
        if score > best_score:
            best_text = candidate
            best_source = source_encoding
            best_score = score

    if best_text != value and best_score >= _readability_score(value) + 4:
        return best_text, {"applied": True, "source": f"{best_source}->utf-8", "preview": best_text[:80]}
    return value, info


def _looks_like_utf8_as_gbk_mojibake(text: str) -> bool:
    if not text:
        return False
    if any("\u3100" <= ch <= "\u312f" for ch in text):
        return True
    markers = ("锛", "闂", "鍦", "灏忚礉", "瀹㈡埛", "棰勭害", "杈撳叆")
    return sum(1 for marker in markers if marker in text) >= 2


def _normalize_repaired_mojibake(text: str) -> str:
    value = str(text or "")
    return value.replace("\ufffd?", "？").replace("\ufffd？", "？").replace("\ufffd", "")


def _readability_score(text: str) -> int:
    value = str(text or "")
    cjk_count = sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")
    bopomofo_count = sum(1 for ch in value if "\u3100" <= ch <= "\u312f")
    replacement_count = value.count("\ufffd")
    marker_count = sum(1 for marker in ("锛", "闂", "鍦", "灏忚礉", "瀹㈡埛") if marker in value)
    punctuation_bonus = sum(1 for ch in value if ch in "，。？！：；,.?!")
    return cjk_count * 2 + punctuation_bonus - bopomofo_count * 6 - replacement_count * 8 - marker_count * 4


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
        "candidate_models": usage.get("candidate_models", []),
        "primary_model": usage.get("primary_model", ""),
        "hedge_model": usage.get("hedge_model", ""),
        "started_models": usage.get("started_models", []),
        "pending_models": usage.get("pending_models", []),
        "hedge_started": bool(usage.get("hedge_started", False)),
        "winner_model": usage.get("winner_model", usage.get("model", "")),
        "cancelled_models": usage.get("cancelled_models", []),
        "attempts": usage.get("attempts", usage.get("request_attempt", 0)),
        "deadline_seconds": usage.get("deadline_seconds", usage.get("total_timeout_seconds", 0)),
        "total_timeout_seconds": usage.get("total_timeout_seconds", 0),
        "configured_total_timeout_seconds": usage.get("configured_total_timeout_seconds", 0),
        "remaining_budget_ms": usage.get("remaining_budget_ms", 0),
        "timeout_stage": usage.get("timeout_stage", ""),
        "overall_duration_ms": usage.get("overall_duration_ms", usage.get("duration_ms", 0)),
        "error": usage.get("error", ""),
        "duration_ms": usage.get("duration_ms", 0),
        "prompt_tokens": raw_usage.get("prompt_tokens", 0),
        "completion_tokens": raw_usage.get("completion_tokens", 0),
        "total_tokens": raw_usage.get("total_tokens", 0),
    }


def model_call_metrics(model_call: Any, *, prompt_warning_threshold: int) -> dict[str, Any]:
    """Build a small observable summary without copying model prompts into state."""
    if not isinstance(model_call, dict):
        return {}
    call_input = model_call.get("input") if isinstance(model_call.get("input"), dict) else {}
    messages = call_input.get("messages") if isinstance(call_input.get("messages"), list) else []
    input_chars = sum(
        len(str(message.get("content") or ""))
        for message in messages
        if isinstance(message, dict)
    )
    usages: list[dict[str, Any]] = []
    if isinstance(model_call.get("usage"), dict):
        usages.append(model_call["usage"])
    for value in (model_call.get("retry"), model_call.get("recovery")):
        if isinstance(value, dict) and isinstance(value.get("usage"), dict):
            usages.append(value["usage"])
    for value in model_call.get("nested_calls") or []:
        if isinstance(value, dict) and isinstance(value.get("usage"), dict):
            usages.append(value["usage"])
    usage = next(
        (
            value
            for value in reversed(usages)
            if value.get("winner_model") or value.get("prompt_tokens")
        ),
        usages[-1] if usages else {},
    )
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    return {
        "message_count": len(messages),
        "input_chars": input_chars,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "winner_model": str(usage.get("winner_model") or usage.get("model") or ""),
        "hedge_started": bool(usage.get("hedge_started", False)),
        "duration_ms": int(usage.get("overall_duration_ms") or usage.get("duration_ms") or 0),
        "prompt_warning_threshold": int(prompt_warning_threshold),
        "prompt_warning": bool(prompt_tokens > prompt_warning_threshold),
    }


def model_recovery_attempts(model_call: Any, *, node: str) -> list[dict[str, Any]]:
    if not isinstance(model_call, dict):
        return []
    attempts: list[dict[str, Any]] = []
    repair_attempts = model_call.get("repair_retries")
    if isinstance(repair_attempts, list) and repair_attempts:
        for index, value in enumerate(repair_attempts, start=1):
            if not isinstance(value, dict):
                continue
            attempts.append(
                {
                    "node": node,
                    "type": "repair" if index == 1 else f"repair_{index}",
                    "reason": str(value.get("reason") or value.get("error") or "")[:240],
                    "succeeded": not bool(value.get("error")),
                }
            )
    else:
        value = model_call.get("retry")
        if not isinstance(value, dict):
            value = None
        if isinstance(value, dict):
            attempts.append(
                {
                    "node": node,
                    "type": "repair",
                    "reason": str(value.get("reason") or value.get("error") or "")[:240],
                    "succeeded": not bool(value.get("error")),
                }
            )
    value = model_call.get("recovery")
    if isinstance(value, dict):
        attempts.append(
            {
                "node": node,
                "type": "compact_recovery",
                "reason": str(value.get("reason") or value.get("error") or "")[:240],
                "succeeded": not bool(value.get("error")),
            }
        )
    for value in model_call.get("nested_calls") or []:
        if not isinstance(value, dict):
            continue
        attempts.append(
            {
                "node": node,
                "type": str(value.get("name") or "nested_model_call"),
                "reason": str(value.get("error") or "")[:240],
                "succeeded": not bool(value.get("error")),
            }
        )
    return attempts


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
            content_text = str(
                content.get("text")
                or content.get("handoff_reason")
                or content.get("url")
                or content.get("store_id")
                or content.get("id")
                or content.get("amount")
                or ""
            ).strip()
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
