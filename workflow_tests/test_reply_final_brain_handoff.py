from __future__ import annotations

import json

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.reply_final_brain_handoff import reply_final_brain_handoff_shadow_from_planner_output


def test_reply_final_brain_handoff_groups_legacy_planner_semantics() -> None:
    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "planner_reply_messages": [{"type": "text", "content": {"text": "customer candidate"}}],
            "planner_decision": "need_tools",
            "conversion_stage": "payment",
            "payment_decision": {"action": "send_now"},
            "reply_strategy": {"tone": "warm"},
            "tool_plan_preview": {
                "schema_version": "tool_plan_preview_v2",
                "fact_requirement": "required",
                "read_tool_calls": [{"name": "customer_store_lookup"}],
                "migration_audit": {"legacy_residue_count": 2},
            },
            "read_only_tool_executor_shadow": {
                "schema_version": "read_only_tool_executor_shadow_v1",
                "mode": "dry_run_no_external_calls",
                "summary": {"would_execute_count": 1, "blocked_count": 0},
                "dependency_audit": {
                    "schema_version": "read_only_tool_dependency_audit_v1",
                    "ready_for_early_execution_ordering": True,
                },
            },
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "final_route": "reply_with_tools",
                "direct_reply_allowed": False,
                "final_expression_boundary": {
                    "schema_version": "reply_final_expression_boundary_v1",
                    "join_generates_customer_visible_text": False,
                    "join_decides_sales_psychology": False,
                },
            },
        },
        reply_chain_shadow_context={
            "schema_version": "reply_chain_shadow_v1",
            "authority_audit": {
                "schema_version": "reply_chain_authority_audit_v1",
                "complete_chat_is_primary_authority": True,
                "all_messages_have_sent_at": True,
                "timeline_window_audit": {
                    "schema_version": "reply_chain_timeline_window_audit_v1",
                    "ready_for_authoritative_model_input": True,
                    "source_window_complete": True,
                    "truncated": False,
                },
                "current_message_audit": {
                    "schema_version": "reply_chain_current_message_audit_v1",
                    "current_message_in_timeline": True,
                    "current_message_is_last": True,
                    "ready_for_authoritative_model_input": True,
                },
                "fact_snapshot": {"schema_version": "reply_chain_fact_snapshot_audit_v1"},
            },
        },
        gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
    )

    assert handoff["schema_version"] == "reply_final_brain_handoff_shadow_v1"
    assert handoff["target_owner"] == "reply"
    assert handoff["input_groups"]["customer_message_candidates"]["planner_reply_messages"][0]["type"] == "text"
    assert handoff["input_groups"]["turn_outcome_signals"]["planner_decision"] == "need_tools"
    assert handoff["input_groups"]["turn_outcome_signals"]["conversion_stage"] == "payment"
    assert handoff["input_groups"]["sales_decision_signals"]["payment_decision"]["action"] == "send_now"
    assert handoff["input_groups"]["sales_decision_signals"]["reply_strategy"]["tone"] == "warm"
    assert handoff["input_groups"]["fact_and_tool_evidence"]["tool_plan_preview"]["read_tool_count"] == 1
    assert handoff["input_groups"]["fact_and_tool_evidence"]["read_only_tool_executor_shadow"]["would_execute_count"] == 1
    assert handoff["input_groups"]["fact_and_tool_evidence"]["read_only_tool_executor_shadow"]["dependency_ready"] is True
    assert handoff["migration_audit"]["legacy_business_field_count"] == 5
    assert handoff["migration_audit"]["requires_reply_schema_before_activation"] is True
    target_schema = handoff["target_reply_input_schema"]
    assert target_schema["schema_version"] == "reply_final_brain_target_input_schema_v1"
    active_group_ids = {group["group_id"] for group in target_schema["active_input_groups"]}
    shadow_only_group_ids = {group["group_id"] for group in target_schema["shadow_only_groups"]}
    assert "complete_timed_chat" in active_group_ids
    assert "authoritative_facts" in active_group_ids
    assert "gate_content_candidates" in active_group_ids
    assert "read_only_tool_facts" in active_group_ids
    assert "join_route_and_conflicts" in active_group_ids
    assert "legacy_planner_customer_message_candidates" not in active_group_ids
    assert "legacy_planner_customer_message_candidates" in shadow_only_group_ids
    assert "legacy_planner_sales_decision_signals" in shadow_only_group_ids
    assert handoff["target_reply_input_schema_audit"]["schema_version"] == "reply_final_brain_target_input_schema_audit_v1"
    assert handoff["target_reply_input_schema_audit"]["ready_for_reply_payload_design_review"] is True
    mapping_audit = handoff["migration_audit"]["field_mapping_audit"]
    assert mapping_audit["schema_version"] == "reply_legacy_field_mapping_audit_v1"
    assert mapping_audit["legacy_business_field_count"] == 5
    assert mapping_audit["mapped_legacy_business_field_count"] == 5
    assert mapping_audit["all_legacy_business_fields_mapped"] is True
    mapping_targets = {
        mapping["legacy_field"]: mapping["target_reply_contract"]
        for mapping in mapping_audit["mappings"]
    }
    assert mapping_targets["planner_reply_messages"] == "reply_output_schema.final_customer_visible_messages"
    assert mapping_targets["payment_decision"] == "reply_output_schema.payment_action"
    assert mapping_targets["reply_strategy"] == "reply_output_schema.expression_style"
    assert handoff["handoff_readiness_audit"]["schema_version"] == "reply_final_brain_handoff_readiness_audit_v1"
    assert handoff["handoff_readiness_audit"]["ready_for_reply_payload_switch_shadow"] is True
    assert handoff["handoff_readiness_audit"]["observed_inputs"]["timeline_window_audit_schema"] == "reply_chain_timeline_window_audit_v1"
    assert handoff["handoff_readiness_audit"]["observed_inputs"]["timeline_window_ready"] is True
    assert handoff["handoff_readiness_audit"]["observed_inputs"]["target_reply_input_schema_version"] == "reply_final_brain_target_input_schema_v1"
    assert handoff["handoff_readiness_audit"]["observed_inputs"]["target_reply_input_schema_ready"] is True
    assert "customer_visible_text" in handoff["ownership_contract"]["tool_planner_must_not_own"]


