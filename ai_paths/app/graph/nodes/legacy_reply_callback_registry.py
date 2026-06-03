from __future__ import annotations

from typing import Any

from app.graph.nodes.legacy_reply_callback_dependencies import legacy_reply_callback_factory_callbacks
from app.graph.nodes.legacy_reply_callback_factories import (
    reply_brief_callbacks as _reply_brief_callbacks_from_module,
    reply_postprocess_callbacks as _reply_postprocess_callbacks_from_module,
    reply_quality_callbacks as _reply_quality_callbacks_from_module,
)
from app.graph.nodes.reply_brief import reply_brief_for_model as _reply_brief_from_module
from app.graph.nodes.reply_postprocess import postprocess_reply_messages as _postprocess_reply_messages_from_postprocess
from app.graph.nodes.reply_quality import (
    forced_reply_safe as _forced_reply_safe_from_quality,
    model_reply_unsafe as _model_reply_unsafe_from_quality,
)
from app.graph.state import AgentState


def _reply_brief_callbacks():
    return _reply_brief_callbacks_from_module(legacy_reply_callback_factory_callbacks())


def _reply_quality_callbacks():
    return _reply_quality_callbacks_from_module(legacy_reply_callback_factory_callbacks())


def _reply_postprocess_callbacks():
    return _reply_postprocess_callbacks_from_module(legacy_reply_callback_factory_callbacks())


def _postprocess_reply_messages(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _postprocess_reply_messages_from_postprocess(state, messages, _reply_postprocess_callbacks())


def _reply_brief_for_model(state: AgentState) -> dict[str, Any]:
    return _reply_brief_from_module(state, _reply_brief_callbacks())


def _model_reply_unsafe(state: AgentState, messages: list[dict[str, Any]]) -> bool:
    return _model_reply_unsafe_from_quality(state, messages, _reply_quality_callbacks())


def _forced_reply_satisfies_hard_instruction(messages: list[dict[str, Any]], payload: dict[str, Any]) -> bool:
    return _forced_reply_safe_from_quality(messages, payload, _reply_quality_callbacks())
