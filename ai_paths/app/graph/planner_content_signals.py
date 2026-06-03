from __future__ import annotations

import re

from app.graph.planner_dispute_signals import has_fee_or_refund_dispute, recent_conversation_text
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
    STORE_KEYWORDS,
    TRUST_KEYWORDS,
)


def is_pre_visit_only_question(content: str) -> bool:
    if not content:
        return False
    prep_terms = ["需要带什么", "要带什么", "带什么", "能不能化妆", "可以化妆", "要不要空腹", "需要空腹", "到店流程", "第一次去注意"]
    return any(term in content for term in prep_terms)


def has_current_after_sales_signal(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["做完", "术后", "恢复", "反黑", "红肿", "流脓", "出血", "疼", "痛", "没效果"])


def is_service_response_complaint(content: str) -> bool:
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


def has_project_consult_intent(content: str) -> bool:
    if has_price_objection(content):
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


def has_generic_project_request(content: str) -> bool:
    if not content or is_low_information_content(content):
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


def is_low_information_content(content: str) -> bool:
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
    if len(normalized) <= 2 and not has_business_signal(text):
        return True
    return False


def has_business_signal(content: str) -> bool:
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


def has_recent_action_context(state: AgentState) -> bool:
    recent = recent_conversation_text(state, limit=8)
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


def has_case_request(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["案例", "效果案例", "前后对比", "对比照", "做完效果", "客户做完", "案例效果", "案例展示"])


def has_project_process_question(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["流程", "操作流程", "怎么操作", "怎么做", "要做多久", "大概要多久", "多久能做完", "时长", "步骤", "过程"])


def has_ad_price_check(content: str) -> bool:
    if not content:
        return False
    context_terms = ["广告", "直播", "团购", "预约金", "尾款", "隐形收费", "其他收费", "另收费", "包含什么", "包含哪些"]
    price_terms = PRICE_KEYWORDS + ["199", "299", "268", "10元", "定金", "订金"]
    return any(term in content for term in context_terms) and (
        any(term in content for term in price_terms) or bool(re.search(r"\d+\s*元?", content))
    )


def has_campaign_inquiry(content: str) -> bool:
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


def is_ad_source_only_project_question(content: str) -> bool:
    if not content:
        return False
    if not any(term in content for term in ["广告", "直播"]):
        return False
    if has_ad_price_check(content) or has_campaign_inquiry(content) or has_price_objection(content):
        return False
    if any(term in content for term in PRICE_KEYWORDS):
        return False
    return has_project_process_question(content) or has_project_consult_intent(content) or has_generic_project_request(content)


def has_appointment_record_query(content: str) -> bool:
    if not content:
        return False
    terms = ["我有没有预约", "我约的是", "约的是几点", "预约成功", "查一下预约", "查下预约", "是不是约了", "有没有约", "之前是不是约"]
    return any(term in content for term in terms)


def has_price_objection(content: str) -> bool:
    if not content:
        return False
    if has_effect_guarantee_request(content):
        return False
    return any(term in content for term in PRICE_OBJECTION_KEYWORDS)


def is_identity_question(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["你是真人", "是AI", "是 ai", "机器人", "不是人", "客服是真人", "别骗我"]) and any(
        term in content for term in ["真人", "AI", "ai", "机器人", "骗"]
    )


def has_effect_guarantee_request(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["保证一次有效", "保证有效", "一次有效", "一次见效", "包效果", "不保证就算了"])


def needs_real_order_lookup(content: str) -> bool:
    if not content:
        return False
    order_terms = ["订单", "付款", "付的钱", "那笔钱", "到账", "扣款", "支付记录", "尾款", "定金", "预约金", "退款进度", "款项"]
    query_terms = ["查一下", "查下", "帮我查", "到底去哪了", "去哪了", "有没有到账", "什么时候到", "什么状态", "记录", "明细"]
    if not any(term in content for term in order_terms):
        return False
    if any(term in content for term in query_terms):
        return True
    return "订单" in content and "项目" not in content


def has_store_inquiry(content: str) -> bool:
    if has_advantage_question(content) or has_case_request(content) or has_fee_or_refund_dispute(content):
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


def has_advantage_question(content: str) -> bool:
    return any(term in content for term in ADVANTAGE_KEYWORDS)
