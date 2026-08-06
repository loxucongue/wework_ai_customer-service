from __future__ import annotations

import json
import unittest

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.parallel_reply_chain_shadow import parallel_reply_chain_shadow


def _reply_chain_shadow_context(
    *,
    all_messages_have_sent_at: bool = True,
    current_message_ready: bool = True,
    current_message_blockers: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "reply_chain_shadow_v1",
        "authority_audit": {
            "schema_version": "reply_chain_authority_audit_v1",
            "complete_chat_is_primary_authority": True,
            "soft_profile_excluded_from_authority": True,
            "timeline_message_count": 3,
            "all_messages_have_sent_at": all_messages_have_sent_at,
            "timeline_window_audit": {
                "schema_version": "reply_chain_timeline_window_audit_v1",
                "ready_for_authoritative_model_input": True,
                "truncated": False,
                "dropped_message_count": 0,
                "retained_window": {
                    "schema_version": "reply_chain_retained_timeline_window_v1",
                    "oldest_message_ref": "m1",
                    "newest_message_ref": "current",
                    "current_request_message_refs": ["current"],
                },
                "blockers": [],
            },
            "current_message_audit": {
                "schema_version": "reply_chain_current_message_audit_v1",
                "current_message_in_timeline": current_message_ready,
                "current_message_is_last": current_message_ready,
                "ready_for_authoritative_model_input": current_message_ready,
                "blockers": current_message_blockers or [],
            },
            "fact_snapshot": {
                "schema_version": "reply_chain_fact_snapshot_audit_v1",
                "sections_with_error": [],
            },
        },
    }


def _reply_final_brain_handoff_shadow(
    *,
    legacy_business_field_count: int = 4,
    requires_reply_schema_before_activation: bool = True,
    handoff_ready: bool = True,
    handoff_blockers: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "reply_final_brain_handoff_shadow_v1",
        "target_reply_input_schema_audit": {
            "schema_version": "reply_final_brain_target_input_schema_audit_v1",
            "target_schema_version": "reply_final_brain_target_input_schema_v1",
            "ready_for_reply_payload_design_review": True,
            "active_group_count": 5,
            "shadow_only_group_count": 4,
            "blockers": [],
        },
        "migration_audit": {
            "legacy_business_field_count": legacy_business_field_count,
            "requires_reply_schema_before_activation": requires_reply_schema_before_activation,
        },
        "handoff_readiness_audit": {
            "schema_version": "reply_final_brain_handoff_readiness_audit_v1",
            "ready_for_reply_payload_switch_shadow": handoff_ready,
            "blockers": handoff_blockers or [],
        },
    }


def _gate_router_shadow(**overrides: object) -> dict:
    base = {
        "schema_version": "chat_gate_router_shadow_v1",
        "route_suggestion": "content_only_reply",
        "commit_boundary": {
            "schema_version": "chat_gate_commit_boundary_v1",
            "shadow_output_only": True,
            "this_shadow_creates_sop_task": False,
            "this_shadow_updates_send_once": False,
            "this_shadow_sends_customer_messages": False,
            "this_shadow_writes_database": False,
            "target_commit_owner": "reply_chain_commit_phase_after_reply_validation",
        },
    }
    base.update(overrides)
    return base


def _join_shadow(**overrides: object) -> dict:
    base = {
        "schema_version": "reply_chain_join_shadow_v1",
        "final_route": "reply_with_content",
        "direct_reply_allowed": False,
        "direct_reply_guard_audit": {
            "schema_version": "reply_chain_direct_reply_guard_audit_v1",
            "direct_reply_requested": False,
            "ready_for_direct_reply": False,
            "blockers": [],
        },
        "final_expression_boundary": {
            "schema_version": "reply_final_expression_boundary_v1",
            "reply_required_for_complex_turn": True,
            "final_customer_message_owner": "reply",
            "join_generates_customer_visible_text": False,
            "join_decides_sales_psychology": False,
        },
    }
    base.update(overrides)
    return base


