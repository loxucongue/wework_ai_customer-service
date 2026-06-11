from __future__ import annotations

import re

from app.graph.reply_internal_sanitizer import dedupe_repeated_phrase_noise
from app.policies.compliance_terms import (
    LICENSE_DOC_TERMS,
    LICENSE_PROMISE_REPLACEMENTS,
    LICENSE_SEND_TERMS,
    PROJECT_PHRASE_REGEX_REPLACEMENTS,
    PROJECT_PHRASE_REPLACEMENTS,
    SPECIFIC_PROJECT_TERMS,
    STRICT_LICENSE_REPLACEMENTS,
    UNSUPPORTED_FACT_REPLACEMENTS,
    replace_sensitive_terms,
    sensitive_reply_terms,
)


def is_license_doc_request(content: str) -> bool:
    asks_doc = any(term in content for term in LICENSE_DOC_TERMS)
    asks_send = any(term in content for term in LICENSE_SEND_TERMS)
    return asks_doc and asks_send


def sanitize_license_promise(text: str, *, strict: bool = False) -> str:
    cleaned = str(text or "")
    for old, new in LICENSE_PROMISE_REPLACEMENTS.items():
        cleaned = cleaned.replace(old, new)
    for old, new in UNSUPPORTED_FACT_REPLACEMENTS.items():
        cleaned = cleaned.replace(old, new)

    if strict:
        cleaned = re.sub(r"https?://\S+", "", cleaned).strip()
        for old, new in STRICT_LICENSE_REPLACEMENTS.items():
            cleaned = cleaned.replace(old, new)
    return cleaned


def allows_specific_project_names(
    normalized_content: str,
    conversation_history: list[object],
    *,
    task_types: set[str],
    contextual_price_project: str,
) -> bool:
    history = " ".join(str(item) for item in conversation_history[-6:])
    text = f"{normalized_content} {history}"
    if any(name in text for name in SPECIFIC_PROJECT_TERMS):
        return True
    return bool(task_types & {"price_inquiry", "campaign_inquiry"} and contextual_price_project)


def sanitize_unasked_project_names(text: str, *, allow_project_names: bool = False) -> str:
    cleaned = str(text or "")
    if not allow_project_names:
        for old, new in PROJECT_PHRASE_REPLACEMENTS.items():
            cleaned = cleaned.replace(old, new)
    cleaned = replace_sensitive_terms(cleaned, include_project_terms=not allow_project_names)
    for pattern, replacement in PROJECT_PHRASE_REGEX_REPLACEMENTS:
        cleaned = re.sub(pattern, replacement, cleaned)
    return dedupe_repeated_phrase_noise(cleaned)


def has_sensitive_external_terms(text: str) -> bool:
    return any(term in text for term in sensitive_reply_terms())
