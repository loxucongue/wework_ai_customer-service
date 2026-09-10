from __future__ import annotations

import copy
from typing import Any


CONTENT_MESSAGE_TYPES = frozenset({"text", "image", "video"})
_MESSAGE_FIELDS = (
    "messages",
    "reply_messages",
    "media",
    "required_structured_media",
    "reference_messages",
)


def content_only_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Separate reference content from independently authorized runtime actions.

    Role names and source catalogs are provenance, never action permissions.
    Keep sales copy verbatim: semantic/factual judgment still belongs to Reply
    and its existing fact validators.
    """
    result = copy.deepcopy(candidate)
    for field in _MESSAGE_FIELDS:
        if isinstance(result.get(field), list):
            result[field] = [
                message
                for message in result[field]
                if isinstance(message, dict) and message.get("type") in CONTENT_MESSAGE_TYPES
            ]
    for field in ("commit_actions", "tool_calls", "structured_delivery_options"):
        result.pop(field, None)
    return result


def content_only_candidates(value: Any) -> list[dict[str, Any]]:
    return [content_only_candidate(item) for item in value or [] if isinstance(item, dict)]
