from __future__ import annotations

import html
import re
from typing import Any

from app.graph.reply_internal_sanitizer import renumber as _renumber


def attach_asset_images(
    messages: list[dict[str, Any]],
    *,
    intents: set[str],
    fact_envelope: dict[str, Any],
) -> list[dict[str, Any]]:
    asset_key = ""
    if "case_request" in intents:
        asset_key = "case_studies"
    if not asset_key:
        return _renumber(messages)
    if any(message.get("type") == "image" for message in messages):
        return _renumber(messages)
    image_url = first_asset_image_url(fact_envelope, asset_key)
    if not image_url:
        return _renumber(messages)

    image_message = {"type": "image", "order": 2, "content": image_url}
    if not messages:
        return _renumber([image_message])

    result = [messages[0], image_message, *messages[1:]]
    return _renumber(result[:3])


def first_asset_image_url(fact_envelope: dict[str, Any], key: str) -> str:
    structured = fact_envelope.get("structured_facts") or {}
    if isinstance(structured, dict):
        knowledge_facts = structured.get("case_facts") if key == "case_studies" else []
        if isinstance(knowledge_facts, list):
            for item in knowledge_facts:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content") or "")
                match = re.search(r'<img\s+[^>]*src=["\']([^"\']+)["\']', content, flags=re.IGNORECASE)
                if match:
                    return html.unescape(match.group(1))
                stripped = content.strip()
                if stripped.startswith("http://") or stripped.startswith("https://"):
                    return html.unescape(stripped.split()[0])
    items = []
    if isinstance(fact_envelope.get(key), dict):
        items = fact_envelope.get(key, {}).get("items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        match = re.search(r'<img\s+[^>]*src=["\']([^"\']+)["\']', content, flags=re.IGNORECASE)
        if match:
            return html.unescape(match.group(1))
        stripped = content.strip()
        if stripped.startswith("http://") or stripped.startswith("https://"):
            return html.unescape(stripped.split()[0])
    return ""
