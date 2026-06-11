from __future__ import annotations

import re
from typing import Any

_INTERNAL_REPLY_PREFIXES = (
    "判断依据",
    "内部分析",
    "系统分析",
    "工具结果",
    "工具返回",
    "知识库显示",
    "检索结果",
    "流程判断",
    "路由结果",
    "AI回复",
    "调试信息",
)

_INTERNAL_REPLY_MARKERS = (
    "判断依据：",
    "判断依据:",
    "内部分析：",
    "内部分析:",
    "系统查到",
    "工具返回",
    "知识库显示",
    "检索结果显示",
    "根据资料库",
    "根据知识库",
    "reply_brief",
    "module_outputs",
    "route_result",
    "subflow",
    "intent",
    "debug",
    "调试信息",
)


def sanitize_customer_visible_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        msg_type = message.get("type") if message.get("type") in {"text", "image"} else "text"
        content = message.get("content")
        content_text = _message_text(content)
        if not content_text:
            continue
        if msg_type == "text":
            if is_internal_reply_message(content_text):
                continue
            content_text = strip_internal_reply_terms(content_text)
            content_text = sanitize_handoff_visible_phrasing(content_text)
            content_text = dedupe_repeated_phrase_noise(content_text)
            if not content_text:
                continue
            visible.append({"type": "text", "order": len(visible) + 1, "content": {"text": content_text}})
            continue
        visible.append({"type": msg_type, "order": len(visible) + 1, "content": content})
    return renumber(visible)


def has_internal_reply_leak(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    return bool(compact) and any(marker in compact for marker in _INTERNAL_REPLY_MARKERS)


def is_internal_reply_message(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return True
    if any(compact.startswith(prefix) for prefix in _INTERNAL_REPLY_PREFIXES):
        return True
    return any(marker in compact for marker in _INTERNAL_REPLY_MARKERS)


def strip_internal_reply_terms(text: str) -> str:
    cleaned = str(text or "").strip()
    replacements = {
        "根据系统查到的：": "",
        "系统查到": "",
        "根据工具返回": "",
        "工具返回": "",
        "知识库显示：": "",
        "根据知识库：": "",
        "根据资料库：": "",
        "检索结果显示：": "",
        "AI回复": "",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(
        r"^\s*(判断依据|内部分析|系统分析|工具结果|流程判断|路由结果)\s*[:：]\s*",
        "",
        cleaned,
    )
    return cleaned.strip()


def sanitize_handoff_visible_phrasing(text: str) -> str:
    cleaned = str(text or "").strip()
    replacements = (
        ("转给专业同事核实处理", "让专业同事核实处理"),
        ("转给专业同事继续核对", "让专业同事继续核对"),
        ("转给专业同事协助处理", "让专业同事协助处理"),
        ("转接专业同事核实处理", "让专业同事核实处理"),
        ("转接专业同事继续核对", "让专业同事继续核对"),
        ("转接专业同事协助处理", "让专业同事协助处理"),
        ("转交专业同事核实处理", "让专业同事核实处理"),
        ("转交专业同事继续核对", "让专业同事继续核对"),
        ("转交专业同事协助处理", "让专业同事协助处理"),
        ("转给专业同事", "让专业同事"),
        ("转接专业同事", "让专业同事"),
        ("转交专业同事", "让专业同事"),
        ("转人工处理", "让专业同事协助处理"),
        ("转人工核实", "让专业同事核实"),
        ("转人工", "让专业同事继续核对"),
    )
    for old, new in replacements:
        cleaned = cleaned.replace(old, new)
    cleaned = cleaned.replace("帮您登记并让专业同事帮您", "帮您登记，并让专业同事")
    cleaned = cleaned.replace("帮您记录并让专业同事帮您", "帮您记录，并让专业同事")
    cleaned = cleaned.replace("帮您登记并让专业同事", "帮您登记，并让专业同事")
    cleaned = cleaned.replace("帮您记录并让专业同事", "帮您记录，并让专业同事")
    cleaned = cleaned.replace("帮您让专业同事", "让专业同事")
    cleaned = cleaned.replace("为您让专业同事", "让专业同事")
    cleaned = cleaned.replace("为您让", "让")
    return cleaned.strip()


def dedupe_repeated_phrase_noise(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    cleaned = cleaned.replace("类项目类方向", "类项目方向")
    cleaned = re.sub(r"([^，。；、]{4,30})这类\1", r"\1", cleaned)
    cleaned = re.sub(r"(比如|例如)([^，。；]{2,24})\1", r"\1\2", cleaned)
    cleaned = re.sub(r"([^，。；]{2,24})\1", r"\1", cleaned)
    cleaned = re.sub(r"([。！？])\1+", r"\1", cleaned)
    return cleaned.strip(" ，。；")


def renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for index, message in enumerate(messages, start=1):
        copied = dict(message)
        copied["order"] = index
        ordered.append(copied)
    return ordered


def _message_text(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("text") or content.get("handoff_reason") or "").strip()
    return str(content or "").strip()