class ParallelReplyChainShadowTests(unittest.TestCase):
    def test_parallel_contract_keeps_gate_planner_and_reply_ownership_separate(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(),
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={
                "schema_version": "tool_plan_preview_v2",
                "fact_requirement": "none",
                "migration_audit": {
                    "legacy_residue_count": 2,
                    "tool_planner_only_ready": False,
                },
            },
            read_only_tool_executor_shadow={
                "schema_version": "read_only_tool_executor_shadow_v1",
                "mode": "dry_run_no_external_calls",
            },
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(
                legacy_business_field_count=6,
                requires_reply_schema_before_activation=True,
            ),
        )

        self.assertEqual(shadow["schema_version"], "parallel_reply_chain_shadow_v1")
        self.assertEqual(shadow["mode"], "shadow_no_parallel_execution")
        self.assertEqual(shadow["target_topology"]["parallel_branches"], ["sop_chat_gate", "tool_planner"])
        self.assertEqual(shadow["target_topology"]["final_expression_owner"], "reply")
        self.assertEqual(
            shadow["current_serial_observation"]["shared_context_authority_audit_schema"],
            "reply_chain_authority_audit_v1",
        )
        self.assertEqual(shadow["current_serial_observation"]["shared_context_message_count"], 3)
        self.assertTrue(shadow["current_serial_observation"]["shared_context_all_messages_have_sent_at"])
        self.assertTrue(shadow["current_serial_observation"]["shared_context_complete_chat_is_primary"])
        self.assertTrue(shadow["current_serial_observation"]["shared_context_soft_profile_excluded"])
        self.assertEqual(
            shadow["current_serial_observation"]["shared_context_current_message_audit_schema"],
            "reply_chain_current_message_audit_v1",
        )
        self.assertTrue(shadow["current_serial_observation"]["shared_context_current_message_in_timeline"])
        self.assertTrue(shadow["current_serial_observation"]["shared_context_current_message_is_last"])
        self.assertTrue(shadow["current_serial_observation"]["shared_context_current_message_ready"])
        self.assertEqual(
            shadow["current_serial_observation"]["shared_context_timeline_window_audit_schema"],
            "reply_chain_timeline_window_audit_v1",
        )
        self.assertTrue(shadow["current_serial_observation"]["shared_context_timeline_window_ready"])
        self.assertFalse(shadow["current_serial_observation"]["shared_context_timeline_window_truncated"])
        self.assertEqual(shadow["current_serial_observation"]["shared_context_timeline_window_dropped_count"], 0)
        self.assertEqual(
            shadow["current_serial_observation"]["shared_context_timeline_retained_window_schema"],
            "reply_chain_retained_timeline_window_v1",
        )
        self.assertEqual(shadow["current_serial_observation"]["shared_context_timeline_retained_oldest_message_ref"], "m1")
        self.assertEqual(shadow["current_serial_observation"]["shared_context_timeline_retained_newest_message_ref"], "current")
        self.assertEqual(
            shadow["current_serial_observation"]["shared_context_timeline_retained_current_request_message_refs"],
            ["current"],
        )
        self.assertEqual(
            shadow["current_serial_observation"]["shared_context_fact_snapshot_schema"],
            "reply_chain_fact_snapshot_audit_v1",
        )
        self.assertEqual(shadow["current_serial_observation"]["tool_planner_legacy_residue_count"], 2)
        self.assertEqual(
            shadow["current_serial_observation"]["gate_commit_boundary_schema"],
            "chat_gate_commit_boundary_v1",
        )
        self.assertTrue(shadow["current_serial_observation"]["gate_shadow_output_only"])
        self.assertEqual(
            shadow["current_serial_observation"]["gate_target_commit_owner"],
            "reply_chain_commit_phase_after_reply_validation",
        )
        self.assertFalse(shadow["current_serial_observation"]["gate_shadow_creates_sop_task"])
        self.assertFalse(shadow["current_serial_observation"]["gate_shadow_updates_send_once"])
        self.assertFalse(shadow["current_serial_observation"]["gate_shadow_sends_customer_messages"])
        self.assertFalse(shadow["current_serial_observation"]["gate_shadow_writes_database"])
        self.assertFalse(shadow["current_serial_observation"]["tool_planner_only_ready"])
        self.assertEqual(
            shadow["current_serial_observation"]["reply_handoff_schema"],
            "reply_final_brain_handoff_shadow_v1",
        )
        self.assertEqual(
            shadow["current_serial_observation"]["join_final_expression_boundary_schema"],
            "reply_final_expression_boundary_v1",
        )
        self.assertEqual(
            shadow["current_serial_observation"]["direct_reply_guard_schema"],
            "reply_chain_direct_reply_guard_audit_v1",
        )
        self.assertFalse(shadow["current_serial_observation"]["direct_reply_guard_requested"])
        self.assertEqual(shadow["current_serial_observation"]["join_final_customer_message_owner"], "reply")
        self.assertTrue(shadow["current_serial_observation"]["join_reply_required_for_complex_turn"])
        self.assertFalse(shadow["current_serial_observation"]["join_generates_customer_visible_text"])
        self.assertFalse(shadow["current_serial_observation"]["join_decides_sales_psychology"])
        self.assertEqual(shadow["current_serial_observation"]["reply_legacy_business_field_count"], 6)
        self.assertEqual(shadow["current_serial_observation"]["reply_handoff_legacy_business_field_count"], 6)
        self.assertTrue(shadow["current_serial_observation"]["reply_handoff_requires_schema"])
        self.assertEqual(
            shadow["current_serial_observation"]["reply_handoff_readiness_schema"],
            "reply_final_brain_handoff_readiness_audit_v1",
        )
        self.assertTrue(shadow["current_serial_observation"]["reply_handoff_ready_for_payload_switch_shadow"])
        self.assertEqual(
            shadow["current_serial_observation"]["reply_target_input_schema_audit_schema"],
            "reply_final_brain_target_input_schema_audit_v1",
        )
        self.assertEqual(
            shadow["current_serial_observation"]["reply_target_input_schema_version"],
            "reply_final_brain_target_input_schema_v1",
        )
        self.assertTrue(shadow["current_serial_observation"]["reply_target_input_schema_ready"])
        self.assertEqual(shadow["current_serial_observation"]["reply_target_input_active_group_count"], 5)
        self.assertEqual(shadow["current_serial_observation"]["reply_target_input_shadow_only_group_count"], 4)
        self.assertIn("final_closing_move", shadow["ownership_contract"]["sop_chat_gate"]["must_not_own"])
        self.assertIn("customer_visible_text", shadow["ownership_contract"]["tool_planner"]["must_not_own"])
        self.assertIn("single_mainline_action", shadow["ownership_contract"]["reply"]["owns"])
        self.assertTrue(shadow["activation"]["ready_for_shadow_parallel_runner"])

    def test_blocked_read_executor_prevents_parallel_runner_activation(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(),
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={
                "schema_version": "read_only_tool_executor_shadow_v1",
                "mode": "dry_run_no_external_calls",
                "blocked": [{"tool": "create_order_plan"}],
            },
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("early_tool_executor_has_blocked_calls", shadow["activation"]["blockers"])

    def test_refactor_flag_blockers_prevent_shadow_runner_activation(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(),
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
            refactor_flags={
                "schema_version": "reply_chain_refactor_flags_v1",
                "mode": "parallel_runner_requested",
                "safe_for_shadow_observation": False,
                "activation_blockers": ["sop_chat_gate_v2_required"],
            },
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("flag:sop_chat_gate_v2_required", shadow["activation"]["blockers"])
        self.assertEqual(shadow["configuration"]["mode"], "parallel_runner_requested")

    def test_missing_shadow_inputs_are_explicit_blockers(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context={},
            gate_router_shadow={},
            tool_plan_preview={},
            read_only_tool_executor_shadow={},
            reply_chain_join_shadow={},
            reply_final_brain_handoff_shadow={},
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("missing_shared_reply_chain_shadow_context", shadow["activation"]["blockers"])
        self.assertIn("missing_reply_chain_authority_audit", shadow["activation"]["blockers"])
        self.assertIn("missing_gate_router_shadow", shadow["activation"]["blockers"])
        self.assertIn("missing_gate_commit_boundary_audit", shadow["activation"]["blockers"])
        self.assertIn("missing_tool_plan_preview", shadow["activation"]["blockers"])
        self.assertIn("missing_join_final_expression_boundary", shadow["activation"]["blockers"])
        self.assertIn("missing_reply_final_brain_handoff_shadow", shadow["activation"]["blockers"])

    def test_missing_reply_handoff_blocks_parallel_runner_activation(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(),
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow={},
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("missing_reply_final_brain_handoff_shadow", shadow["activation"]["blockers"])

    def test_missing_reply_handoff_readiness_audit_blocks_parallel_runner_activation(self) -> None:
        handoff = _reply_final_brain_handoff_shadow()
        handoff.pop("handoff_readiness_audit")
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(),
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=handoff,
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("missing_reply_handoff_readiness_audit", shadow["activation"]["blockers"])

    def test_reply_handoff_readiness_blockers_prevent_parallel_runner_activation(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(),
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(
                handoff_ready=False,
                handoff_blockers=["missing_complete_timed_chat_context"],
            ),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("reply_handoff:missing_complete_timed_chat_context", shadow["activation"]["blockers"])
        self.assertEqual(
            shadow["current_serial_observation"]["reply_handoff_blockers"],
            ["missing_complete_timed_chat_context"],
        )

    def test_missing_authority_audit_blocks_parallel_runner_activation(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context={"schema_version": "reply_chain_shadow_v1"},
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("missing_reply_chain_authority_audit", shadow["activation"]["blockers"])

    def test_missing_timeline_window_audit_blocks_parallel_runner_activation(self) -> None:
        context = _reply_chain_shadow_context()
        context["authority_audit"].pop("timeline_window_audit")
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=context,
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("missing_reply_chain_timeline_window_audit", shadow["activation"]["blockers"])

    def test_timeline_window_blockers_prevent_parallel_runner_activation(self) -> None:
        context = _reply_chain_shadow_context()
        context["authority_audit"]["timeline_window_audit"]["ready_for_authoritative_model_input"] = False
        context["authority_audit"]["timeline_window_audit"]["blockers"] = ["source_window_incomplete_under_limit"]
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=context,
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("timeline_window:source_window_incomplete_under_limit", shadow["activation"]["blockers"])
        self.assertEqual(
            shadow["current_serial_observation"]["shared_context_timeline_window_blockers"],
            ["source_window_incomplete_under_limit"],
        )

    def test_incomplete_timestamps_block_parallel_runner_activation(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(all_messages_have_sent_at=False),
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("incomplete_timestamped_conversation", shadow["activation"]["blockers"])

    def test_current_message_not_ready_blocks_parallel_runner_activation(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(
                current_message_ready=False,
                current_message_blockers=["current_message_not_last_in_timeline"],
            ),
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("current_message:current_message_not_last_in_timeline", shadow["activation"]["blockers"])
        self.assertEqual(
            shadow["current_serial_observation"]["shared_context_current_message_blockers"],
            ["current_message_not_last_in_timeline"],
        )

    def test_authoritative_fact_snapshot_errors_block_parallel_runner_activation(self) -> None:
        context = _reply_chain_shadow_context()
        context["authority_audit"]["fact_snapshot"]["sections_with_error"] = ["visible_store_scope"]
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=context,
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("authoritative_fact_snapshot_errors:1", shadow["activation"]["blockers"])
        self.assertEqual(
            shadow["current_serial_observation"]["shared_context_fact_sections_with_error"],
            ["visible_store_scope"],
        )

    def test_missing_gate_commit_boundary_blocks_parallel_runner_activation(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(),
            gate_router_shadow={
                "schema_version": "chat_gate_router_shadow_v1",
                "route_suggestion": "content_only_reply",
            },
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("missing_gate_commit_boundary_audit", shadow["activation"]["blockers"])

    def test_gate_commit_side_effects_block_parallel_runner_activation(self) -> None:
        gate_shadow = _gate_router_shadow()
        gate_shadow["commit_boundary"]["this_shadow_updates_send_once"] = True
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(),
            gate_router_shadow=gate_shadow,
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=_join_shadow(),
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn(
            "gate_shadow_commit_side_effect:this_shadow_updates_send_once",
            shadow["activation"]["blockers"],
        )

    def test_missing_join_final_expression_boundary_blocks_parallel_runner_activation(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(),
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow={"schema_version": "reply_chain_join_shadow_v1"},
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("missing_join_final_expression_boundary", shadow["activation"]["blockers"])

    def test_missing_direct_reply_guard_blocks_parallel_runner_activation(self) -> None:
        join_shadow = _join_shadow()
        join_shadow.pop("direct_reply_guard_audit")
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(),
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=join_shadow,
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("missing_direct_reply_guard_audit", shadow["activation"]["blockers"])

    def test_complex_join_route_must_keep_reply_as_final_owner(self) -> None:
        join_shadow = _join_shadow()
        join_shadow["final_expression_boundary"]["final_customer_message_owner"] = "validated_static_gate_candidate"
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context=_reply_chain_shadow_context(),
            gate_router_shadow=_gate_router_shadow(),
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow=join_shadow,
            reply_final_brain_handoff_shadow=_reply_final_brain_handoff_shadow(),
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("join_complex_turn_owner_not_reply", shadow["activation"]["blockers"])

    def test_parallel_shadow_is_not_consumed_by_current_model_payloads(self) -> None:
        state = {
            "normalized_content": "怎么预约",
            "conversation_history": ["用户: 怎么预约"],
            "parallel_reply_chain_shadow": {
                "schema_version": "parallel_reply_chain_shadow_v1",
                "ownership_contract": {
                    "sop_chat_gate": {"must_not_own": ["shadow-only-marker"]},
                },
            },
            "request_context": {},
        }

        planner_payload = _planner_payload_for_model(state)
        reply_payload = reply_user_payload_for_model(state)
        combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

        self.assertNotIn("parallel_reply_chain_shadow", planner_payload)
        self.assertNotIn("parallel_reply_chain_shadow", reply_payload)
        self.assertNotIn("shadow-only-marker", combined)


if __name__ == "__main__":
    unittest.main()
