from __future__ import annotations

import json
import unittest

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.parallel_reply_chain_shadow import parallel_reply_chain_shadow


class ParallelReplyChainShadowTests(unittest.TestCase):
    def test_parallel_contract_keeps_gate_planner_and_reply_ownership_separate(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context={"schema_version": "reply_chain_shadow_v1"},
            gate_router_shadow={
                "schema_version": "chat_gate_router_shadow_v1",
                "route_suggestion": "content_only_reply",
            },
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
            reply_chain_join_shadow={
                "schema_version": "reply_chain_join_shadow_v1",
                "final_route": "reply_with_content",
                "direct_reply_allowed": False,
            },
        )

        self.assertEqual(shadow["schema_version"], "parallel_reply_chain_shadow_v1")
        self.assertEqual(shadow["mode"], "shadow_no_parallel_execution")
        self.assertEqual(shadow["target_topology"]["parallel_branches"], ["sop_chat_gate", "tool_planner"])
        self.assertEqual(shadow["target_topology"]["final_expression_owner"], "reply")
        self.assertEqual(shadow["current_serial_observation"]["tool_planner_legacy_residue_count"], 2)
        self.assertFalse(shadow["current_serial_observation"]["tool_planner_only_ready"])
        self.assertIn("final_closing_move", shadow["ownership_contract"]["sop_chat_gate"]["must_not_own"])
        self.assertIn("customer_visible_text", shadow["ownership_contract"]["tool_planner"]["must_not_own"])
        self.assertIn("single_mainline_action", shadow["ownership_contract"]["reply"]["owns"])
        self.assertTrue(shadow["activation"]["ready_for_shadow_parallel_runner"])

    def test_blocked_read_executor_prevents_parallel_runner_activation(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context={"schema_version": "reply_chain_shadow_v1"},
            gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={
                "schema_version": "read_only_tool_executor_shadow_v1",
                "mode": "dry_run_no_external_calls",
                "blocked": [{"tool": "create_order_plan"}],
            },
            reply_chain_join_shadow={"schema_version": "reply_chain_join_shadow_v1"},
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("early_tool_executor_has_blocked_calls", shadow["activation"]["blockers"])

    def test_refactor_flag_blockers_prevent_shadow_runner_activation(self) -> None:
        shadow = parallel_reply_chain_shadow(
            reply_chain_shadow_context={"schema_version": "reply_chain_shadow_v1"},
            gate_router_shadow={"schema_version": "chat_gate_router_shadow_v1"},
            tool_plan_preview={"schema_version": "tool_plan_preview_v2"},
            read_only_tool_executor_shadow={"schema_version": "read_only_tool_executor_shadow_v1"},
            reply_chain_join_shadow={"schema_version": "reply_chain_join_shadow_v1"},
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
        )

        self.assertFalse(shadow["activation"]["ready_for_shadow_parallel_runner"])
        self.assertIn("missing_shared_reply_chain_shadow_context", shadow["activation"]["blockers"])
        self.assertIn("missing_gate_router_shadow", shadow["activation"]["blockers"])
        self.assertIn("missing_tool_plan_preview", shadow["activation"]["blockers"])

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
