from __future__ import annotations

import re
from typing import Any

from app.graph.planner_dispute_signals import recent_conversation_text
from app.graph.planner_intent_meta import extract_city
from app.graph.state import AgentState
from app.graph.store_anchor import current_store_anchor_from_state
from app.policies.constants import AFTER_SALES_KEYWORDS, COMPETITOR_KEYWORDS, PRICE_KEYWORDS, SALES_TALK_KB_NAME, TRUST_KEYWORDS


def contextual_followup_intents(state: AgentState) -> list[dict[str, Any]]:
    if is_store_city_followup(state):
        return [store_city_followup_intent(state)]
    if is_store_fact_send_followup(state):
        return [store_fact_send_followup_intent(state)]
    if is_store_hours_followup(state):
        return [store_hours_followup_intent(state)]
    if is_store_recommendation_followup(state):
        return [store_recommendation_followup_intent(state)]
    return []


def is_store_city_followup(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    if not _extract_location_followup_value(content):
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
    location_text = _extract_location_followup_value(content) or content.strip()
    city = _current_city_from_text(location_text) or extract_city(location_text) or location_text
    preference = store_location_preference_from_context(state)
    query_base = location_text if _has_explicit_location_reference(location_text) or "区" in location_text else city
    query = " ".join(part for part in [query_base, preference] if part).strip()
    known_info = [f"客户补充位置：{location_text or city}"]
    if preference:
        known_info.append(f"客户门店偏好：{preference}")
    return {
        "intent": "store_inquiry",
        "skill": "store",
        "priority": 1,
        "reason": "客户用城市或区域短句承接上一轮门店查询。",
        "known_info": known_info,
        "missing_info": [],
        "reply_goal": "根据客户补充的城市、区域或地标查询更方便的门店；如果上下文有位置偏好，需要直接给出优先推荐门店和理由。",
        "should_ask": False,
        "tool_plan": [
            {
                "name": "kb_search",
                "kb_name": SALES_TALK_KB_NAME,
                "query": query or location_text or city,
                "purpose": "检索门店匹配、位置补位和推荐门店的承接策略。",
            },
            {
                "name": "store_lookup",
                "query": query or location_text or city,
                "purpose": "按客户补充城市、区域或地标以及上下文位置偏好查询门店。",
            }
        ],
    }


def is_store_recommendation_followup(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    readable_terms = [
        "推荐一家",
        "推荐一个",
        "最近的一家",
        "最近一个",
        "近一点",
        "近点",
        "哪家近",
        "哪家方便",
        "离我近",
        "近吗",
        "近不近",
        "直接推荐",
        "帮我选",
    ]
    if not any(term in content for term in readable_terms):
        return False
    if any(term in content for term in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS):
        return False
    recent = recent_conversation_text(state, limit=8)
    store_context_terms = [
        "门店",
        "地址",
        "哪里",
        "哪家",
        "附近",
        "导航",
        "停车",
        "店信息",
        "机场",
        "火车站",
        "高铁站",
        "城市",
        "区域",
        "更方便",
        "近一点",
        "推荐",
        "厦门",
        "上海",
        "西安",
        "重庆",
        "广州",
    ]
    return any(term in recent for term in store_context_terms) or bool(current_store_anchor_from_state(state)) or bool(
        state.get("customer_basic_info", {}).get("city")
    )


def is_store_fact_send_followup(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    if not any(term in content for term in ["发给我", "发我", "发一下", "发来", "把这家", "这家店"]):
        return False
    if any(term in content for term in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS):
        return False
    recent = recent_conversation_text(state, limit=8)
    return any(term in recent for term in ["推荐门店", "优先推荐", "门店", "地址", "导航", "停车", "百星", "思明", "浦东", "徐汇"])


def store_fact_send_followup_intent(state: AgentState) -> dict[str, Any]:
    content = state.get("normalized_content") or ""
    recent = recent_conversation_text(state, limit=8)
    city = extract_city(recent) or _known_city_from_state(state) or ""
    anchor = current_store_anchor_from_state(state)
    query = " ".join(part for part in [city, anchor, content] if part).strip()
    known_info = ["客户在承接刚刚推荐的门店，明确要发这家店资料。"]
    if city:
        known_info.append(f"客户所在城市：{city}")
    if anchor:
        known_info.append(f"当前默认门店：{anchor}")
    return {
        "intent": "store_inquiry",
        "skill": "store",
        "priority": 1,
        "reason": "客户在承接上一轮已推荐的门店，要求直接发送该门店资料。",
        "known_info": known_info,
        "missing_info": [] if anchor or city else ["城市或门店"],
        "reply_goal": "默认按刚刚推荐的门店直接发送地址、导航或停车信息，不再重新列门店。",
        "should_ask": not bool(anchor or city),
        "tool_plan": [
            {
                "name": "kb_search",
                "kb_name": SALES_TALK_KB_NAME,
                "query": query or content,
                "purpose": "检索门店资料发送和直接承接的回复节奏。",
            },
            {
                "name": "store_lookup",
                "query": query or content,
                "purpose": "按已推荐门店和当前请求直接返回该门店地址、导航或停车信息。",
            },
        ],
    }


def is_store_hours_followup(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    if not any(term in content for term in ["几点上班", "几点开门", "几点营业", "营业时间", "几点开", "几点关"]):
        return False
    if any(term in content for term in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS):
        return False
    recent = recent_conversation_text(state, limit=8)
    return any(term in recent for term in ["门店", "地址", "推荐", "百星", "思明", "浦东", "徐汇", "报名", "明天上午", "预约"])


def store_hours_followup_intent(state: AgentState) -> dict[str, Any]:
    content = state.get("normalized_content") or ""
    recent = recent_conversation_text(state, limit=8)
    city = extract_city(recent) or _known_city_from_state(state) or ""
    anchor = current_store_anchor_from_state(state)
    query = " ".join(part for part in [city, anchor, content] if part).strip()
    known_info = ["客户在追问具体门店营业时间。"]
    if city:
        known_info.append(f"客户所在城市：{city}")
    if anchor:
        known_info.append(f"当前默认门店：{anchor}")
    return {
        "intent": "store_inquiry",
        "skill": "store",
        "priority": 1,
        "reason": "客户在门店或预约上下文里追问营业时间，应直接按当前门店回答。",
        "known_info": known_info,
        "missing_info": [] if anchor or city else ["城市或门店"],
        "reply_goal": "按当前门店直接回答营业时间，不要回到普通问候。",
        "should_ask": not bool(anchor or city),
        "tool_plan": [
            {
                "name": "kb_search",
                "kb_name": SALES_TALK_KB_NAME,
                "query": query or content,
                "purpose": "检索门店营业时间问答和单一事实收口话术。",
            },
            {
                "name": "store_lookup",
                "query": query or content,
                "purpose": "按当前门店或城市查询营业时间和状态。",
            },
        ],
    }


def store_recommendation_followup_intent(state: AgentState) -> dict[str, Any]:
    content = state.get("normalized_content") or ""
    recent = recent_conversation_text(state, limit=8)
    current_city = _current_city_from_text(content)
    city = current_city or extract_city(recent) or _known_city_from_state(state) or ""
    preference = store_location_preference_from_context(state)
    anchor = ""
    if not current_city and not _has_explicit_location_reference(content):
        anchor = current_store_anchor_from_state(state)
    query = " ".join(part for part in [city, preference, anchor, content] if part).strip()
    known_info: list[str] = []
    if city:
        known_info.append(f"客户所在城市：{city}")
    if preference:
        known_info.append(f"客户门店偏好：{preference}")
    if anchor:
        known_info.append(f"最近已命中的门店：{anchor}")
    known_info.append("客户希望直接推荐更近或更方便的一家门店")
    return {
        "intent": "store_inquiry",
        "skill": "store",
        "priority": 1,
        "reason": "客户承接上一轮门店查询，要求直接推荐更近或更方便的门店。",
        "known_info": known_info,
        "missing_info": [] if city else ["城市或区域"],
        "reply_goal": "结合最近门店上下文和位置偏好，直接推荐优先门店并说明理由；如果缺城市再只问城市。",
        "should_ask": not bool(city),
        "tool_plan": [
            {
                "name": "kb_search",
                "kb_name": SALES_TALK_KB_NAME,
                "query": query,
                "purpose": "检索门店推荐、地址发送和下一步推进的承接策略。",
            },
            {
                "name": "store_lookup",
                "query": query,
                "purpose": "按最近门店上下文和客户位置偏好推荐门店。",
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
    if any(term in text for term in ["机场附近", "机场周边", "离机场近", "机场近点", "高崎机场", "浦东机场", "机场"]):
        return "机场附近"
    if any(term in text for term in ["火车站附近", "离火车站近", "高铁站附近"]):
        return "火车站附近"
    if any(term in text for term in ["机场附近", "机场周边", "离机场近", "机场近点", "高崎机场", "浦东机场", "机场"]):
        return "机场附近"
    if any(term in text for term in ["火车站附近", "离火车站近", "高铁站附近"]):
        return "火车站附近"
    return ""


def _current_city_from_text(text: str) -> str:
    current = extract_city(text)
    if current:
        return current
    readable_airport_map = {
        "浦东机场": "上海",
        "虹桥机场": "上海",
        "高崎机场": "厦门",
        "厦门机场": "厦门",
        "中贸": "西安",
        "小寨": "西安",
        "未央": "西安",
        "碑林": "西安",
        "枋湖": "厦门",
        "湖里": "厦门",
        "浦东": "上海",
        "虹口": "上海",
        "嘉定": "上海",
    }
    for hint, city in readable_airport_map.items():
        if hint in text:
            return city
    airport_map = {
        "浦东机场": "上海",
        "虹桥机场": "上海",
        "高崎机场": "厦门",
        "厦门机场": "厦门",
        "中贸": "西安",
        "小寨": "西安",
        "未央": "西安",
        "碑林": "西安",
        "枋湖": "厦门",
        "湖里": "厦门",
        "浦东": "上海",
        "虹口": "上海",
        "嘉定": "上海",
    }
    for hint, city in airport_map.items():
        if hint in text:
            return city
    return ""


def _extract_location_followup_value(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    current = _current_city_from_text(value)
    if current:
        return current
    for prefix in ["我在", "人在", "目前在", "现在在", "住在", "我住", "在"]:
        if value.startswith(prefix):
            value = value[len(prefix):].strip()
            break
    for suffix in ["这边", "这儿", "附近", "这块", "这一片", "周边"]:
        if value.endswith(suffix):
            value = value[: -len(suffix)].strip()
    value = value.strip(" ，。！？?~～")
    if re.fullmatch(r"[\u4e00-\u9fa5]{2,8}(?:市|区|县)?", value):
        return value
    return ""


def _has_explicit_location_reference(text: str) -> bool:
    readable_terms = [
        "机场",
        "火车站",
        "高铁站",
        "车站",
        "附近",
        "浦东",
        "虹桥",
        "高崎",
        "枋湖",
        "湖里",
        "中贸",
        "小寨",
        "未央",
        "碑林",
    ]
    if any(term in text for term in readable_terms):
        return True
    return any(
        term in text
        for term in [
            "机场",
            "火车站",
            "高铁站",
            "车站",
            "附近",
            "浦东",
            "虹桥",
            "高崎",
            "枋湖",
            "湖里",
            "中贸",
            "小寨",
            "未央",
            "碑林",
        ]
    )


def _known_city_from_state(state: AgentState) -> str:
    basic = state.get("customer_basic_info") or {}
    if isinstance(basic, dict):
        city = str(basic.get("city") or "").strip()
        if city:
            return city
    for event in reversed(state.get("history_events", [])[-10:]):
        if not isinstance(event, dict):
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        city = str(facts.get("city") or "").strip()
        if city:
            return city
    return ""
