from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app.graph.state import AgentState


@dataclass(frozen=True)
class LegacyKbPlanningCallbacks:
    canonical_price_project: Callable[[str], str]
    contextual_price_project: Callable[[AgentState], str]
    dedupe_strings: Callable[[list[str]], list[str]]
    extract_project: Callable[[str], str]
    is_broad_price_category: Callable[[str], bool]
    looks_bad_text: Callable[[str], bool]
    pricing_rows_from_kb: Callable[[dict[str, Any]], list[dict[str, Any]]]
    project_direction_name_candidates: Callable[[str], list[str]]
    project_slices_from_tool_results: Callable[[dict[str, Any]], list[dict[str, str]]]
    recent_project_from_state: Callable[[AgentState], str]
    safe_query_from_state: Callable[[AgentState, str], str]


def planned_kb_searches(
    action: dict[str, Any],
    state: AgentState | None = None,
    *,
    callbacks: LegacyKbPlanningCallbacks,
) -> list[dict[str, str]]:
    allowed = {"project_qa", "project_price", "trust_assets", "competitor_qa", "after_sales_qa"}
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
            query = clean_planned_kb_query(state, str(action.get("name") or ""), kb_name, query, callbacks=callbacks)
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


def clean_planned_kb_query(
    state: AgentState,
    skill: str,
    kb_name: str,
    query: str,
    *,
    callbacks: LegacyKbPlanningCallbacks,
) -> str:
    text = str(query or "").strip()
    if text and not looks_generic_price_query(text, callbacks=callbacks) and not callbacks.looks_bad_text(text):
        return text
    if kb_name == "project_qa":
        return callbacks.safe_query_from_state(state, "project_consult")
    if kb_name == "project_price":
        project = callbacks.canonical_price_project(callbacks.recent_project_from_state(state))
        if project and not callbacks.is_broad_price_category(project):
            return project
        direction_query = callbacks.safe_query_from_state(state, "project_consult")
        direction_terms = [term for term in direction_query.split() if term not in {"项目建议", "替换词名称", "适合人群"}]
        return " ".join([*direction_terms, "价格"]).strip()
    return callbacks.safe_query_from_state(state, skill)


def looks_generic_price_query(text: str, *, callbacks: LegacyKbPlanningCallbacks) -> bool:
    normalized = re.sub(r"[\s，。！？?~～、,.!]", "", str(text or "").strip())
    if normalized in {"", "价格", "多少钱", "项目价格", "这个多少钱", "这种多少钱", "这种大概多少钱", "大概多少钱", "一次多少钱", "普通一次多少钱"}:
        return True
    extracted_project = callbacks.extract_project(normalized)
    has_project = bool(extracted_project) and not callbacks.is_broad_price_category(
        callbacks.canonical_price_project(extracted_project)
    )
    has_need = any(term in normalized for term in ["斑", "色沉", "肤色不均", "暗沉", "毛孔", "痘印", "痘坑", "敏感", "泛红"])
    return not has_project and not has_need and any(term in normalized for term in ["价格", "多少钱", "预算", "贵"])


def needs_project_price_followup(
    actions: list[dict[str, Any]],
    tool_results: dict[str, Any],
    state: AgentState,
    *,
    callbacks: LegacyKbPlanningCallbacks,
) -> bool:
    if not any(action.get("name") == "price_consult" for action in actions):
        return False
    if callbacks.pricing_rows_from_kb(tool_results):
        return False
    project = callbacks.canonical_price_project(callbacks.contextual_price_project(state))
    if project and not callbacks.is_broad_price_category(project):
        return False
    return bool(project_price_followup_queries(tool_results, callbacks=callbacks))


def project_price_followup_queries(
    tool_results: dict[str, Any],
    *,
    callbacks: LegacyKbPlanningCallbacks,
) -> list[str]:
    queries: list[str] = []
    for item in callbacks.project_slices_from_tool_results(tool_results):
        for key in ("replacement_name", "title"):
            value = str(item.get(key) or "").strip()
            if not value:
                continue
            value = value.split("|")[-1].strip() if "|" in value else value
            value = re.sub(r"^(切片\d+\s*)", "", value).strip()
            queries.extend(callbacks.project_direction_name_candidates(value))
        direction = str(item.get("direction") or "").strip()
        if direction:
            for candidate in ["肤色改善", "针对性色素淡化", "毛孔肤质改善", "痘印痘坑肤质改善", "敏感泛红修护"]:
                if candidate in direction:
                    queries.append(candidate)
    return callbacks.dedupe_strings(queries)[:2]
