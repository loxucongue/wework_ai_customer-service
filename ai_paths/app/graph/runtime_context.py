from __future__ import annotations

from app.graph.nodes.common import dedupe_strings
from app.graph.planner.runtime_plan import planner_project_hints
from app.graph.state import AgentState


def contextual_price_project(state: AgentState) -> str:
    for hint in planner_project_hints(state):
        value = str(hint or "").strip()
        if value:
            return value[:80]
    return ""


def project_direction_names_from_state(state: AgentState) -> list[str]:
    names: list[str] = []
    image_info = state.get("image_info") if isinstance(state.get("image_info"), dict) else {}
    for concern in image_info.get("visible_concerns") or []:
        text = str(concern or "").strip()
        if text:
            names.append(text)
    for hint in planner_project_hints(state):
        text = str(hint or "").strip()
        if text:
            names.append(text)
    return dedupe_strings(names)[:8]
