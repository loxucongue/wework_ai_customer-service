from __future__ import annotations

import re

from app.graph.planner_dispute_signals import recent_conversation_text
from app.graph.state import AgentState
from app.policies.constants import (
    AFTER_SALES_KEYWORDS,
    APPOINTMENT_KEYWORDS,
    CAMPAIGN_KEYWORDS,
    COMPETITOR_KEYWORDS,
    PRICE_KEYWORDS,
    PROJECT_KEYWORDS,
    STORE_KEYWORDS,
    TRUST_KEYWORDS,
)


def is_pre_visit_only_question(content: str) -> bool:
    if not content:
        return False
    prep_terms = [
        "需要带什么",
        "要带什么",
        "带什么",
        "能不能化妆",
        "可以化妆",
        "要不要空腹",
        "需要空腹",
        "空腹去",
        "吃了早饭",
        "吃饭能不能去",
        "吃完饭",
        "能不能吃饭",
        "素颜",
        "洗脸",
        "到店流程",
        "第一次去注意",
        "第一次过去",
    ]
    return any(term in content for term in prep_terms)


def has_current_after_sales_signal(content: str) -> bool:
    if not content:
        return False
    if "做完" in content and any(term in content for term in ["效果", "变化", "案例", "对比", "前后"]) and not any(
        term in content for term in ["术后", "恢复", "反黑", "红肿", "流脓", "出血", "疼", "痛", "没效果"]
    ):
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


def is_low_information_closing(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    normalized = re.sub(r"[\s,，。.!！?？~～、]+", "", text)
    closing_terms = {
        "谢谢",
        "谢谢啦",
        "谢谢你",
        "好的谢谢",
        "好的谢谢你",
        "好谢谢",
        "好的哈",
        "好的呢",
        "好的好的",
        "好哒",
        "我看看",
        "我考虑一下",
        "先这样",
        "后面再说",
        "暂时不用",
        "先不用",
        "不用了",
        "明白了",
        "知道啦",
        "收到了",
    }
    return normalized in closing_terms


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


def is_identity_question(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["你是真人", "是AI", "是 ai", "机器人", "不是人", "客服是真人", "别骗我"]) and any(
        term in content for term in ["真人", "AI", "ai", "机器人", "骗"]
    )


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
