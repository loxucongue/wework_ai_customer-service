from __future__ import annotations

import json
import unittest

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.read_only_tool_executor_shadow import read_only_tool_executor_shadow_from_plan


class ReadOnlyToolExecutorShadowTests(unittest.TestCase):
    def test_read_only_calls_are_dry_run_only(self) -> None:
        shadow = read_only_tool_executor_shadow_from_plan(
            {
                "read_tool_calls": [
                    {
                        "call_id": "customer_store_lookup_1",
                        "tool": "customer_store_lookup",
                        "arguments": {"query": "洪湖市"},
                        "required_fact_fields": ["visible_store_candidates"],
                    }
                ]
            }
        )

        self.assertEqual(shadow["mode"], "dry_run_no_external_calls")
        self.assertEqual(shadow["would_execute"][0]["status"], "would_execute_read_only")
        self.assertEqual(shadow["would_execute"][0]["tool"], "customer_store_lookup")
        self.assertEqual(shadow["summary"]["would_execute_count"], 1)
        self.assertTrue(shadow["summary"]["safe_to_enable_early_execution"])
        self.assertEqual(shadow["dependency_audit"]["schema_version"], "read_only_tool_dependency_audit_v1")
        self.assertTrue(shadow["dependency_audit"]["ready_for_early_execution_ordering"])

    def test_deferred_writes_and_unknown_tools_are_blocked(self) -> None:
        shadow = read_only_tool_executor_shadow_from_plan(
            {
                "read_tool_calls": [{"tool": "kb_search"}],
                "deferred_write_proposals": [{"tool": "create_work_order", "arguments": {"store_id": "241"}}],
                "unknown_tools": [{"tool": "future_tool"}],
            }
        )

        self.assertEqual(shadow["summary"]["would_execute_count"], 1)
        self.assertEqual(shadow["summary"]["blocked_count"], 2)
        self.assertFalse(shadow["summary"]["safe_to_enable_early_execution"])
        self.assertEqual(
            {item["reason"] for item in shadow["blocked"]},
            {"write_tool_deferred_until_commit", "unknown_tool_not_allowed_in_early_executor"},
        )

    def test_misclassified_read_call_is_blocked(self) -> None:
        shadow = read_only_tool_executor_shadow_from_plan(
            {"read_tool_calls": [{"tool": "create_order_plan", "arguments": {"date": "2026-08-06"}}]}
        )

        self.assertNotIn("would_execute", shadow)
        self.assertEqual(shadow["blocked"][0]["reason"], "tool_class_mismatch:deferred_write")
        self.assertFalse(shadow["summary"]["safe_to_enable_early_execution"])

    def test_read_only_dependencies_must_reference_existing_read_call_ids(self) -> None:
        shadow = read_only_tool_executor_shadow_from_plan(
            {
                "read_tool_calls": [
                    {"call_id": "lookup_store", "tool": "customer_store_lookup", "arguments": {"query": "洪湖市"}},
                    {
                        "call_id": "rank_distance",
                        "tool": "distance_calculate",
                        "arguments": {"origin": "洪湖市"},
                        "depends_on": ["lookup_store", "missing_case"],
                    },
                ]
            }
        )

        self.assertEqual(shadow["summary"]["would_execute_count"], 1)
        self.assertEqual(shadow["would_execute"][0]["call_id"], "lookup_store")
        self.assertEqual(shadow["blocked"][0]["call_id"], "rank_distance")
        self.assertEqual(shadow["blocked"][0]["reason"], "missing_read_tool_dependency")
        self.assertEqual(shadow["blocked"][0]["arguments"]["missing_dependencies"], ["missing_case"])
        self.assertFalse(shadow["dependency_audit"]["ready_for_early_execution_ordering"])
        self.assertIn("missing_read_tool_dependency", shadow["dependency_audit"]["blockers"])
        self.assertFalse(shadow["summary"]["safe_to_enable_early_execution"])

    def test_duplicate_read_call_ids_block_early_execution(self) -> None:
        shadow = read_only_tool_executor_shadow_from_plan(
            {
                "read_tool_calls": [
                    {"call_id": "dup", "tool": "customer_store_lookup", "arguments": {"query": "洪湖市"}},
                    {"call_id": "dup", "tool": "kb_search", "arguments": {"query": "case_studies"}},
                ]
            }
        )

        self.assertNotIn("would_execute", shadow)
        self.assertEqual(shadow["summary"]["blocked_count"], 2)
        self.assertEqual(
            {item["reason"] for item in shadow["blocked"]},
            {"duplicate_read_tool_call_id"},
        )
        self.assertFalse(shadow["dependency_audit"]["ready_for_early_execution_ordering"])

    def test_executor_shadow_is_not_consumed_by_current_model_payloads(self) -> None:
        state = {
            "normalized_content": "洪湖市有门店吗",
            "conversation_history": ["用户: 洪湖市有门店吗"],
            "read_only_tool_executor_shadow": {
                "schema_version": "read_only_tool_executor_shadow_v1",
                "would_execute": [{"purpose": "shadow-only-executor"}],
            },
            "request_context": {},
        }

        planner_payload = _planner_payload_for_model(state)
        reply_payload = reply_user_payload_for_model(state)
        combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

        self.assertNotIn("read_only_tool_executor_shadow", planner_payload)
        self.assertNotIn("read_only_tool_executor_shadow", reply_payload)
        self.assertNotIn("shadow-only-executor", combined)


if __name__ == "__main__":
    unittest.main()
