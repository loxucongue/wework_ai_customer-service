from __future__ import annotations

from typing import Any

from app.graph.planner_content_signals import (
    has_ad_price_check as _has_ad_price_check,
    has_advantage_question as _has_advantage_question,
    has_appointment_record_query as _has_appointment_record_query,
    has_business_signal as _has_business_signal,
    has_campaign_inquiry as _has_campaign_inquiry,
    has_case_request as _has_case_request,
    has_current_after_sales_signal as _has_current_after_sales_signal,
    has_effect_guarantee_request as _has_effect_guarantee_request,
    has_generic_project_request as _has_generic_project_request,
    has_price_objection as _has_price_objection,
    has_project_consult_intent as _has_project_consult_intent,
    has_project_process_question as _has_project_process_question,
    has_recent_action_context as _has_recent_action_context,
    has_store_inquiry as _has_store_inquiry,
    is_ad_source_only_project_question as _is_ad_source_only_project_question,
    is_identity_question as _is_identity_question,
    is_low_information_content as _is_low_information_content,
    is_pre_visit_only_question as _is_pre_visit_only_question,
    is_service_response_complaint as _is_service_response_complaint,
    needs_real_order_lookup as _needs_real_order_lookup,
)
from app.graph.planner_dispute_signals import (
    complaint_terms as _complaint_terms,
    has_effect_dispute as _has_effect_dispute,
    has_fee_or_refund_dispute as _has_fee_or_refund_dispute,
    has_recent_complaint_context as _has_recent_complaint_context,
    has_recent_competitor_context as _has_recent_competitor_context,
    is_pre_service_effect_concern as _is_pre_service_effect_concern,
    is_soft_fee_concern as _is_soft_fee_concern,
    model_intent_has_current_trigger as _model_intent_has_current_trigger,
    recent_conversation_text as _recent_conversation_text,
    severe_after_sales_terms as _severe_after_sales_terms,
)
from app.graph.planner_intent_meta import (
    dedupe_intents as _dedupe_intents,
    dedupe_strings as _dedupe_strings,
    known_info_from_state as _known_info_from_state,
    merge_intent_details as _merge_intent_details,
    missing_info_from_state as _missing_info_from_state,
    must_ask_for_intent as _must_ask_for_intent,
    reply_goal_for_intent as _reply_goal_for_intent,
)
from app.graph.planner_prompt import planner_messages_for_model, planner_model_tier, should_use_model_planner
from app.graph.planner_store_followup import (
    contextual_followup_intents as _contextual_followup_intents,
    is_store_city_followup as _is_store_city_followup,
    store_city_followup_intent as _store_city_followup_intent,
)
from app.graph.planner_tool_plan import (
    default_tool_plan as _default_tool_plan,
    needs_default_tool_plan as _needs_default_tool_plan,
    normalize_tool_plan_for_intent as _normalize_tool_plan_for_intent,
)
from app.graph.planner_validation import validated_planner_intents
from app.graph.state import AgentState
from app.policies.constants import (
    ADVANTAGE_KEYWORDS,
    AFTER_SALES_KEYWORDS,
    APPOINTMENT_KEYWORDS,
    CAMPAIGN_KEYWORDS,
    COMPETITOR_KEYWORDS,
    PRICE_KEYWORDS,
    PRICE_OBJECTION_KEYWORDS,
    PROJECT_KEYWORDS,
    SEVERE_AFTER_SALES_KEYWORDS,
    STORE_KEYWORDS,
    TRUST_KEYWORDS,
)


