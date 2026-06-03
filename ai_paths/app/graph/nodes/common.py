from __future__ import annotations

import json
import re
from typing import Any


def dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def intent_for_skill(skill: str) -> str:
    return {
        "project_consult": "project_inquiry",
        "price_consult": "price_inquiry",
        "trust_build": "trust_issue",
        "competitor": "competitor_compare",
        "after_sales": "after_sales",
        "store": "store_inquiry",
        "appointment": "appointment_intent",
    }.get(skill, "emotion_chat")


def infer_scene(intent: str) -> str:
    if intent in {"appointment_intent", "store_inquiry"}:
        return "S4_appointment_negotiating"
    if intent == "after_sales":
        return "S7_dealed_active"
    return "S3_deep_consult"


def looks_bad_text(text: str) -> bool:
    return text.count("?") >= 2 and not any("\u4e00" <= ch <= "\u9fff" for ch in text)


def model_usage_snapshot(model_client: Any | None) -> dict[str, Any]:
    usage = getattr(model_client, "last_usage", None) if model_client else None
    if not isinstance(usage, dict):
        return {}
    raw_usage = usage.get("usage") if isinstance(usage.get("usage"), dict) else {}
    return {
        "provider": usage.get("provider", ""),
        "model": usage.get("model", ""),
        "tier": usage.get("tier", ""),
        "fallback_index": usage.get("fallback_index", 0),
        "fallback_errors": usage.get("fallback_errors", []),
        "prompt_tokens": raw_usage.get("prompt_tokens", 0),
        "completion_tokens": raw_usage.get("completion_tokens", 0),
        "total_tokens": raw_usage.get("total_tokens", 0),
    }


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def recent_assistant_replies(state: dict[str, Any], limit: int = 4) -> list[str]:
    replies: list[str] = []
    for item in reversed(state.get("conversation_history") or []):
        text = str(item).strip()
        if not text:
            continue
        if text.startswith(("小贝：", "小贝:", "助手：", "助手:", "客服：", "客服:")):
            cleaned = re.sub(r"^(小贝|助手|客服)[：:]\s*", "", text).strip()
            if cleaned:
                replies.append(cleaned[:300])
        if len(replies) >= limit:
            break
    return list(reversed(replies))


def next_step_for_skill(skill: str, content: str) -> str:
    if skill == "price_consult":
        return "确认项目配置"
    if skill == "project_consult":
        return "补充需求或照片"
    if skill == "trust_build":
        return "提供资质和服务保障说明"
    if skill == "appointment":
        return "确认门店和时间"
    return ""


def primary_goal(intents: list[dict[str, Any]]) -> str:
    names = "、".join(str(item["intent"]) for item in intents[:3])
    return f"处理客户本轮的{names}诉求，并在合适时轻度推进项目了解或到店面诊。"


def renumber_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for message in messages:
        key = (str(message.get("type") or ""), str(message.get("content") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(message)
    for index, message in enumerate(deduped, start=1):
        message["order"] = index
    return deduped


def subflow_for_skill(skill: str) -> str:
    return {
        "handoff": "HUMAN_HANDOFF",
        "project_consult": "SF3_project_consult",
        "price_consult": "SF7_price_consult",
        "trust_build": "SF10_trust_build",
        "competitor": "SF5_competitor_response",
        "after_sales": "SF12_after_sales",
        "store": "SF6_store_match",
        "appointment": "SF9_appointment",
    }.get(skill, "DIRECT_REPLY")
