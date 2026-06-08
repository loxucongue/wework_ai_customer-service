from __future__ import annotations

import re

from app.graph.store_anchor import current_store_anchor_from_state, known_store_name_matches
from app.graph.state import AgentState
from app.graph.task_time_slots import (
    has_time_period,
    same_clock_hour,
    visit_date_from_text,
    visit_time_from_text,
)
from app.policies.constants import CITY_NAMES


def recent_text(state: AgentState, limit: int = 10) -> str:
    history = state.get("conversation_history") or []
    return "\n".join(str(item) for item in history[-limit:])


def city_from_text(text: str) -> str:
    for city in CITY_NAMES:
        if city in text:
            return city
    return ""


def store_name_from_state(state: AgentState) -> str:
    return current_store_anchor_from_state(state)


def store_name_from_text(text: str, city: str = "") -> str:
    alias_candidates: list[str] = []
    regex_candidates: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"^(用户|客户|助手|小贝)\s*[:：]\s*", "", line.strip())
        if not cleaned:
            continue
        if line_expresses_unknown_store(cleaned):
            continue
        if any(term in cleaned for term in ["百星", "思明", "二店", "徐汇", "静安", "浦东", "武侯", "渝北", "嘉定"]):
            if "百星" in cleaned:
                alias_candidates.append(f"{city or '厦门'}百星")
            elif "思明" in cleaned:
                alias_candidates.append(f"{city or '厦门'}思明")
            elif "二店" in cleaned:
                alias_candidates.append(f"{city or ''}二店".strip())
            elif "徐汇" in cleaned:
                alias_candidates.append("上海徐汇")
            elif "静安" in cleaned:
                alias_candidates.append("上海静安")
            elif "浦东" in cleaned:
                alias_candidates.append("上海浦东")
            elif "武侯" in cleaned:
                alias_candidates.append("成都武侯")
            elif "渝北" in cleaned:
                alias_candidates.append("重庆渝北")
            elif "嘉定" in cleaned:
                alias_candidates.append("嘉定")
        matches = known_store_name_matches(cleaned)
        if matches:
            regex_candidates.append(matches[-1][0])
    if alias_candidates:
        return alias_candidates[-1]
    return regex_candidates[-1] if regex_candidates else ""


def line_expresses_unknown_store(text: str) -> bool:
    unknown_terms = ["不知道哪家", "不确定哪家", "不知道哪个店", "不确定哪个店", "不知道门店", "不确定门店", "哪家方便", "附近哪家"]
    return any(term in text for term in unknown_terms)


def party_size_from_text(text: str) -> int:
    if not text:
        return 0
    friend = re.search(r"带\s*(\d+)\s*个?朋友", text)
    if friend:
        return int(friend.group(1)) + 1
    people = re.search(r"(\d+)\s*个?人", text)
    if people:
        return int(people.group(1))
    chinese_people = {
        "两个人": 2,
        "二个人": 2,
        "三个人": 3,
        "四个人": 4,
        "五个人": 5,
        "我和朋友": 2,
        "带朋友": 2,
        "带闺蜜": 2,
    }
    for word, value in chinese_people.items():
        if word in text:
            return value
    return 0
