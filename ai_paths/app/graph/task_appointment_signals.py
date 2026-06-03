from __future__ import annotations

from app.graph.state import AgentState
from app.graph.task_slots import (
    has_time_period,
    party_size_from_text,
    recent_text,
    same_clock_hour,
    visit_date_from_text,
    visit_time_from_text,
)
from app.policies.constants import (
    AFTER_SALES_KEYWORDS,
    APPOINTMENT_KEYWORDS,
    CAMPAIGN_KEYWORDS,
    COMPETITOR_KEYWORDS,
    PRICE_KEYWORDS,
    PROJECT_KEYWORDS,
    TRUST_KEYWORDS,
)


def is_appointment_followup(state: AgentState) -> bool:
    content = (state.get("normalized_content") or "").strip()
    if not content:
        return False
    if has_non_appointment_interrupt(content) and not has_current_appointment_signal(content):
        return False
    if any(term in content for term in APPOINTMENT_KEYWORDS):
        return True
    if visit_date_from_text(content) or visit_time_from_text(content) or party_size_from_text(content):
        return True
    short_followups = [
        "是的",
        "对",
        "对的",
        "嗯",
        "好",
        "好的",
        "可以",
        "可以的",
        "行",
        "亲",
        "约好吗",
        "约好了么",
        "约好了吗",
        "可以约吗",
        "能约吗",
        "行吗",
        "可以吗",
        "就这个",
        "就这家",
        "这家吧",
        "那家吧",
        "确定",
        "确认",
        "肯定是今天",
        "今天啊",
        "就是今天",
        "现在就过来",
        "现在过来",
        "现在过去",
        "马上过来",
        "马上过去",
        "直接过去",
        "就过来",
        "下午五点",
        "下午5点",
    ]
    if any(term in content for term in short_followups):
        return looks_like_appointment_context(recent_text(state, limit=10))
    if len(content) <= 8 and any(term in content for term in ["今天", "明天", "后天", "下午", "上午", "晚上", "五点", "5点", "约", "过来", "过去", "位置"]):
        return looks_like_appointment_context(recent_text(state, limit=10))
    return False


def has_strong_new_non_appointment_intent(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    if has_non_appointment_interrupt(content):
        return True
    strong_groups = [
        TRUST_KEYWORDS,
        PRICE_KEYWORDS,
        CAMPAIGN_KEYWORDS,
        COMPETITOR_KEYWORDS,
        AFTER_SALES_KEYWORDS,
    ]
    if any(any(term in content for term in group) for group in strong_groups):
        return True
    if any(term in content for term in PROJECT_KEYWORDS) and not any(term in content for term in APPOINTMENT_KEYWORDS):
        return True
    return False


def has_current_appointment_signal(content: str) -> bool:
    if not content:
        return False
    return bool(
        any(term in content for term in APPOINTMENT_KEYWORDS)
        or visit_date_from_text(content)
        or visit_time_from_text(content)
        or party_size_from_text(content)
    )


def has_non_appointment_interrupt(content: str) -> bool:
    if not content:
        return False
    hard_terms = [
        "投诉",
        "退款",
        "退钱",
        "退给我",
        "骗人",
        "骗子",
        "被坑",
        "坑我",
        "太坑",
        "乱收费",
        "加钱",
        "额外收费",
        "收费不一样",
        "效果不好",
        "效果一点也不好",
        "效果一点都不好",
        "一点效果都没有",
        "一点用都没",
        "没效果",
        "没变化",
        "跟没做一样",
        "白做",
        "白花钱",
        "为什么这么慢",
        "怎么这么慢",
        "回复太慢",
        "回消息太慢",
        "没人回",
        "等这么久",
    ]
    return any(term in content for term in hard_terms)


def looks_like_appointment_context(text: str) -> bool:
    if not text:
        return False
    appointment_terms = [
        "预约",
        "到店",
        "来店",
        "接待",
        "过来",
        "过去",
        "可约",
        "空闲",
        "时间",
        "几点",
        "位置",
        "安排位置",
        "五点",
        "5点",
        "下午",
        "上午",
        "现在过来",
        "现在过去",
        "马上过来",
        "直接过去",
    ]
    store_terms = ["门店", "店", "地址", "厦门", "上海", "重庆", "成都", "嘉定", "百星", "思明", "徐汇", "静安", "浦东"]
    strong_schedule_terms = ["安排位置", "位置", "几点", "几点呀", "现在过来", "现在过去", "马上过来", "直接过去", "可约", "空闲"]
    if any(term in text for term in strong_schedule_terms):
        return True
    return any(term in text for term in appointment_terms) and any(term in text for term in store_terms)


def visit_time_from_context(content: str, recent: str) -> str:
    if has_non_appointment_interrupt(content) and not has_current_appointment_signal(content):
        return ""
    current_time = visit_time_from_text(content)
    recent_time = visit_time_from_text(recent)
    if (
        current_time
        and recent_time
        and not has_time_period(content)
        and has_time_period(recent)
        and same_clock_hour(current_time, recent_time)
    ):
        return recent_time
    return current_time or recent_time
