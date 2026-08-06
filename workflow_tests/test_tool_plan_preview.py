from __future__ import annotations

import unittest

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

        self.assertEqual(preview["fact_requirement"], "required")
        self.assertEqual([tool["name"] for tool in preview["read_tool_calls"]], ["customer_store_lookup", "kb_search"])
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

        self.assertEqual(preview["fact_requirement"], "write_deferred")
        self.assertEqual(
            [tool["name"] for tool in preview["deferred_write_proposals"]],
            ["create_work_order", "add_customer_mobile"],
        )
        self.assertNotIn("read_tool_calls", preview)

    def test_unknown_tools_are_visible_for_review(self) -> None:
        preview = tool_plan_preview_from_planner_output(
            {"required_tools": [{"name": "new_tool", "foo": "bar"}]}
        )

        self.assertEqual(preview["fact_requirement"], "write_deferred")
        self.assertEqual(preview["unknown_tools"][0]["name"], "new_tool")


if __name__ == "__main__":
    unittest.main()
