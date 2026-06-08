from __future__ import annotations

from app.graph.planner_intent_meta import dedupe_strings
from app.graph.state import AgentState
from app.policies.constants import (
    AFTER_SALES_KEYWORDS,
    APPOINTMENT_KEYWORDS,
    CAMPAIGN_KEYWORDS,
    COMPETITOR_KEYWORDS,
    PRICE_KEYWORDS,
    PROJECT_KEYWORDS,
    SEVERE_AFTER_SALES_KEYWORDS,
    STORE_KEYWORDS,
    TRUST_KEYWORDS,
    ADVANTAGE_KEYWORDS,
)


def recent_conversation_text(state: AgentState, limit: int = 6) -> str:
    history = state.get("conversation_history") or []
    return "\n".join(str(item) for item in history[-limit:])


def complaint_terms(content: str) -> list[str]:
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
    return dedupe_strings(terms)


def severe_after_sales_terms(content: str) -> list[str]:
    if not content:
        return []
    return [word for word in SEVERE_AFTER_SALES_KEYWORDS if word in content and not _is_negated_symptom(content, word)]


def has_effect_dispute(content: str) -> bool:
    if not content:
        return False
    if is_pre_service_effect_concern(content):
        return False
    if is_mild_effect_dissatisfaction(content):
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


def is_mild_effect_dissatisfaction(content: str) -> bool:
    if not content:
        return False
    past_context = any(word in content for word in ["做了", "做完", "已经做", "去过", "做过"])
    mild_terms = ["不见效果", "没什么变化", "变化不大", "怎么没变化", "怎么还没淡", "怎么感觉没什么效果"]
    consultive_tone = any(word in content for word in ["呢", "吗", "咋办", "怎么办", "怎么回事", "正常吗", "想问"])
    return past_context and any(term in content for term in mild_terms) and consultive_tone


def has_fee_or_refund_dispute(content: str) -> bool:
    if not content:
        return False
    if is_soft_fee_concern(content):
        return False
    if _is_pre_service_fee_trust_concern(content):
        return False
    if is_deposit_rule_question(content):
        return False
    refund_terms = ["退给我", "退钱", "退款", "退定金", "退订金", "退预约金", "把钱退", "10块钱退", "十块钱退"]
    fee_terms = ["额外加钱", "加钱", "另收费", "额外收费", "多收", "乱收费", "收费不一样", "价格不一样", "说法不一样", "口径不一样"]
    payment_terms = ["定金", "订金", "预约金", "尾款", "付款", "付的钱", "门店说", "到店说"]
    if any(term in content for term in refund_terms + fee_terms):
        return True
    return any(term in content for term in payment_terms) and any(term in content for term in ["退", "加钱", "收费", "不一样", "不一致", "怎么说"])


def _is_pre_service_fee_trust_concern(content: str) -> bool:
    trust_terms = ["不正规", "不太正规", "正规吗", "靠谱", "不靠谱", "怕", "担心", "是不是", "会不会", "朋友说", "听说"]
    fee_terms = ["加钱", "另收费", "额外收费", "乱收费", "隐形消费", "到店加", "门店去要加"]
    hard_terms = ["已付", "已经付", "刚付", "付款了", "收了", "扣了", "退钱", "退款", "退给我", "投诉", "维权", "曝光", "起诉"]
    return any(term in content for term in trust_terms) and any(term in content for term in fee_terms) and not any(
        term in content for term in hard_terms
    )


def is_deposit_rule_question(content: str) -> bool:
    if not content:
        return False
    deposit_terms = ["定金", "订金", "预约金", "10元", "十元", "10块", "十块"]
    payment_method_terms = [
        "不交定金",
        "不交订金",
        "不交预约金",
        "不用交定金",
        "不用交订金",
        "不用交预约金",
        "不付定金",
        "不付订金",
        "不付预约金",
        "到店付全款",
        "到店再付全款",
        "到店付款",
        "到店再付",
        "到店付",
        "付全款",
        "再付全款",
        "交全款",
    ]
    question_terms = [
        "是什么意思",
        "什么意思",
        "能退吗",
        "可以退吗",
        "可退吗",
        "退吗",
        "怎么退",
        "规则",
        "干嘛的",
        "为什么要付",
        "要付吗",
        "需要吗",
        "需要定金吗",
        "需要预约金吗",
        "要交吗",
        "要给定金吗",
        "怎么用",
    ]
    hard_terms = [
        "退给我",
        "把钱退",
        "退钱",
        "退款",
        "要求退",
        "不退",
        "不然",
        "投诉",
        "维权",
        "曝光",
        "起诉",
        "骗",
        "额外加钱",
        "另收费",
        "乱收费",
        "口径不一样",
        "说法不一样",
        "门店说",
        "到店说",
        "收了",
        "付了",
        "已经付",
        "刚付",
    ]
    if any(term in content for term in hard_terms):
        return False
    if any(term in content for term in deposit_terms) and any(term in content for term in question_terms):
        return True
    return any(term in content for term in deposit_terms) and any(term in content for term in payment_method_terms)