def test_reply_final_brain_handoff_allows_fact_only_output() -> None:
    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": {
                "fact_requirement": "none",
                "migration_audit": {"legacy_residue_count": 0},
            }
        }
    )

    assert handoff["migration_audit"]["legacy_business_field_count"] == 0
    assert handoff["migration_audit"]["field_mapping_audit"]["all_legacy_business_fields_mapped"] is True
    assert "customer_message_candidates" not in handoff["input_groups"]
    assert "sales_decision_signals" not in handoff["input_groups"]
    assert handoff["input_groups"]["fact_and_tool_evidence"]["tool_plan_preview"]["fact_requirement"] == "none"
    assert handoff["handoff_readiness_audit"]["ready_for_reply_payload_switch_shadow"] is False
    assert "missing_complete_timed_chat_context" in handoff["handoff_readiness_audit"]["blockers"]


def test_reply_final_brain_handoff_blocks_unmapped_legacy_business_fields(monkeypatch) -> None:
    import app.services.reply_final_brain_handoff as handoff_module

    target_map = dict(handoff_module.LEGACY_FIELD_TARGETS)
    target_map.pop("payment_decision")
    monkeypatch.setattr(handoff_module, "LEGACY_FIELD_TARGETS", target_map)

    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "payment_decision": {"action": "send_now"},
            "tool_plan_preview": {"schema_version": "tool_plan_preview_v2"},
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "final_expression_boundary": {
                    "schema_version": "reply_final_expression_boundary_v1",
                    "join_generates_customer_visible_text": False,
                    "join_decides_sales_psychology": False,
                },
            },
        },
        reply_chain_shadow_context={
            "schema_version": "reply_chain_shadow_v1",
            "authority_audit": {
                "schema_version": "reply_chain_authority_audit_v1",
                "complete_chat_is_primary_authority": True,
                "all_messages_have_sent_at": True,
                "timeline_window_audit": {
                    "schema_version": "reply_chain_timeline_window_audit_v1",
                    "ready_for_authoritative_model_input": True,
                    "source_window_complete": True,
                    "truncated": False,
                },
                "current_message_audit": {
                    "schema_version": "reply_chain_current_message_audit_v1",
                    "ready_for_authoritative_model_input": True,
                },
                "fact_snapshot": {"schema_version": "reply_chain_fact_snapshot_audit_v1"},
            },
        },
        gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
    )

    mapping_audit = handoff["migration_audit"]["field_mapping_audit"]
    assert mapping_audit["all_legacy_business_fields_mapped"] is False
    assert mapping_audit["unmapped_legacy_business_fields"] == ["payment_decision"]
    assert handoff["handoff_readiness_audit"]["ready_for_reply_payload_switch_shadow"] is False
    assert "unmapped_legacy_business_field:payment_decision" in handoff["handoff_readiness_audit"]["blockers"]


