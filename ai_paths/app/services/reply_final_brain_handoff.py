from __future__ import annotations

from typing import Any


CUSTOMER_MESSAGE_FIELDS = ("planner_reply_messages",)
TURN_OUTCOME_FIELDS = (
    "planner_decision",
    "planner_stage",
    "planner_sub_rule_id",
    "conversion_stage",
    "customer_type",
    "main_blocker",
    "next_step",
    "primary_task",
    "secondary_tasks",
)
SALES_DECISION_FIELDS = (
    "payment_state",
    "payment_action",
    "payment_decision",
    "store_binding_decision",
    "order_decision",
    "appointment_decision",
    "sales_progression",
    "precision_qa_decision",
    "reply_strategy",
    "handoff",
    "memory_update_hint",
)
FACT_AND_TOOL_FIELDS = (
    "tool_plan_preview",
    "read_only_tool_executor_shadow",
    "reply_chain_join_shadow",
)


def reply_final_brain_handoff_shadow_from_planner_output(output: dict[str, Any]) -> dict[str, Any]:
    """Build the future Reply-final-brain handoff from current Planner output.

    This is shadow-only migration evidence. It does not approve, alter, or
    generate customer-visible replies.
    """

    customer_message_fields = _present_fields(output, CUSTOMER_MESSAGE_FIELDS)
    turn_outcome_fields = _present_fields(output, TURN_OUTCOME_FIELDS)
    sales_decision_fields = _present_fields(output, SALES_DECISION_FIELDS)
    fact_and_tool_fields = _present_fields(output, FACT_AND_TOOL_FIELDS)
    return _drop_empty(
        {
            "schema_version": "reply_final_brain_handoff_shadow_v1",
            "mode": "shadow_only_no_runtime_behavior_change",
            "purpose": "map_legacy_planner_business_outputs_to_future_reply_inputs",
            "target_owner": "reply",
            "input_groups": {
                "customer_message_candidates": _pick(output, customer_message_fields),
                "turn_outcome_signals": _pick(output, turn_outcome_fields),
                "sales_decision_signals": _pick(output, sales_decision_fields),
                "fact_and_tool_evidence": _fact_and_tool_summary(output, fact_and_tool_fields),
            },
            "migration_audit": {
                "customer_message_field_count": len(customer_message_fields),
                "turn_outcome_field_count": len(turn_outcome_fields),
                "sales_decision_field_count": len(sales_decision_fields),
                "fact_and_tool_field_count": len(fact_and_tool_fields),
                "legacy_business_field_count": len(customer_message_fields) + len(turn_outcome_fields) + len(sales_decision_fields),
                "requires_reply_schema_before_activation": True,
            },
            "ownership_contract": {
                "reply_owns": [
                    "final_customer_visible_messages",
                    "complex_turn_outcome",
                    "single_mainline_action",
                    "closing_move",
                ],
                "tool_planner_must_not_own": [
                    "customer_visible_text",
                    "sales_psychology",
                    "closing_move",
                    "complex_customer_state",
                ],
            },
            "safety": {
                "shadow_only": True,
                "no_model_payload_consumption": True,
                "no_customer_messages_sent": True,
                "no_database_writes": True,
            },
        }
    )


def _present_fields(output: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if _has_value(output.get(field))]


def _pick(output: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: output[field] for field in fields}


def _fact_and_tool_summary(output: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if "tool_plan_preview" in fields:
        tool_plan = output.get("tool_plan_preview") if isinstance(output.get("tool_plan_preview"), dict) else {}
        summary["tool_plan_preview"] = _drop_empty(
            {
                "fact_requirement": tool_plan.get("fact_requirement"),
                "read_tool_count": len(tool_plan.get("read_tool_calls") or []),
                "deferred_write_count": len(tool_plan.get("deferred_write_proposals") or []),
                "unknown_tool_count": len(tool_plan.get("unknown_tools") or []),
                "migration_audit": tool_plan.get("migration_audit") if isinstance(tool_plan.get("migration_audit"), dict) else {},
            }
        )
    if "read_only_tool_executor_shadow" in fields:
        executor = output.get("read_only_tool_executor_shadow") if isinstance(output.get("read_only_tool_executor_shadow"), dict) else {}
        summary["read_only_tool_executor_shadow"] = _drop_empty(
            {
                "mode": executor.get("mode"),
                "would_execute_count": (executor.get("summary") or {}).get("would_execute_count"),
                "blocked_count": (executor.get("summary") or {}).get("blocked_count"),
            }
        )
    if "reply_chain_join_shadow" in fields:
        join = output.get("reply_chain_join_shadow") if isinstance(output.get("reply_chain_join_shadow"), dict) else {}
        summary["reply_chain_join_shadow"] = _drop_empty(
            {
                "final_route": join.get("final_route"),
                "direct_reply_allowed": join.get("direct_reply_allowed"),
                "read_tool_count": join.get("read_tool_count"),
            }
        )
    return _drop_empty(summary)


def _has_value(value: Any) -> bool:
    if value in ("", None, [], {}):
        return False
    return True


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
