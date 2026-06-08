from __future__ import annotations

from typing import Any

from app.graph.state import AgentState
from app.policies.constants import CITY_NAMES, PROJECT_KEYWORDS


def dedupe_intents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, item in sorted(enumerate(items), key=lambda pair: (intent_rank(str(pair[1]["intent"])), int(pair[1]["priority"]), pair[0])):
        key = str(item["intent"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= 3:
            break
    return deduped


def intent_rank(intent: str) -> int:
    return {
        "human_request": 0,
        "complaint_refund": 0,
        "after_sales": 1,
        "trust_issue": 2,
        "competitor_compare": 3,
        "case_request": 3,
        "ad_price_check": 4,
        "price_inquiry": 4,
        "campaign_inquiry": 4,
        "store_inquiry": 5,
        "appointment_intent": 6,
        "appointment_confirm": 6,
        "appointment_change": 6,
        "appointment_cancel": 6,
        "image_inquiry": 7,
        "project_process": 7,
        "project_inquiry": 8,
        "emotion_chat": 9,
    }.get(intent, 9)


def extract_city(content: str) -> str:
    for city in CITY_NAMES:
        if city in content:
            return city
    return ""


def dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def string_list(value: Any, *, limit: int) -> list[str]:
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


def merge_intent_details(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in ("known_info", "missing_info"):
        values = string_list(merged.get(key), limit=8)
        for item in string_list(extra.get(key), limit=8):
            if item not in values:
                values.append(item)
        merged[key] = values[:8]
    for key in ("reply_goal", "reason"):
        if not merged.get(key) and extra.get(key):
            merged[key] = extra.get(key)
    if extra.get("tool_plan") and not merged.get("tool_plan"):
        merged["tool_plan"] = extra.get("tool_plan")
    if extra.get("should_ask") is True:
        merged["should_ask"] = True
    return merged


def known_info_from_state(state: AgentState, item: dict[str, Any]) -> list[str]:
    content = state.get("normalized_content") or ""
    known: list[str] = []
    city = extract_city(content)
    if city:
        known.append(f"客户所在城市：{city}")
    image_info = state.get("image_info") or {}
    concerns = image_info.get("visible_concerns") if isinstance(image_info, dict) else []
    if isinstance(concerns, list) and concerns:
        known.append("图片可见问题：" + "、".join(str(value) for value in concerns[:5]))
    if any(term in content for term in ["点状斑", "点状", "斑点"]):
        known.append("客户主要关注点状斑/斑点")
    if any(term in content for term in ["预算", "太贵", "贵", "便宜"]):
        known.append("客户关注预算或价格")
    active_task = state.get("active_task") or {}
    if isinstance(active_task, dict) and active_task:
        known.append("存在进行中的任务：" + str(active_task.get("type") or ""))
    return known[:6]


def missing_info_from_state(state: AgentState, item: dict[str, Any]) -> list[str]:
    intent = str(item.get("intent") or "")
    content = state.get("normalized_content") or ""
    missing: list[str] = []
    if intent == "appointment_intent":
        if not (extract_city(content) or (state.get("confirmed_store_id") or state.get("confirmed_store_name"))):
            missing.append("门店或城市")
        if not any(term in content for term in ["今天", "明天", "后天", "周六", "周日", "上午", "下午", "晚上", "点"]):
            missing.append("到店日期或时间")
    elif intent == "store_inquiry":
        if not extract_city(content):
            missing.append("所在城市或区域")
    elif intent == "price_inquiry":
        if not any(project in content for project in PROJECT_KEYWORDS):
            missing.append("具体项目或改善方向")
    return missing[:4]


def reply_goal_for_intent(item: dict[str, Any]) -> str:
    intent = str(item.get("intent") or "")
    return {
        "project_inquiry": "先回答可改善方向，再给一个最关键判断点；不要强迫客户先说专业项目名。",
        "image_inquiry": "承接图片可见问题，直接说明可考虑方向和限制。",
        "price_inquiry": "先说明已知价格或无法乱报价格的原因，再给核价路径。",
        "store_inquiry": "直接回答门店、地址、路线或停车信息。",
        "appointment_intent": "复用已知门店和时间，按真实可约结果推进。",
        "trust_issue": "先解决客户正规、靠谱或收费透明顾虑。",
        "competitor_compare": "不诋毁竞品，拆清楚对比维度。",
        "after_sales": "先确认风险并给安全处理方向。",
        "complaint_refund": "先承接不满和处理诉求，让专业同事核对真实记录。",
        "human_request": "自然说明让专业人士协助。",
    }.get(intent, "先解决客户当前问题，再轻度推进下一步。")


def must_ask_for_intent(item: dict[str, Any]) -> bool:
    return str(item.get("intent") or "") in {"store_inquiry", "appointment_intent"}
