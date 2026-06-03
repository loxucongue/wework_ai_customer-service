from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.state import AgentState


@dataclass(frozen=True)
class LegacySkillDispatchCallbacks:
    price_skill_output: Callable[[str, dict[str, Any], AgentState], dict[str, Any]]
    trust_skill_output: Callable[[str, dict[str, Any]], dict[str, Any]]
    project_skill_output: Callable[[str, dict[str, Any], AgentState], dict[str, Any]]
    competitor_skill_output: Callable[[str, dict[str, Any]], dict[str, Any]]
    after_sales_skill_output: Callable[[str, dict[str, Any]], dict[str, Any]]
    store_skill_output: Callable[[str, dict[str, Any]], dict[str, Any]]
    basic_skill_output: Callable[..., dict[str, Any]]
    json_dumps: Callable[[Any], str]


def skill_output(
    skill: str,
    content: str,
    tool_results: dict[str, Any],
    state: AgentState,
    callbacks: LegacySkillDispatchCallbacks,
) -> dict[str, Any]:
    if skill == "price_consult":
        return callbacks.price_skill_output(content, tool_results, state)
    if skill == "trust_build":
        return callbacks.trust_skill_output(content, tool_results)
    if skill == "project_consult":
        return callbacks.project_skill_output(content, tool_results, state)
    if skill == "competitor":
        return callbacks.competitor_skill_output(content, tool_results)
    if skill == "after_sales":
        return callbacks.after_sales_skill_output(content, tool_results)
    if skill == "store":
        return callbacks.store_skill_output(content, tool_results)
    if skill == "appointment":
        active_task = state.get("active_task") or {}
        facts = [callbacks.json_dumps(active_task)] if isinstance(active_task, dict) and active_task else []
        return callbacks.basic_skill_output(
            skill,
            ["预约相关问题必须复用已知门店、日期、时间和人数，继续推进当前预约任务；不要切回项目咨询。"],
            suggested_next_step=str(active_task.get("next_action") or "按当前预约诉求处理") if isinstance(active_task, dict) else "按当前预约诉求处理",
            facts=facts,
        )
    return callbacks.basic_skill_output(skill, ["小贝先按客户当前问题做轻量承接。"])