def detect_intents(content: str, image_info: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    image_info = image_info or {}
    pre_service_effect_concern = _is_pre_service_effect_concern(content)
    case_request = _has_case_request(content)
    project_process = _has_project_process_question(content)
    ad_price_check = _has_ad_price_check(content)
    if _is_service_response_complaint(content):
        items.append(
            {
                "intent": "emotion_chat",
                "skill": "direct_reply",
                "priority": 1,
                "reason": "客户在反馈回复慢或等待体验不佳，需要先道歉并承接当前诉求",
            }
        )
    if _needs_real_order_lookup(content):
        items.append({"intent": "human_request", "skill": "handoff", "priority": 0, "reason": "订单、付款或到账状态需要真实系统数据核实"})
    if _has_fee_or_refund_dispute(content):
        items.append({"intent": "complaint_refund", "skill": "handoff", "priority": 0, "reason": "费用、退款或门店收费口径争议"})
    if image_info.get("has_image"):
        image_intent = str(image_info.get("image_intent") or "")
        suggested_route = str(image_info.get("suggested_route") or "")
        if image_intent == "after_sales" or suggested_route == "SF12_after_sales":
            items.append({"intent": "after_sales", "skill": "after_sales", "priority": 1, "reason": "图片售后反馈"})
        elif image_intent == "competitor_compare" or suggested_route == "SF5_competitor_response":
            items.append({"intent": "competitor_compare", "skill": "competitor", "priority": 1, "reason": "图片竞品/报价咨询"})
        elif image_intent == "store_inquiry" or suggested_route == "SF6_store_match":
            items.append({"intent": "store_inquiry", "skill": "store", "priority": 1, "reason": "图片门店/地图咨询"})
        elif image_intent == "trust_issue" or suggested_route == "SF10_trust_build":
            items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "图片资质/产品信任咨询"})
        elif image_intent == "human_request" or suggested_route == "HUMAN_HANDOFF":
            items.append({"intent": "human_request", "skill": "handoff", "priority": 0, "reason": "图片包含高风险或需专业协助内容"})
        else:
            items.append({"intent": "image_inquiry", "skill": "project_consult", "priority": 1, "reason": "图片面诊咨询"})
    if any(word in content for word in TRUST_KEYWORDS):
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "信任或正规性顾虑"})
    if _is_identity_question(content):
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户询问身份和服务承接方式"})
    if _has_effect_guarantee_request(content):
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户要求效果保证或一次见效承诺"})
    if pre_service_effect_concern:
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "效果或被坑顾虑"})
    if any(word in content for word in COMPETITOR_KEYWORDS):
        items.append({"intent": "competitor_compare", "skill": "competitor", "priority": 2, "reason": "竞品或外部报价对比"})
    if _has_advantage_question(content):
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 2, "reason": "询问品牌或服务优势"})
    if _has_price_objection(content):
        items.append({"intent": "price_inquiry", "skill": "price_consult", "priority": 2, "reason": "价格异议或议价"})
    elif ad_price_check:
        items.append({"intent": "ad_price_check", "skill": "price_consult", "priority": 2, "reason": "广告价、预约金或收费口径核对"})
    elif _has_campaign_inquiry(content):
        items.append({"intent": "campaign_inquiry", "skill": "price_consult", "priority": 2, "reason": "活动或优惠咨询"})
    elif any(word in content for word in PRICE_KEYWORDS):
        items.append({"intent": "price_inquiry", "skill": "price_consult", "priority": 2, "reason": "价格咨询"})
    if any(word in content for word in AFTER_SALES_KEYWORDS) and not pre_service_effect_concern and not case_request:
        items.append({"intent": "after_sales", "skill": "after_sales", "priority": 2, "reason": "售后或恢复问题"})
    if _has_effect_dispute(content):
        items.append({"intent": "complaint_refund", "skill": "handoff", "priority": 0, "reason": "效果不满或纠纷倾向"})
    if _has_store_inquiry(content):
        items.append({"intent": "store_inquiry", "skill": "store", "priority": 3, "reason": "门店地址或路线咨询"})
    if _has_appointment_record_query(content) and not ad_price_check:
        items.append({"intent": "appointment_confirm", "skill": "appointment", "priority": 3, "reason": "查询已有预约记录"})
    elif any(word in content for word in APPOINTMENT_KEYWORDS) and not ad_price_check:
        items.append({"intent": "appointment_intent", "skill": "appointment", "priority": 3, "reason": "预约或到店意向"})
    if case_request:
        items.append({"intent": "case_request", "skill": "project_consult", "priority": 3, "reason": "案例或效果对比诉求"})
    if project_process:
        items.append({"intent": "project_process", "skill": "project_consult", "priority": 3, "reason": "项目流程或时长咨询"})
    if _has_project_consult_intent(content) or _has_generic_project_request(content):
        items.append({"intent": "project_inquiry", "skill": "project_consult", "priority": 4, "reason": "项目咨询或普通咨询"})
    elif not items:
        items.append({"intent": "emotion_chat", "skill": "direct_reply", "priority": 9, "reason": "普通问候或低信息承接，无需进入业务知识库"})
    return _dedupe_intents(items)


