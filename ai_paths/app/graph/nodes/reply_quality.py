from __future__ import annotations

from app.graph import reply_filters
from app.graph.nodes.reply_validation import message_content_text
from app.graph.state import AgentState

_HARD_FORBIDDEN_TERMS = (
    "包接送",
    "车费报销",
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


def model_reply_unsafe(
    state: AgentState,
    messages: list[dict[str, object]],
) -> bool:
    del state
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
    return False