def is_soft_fee_concern(content: str) -> bool:
    if not content:
        return False
    soft_markers = [
        "会不会",
        "怕",
        "担心",
        "是不是",
        "有没有隐形",
        "会不会到店",
        "到店会",
        "到店后会",
        "到店是不是会",
        "到店乱",
        "会不会到店乱",
        "到店会不会乱",
        "会不会乱",
        "会不会加",
        "会不会多",
        "是不是会加",
        "会不会乱",
        "有没有乱",
    ]
    fee_markers = [
        "乱收费",
        "隐形消费",
        "加钱",
        "额外收费",
        "另收费",
        "到店加",
        "到店会不会加",
        "到店加收",
        "到店费用",
        "到店支出",
        "到店账单",
        "乱加",
        "乱收",
        "多收",
        "乱收费",
        "多扣",
        "到店怎么收",
        "费用会不会",
        "会不会乱收",
        "会不会乱加",
        "会不会到店乱收",
        "到店会不会乱收",
        "会不会到店乱收费",
        "到店会不会乱收费",
    ]
    hard_markers = [
        "已经",
        "刚刚",
        "门店说",
        "到店说",
        "让我",
        "要我",
        "收了",
        "付了",
        "退钱",
        "退款",
        "投诉",
        "维权",
        "把钱退",
        "要求退",
        "不给",
        "退货",
    ]
    return any(marker in content for marker in soft_markers) and any(marker in content for marker in fee_markers) and not any(
        marker in content for marker in hard_markers
    )


def has_recent_complaint_context(state: AgentState) -> bool:
    text = recent_conversation_text(state)
    if not text:
        return False
    return bool(complaint_terms(text) or has_effect_dispute(text))


def has_recent_competitor_context(state: AgentState) -> bool:
    text = recent_conversation_text(state, limit=8)
    return any(word in text for word in COMPETITOR_KEYWORDS + ["对比", "报价截图", "别人报价", "竞品"])


def is_pre_service_effect_concern(content: str) -> bool:
    if not content:
        return False
    soft_future_terms = [
        "不会做完没效果",
        "会不会做完没效果",
        "怕做完没效果",
        "担心做完没效果",
        "做完没效果吧",
        "做完没有效果吧",
    ]
    actual_done_terms = ["已经做", "做了", "做过", "去过", "刚做", "刚做完", "术后"]
    if any(term in content for term in soft_future_terms) and not any(term in content for term in actual_done_terms):
        return True
    soft_terms = [
        "会不会没效果",
        "会不会没有效果",
        "怕没效果",
        "怕没有效果",
        "担心没效果",
        "担心没有效果",
        "有没有效果",
        "会不会反弹",
        "反弹",
        "返弹",
        "反复",
        "又回来",
        "怕反弹",
        "担心反弹",
        "能维持多久",
        "维持多久",
        "保持多久",
        "能保持多久",
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


def model_intent_has_current_trigger(state: AgentState, intent: str) -> bool:
    content = state.get("normalized_content") or ""
    image_info = state.get("image_info") or {}
    if intent == "image_inquiry":
        return bool(image_info.get("has_image"))
    if intent == "appointment_intent" and _has_ad_price_check(content):
        return False
    if intent == "appointment_intent" and is_deposit_rule_question(content):
        return False
    if intent == "complaint_refund" and is_deposit_rule_question(content):
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
        "complaint_refund": ["额外加钱", "加钱", "收费不一样", "说法不一样", "退给我", "退钱", "退款", "退定金", "退订金", "退预约金"],
    }
    return any(word in content for word in trigger_map.get(intent, []))


def _is_identity_question(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["你是真人", "是AI", "是 ai", "机器人", "不是人", "客服是真人", "别骗我"]) and any(
        term in content for term in ["真人", "AI", "ai", "机器人", "骗"]
    )


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


def _has_ad_price_check(content: str) -> bool:
    if not content:
        return False
    source_terms = ["广告", "直播", "团购", "券", "小红书", "抖音", "美团", "大众点评"]
    price_terms = ["199", "一百九十九", "预约金", "定金", "尾款", "其他收费", "另收费", "隐形收费", "包含", "有效果"]
    return any(term in content for term in source_terms) and any(term in content for term in price_terms)
