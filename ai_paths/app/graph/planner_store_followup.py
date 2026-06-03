from __future__ import annotations

from typing import Any

from app.graph.planner_dispute_signals import recent_conversation_text
from app.graph.planner_intent_meta import extract_city
from app.graph.state import AgentState
from app.policies.constants import AFTER_SALES_KEYWORDS, COMPETITOR_KEYWORDS, PRICE_KEYWORDS, TRUST_KEYWORDS


def contextual_followup_intents(state: AgentState) -> list[dict[str, Any]]:
    if not is_store_city_followup(state):
        return []
    return [store_city_followup_intent(state)]


def is_store_city_followup(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    if not extract_city(content):
        return False
    if any(term in content for term in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS):
        return False
    if any(term in content for term in ["项目", "价格", "多少钱", "适合", "效果", "做什么", "能解决"]):
        return False
    recent = recent_conversation_text(state, limit=6)
    store_context_terms = ["门店", "地址", "哪里", "哪家", "更方便", "城市", "区域", "附近", "导航", "停车", "店信息"]
    return any(term in recent for term in store_context_terms)


def store_city_followup_intent(state: AgentState) -> dict[str, Any]:
    content = state.get("normalized_content") or ""
    city = extract_city(content) or content.strip()
    preference = store_location_preference_from_context(state)
    query = " ".join(part for part in [city, preference] if part).strip()
    known_info = [f"客户补充位置：{city}"]
    if preference:
        known_info.append(f"客户门店偏好：{preference}")
    return {
        "intent": "store_inquiry",
        "skill": "store",
        "priority": 1,
        "reason": "客户用城市或区域短句承接上一轮门店查询",
        "known_info": known_info,
        "missing_info": [],
        "reply_goal": "根据客户补充的城市或区域查询门店列表；如果上下文有位置偏好，需要直接给出优先推荐门店和理由。",
        "should_ask": False,
        "tool_plan": [
            {
                "name": "store_lookup",
                "query": query or city,
                "purpose": "按客户补充城市或区域以及上下文位置偏好查询门店",
            }
        ],
    }


def store_location_preference_from_context(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    preference = store_location_preference_from_text(content)
    if preference:
        return preference
    return store_location_preference_from_text(recent_conversation_text(state, limit=6))


def store_location_preference_from_text(text: str) -> str:
    if any(term in text for term in ["机场附近", "机场周边", "离机场近", "机场近", "高崎机场", "厦门机场", "机场"]):
        return "机场附近"
    if any(term in text for term in ["火车站附近", "离火车站近", "高铁站附近"]):
        return "火车站附近"
    return ""
