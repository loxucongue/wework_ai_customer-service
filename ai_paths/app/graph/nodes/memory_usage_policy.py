from __future__ import annotations

from app.graph.planner_general_signals import is_low_information_content
from app.graph.state import AgentState


CONTINUATION_TERMS = [
    "刚刚",
    "刚才",
    "前面",
    "之前",
    "上次",
    "继续",
    "接着",
    "还是",
    "那个",
    "这个",
    "你说的",
    "刚说的",
    "上一条",
    "前一条",
]

GENERIC_OPENING_TERMS = [
    "了解一下",
    "了解下",
    "咨询一下",
    "咨询下",
    "问一下",
    "问下",
    "介绍一下",
    "介绍下",
    "有什么项目",
    "项目有哪些",
    "想看看项目",
    "看一下项目",
]

SPECIFIC_NEED_TERMS = [
    "斑",
    "痘",
    "毛孔",
    "暗沉",
    "色沉",
    "皱纹",
    "松弛",
    "抗衰",
    "补水",
    "美白",
    "黑眼圈",
    "眼袋",
    "水光",
    "皮秒",
    "光子",
    "热玛吉",
    "超声",
    "价格",
    "多少钱",
    "预算",
    "门店",
    "地址",
    "预约",
    "活动",
    "优惠",
    "案例",
    "效果",
    "正规",
    "靠谱",
]


def has_continuation_reference(content: str) -> bool:
    return any(term in (content or "") for term in CONTINUATION_TERMS)


def is_generic_opening_without_specific_need(content: str) -> bool:
    text = (content or "").strip()
    if not text or has_continuation_reference(text):
        return False
    if not any(term in text for term in GENERIC_OPENING_TERMS):
        return False
    return not any(term in text for term in SPECIFIC_NEED_TERMS)


def should_suppress_profile_memory_for_reply(state: AgentState) -> bool:
    """Return true when old profile facts should not actively shape this turn."""
    content = str(state.get("normalized_content") or "").strip()
    if has_continuation_reference(content):
        return False
    if is_low_information_content(content):
        return True
    if is_generic_opening_without_specific_need(content):
        return True
    intents = {
        str(item.get("intent") or "")
        for item in state.get("intents", [])
        if isinstance(item, dict)
    }
    return bool(intents) and intents <= {"emotion_chat"}


def memory_usage_policy_for_reply(state: AgentState) -> dict[str, object]:
    suppress = should_suppress_profile_memory_for_reply(state)
    return {
        "active_profile_memory": not suppress,
        "reason": (
            "current_turn_low_information_or_generic_opening"
            if suppress
            else "current_turn_allows_contextual_memory"
        ),
        "instruction": (
            "本轮只是问候、低信息承接或泛开场，不能主动提旧画像里的项目、痛点、预算、历史事件或客户昵称。"
            if suppress
            else "可以在不盖过当前问题的前提下使用相关历史画像。"
        ),
    }
