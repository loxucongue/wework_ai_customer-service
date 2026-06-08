from __future__ import annotations

import html
import re
from typing import Any

from app.graph.reply_internal_sanitizer import renumber as _renumber


def attach_asset_images(
    messages: list[dict[str, Any]],
    *,
    intents: set[str],
    tool_results: dict[str, Any],
    conversation_history: list[Any] | None = None,
    normalized_content: str = "",
    allow_case_study_image: bool = True,
) -> list[dict[str, Any]]:
    asset_key = ""
    if "trust_issue" in intents:
        return _renumber(messages)
    elif "case_request" in intents:
        asset_key = "case_studies"
    elif intents & {"project_inquiry", "image_inquiry"} and _should_attach_case_study_image(normalized_content):
        asset_key = "case_studies"
    if not asset_key:
        return _renumber(messages)
    if asset_key == "case_studies" and not allow_case_study_image:
        return _renumber(messages)
    if any(message.get("type") == "image" for message in messages):
        return _renumber(messages)
    image_url = select_asset_image_url(
        tool_results,
        asset_key,
        conversation_history=conversation_history or [],
    )
    if not image_url:
        return _renumber(messages)

    image_message = {"type": "image", "order": 2, "content": image_url}
    if not messages:
        return _renumber([image_message])

    if asset_key == "case_studies":
        return _renumber([messages[0], image_message])

    result = [messages[0], image_message, *messages[1:]]
    return _renumber(result[:3])


def select_asset_image_url(
    tool_results: dict[str, Any],
    key: str,
    *,
    conversation_history: list[Any],
) -> str:
    candidates = asset_image_urls(tool_results, key)
    if not candidates:
        return ""
    recent_urls = recent_assistant_image_urls(conversation_history, limit=3)
    latest_url = recent_urls[0] if recent_urls else ""
    recent_url_set = set(recent_urls)
    for candidate in candidates:
        if candidate != latest_url:
            return candidate
    for candidate in candidates:
        if candidate not in recent_url_set:
            return candidate
    return ""


def first_asset_image_url(tool_results: dict[str, Any], key: str) -> str:
    urls = asset_image_urls(tool_results, key)
    return urls[0] if urls else ""


def asset_image_urls(tool_results: dict[str, Any], key: str) -> list[str]:
    items = tool_results.get(key, {}).get("items") or []
    urls: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        match = re.search(r'<img\s+[^>]*src=["\']([^"\']+)["\']', content, flags=re.IGNORECASE)
        if match:
            _append_unique_url(urls, seen, html.unescape(match.group(1)))
        markdown_match = re.search(r'!\[[^\]]*\]\((https?://[^)\s]+)\)', content, flags=re.IGNORECASE)
        if markdown_match:
            _append_unique_url(
                urls,
                seen,
                html.unescape(markdown_match.group(1).strip().rstrip("，,。.;；")),
            )
        for url in re.findall(r"https?://[^\s<>'\")]+", content, flags=re.IGNORECASE):
            candidate = html.unescape(url.strip().rstrip("，,。.;；"))
            if _looks_like_image_url(candidate):
                _append_unique_url(urls, seen, candidate)
        stripped = content.strip()
        if stripped.startswith("http://") or stripped.startswith("https://"):
            candidate = html.unescape(stripped.split()[0].rstrip("，,。.;；"))
            if _looks_like_image_url(candidate):
                _append_unique_url(urls, seen, candidate)
    return urls


def recent_assistant_image_urls(history: list[Any], *, limit: int = 3) -> list[str]:
    urls: list[str] = []
    for item in reversed(history or []):
        for candidate in _history_item_image_urls(item):
            if candidate not in urls:
                urls.append(candidate)
            if len(urls) >= limit:
                return urls
    return urls


def _history_item_image_urls(item: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(item, dict):
        direction = str(item.get("direction") or item.get("role") or "").strip().lower()
        msgtype = str(item.get("msgtype") or item.get("type") or "").strip().lower()
        content = item.get("content")
        if direction not in {"service", "assistant", "bot", "ai", "system"} and not (
            isinstance(content, str) and _is_assistant_history_line(content)
        ):
            return []
        if msgtype == "image":
            candidate = _coerce_image_url(content)
            if candidate:
                urls.append(candidate)
        text = _coerce_history_text(content)
        if text:
            urls.extend(_text_image_urls(text))
        return _unique_preserve_order(urls)
    text = str(item or "").strip()
    if not text or not _is_assistant_history_line(text):
        return []
    return _text_image_urls(text)


def _append_unique_url(urls: list[str], seen: set[str], candidate: str) -> None:
    if not candidate or not _looks_like_image_url(candidate) or candidate in seen:
        return
    seen.add(candidate)
    urls.append(candidate)


def _is_assistant_history_line(text: str) -> bool:
    stripped = str(text or "").strip()
    return stripped.startswith("小贝：") or stripped.startswith("助手：") or stripped.startswith("AI回复：") or stripped.startswith("客服：")


def _looks_like_image_url(url: str) -> bool:
    lowered = str(url or "").lower()
    return any(marker in lowered for marker in [".png", ".jpg", ".jpeg", ".webp", "filebiztype", "image"])


def _coerce_history_text(content: Any) -> str:
    if isinstance(content, dict):
        for key in ("text", "url", "content"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    return str(content or "").strip()


def _coerce_image_url(content: Any) -> str:
    if isinstance(content, dict):
        for key in ("url", "image_url", "src", "text"):
            value = str(content.get(key) or "").strip()
            if _looks_like_image_url(value):
                return value
        return ""
    value = str(content or "").strip()
    return value if _looks_like_image_url(value) else ""


def _text_image_urls(text: str) -> list[str]:
    urls: list[str] = []
    for url in re.findall(r"https?://[^\s<>'\")]+", text, flags=re.IGNORECASE):
        candidate = html.unescape(url.strip().rstrip("，,。.;；"))
        if _looks_like_image_url(candidate) and candidate not in urls:
            urls.append(candidate)
    return urls


def _unique_preserve_order(urls: list[str]) -> list[str]:
    result: list[str] = []
    for url in urls:
        if url not in result:
            result.append(url)
    return result


def _should_attach_case_study_image(content: str) -> bool:
    text = str(content or "")
    if not text:
        return False
    explicit_terms = [
        "案例",
        "对比",
        "效果图",
        "前后",
        "图片",
        "照片",
        "发我看看",
        "给我看看",
        "看一下你们做过",
        "同类参考",
        "改善参考",
        "客户做完",
    ]
    effect_terms = ["这种效果", "这样效果", "看到变化", "能做到这样", "能做成这样", "能有这种变化"]
    need_terms = ["黑色素", "祛斑", "淡斑", "色沉", "暗沉", "肤色不均", "毛孔", "痘印", "松弛", "细纹", "提亮"]
    ask_terms = ["能弄吗", "能做吗", "能改善吗", "可以做吗", "有没有效果", "有效果吗", "能不能", "行不行", "年纪大"]
    return (
        any(term in text for term in explicit_terms)
        or any(term in text for term in effect_terms)
        or (any(term in text for term in need_terms) and any(term in text for term in ask_terms))
    )
