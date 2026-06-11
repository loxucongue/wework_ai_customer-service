from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.planner.runtime_plan import planner_handoff, planner_required_tools, planner_tasks
from app.graph.state import AgentState
from app.services.trace_logger import TraceLogger


def create_professional_assist_node(*, trace_logger: TraceLogger) -> Callable[[AgentState], Any]:
    async def professional_assist(state: AgentState) -> dict[str, Any]:
        required_tools = planner_required_tools(state)
        handoff = planner_handoff(state)
        tasks = planner_tasks(state)
        needs_assist = _needs_professional_assist(required_tools) or bool(handoff.get("needed"))
        with trace_logger.node(
            state,
            "professional_assist",
            {
                "planned": _needs_professional_assist(required_tools),
                "handoff_needed": bool(handoff.get("needed")),
            },
        ) as span:
            if not needs_assist:
                output = {"trace": state.get("trace", [])}
                span["output_snapshot"] = output
                return output

            content = str(state.get("normalized_content") or "")
            tool_results = dict(state.get("tool_results") or {})
            assist_result = build_professional_assist_result(
                state=state,
                content=content,
                tasks=tasks,
                handoff=handoff,
                required_tools=required_tools,
            )
            tool_results["professional_assist"] = assist_result
            tool_call = {
                "name": "professional_assist",
                "input": {
                    "planned": _needs_professional_assist(required_tools),
                    "handoff_needed": bool(handoff.get("needed")),
                },
                "output": assist_result,
            }
            planner_fact_output = build_planner_fact_output(tool_results, state)
            output = {
                "tool_results": tool_results,
                "fact_envelope": dict(planner_fact_output.get("fact_envelope") or {}),
                "trace": state.get("trace", []),
            }
            span["entry"]["tool_calls"] = [tool_call]
            span["output_snapshot"] = output
            return output

    return professional_assist


def build_professional_assist_result(
    *,
    state: AgentState,
    content: str,
    tasks: list[dict[str, Any]],
    handoff: dict[str, Any],
    required_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    primary = tasks[0] if tasks and isinstance(tasks[0], dict) else {}
    guardrail = state.get("guardrail_result") if isinstance(state.get("guardrail_result"), dict) else {}
    planned_reasons = [
        str(tool.get("purpose") or "").strip()
        for tool in required_tools
        if isinstance(tool, dict) and str(tool.get("name") or "") == "professional_assist"
    ]
    planned_reasons = [reason for reason in planned_reasons if reason]
    return {
        "status": "requested",
        "reason": str(handoff.get("reason") or primary.get("answer_goal") or primary.get("customer_need") or "").strip(),
        "task_type": str(primary.get("type") or "").strip(),
        "subtype": str(primary.get("subtype") or "").strip(),
        "policy_hint": str(primary.get("policy_hint") or "").strip(),
        "customer_message": content[:240],
        "guardrail_terms": [str(item) for item in (guardrail.get("terms") or [])[:8]],
        "planned_reasons": planned_reasons[:3],
        "required_internal_action": "professional_colleague_review",
    }


def _needs_professional_assist(required_tools: list[dict[str, Any]]) -> bool:
    return any(str(item.get("name") or "") == "professional_assist" for item in required_tools if isinstance(item, dict))
