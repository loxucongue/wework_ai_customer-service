from __future__ import annotations

import re

from app.graph.state import AgentState
from app.policies.constants import PROJECT_KEYWORDS

_BROAD_PROJECT_TERMS = {"祛斑", "淡斑", "斑", "色沉", "肤色不均", "痘印", "痘坑", "毛孔", "抗衰", "紧致", "暗沉"}
_GENERIC_QUERY_TERMS = {
    "",
    "多少钱",
    "价格",
    "项目价格",
    "这种多少钱",
    "这种大概多少钱",
    "这个多少钱",
    "大概多少钱",
    "一次多少钱",
    "普通一次多少钱",
    "预算太高",
    "太贵",
    "太贵了",
}
_NEED_SIGNAL_TERMS = [
    "点状",
    "斑点",
    "斑",
    "色沉",
    "肤色不均",
    "暗沉",
    "泛红",
    "毛孔",
    "出油",
    "黑头",
    "闭口",
    "痘印",
    "痘坑",
    "敏感",
    "松弛",
    "法令纹",
    "眼袋",
    "黑眼圈",
    "泪沟",
]


def needs_project_direction_before_price(state: AgentState, content: str) -> bool:
    if explicit_project_from_content(content):
        return False
    if need_terms_from_state(state, content):
        return True
    return is_generic_query(content)


def price_query_from_state(state: AgentState, content: str) -> str:
    project = explicit_project_from_content(content)
    if project:
        return project
    need_query = need_query_from_state(state, content)
    if need_query and need_query != "项目建议 适合人群":
        price_terms = [term for term in need_query.split() if term not in {"项目建议", "替换词名称", "适合人群"}]
        return " ".join([*price_terms, "价格"]).strip()
    return "项目价格"


def need_query_from_state(state: AgentState, content: str) -> str:
    terms = need_terms_from_state(state, content)
    if not terms and content and not is_generic_query(content):
        terms.append(content)
    if any(_contains_any(term, ["斑", "色沉", "肤色不均", "暗沉"]) for term in terms):
        terms.extend(["针对性色素淡化", "肤色改善"])
    if any(_contains_any(term, ["毛孔", "出油", "黑头"]) for term in terms):
        terms.append("毛孔肤质改善")
    if any(_contains_any(term, ["痘印", "痘坑", "闭口"]) for term in terms):
        terms.append("痘印痘坑肤质改善")
    if any(_contains_any(term, ["敏感", "泛红", "屏障"]) for term in terms):
        terms.append("敏感泛红修护")
    terms.extend(["项目建议", "替换词名称"])
    return " ".join(_dedupe_strings([term for term in terms if term])[:10]) or "项目建议 适合人群"


def need_terms_from_state(state: AgentState, content: str) -> list[str]:
    terms: list[str] = []
    image_info = state.get("image_info") or {}
    if isinstance(image_info, dict):
        concerns = image_info.get("visible_concerns")
        if isinstance(concerns, list):
            terms.extend(str(item).strip() for item in concerns[:6] if str(item).strip())
        text_clues = image_info.get("text_clues")
        if isinstance(text_clues, list):
            terms.extend(str(item).strip() for item in text_clues[:4] if str(item).strip())

    profile = state.get("customer_profile") or {}
    if isinstance(profile, dict):
        for key in ("needs", "pain_points", "concerns"):
            values = profile.get(key)
            if isinstance(values, list):
                terms.extend(str(item).strip() for item in values[:4] if str(item).strip())

    if content and not is_generic_query(content):
        if any(term in content for term in _NEED_SIGNAL_TERMS):
            terms.append(content)
        else:
            for term in _NEED_SIGNAL_TERMS:
                if term in content:
                    terms.append(term)

    recent_text = _recent_conversation_text(state, limit=6)
    for term in _NEED_SIGNAL_TERMS:
        if term in recent_text:
            terms.append(term)
    return _dedupe_strings(terms)[:8]


def explicit_project_from_content(content: str) -> str:
    for project in PROJECT_KEYWORDS:
        if project in _BROAD_PROJECT_TERMS:
            continue
        if project and project in content:
            return project
    return ""


def is_generic_query(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？?~～、,.!]", "", str(text or "").strip())
    if normalized in _GENERIC_QUERY_TERMS:
        return True
    if len(normalized) <= 3 and any(term in normalized for term in ["价格", "多少", "贵"]):
        return True
    has_project = bool(explicit_project_from_content(normalized))
    has_need = any(term in normalized for term in _NEED_SIGNAL_TERMS)
    return not has_project and not has_need and any(term in normalized for term in ["多少钱", "价格", "预算", "贵"])


def _recent_conversation_text(state: AgentState, limit: int = 6) -> str:
    history = state.get("conversation_history") or []
    return "\n".join(str(item) for item in history[-limit:])


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _contains_any(text: str, candidates: list[str]) -> bool:
    return any(candidate in text for candidate in candidates)
