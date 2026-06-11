from __future__ import annotations

from app.graph.planner.runtime_plan import planner_task_views
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
    "咨询一下",
    "问一下",
    "介绍一下",
    "有什么项目",
    "项目有哪些",
    "看看项目",
    "你好",
    "在吗",
]

SPECIFIC_NEED_TERMS = [
    "斑",
    "痘",
    "毛孔",
    "暗沉",
    "色沉",
    "细纹",
    "松弛",
    "抗衰",
    "补水",
    "提亮",
    "黑眼圈",
    "眼袋",
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
    "正规吗",
    "靠谱吗",
]

LOW_INFORMATION_TASK_TYPES = {"general_consult", "emotion_chat"}


def has_continuation_reference(content: str) -> bool:
    text = str(content or "")
    return any(term in text for term in CONTINUATION_TERMS)


def is_generic_opening_without_specific_need(content: str) -> bool:
    text = str(content or "").strip()
    if not text or has_continuation_reference(text):
        return False
    if not any(term in text for term in GENERIC_OPENING_TERMS):
        return False
    return not any(term in text for term in SPECIFIC_NEED_TERMS)


def should_suppress_profile_memory_for_reply(state: AgentState) -> bool:
    """低信息量开场轮次，不主动带出旧画像、旧项目和旧痛点。"""
    content = str(state.get("normalized_content") or "").strip()
    if has_continuation_reference(content):
        return False
    if is_low_information_content(content):
        return True
    if is_generic_opening_without_specific_need(content):
        return True

    task_views = planner_task_views(state)
    if not task_views:
        return False
    task_types = {
        str(view.get("type") or "").strip()
        for view in task_views
        if isinstance(view, dict)
    }
    return bool(task_types) and task_types <= LOW_INFORMATION_TASK_TYPES


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
            "本轮是低信息量开场或简单承接，不要主动带出旧画像、旧项目、旧痛点或客户标签。"
            if suppress
            else "可以在不盖过当前问题的前提下，少量引用相关历史信息。"
        ),
    }