def test_reply_final_brain_handoff_blocks_legacy_planner_groups_in_active_schema(monkeypatch) -> None:
    import app.services.reply_final_brain_handoff as handoff_module

    monkeypatch.setattr(
        handoff_module,
        "TARGET_REPLY_ACTIVE_INPUT_GROUPS",
        (
            {
                "group_id": "complete_timed_chat",
                "source": "reply_chain_shadow_context.timeline",
                "consumption_rule": "authoritative_input",
            },
            {
                "group_id": "legacy_planner_sales_decision_signals",
                "source": "input_groups.sales_decision_signals",
                "consumption_rule": "active_input",
            },
        ),
    )

    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": {"schema_version": "tool_plan_preview_v2"},
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "final_expression_boundary": {
                    "schema_version": "reply_final_expression_boundary_v1",
                    "join_generates_customer_visible_text": False,
                    "join_decides_sales_psychology": False,
                },
            },
        },
        reply_chain_shadow_context={
            "schema_version": "reply_chain_shadow_v1",
            "authority_audit": {
                "schema_version": "reply_chain_authority_audit_v1",
                "complete_chat_is_primary_authority": True,
                "all_messages_have_sent_at": True,
                "timeline_window_audit": {
                    "schema_version": "reply_chain_timeline_window_audit_v1",
                    "ready_for_authoritative_model_input": True,
                    "source_window_complete": True,
                    "truncated": False,
                },
                "current_message_audit": {
                    "schema_version": "reply_chain_current_message_audit_v1",
                    "ready_for_authoritative_model_input": True,
                },
                "fact_snapshot": {"schema_version": "reply_chain_fact_snapshot_audit_v1"},
            },
        },
        gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
    )

    assert handoff["target_reply_input_schema_audit"]["ready_for_reply_payload_design_review"] is False
    assert (
        "legacy_or_planner_group_marked_active:legacy_planner_sales_decision_signals"
        in handoff["target_reply_input_schema_audit"]["blockers"]
    )
    assert handoff["handoff_readiness_audit"]["ready_for_reply_payload_switch_shadow"] is False
    assert (
        "target_reply_input_schema:legacy_or_planner_group_marked_active:legacy_planner_sales_decision_signals"
        in handoff["handoff_readiness_audit"]["blockers"]
    )


