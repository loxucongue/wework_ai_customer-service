from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class AfterSalesSkillCallbacks:
    first_after_sales_slice: Callable[[list[Any]], dict[str, str]]
    clean_after_sales_text: Callable[[str], str]
    split_collect_items: Callable[[str], list[str]]


def after_sales_skill_output(
    content: str,
    tool_results: dict[str, Any],
    callbacks: AfterSalesSkillCallbacks,
) -> dict[str, Any]:
    """Build factual after-sales skill output for the final reply model."""

    after_sales_result = tool_results.get("after_sales_qa", {}) if isinstance(tool_results, dict) else {}
    items = after_sales_result.get("items", []) if isinstance(after_sales_result, dict) else []
    if not isinstance(items, list):
        items = []
    parsed = callbacks.first_after_sales_slice(items)
    risk_level = parsed.get("risk_level", "")
    say = parsed.get("say", "")
    collect = parsed.get("collect", "")
    next_step = parsed.get("next_step", "")

    reply_points: list[str] = []
    facts: list[str] = []
    missing_slots: list[str] = []
    risk_flags: list[str] = []

    if risk_level:
        facts.append(f"风险等级：{risk_level}")
        if risk_level in {"高", "严重"}:
            risk_flags.append(risk_level)
    if say:
        reply_points.append(callbacks.clean_after_sales_text(say))
    else:
        reply_points.append("售后反应需要结合项目、操作时间、照片和是否加重判断；最终回复先承接不适，再收集必要信息，不能直接诊断或说没事。")
    if collect:
        missing_slots = callbacks.split_collect_items(collect)[:5]
    else:
        missing_slots = ["项目", "做完第几天", "现在主要表现", "是否加重", "照片"]
    if next_step:
        facts.append(f"下一步：{next_step}")

    return {
        "skill": "after_sales",
        "intent": "after_sales",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": missing_slots,
        "risk_flags": risk_flags,
        "suggested_next_step": next_step or "补充项目、时间和照片",
        "confidence": 0.78 if parsed else 0.65,
    }
