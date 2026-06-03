from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class CompetitorSkillCallbacks:
    first_competitor_slice: Callable[[list[Any]], dict[str, str]]
    competitor_scenario: Callable[[str], str]
    extract_project: Callable[[str], str]
    extract_price_digits: Callable[[str], list[str]]
    competitor_slice_matches: Callable[[str, str, str], bool]
    clean_competitor_text: Callable[[str], str]
    competitor_default_reply: Callable[[str, str, list[str], str], str]
    split_collect_items: Callable[[str], list[str]]
    competitor_risk_terms: Callable[[str], list[str]]


def competitor_skill_output(
    content: str,
    tool_results: dict[str, Any],
    callbacks: CompetitorSkillCallbacks,
) -> dict[str, Any]:
    """Build factual competitor-skill output for the final reply model."""

    competitor_result = tool_results.get("competitor_qa", {}) if isinstance(tool_results, dict) else {}
    items = competitor_result.get("items", []) if isinstance(competitor_result, dict) else []
    if not isinstance(items, list):
        items = []

    parsed = callbacks.first_competitor_slice(items)
    say = parsed.get("say", "")
    collect = parsed.get("collect", "")
    next_step = parsed.get("next_step", "")
    target = parsed.get("target", "")
    forbidden = parsed.get("forbidden", "")
    scene_type = parsed.get("scene_type", "")

    scenario = callbacks.competitor_scenario(content)
    project = callbacks.extract_project(content)
    price_digits = callbacks.extract_price_digits(content)
    reply_points: list[str] = []
    facts: list[str] = []
    missing_slots: list[str] = []

    if target:
        facts.append(f"回复目标：{target}")
    if forbidden:
        facts.append(f"禁用表达：{forbidden}")
    if say and callbacks.competitor_slice_matches(scenario, scene_type, say):
        reply_points.append(callbacks.clean_competitor_text(say))
    else:
        reply_points.append(callbacks.competitor_default_reply(content, project, price_digits, scenario))
    if collect:
        missing_slots = callbacks.split_collect_items(collect)[:5]
    elif "截图" in content or "报价" in content:
        missing_slots = ["项目", "产品/剂量", "部位", "次数", "是否含售后"]
    if next_step:
        facts.append(f"下一步：{next_step}")

    return {
        "skill": "competitor",
        "intent": "competitor_compare",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": missing_slots,
        "risk_flags": callbacks.competitor_risk_terms(content),
        "suggested_next_step": next_step or "拆清对比维度",
        "confidence": 0.78 if parsed else 0.68,
    }
