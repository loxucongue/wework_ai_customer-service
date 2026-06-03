from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.state import AgentState
from app.prompts.reply_synthesizer import build_repair_messages, build_reply_messages


@dataclass(frozen=True)
class ReplyInputCallbacks:
    json_dumps: Callable[[Any], str]
    reply_user_payload: Callable[[AgentState], dict[str, Any]]


def should_use_model_reply(state: AgentState) -> bool:
    intents = state.get("intents", [])
    return bool(intents)


def reply_model_tier(state: AgentState) -> str:
    intents = {item.get("intent") for item in state.get("intents", [])}
    if "human_request" in intents or "complaint_refund" in intents or "after_sales" in intents or "competitor_compare" in intents:
        return "strong"
    if intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        return "strong"
    if len(intents) >= 2 or "trust_issue" in intents:
        return "balanced"
    return "fast"


def reply_messages_for_model(state: AgentState, callbacks: ReplyInputCallbacks) -> list[dict[str, Any]]:
    return build_reply_messages(callbacks.reply_user_payload(state), json_dumps=callbacks.json_dumps)


def reply_repair_messages_for_model(
    state: AgentState,
    draft_messages: list[dict[str, Any]],
    callbacks: ReplyInputCallbacks,
) -> list[dict[str, Any]]:
    return build_repair_messages(callbacks.reply_user_payload(state), draft_messages, json_dumps=callbacks.json_dumps)
