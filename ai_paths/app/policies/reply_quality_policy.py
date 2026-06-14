from __future__ import annotations

from app.policies.identity_policy import FORBIDDEN_IDENTITY_TERMS


REPLY_HARD_FORBIDDEN_TERMS = (
    *FORBIDDEN_IDENTITY_TERMS,
    "返现",
    "真人客服",
    "我不是AI",
    "我不是机器人",
    "系统查询",
    "系统可查",
    "系统里",
    "工具返回",
    "知识库",
    "检索结果",
    "内部分析",
    "判断依据",
    "reply_brief",
    "module_outputs",
    "route_result",
    "subflow",
    "intent",
    "debug",
    "调试信息",
    "转人工",
    "转接",
    "转人",
    "转过去",
    "帮您转",
    "国内最先进",
)

REPLY_THIRD_PERSON_CUSTOMER_TERMS = (
    "客户当前",
    "客户提到",
    "客户表示",
    "客户偏好",
    "客户问题",
)

REPLY_PRICE_RULE_TERMS = (
    "活动价",
    "活动报价",
    "统一活动报价",
    "当前活动价",
    "体验价",
    "最终体验价",
    "定金",
    "尾款",
    "多退少补",
    "到店再付",
    "锁定名额",
)

REPLY_LONG_FORM_TASK_TYPES = {
    "store_inquiry",
    "appointment",
    "appointment_status",
    "appointment_change",
    "appointment_cancel",
}

REPLY_MAX_TEXT_MESSAGE_CHARS = 180
REPLY_MAX_TEXT_MESSAGE_CHARS_LONG_FORM = 260
REPLY_MAX_TOTAL_TEXT_CHARS = 300
REPLY_MAX_TOTAL_TEXT_CHARS_LONG_FORM = 460
