from __future__ import annotations

import copy
import re
from typing import Any


MAX_CUSTOMER_VISIBLE_MESSAGES = 8
MAX_CUSTOMER_VISIBLE_TEXT_CHARS = 300
MAX_NORMAL_TURN_EMOJIS = 1

_INLINE_SPACE_RE = re.compile(r"[ \t\u3000]+")
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\u2600-\u27BF"
    "]"
)


def compact_reply_message_format(value: Any) -> list[dict[str, Any]]:
    """Normalize harmless formatting and remove exact duplicate text messages.

    This helper deliberately does not shorten or rewrite sales content.  If the
    model remains over the presentation budget after lossless compaction, the
    caller asks the same Reply model for one structural repair.
    """

    if not isinstance(value, list):
        return []
    compacted: list[dict[str, Any]] = []
    seen_texts: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            compacted.append(raw)
            continue
        item = copy.deepcopy(raw)
        if str(item.get("type") or "").strip() != "text":
            compacted.append(item)
            continue
        content = item.get("content")
        if not isinstance(content, str):
            compacted.append(item)
            continue
        text = content.replace("\r\n", "\n").replace("\r", "\n")
        text = "\n".join(_INLINE_SPACE_RE.sub(" ", line).strip() for line in text.split("\n"))
        text = _EXCESS_BLANK_LINES_RE.sub("\n\n", text).strip()
        item["content"] = text
        if text and text in seen_texts:
            continue
        if text:
            seen_texts.add(text)
        compacted.append(item)
    return compacted


def reply_presentation_metrics(messages: Any) -> dict[str, int]:
    visible_messages = [item for item in messages if isinstance(item, dict)] if isinstance(messages, list) else []
    text = "".join(
        str(item.get("content") or "")
        for item in visible_messages
        if str(item.get("type") or "").strip() == "text"
        and isinstance(item.get("content"), str)
    )
    compact_text = "".join(text.split())
    return {
        "message_count": len(visible_messages),
        "text_chars": len(compact_text),
        "emoji_count": len(_EMOJI_RE.findall(text)),
    }


def reply_presentation_violations(
    messages: Any,
    *,
    sensitive_turn: bool = False,
    max_messages: int = MAX_CUSTOMER_VISIBLE_MESSAGES,
    max_text_chars: int = MAX_CUSTOMER_VISIBLE_TEXT_CHARS,
) -> list[str]:
    metrics = reply_presentation_metrics(messages)
    violations: list[str] = []
    safe_max_messages = max(1, int(max_messages or MAX_CUSTOMER_VISIBLE_MESSAGES))
    safe_max_text_chars = max(1, int(max_text_chars or MAX_CUSTOMER_VISIBLE_TEXT_CHARS))
    if metrics["message_count"] > safe_max_messages:
        violations.append(
            f"reply_presentation_message_limit_exceeded:{metrics['message_count']}"
        )
    if metrics["text_chars"] > safe_max_text_chars:
        violations.append(
            f"reply_presentation_text_limit_exceeded:{metrics['text_chars']}"
        )
    allowed_emojis = 0 if sensitive_turn else MAX_NORMAL_TURN_EMOJIS
    if metrics["emoji_count"] > allowed_emojis:
        violations.append(
            f"reply_presentation_emoji_limit_exceeded:{metrics['emoji_count']}:{allowed_emojis}"
        )
    return violations
