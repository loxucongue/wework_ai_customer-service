from __future__ import annotations

from app.graph.planner.runtime_plan import (
    planner_handoff,
    planner_required_tools,
)
from app.graph.state import AgentState


def should_use_model_reply(state: AgentState) -> bool:
    if state.get("evidence_join"):
        return True
    decision = str(state.get("planner_decision") or "").strip()
    if decision == "need_tools":
        return True
    if decision == "direct_reply":
        return True
    if planner_required_tools(state):
        return True
    if planner_handoff(state).get("needed"):
        return True
    return False
