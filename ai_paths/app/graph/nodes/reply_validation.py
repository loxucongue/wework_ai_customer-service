from __future__ import annotations

import html
import re
from typing import Any

from app.graph.nodes.common import renumber_messages


def validated_model_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages = payload.get("reply_messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Model JSON missing reply_messages")
    result: list[dict[str, Any]] = []
    for item in messages[:3]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        msg_type = item.get("type") if item.get("type") in {"text", "image"} else "text"
        if msg_type == "text":
            image_url = extract_image_url_from_text(content)
            if image_url:
                text_without_url = strip_image_url_from_text(content, image_url)
                if text_without_url:
                    result.append({"type": "text", "order": len(result) + 1, "content": text_without_url})
                result.append({"type": "image", "order": len(result) + 1, "content": image_url})
                continue
        result.append({"type": msg_type, "order": len(result) + 1, "content": content})
    if not result:
        raise ValueError("Model reply_messages are empty")
    return renumber_messages(result[:3])


def debug_message_contents(messages: list[dict[str, Any]]) -> list[str]:
    return [str(message.get("content") or "")[:240] for message in messages[:3] if isinstance(message, dict)]


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
