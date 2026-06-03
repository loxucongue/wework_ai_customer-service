from __future__ import annotations

from typing import Any

from app.graph.planner_content_signals import (
    has_ad_price_check,
    has_advantage_question,
    has_appointment_record_query,
    has_campaign_inquiry,
    has_case_request,
    has_effect_guarantee_request,
    has_generic_project_request,
    has_price_objection,
    has_project_consult_intent,
    has_project_process_question,
    has_store_inquiry,
    is_identity_question,
    is_service_response_complaint,
    needs_real_order_lookup,
)
from app.graph.planner_dispute_signals import (
    has_effect_dispute,
    has_fee_or_refund_dispute,
    is_pre_service_effect_concern,
)
from app.graph.planner_intent_meta import dedupe_intents
from app.policies.constants import (
    AFTER_SALES_KEYWORDS,
    APPOINTMENT_KEYWORDS,
    COMPETITOR_KEYWORDS,
    PRICE_KEYWORDS,
    TRUST_KEYWORDS,
)


def detect_intents(content: str, image_info: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    image_info = image_info or {}
    pre_service_effect_concern = is_pre_service_effect_concern(content)
    case_request = has_case_request(content)
    project_process = has_project_process_question(content)
    ad_price_check = has_ad_price_check(content)
    if is_service_response_complaint(content):
        items.append(
            {
                "intent": "emotion_chat",
                "skill": "direct_reply",
                "priority": 1,
                "reason": "客户在反馈回复慢或等待体验不佳，需要先道歉并承接当前诉求",
            }
        )
    if needs_real_order_lookup(content):
        items.append({"intent": "human_request", "skill": "handoff", "priority": 0, "reason": "订单、付款或到账状态需要真实系统数据核实"})
    if has_fee_or_refund_dispute(content):
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
    if is_identity_question(content):
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户询问身份和服务承接方式"})
    if has_effect_guarantee_request(content):
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户要求效果保证或一次见效承诺"})
    if pre_service_effect_concern:
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "效果或被坑顾虑"})
    if any(word in content for word in COMPETITOR_KEYWORDS):
        items.append({"intent": "competitor_compare", "skill": "competitor", "priority": 2, "reason": "竞品或外部报价对比"})
    if has_advantage_question(content):
        items.append({"intent": "trust_issue", "skill": "trust_build", "priority": 2, "reason": "询问品牌或服务优势"})
    if has_price_objection(content):
        items.append({"intent": "price_inquiry", "skill": "price_consult", "priority": 2, "reason": "价格异议或议价"})
    elif ad_price_check:
        items.append({"intent": "ad_price_check", "skill": "price_consult", "priority": 2, "reason": "广告价、预约金或收费口径核对"})
    elif has_campaign_inquiry(content):
        items.append({"intent": "campaign_inquiry", "skill": "price_consult", "priority": 2, "reason": "活动或优惠咨询"})
    elif any(word in content for word in PRICE_KEYWORDS):
        items.append({"intent": "price_inquiry", "skill": "price_consult", "priority": 2, "reason": "价格咨询"})
    if any(word in content for word in AFTER_SALES_KEYWORDS) and not pre_service_effect_concern and not case_request:
        items.append({"intent": "after_sales", "skill": "after_sales", "priority": 2, "reason": "售后或恢复问题"})
    if has_effect_dispute(content):
        items.append({"intent": "complaint_refund", "skill": "handoff", "priority": 0, "reason": "效果不满或纠纷倾向"})
    if has_store_inquiry(content):
        items.append({"intent": "store_inquiry", "skill": "store", "priority": 3, "reason": "门店地址或路线咨询"})
    if has_appointment_record_query(content) and not ad_price_check:
        items.append({"intent": "appointment_confirm", "skill": "appointment", "priority": 3, "reason": "查询已有预约记录"})
    elif any(word in content for word in APPOINTMENT_KEYWORDS) and not ad_price_check:
        items.append({"intent": "appointment_intent", "skill": "appointment", "priority": 3, "reason": "预约或到店意向"})
    if case_request:
        items.append({"intent": "case_request", "skill": "project_consult", "priority": 3, "reason": "案例或效果对比诉求"})
    if project_process:
        items.append({"intent": "project_process", "skill": "project_consult", "priority": 3, "reason": "项目流程或时长咨询"})
    if has_project_consult_intent(content) or has_generic_project_request(content):
        items.append({"intent": "project_inquiry", "skill": "project_consult", "priority": 4, "reason": "项目咨询或普通咨询"})
    elif not items:
        items.append({"intent": "emotion_chat", "skill": "direct_reply", "priority": 9, "reason": "普通问候或低信息承接，无需进入业务知识库"})
    return dedupe_intents(items)
