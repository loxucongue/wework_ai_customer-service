from __future__ import annotations

import re

from app.graph import reply_filters
from app.graph.nodes.reply_validation import message_content_text
from app.graph.state import AgentState
from app.policies.reply_quality_policy import (
    REPLY_HARD_FORBIDDEN_TERMS,
    REPLY_LONG_FORM_TASK_TYPES,
    REPLY_MAX_TEXT_MESSAGE_CHARS,
    REPLY_MAX_TEXT_MESSAGE_CHARS_LONG_FORM,
    REPLY_MAX_TOTAL_TEXT_CHARS,
    REPLY_MAX_TOTAL_TEXT_CHARS_LONG_FORM,
    REPLY_PRICE_RULE_TERMS,
    REPLY_THIRD_PERSON_CUSTOMER_TERMS,
)

_PRICE_CLAIM_PATTERN = re.compile(r"(?<!\d)\d{2,5}\s*元|[一二三四五六七八九十百千万]+元")


def model_reply_unsafe(
    state: AgentState,
    messages: list[dict[str, object]],
) -> bool:
    text = "\n".join(
        message_content_text(message.get("content"))
        for message in messages
        if isinstance(message, dict) and message.get("type") != "human_handoff"
    ).strip()
    if not text:
        return True
    if reply_filters.has_internal_reply_leak(text):
        return True
    if any(term in text for term in REPLY_HARD_FORBIDDEN_TERMS):
        return True
    if any(term in text for term in REPLY_THIRD_PERSON_CUSTOMER_TERMS):
        return True
    if _has_unbacked_price_claim(state, text):
        return True
    if _has_poor_visible_format(state, messages):
        return True
    return False


def _has_unbacked_price_claim(state: AgentState, text: str) -> bool:
    if _has_price_facts(state):
        return False
    if _PRICE_CLAIM_PATTERN.search(text):
        return True
    return any(term in text for term in REPLY_PRICE_RULE_TERMS)


def _has_price_facts(state: AgentState) -> bool:
    fact_envelope = state.get("fact_envelope") if isinstance(state, dict) else {}
    if not isinstance(fact_envelope, dict):
        return False
    structured = fact_envelope.get("structured_facts")
    if isinstance(structured, dict) and structured.get("price_facts"):
        return True
    usable = fact_envelope.get("usable_facts")
    if isinstance(usable, list):
        return any("pricing_" in str(item) or "project_price" in str(item) for item in usable)
    return False


def _has_poor_visible_format(state: AgentState, messages: list[dict[str, object]]) -> bool:
    text_messages = [
        message_content_text(message.get("content"))
        for message in messages
        if isinstance(message, dict) and message.get("type") == "text"
    ]
    text_messages = [text for text in text_messages if text]
    if len(text_messages) > 2:
        return True

    long_form = _is_long_form_turn(state)
    per_message_limit = REPLY_MAX_TEXT_MESSAGE_CHARS_LONG_FORM if long_form else REPLY_MAX_TEXT_MESSAGE_CHARS
    total_limit = REPLY_MAX_TOTAL_TEXT_CHARS_LONG_FORM if long_form else REPLY_MAX_TOTAL_TEXT_CHARS
    if any(len(text) > per_message_limit for text in text_messages):
        return True
    if sum(len(text) for text in text_messages) > total_limit:
        return True
    if len(text_messages) == 2 and _looks_redundant(text_messages[0], text_messages[1]):
        return True
    return False


def _is_long_form_turn(state: AgentState) -> bool:
    task_types = {
        str(task.get("type") or "").strip()
        for task in [state.get("primary_task") or {}, *(state.get("secondary_tasks") or [])]
        if isinstance(task, dict)
    }
    return bool(task_types & REPLY_LONG_FORM_TASK_TYPES)


def _looks_redundant(first: str, second: str) -> bool:
    first_compact = re.sub(r"\s+", "", first)
    second_compact = re.sub(r"\s+", "", second)
    if not first_compact or not second_compact:
        return False
    if first_compact in second_compact or second_compact in first_compact:
        return True
    first_tokens = _char_ngrams(first_compact)
    second_tokens = _char_ngrams(second_compact)
    if not first_tokens or not second_tokens:
        return False
    overlap = len(first_tokens & second_tokens)
    smaller = min(len(first_tokens), len(second_tokens))
    return smaller > 0 and overlap / smaller >= 0.55


def _char_ngrams(text: str, size: int = 4) -> set[str]:
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(0, len(text) - size + 1)}
