from __future__ import annotations

import re
from typing import Any

_ROLE_RE = re.compile(r"^\s*(用户|客户|顾客|小贝|助手|AI回复|客服|人工|对话)\s*[:：]\s*(.*)$")
_ASSISTANT_ROLES = {"小贝", "助手", "AI回复", "客服", "人工"}
_DROP_EXACT = {"", "true", "false", "已发送", "发送成功", "发送失败", "撤回", "[消息]"}


def clean_conversation_history(history: Any, *, limit: int = 10) -> list[str]:
    if not isinstance(history, list):
        return []
    cleaned: list[str] = []
    previous = ""
    for item in history:
        line = _clean_history_line(item)
        if not line:
            continue
        if line == previous:
            continue
        cleaned.append(line)
        previous = line
    return cleaned[-limit:]


def _clean_history_line(item: Any) -> str:
    if isinstance(item, dict):
        role = str(item.get("role") or item.get("direction") or "").strip()
        content = _extract_content(item)
        return _format_history_line(role, content)
    raw = str(item or "").strip()
    if not raw:
        return ""
    match = _ROLE_RE.match(raw)
    if match:
        role, content = match.group(1), match.group(2)
        return _format_history_line(role, content)
    if _should_drop_content("", raw):
        return ""
    return raw


def _extract_content(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, dict):
        for key in ("text", "content", "url", "title"):
            value = str(content.get(key) or "").strip()
            if value:
                return value
        return ""
    return str(content or item.get("text") or "").strip()


def _format_history_line(role: str, content: str) -> str:
    normalized_role = _normalize_role(role)
    normalized_content = str(content or "").strip()
    if _should_drop_content(normalized_role, normalized_content):
        return ""
    if normalized_role:
        return f"{normalized_role}：{normalized_content}"
    return normalized_content


def _normalize_role(role: str) -> str:
    value = str(role or "").strip()
    if value in {"customer", "user", "external", "用户", "客户", "顾客"}:
        return "用户"
    if value in {"staff", "assistant", "service", "小贝", "助手", "AI回复", "客服", "人工"}:
        return "小贝"
    if value == "对话":
        return "对话"
    return value


def _should_drop_content(role: str, content: str) -> bool:
    text = str(content or "").strip()
    compact = re.sub(r"\s+", "", text).lower()
    if compact in _DROP_EXACT:
        return True
    if role in _ASSISTANT_ROLES and re.fullmatch(r"\d+", compact):
        return True
    if re.fullmatch(r"\[(?:图片|语音|视频|表情|文件|链接)?\]", text):
        return True
    if text in {"<empty>", "null", "None"}:
        return True
    return False
