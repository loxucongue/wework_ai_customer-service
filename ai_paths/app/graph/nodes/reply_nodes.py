from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.common import model_usage_snapshot
from app.graph.planner.runtime_plan import planner_handoff
from app.graph.state import AgentState
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


def create_synthesize_reply_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    model_reply_unsafe: Callable[[AgentState, list[dict[str, Any]]], bool],
    postprocess_reply_messages: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]],
    reply_messages_for_model: Callable[[AgentState], list[dict[str, Any]]],
    reply_model_tier: Callable[[AgentState], str],
    reply_repair_messages_for_model: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]],
    should_use_model_reply: Callable[[AgentState], bool],
    validated_model_messages: Callable[[dict[str, Any]], list[dict[str, Any]]],
):
    async def synthesize_reply(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "synthesize_reply",
            {"fact_envelope": state.get("fact_envelope"), "required_tools": state.get("required_tools")},
        ) as span:
            model_call: dict[str, Any] | None = None
            errors = list(state.get("errors", []))
            messages: list[dict[str, Any]] = []
            reply_source = "main_model"

            try:
                if not (model_client and model_client.available and should_use_model_reply(state)):
                    raise RuntimeError("reply_synthesizer_model_required")

                tier = reply_model_tier(state)
                model_call = {"name": "reply_synthesizer_model", "input": {"tier": tier, "required": True}}

                payload = await model_client.chat_json(reply_messages_for_model(state), tier=tier)
                model_call["usage"] = model_usage_snapshot(model_client)
                messages = validated_model_messages(payload)
                model_call["draft_messages"] = debug_message_contents(messages)
                if messages:
                    messages = postprocess_reply_messages(state, messages)
                    model_call["postprocessed_messages"] = debug_message_contents(messages)

                if not messages or model_reply_unsafe(state, messages):
                    messages, repair_call, repaired = await _try_repair_reply(
                        state=state,
                        draft_messages=messages,
                        tier=tier,
                        model_client=model_client,
                        model_reply_unsafe=model_reply_unsafe,
                        postprocess_reply_messages=postprocess_reply_messages,
                        reply_repair_messages_for_model=reply_repair_messages_for_model,
                        validated_model_messages=validated_model_messages,
                        debug_message_contents=debug_message_contents,
                        reason="initial_quality_gate",
                    )
                    model_call.setdefault("nested_calls", []).append(repair_call)
                    if repaired:
                        model_call["fallback"] = "repaired_model_reply"
                        reply_source = "repair_model"
                    else:
                        model_call["fallback"] = "handoff_after_repair_failure"

                model_call["output"] = {"messages": len(messages)}
            except Exception as exc:
                model_call = model_call or {"name": "reply_synthesizer_model", "input": {}}
                primary_error = f"{type(exc).__name__}: {exc}"
                model_call["error"] = primary_error
                errors.append(
                    {
                        "node": "synthesize_reply",
                        "message": "final_reply_model_failed",
                        "detail": primary_error,
                    }
                )
                messages = []

            if messages and model_reply_unsafe(state, messages):
                repaired = False
                if model_call and model_client and model_client.available:
                    tier = str((model_call.get("input") or {}).get("tier") or reply_model_tier(state))
                    messages, repair_call, repaired = await _try_repair_reply(
                        state=state,
                        draft_messages=messages,
                        tier=tier,
                        model_client=model_client,
                        model_reply_unsafe=model_reply_unsafe,
                        postprocess_reply_messages=postprocess_reply_messages,
                        reply_repair_messages_for_model=reply_repair_messages_for_model,
                        validated_model_messages=validated_model_messages,
                        debug_message_contents=debug_message_contents,
                        reason="final_quality_gate",
                    )
                    model_call.setdefault("nested_calls", []).append(repair_call)
                    if repaired:
                        reply_source = "repair_model"
                        model_call["fallback"] = "repaired_after_final_quality_gate"
                if not repaired:
                    messages = []
                    errors.append({"node": "synthesize_reply", "message": "final_reply_failed_quality_gate"})

            if not messages:
                errors.append({"node": "synthesize_reply", "message": "customer_visible_reply_unavailable"})
                messages = _metadata_only_handoff_messages(state)
                reply_source = "metadata_only_handoff"

            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            output = {
                "reply_messages": messages,
                "reply_source": reply_source,
                "postprocess_changed": bool(state.get("postprocess_changed")),
                "postprocess_reasons": state.get("postprocess_reasons", []),
                "errors": errors,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return synthesize_reply


async def _try_repair_reply(
    *,
    state: AgentState,
    draft_messages: list[dict[str, Any]],
    tier: str,
    model_client: ModelClient,
    model_reply_unsafe: Callable[[AgentState, list[dict[str, Any]]], bool],
    postprocess_reply_messages: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]],
    reply_repair_messages_for_model: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]],
    validated_model_messages: Callable[[dict[str, Any]], list[dict[str, Any]]],
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    reason: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    repair_call: dict[str, Any] = {"name": "reply_repair_model", "input": {"tier": tier, "required": True, "reason": reason}}
    try:
        repair_payload = await model_client.chat_json(
            reply_repair_messages_for_model(state, draft_messages),
            tier=tier,
        )
        repair_call["usage"] = model_usage_snapshot(model_client)
        repaired_messages = validated_model_messages(repair_payload)
        repair_call["draft_messages"] = debug_message_contents(repaired_messages)
        if repaired_messages:
            repaired_messages = postprocess_reply_messages(state, repaired_messages)
            repair_call["postprocessed_messages"] = debug_message_contents(repaired_messages)
        if repaired_messages and not model_reply_unsafe(state, repaired_messages):
            return repaired_messages, repair_call, True
        repair_call["error"] = "repaired_reply_still_unsafe"
    except Exception as repair_exc:
        repair_call["error"] = f"{type(repair_exc).__name__}: {repair_exc}"
    return [], repair_call, False


def _metadata_only_handoff_messages(state: AgentState) -> list[dict[str, Any]]:
    handoff = planner_handoff(state)
    reason = str(handoff.get("reason") or "").strip() or "当前问题需要进一步核对"
    return [{"type": "human_handoff", "order": 1, "content": {"handoff_reason": reason}}]