def merge_intents(state: AgentState, rule_items: list[dict[str, Any]], model_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    positions: dict[str, int] = {}

    def add(item: dict[str, Any]) -> None:
        intent = str(item.get("intent") or "")
        if not intent:
            return
        if intent in seen:
            existing = merged[positions[intent]]
            merged[positions[intent]] = _merge_intent_details(existing, item)
            return
        seen.add(intent)
        positions[intent] = len(merged)
        merged.append(item)

    for item in rule_items:
        add(item)
    for item in model_items:
        intent = str(item.get("intent") or "")
        if intent in seen or _model_intent_has_current_trigger(state, intent):
            add(item)
    for item in _contextual_followup_intents(state):
        add(item)
    return merged[:3] or _dedupe_intents(rule_items + model_items)


def enrich_intents_with_tool_plan(state: AgentState, intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for item in intents:
        copied = dict(item)
        copied.setdefault("known_info", _known_info_from_state(state, copied))
        copied.setdefault("missing_info", _missing_info_from_state(state, copied))
        copied.setdefault("reply_goal", _reply_goal_for_intent(copied))
        copied.setdefault("should_ask", bool(copied.get("missing_info")) and _must_ask_for_intent(copied))
        if _needs_default_tool_plan(copied.get("skill", ""), copied.get("tool_plan")):
            copied["tool_plan"] = _default_tool_plan(state, copied)
        copied["tool_plan"] = _normalize_tool_plan_for_intent(state, copied)
        enriched.append(copied)
    return enriched


def filter_spurious_intents(state: AgentState, intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    image_info = state.get("image_info") or {}
    content = state.get("normalized_content") or ""
    has_current_competitor = any(word in content for word in COMPETITOR_KEYWORDS)
    has_current_trust = any(word in content for word in TRUST_KEYWORDS)
    has_price_objection = _has_price_objection(content)
    pre_service_effect_concern = _is_pre_service_effect_concern(content)
    pre_visit_only = _is_pre_visit_only_question(content)
    if _is_store_city_followup(state):
        kept = [item for item in intents if item.get("intent") not in {"project_inquiry", "price_inquiry", "campaign_inquiry", "emotion_chat"}]
        if not any(item.get("intent") == "store_inquiry" for item in kept):
            kept.append(_store_city_followup_intent(state))
        return _dedupe_intents(kept)
    if _is_low_information_content(content) and not _has_recent_action_context(state):
        return [
            {
                "intent": "emotion_chat",
                "skill": "direct_reply",
                "priority": 9,
                "reason": "普通问候或低信息承接，无需进入业务流程",
            }
        ]
    if _is_service_response_complaint(content):
        return [
            {
                "intent": "emotion_chat",
                "skill": "direct_reply",
                "priority": 1,
                "reason": "客户在反馈回复慢或等待体验不佳，需要先道歉并承接当前诉求",
            }
        ]
    if _has_fee_or_refund_dispute(content):
        intents = [item for item in intents if item.get("intent") != "store_inquiry"]
        if not any(item.get("intent") == "complaint_refund" for item in intents):
            intents.append({"intent": "complaint_refund", "skill": "handoff", "priority": 0, "reason": "费用、退款或门店收费口径争议"})
    if _has_effect_dispute(content):
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
    elif _is_soft_fee_concern(content):
        intents = [
            item
            for item in intents
            if item.get("intent") not in {"complaint_refund", "store_inquiry", "appointment_intent", "appointment_confirm"}
        ]
        if not any(item.get("intent") == "trust_issue" for item in intents):
            intents.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "收费透明度顾虑"})
    if _has_effect_guarantee_request(content):
        intents = [item for item in intents if item.get("intent") != "price_inquiry"]
        if not any(item.get("intent") == "trust_issue" for item in intents):
            intents.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户要求效果保证或一次见效承诺"})
    if _is_ad_source_only_project_question(content):
        intents = [
            item
            for item in intents
            if item.get("intent") not in {"campaign_inquiry", "ad_price_check", "price_inquiry"}
            and item.get("skill") != "price_consult"
        ]
        if _has_project_process_question(content) and not any(item.get("intent") == "project_process" for item in intents):
            intents.append({"intent": "project_process", "skill": "project_consult", "priority": 2, "reason": "广告只是信息来源，客户实际询问项目流程或时长"})
        elif not any(item.get("intent") == "project_inquiry" for item in intents):
            intents.append({"intent": "project_inquiry", "skill": "project_consult", "priority": 2, "reason": "广告只是信息来源，客户实际询问项目内容"})
    if _has_advantage_question(content):
        intents = [item for item in intents if item.get("intent") != "store_inquiry"]
        target_skill = "competitor" if _has_recent_competitor_context(state) else "trust_build"
        target_intent = "competitor_compare" if target_skill == "competitor" else "trust_issue"
        if not any(item.get("intent") == target_intent for item in intents):
            intents.append({"intent": target_intent, "skill": target_skill, "priority": 2, "reason": "客户询问优势或差异点"})
    if has_current_trust and not _has_store_inquiry(content):
        intents = [item for item in intents if item.get("intent") != "store_inquiry"]
        if not any(item.get("intent") == "trust_issue" for item in intents):
            intents.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户当前表达资质或正规性顾虑"})
    if has_price_objection:
        intents = [item for item in intents if item.get("intent") != "project_inquiry"]
        if not any(item.get("intent") == "price_inquiry" for item in intents):
            intents.append({"intent": "price_inquiry", "skill": "price_consult", "priority": 2, "reason": "价格异议或议价"})
    if pre_service_effect_concern:
        intents = [item for item in intents if item.get("intent") != "after_sales"]
    if pre_visit_only and not _has_current_after_sales_signal(content):
        intents = [item for item in intents if item.get("intent") != "after_sales"]
    if has_current_competitor and not has_current_trust:
        intents = [item for item in intents if item.get("intent") != "trust_issue"]
    if image_info.get("has_image") and image_info.get("image_intent") == "face_consult":
        allowed = {"image_inquiry", "project_inquiry"}
        if _has_recent_complaint_context(state):
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
        if _has_store_inquiry(content):
            allowed.add("store_inquiry")
        if any(word in content for word in AFTER_SALES_KEYWORDS) and not pre_service_effect_concern:
            allowed.add("after_sales")
        filtered = [item for item in intents if item.get("intent") in allowed]
        if filtered:
            return _dedupe_intents(filtered)
    return _dedupe_intents(intents)
