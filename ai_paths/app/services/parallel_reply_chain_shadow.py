from __future__ import annotations

from typing import Any


def parallel_reply_chain_shadow(
    *,
    reply_chain_shadow_context: dict[str, Any],
    gate_router_shadow: dict[str, Any],
    tool_plan_preview: dict[str, Any],
    read_only_tool_executor_shadow: dict[str, Any],
    reply_chain_join_shadow: dict[str, Any],
) -> dict[str, Any]:
    """Describe the target parallel reply chain without changing runtime behavior."""

    activation_blockers = _activation_blockers(
        reply_chain_shadow_context=reply_chain_shadow_context,
        gate_router_shadow=gate_router_shadow,
        tool_plan_preview=tool_plan_preview,
        read_only_tool_executor_shadow=read_only_tool_executor_shadow,
        reply_chain_join_shadow=reply_chain_join_shadow,
    )
    return _drop_empty(
        {
            "schema_version": "parallel_reply_chain_shadow_v1",
            "mode": "shadow_no_parallel_execution",
            "target_topology": {
                "start": "single_normalized_input_and_authoritative_evidence",
                "parallel_branches": ["sop_chat_gate", "tool_planner"],
                "after_tool_planner": "read_only_tool_executor",
                "join": "deterministic_join",
                "final_expression_owner": "reply",
                "write_phase": "deferred_commit_after_validation",
            },
            "ownership_contract": {
                "sop_chat_gate": {
                    "owns": ["content_match", "first_layer_route", "direct_reply_candidate"],
                    "must_not_own": ["tool_arguments", "final_closing_move", "complex_customer_state", "writes"],
                },
                "tool_planner": {
                    "owns": ["minimal_read_tool_plan", "required_fact_fields", "incomplete_fact_question_goal"],
                    "must_not_own": ["customer_visible_text", "sop_selection", "sales_psychology", "closing_move"],
                },
                "reply": {
                    "owns": ["final_customer_visible_messages", "complex_turn_outcome", "single_mainline_action"],
                    "must_use": ["complete_timed_chat", "authoritative_facts", "gate_content_candidates", "tool_facts"],
                },
                "code": {
                    "owns": ["schema", "tool_execution", "idempotency", "hard_safety", "non_business_fallback"],
                    "must_not_own": ["normal_sales_intent", "objection_psychology", "sales_rhythm"],
                },
            },
            "current_serial_observation": {
                "shared_context_schema": reply_chain_shadow_context.get("schema_version"),
                "gate_route": gate_router_shadow.get("route_suggestion"),
                "planner_fact_requirement": tool_plan_preview.get("fact_requirement"),
                "read_executor_mode": read_only_tool_executor_shadow.get("mode"),
                "join_final_route": reply_chain_join_shadow.get("final_route"),
                "direct_reply_allowed": reply_chain_join_shadow.get("direct_reply_allowed"),
            },
            "safety": {
                "no_runtime_behavior_change": True,
                "no_model_payload_consumption": True,
                "no_external_tool_calls": True,
                "no_customer_messages_sent": True,
                "no_database_writes": True,
            },
            "activation": {
                "ready_for_shadow_parallel_runner": not activation_blockers,
                "blockers": activation_blockers,
            },
            "source": "shadow_contract_only",
        }
    )


def _activation_blockers(
    *,
    reply_chain_shadow_context: dict[str, Any],
    gate_router_shadow: dict[str, Any],
    tool_plan_preview: dict[str, Any],
    read_only_tool_executor_shadow: dict[str, Any],
    reply_chain_join_shadow: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if reply_chain_shadow_context.get("schema_version") != "reply_chain_shadow_v1":
        blockers.append("missing_shared_reply_chain_shadow_context")
    if gate_router_shadow.get("schema_version") != "chat_gate_router_shadow_v1":
        blockers.append("missing_gate_router_shadow")
    if tool_plan_preview.get("schema_version") != "tool_plan_preview_v2":
        blockers.append("missing_tool_plan_preview")
    if read_only_tool_executor_shadow.get("schema_version") != "read_only_tool_executor_shadow_v1":
        blockers.append("missing_read_only_tool_executor_shadow")
    if reply_chain_join_shadow.get("schema_version") != "reply_chain_join_shadow_v1":
        blockers.append("missing_reply_chain_join_shadow")
    if read_only_tool_executor_shadow.get("blocked"):
        blockers.append("early_tool_executor_has_blocked_calls")
    return blockers


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
