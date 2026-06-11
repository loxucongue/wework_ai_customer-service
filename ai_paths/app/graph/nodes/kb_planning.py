from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.action_queries import safe_query_from_state
from app.graph.nodes.common import dedupe_strings, looks_bad_text
from app.graph.nodes.kb_slice_parsing import pricing_rows_from_kb
from app.graph.nodes.pricing_context import canonical_price_project, extract_project, is_broad_price_category
from app.graph.nodes.project_kb_context import project_direction_name_candidates, project_slices_from_tool_results
from app.graph.runtime_context import contextual_price_project, recent_project_from_state
from app.graph.state import AgentState


def planned_kb_searches(action: dict[str, Any], state: AgentState | None = None) -> list[dict[str, str]]:
    allowed = {"project_qa", "project_price", "sales_talk_qa", "case_studies", "competitor_qa", "after_sales_qa"}
    planned = action.get("tool_plan")
    if not isinstance(planned, list):
        return []

    result: list[dict[str, str]] = []
    for item in planned:
        if not isinstance(item, dict) or item.get("name") != "kb_search":
            continue

        kb_name = str(item.get("kb_name") or "").strip()
        if kb_name not in allowed:
            continue

        query = str(item.get("query") or "").strip()
        if state is not None:
            query = clean_planned_kb_query(
                state,
                str(action.get("type") or action.get("name") or "").strip(),
                str(action.get("subtype") or "").strip(),
                kb_name,
                query,
            )

        result.append(
            {
                "kb_name": kb_name,
                "query": query,
                "purpose": str(item.get("purpose") or "").strip(),
            }
        )
        if len(result) >= 3:
            break
    return result


def clean_planned_kb_query(state: AgentState, task_type: str, task_subtype: str, kb_name: str, query: str) -> str:
    text = str(query or "").strip()
    if text and not looks_generic_price_query(text) and not looks_bad_text(text):
        return text

    if kb_name == "project_qa":
        return _safe_query_from_state(state, "project_inquiry", task_subtype or "project_direction")

    if kb_name == "project_price":
        project = canonical_price_project(recent_project_from_state(state))
        if project and not is_broad_price_category(project):
            return project

        direction_query = _safe_query_from_state(state, "project_inquiry", "project_direction")
        direction_terms = [
            term
            for term in direction_query.split()
            if term not in {"项目建议", "适合方向", "适合人群", "改善方向"}
        ]
        return " ".join([*direction_terms, "价格"]).strip()

    return _safe_query_from_state(state, task_type, task_subtype)


def looks_generic_price_query(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？、?!?]", "", str(text or "").strip())
    if normalized in {
        "",
        "价格",
        "多少钱",
        "项目价格",
        "这个多少钱",
        "这种多少钱",
        "这种大概多少钱",
        "大概多少钱",
        "一次多少钱",
        "普通一次多少钱",
    }:
        return True

    extracted = extract_project(normalized)
    has_project = bool(extracted) and not is_broad_price_category(canonical_price_project(extracted))
    has_need = any(
        term in normalized for term in ["斑", "色沉", "肤色不均", "暗沉", "毛孔", "痘印", "痘坑", "敏感", "泛红"]
    )
    return not has_project and not has_need and any(
        term in normalized for term in ["价格", "多少钱", "预算", "费用"]
    )


def needs_project_price_followup(tasks: list[dict[str, Any]], tool_results: dict[str, Any], state: AgentState) -> bool:
    if not any(
        str(task.get("type") or task.get("subtype") or task.get("name") or "").strip() == "price_inquiry"
        for task in tasks
        if isinstance(task, dict)
    ):
        return False

    if pricing_rows_from_kb(tool_results):
        return False

    project = canonical_price_project(contextual_price_project(state))
    if project and not is_broad_price_category(project):
        return False

    return bool(project_price_followup_queries(tool_results))


def project_price_followup_queries(tool_results: dict[str, Any]) -> list[str]:
    queries: list[str] = []

    for item in project_slices_from_tool_results(tool_results):
        for key in ("replacement_name", "title"):
            value = str(item.get(key) or "").strip()
            if not value:
                continue
            value = value.split("|")[-1].strip() if "|" in value else value
            value = re.sub(r"^(切片\d+\s*)", "", value).strip()
            queries.extend(project_direction_name_candidates(value))

        direction = str(item.get("direction") or "").strip()
        if direction:
            for candidate in [
                "肤色改善",
                "针对性色素淡化",
                "毛孔肤质改善",
                "痘印痘坑肤质改善",
                "敏感泛红修护",
            ]:
                if candidate in direction:
                    queries.append(candidate)

    return dedupe_strings(queries)[:2]


def _safe_query_from_state(state: AgentState, task_type: str, task_subtype: str = "") -> str:
    from app.graph.runtime_common import extract_price_digits

    return safe_query_from_state(
        state,
        task_type,
        task_subtype=task_subtype,
        canonical_price_project=canonical_price_project,
        contextual_price_project=contextual_price_project,
        extract_price_digits=extract_price_digits,
        extract_project=extract_project,
    )
