from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.common import model_call_metrics, model_recovery_attempts, model_usage_snapshot
from app.graph.planner.brain_v2 import planner_unavailable_fallback_plan, run_planner_brain_v2, safety_fallback_plan
from app.graph.state import AgentState
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


def create_planner_brain_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
) -> Callable[[AgentState], Any]:
    async def planner_brain(state: AgentState) -> dict[str, Any]:
        content = state.get("normalized_content") or ""
        with trace_logger.node(
            state,
            "planner_brain",
            {"content": content, "image_info": state.get("image_info"), "guardrail_result": state.get("guardrail_result")},
        ) as span:
            planner_call: dict[str, Any] | None = None

            try:
                guardrail = state.get("guardrail_result") or {}
                if guardrail.get("blocked"):
                    plan = safety_fallback_plan(state)
                    planner_call = {
                        "name": "planner_brain_guardrail_fallback",
                        "input": {"terms": guardrail.get("terms", [])},
                        "output": {
                            "primary_task": plan.get("primary_task", {}).get("type", ""),
                            "required_tools": len(plan.get("required_tools", [])),
                            "tool_policy_violations": len(plan.get("tool_policy_violations", [])),
                        },
                    }
                elif model_client and model_client.available:
                    plan, planner_call = await run_planner_brain_v2(state, model_client)
                else:
                    plan = planner_unavailable_fallback_plan(state, reason="No model API key configured")
                    planner_call = {
                        "name": "planner_brain_model_unavailable_fallback",
                        "input": {},
                        "output": {
                            "primary_task": plan.get("primary_task", {}).get("type", ""),
                            "required_tools": len(plan.get("required_tools", [])),
                            "tool_policy_violations": len(plan.get("tool_policy_violations", [])),
                        },
                    }
            except Exception as exc:
                error_detail = f"{type(exc).__name__}: {exc}"
                plan = planner_unavailable_fallback_plan(state, reason=error_detail)
                planner_call = planner_call or {"name": "planner_brain_v2", "input": {}}
                planner_call["error"] = error_detail
                if model_client and model_client.available:
                    planner_call["usage"] = model_usage_snapshot(model_client)

            if planner_call:
                span["entry"]["tool_calls"] = [planner_call]

            context_metrics = dict(state.get("model_context_metrics") or {})
            context_metrics["planner"] = model_call_metrics(planner_call, prompt_warning_threshold=12_000)
            recovery_attempts = [
                *list(state.get("recovery_attempts") or []),
                *model_recovery_attempts(planner_call, node="planner_brain"),
            ]
            recovery_reason = str(
                (planner_call or {}).get("initial_error")
                or (planner_call or {}).get("error")
                or state.get("recovery_reason")
                or ""
            )[:500]

            output = {
                "planner_decision": plan.get("planner_decision", "need_tools"),
                "planner_stage": plan.get("planner_stage", ""),
                "planner_sub_rule_id": plan.get("planner_sub_rule_id", ""),
                "conversion_stage": plan.get("conversion_stage", ""),
                "customer_type": plan.get("customer_type", "unknown"),
                "main_blocker": plan.get("main_blocker", "none"),
                "next_step": plan.get("next_step", "no_action"),
                "payment_state": plan.get("payment_state", "unknown"),
                "payment_action": plan.get("payment_action", "unknown"),
                "payment_decision": plan.get("payment_decision", {}),
                "store_binding_decision": plan.get("store_binding_decision", {}),
                "order_decision": plan.get("order_decision", {}),
                "appointment_decision": plan.get("appointment_decision", {}),
                "sales_progression": plan.get("sales_progression", {}),
                "current_turn_resolution": plan.get("current_turn_resolution", {}),
                "reply_contract": plan.get("reply_contract", {}),
                "authorized_sop_delivery_manifest": plan.get("authorized_sop_delivery_manifest", {}),
                "closing_move": plan.get("closing_move", {}),
                "precision_qa_decision": plan.get("precision_qa_decision", {}),
                "current_known_store": plan.get("current_known_store", {}),
                "store_candidate": plan.get("store_candidate", {}),
                "planner_reply_messages": plan.get("planner_reply_messages", []),
                "planner_tool_calls": plan.get("planner_tool_calls", []),
                "reply_constraints": plan.get("reply_constraints", []),
                "primary_task": plan.get("primary_task", {}),
                "secondary_tasks": plan.get("secondary_tasks", []),
                "required_tools": plan.get("required_tools", []),
                "tool_policy_violations": plan.get("tool_policy_violations", []),
                "reply_strategy": plan.get("reply_strategy", {}),
                "handoff": plan.get("handoff", {}),
                "memory_update_hint": plan.get("memory_update_hint", {}),
                "policy_id": "",
                "policy_family_id": "",
                "exact_policy_id": "",
                "policy_match_level": "",
                "policy_version": "",
                "planner_source": (
                    "guardrail"
                    if (state.get("guardrail_result") or {}).get("blocked")
                    else (
                        "fallback"
                        if str(plan.get("planner_sub_rule_id") or "") == "PLANNER_SYSTEM_UNAVAILABLE"
                        else ("llm" if model_client and model_client.available else "fallback")
                    )
                ),
                "model_deadline": {
                    **dict(state.get("model_deadline") or {}),
                    "planner": dict((planner_call or {}).get("deadline") or {}),
                },
                "model_context_metrics": context_metrics,
                "recovery_attempts": recovery_attempts,
                "recovery_reason": recovery_reason,
                "fallback_source": str(
                    (planner_call or {}).get("fallback_source")
                    or plan.get("fallback_source")
                    or state.get("fallback_source")
                    or ""
                ),
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return planner_brain
