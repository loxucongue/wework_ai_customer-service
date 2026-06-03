from __future__ import annotations

import re
from typing import Any

from app.policies.constants import (
    ADVANTAGE_KEYWORDS,
    PRICE_KEYWORDS,
    PRICE_OBJECTION_KEYWORDS,
    PROJECT_KEYWORDS,
    SEVERE_AFTER_SALES_KEYWORDS,
)


def has_project_consult_intent(content: str) -> bool:
    """Project names alone are not enough; otherwise simple price turns become noisy."""
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


def has_case_request(content: str) -> bool:
    if not content:
        return False
    case_terms = ["案例", "效果案例", "前后对比", "对比照", "做完效果", "客户做完", "案例效果", "案例展示"]
    return any(term in content for term in case_terms)


def has_project_process_question(content: str) -> bool:
    if not content:
        return False
    process_terms = ["流程", "操作流程", "怎么操作", "怎么做", "要做多久", "大概要多久", "多久能做完", "时长", "步骤", "过程"]
    return any(term in content for term in process_terms)


def is_generic_project_intro(content: str) -> bool:
    if not content:
        return False
    if any(term in content for term in ["斑", "痘", "毛孔", "暗沉", "松弛", "抗衰", "价格", "多少钱", "门店", "预约", "案例"]):
        return False
    return any(term in content for term in ["了解一下项目", "了解下项目", "有什么项目", "有哪些项目", "介绍一下项目", "推荐个项目"])


def is_unclear_need(content: str) -> bool:
    if not content:
        return False
    vague_terms = ["不知道要做啥", "不知道做啥", "不知道做什么", "没明确需求", "脸看着很累", "状态不好", "气色不好"]
    return any(term in content for term in vague_terms) and not any(term in content for term in ["多少钱", "价格", "预约", "门店"])


def has_ad_price_check(content: str) -> bool:
    if not content:
        return False
    context_terms = ["广告", "直播", "团购", "预约金", "尾款", "隐形收费", "其他收费", "另收费", "包含什么", "包含哪些"]
    price_terms = PRICE_KEYWORDS + ["199", "299", "268", "10元", "定金", "订金"]
    return any(term in content for term in context_terms) and (
        any(term in content for term in price_terms) or bool(re.search(r"\d+\s*元?", content))
    )


def has_appointment_record_query(content: str) -> bool:
    if not content:
        return False
    terms = ["我有没有预约", "我约的是", "约的是几点", "预约成功", "查一下预约", "查下预约", "是不是约了", "有没有约", "之前是不是约"]
    return any(term in content for term in terms)


def has_appointment_change_or_cancel(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["取消预约", "帮我取消", "不去了", "明天不去", "改约", "改时间", "换个时间", "改到", "换到"])


def has_price_objection(content: str) -> bool:
    if not content:
        return False
    if has_effect_guarantee_request(content):
        return False
    return any(term in content for term in PRICE_OBJECTION_KEYWORDS)


def has_effect_guarantee_request(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["保证一次有效", "保证有效", "一次有效", "一次见效", "包效果", "不保证就算了"])


def has_store_inquiry(content: str) -> bool:
    if has_advantage_question(content):
        return False
    if has_case_request(content):
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
        "营业时间",
        "开门",
        "关门",
        "闭店",
        "停业",
        "还开",
        "还营业",
        "几点开",
        "几点关",
        "哪家近",
        "离我近",
        "近吗",
        "近不近",
        "位置",
        "路线",
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


def recent_conversation_text(state: dict[str, Any], limit: int = 6) -> str:
    history = state.get("conversation_history") or []
    return "\n".join(str(item) for item in history[-limit:])


def is_negated_symptom(content: str, symptom: str) -> bool:
    negations = ["没有", "没", "无", "不", "未", "并不", "不是"]
    for prefix in negations:
        if f"{prefix}{symptom}" in content:
            return True
    index = content.find(symptom)
    if index < 0:
        return False
    left = content[max(0, index - 4) : index]
    return any(neg in left for neg in negations)


def denies_severe_after_sales(content: str) -> bool:
    return any(is_negated_symptom(content, word) for word in SEVERE_AFTER_SALES_KEYWORDS)


def is_pre_service_effect_concern(content: str) -> bool:
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
