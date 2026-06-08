from __future__ import annotations

from typing import Any, Callable

from app.graph import planner_helpers, sales_strategy, task_state
from app.graph.nodes.common import infer_scene, model_usage_snapshot, primary_goal, subflow_for_skill
from app.graph.state import AgentState
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


def create_planner_brain_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    should_suspend_active_task: Callable[[AgentState, dict[str, Any], list[dict[str, Any]]], bool],
    without_appointment_intents: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> Callable[[AgentState], Any]:
    async def planner_brain(state: AgentState) -> dict[str, Any]:
        content = state.get("normalized_content") or ""
        with trace_logger.node(
            state,
            "planner_brain",
            {"content": content, "image_info": state.get("image_info"), "guardrail_result": state.get("guardrail_result")},
        ) as span:
            planner_call: dict[str, Any] | None = None
            if state.get("guardrail_result", {}).get("blocked"):
                terms = state.get("guardrail_result", {}).get("terms", [])
                intent = "complaint_refund" if any(term in terms for term in ["投诉", "退款", "维权", "曝光", "骗子", "骗钱", "骗我", "效果纠纷"]) else "human_request"
                intents = [{"intent": intent, "skill": "handoff", "priority": 0, "reason": "命中投诉、纠纷或风险关键词"}]
            else:
                try:
                    rule_intents = planner_helpers.detect_intents(content, state.get("image_info", {}))
                    pre_active_task = task_state.build_active_task(state, rule_intents)
                    planner_state = dict(state)
                    if pre_active_task and not should_suspend_active_task(state, pre_active_task, rule_intents):
                        planner_state["active_task"] = pre_active_task
                    if model_client and model_client.available and planner_helpers.should_use_model_planner(state):
                        tier = planner_helpers.planner_model_tier(state)
                        planner_call = {"name": "planner_brain_model", "input": {"tier": tier}}
                        payload = await model_client.chat_json(planner_helpers.planner_messages_for_model(planner_state), tier=tier)
                        planner_call["usage"] = model_usage_snapshot(model_client)
                        intents = planner_helpers.validated_planner_intents(payload)
                        intents = planner_helpers.merge_intents(state, rule_intents, intents)
                        intents = planner_helpers.filter_spurious_intents(state, intents)
                        planner_call["output"] = {"intents": len(intents)}
                    else:
                        intents = rule_intents
                    intents = planner_helpers.filter_spurious_intents(state, intents)
                except Exception as exc:
                    intents = planner_helpers.detect_intents(content, state.get("image_info", {}))
                    intents = planner_helpers.filter_spurious_intents(state, intents)
                    planner_call = planner_call or {"name": "planner_brain_model", "input": {}}
                    planner_call["error"] = f"{type(exc).__name__}: {exc}"
            if planner_call:
                span["entry"]["tool_calls"] = [planner_call]

            active_task = task_state.build_active_task(state, intents)
            intents = task_state.apply_active_task_intent(state, intents, active_task)
            intents = planner_helpers.filter_spurious_intents(state, intents)
            active_task = task_state.build_active_task(state, intents)
            intents = planner_helpers.enrich_intents_with_tool_plan(state, planner_helpers.filter_spurious_intents(state, intents))
            intents = planner_helpers.filter_spurious_intents(state, intents)
            if should_suspend_active_task(state, active_task, intents):
                intents = without_appointment_intents(intents)
                active_task = {}
            current_sales_strategy = sales_strategy.build_sales_strategy(state, intents, active_task)

            actions = []
            for item in intents[:3]:
                actions.append(
                    {
                        "type": "skill",
                        "name": item["skill"],
                        "intent": item["intent"],
                        "reason": item["reason"],
                        "priority": item["priority"],
                        "known_info": item.get("known_info", []),
                        "missing_info": item.get("missing_info", []),
                        "reply_goal": item.get("reply_goal", ""),
                        "should_ask": item.get("should_ask", False),
                        "tool_plan": item.get("tool_plan", []),
                    }
                )

            primary = intents[0]
            route_result = {
                "scene": infer_scene(primary["intent"]),
                "intent": primary["intent"],
                "subflow": subflow_for_skill(primary["skill"]),
                "reason": f"当前消息触发{primary['reason']}，本轮采用轻量规划并最多处理三个主要意图。",
                "confidence": 0.72 if not state.get("errors") else 0.55,
                "need_human": primary["skill"] == "handoff",
            }
            action_plan = {
                "primary_goal": primary_goal(intents),
                "detected_intents": intents[:3],
                "actions": actions,
                "reply_strategy": {
                    "max_messages": 3,
                    "must_answer": [item["intent"] for item in intents[:3]],
                    "may_guide_to": "项目了解或到店面诊",
                    "must_not": ["编造价格", "承诺效果", "透露工具过程", "生硬暴露AI身份"],
                },
                "active_task": active_task,
                "sales_strategy": current_sales_strategy,
                "confidence": route_result["confidence"],
            }
            output = {
                "intents": intents[:3],
                "route_result": route_result,
                "action_plan": action_plan,
                "active_task": active_task,
                "sales_strategy": current_sales_strategy,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return planner_brain
