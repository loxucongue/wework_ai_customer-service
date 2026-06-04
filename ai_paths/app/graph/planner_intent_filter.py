from __future__ import annotations

from typing import Any

from app.graph.planner_content_signals import (
    has_ad_price_check,
    has_advantage_question,
    has_current_after_sales_signal,
    has_effect_guarantee_request,
    has_price_objection,
    has_project_process_question,
    has_recent_action_context,
    has_store_inquiry,
    is_ad_source_only_project_question,
    is_low_information_content,
    is_pre_visit_only_question,
    is_service_response_complaint,
)
from app.graph.nodes.memory_usage_policy import is_generic_opening_without_specific_need
from app.graph.planner_dispute_signals import (
    has_effect_dispute,
    has_fee_or_refund_dispute,
    has_recent_complaint_context,
    has_recent_competitor_context,
    is_pre_service_effect_concern,
    is_soft_fee_concern,
)
from app.graph.planner_intent_meta import dedupe_intents
from app.graph.planner_store_followup import is_store_city_followup, store_city_followup_intent
from app.graph.state import AgentState
from app.policies.constants import (
    AFTER_SALES_KEYWORDS,
    APPOINTMENT_KEYWORDS,
    CAMPAIGN_KEYWORDS,
    COMPETITOR_KEYWORDS,
    PRICE_KEYWORDS,
    TRUST_KEYWORDS,
)


def filter_spurious_intents(state: AgentState, intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    image_info = state.get("image_info") or {}
    content = state.get("normalized_content") or ""
    has_current_competitor = any(word in content for word in COMPETITOR_KEYWORDS)
    has_current_trust = any(word in content for word in TRUST_KEYWORDS)
    price_objection = has_price_objection(content)
    pre_service_effect_concern = is_pre_service_effect_concern(content)
    pre_visit_only = is_pre_visit_only_question(content)
    if is_store_city_followup(state):
        kept = [item for item in intents if item.get("intent") not in {"project_inquiry", "price_inquiry", "campaign_inquiry", "emotion_chat"}]
        if not any(item.get("intent") == "store_inquiry" for item in kept):
            kept.append(store_city_followup_intent(state))
        return dedupe_intents(kept)
    if (is_low_information_content(content) and not has_recent_action_context(state)) or is_generic_opening_without_specific_need(content):
        return [
            {
                "intent": "emotion_chat",
                "skill": "direct_reply",
                "priority": 9,
                "reason": "普通问候、低信息承接或泛项目开场，无需进入业务流程",
            }
        ]
    if is_service_response_complaint(content):
        return [
            {
                "intent": "emotion_chat",
                "skill": "direct_reply",
                "priority": 1,
                "reason": "客户在反馈回复慢或等待体验不佳，需要先道歉并承接当前诉求",
            }
        ]
    if has_fee_or_refund_dispute(content):
        intents = [item for item in intents if item.get("intent") != "store_inquiry"]
        if not any(item.get("intent") == "complaint_refund" for item in intents):
            intents.append({"intent": "complaint_refund", "skill": "handoff", "priority": 0, "reason": "费用、退款或门店收费口径争议"})
    if has_effect_dispute(content):
        intents = [
            item
            for item in intents
            if item.get("intent")
            not in {
                "appointment_intent",
                "appointment_confirm",
                "appointment_change",
                "appointment_cancel",
                "store_inquiry",
                "project_inquiry",
                "price_inquiry",
                "campaign_inquiry",
            }
        ]
        if not any(item.get("intent") == "complaint_refund" for item in intents):
            intents.append({"intent": "complaint_refund", "skill": "handoff", "priority": 0, "reason": "效果不满或投诉倾向"})
    elif is_soft_fee_concern(content):
        intents = [
            item
            for item in intents
            if item.get("intent") not in {"complaint_refund", "store_inquiry", "appointment_intent", "appointment_confirm"}
        ]
        if not any(item.get("intent") == "trust_issue" for item in intents):
            intents.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "收费透明度顾虑"})
    if has_effect_guarantee_request(content):
        intents = [item for item in intents if item.get("intent") != "price_inquiry"]
        if not any(item.get("intent") == "trust_issue" for item in intents):
            intents.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户要求效果保证或一次见效承诺"})
    if is_ad_source_only_project_question(content):
        intents = [
            item
            for item in intents
            if item.get("intent") not in {"campaign_inquiry", "ad_price_check", "price_inquiry"}
            and item.get("skill") != "price_consult"
        ]
        if has_project_process_question(content) and not any(item.get("intent") == "project_process" for item in intents):
            intents.append({"intent": "project_process", "skill": "project_consult", "priority": 2, "reason": "广告只是信息来源，客户实际询问项目流程或时长"})
        elif not any(item.get("intent") == "project_inquiry" for item in intents):
            intents.append({"intent": "project_inquiry", "skill": "project_consult", "priority": 2, "reason": "广告只是信息来源，客户实际询问项目内容"})
    if has_advantage_question(content):
        intents = [item for item in intents if item.get("intent") != "store_inquiry"]
        target_skill = "competitor" if has_recent_competitor_context(state) else "trust_build"
        target_intent = "competitor_compare" if target_skill == "competitor" else "trust_issue"
        if not any(item.get("intent") == target_intent for item in intents):
            intents.append({"intent": target_intent, "skill": target_skill, "priority": 2, "reason": "客户询问优势或差异点"})
    if has_current_trust and not has_store_inquiry(content):
        intents = [item for item in intents if item.get("intent") != "store_inquiry"]
        if not any(item.get("intent") == "trust_issue" for item in intents):
            intents.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户当前表达资质或正规性顾虑"})
    if price_objection:
        intents = [item for item in intents if item.get("intent") != "project_inquiry"]
        if not any(item.get("intent") == "price_inquiry" for item in intents):
            intents.append({"intent": "price_inquiry", "skill": "price_consult", "priority": 2, "reason": "价格异议或议价"})
    if pre_service_effect_concern:
        intents = [item for item in intents if item.get("intent") != "after_sales"]
    if pre_visit_only and not has_current_after_sales_signal(content):
        intents = [item for item in intents if item.get("intent") != "after_sales"]
    if has_current_competitor and not has_current_trust:
        intents = [item for item in intents if item.get("intent") != "trust_issue"]
    if image_info.get("has_image") and image_info.get("image_intent") == "face_consult":
        allowed = {"image_inquiry", "project_inquiry"}
        if has_recent_complaint_context(state):
            allowed.add("complaint_refund")
            allowed.add("after_sales")
        if any(word in content for word in PRICE_KEYWORDS):
            allowed.add("price_inquiry")
        if any(word in content for word in CAMPAIGN_KEYWORDS):
            allowed.add("campaign_inquiry")
        if any(word in content for word in APPOINTMENT_KEYWORDS):
            allowed.add("appointment_intent")
        if any(word in content for word in TRUST_KEYWORDS):
            allowed.add("trust_issue")
        if has_store_inquiry(content):
            allowed.add("store_inquiry")
        if any(word in content for word in AFTER_SALES_KEYWORDS) and not pre_service_effect_concern:
            allowed.add("after_sales")
        filtered = [item for item in intents if item.get("intent") in allowed]
        if filtered:
            return dedupe_intents(filtered)
    return dedupe_intents(intents)
