from __future__ import annotations

import re

from app.graph import reply_filters
from app.graph.nodes.reply_validation import message_content_text
from app.graph.state import AgentState

_HARD_FORBIDDEN_TERMS = (
    "包接送",
    "车费报销",
    "医美",
    "医疗美容",
    "正规皮肤管理机构",
    "正规备案",
    "CFDA",
    "进口仪器",
    "执业许可证",
    "持证上岗",
    "持证技师",
    "持证老师",
    "持证合规",
    "临床",
    "更安全",
    "所有门店都持有",
    "所有门店均具备",
    "所有门店资质合规",
    "绝无",
    "绝对安全",
    "保证效果",
    "包效果",
    "100%见效",
    "百分百见效",
    "根治",
    "一次根治",
    "一次一定好",
    "一定有效",
    "肯定有效",
    "真人客服",
    "我不是AI",
    "我不是机器人",
    "系统查询",
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
)

_THIRD_PERSON_CUSTOMER_TERMS = (
    "客户当前",
    "客户提到",
    "客户表示",
    "客户偏好",
    "客户问题",
)

_PRICE_CLAIM_PATTERN = re.compile(r"(?<!\d)\d{2,5}\s*元|[一二三四五六七八九十百千万]+元")
_PRICE_RULE_TERMS = (
    "活动价",
    "体验价",
    "最终体验价",
    "定金",
    "尾款",
    "多退少补",
    "到店再付",
    "锁定名额",
)


def model_reply_unsafe(
    state: AgentState,
    messages: list[dict[str, object]],
) -> bool:
    text = "\n".join(
        message_content_text(message.get("content"))
        for message in messages
        if isinstance(message, dict) and message.get("type") != "human_handoff"
    ).strip()
    if not text:
        return True
    if reply_filters.has_internal_reply_leak(text):
        return True
    if any(term in text for term in _HARD_FORBIDDEN_TERMS):
        return True
    if any(term in text for term in _THIRD_PERSON_CUSTOMER_TERMS):
        return True
    if _has_unbacked_price_claim(state, text):
        return True
    return False


def _has_unbacked_price_claim(state: AgentState, text: str) -> bool:
    if _has_price_facts(state):
        return False
    if _PRICE_CLAIM_PATTERN.search(text):
        return True
    return any(term in text for term in _PRICE_RULE_TERMS)


def _has_price_facts(state: AgentState) -> bool:
    fact_envelope = state.get("fact_envelope") if isinstance(state, dict) else {}
    if not isinstance(fact_envelope, dict):
        return False
    structured = fact_envelope.get("structured_facts")
    if isinstance(structured, dict) and structured.get("price_facts"):
        return True
    usable = fact_envelope.get("usable_facts")
    if isinstance(usable, list):
        return any("pricing_" in str(item) or "project_price" in str(item) for item in usable)
    return False
