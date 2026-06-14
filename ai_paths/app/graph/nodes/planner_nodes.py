from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.common import model_usage_snapshot
from app.graph.planner.brain_v2 import planner_unavailable_fallback_plan, run_planner_brain_v2, safety_fallback_plan
from app.graph.state import AgentState
from app.policies.sop_rules import compact_sop_stage_rules_for_reply, normalize_sop_stage, normalize_sop_step
from app.policies.rule_catalog import policy_selection_from_task
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
                    plan = planner_unavailable_fallback_plan(state)
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
                plan = planner_unavailable_fallback_plan(state)
                planner_call = planner_call or {"name": "planner_brain_v2", "input": {}}
                planner_call["error"] = f"{type(exc).__name__}: {exc}"
                if model_client and model_client.available:
                    planner_call["usage"] = model_usage_snapshot(model_client)

            if planner_call:
                span["entry"]["tool_calls"] = [planner_call]

            policy = policy_selection_from_task(plan.get("primary_task", {}), plan.get("handoff", {}))
            primary_task = plan.get("primary_task", {})
            sop_stage = normalize_sop_stage(
                plan.get("sop_stage") or primary_task.get("sop_stage"),
                task_type=str(primary_task.get("type") or ""),
                request_stage=str((state.get("request_context") or {}).get("customer_stage") or "")
                if isinstance(state.get("request_context"), dict)
                else "",
            )
            sop_step = normalize_sop_step(sop_stage, plan.get("sop_step") or primary_task.get("sop_step"))
            output = {
                "primary_task": primary_task,
                "secondary_tasks": plan.get("secondary_tasks", []),
                "required_tools": plan.get("required_tools", []),
                "tool_policy_violations": plan.get("tool_policy_violations", []),
                "reply_strategy": plan.get("reply_strategy", {}),
                "handoff": plan.get("handoff", {}),
                "memory_update_hint": plan.get("memory_update_hint", {}),
                "sop_stage": sop_stage,
                "sop_step": sop_step,
                "sop_stage_rules": compact_sop_stage_rules_for_reply(sop_stage, sop_step),
                "policy_id": policy["policy_id"],
                "policy_family_id": policy["policy_family_id"],
                "exact_policy_id": policy["exact_policy_id"],
                "policy_match_level": policy["policy_match_level"],
                "policy_version": policy["policy_version"],
                "planner_source": (
                    "guardrail"
                    if (state.get("guardrail_result") or {}).get("blocked")
                    else ("llm" if model_client and model_client.available else "fallback")
                ),
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return planner_brain
