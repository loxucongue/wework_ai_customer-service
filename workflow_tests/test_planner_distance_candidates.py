from __future__ import annotations

import unittest

from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.graph.planner.runtime_plan import planner_public_route


class PlannerModelOwnershipTests(unittest.TestCase):
    def test_planner_keeps_lookup_backed_distance_candidate_source(self) -> None:
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
                "tool_calls": [
                    {"name": "customer_store_lookup", "query": "重庆巴南", "purpose": "nearby_candidates"},
                    {"name": "distance_calculate", "origin": "重庆巴南", "candidate_source": "customer_store_lookup"},
                ],
                "handoff": {"needed": False, "reason": ""},
            },
        )

        self.assertEqual([tool["name"] for tool in plan["planner_tool_calls"]], ["customer_store_lookup", "distance_calculate"])
        self.assertEqual(plan["planner_tool_calls"][1]["candidate_source"], "customer_store_lookup")

    def test_planner_store_address_without_tool_is_grounded_by_lookup(self) -> None:
        plan = build_planner_plan_v2(
            {
                "normalized_content": "地图发一个给我",
                "conversation_history": [
                    "用户: 我在南昌市",
                    "小贝: 南昌市有3家门店，分别是东湖店、红谷滩店和高新店。",
                    "用户: 高新店具体位置呢",
                    "小贝: 南昌高新店地址是江西省南昌市青山湖区解放东路 2226号。",
                ],
                "customer_store_knowledge": {
                    "stores": [
                        {"store_id": "189", "store_name": "重庆巴南店", "city": "重庆市", "district": "巴南区"},
                        {"store_id": "221", "store_name": "南昌高新店", "city": "南昌市", "district": "青山湖区"},
                    ]
                },
            },
            {
                "decision": "direct_reply",
                "stage": "S2",
                "sub_rule_id": "S2_ADDRESS_DETAIL",
                "reply_messages": [{"type": "store_address", "content": {"store_id": "189"}}],
                "tool_calls": [],
                "handoff": {"needed": False, "reason": ""},
            },
        )

        self.assertEqual(plan["planner_decision"], "need_tools")
        self.assertEqual(plan["planner_reply_messages"], [{"type": "text", "order": 1, "content": {"text": "好，我帮您看一下"}}])
        self.assertEqual(plan["planner_tool_calls"], [{"name": "customer_store_lookup", "purpose": "detail", "query": "南昌高新店"}])

    def test_planner_preserves_conversion_psychology_fields(self) -> None:
        plan = build_planner_plan_v2(
            {"normalized_content": "多少钱"},
            {
                "decision": "direct_reply",
                "stage": "S3",
                "sub_rule_id": "S3_PRICE",
                "conversion_stage": "objection_resolution",
                "customer_type": "price",
                "main_blocker": "price",
                "next_step": "solve_blocker",
                "reply_messages": [{"type": "text", "content": {"text": "现在周年庆活动价是268。"}}],
                "tool_calls": [],
                "handoff": {"needed": False, "reason": ""},
            },
        )

        self.assertEqual(plan["conversion_stage"], "objection_resolution")
        self.assertEqual(plan["customer_type"], "price")
        self.assertEqual(plan["main_blocker"], "price")
        self.assertEqual(plan["next_step"], "solve_blocker")
        route = planner_public_route(plan)
        self.assertEqual(route["conversion_stage"], "objection_resolution")
        self.assertEqual(route["customer_type"], "price")

    def test_invalid_conversion_fields_use_neutral_defaults(self) -> None:
        plan = build_planner_plan_v2(
            {"normalized_content": "你好"},
            {
                "decision": "direct_reply",
                "stage": "S1",
                "sub_rule_id": "S1_GREETING",
                "conversion_stage": "bad_stage",
                "customer_type": "bad_type",
                "main_blocker": "bad_blocker",
                "next_step": "bad_step",
                "reply_messages": [{"type": "text", "content": {"text": "您好。"}}],
                "tool_calls": [],
            },
        )

        self.assertEqual(plan["conversion_stage"], "")
        self.assertEqual(plan["customer_type"], "unknown")
        self.assertEqual(plan["main_blocker"], "none")
        self.assertEqual(plan["next_step"], "no_action")

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

    def test_planner_store_scope_payload_only_contains_province_counts(self) -> None:
        payload = _planner_payload_for_model(
            {
                "normalized_content": "厦门机场附近哪家近",
                "customer_store_knowledge": {
                    "store_count": 2,
                    "stores": [
                        {"store_id": "227", "store_name": "厦门湖里店", "province": "福建省", "city": "厦门市", "district": "湖里区"},
                        {"store_id": "467", "store_name": "重庆渝中店", "province": "重庆市", "city": "重庆市", "district": "渝中区"},
                    ],
                    "missing_snapshot_store_ids": [],
                },
            }
        )

        self.assertNotIn("customer_store_knowledge", payload)
        summary = payload["store_scope_summary"]
        self.assertEqual(summary["store_count"], 2)
        self.assertEqual(summary["province_counts"], [{"province": "福建省", "store_count": 1}, {"province": "重庆市", "store_count": 1}])
        self.assertNotIn("regions", summary)
        self.assertNotIn("stores", summary)

    def test_reply_payload_includes_conversion_psychology_fields(self) -> None:
        payload = reply_user_payload_for_model(
            {
                "content": "多少钱",
                "normalized_content": "多少钱",
                "planner_decision": "direct_reply",
                "planner_stage": "S3",
                "planner_sub_rule_id": "S3_PRICE",
                "conversion_stage": "objection_resolution",
                "customer_type": "price",
                "main_blocker": "price",
                "next_step": "solve_blocker",
                "fact_envelope": {},
            }
        )

        self.assertEqual(payload["conversion_stage"], "objection_resolution")
        self.assertEqual(payload["customer_type"], "price")
        self.assertEqual(payload["main_blocker"], "price")
        self.assertEqual(payload["next_step"], "solve_blocker")


if __name__ == "__main__":
    unittest.main()
