from __future__ import annotations

import re
from typing import Any

from app.graph.planner_intent_meta import (
    dedupe_intents as _dedupe_intents,
    dedupe_strings as _dedupe_strings,
    extract_city as _extract_city,
    known_info_from_state as _known_info_from_state,
    merge_intent_details as _merge_intent_details,
    missing_info_from_state as _missing_info_from_state,
    must_ask_for_intent as _must_ask_for_intent,
    reply_goal_for_intent as _reply_goal_for_intent,
)
from app.graph.planner_prompt import planner_messages_for_model, planner_model_tier, should_use_model_planner
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


def _is_pre_visit_only_question(content: str) -> bool:
    if not content:
        return False
    prep_terms = ["需要带什么", "要带什么", "带什么", "能不能化妆", "可以化妆", "要不要空腹", "需要空腹", "到店流程", "第一次去注意"]
    return any(term in content for term in prep_terms)


def _has_current_after_sales_signal(content: str) -> bool:
    if not content:
        return False
    if any(term in content for term in ["做完", "术后", "恢复", "反黑", "红肿", "流脓", "出血", "疼", "痛", "没效果"]):
        return True
    return False


def _is_service_response_complaint(content: str) -> bool:
    if not content:
        return False
    terms = [
        "为什么这么慢",
        "怎么这么慢",
        "回消息这么慢",
        "回复这么慢",
        "你回消息为什么这么慢",
        "半天不回",
        "等这么久",
        "等半天",
        "没人回",
        "没有人回",
        "消息太慢",
        "回复太慢",
    ]
    return any(term in content for term in terms)


def _has_project_consult_intent(content: str) -> bool:
    if _has_price_objection(content):
        return False
    if not any(word in content for word in PROJECT_KEYWORDS):
        return False
    consult_terms = [
        "适合",
        "效果",
        "原理",
        "恢复",
        "副作用",
        "维持",
        "推荐",
        "方案",
        "怎么弄",
        "怎么做",
        "做什么",
        "能不能做",
        "哪个好",
        "区别",
        "改善",
        "解决",
        "想淡斑",
        "想祛斑",
        "淡化",
        "去掉",
        "去除",
    ]
    return any(term in content for term in consult_terms)


def _has_generic_project_request(content: str) -> bool:
    if not content or _is_low_information_content(content):
        return False
    if any(term in content for term in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS):
        return False
    generic_terms = [
        "了解项目",
        "了解一下项目",
        "项目介绍",
        "有什么项目",
        "有哪些项目",
        "推荐项目",
        "做什么项目",
        "想看项目",
        "想了解项目",
    ]
    return any(term in content for term in generic_terms)


