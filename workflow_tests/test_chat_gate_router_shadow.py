from __future__ import annotations

import json
import unittest

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.chat_gate_router_shadow import chat_gate_router_shadow_from_result


class ChatGateRouterShadowTests(unittest.TestCase):
    def test_sop_only_maps_to_direct_text_candidate(self) -> None:
        shadow = chat_gate_router_shadow_from_result(
            {
                "mode": "sop_only",
                "coverage": "exact",
                "send_sop": True,
                "need_ai_reply": False,
                "sop_pack_id": "s10_activity_intro",
                "reply_messages": [{"type": "text", "content": {"text": "activity"}}],
            }
        )

        self.assertEqual(shadow["route_suggestion"], "direct_text")
        self.assertEqual(shadow["selected_content"]["sop_pack_ids"], ["s10_activity_intro"])
        self.assertEqual(shadow["selected_content"]["usage"], "direct")
        self.assertEqual(shadow["dynamic_fact_expectation"]["requirement"], "none")
        self.assertEqual(len(shadow["direct_reply_candidate"]), 1)

    def test_ai_then_sop_maps_to_content_only_reply(self) -> None:
        shadow = chat_gate_router_shadow_from_result(
            {
                "mode": "ai_then_sop",
                "coverage": "partial",
                "send_sop": True,
                "need_ai_reply": True,
                "priority_question_id": "one_session_effect",
                "sop_pack_id": "s10_need_and_case",
                "reply_messages": [{"type": "image", "content": {"url": "https://example.invalid/a.jpg"}}],
            }
        )

        self.assertEqual(shadow["route_suggestion"], "content_only_reply")
        self.assertEqual(shadow["selected_content"]["precision_qa_ids"], ["one_session_effect"])
        self.assertEqual(shadow["selected_content"]["sop_pack_ids"], ["s10_need_and_case"])
        self.assertNotIn("direct_reply_candidate", shadow)

    def test_required_tool_maps_to_tools_or_content_and_tools(self) -> None:
        tool_only = chat_gate_router_shadow_from_result(
            {
                "mode": "ai_only",
                "coverage": "none",
                "send_sop": False,
                "need_ai_reply": True,
                "active_task": {
                    "type": "location_confirmation",
                    "query": "洪湖市",
                    "required_tool": "customer_store_lookup",
                    "customer_evidence_ref": "msg_3",
                },
            }
        )
        content_and_tools = chat_gate_router_shadow_from_result(
            {
                "mode": "ai_then_sop",
                "send_sop": True,
                "need_ai_reply": True,
                "sop_pack_id": "s10_need_and_case",
                "active_task": {"type": "store_lookup", "required_tool": "customer_store_lookup"},
            }
        )

        self.assertEqual(tool_only["route_suggestion"], "tools_only")
        self.assertEqual(tool_only["dynamic_fact_expectation"]["capability_classes"], ["customer_store_lookup"])
        self.assertEqual(tool_only["current_question"]["evidence_refs"], ["msg_3"])
        self.assertEqual(content_and_tools["route_suggestion"], "content_and_tools")

    def test_terminal_no_reply_is_preserved(self) -> None:
        shadow = chat_gate_router_shadow_from_result(
            {
                "mode": "ignored_platform_auto_message",
                "send_sop": False,
                "need_ai_reply": False,
            }
        )

        self.assertEqual(shadow["route_suggestion"], "no_reply")
        self.assertFalse(shadow["current_question"]["must_answer"])

    def test_shadow_router_is_not_consumed_by_current_model_payloads(self) -> None:
        state = {
            "normalized_content": "门店在哪里",
            "conversation_history": ["用户: 门店在哪里"],
            "sop_gate_router_shadow": {
                "schema_version": "chat_gate_router_shadow_v1",
                "handoff_notes": ["shadow-only-router"],
            },
            "request_context": {},
        }

        planner_payload = _planner_payload_for_model(state)
        reply_payload = reply_user_payload_for_model(state)
        combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

        self.assertNotIn("sop_gate_router_shadow", planner_payload)
        self.assertNotIn("sop_gate_router_shadow", reply_payload)
        self.assertNotIn("shadow-only-router", combined)


if __name__ == "__main__":
    unittest.main()
