from __future__ import annotations

import unittest

from app.services.tool_registry import read_only_tool_contract, tool_execution_class


class ToolRegistryTests(unittest.TestCase):
    def test_known_read_only_tools_are_classified_read_only(self) -> None:
        for name in [
            "appointment_record_query",
            "available_time",
            "customer_store_lookup",
            "distance_calculate",
            "kb_search",
            "professional_assist",
        ]:
            self.assertEqual(tool_execution_class(name), "read_only")

    def test_write_tools_are_deferred(self) -> None:
        for name in ["add_customer_mobile", "check_customer", "create_order_plan", "create_work_order"]:
            self.assertEqual(tool_execution_class(name), "deferred_write")

    def test_no_tool_and_unknown_are_distinct(self) -> None:
        self.assertEqual(tool_execution_class("no_tool"), "no_tool")
        self.assertEqual(tool_execution_class(""), "no_tool")
        self.assertEqual(tool_execution_class("new_future_tool"), "unknown")

    def test_read_only_contract_returns_copy(self) -> None:
        contract = read_only_tool_contract("customer_store_lookup")
        contract["required_fact_fields"].append("mutated")

        self.assertNotIn("mutated", read_only_tool_contract("customer_store_lookup")["required_fact_fields"])


if __name__ == "__main__":
    unittest.main()
