from __future__ import annotations

from app.graph.reply_filters import has_budget_or_price_answer
from app.graph.state import AgentState


def has_no_price_fact_phrase(text: str) -> bool:
    return any(
        term in text
        for term in [
            "没查到",
            "没有查到",
            "暂时没查到",
            "暂时没有查到",
            "暂未查到",
            "没有明确价格",
            "没有查到明确",
            "不乱报",
            "价格表没看到",
        ]
    )


def lacks_price_answer_for_price_question(state: AgentState, text: str) -> bool:
    content = str(state.get("normalized_content") or "")
    if not any(term in content for term in ["多少钱", "价格", "费用", "预算", "贵不贵"]):
        return False
    if has_budget_or_price_answer(text):
        return False
    if has_no_price_fact_phrase(text):
        return False
    return True
