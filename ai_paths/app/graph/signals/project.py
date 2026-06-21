from __future__ import annotations

import re

from app.graph.signals.general import is_low_information_content
from app.policies.constants import (
    ADVANTAGE_KEYWORDS,
    AFTER_SALES_KEYWORDS,
    CAMPAIGN_KEYWORDS,
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
    consult_terms = (
        "适合",
        "效果",
        "原理",
        "恢复",
        "副作用",
        "维持",
        "推荐",
        "方案",
        "怎么做",
        "做什么",
        "能不能做",
        "可以做吗",
        "哪一个",
        "哪个",
        "区别",
        "改善",
        "解决",
        "淡化",
        "去掉",
        "去除",
        "方法",
        "技术",
        "仪器",
        "操作",
    )
    return any(term in content for term in consult_terms)


def has_generic_project_request(content: str) -> bool:
    if not content or is_low_information_content(content):
        return False
    if any(term in content for term in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS):
        return False
    generic_terms = (
        "了解项目",
        "了解一下项目",
        "项目介绍",
        "有什么项目",
        "有哪些项目",
        "推荐项目",
        "做什么项目",
        "想看项目",
        "想了解项目",
        "了解一下祛斑",
        "了解一下淡斑",
    )
    return any(term in content for term in generic_terms)


def has_case_request(content: str) -> bool:
    if not content:
        return False
    case_terms = (
        "案例",
        "效果案例",
        "前后对比",
        "对比照",
        "对比图",
        "做完效果",
        "客户做完",
        "客户做完后的效果",
        "案例效果",
        "案例展示",
        "效果图",
        "对比案例",
        "客户效果",
        "做完之后的效果",
        "真实做完的效果",
        "真实做完效果",
        "真实效果",
        "真实案例",
        "真实对比",
        "恢复后的效果",
        "发我看看效果",
        "发个效果",
        "看看效果",
        "看一下效果",
        "效果对比",
        "图片上的客户",
        "做几次的效果",
        "有图吗",
        "有照片吗",
    )
    if any(term in content for term in case_terms):
        return True
    if "效果" in content and any(
        term in content
        for term in (
            "一般",
            "能看到",
            "看得到",
            "看得到吗",
            "看出来",
            "看出来吗",
            "明显吗",
            "明显不",
            "怎么样",
            "好不好",
            "参考",
            "真实",
            "有吗",
            "有没有",
        )
    ):
        return True
    if any(term in content for term in ("我这种", "这种情况", "类似情况", "同类情况")) and any(
        term in content for term in ("能改善", "有用", "有效", "明显", "黑色素", "斑")
    ):
        return True
    return False


def has_project_process_question(content: str) -> bool:
    if not content:
        return False
    process_terms = (
        "流程",
        "操作流程",
        "怎么操作",
        "怎么做",
        "要做多久",
        "大概要多久",
        "多久能做完",
        "操作多久",
        "时长",
        "步骤",
        "过程",
    )
    return any(term in content for term in process_terms)


def has_ad_price_check(content: str) -> bool:
    if not content:
        return False
    context_terms = (
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
        "隐形消费",
        "其他收费",
        "另收费",
        "包含什么",
        "包含哪些",
    )
    price_terms = PRICE_KEYWORDS + (
        "199",
        "299",
        "268",
        "308",
        "58",
        "380",
        "10元",
        "十元",
        "10块",
        "十块",
        "定金",
        "订金",
        "预约金",
        "能退",
        "可退",
        "怎么退",
    )
    return any(term in content for term in context_terms) and (
        any(term in content for term in price_terms) or bool(re.search(r"\d+\s*元", content))
    )


def has_campaign_inquiry(content: str) -> bool:
    if not content:
        return False
    campaign_terms = CAMPAIGN_KEYWORDS + (
        "福利",
        "团购",
        "预约金",
        "定金",
        "订金",
        "尾款",
        "券",
        "广告价",
        "直播价",
    )
    return any(term in content for term in campaign_terms)


def is_ad_source_only_project_question(content: str) -> bool:
    if not content or not any(term in content for term in ("广告", "直播", "抖音", "快手")):
        return False
    if has_ad_price_check(content) or has_campaign_inquiry(content) or has_price_objection(content):
        return False
    if any(term in content for term in PRICE_KEYWORDS):
        return False
    return has_project_process_question(content) or has_project_consult_intent(content) or has_generic_project_request(
        content
    )


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
        for term in (
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
        )
    )


def has_advantage_question(content: str) -> bool:
    return any(term in content for term in ADVANTAGE_KEYWORDS)
