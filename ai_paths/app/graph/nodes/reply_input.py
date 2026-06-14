from __future__ import annotations

from typing import Any

from app.graph.planner.runtime_plan import (
    planner_handoff,
    planner_primary_task,
    planner_required_tools,
    planner_secondary_tasks,
    planner_task_views,
)
from app.graph.state import AgentState
from app.graph.nodes.common import json_dumps
from app.prompts.reply_synthesizer import build_repair_messages, build_reply_messages, build_text_rescue_messages


def should_use_model_reply(state: AgentState) -> bool:
    if planner_primary_task(state):
        return True
    if planner_required_tools(state):
        return True
    if planner_handoff(state).get("needed"):
        return True
    return bool(planner_task_views(state))


def reply_model_tier(state: AgentState) -> str:
    del state
    return "fast"


def reply_messages_for_model(state: AgentState, reply_user_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return build_reply_messages(reply_user_payload, json_dumps=json_dumps)


def reply_repair_messages_for_model(
    state: AgentState,
    draft_messages: list[dict[str, Any]],
    reply_user_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    del state
    return build_repair_messages(reply_user_payload, draft_messages, json_dumps=json_dumps)


def reply_text_rescue_messages_for_model(state: AgentState, reply_user_payload: dict[str, Any]) -> list[dict[str, Any]]:
    del state
    return build_text_rescue_messages(reply_user_payload, json_dumps=json_dumps)
