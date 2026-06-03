from __future__ import annotations

from typing import Any

from app.graph.planner_query_terms import (
    explicit_project_from_content,
    is_generic_query,
    need_query_from_state,
    needs_project_direction_before_price,
    price_query_from_state,
)
from app.graph.planner_project_signals import has_case_request
from app.graph.state import AgentState


def needs_default_tool_plan(skill: Any, tool_plan: Any) -> bool:
    skill_name = str(skill or "")
    if not isinstance(tool_plan, list) or not tool_plan:
        return True

    tool_names = {
        str(item.get("name") or "")
        for item in tool_plan
        if isinstance(item, dict) and item.get("name")
    }
    if not tool_names:
        return True

    required_kbs_by_skill = {
        "project_consult": {"project_qa", "case_studies"},
        "price_consult": {"project_price"},
        "trust_build": {"trust_assets"},
        "competitor": {"competitor_qa"},
        "after_sales": {"after_sales_qa"},
    }
    required_kbs = required_kbs_by_skill.get(skill_name)
    if required_kbs:
        return not any(
            isinstance(item, dict)
            and item.get("name") == "kb_search"
            and item.get("kb_name") in required_kbs
            for item in tool_plan
        )

    if skill_name == "store":
        return "store_lookup" not in tool_names
    if skill_name == "appointment":
        return not ({"store_lookup", "available_time"} & tool_names)
    if skill_name == "handoff":
        return "professional_assist" not in tool_names
    return False


def default_tool_plan(state: AgentState, item: dict[str, Any]) -> list[dict[str, str]]:
    skill = str(item.get("skill") or "")
    content = state.get("normalized_content") or ""
    query = default_query_for_skill(skill, content=content, state=state)
    if skill == "project_consult":
        if has_case_request(content):
            return [{"name": "kb_search", "kb_name": "case_studies", "query": query, "purpose": "检索效果案例、前后对比或客户做完效果资料"}]
        return [{"name": "kb_search", "kb_name": "project_qa", "query": query, "purpose": "检索改善方向和项目建议"}]
    if skill == "price_consult":
        plan: list[dict[str, str]] = []
        if needs_project_direction_before_price(state, content):
            plan.append(
                {
                    "name": "kb_search",
                    "kb_name": "project_qa",
                    "query": need_query_from_state(state, content),
                    "purpose": "先检索可考虑的改善方向和替换词名称",
                }
            )
        plan.append({"name": "kb_search", "kb_name": "project_price", "query": query, "purpose": "按项目或改善方向模糊匹配价格"})
        return plan
    if skill == "trust_build":
        return [{"name": "kb_search", "kb_name": "trust_assets", "query": query, "purpose": "检索资质、背书或收费透明说明"}]
    if skill == "competitor":
        return [{"name": "kb_search", "kb_name": "competitor_qa", "query": query, "purpose": "检索竞品应对话术边界"}]
    if skill == "after_sales":
        return [{"name": "kb_search", "kb_name": "after_sales_qa", "query": query, "purpose": "检索售后护理和风险边界"}]
    if skill == "store":
        return [{"name": "store_lookup", "query": content, "purpose": "查询匹配门店"}]
    if skill == "appointment":
        return [{"name": "store_lookup", "query": content, "purpose": "确认预约门店"}, {"name": "available_time", "query": content, "purpose": "查询可约时间"}]
    if skill == "handoff":
        return [{"name": "professional_assist", "purpose": "需要专业同事核对真实记录"}]
    return [{"name": "no_tool", "purpose": "无需工具"}]


def default_query_for_skill(skill: str, *, content: str = "", state: AgentState | None = None) -> str:
    content = (content or "").strip()
    if skill == "project_consult":
        if has_case_request(content):
            query_parts = [content, "案例", "效果", "前后对比", "改善参考"]
            return " ".join(part for part in query_parts if part).strip()
        if state:
            image_info = state.get("image_info") or {}
            concerns = image_info.get("visible_concerns") if isinstance(image_info, dict) else []
            if isinstance(concerns, list) and concerns:
                return need_query_from_state(state, content)
        return need_query_from_state(state, content) if state else content or "项目建议 适合人群"
    if skill == "price_consult":
        if state:
            return price_query_from_state(state, content)
        project = explicit_project_from_content(content)
        return project or content or "项目价格"
    if skill == "trust_build":
        return content or "正规 靠谱 资质 收费透明"
    if skill == "competitor":
        return content or "竞品对比 不诋毁 对比维度"
    if skill == "after_sales":
        return content or "售后护理 风险边界"
    return content


def normalize_tool_plan_for_intent(state: AgentState, item: dict[str, Any]) -> list[dict[str, str]]:
    plan = item.get("tool_plan")
    if not isinstance(plan, list):
        return []
    skill = str(item.get("skill") or "")
    content = state.get("normalized_content") or ""
    normalized: list[dict[str, str]] = []

    for tool in plan:
        if not isinstance(tool, dict):
            continue
        copied = {str(key): str(value) for key, value in tool.items() if value is not None}
        if copied.get("name") == "kb_search":
            kb_name = copied.get("kb_name", "")
            query = copied.get("query", "")
            if kb_name in {"project_qa", "case_studies"}:
                copied["query"] = need_query_from_state(state, content) if is_generic_query(query) else query
            elif kb_name == "project_price":
                copied["query"] = price_query_from_state(state, content) if is_generic_query(query) else query
        normalized.append(copied)

    if skill == "project_consult" and has_case_request(content):
        has_case_studies = any(tool.get("name") == "kb_search" and tool.get("kb_name") == "case_studies" for tool in normalized)
        if not has_case_studies:
            normalized.insert(
                0,
                {
                    "name": "kb_search",
                    "kb_name": "case_studies",
                    "query": need_query_from_state(state, content),
                    "purpose": "检索效果案例、前后对比或客户做完效果资料",
                },
            )

    if skill == "price_consult":
        has_project_qa = any(tool.get("name") == "kb_search" and tool.get("kb_name") == "project_qa" for tool in normalized)
        has_project_price = any(tool.get("name") == "kb_search" and tool.get("kb_name") == "project_price" for tool in normalized)
        if needs_project_direction_before_price(state, content) and not has_project_qa:
            normalized.insert(
                0,
                {
                    "name": "kb_search",
                    "kb_name": "project_qa",
                    "query": need_query_from_state(state, content),
                    "purpose": "先检索可考虑的改善方向和替换词名称",
                },
            )
        if not has_project_price:
            normalized.append(
                {
                    "name": "kb_search",
                    "kb_name": "project_price",
                    "query": price_query_from_state(state, content),
                    "purpose": "按项目或改善方向模糊匹配价格",
                }
            )

    return normalized[:4]
