from __future__ import annotations

import re

from app.graph.nodes.common import dedupe_strings
from app.graph.nodes.pricing_context import canonical_price_project, extract_project
from app.graph.planner.runtime_plan import planner_project_hints
from app.graph.signals.general import recent_conversation_text
from app.graph.state import AgentState
from app.policies.constants import PROJECT_KEYWORDS


def recent_project_from_state(state: AgentState) -> str:
    text = recent_conversation_text(state)
    project = extract_project(text)
    return canonical_price_project(project)


def contextual_price_project(state: AgentState) -> str:
    content = str(state.get("normalized_content") or "")
    direct = canonical_price_project(extract_project(content))
    if direct:
        return direct

    for hint in planner_project_hints(state):
        value = canonical_price_project(hint)
        if value:
            return value

    return recent_project_from_state(state)


def project_direction_names_from_state(state: AgentState) -> list[str]:
    names: list[str] = []
    content = str(state.get("normalized_content") or "")
    normalized_content = re.sub(r"\s+", "", content)

    for keyword in PROJECT_KEYWORDS:
        text = str(keyword or "").strip()
        if text and text in content and text not in names:
            names.append(text)

    for hint in planner_project_hints(state):
        value = str(hint or "").strip()
        if value and value not in names:
            names.append(value)

    if any(term in normalized_content for term in ("S10", "斑", "黑色素", "色沉", "肤色不均")):
        names.append("S10")
        names.append("斑点改善方向")
    return dedupe_strings(names)[:8]
