from __future__ import annotations

import re
from difflib import SequenceMatcher
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
)

_INTERNAL_REPLY_MARKERS = (
    "判断依据：",
    "判断依据:",
    "内部分析：",
    "内部分析:",
    "系统查询到",
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
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if msg_type == "text":
            if is_internal_reply_message(content):
                continue
            content = strip_internal_reply_terms(content)
            content = dedupe_repeated_phrase_noise(content)
            if not content:
                continue
        visible.append({"type": msg_type, "order": len(visible) + 1, "content": content})
    return renumber(_compact_redundant_text_messages(visible))


def has_internal_reply_leak(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    return bool(compact) and any(marker in compact for marker in _INTERNAL_REPLY_MARKERS)


def is_internal_reply_message(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return True
    if any(compact.startswith(prefix) for prefix in _INTERNAL_REPLY_PREFIXES):
        return True
    if any(marker in compact for marker in _INTERNAL_REPLY_MARKERS):
        return True
    return False


def strip_internal_reply_terms(text: str) -> str:
    cleaned = str(text or "").strip()
    replacements = {
        "根据系统查询到的": "",
        "系统查询到": "",
        "根据工具返回": "",
        "工具返回": "",
        "知识库显示": "",
        "根据知识库": "",
        "根据资料库": "",
        "检索结果显示": "",
        "AI回复": "",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"^\s*(判断依据|内部分析|系统分析|工具结果|流程判断|路由结果)\s*[:：]\s*", "", cleaned)
    return cleaned.strip()


def dedupe_repeated_phrase_noise(text: str) -> str:
    cleaned = str(text or "")
    cleaned = cleaned.replace("类项目类方向", "类项目方向")
    pattern = re.compile(r"(?P<prefix>比如|像)?(?P<phrase>[^，。、；;（）()]{2,32})[、，,]\s*(?P=phrase)(?P<suffix>这类|这种|等)?")
    parenthetical_pattern = re.compile(
        r"(?P<phrase>[^，。、；;（）()]{2,32})（(?:像|如)?(?P=phrase)(?:方向|等方向|这类方向|这类|类方向|类|等)?）"
    )
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = parenthetical_pattern.sub(lambda m: m.group("phrase"), cleaned)
        cleaned = pattern.sub(lambda m: f"{m.group('prefix') or ''}{m.group('phrase')}{m.group('suffix') or ''}", cleaned)
    return cleaned


def renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for index, message in enumerate(messages, start=1):
        copied = dict(message)
        copied["order"] = index
        ordered.append(copied)
    return ordered


def _compact_redundant_text_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(messages) < 2:
        return messages
    compacted: list[dict[str, Any]] = []
    for message in messages:
        if not compacted:
            compacted.append(message)
            continue
        previous = compacted[-1]
        if previous.get("type") == "text" and message.get("type") == "text":
            prev_text = str(previous.get("content") or "").strip()
            curr_text = str(message.get("content") or "").strip()
            if _is_redundant_followup_pair(prev_text, curr_text):
                continue
        compacted.append(message)
    return compacted


def _is_redundant_followup_pair(previous: str, current: str) -> bool:
    prev_norm = re.sub(r"\s+", "", previous)
    curr_norm = re.sub(r"\s+", "", current)
    if not prev_norm or not curr_norm:
        return False
    if prev_norm == curr_norm:
        return True
    similarity = SequenceMatcher(None, prev_norm, curr_norm).ratio()
    if similarity >= 0.76:
        return True
    if len(curr_norm) <= len(prev_norm) and curr_norm in prev_norm:
        return True
    redundant_question_terms = [
        "哪家更方便",
        "如果需要",
        "如需",
        "需要我帮你",
        "要不要我",
        "可以发给你",
        "继续确认",
    ]
    return any(term in curr_norm for term in redundant_question_terms) and similarity >= 0.45
