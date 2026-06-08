from __future__ import annotations

import re

from app.graph.state import AgentState


_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_NAME_ONLY_RE = re.compile(r"[\u4e00-\u9fa5]{2,6}")
_NAME_EMBEDDED_PATTERNS = [
    re.compile(r"(?:我叫|我是|我姓|名字是|姓名是|就是)\s*[:：]?\s*([\u4e00-\u9fa5]{2,6})"),
]
_LINE_PREFIX_RE = re.compile(r"^(?:用户|客户|顾客)\s*[:：]\s*")
_ASSISTANT_PREFIX_RE = re.compile(r"^(?:小贝|助手|AI回复|客服|员工)\s*[:：]\s*")
_TRAILING_PARTICLE_RE = re.compile(r"[啊呀呢哦哈吧啦嘛~～!！。,.，、]+$")
_GENERIC_NON_NAME = {
    "可以",
    "好的",
    "行",
    "好",
    "收到",
    "谢谢",
    "你好",
    "您好",
    "在吗",
    "哈喽",
    "不用",
    "算了",
    "不约",
    "厦门百星",
    "厦门思明",
    "厦门二店",
    "先这样",
    "那就先这样",
    "就先这样",
    "后面再说",
    "暂时不用",
    "不用了",
    "我考虑下",
    "我再考虑",
}
_IDENTITY_ASK_TERMS = ("姓名", "全名", "名字", "电话", "手机号", "手机号码")
_IDENTITY_DIRECT_ASK_TERMS = (
    "把姓名发我",
    "把全名发我",
    "把名字发我",
    "把电话发我",
    "把手机号发我",
    "把手机号码发我",
    "姓名发我",
    "全名发我",
    "电话发我",
    "手机号发我",
    "手机号码发我",
    "提供一下你的姓名",
    "提供一下你的全名",
    "提供一下你的电话",
    "提供一下你的手机号",
    "提供一下你的手机号码",
)
_APPOINTMENT_CONTEXT_TERMS = (
    "有空位",
    "可约时间",
    "这个时间",
    "明天下午",
    "明天13:00",
    "预约",
    "到店",
    "厦门百星",
    "帮你确认",
)


def extract_phone_value(text: str) -> str:
    match = _PHONE_RE.search(str(text or ""))
    return match.group(0) if match else ""


def extract_customer_name_value(text: str) -> str:
    raw = _normalize_user_text(text)
    if not raw or extract_phone_value(raw):
        return ""
    for pattern in _NAME_EMBEDDED_PATTERNS:
        match = pattern.search(raw)
        if match:
            candidate = _finalize_name_candidate(match.group(1))
            if candidate:
                return candidate
    candidate = _NAME_ONLY_RE.fullmatch(_TRAILING_PARTICLE_RE.sub("", raw))
    if candidate:
        finalized = _finalize_name_candidate(candidate.group(0))
        if finalized:
            return finalized
    return ""


def recent_assistant_asked_identity_slot(state: AgentState) -> bool:
    for text in _iter_recent_texts(state, roles={"assistant"}, limit=8):
        if any(term in text for term in _IDENTITY_DIRECT_ASK_TERMS):
            return True
        if any(term in text for term in _IDENTITY_ASK_TERMS):
            return True
    return False


def recent_history_has_appointment_context(state: AgentState) -> bool:
    return any(
        any(term in text for term in _APPOINTMENT_CONTEXT_TERMS)
        for text in _iter_recent_texts(state, roles={"assistant", "user"}, limit=10)
    )


def recent_identity_values_from_history(state: AgentState) -> dict[str, str]:
    name = ""
    phone = ""
    for text in _iter_recent_texts(state, roles={"user"}, limit=12):
        normalized = _normalize_user_text(text)
        if not normalized:
            continue
        if not phone:
            phone = extract_phone_value(normalized)
        if not name:
            name = extract_customer_name_value(normalized)
        if name and phone:
            break
    return {"customer_name": name, "phone": phone}


def appointment_identity_followup_value(state: AgentState) -> dict[str, str]:
    active_task = state.get("active_task") if isinstance(state.get("active_task"), dict) else {}
    known_slots = active_task.get("known_slots") if isinstance(active_task.get("known_slots"), dict) else {}
    has_active_appointment = (
        str(active_task.get("type") or "") == "appointment_visit"
        and known_slots.get("store_name")
        and (known_slots.get("visit_date_value") or known_slots.get("visit_date_label"))
        and known_slots.get("visit_time")
    )
    if not has_active_appointment and not recent_history_has_appointment_context(state):
        return {}
    content = str(state.get("normalized_content") or "").strip()
    if not content:
        return {}
    phone = extract_phone_value(content)
    name = extract_customer_name_value(content)
    if not phone and not name:
        return {}
    if not recent_assistant_asked_identity_slot(state):
        return {}
    return {"customer_name": name, "phone": phone}


def _normalize_user_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = _LINE_PREFIX_RE.sub("", raw)
    raw = _ASSISTANT_PREFIX_RE.sub("", raw)
    return raw.strip()


def _finalize_name_candidate(candidate: str) -> str:
    cleaned = _TRAILING_PARTICLE_RE.sub("", str(candidate or "").strip())
    if not cleaned:
        return ""
    if cleaned in _GENERIC_NON_NAME:
        return ""
    if any(ch.isdigit() for ch in cleaned):
        return ""
    if not _NAME_ONLY_RE.fullmatch(cleaned):
        return ""
    return cleaned


def _iter_recent_texts(
    state: AgentState,
    *,
    roles: set[str],
    limit: int,
) -> list[str]:
    texts: list[str] = []
    recent_messages = state.get("recent_messages") or []
    for item in reversed(recent_messages[-limit:]):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if role not in roles:
            continue
        text = str(item.get("content") or "").strip()
        if text:
            texts.append(text)

    if len(texts) < limit:
        history = state.get("conversation_history") or []
        for item in reversed(history[-limit:]):
            text = str(item or "").strip()
            if not text:
                continue
            lines = [segment.strip() for segment in text.splitlines() if str(segment).strip()]
            if not lines:
                continue
            for line in reversed(lines):
                normalized = line
                if normalized.startswith("对话摘要:"):
                    normalized = normalized.split("对话摘要:", 1)[1].strip()
                if not normalized:
                    continue
                if normalized.startswith("用户:") or normalized.startswith("客户:"):
                    if "user" in roles:
                        texts.append(normalized)
                    continue
                if (
                    normalized.startswith("小贝:")
                    or normalized.startswith("助手:")
                    or normalized.startswith("AI回复")
                    or normalized.startswith("客服:")
                    or normalized.startswith("客服：")
                    or normalized.startswith("员工:")
                    or normalized.startswith("员工：")
                ):
                    if "assistant" in roles:
                        texts.append(normalized)
                    continue
                if "user" in roles:
                    texts.append(normalized)
    return texts
