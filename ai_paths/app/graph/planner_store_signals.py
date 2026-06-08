from __future__ import annotations

import re

from app.graph.planner_dispute_signals import has_fee_or_refund_dispute
from app.graph.planner_project_signals import has_advantage_question, has_case_request

_APPOINTMENT_RECORD_TERMS = [
    "我有没有预约",
    "我约的是",
    "约的是几点",
    "预约成功",
    "查一下预约",
    "查预约",
    "是不是约了",
    "有没有约",
    "之前是不是约",
]

_TRUST_TERMS = [
    "正规",
    "靠谱",
    "骗人",
    "被骗",
    "资质",
    "营业执照",
    "证照",
    "许可证",
    "真假",
    "隐形消费",
    "被坑",
    "安全",
    "售后",
]

_STORE_CORE_TERMS = [
    "门店",
    "地址",
    "哪里",
    "在哪",
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
    "哪家",
    "位置",
    "路线",
    "搬走",
    "搬了",
    "搬迁",
    "换地址",
    "换地方",
    "店还在",
    "门店还在",
]

_LOCATION_HINT_TERMS = [
    "机场",
    "高铁站",
    "火车站",
    "地铁站",
    "商圈",
    "广场",
    "万达",
    "附近",
    "这边",
    "这附近",
]

_RECOMMEND_PATTERNS = [
    r"推荐.*(近|最近|方便)",
    r"(离|距).*(近|最近)",
    r"哪家.*(近|最近|方便)",
]

_STATUS_PATTERNS = [
    r".*(还在|还开|还营业|开门|关门|闭店|停业|搬了|搬走了|搬迁).*$",
]

_STORE_NAME_PATTERNS = [
    r".*门店名字.*",
    r".*店名.*叫什么.*",
    r".*哪家店.*",
    r".*哪几家店.*",
    r".*有几家店.*",
    r".*有哪些店.*",
    r".*门店都有哪些.*",
    r".*都有哪些门店.*",
]


def has_appointment_record_query(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in _APPOINTMENT_RECORD_TERMS)


def _has_location_hint(content: str) -> bool:
    return any(term in content for term in _LOCATION_HINT_TERMS)


def _has_recommendation_shape(content: str) -> bool:
    return any(re.search(pattern, content) for pattern in _RECOMMEND_PATTERNS)


def _has_status_shape(content: str) -> bool:
    return any(re.search(pattern, content) for pattern in _STATUS_PATTERNS)


def _has_store_name_shape(content: str) -> bool:
    return any(re.search(pattern, content) for pattern in _STORE_NAME_PATTERNS)


def has_store_inquiry(content: str) -> bool:
    if not content:
        return False
    if has_advantage_question(content) or has_case_request(content) or has_fee_or_refund_dispute(content):
        return False
    if any(term in content for term in _TRUST_TERMS):
        return False
    if any(term in content for term in _STORE_CORE_TERMS):
        return True
    if _has_recommendation_shape(content) and _has_location_hint(content):
        return True
    if _has_status_shape(content) and ("店" in content or _has_location_hint(content)):
        return True
    if _has_store_name_shape(content):
        return True
    if ("店" in content or "门店" in content) and _has_location_hint(content):
        return True
    return False
