from __future__ import annotations

from app.graph.signals.general import is_identity_question, recent_conversation_text


def dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
from app.graph.state import AgentState
from app.policies.constants import COMPLAINT_KEYWORDS, COMPETITOR_KEYWORDS, SEVERE_AFTER_SALES_KEYWORDS


def complaint_terms(content: str) -> list[str]:
    if not content or is_identity_question(content):
        return []
    soft_trust_markers = ["是不是", "会不会", "不会", "怕", "担心", "感觉", "不靠谱", "靠不靠谱"]
    hard_accusations = ["我要投诉", "要求退款", "退钱", "维权", "曝光", "起诉", "骗我钱", "你们就是骗子", "骗子公司"]
    if any(marker in content for marker in soft_trust_markers) and not any(hard in content for hard in hard_accusations):
        return []
    terms = [word for word in COMPLAINT_KEYWORDS if word in content]
    if "骗人" in content and not any(marker in content for marker in soft_trust_markers):
        terms.append("骗人")
    return dedupe_strings(terms)


def severe_after_sales_terms(content: str) -> list[str]:
    if not content:
        return []
    return [word for word in SEVERE_AFTER_SALES_KEYWORDS if word in content and not _is_negated_symptom(content, word)]


def has_effect_dispute(content: str) -> bool:
    if not content:
        return False
    if any(prefix in content for prefix in ["会不会", "怕", "担心", "有没有可能"]) and any(
        word in content for word in ["没效果", "没用", "被坑", "骗人", "骗子"]
    ):
        return False
    accusation_terms = ["像骗子", "跟骗子一样", "你们骗人", "你们就是骗人", "骗我钱", "被你们坑", "坑我", "太坑了"]
    if any(term in content for term in accusation_terms):
        return True
    hard_effect_dispute_terms = ["白花钱", "赔钱", "毁容", "做坏了", "出问题没人管"]
    if any(term in content for term in hard_effect_dispute_terms):
        return True
    return False


def has_fee_or_refund_dispute(content: str) -> bool:
    if not content or is_soft_fee_concern(content) or is_deposit_rule_question(content):
        return False
    refund_terms = ["退给我", "退钱", "退款", "退定金", "退订金", "退预约金", "把钱退", "10块钱退", "十块钱退"]
    fee_terms = ["额外加钱", "加钱", "另收费", "额外收费", "多收", "乱收费", "收费不一样", "价格不一样", "说法不一样"]
    payment_terms = ["定金", "订金", "预约金", "尾款", "付款", "付的钱", "门店说", "到店说"]
    if any(term in content for term in refund_terms + fee_terms):
        return True
    return any(term in content for term in payment_terms) and any(
        term in content for term in ["退", "加钱", "收费", "不一样", "不一致", "怎么说"]
    )


def is_deposit_rule_question(content: str) -> bool:
    if not content:
        return False
    deposit_terms = ["定金", "订金", "预约金", "10元", "十元", "10块", "十块"]
    question_terms = ["什么意思", "能退吗", "可以退吗", "退吗", "怎么退", "规则", "干啥的", "为什么要付", "要付吗", "怎么用"]
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
    return any(term in content for term in deposit_terms) and any(term in content for term in question_terms) and not any(
        term in content for term in hard_terms
    )


def is_soft_fee_concern(content: str) -> bool:
    if not content:
        return False
    soft_markers = ["会不会", "怕", "担心", "是不是", "有没有隐形", "会不会到店"]
    fee_markers = ["乱收费", "隐形消费", "加钱", "额外收费", "另收费", "到店加"]
    hard_markers = ["已经", "刚刚", "门店说", "到店说", "让我", "要我", "收了", "付了", "退钱", "退款", "投诉", "维权"]
    return any(marker in content for marker in soft_markers) and any(marker in content for marker in fee_markers) and not any(
        marker in content for marker in hard_markers
    )


def has_recent_complaint_context(state: AgentState) -> bool:
    text = recent_conversation_text(state)
    return bool(text and (complaint_terms(text) or has_effect_dispute(text)))


def has_recent_competitor_context(state: AgentState) -> bool:
    text = recent_conversation_text(state, limit=8)
    return any(word in text for word in COMPETITOR_KEYWORDS + ["对比", "报价截图", "别人的报价", "竞品"])


def is_pre_service_effect_concern(content: str) -> bool:
    if not content:
        return False
    soft_terms = ["会不会没效果", "怕没效果", "担心没效果", "有没有效果", "怕被坑", "担心被坑", "会不会被坑", "怕乱收费", "隐形消费"]
    if not any(term in content for term in soft_terms):
        return False
    past_or_done_terms = ["做完", "术后", "刚做", "已经做", "做了", "花了", "一点用都没", "没淡", "没有淡"]
    return not any(term in content for term in past_or_done_terms)


def model_intent_has_current_trigger(state: AgentState, intent: str) -> bool:
    content = state.get("normalized_content") or ""
    image_info = state.get("image_info") or {}
    if intent == "image_inquiry":
        return bool(image_info.get("has_image"))
    trigger_map = {
        "trust_issue": ["正规", "靠谱吗", "骗人", "被骗", "资质", "营业执照", "证照", "许可证", "真假", "隐形消费", "被坑", "安全", "售后"],
        "competitor_compare": COMPETITOR_KEYWORDS,
        "price_inquiry": ["多少钱", "价格", "费用", "贵吗", "预算", "报价", "活动价", "尾款", "定金"],
        "ad_price_check": ["广告", "直播", "团购", "预约金", "尾款", "隐形收费", "其他收费", "另收费", "包含"],
        "campaign_inquiry": ["活动", "优惠", "福利", "新客活动", "节日活动", "团购"],
        "after_sales": ["术后", "恢复", "反黑", "红肿", "流脓", "出血", "疼", "痛", "没效果"],
        "store_inquiry": ["门店", "地址", "哪里", "附近", "导航", "停车", "营业时间", "还在", "关门"],
        "appointment_intent": ["预约", "能去", "到店", "周六", "周日", "明天", "下午", "上午"],
        "appointment_confirm": ["我有没有预约", "我约的是", "约的是几点", "预约成功", "查一下预约", "是不是约了", "有没有约"],
        "project_inquiry": ["斑", "点状", "片状", "痘印", "痘坑", "毛孔", "暗沉", "适合", "改善"],
        "case_request": ["案例", "效果案例", "前后对比", "对比照", "做完效果", "客户做完"],
        "project_process": ["流程", "操作流程", "怎么操作", "要做多久", "时长", "步骤"],
        "complaint_refund": ["额外加钱", "加钱", "收费不一样", "说法不一样", "退给我", "退钱", "退款", "退定金", "退订金", "退预约金"],
    }
    return any(keyword in content for keyword in trigger_map.get(intent, []))


def _is_negated_symptom(content: str, word: str) -> bool:
    negations = ["不", "没", "没有", "并不"]
    index = content.find(word)
    if index <= 0:
        return False
    window = content[max(0, index - 3) : index]
    return any(neg in window for neg in negations)
