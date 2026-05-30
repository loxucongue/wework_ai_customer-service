from __future__ import annotations

import json
import re
from typing import Any

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
    if _needs_real_order_lookup(content):
        items.append({"intent": "human_request", "skill": "handoff", "priority": 0, "reason": "订单、付款或到账状态需要真实系统数据核实"})
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
    elif any(word in content for word in CAMPAIGN_KEYWORDS):
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
    if _has_project_consult_intent(content) or not items:
        items.append({"intent": "project_inquiry", "skill": "project_consult", "priority": 4, "reason": "项目咨询或普通咨询"})
    return _dedupe_intents(items)


def should_use_model_planner(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    if not content and not state.get("file_image"):
        return False
    return True


def planner_model_tier(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    if any(word in content for word in AFTER_SALES_KEYWORDS + COMPETITOR_KEYWORDS + TRUST_KEYWORDS):
        return "balanced"
    return "fast"


def planner_messages_for_model(state: AgentState) -> list[dict[str, Any]]:
    system = (
        "你是企业微信医美客服系统的轻量动作规划节点。"
        "你不回复客户，只判断本轮需要调用哪些业务skill。"
        "最多输出3个意图，按优先级排序。"
        "可选skill只能是：project_consult, price_consult, trust_build, competitor, after_sales, store, appointment。"
        "如果只是普通项目咨询，用project_consult；价格用price_consult；正规/靠谱/怕被骗用trust_build；别家/竞品用competitor。"
        "营业执照、资质、证照、许可证、机构是否正规属于trust_build，不属于store；客户没有问地址/附近/停车/路线时不要调用store。"
        "客户问“你们优势在哪里/为什么选你们/有什么不一样”，属于trust_build；如果上一轮明显在竞品对比，可用competitor。不要因为出现“哪里”误判成门店。"
        "如果上一轮在问门店/地址/哪家方便，客户本轮只补充城市如“我在上海/上海/人在上海”，必须用store。"
        "客户说太贵、贵了、便宜点、能不能优惠、最低价、底价、预算不够时，属于price_consult，不要归到project_consult。"
        "最终只输出合法JSON：{\"intents\":[{\"intent\":\"\",\"skill\":\"\",\"priority\":1,\"reason\":\"\"}]}"
    )
    user = {
        "content": state.get("normalized_content"),
        "conversation_history": state.get("conversation_history", [])[-6:],
        "image_info": state.get("image_info", {}),
        "customer_profile": state.get("customer_profile", {}),
        "history_events": state.get("history_events", [])[-6:],
        "appointment_cache": state.get("appointment_cache", {}),
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": _json_dumps(user)},
    ]


def validated_planner_intents(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("intents")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Planner JSON missing intents")
    allowed_skills = {"project_consult", "price_consult", "trust_build", "competitor", "after_sales", "store", "appointment"}
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        skill = str(item.get("skill", "")).strip()
        if skill not in allowed_skills:
            continue
        intent = _intent_for_skill(skill)
        priority_raw = item.get("priority", len(result) + 1)
        try:
            priority = int(priority_raw)
        except (TypeError, ValueError):
            priority = len(result) + 1
        reason = str(item.get("reason") or "模型规划识别").strip()
        result.append({"intent": intent, "skill": skill, "priority": priority, "reason": reason[:80]})
        if len(result) >= 3:
            break
    if not result:
        raise ValueError("Planner JSON has no valid intents")
    return _dedupe_intents(result)


def merge_intents(state: AgentState, rule_items: list[dict[str, Any]], model_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        intent = str(item.get("intent") or "")
        if not intent or intent in seen:
            return
        seen.add(intent)
        merged.append(item)

    for item in rule_items:
        add(item)
    for item in model_items:
        intent = str(item.get("intent") or "")
        if intent in seen or _model_intent_has_current_trigger(state, intent):
            add(item)
    return merged[:3] or _dedupe_intents(rule_items + model_items)


def filter_spurious_intents(state: AgentState, intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    image_info = state.get("image_info") or {}
    content = state.get("normalized_content") or ""
    has_current_competitor = any(word in content for word in COMPETITOR_KEYWORDS)
    has_current_trust = any(word in content for word in TRUST_KEYWORDS)
    has_price_objection = _has_price_objection(content)
    pre_service_effect_concern = _is_pre_service_effect_concern(content)
    if _has_effect_guarantee_request(content):
        intents = [item for item in intents if item.get("intent") != "price_inquiry"]
        if not any(item.get("intent") == "trust_issue" for item in intents):
            intents.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户要求效果保证或一次见效承诺"})
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
    if _is_store_city_followup(state):
        intents = [item for item in intents if item.get("intent") not in {"project_inquiry", "price_inquiry", "campaign_inquiry"}]
        if not any(item.get("intent") == "store_inquiry" for item in intents):
            intents.append({"intent": "store_inquiry", "skill": "store", "priority": 1, "reason": "承接上一轮门店查询补充城市"})
    if has_price_objection:
        intents = [item for item in intents if item.get("intent") != "project_inquiry"]
        if not any(item.get("intent") == "price_inquiry" for item in intents):
            intents.append({"intent": "price_inquiry", "skill": "price_consult", "priority": 2, "reason": "价格异议或议价"})
    if pre_service_effect_concern:
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
    if _has_advantage_question(content) or _has_case_request(content):
        return False
    trust_terms = ["正规", "靠谱", "骗人", "被骗", "资质", "营业执照", "证照", "许可证", "真假", "隐形消费", "被坑", "安全", "售后"]
    if any(term in content for term in trust_terms):
        return False
    hard_store_terms = ["地址", "哪里", "附近", "停车", "导航", "怎么过去", "地铁", "营业", "哪家近", "离我近", "近吗", "近不近", "位置", "路线"]
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
    if any(prefix in content for prefix in ["会不会", "怕", "担心", "有没有可能"]) and any(word in content for word in ["没效果", "没用", "被坑"]):
        return False
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
    }
    return any(word in content for word in trigger_map.get(intent, []))


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
    }.get(skill, "emotion_chat")


def _extract_city(content: str) -> str:
    from app.policies.constants import CITY_NAMES

    for city in CITY_NAMES:
        if city in content:
            return city
    return ""


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
