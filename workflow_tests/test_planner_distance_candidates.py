from __future__ import annotations

import unittest

from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2


class PlannerModelOwnershipTests(unittest.TestCase):
    def test_planner_keeps_model_selected_distance_candidates(self) -> None:
        plan = build_planner_plan_v2(
            {
                "normalized_content": "重庆巴南附近哪家店最近",
                "customer_store_knowledge": {
                    "stores": [
                        {"store_id": "189", "store_name": "重庆巴南店", "city": "重庆市", "district": "巴南区"},
                        {"store_id": "467", "store_name": "重庆百星渝中店", "city": "重庆市", "district": "渝中区"},
                    ]
                },
            },
            {
                "decision": "need_tools",
                "stage": "S2",
                "sub_rule_id": "S2_LOCATION_DETAIL",
                "reply_messages": [{"type": "text", "content": {"text": "我帮您核对一下附近门店。"}}],
                "tool_calls": [{"name": "distance_calculate", "origin": "重庆巴南", "candidate_store_ids": ["189"]}],
                "handoff": {"needed": False, "reason": ""},
            },
        )

        tool = plan["planner_tool_calls"][0]
        self.assertEqual(tool["name"], "distance_calculate")
        self.assertEqual(tool["candidate_store_ids"], ["189"])

    def test_planner_keeps_model_emitted_payment_collection(self) -> None:
        plan = build_planner_plan_v2(
            {"normalized_content": "我想报名"},
            {
                "decision": "direct_reply",
                "stage": "S3",
                "sub_rule_id": "S3_PAYMENT_COLLECTION",
                "reply_messages": [
                    {"type": "text", "content": {"text": "可以，我把10元预约入口发您。"}},
                    {"type": "payment_collection", "content": {"amount": 10, "remark": ""}},
                ],
                "tool_calls": [],
            },
        )

        self.assertEqual([item["type"] for item in plan["planner_reply_messages"]], ["text", "payment_collection"])

    def test_planner_does_not_auto_add_payment_collection(self) -> None:
        plan = build_planner_plan_v2(
            {"normalized_content": "我想报名"},
            {
                "decision": "direct_reply",
                "stage": "S3",
                "sub_rule_id": "S3_PAYMENT_COLLECTION",
                "reply_messages": [{"type": "text", "content": {"text": "可以，我先帮您登记意向。"}}],
                "tool_calls": [],
            },
        )

        self.assertEqual([item["type"] for item in plan["planner_reply_messages"]], ["text"])

    def test_low_information_opening_suppresses_old_profile_for_planner(self) -> None:
        payload = _planner_payload_for_model(
            {
                "normalized_content": "你好",
                "customer_profile": {"summary": "旧画像"},
                "history_events": [{"event_type": "old"}],
                "conversation_history": ["用户: 之前的历史"],
            }
        )

        self.assertNotIn("customer_profile", payload)
        self.assertNotIn("history_events", payload)
        self.assertNotIn("conversation_history", payload)


if __name__ == "__main__":
    unittest.main()
