from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.nodes.common import model_usage_snapshot
from app.graph.state import AgentState
from app.prompts.appointment_reply_synthesizer import build_appointment_reply_messages
from app.prompts.reply_synthesizer import build_forced_reply_messages
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


@dataclass(frozen=True)
class ReplyCallbacks:
    appointment_reply_payload_for_model: Callable[[AgentState], dict[str, Any]]
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]]
    forced_reply_satisfies_hard_instruction: Callable[[list[dict[str, Any]], dict[str, Any]], bool]
    json_dumps: Callable[[Any], str]
    model_reply_unsafe: Callable[[AgentState, list[dict[str, Any]]], bool]
    postprocess_reply_messages: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]]
    reply_forced_payload_for_model: Callable[[AgentState], dict[str, Any]]
    reply_messages_for_model: Callable[[AgentState], list[dict[str, Any]]]
    reply_model_tier: Callable[[AgentState], str]
    reply_repair_messages_for_model: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]]
    should_use_appointment_fact_reply: Callable[[AgentState], bool]
    should_use_model_reply: Callable[[AgentState], bool]
    validated_model_messages: Callable[[dict[str, Any]], list[dict[str, Any]]]


def create_synthesize_reply_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    callbacks: ReplyCallbacks,
):
    async def synthesize_reply(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "synthesize_reply",
            {"action_plan": state.get("action_plan"), "module_outputs": state.get("module_outputs")},
        ) as span:
            model_call: dict[str, Any] | None = None
            errors = list(state.get("errors", []))
            messages: list[dict[str, Any]] = []

            async def try_forced_reply(tier: str, reason: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
                forced_user_payload = callbacks.reply_forced_payload_for_model(state)
                forced_facts = forced_user_payload.get("fact_brief", {}).get("available_facts", {})
                forced_call: dict[str, Any] = {
                    "name": "reply_forced_fact_model",
                    "input": {
                        "tier": tier,
                        "required": True,
                        "reason": reason,
                        "code_revision": "forced_hard_v2",
                        "hard_instruction": forced_user_payload.get("hard_instruction", ""),
                        "preferred_time_available": forced_facts.get("preferred_time_available")
                        if isinstance(forced_facts, dict)
                        else None,
                    },
                }
                try:
                    if not model_client:
                        raise RuntimeError("model_client_unavailable")
                    forced_payload = await model_client.chat_json(
                        build_forced_reply_messages(forced_user_payload, json_dumps=callbacks.json_dumps),
                        tier=tier,
                    )
                    forced_call["usage"] = model_usage_snapshot(model_client)
                    forced_messages = callbacks.validated_model_messages(forced_payload)
                    forced_call["draft_messages"] = callbacks.debug_message_contents(forced_messages)
                    forced_call["draft_messages_full"] = callbacks.debug_message_contents(forced_messages)
                    forced_unsafe = callbacks.model_reply_unsafe(state, forced_messages) if forced_messages else True
                    forced_call["unsafe"] = forced_unsafe
                    if forced_messages and (
                        not forced_unsafe
                        or callbacks.forced_reply_satisfies_hard_instruction(forced_messages, forced_user_payload)
                    ):
                        forced_call["output"] = {"messages": len(forced_messages)}
                        return forced_messages, forced_call
                    forced_call["error"] = "forced_reply_still_unsafe_or_empty"
                except Exception as forced_exc:
                    forced_call["error"] = f"{type(forced_exc).__name__}: {forced_exc}"
                return [], forced_call

            async def try_appointment_fact_reply(tier: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
                appointment_payload = callbacks.appointment_reply_payload_for_model(state)
                appointment_call: dict[str, Any] = {
                    "name": "appointment_fact_reply_model",
                    "input": {
                        "tier": tier,
                        "required": True,
                        "preferred_time": appointment_payload.get("preferred_time"),
                        "preferred_time_available": appointment_payload.get("preferred_time_available"),
                        "direct_arrival_question": appointment_payload.get("direct_arrival_question"),
                    },
                }
                try:
                    if not model_client:
                        raise RuntimeError("model_client_unavailable")
                    appointment_model_payload = await model_client.chat_json(
                        build_appointment_reply_messages(appointment_payload, json_dumps=callbacks.json_dumps),
                        tier=tier,
                    )
                    appointment_call["usage"] = model_usage_snapshot(model_client)
                    appointment_messages = callbacks.validated_model_messages(appointment_model_payload)
                    appointment_call["draft_messages"] = callbacks.debug_message_contents(appointment_messages)
                    appointment_unsafe = callbacks.model_reply_unsafe(state, appointment_messages) if appointment_messages else True
                    appointment_call["unsafe"] = appointment_unsafe
                    if appointment_messages and not appointment_unsafe:
                        appointment_call["output"] = {"messages": len(appointment_messages)}
                        return appointment_messages, appointment_call
                    appointment_call["error"] = "appointment_reply_unsafe_or_empty"
                except Exception as appointment_exc:
                    appointment_call["error"] = f"{type(appointment_exc).__name__}: {appointment_exc}"
                return [], appointment_call

            try:
                if not (model_client and model_client.available and callbacks.should_use_model_reply(state)):
                    raise RuntimeError("reply_synthesizer_model_required")
                tier = callbacks.reply_model_tier(state)
                model_call = {"name": "reply_synthesizer_model", "input": {"tier": tier, "required": True}}
                if callbacks.should_use_appointment_fact_reply(state):
                    messages, appointment_call = await try_appointment_fact_reply(tier)
                    model_call.setdefault("nested_calls", []).append(appointment_call)
                    if messages:
                        model_call["fallback"] = "appointment_fact_model_reply"
                if not messages:
                    payload = await model_client.chat_json(callbacks.reply_messages_for_model(state), tier=tier)
                    model_call["usage"] = model_usage_snapshot(model_client)
                    messages = callbacks.validated_model_messages(payload)
                    model_call["draft_messages"] = callbacks.debug_message_contents(messages)
                if not messages or callbacks.model_reply_unsafe(state, messages):
                    repair_call: dict[str, Any] = {"name": "reply_repair_model", "input": {"tier": tier, "required": True}}
                    try:
                        repair_payload = await model_client.chat_json(callbacks.reply_repair_messages_for_model(state, messages), tier=tier)
                        repair_call["usage"] = model_usage_snapshot(model_client)
                        repaired_messages = callbacks.validated_model_messages(repair_payload)
                        repair_call["draft_messages"] = callbacks.debug_message_contents(repaired_messages)
                        if repaired_messages and not callbacks.model_reply_unsafe(state, repaired_messages):
                            messages = repaired_messages
                            model_call["fallback"] = "repaired_model_reply"
                        else:
                            messages = []
                            repair_call["error"] = "repaired_reply_still_unsafe"
                    except Exception as repair_exc:
                        messages = []
                        repair_call["error"] = f"{type(repair_exc).__name__}: {repair_exc}"
                    model_call.setdefault("nested_calls", []).append(repair_call)
                    if not messages:
                        forced_messages, forced_call = await try_forced_reply(tier, "repair_failed_or_unsafe")
                        model_call.setdefault("nested_calls", []).append(forced_call)
                        if not forced_messages and tier != "balanced":
                            forced_messages, forced_call = await try_forced_reply("balanced", "strong_forced_reply_failed")
                            model_call.setdefault("nested_calls", []).append(forced_call)
                        if not forced_messages and tier != "fast":
                            forced_messages, forced_call = await try_forced_reply("fast", "balanced_forced_reply_failed")
                            model_call.setdefault("nested_calls", []).append(forced_call)
                        if forced_messages:
                            messages = forced_messages
                            model_call["fallback"] = "forced_fact_model_reply"
                        else:
                            model_call["fallback"] = "blocked_without_template_fallback"
                model_call["output"] = {"messages": len(messages)}
            except Exception as exc:
                model_call = model_call or {"name": "reply_synthesizer_model", "input": {}}
                primary_error = f"{type(exc).__name__}: {exc}"
                model_call["primary_error"] = primary_error
                forced_messages, forced_call = await try_forced_reply("balanced", "primary_reply_model_exception")
                model_call.setdefault("nested_calls", []).append(forced_call)
                if not forced_messages:
                    forced_messages, forced_call = await try_forced_reply("fast", "balanced_forced_after_exception_failed")
                    model_call.setdefault("nested_calls", []).append(forced_call)
                if forced_messages:
                    messages = forced_messages
                    model_call["fallback"] = "forced_fact_model_after_exception"
                    model_call["output"] = {"messages": len(messages)}
                else:
                    model_call["error"] = primary_error
                    errors.append(
                        {"node": "synthesize_reply", "message": "final_reply_model_failed", "detail": primary_error}
                    )
                    messages = []
            if messages:
                messages = callbacks.postprocess_reply_messages(state, messages)
            if messages and callbacks.model_reply_unsafe(state, messages):
                messages = []
                errors.append({"node": "synthesize_reply", "message": "final_reply_failed_quality_gate"})
            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            output = {"reply_messages": messages, "errors": errors, "trace": state.get("trace", [])}
            span["output_snapshot"] = output
            return output

    return synthesize_reply
