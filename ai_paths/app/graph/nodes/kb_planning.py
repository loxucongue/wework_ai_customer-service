from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.common import dedupe_strings
from app.graph.nodes.kb_slice_parsing import pricing_rows_from_kb
from app.graph.nodes.pricing_context import canonical_price_project, is_broad_price_category
from app.graph.nodes.project_kb_context import project_direction_name_candidates, project_slices_from_tool_results
from app.graph.runtime_context import contextual_price_project
from app.graph.state import AgentState


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
