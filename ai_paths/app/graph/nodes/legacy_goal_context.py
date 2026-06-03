from __future__ import annotations

from typing import Any, Callable

from app.graph.state import AgentState


def has_confirmed_spot_goal(state: AgentState, json_dumps: Callable[[Any], str]) -> bool:
    content = str(state.get("normalized_content") or "")
    if any(term in content for term in ["就是斑", "主要斑", "祛斑", "淡斑", "斑呀", "斑啊", "斑点"]):
        return True
    profile = state.get("customer_profile") or {}
    if isinstance(profile, dict):
        joined = json_dumps(
            {
                "needs": profile.get("needs", []),
                "pain_points": profile.get("pain_points", []),
                "summary": profile.get("summary", ""),
            }
        )
        if any(term in joined for term in ["斑", "色沉", "肤色不均"]):
            return True
    for event in state.get("history_events", [])[-8:]:
        event_text = json_dumps(event) if isinstance(event, dict) else str(event)
        if any(term in event_text for term in ["点状斑", "斑点", "色沉", "肤色不均", "淡斑", "祛斑"]):
            return True
    for message in state.get("conversation_history", [])[-8:]:
        if any(term in str(message) for term in ["点状斑", "斑点", "色沉", "肤色不均", "淡斑", "祛斑"]):
            return True
    return False


def is_redundant_known_goal_question(
    state: AgentState,
    text: str,
    *,
    has_known_image_context: Callable[[AgentState], bool],
    known_visible_concerns_from_state: Callable[[AgentState], list[str]],
    json_dumps: Callable[[Any], str],
) -> bool:
    if not text:
        return False
    if not any(term in text for term in ["最想先改善", "想先改善哪", "更想改善哪", "主要想改善哪", "告诉我最想改善"]):
        return False
    if has_confirmed_spot_goal(state, json_dumps):
        return True
    if has_known_image_context(state) and known_visible_concerns_from_state(state):
        return True
    return False
