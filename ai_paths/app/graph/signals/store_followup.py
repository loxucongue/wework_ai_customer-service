from __future__ import annotations

from app.graph.state import AgentState


def store_location_preference_from_context(state: AgentState) -> str:
    content = str(state.get("normalized_content") or "")
    preference = store_location_preference_from_text(content)
    if preference:
        return preference

    history = "\n".join(str(item) for item in (state.get("conversation_history") or [])[-6:])
    return store_location_preference_from_text(history)


def store_location_preference_from_text(text: str) -> str:
    text = str(text or "")
    if any(term in text for term in ["机场附近", "机场周边", "离机场近", "机场边", "高崎机场", "厦门机场", "机场"]):
        return "机场附近"
    if any(term in text for term in ["火车站附近", "离火车站近", "高铁站附近", "高铁站"]):
        return "火车站附近"
    return ""