def test_reply_final_brain_handoff_blocks_legacy_planner_sources_in_active_schema(monkeypatch) -> None:
    import app.services.reply_final_brain_handoff as handoff_module

    monkeypatch.setattr(
        handoff_module,
        "TARGET_REPLY_ACTIVE_INPUT_GROUPS",
        (
            {
                "group_id": "complete_timed_chat",
                "source": "reply_chain_shadow_context.timeline",
                "consumption_rule": "authoritative_input",
            },
            {
                "group_id": "sales_context",
                "source": "input_groups.sales_decision_signals",
                "consumption_rule": "active_input",
            },
        ),
    )

    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": {"schema_version": "tool_plan_preview_v2"},
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "final_expression_boundary": {
                    "schema_version": "reply_final_expression_boundary_v1",
                    "join_generates_customer_visible_text": False,
                    "join_decides_sales_psychology": False,
                },
            },
        },
        reply_chain_shadow_context={
            "schema_version": "reply_chain_shadow_v1",
            "authority_audit": {
                "schema_version": "reply_chain_authority_audit_v1",
                "complete_chat_is_primary_authority": True,
                "all_messages_have_sent_at": True,
                "timeline_window_audit": {
                    "schema_version": "reply_chain_timeline_window_audit_v1",
                    "ready_for_authoritative_model_input": True,
                    "source_window_complete": True,
                    "truncated": False,
                },
                "current_message_audit": {
                    "schema_version": "reply_chain_current_message_audit_v1",
                    "ready_for_authoritative_model_input": True,
                },
                "fact_snapshot": {"schema_version": "reply_chain_fact_snapshot_audit_v1"},
            },
        },
        gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
    )

    assert handoff["target_reply_input_schema_audit"]["ready_for_reply_payload_design_review"] is False
    assert (
        "legacy_or_planner_source_marked_active:sales_context:input_groups.sales_decision_signals"
        in handoff["target_reply_input_schema_audit"]["blockers"]
    )
    assert (
        "target_reply_input_schema:legacy_or_planner_source_marked_active:sales_context:input_groups.sales_decision_signals"
        in handoff["handoff_readiness_audit"]["blockers"]
    )


def test_reply_final_brain_handoff_blocks_shadow_consumption_rule_in_active_schema(monkeypatch) -> None:
    import app.services.reply_final_brain_handoff as handoff_module

    monkeypatch.setattr(
        handoff_module,
        "TARGET_REPLY_ACTIVE_INPUT_GROUPS",
        (
            {
                "group_id": "complete_timed_chat",
                "source": "reply_chain_shadow_context.timeline",
                "consumption_rule": "shadow_comparison_only",
            },
        ),
    )

    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": {"schema_version": "tool_plan_preview_v2"},
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "final_expression_boundary": {
                    "schema_version": "reply_final_expression_boundary_v1",
                    "join_generates_customer_visible_text": False,
                    "join_decides_sales_psychology": False,
                },
            },
        },
        reply_chain_shadow_context={
            "schema_version": "reply_chain_shadow_v1",
            "authority_audit": {
                "schema_version": "reply_chain_authority_audit_v1",
                "complete_chat_is_primary_authority": True,
                "all_messages_have_sent_at": True,
                "timeline_window_audit": {
                    "schema_version": "reply_chain_timeline_window_audit_v1",
                    "ready_for_authoritative_model_input": True,
                    "source_window_complete": True,
                    "truncated": False,
                },
                "current_message_audit": {
                    "schema_version": "reply_chain_current_message_audit_v1",
                    "ready_for_authoritative_model_input": True,
                },
                "fact_snapshot": {"schema_version": "reply_chain_fact_snapshot_audit_v1"},
            },
        },
        gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
    )

    assert handoff["target_reply_input_schema_audit"]["ready_for_reply_payload_design_review"] is False
    assert (
        "shadow_consumption_rule_marked_active:complete_timed_chat:shadow_comparison_only"
        in handoff["target_reply_input_schema_audit"]["blockers"]
    )
    assert (
        "target_reply_input_schema:shadow_consumption_rule_marked_active:complete_timed_chat:shadow_comparison_only"
        in handoff["handoff_readiness_audit"]["blockers"]
    )


