from __future__ import annotations

from typing import Any

from app.graph.planner.runtime_plan import (
    planner_handoff,
    planner_required_tools,
)
from app.graph.state import AgentState
from app.graph.nodes.common import json_dumps
from app.prompts.reply_synthesizer import build_reply_messages


def should_use_model_reply(state: AgentState) -> bool:
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


def reply_messages_for_model(state: AgentState, reply_user_payload: dict[str, Any]) -> list[dict[str, Any]]:
    del state
    return build_reply_messages(reply_user_payload, json_dumps=json_dumps)
