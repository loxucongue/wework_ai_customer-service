from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.common import model_usage_snapshot
from app.graph.planner.runtime_plan import planner_handoff
from app.graph.state import AgentState
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


FINAL_REPLY_MODEL_NAMES = [
    "deepseek-v4-flash",
]
FINAL_REPLY_JSON_FORMAT = {"type": "json_object"}
FINAL_REPLY_TEMPERATURE = 0.25


def create_synthesize_reply_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    model_reply_unsafe: Callable[[AgentState, list[dict[str, Any]]], bool],
    postprocess_reply_messages: Callable[[AgentState, list[dict[str, Any]]], list[dict[str, Any]]],
    reply_messages_for_model: Callable[[AgentState], list[dict[str, Any]]],
    reply_model_tier: Callable[[AgentState], str],
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
            pending_model_errors: list[dict[str, Any]] = []

            try:
                if not (model_client and model_client.available and should_use_model_reply(state)):
                    raise RuntimeError("reply_synthesizer_model_required")

                tier = reply_model_tier(state)
                model_call = {"name": "reply_synthesizer_model", "input": {"tier": tier, "required": True}}
                attempt_errors: list[str] = []
                for attempt in (1, 2):
                    try:
                        payload = await model_client.chat_json(
                            reply_messages_for_model(state),
                            tier=tier,
                            temperature=FINAL_REPLY_TEMPERATURE,
                            model_names=FINAL_REPLY_MODEL_NAMES,
                            response_format=FINAL_REPLY_JSON_FORMAT,
                        )
                        model_call["usage"] = model_usage_snapshot(model_client)
                        messages = validated_model_messages(payload)
                        model_call["draft_messages"] = debug_message_contents(messages)
                        if messages:
                            messages = postprocess_reply_messages(state, messages)
                            model_call["postprocessed_messages"] = debug_message_contents(messages)
                        if not _has_customer_visible_text(messages):
                            raise RuntimeError("reply_messages_empty_after_postprocess")
                        model_call["output"] = {"messages": len(messages), "attempt": attempt}
                        model_call["attempt_errors"] = list(attempt_errors)
                        break
                    except Exception as exc:
                        attempt_errors.append(f"attempt={attempt}: {type(exc).__name__}: {exc}")
                        if attempt == 2:
                            raise RuntimeError(" | ".join(attempt_errors)) from exc
                        continue
            except Exception as exc:
                model_call = model_call or {"name": "reply_synthesizer_model", "input": {}}
                primary_error = f"{type(exc).__name__}: {exc}"
                model_call["error"] = primary_error
                pending_model_errors.append(
                    {
                        "node": "synthesize_reply",
                        "message": "final_reply_model_failed",
                        "detail": primary_error,
                    }
                )
                messages = []

            if messages and model_reply_unsafe(state, messages):
                errors.append({"node": "synthesize_reply", "message": "final_reply_failed_quality_gate"})

            if not _has_customer_visible_text(messages):
                errors.extend(pending_model_errors)
                errors.append({"node": "synthesize_reply", "message": "customer_visible_reply_unavailable"})
                messages, reply_source = _safe_visible_fallback_messages(state)

            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            recovered_errors = []
            if pending_model_errors and reply_source == "model_failed_handoff":
                errors.extend(error for error in pending_model_errors if error not in errors)
            output = {
                "reply_messages": messages,
                "reply_source": reply_source,
                "postprocess_changed": bool(state.get("postprocess_changed")),
                "postprocess_reasons": state.get("postprocess_reasons", []),
                "errors": errors,
                "recovered_errors": recovered_errors,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return synthesize_reply


def _has_customer_visible_text(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "text":
            continue
        content = message.get("content")
        if isinstance(content, dict):
            text = str(content.get("text") or "").strip()
        else:
            text = str(content or "").strip()
        if text:
            return True
    return False


def _safe_visible_fallback_messages(state: AgentState) -> tuple[list[dict[str, Any]], str]:
    handoff = planner_handoff(state)
    reason = str(handoff.get("reason") or "").strip() or "最终回复生成失败，转专业同事继续跟进"
    text = "这边帮您对接同事继续跟进，请您稍等一下。"
    return (
        [
            {"type": "text", "order": 1, "content": {"text": text}},
            {"type": "human_handoff", "order": 2, "content": {"handoff_reason": reason}},
        ],
        "model_failed_handoff",
    )
