from __future__ import annotations

from typing import Any

from app.graph.nodes.legacy_reply_callback_registry import (
    _forced_reply_satisfies_hard_instruction as _forced_reply_satisfies_hard_instruction_from_registry,
    _model_reply_unsafe as _model_reply_unsafe_from_registry,
    _postprocess_reply_messages as _postprocess_reply_messages_from_registry,
    _reply_brief_for_model as _reply_brief_for_model_from_registry,
)
from app.graph.state import AgentState


def _postprocess_reply_messages(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _postprocess_reply_messages_from_registry(state, messages)


def _reply_brief_for_model(state: AgentState) -> dict[str, Any]:
    return _reply_brief_for_model_from_registry(state)


def _model_reply_unsafe(state: AgentState, messages: list[dict[str, Any]]) -> bool:
    return _model_reply_unsafe_from_registry(state, messages)


def _forced_reply_satisfies_hard_instruction(messages: list[dict[str, Any]], payload: dict[str, Any]) -> bool:
    return _forced_reply_satisfies_hard_instruction_from_registry(messages, payload)
