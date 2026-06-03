from __future__ import annotations

from typing import Any

from app.graph.planner_tool_plan import default_query_for_skill


def validated_planner_intents(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("intents")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Planner JSON missing intents")
    allowed_skills = {"project_consult", "price_consult", "trust_build", "competitor", "after_sales", "store", "appointment", "handoff", "direct_reply"}
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        skill = str(item.get("skill", "")).strip()
        if skill not in allowed_skills:
            continue
        raw_intent = str(item.get("intent") or "").strip()
        if skill == "handoff":
            intent = raw_intent if raw_intent in {"human_request", "complaint_refund"} else "human_request"
        else:
            intent = _intent_for_skill(skill)
        priority_raw = item.get("priority", len(result) + 1)
        try:
            priority = int(priority_raw)
        except (TypeError, ValueError):
            priority = len(result) + 1
        reason = str(item.get("reason") or "模型规划识别").strip()
        result.append(
            {
                "intent": intent,
                "skill": skill,
                "priority": priority,
                "reason": reason[:80],
                "known_info": _string_list(item.get("known_info"), limit=8),
                "missing_info": _string_list(item.get("missing_info"), limit=6),
                "reply_goal": str(item.get("reply_goal") or "").strip()[:160],
                "should_ask": bool(item.get("should_ask")) if isinstance(item.get("should_ask"), bool) else False,
                "tool_plan": _validated_tool_plan(item.get("tools"), skill),
            }
        )
        if len(result) >= 3:
            break
    if not result:
        raise ValueError("Planner JSON has no valid intents")
    return _dedupe_intents(result)


def _dedupe_intents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, item in sorted(enumerate(items), key=lambda pair: (_intent_rank(str(pair[1]["intent"])), int(pair[1]["priority"]), pair[0])):
        key = str(item["intent"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= 3:
            break
    return deduped


def _intent_rank(intent: str) -> int:
    return {
        "human_request": 0,
        "complaint_refund": 0,
        "after_sales": 1,
        "trust_issue": 2,
        "competitor_compare": 3,
        "ad_price_check": 4,
        "price_inquiry": 4,
        "campaign_inquiry": 4,
        "store_inquiry": 5,
        "appointment_intent": 6,
        "appointment_confirm": 6,
        "appointment_change": 6,
        "appointment_cancel": 6,
        "image_inquiry": 7,
        "project_inquiry": 8,
        "emotion_chat": 9,
    }.get(intent, 9)


def _intent_for_skill(skill: str) -> str:
    return {
        "project_consult": "project_inquiry",
        "price_consult": "price_inquiry",
        "trust_build": "trust_issue",
        "competitor": "competitor_compare",
        "after_sales": "after_sales",
        "store": "store_inquiry",
        "appointment": "appointment_intent",
        "handoff": "human_request",
        "direct_reply": "emotion_chat",
    }.get(skill, "emotion_chat")


def _string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text[:80])
        if len(result) >= limit:
            break
    return result


def _validated_tool_plan(value: Any, skill: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    allowed_tools = {
        "kb_search",
        "pricing_db",
        "local_pricing",
        "store_lookup",
        "available_time",
        "appointment_record_query",
        "professional_assist",
        "no_tool",
    }
    allowed_kbs = {"project_qa", "project_price", "case_studies", "trust_assets", "competitor_qa", "after_sales_qa"}
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name not in allowed_tools:
            continue
        tool: dict[str, str] = {
            "name": name,
            "purpose": str(item.get("purpose") or "").strip()[:80],
        }
        if name == "kb_search":
            kb_name = str(item.get("kb_name") or "").strip()
            if kb_name not in allowed_kbs:
                continue
            tool["kb_name"] = kb_name
            tool["query"] = str(item.get("query") or "").strip()[:120] or default_query_for_skill(skill)
        elif name in {"pricing_db", "local_pricing", "store_lookup", "available_time", "appointment_record_query"}:
            tool["query"] = str(item.get("query") or "").strip()[:120]
        result.append(tool)
        if len(result) >= 4:
            break
    return result