def test_reply_final_brain_handoff_blocks_active_consumption_rule_in_shadow_only_schema(monkeypatch) -> None:
    import app.services.reply_final_brain_handoff as handoff_module

    monkeypatch.setattr(
        handoff_module,
        "TARGET_REPLY_SHADOW_ONLY_GROUPS",
        (
            {
                "group_id": "legacy_planner_customer_message_candidates",
                "source": "input_groups.customer_message_candidates",
                "consumption_rule": "active_input",
            },
            {
                "group_id": "legacy_planner_turn_outcome_signals",
                "source": "input_groups.turn_outcome_signals",
                "consumption_rule": "shadow_comparison_only",
            },
            {
                "group_id": "legacy_planner_sales_decision_signals",
                "source": "input_groups.sales_decision_signals",
                "consumption_rule": "shadow_comparison_only",
            },
            {
                "group_id": "legacy_planner_field_mapping_audit",
                "source": "migration_audit.field_mapping_audit",
                "consumption_rule": "review_evidence_only",
            },
        ),
    )

    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": {"schema_version": "tool_plan_preview_v2"},
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "final_expression_boundary": {
                    "schema_version": "reply_final_expression_boundary_v1",
                    "join_generates_customer_visible_text": False,
                    "join_decides_sales_psychology": False,
                },
            },
        },
        reply_chain_shadow_context={
            "schema_version": "reply_chain_shadow_v1",
            "authority_audit": {
                "schema_version": "reply_chain_authority_audit_v1",
                "complete_chat_is_primary_authority": True,
                "all_messages_have_sent_at": True,
                "timeline_window_audit": {
                    "schema_version": "reply_chain_timeline_window_audit_v1",
                    "ready_for_authoritative_model_input": True,
                    "source_window_complete": True,
                    "truncated": False,
                },
                "current_message_audit": {
                    "schema_version": "reply_chain_current_message_audit_v1",
                    "ready_for_authoritative_model_input": True,
                },
                "fact_snapshot": {"schema_version": "reply_chain_fact_snapshot_audit_v1"},
            },
        },
        gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
    )

    assert handoff["target_reply_input_schema_audit"]["ready_for_reply_payload_design_review"] is False
    assert (
        "active_consumption_rule_marked_shadow_only:legacy_planner_customer_message_candidates:active_input"
        in handoff["target_reply_input_schema_audit"]["blockers"]
    )
    assert (
        "target_reply_input_schema:active_consumption_rule_marked_shadow_only:legacy_planner_customer_message_candidates:active_input"
        in handoff["handoff_readiness_audit"]["blockers"]
    )


def test_reply_final_brain_handoff_blocks_when_join_can_generate_customer_text() -> None:
    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": {"schema_version": "tool_plan_preview_v2"},
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "final_expression_boundary": {
                    "schema_version": "reply_final_expression_boundary_v1",
                    "join_generates_customer_visible_text": True,
                    "join_decides_sales_psychology": False,
                },
            },
        },
        reply_chain_shadow_context={
            "schema_version": "reply_chain_shadow_v1",
            "authority_audit": {
                "schema_version": "reply_chain_authority_audit_v1",
                "complete_chat_is_primary_authority": True,
                "all_messages_have_sent_at": True,
                "timeline_window_audit": {
                    "schema_version": "reply_chain_timeline_window_audit_v1",
                    "ready_for_authoritative_model_input": True,
                    "source_window_complete": True,
                    "truncated": False,
                },
                "current_message_audit": {
                    "schema_version": "reply_chain_current_message_audit_v1",
                    "current_message_in_timeline": True,
                    "current_message_is_last": True,
                    "ready_for_authoritative_model_input": True,
                },
                "fact_snapshot": {"schema_version": "reply_chain_fact_snapshot_audit_v1"},
            },
        },
        gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
    )

    assert handoff["handoff_readiness_audit"]["ready_for_reply_payload_switch_shadow"] is False
    assert "join_may_generate_customer_visible_text" in handoff["handoff_readiness_audit"]["blockers"]


def test_reply_final_brain_handoff_blocks_when_current_message_not_authoritative() -> None:
    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": {"schema_version": "tool_plan_preview_v2"},
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "final_expression_boundary": {
                    "schema_version": "reply_final_expression_boundary_v1",
                    "join_generates_customer_visible_text": False,
                    "join_decides_sales_psychology": False,
                },
            },
        },
        reply_chain_shadow_context={
            "schema_version": "reply_chain_shadow_v1",
            "authority_audit": {
                "schema_version": "reply_chain_authority_audit_v1",
                "complete_chat_is_primary_authority": True,
                "all_messages_have_sent_at": True,
                "timeline_window_audit": {
                    "schema_version": "reply_chain_timeline_window_audit_v1",
                    "ready_for_authoritative_model_input": True,
                    "source_window_complete": True,
                    "truncated": False,
                },
                "current_message_audit": {
                    "schema_version": "reply_chain_current_message_audit_v1",
                    "current_message_in_timeline": True,
                    "current_message_is_last": False,
                    "ready_for_authoritative_model_input": False,
                    "blockers": ["current_message_not_last_in_timeline"],
                },
                "fact_snapshot": {"schema_version": "reply_chain_fact_snapshot_audit_v1"},
            },
        },
        gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
    )

    assert handoff["handoff_readiness_audit"]["ready_for_reply_payload_switch_shadow"] is False
    assert "current_message:current_message_not_last_in_timeline" in handoff["handoff_readiness_audit"]["blockers"]


