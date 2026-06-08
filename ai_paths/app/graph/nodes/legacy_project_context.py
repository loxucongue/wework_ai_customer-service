from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.state import AgentState


@dataclass(frozen=True)
class LegacyProjectContextCallbacks:
    business_project_slices: Callable[[list[dict[str, Any]], AgentState | None], list[dict[str, Any]]]
    canonical_price_project: Callable[[str], str]
    dedupe_strings: Callable[[list[str]], list[str]]
    extract_project: Callable[[str], str]
    has_image_concern: Callable[[dict[str, Any], list[str]], bool]
    project_direction_name_candidates: Callable[[str], list[str]]
    project_slices_from_tool_results: Callable[[dict[str, Any]], list[dict[str, Any]]]
    recent_conversation_text: Callable[[AgentState], str]


def current_turn_explicit_project(state: AgentState, callbacks: LegacyProjectContextCallbacks) -> str:
    content = state.get("normalized_content") or ""
    project = callbacks.extract_project(content)
    if project:
        return callbacks.canonical_price_project(project)
    return ""


def recent_project_from_state(state: AgentState, callbacks: LegacyProjectContextCallbacks) -> str:
    content = state.get("normalized_content") or ""
    project = callbacks.extract_project(content)
    if project:
        return project
    for message in reversed(state.get("conversation_history", [])[-10:]):
        project = callbacks.extract_project(str(message))
        if project:
            return project
    profile = state.get("customer_profile") or {}
    if isinstance(profile, dict):
        for item in profile.get("projects", []) or []:
            project = callbacks.extract_project(str(item))
            if project:
                return project
    return ""


def contextual_price_project(state: AgentState, callbacks: LegacyProjectContextCallbacks) -> str:
    content = state.get("normalized_content") or ""
    project = recent_project_from_state(state, callbacks)
    if project:
        return callbacks.canonical_price_project(project)
    if any(term in content for term in ["脸上的斑", "脸上有斑", "斑点", "色沉", "肤色不均", "淡斑", "祛斑"]):
        return "淡斑"
    if any(term in content for term in ["痘印", "痘坑"]):
        return "痘印"
    if "毛孔" in content:
        return "毛孔"
    image_info = state.get("image_info") or {}
    if callbacks.has_image_concern(image_info, ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]):
        return "淡斑"
    if callbacks.has_image_concern(image_info, ["痘印", "痘坑"]):
        return "痘印"
    if callbacks.has_image_concern(image_info, ["毛孔"]):
        return "毛孔"
    history = callbacks.recent_conversation_text(state)
    if any(term in history for term in ["点状斑", "小斑点", "色沉", "淡斑", "祛斑"]):
        return "淡斑"
    return ""


def project_direction_names_from_state(state: AgentState, callbacks: LegacyProjectContextCallbacks) -> list[str]:
    slices = callbacks.business_project_slices(
        callbacks.project_slices_from_tool_results(state.get("tool_results", {}) or {}),
        state,
    )
    names: list[str] = []
    for item in slices:
        names.extend(callbacks.project_direction_name_candidates(str(item.get("replacement_name") or "")))
    return callbacks.dedupe_strings(names)