def _is_low_information_content(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return True
    normalized = re.sub(r"[\s,，。.!！?？~～、]+", "", text)
    low_terms = {
        "你好",
        "您好",
        "在吗",
        "在不在",
        "哈喽",
        "hello",
        "hi",
        "嗨",
        "亲",
        "谢谢",
        "好的",
        "好",
        "嗯",
        "收到",
        "明白",
        "知道了",
    }
    if normalized.lower() in low_terms:
        return True
    if len(normalized) <= 2 and not _has_business_signal(text):
        return True
    return False


def _has_business_signal(content: str) -> bool:
    if not content:
        return False
    signal_groups = [
        TRUST_KEYWORDS,
        COMPETITOR_KEYWORDS,
        PRICE_KEYWORDS,
        CAMPAIGN_KEYWORDS,
        AFTER_SALES_KEYWORDS,
        APPOINTMENT_KEYWORDS,
        STORE_KEYWORDS,
        PROJECT_KEYWORDS,
    ]
    if any(any(term in content for term in group) for group in signal_groups):
        return True
    extra_terms = [
        "项目",
        "门店",
        "地址",
        "搬走",
        "还在",
        "换地址",
        "今天",
        "明天",
        "现在过来",
        "现在过去",
        "几点",
        "可以吗",
        "约",
        "斑",
        "痘",
        "毛孔",
        "暗沉",
    ]
    return any(term in content for term in extra_terms)


def _has_recent_action_context(state: AgentState) -> bool:
    recent = _recent_conversation_text(state, limit=8)
    if not recent:
        return False
    action_terms = [
        "预约",
        "约",
        "到店",
        "来店",
        "过来",
        "接待",
        "安排位置",
        "位置",
        "几点",
        "时间",
        "可以吗",
        "门店",
        "地址",
        "搬走",
        "还在",
        "换地址",
    ]
    return any(term in recent for term in action_terms)


def _has_case_request(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["案例", "效果案例", "前后对比", "对比照", "做完效果", "客户做完", "案例效果", "案例展示"])


def _has_project_process_question(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["流程", "操作流程", "怎么操作", "怎么做", "要做多久", "大概要多久", "多久能做完", "时长", "步骤", "过程"])


def _has_ad_price_check(content: str) -> bool:
    if not content:
        return False
    context_terms = ["广告", "直播", "团购", "预约金", "尾款", "隐形收费", "其他收费", "另收费", "包含什么", "包含哪些"]
    price_terms = PRICE_KEYWORDS + ["199", "299", "268", "10元", "定金", "订金"]
    return any(term in content for term in context_terms) and (
        any(term in content for term in price_terms) or bool(re.search(r"\d+\s*元?", content))
    )


def _has_campaign_inquiry(content: str) -> bool:
    if not content:
        return False
    campaign_terms = [
        "活动",
        "优惠",
        "福利",
        "新客活动",
        "节日活动",
        "团购",
        "预约金",
        "尾款",
        "券",
        "广告价",
        "直播价",
    ]
    return any(term in content for term in campaign_terms)


def _is_ad_source_only_project_question(content: str) -> bool:
    if not content:
        return False
    if not any(term in content for term in ["广告", "直播"]):
        return False
    if _has_ad_price_check(content) or _has_campaign_inquiry(content) or _has_price_objection(content):
        return False
    if any(term in content for term in PRICE_KEYWORDS):
        return False
    return _has_project_process_question(content) or _has_project_consult_intent(content) or _has_generic_project_request(content)


def _has_appointment_record_query(content: str) -> bool:
    if not content:
        return False
    terms = ["我有没有预约", "我约的是", "约的是几点", "预约成功", "查一下预约", "查下预约", "是不是约了", "有没有约", "之前是不是约"]
    return any(term in content for term in terms)


def _has_price_objection(content: str) -> bool:
    if not content:
        return False
    if _has_effect_guarantee_request(content):
        return False
    return any(term in content for term in PRICE_OBJECTION_KEYWORDS)


def _is_identity_question(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["你是真人", "是AI", "是 ai", "机器人", "不是人", "客服是真人", "别骗我"]) and any(
        term in content for term in ["真人", "AI", "ai", "机器人", "骗"]
    )


def _has_effect_guarantee_request(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["保证一次有效", "保证有效", "一次有效", "一次见效", "包效果", "不保证就算了"])


def _needs_real_order_lookup(content: str) -> bool:
    if not content:
        return False
    order_terms = ["订单", "付款", "付的钱", "那笔钱", "到账", "扣款", "支付记录", "尾款", "定金", "预约金", "退款进度", "款项"]
    query_terms = ["查一下", "查下", "帮我查", "到底去哪了", "去哪了", "有没有到账", "什么时候到", "什么状态", "记录", "明细"]
    if not any(term in content for term in order_terms):
        return False
    if any(term in content for term in query_terms):
        return True
    return "订单" in content and "项目" not in content


def _has_store_inquiry(content: str) -> bool:
    if _has_advantage_question(content) or _has_case_request(content) or _has_fee_or_refund_dispute(content):
        return False
    trust_terms = ["正规", "靠谱", "骗人", "被骗", "资质", "营业执照", "证照", "许可证", "真假", "隐形消费", "被坑", "安全", "售后"]
    if any(term in content for term in trust_terms):
        return False
    hard_store_terms = [
        "地址",
        "哪里",
        "附近",
        "停车",
        "导航",
        "怎么过去",
        "地铁",
        "营业",
        "哪家近",
        "离我近",
        "近吗",
        "近不近",
        "位置",
        "路线",
        "搬走",
        "搬了吗",
        "搬走了吗",
        "还在",
        "还开",
        "还营业",
        "开门",
        "关门",
        "闭店",
        "停业",
        "几点开",
        "几点关",
        "营业时间",
        "换地址",
        "换地方",
        "店还在",
        "门店还在",
    ]
    if any(term in content for term in hard_store_terms):
        return True
    if not any(term in content for term in ["门店", "店"]):
        return False
    appointment_terms = ["预约", "能约", "能去", "周六", "周日", "明天", "后天", "下午", "上午", "到店"]
    if any(term in content for term in trust_terms + appointment_terms):
        return False
    return True


def _has_advantage_question(content: str) -> bool:
    return any(term in content for term in ADVANTAGE_KEYWORDS)


def _is_store_city_followup(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    if not _extract_city(content):
        return False
    if any(term in content for term in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS):
        return False
    if any(term in content for term in ["项目", "价格", "多少钱", "适合", "效果", "做什么", "能解决"]):
        return False
    recent = _recent_conversation_text(state, limit=6)
    store_context_terms = ["门店", "地址", "哪里", "哪家", "更方便", "城市", "区域", "附近", "导航", "停车", "店信息"]
    return any(term in recent for term in store_context_terms)


def _store_location_preference_from_text(text: str) -> str:
    if any(term in text for term in ["机场附近", "机场周边", "离机场近", "机场近", "高崎机场", "厦门机场", "机场"]):
        return "机场附近"
    if any(term in text for term in ["火车站附近", "离火车站近", "高铁站附近"]):
        return "火车站附近"
    return ""


def _store_location_preference_from_context(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    preference = _store_location_preference_from_text(content)
    if preference:
        return preference
    return _store_location_preference_from_text(_recent_conversation_text(state, limit=6))


def _contextual_followup_intents(state: AgentState) -> list[dict[str, Any]]:
    if not _is_store_city_followup(state):
        return []
    return [_store_city_followup_intent(state)]


def _store_city_followup_intent(state: AgentState) -> dict[str, Any]:
    content = state.get("normalized_content") or ""
    city = _extract_city(content) or content.strip()
    preference = _store_location_preference_from_context(state)
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


def _recent_conversation_text(state: AgentState, limit: int = 6) -> str:
    history = state.get("conversation_history") or []
    return "\n".join(str(item) for item in history[-limit:])


def _complaint_terms(content: str) -> list[str]:
    if not content:
        return []
    if _is_identity_question(content):
        return []
    soft_trust_markers = ["是不是", "会不会", "怕", "担心", "感觉", "不靠谱", "靠不靠谱"]
    if any(prefix in content for prefix in ["是不是", "会不会", "怕", "担心"]) and not any(
        hard in content for hard in ["我要投诉", "要求退款", "退钱", "维权", "曝光", "起诉"]
    ):
        return []
    from app.policies.constants import COMPLAINT_KEYWORDS

    terms = [word for word in COMPLAINT_KEYWORDS if word in content]
    if terms and any(marker in content for marker in soft_trust_markers) and not any(
        hard in content for hard in ["我要投诉", "要求退款", "退钱", "维权", "曝光", "起诉", "骗我钱", "骗钱"]
    ):
        return []
    if "骗人" in content and not any(prefix in content for prefix in soft_trust_markers):
        terms.append("骗人")
    return _dedupe_strings(terms)


def _severe_after_sales_terms(content: str) -> list[str]:
    if not content:
        return []
    return [word for word in SEVERE_AFTER_SALES_KEYWORDS if word in content and not _is_negated_symptom(content, word)]


def _is_negated_symptom(content: str, symptom: str) -> bool:
    negations = ["没有", "没", "无", "不", "未", "并不", "不是"]
    for prefix in negations:
        if f"{prefix}{symptom}" in content:
            return True
    index = content.find(symptom)
    if index < 0:
        return False
    left = content[max(0, index - 4) : index]
    return any(neg in left for neg in negations)


def _has_effect_dispute(content: str) -> bool:
    if not content:
        return False
    if any(prefix in content for prefix in ["会不会", "怕", "担心", "有没有可能"]) and any(
        word in content for word in ["没效果", "没用", "被坑", "骗人", "骗子"]
    ):
        return False
    accusation_terms = [
        "像骗子",
        "骗子一样",
        "你们骗人",
        "你们就是骗人",
        "骗我",
        "被你们坑",
        "坑我",
        "太坑了",
    ]
    if any(term in content for term in accusation_terms):
        return True
    dissatisfaction_terms = [
        "效果一点也不好",
        "效果一点都不好",
        "效果不好",
        "一点也没效果",
        "一点都没效果",
        "一点效果都没有",
        "一点用都没",
        "一点用都没有",
        "一点变化都没有",
        "没有变化",
        "没变化",
        "跟没做一样",
        "完全没用",
        "白做",
        "白花钱",
    ]
    if any(term in content for term in dissatisfaction_terms):
        return True
    past_context = any(word in content for word in ["做了", "做完", "做的", "花了", "丢了", "付了", "买了"])
    if any(word in content for word in ["一点用都没", "没有用", "没用", "白做"]) and past_context:
        return True
    if "没效果" in content and past_context:
        return True
    if any(word in content for word in ["没有淡", "没淡"]) and any(word in content for word in ["斑", "色沉", "痘印"]) and past_context:
        return True
    if any(word in content for word in ["花了", "丢了", "花"]) and any(word in content for word in ["没效果", "没用", "没有淡", "没淡", "一点用都没"]):
        return True
    return False


def _has_fee_or_refund_dispute(content: str) -> bool:
    if not content:
        return False
    if _is_soft_fee_concern(content):
        return False
    refund_terms = ["退给我", "退钱", "退款", "退定金", "退订金", "退预约金", "把钱退", "10块钱退", "十块钱退"]
    fee_terms = ["额外加钱", "加钱", "另收费", "额外收费", "多收", "乱收费", "收费不一样", "价格不一样", "说法不一样", "口径不一样"]
    payment_terms = ["定金", "订金", "预约金", "尾款", "付款", "付的钱", "门店说", "到店说"]
    if any(term in content for term in refund_terms + fee_terms):
        return True
    return any(term in content for term in payment_terms) and any(term in content for term in ["退", "加钱", "收费", "不一样", "不一致", "怎么说"])


def _is_soft_fee_concern(content: str) -> bool:
    if not content:
        return False
    soft_markers = ["会不会", "怕", "担心", "是不是", "有没有隐形", "会不会到店"]
    fee_markers = ["乱收费", "隐形消费", "加钱", "额外收费", "另收费", "到店加"]
    hard_markers = ["已经", "刚刚", "门店说", "到店说", "让我", "要我", "收了", "付了", "退钱", "退款", "投诉", "维权"]
    return any(marker in content for marker in soft_markers) and any(marker in content for marker in fee_markers) and not any(
        marker in content for marker in hard_markers
    )


def _has_recent_complaint_context(state: AgentState) -> bool:
    text = _recent_conversation_text(state)
    if not text:
        return False
    return bool(_complaint_terms(text) or _has_effect_dispute(text))


def _has_recent_competitor_context(state: AgentState) -> bool:
    text = _recent_conversation_text(state, limit=8)
    return any(word in text for word in COMPETITOR_KEYWORDS + ["对比", "报价截图", "别人报价", "竞品"])


def _is_pre_service_effect_concern(content: str) -> bool:
    if not content:
        return False
    soft_terms = [
        "会不会没效果",
        "会不会没有效果",
        "怕没效果",
        "怕没有效果",
        "担心没效果",
        "担心没有效果",
        "有没有效果",
        "怕被坑",
        "担心被坑",
        "会不会被坑",
        "怕乱收费",
        "隐形消费",
    ]
    if not any(term in content for term in soft_terms):
        return False
    past_or_done_terms = ["做完", "术后", "刚做", "已经做", "做了", "花了", "一点用都没", "没有淡", "没淡"]
    return not any(term in content for term in past_or_done_terms)


def _model_intent_has_current_trigger(state: AgentState, intent: str) -> bool:
    content = state.get("normalized_content") or ""
    image_info = state.get("image_info") or {}
    if intent == "image_inquiry":
        return bool(image_info.get("has_image"))
    if intent == "appointment_intent" and _has_ad_price_check(content):
        return False
    trigger_map = {
        "trust_issue": TRUST_KEYWORDS + ADVANTAGE_KEYWORDS,
        "competitor_compare": COMPETITOR_KEYWORDS + ADVANTAGE_KEYWORDS,
        "price_inquiry": PRICE_KEYWORDS,
        "ad_price_check": ["广告", "直播", "团购", "预约金", "尾款", "隐形收费", "其他收费", "另收费", "包含"],
        "campaign_inquiry": CAMPAIGN_KEYWORDS,
        "after_sales": AFTER_SALES_KEYWORDS,
        "store_inquiry": STORE_KEYWORDS,
        "appointment_intent": APPOINTMENT_KEYWORDS,
        "appointment_confirm": ["我有没有预约", "我约的是", "约的是几点", "预约成功", "查一下预约", "查下预约", "是不是约了", "有没有约"],
        "project_inquiry": PROJECT_KEYWORDS + ["斑", "点状", "片状", "痘印", "痘坑", "毛孔", "暗沉", "适合", "改善"],
        "case_request": ["案例", "效果案例", "前后对比", "对比照", "做完效果", "客户做完"],
        "project_process": ["流程", "操作流程", "怎么操作", "要做多久", "多久能做完", "时长", "步骤"],
        "complaint_refund": ["额外加钱", "加钱", "收费不一样", "说法不一样", "退给我", "退钱", "退款", "定金", "订金", "预约金"],
    }
    return any(word in content for word in trigger_map.get(intent, []))