def test_reply_final_brain_handoff_blocks_when_timeline_window_not_ready() -> None:
    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": {"schema_version": "tool_plan_preview_v2"},
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "final_expression_boundary": {
                    "schema_version": "reply_final_expression_boundary_v1",
                    "join_generates_customer_visible_text": False,
                    "join_decides_sales_psychology": False,
                },
            },
        },
        reply_chain_shadow_context={
            "schema_version": "reply_chain_shadow_v1",
            "authority_audit": {
                "schema_version": "reply_chain_authority_audit_v1",
                "complete_chat_is_primary_authority": True,
                "all_messages_have_sent_at": True,
                "timeline_window_audit": {
                    "schema_version": "reply_chain_timeline_window_audit_v1",
                    "ready_for_authoritative_model_input": False,
                    "source_window_complete": False,
                    "blockers": ["source_window_incomplete_under_limit"],
                },
                "current_message_audit": {
                    "schema_version": "reply_chain_current_message_audit_v1",
                    "ready_for_authoritative_model_input": True,
                },
                "fact_snapshot": {"schema_version": "reply_chain_fact_snapshot_audit_v1"},
            },
        },
        gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
    )

    audit = handoff["handoff_readiness_audit"]
    assert audit["ready_for_reply_payload_switch_shadow"] is False
    assert "timeline_window:source_window_incomplete_under_limit" in audit["blockers"]
    assert audit["observed_inputs"]["timeline_window_ready"] is False


def test_reply_final_brain_handoff_blocks_when_read_tools_have_no_executor_facts() -> None:
    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": {
                "schema_version": "tool_plan_preview_v2",
                "fact_requirement": "required",
                "read_tool_calls": [{"tool": "customer_store_lookup"}],
            },
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "final_expression_boundary": {
                    "schema_version": "reply_final_expression_boundary_v1",
                    "join_generates_customer_visible_text": False,
                    "join_decides_sales_psychology": False,
                },
            },
        },
        reply_chain_shadow_context={
            "schema_version": "reply_chain_shadow_v1",
            "authority_audit": {
                "schema_version": "reply_chain_authority_audit_v1",
                "complete_chat_is_primary_authority": True,
                "all_messages_have_sent_at": True,
                "timeline_window_audit": {
                    "schema_version": "reply_chain_timeline_window_audit_v1",
                    "ready_for_authoritative_model_input": True,
                    "source_window_complete": True,
                    "truncated": False,
                },
                "current_message_audit": {
                    "schema_version": "reply_chain_current_message_audit_v1",
                    "ready_for_authoritative_model_input": True,
                },
                "fact_snapshot": {"schema_version": "reply_chain_fact_snapshot_audit_v1"},
            },
        },
        gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
    )

    audit = handoff["handoff_readiness_audit"]
    assert audit["ready_for_reply_payload_switch_shadow"] is False
    assert "missing_read_only_tool_executor_shadow" in audit["blockers"]
    assert "missing_read_only_tool_dependency_audit" in audit["blockers"]
    assert audit["observed_inputs"]["tool_plan_read_tool_count"] == 1


