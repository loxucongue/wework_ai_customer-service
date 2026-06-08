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
    is_pre_visit_only_question,
    needs_real_order_lookup,
)
from app.graph.planner_dispute_signals import (
    has_effect_dispute,
    has_fee_or_refund_dispute,
    is_soft_fee_concern,
    is_deposit_rule_question,
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
    signup_intent = any(term in content for term in ["报名", "先报名", "帮我登记", "登记一下", "留个名额", "留名额", "保留优惠", "先保留"])
    pre_service_effect_concern = is_pre_service_effect_concern(content)
    case_request = has_case_request(content)
    project_process = has_project_process_question(content)
    ad_price_check = has_ad_price_check(content)
    if any(term in content for term in ["车费报销", "报销车费", "包接送", "接送吗", "接送服务", "包车接送"]):
        items.append(
            {
                "intent": "store_inquiry",
                "skill": "store",
                "priority": 1,
                "reason": "客户在问到店接送或车费报销，属于到店配套说明，先直接回答，不需要回到开场或项目咨询。",
                "tool_plan": [{"name": "no_tool", "purpose": "车费报销和接送口径可直接答复。"}],
            }
        )
        return dedupe_intents(items)
    if is_soft_fee_concern(content):
        items.append(
            {
                "intent": "trust_issue",
                "skill": "trust_build",
                "priority": 1,
                "reason": "客户在问到店乱收/隐形消费等透明度问题，先做客服端口径解释，不升级为投诉或预约流程",
            }
        )
        return dedupe_intents(items)
    if is_service_response_complaint(content):
        items.append(
            {
                "intent": "emotion_chat",
                "skill": "direct_reply",
                "priority": 1,
                "reason": "客户在反馈回复慢或等待体验不佳，需要先道歉并承接当前诉求",
            }
        )
    if is_pre_visit_only_question(content):
        items.append(
            {
                "intent": "emotion_chat",
                "skill": "direct_reply",
                "priority": 1,
                "reason": "客户询问到店前准备事项，直接回答准备口径，不进入项目、门店或预约查询",
                "tool_plan": [{"name": "no_tool", "purpose": "到店准备常见问题无需实时工具"}],
            }
        )
    if needs_real_order_lookup(content):
        items.append({"intent": "human_request", "skill": "handoff", "priority": 0, "reason": "订单、付款或到账状态需要真实系统数据核实"})
    if has_fee_or_refund_dispute(content):
        items.append({"intent": "complaint_refund", "skill": "handoff", "priority": 0, "reason": "费用、退款或门店收费口径争议"})
    if is_deposit_rule_question(content):
        items.append(
            {
                "intent": "price_inquiry",
                "skill": "price_consult",
                "priority": 1,
                "reason": "客户询问定金、预约金或10元规则，先解释规则口径，不进入广告价格或可约时间查询",
                "tool_plan": [{"name": "no_tool", "purpose": "预约金规则咨询无需实时工具"}],
            }
        )
    if image_info.get("has_image"):
        image_intent = str(image_info.get("image_intent") or "")
        suggested_route = str(image_info.get("suggested_route") or "")
        if image_intent == "after_sales" or suggested_route == "SF12_after_sales":
            items.append({"intent": "after_sales", "skill": "after_sales", "priority": 1, "reason": "图片售后反馈"})
        elif image_intent == "competitor_compare" or suggested_route == "SF5_competitor_response":
            items.append({"intent": "competitor_compare", "skill": "competitor", "priority": 1, "reason": "图片竞品/报价咨询"})
        elif image_intent == "campaign_inquiry" or suggested_route == "SF8_campaign_push":
            items.append({"intent": "campaign_inquiry", "skill": "price_consult", "priority": 1, "reason": "图片为活动券、广告图或优惠素材，优先核对活动/价格口径"})
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
    elif signup_intent and not ad_price_check:
        items.append({"intent": "appointment_intent", "skill": "appointment", "priority": 3, "reason": "客户明确表达报名、登记或保留名额意向，应直接推进预约登记"})
    elif any(word in content for word in APPOINTMENT_KEYWORDS) and not ad_price_check:
        items.append({"intent": "appointment_intent", "skill": "appointment", "priority": 3, "reason": "预约或到店意向"})
    if case_request:
        items.append({"intent": "case_request", "skill": "project_consult", "priority": 3, "reason": "案例或效果对比诉求"})
    if project_process:
        items.append({"intent": "project_process", "skill": "project_consult", "priority": 3, "reason": "项目流程或时长咨询"})
    if has_project_consult_intent(content) or has_generic_project_request(content) or _has_explicit_improvement_need(content):
        items.append({"intent": "project_inquiry", "skill": "project_consult", "priority": 4, "reason": "项目咨询或普通咨询"})
    elif not items:
        items.append({"intent": "emotion_chat", "skill": "direct_reply", "priority": 9, "reason": "普通问候或低信息承接，无需进入业务知识库"})
    return dedupe_intents(items)


def _has_explicit_improvement_need(content: str) -> bool:
    text = str(content or "")
    if not text:
        return False
    need_terms = ["黑色素", "淡斑", "祛斑", "色沉", "暗沉", "毛孔", "痘印", "松弛", "细纹", "提亮"]
    ask_terms = ["改善", "变化", "效果", "能不能", "可以吗", "能做", "能看到", "有没有用", "有没有效果", "真的能"]
    return any(term in text for term in need_terms) and any(term in text for term in ask_terms)
