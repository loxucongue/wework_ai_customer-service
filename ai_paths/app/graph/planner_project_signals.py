from __future__ import annotations

import re

from app.graph.planner_general_signals import is_low_information_content
from app.policies.constants import (
    ADVANTAGE_KEYWORDS,
    AFTER_SALES_KEYWORDS,
    COMPETITOR_KEYWORDS,
    PRICE_KEYWORDS,
    PRICE_OBJECTION_KEYWORDS,
    PROJECT_KEYWORDS,
    TRUST_KEYWORDS,
)


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


def has_case_request(content: str) -> bool:
    if not content:
        return False
    return any(
        term in content
        for term in [
            "案例",
            "效果案例",
            "前后对比",
            "对比照",
            "做完效果",
            "客户做完",
            "案例效果",
            "案例展示",
            "效果图",
            "对比案例",
            "客户效果",
            "做完之后的效果",
            "发我看看效果",
            "发个效果",
            "看看效果",
        ]
    )


def has_project_process_question(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["流程", "操作流程", "怎么操作", "怎么做", "要做多久", "大概要多久", "多久能做完", "时长", "步骤", "过程"])


def has_ad_price_check(content: str) -> bool:
    if not content:
        return False
    context_terms = [
        "广告",
        "直播",
        "团购",
        "预约金",
        "定金",
        "订金",
        "尾款",
        "10元",
        "十元",
        "10块",
        "十块",
        "隐形收费",
        "其他收费",
        "另收费",
        "包含什么",
        "包含哪些",
    ]
    price_terms = PRICE_KEYWORDS + ["199", "299", "268", "10元", "十元", "10块", "十块", "定金", "订金", "预约金", "能退", "可退", "怎么退"]
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
        "定金",
        "订金",
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


def has_price_objection(content: str) -> bool:
    if not content:
        return False
    if has_effect_guarantee_request(content):
        return False
    return any(term in content for term in PRICE_OBJECTION_KEYWORDS)


def has_effect_guarantee_request(content: str) -> bool:
    if not content:
        return False
    return any(
        term in content
        for term in [
            "保证一次有效",
            "保证有效",
            "一次有效",
            "一次见效",
            "包效果",
            "效果有保障",
            "效果保障",
            "有保障吗",
            "保障效果",
            "不保证就算了",
        ]
    )


def has_advantage_question(content: str) -> bool:
    return any(term in content for term in ADVANTAGE_KEYWORDS)
