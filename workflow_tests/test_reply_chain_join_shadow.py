from __future__ import annotations

import json
import unittest

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.reply_chain_join_shadow import reply_chain_join_shadow


class ReplyChainJoinShadowTests(unittest.TestCase):
    def test_direct_text_without_dynamic_facts_allows_direct_reply(self) -> None:
        shadow = reply_chain_join_shadow(
            gate_router_shadow={
                "route_suggestion": "direct_text",
                "selected_content": {"message_count": 1},
            },
            tool_plan_preview={"fact_requirement": "none"},
        )

        self.assertEqual(shadow["final_route"], "direct_reply")
        self.assertTrue(shadow["direct_reply_allowed"])
        self.assertEqual(
            shadow["final_expression_boundary"]["schema_version"],
            "reply_final_expression_boundary_v1",
        )
        self.assertFalse(shadow["final_expression_boundary"]["reply_required_for_complex_turn"])
        self.assertEqual(
            shadow["final_expression_boundary"]["final_customer_message_owner"],
            "validated_static_gate_candidate",
        )
        self.assertTrue(shadow["final_expression_boundary"]["direct_reply_exception"])
        self.assertEqual(
            shadow["final_expression_boundary"]["direct_reply_scope"],
            "static_candidate_only_no_dynamic_facts",
        )
        self.assertTrue(shadow["final_expression_boundary"]["direct_reply_requires_commit_validation"])
        self.assertFalse(shadow["final_expression_boundary"]["join_generates_customer_visible_text"])
        self.assertFalse(shadow["final_expression_boundary"]["join_decides_sales_psychology"])
        self.assertIn("direct_reply_requires_gate_direct_text_and_no_dynamic_facts", shadow["join_reasons"])

    def test_direct_text_with_read_tools_must_go_to_reply_with_content_and_tools(self) -> None:
        shadow = reply_chain_join_shadow(
            gate_router_shadow={
                "route_suggestion": "direct_text",
                "selected_content": {"message_count": 1},
            },
            tool_plan_preview={
                "fact_requirement": "required",
                "read_tool_calls": [{"tool": "customer_store_lookup"}],
            },
        )

        self.assertEqual(shadow["final_route"], "reply_with_content_and_tools")
        self.assertFalse(shadow["direct_reply_allowed"])
        self.assertTrue(shadow["final_expression_boundary"]["reply_required_for_complex_turn"])
        self.assertEqual(shadow["final_expression_boundary"]["final_customer_message_owner"], "reply")
        self.assertFalse(shadow["final_expression_boundary"]["direct_reply_exception"])

    def test_direct_text_without_static_candidate_must_go_to_reply(self) -> None:
        shadow = reply_chain_join_shadow(
            gate_router_shadow={"route_suggestion": "direct_text"},
            tool_plan_preview={"fact_requirement": "none"},
        )

        self.assertEqual(shadow["final_route"], "reply")
        self.assertFalse(shadow["direct_reply_allowed"])
        self.assertTrue(shadow["final_expression_boundary"]["reply_required_for_complex_turn"])
        self.assertEqual(shadow["final_expression_boundary"]["final_customer_message_owner"], "reply")
        self.assertIn("direct_text_missing_static_candidate_requires_reply", shadow["join_reasons"])

    def test_content_tools_and_no_reply_routes_are_distinct(self) -> None:
        content = reply_chain_join_shadow(
            gate_router_shadow={"route_suggestion": "content_only_reply", "selected_content": {"message_count": 1}},
            tool_plan_preview={"fact_requirement": "none"},
        )
        tools = reply_chain_join_shadow(
            gate_router_shadow={"route_suggestion": "tools_only"},
            tool_plan_preview={
                "fact_requirement": "required",
                "read_tool_calls": [{"tool": "kb_search"}],
            },
        )
        no_reply = reply_chain_join_shadow(
            gate_router_shadow={"route_suggestion": "no_reply"},
            tool_plan_preview={"fact_requirement": "none"},
        )

        self.assertEqual(content["final_route"], "reply_with_content")
        self.assertEqual(tools["final_route"], "reply_with_tools")
        self.assertEqual(no_reply["final_route"], "no_reply")
        self.assertEqual(content["final_expression_boundary"]["final_customer_message_owner"], "reply")
        self.assertEqual(tools["final_expression_boundary"]["final_customer_message_owner"], "reply")
        self.assertEqual(no_reply["final_expression_boundary"]["final_customer_message_owner"], "none")

    def test_unknown_tools_force_reply_review_not_direct_reply(self) -> None:
        shadow = reply_chain_join_shadow(
            gate_router_shadow={"route_suggestion": "direct_text", "selected_content": {"message_count": 1}},
            tool_plan_preview={
                "fact_requirement": "required",
                "unknown_tools": [{"tool": "new_tool"}],
            },
        )

        self.assertEqual(shadow["final_route"], "reply_with_content_and_tools")
        self.assertFalse(shadow["direct_reply_allowed"])
        self.assertIn("unknown_tools_require_review", shadow["join_reasons"])

    def test_join_shadow_is_not_consumed_by_current_model_payloads(self) -> None:
        state = {
            "normalized_content": "怎么预约",
            "conversation_history": ["用户: 怎么预约"],
            "reply_chain_join_shadow": {
                "schema_version": "reply_chain_join_shadow_v1",
                "join_reasons": ["shadow-only-join"],
            },
            "request_context": {},
        }

        planner_payload = _planner_payload_for_model(state)
        reply_payload = reply_user_payload_for_model(state)
        combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

        self.assertNotIn("reply_chain_join_shadow", planner_payload)
        self.assertNotIn("reply_chain_join_shadow", reply_payload)
        self.assertNotIn("shadow-only-join", combined)


if __name__ == "__main__":
    unittest.main()
