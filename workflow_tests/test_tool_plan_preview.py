from __future__ import annotations

import json
import unittest

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.tool_plan_preview import tool_plan_preview_from_planner_output


class ToolPlanPreviewTests(unittest.TestCase):
    def test_read_only_tools_make_fact_requirement_required(self) -> None:
        preview = tool_plan_preview_from_planner_output(
            {
                "planner_tool_calls": [
                    {
                        "name": "customer_store_lookup",
                        "query": "洪湖市",
                        "purpose": "lookup visible stores",
                    },
                    {"name": "kb_search", "query": "case_studies"},
                ]
            }
        )

        self.assertEqual(preview["schema_version"], "tool_plan_preview_v2")
        self.assertEqual(preview["fact_requirement"], "required")
        self.assertEqual([tool["name"] for tool in preview["read_tool_calls"]], ["customer_store_lookup", "kb_search"])
        self.assertEqual([tool["tool"] for tool in preview["read_tool_calls"]], ["customer_store_lookup", "kb_search"])
        self.assertIn("visible_store_candidates", preview["required_fact_fields"])
        self.assertIn("knowledge_facts", preview["required_fact_fields"])
        self.assertFalse(preview["customer_question_if_incomplete"]["required"])
        self.assertNotIn("deferred_write_proposals", preview)

    def test_no_tool_has_no_fact_requirement(self) -> None:
        preview = tool_plan_preview_from_planner_output(
            {"required_tools": [{"name": "no_tool", "purpose": "direct reply"}]}
        )

        self.assertEqual(preview["fact_requirement"], "none")
        self.assertNotIn("read_tool_calls", preview)

    def test_write_tools_are_deferred_not_read_calls(self) -> None:
        preview = tool_plan_preview_from_planner_output(
            {
                "required_tools": [
                    {"name": "create_work_order", "store_id": "241"},
                    {"name": "add_customer_mobile", "mobile": "masked"},
                ]
            }
        )

        self.assertEqual(preview["fact_requirement"], "none")
        self.assertEqual(
            [tool["name"] for tool in preview["deferred_write_proposals"]],
            ["create_work_order", "add_customer_mobile"],
        )
        self.assertEqual(
            {tool["execution"] for tool in preview["deferred_write_proposals"]},
            {"deferred_write_only"},
        )
        self.assertNotIn("read_tool_calls", preview)

    def test_unknown_tools_are_visible_for_review(self) -> None:
        preview = tool_plan_preview_from_planner_output(
            {"required_tools": [{"name": "new_tool", "foo": "bar"}]}
        )

        self.assertEqual(preview["fact_requirement"], "required")
        self.assertEqual(preview["unknown_tools"][0]["name"], "new_tool")

    def test_tool_plan_preview_is_not_consumed_by_current_model_payloads(self) -> None:
        state = {
            "normalized_content": "门店在哪里",
            "conversation_history": ["用户: 门店在哪里"],
            "tool_plan_preview": {
                "schema_version": "tool_plan_preview_v2",
                "read_tool_calls": [{"purpose": "shadow-only-tool-plan"}],
            },
            "request_context": {},
        }

        planner_payload = _planner_payload_for_model(state)
        reply_payload = reply_user_payload_for_model(state)
        combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

        self.assertNotIn("tool_plan_preview", planner_payload)
        self.assertNotIn("tool_plan_preview", reply_payload)
        self.assertNotIn("shadow-only-tool-plan", combined)


if __name__ == "__main__":
    unittest.main()
