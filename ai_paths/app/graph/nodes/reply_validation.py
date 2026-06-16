from __future__ import annotations

import html
import re
from typing import Any

from app.graph.nodes.common import renumber_messages

VISIBLE_MESSAGE_TYPES = {"text", "image"}
ALLOWED_MESSAGE_TYPES = {"text", "image", "human_handoff", "book_order", "store_address"}


def validated_model_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages = payload.get("reply_messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Model JSON missing reply_messages")
    result: list[dict[str, Any]] = []
    visible_count = 0
    has_handoff = False
    for item in messages:
        if not isinstance(item, dict):
            continue
        msg_type = item.get("type") if item.get("type") in ALLOWED_MESSAGE_TYPES else "text"
        if msg_type == "human_handoff":
            if has_handoff:
                continue
            handoff_reason = message_content_text(item.get("content"))
            if not handoff_reason:
                continue
            result.append(
                {
                    "type": "human_handoff",
                    "order": len(result) + 1,
                    "content": {"handoff_reason": handoff_reason},
                }
            )
            has_handoff = True
            continue
        if msg_type == "book_order":
            order_id = message_content_order_id(item.get("content"))
            result.append(
                {
                    "type": "book_order",
                    "order": len(result) + 1,
                    "content": {"order_id": order_id},
                }
            )
            continue
        if msg_type == "store_address":
            store_id = message_content_store_id(item.get("content"))
            result.append(
                {
                    "type": "store_address",
                    "order": len(result) + 1,
                    "content": {"store_id": store_id},
                }
            )
            continue
        if visible_count >= 3:
            continue
        content = message_content_text(item.get("content"))
        if not content:
            continue
        if msg_type == "text":
            image_url = extract_image_url_from_text(content)
            if image_url:
                text_without_url = strip_image_url_from_text(content, image_url)
                if text_without_url:
                    result.append({"type": "text", "order": len(result) + 1, "content": text_without_url})
                    visible_count += 1
                result.append({"type": "image", "order": len(result) + 1, "content": image_url})
                visible_count += 1
                continue
        result.append({"type": msg_type, "order": len(result) + 1, "content": content})
        visible_count += 1
    if not result:
        raise ValueError("Model reply_messages are empty")
    return renumber_messages(result)


def debug_message_contents(messages: list[dict[str, Any]]) -> list[str]:
    return [message_content_text(message.get("content"))[:240] for message in messages[:4] if isinstance(message, dict)]


def message_content_text(content: Any) -> str:
    if isinstance(content, dict):
        for key in ("handoff_reason", "text", "url"):
            value = content.get(key)
            text = message_content_text(value)
            if text:
                return text
        return ""
    return str(content or "").strip()


def message_content_order_id(content: Any) -> str:
    if isinstance(content, dict):
        value = content.get("order_id") or content.get("id")
        return str(value or "").strip()
    return ""


def message_content_store_id(content: Any) -> str:
    if isinstance(content, dict):
        value = content.get("store_id") or content.get("id")
        return str(value or "").strip()
    return ""


def looks_like_image_url(content: str) -> bool:
    return bool(extract_image_url_from_text(content))


def extract_image_url_from_text(content: str) -> str:
    text = html.unescape(content.strip())
    img_match = re.search(r'<img\s+[^>]*src=["\']([^"\']+)["\']', text, flags=re.IGNORECASE)
    if img_match:
        return html.unescape(img_match.group(1)).strip()
    markdown_match = re.search(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", text)
    if markdown_match:
        url = html.unescape(markdown_match.group(1)).strip()
        if is_image_url(url):
            return url
    url_match = re.search(r"https?://[^\s<>'\")]+", text)
    if url_match:
        url = html.unescape(url_match.group(0)).strip()
        if is_image_url(url):
            return url
    return ""


def strip_image_url_from_text(content: str, image_url: str) -> str:
    text = html.unescape(content.strip())
    text = re.sub(r"<img\s+[^>]*>", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"!\[[^\]]*\]\(" + re.escape(image_url) + r"\)", "", text).strip()
    text = text.replace(image_url, "").strip()
    text = re.sub(r"\s+", " ", text).strip(" ，,。；;")
    return text


def is_image_url(text: str) -> bool:
    if not (text.startswith("http://") or text.startswith("https://")):
        return False
    if "\n" in text or " " in text:
        return False
    lower = text.lower()
    return any(
        marker in lower
        for marker in [".png", ".jpg", ".jpeg", ".webp", "filebiztype.biz_bot_dataset", "ocean-cloud-tos"]
    )