def test_reply_final_brain_handoff_blocks_when_read_tool_dependencies_are_not_ready() -> None:
    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": {
                "schema_version": "tool_plan_preview_v2",
                "fact_requirement": "required",
                "read_tool_calls": [{"tool": "customer_store_lookup", "call_id": "store_1"}],
            },
            "read_only_tool_executor_shadow": {
                "schema_version": "read_only_tool_executor_shadow_v1",
                "mode": "dry_run_no_external_calls",
                "blocked": [{"call_id": "store_1", "reason": "missing_read_tool_dependency"}],
                "summary": {"would_execute_count": 0, "blocked_count": 1},
                "dependency_audit": {
                    "schema_version": "read_only_tool_dependency_audit_v1",
                    "ready_for_early_execution_ordering": False,
                    "blockers": ["missing_read_tool_dependency"],
                },
            },
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "final_expression_boundary": {
                    "schema_version": "reply_final_expression_boundary_v1",
                    "join_generates_customer_visible_text": False,
                    "join_decides_sales_psychology": False,
                },
            },
        },
        reply_chain_shadow_context={
            "schema_version": "reply_chain_shadow_v1",
            "authority_audit": {
                "schema_version": "reply_chain_authority_audit_v1",
                "complete_chat_is_primary_authority": True,
                "all_messages_have_sent_at": True,
                "timeline_window_audit": {
                    "schema_version": "reply_chain_timeline_window_audit_v1",
                    "ready_for_authoritative_model_input": True,
                    "source_window_complete": True,
                    "truncated": False,
                },
                "current_message_audit": {
                    "schema_version": "reply_chain_current_message_audit_v1",
                    "ready_for_authoritative_model_input": True,
                },
                "fact_snapshot": {"schema_version": "reply_chain_fact_snapshot_audit_v1"},
            },
        },
        gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
    )

    audit = handoff["handoff_readiness_audit"]
    assert audit["ready_for_reply_payload_switch_shadow"] is False
    assert "read_only_tool_executor_has_blocked_calls" in audit["blockers"]
    assert "read_tool_dependency:missing_read_tool_dependency" in audit["blockers"]
    assert audit["observed_inputs"]["read_tool_dependency_ready"] is False


def test_reply_final_brain_handoff_blocks_when_required_facts_have_no_read_tools() -> None:
    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": {
                "schema_version": "tool_plan_preview_v2",
                "fact_requirement": "required",
            },
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "final_expression_boundary": {
                    "schema_version": "reply_final_expression_boundary_v1",
                    "join_generates_customer_visible_text": False,
                    "join_decides_sales_psychology": False,
                },
            },
        },
        reply_chain_shadow_context={
            "schema_version": "reply_chain_shadow_v1",
            "authority_audit": {
                "schema_version": "reply_chain_authority_audit_v1",
                "complete_chat_is_primary_authority": True,
                "all_messages_have_sent_at": True,
                "timeline_window_audit": {
                    "schema_version": "reply_chain_timeline_window_audit_v1",
                    "ready_for_authoritative_model_input": True,
                    "source_window_complete": True,
                    "truncated": False,
                },
                "current_message_audit": {
                    "schema_version": "reply_chain_current_message_audit_v1",
                    "ready_for_authoritative_model_input": True,
                },
                "fact_snapshot": {"schema_version": "reply_chain_fact_snapshot_audit_v1"},
            },
        },
        gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
    )

    assert handoff["handoff_readiness_audit"]["ready_for_reply_payload_switch_shadow"] is False
    assert "required_facts_without_read_tool_calls" in handoff["handoff_readiness_audit"]["blockers"]


def test_reply_final_brain_handoff_is_not_consumed_by_current_model_payloads() -> None:
    state = {
        "normalized_content": "how to book",
        "conversation_history": ["user: how to book"],
        "reply_final_brain_handoff_shadow": {
            "schema_version": "reply_final_brain_handoff_shadow_v1",
            "input_groups": {
                "customer_message_candidates": {
                    "planner_reply_messages": [{"content": {"text": "shadow-only-handoff-marker"}}],
                }
            },
        },
        "request_context": {},
    }

    planner_payload = _planner_payload_for_model(state)
    reply_payload = reply_user_payload_for_model(state)
    combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

    assert "reply_final_brain_handoff_shadow" not in planner_payload
    assert "reply_final_brain_handoff_shadow" not in reply_payload
    assert "shadow-only-handoff-marker" not in combined
