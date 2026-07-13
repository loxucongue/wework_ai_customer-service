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

    def test_planner_rejects_nearby_distance_without_city_or_region_query(self) -> None:
        plan = build_planner_plan_v2(
            {
                "normalized_content": "机场附近哪家近",
                "customer_store_knowledge": {
                    "stores": [
                        {"store_id": "227", "store_name": "厦门思明店", "province": "福建省", "city": "厦门市", "district": "思明区"},
                        {"store_id": "467", "store_name": "重庆渝中店", "province": "重庆市", "city": "重庆市", "district": "渝中区"},
                    ]
                },
            },
            {
                "decision": "need_tools",
                "stage": "S2",
                "sub_rule_id": "S2_LOCATION_DETAIL",
                "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
                "tool_calls": [
                    {"name": "customer_store_lookup", "query": "机场附近", "purpose": "nearby_candidates"},
                    {"name": "distance_calculate", "origin": "机场附近", "candidate_source": "customer_store_lookup"},
                ],
                "handoff": {"needed": False, "reason": ""},
            },
        )

        self.assertEqual(
            [item["missing"] for item in plan["tool_policy_violations"]],
            ["location_query_missing_city_or_region", "location_query_missing_city_or_region"],
        )

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
        self.assertEqual(plan["planner_reply_messages"], [{"type": "text", "order": 1, "content": {"text": "稍等一下哈"}}])
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

    def test_low_information_opening_keeps_context_for_planner(self) -> None:
        payload = _planner_payload_for_model(
            {
                "normalized_content": "你好",
                "customer_profile": {"summary": "旧画像"},
                "history_events": [{"event_type": "old"}],
                "conversation_history": ["用户: 之前的历史"],
            }
        )

        self.assertNotIn("customer_profile", payload)
        self.assertEqual(payload["history_events"], [{"event_type": "old"}])
        self.assertEqual(payload["conversation_history"], ["用户: 之前的历史"])

    def test_planner_store_scope_payload_contains_city_district_counts_and_relevant_stores(self) -> None:
        payload = _planner_payload_for_model(
            {
                "normalized_content": "不是说集美就有吗，我看广告",
                "customer_basic_info": {"city": "厦门市", "area_or_landmark": "集美区"},
                "customer_store_knowledge": {
                    "store_count": 3,
                    "stores": [
                        {"store_id": "227", "store_name": "厦门湖里店", "province": "福建省", "city": "厦门市", "district": "湖里区"},
                        {"store_id": "386", "store_name": "厦门思明店", "province": "福建省", "city": "厦门市", "district": "思明区"},
                        {"store_id": "467", "store_name": "重庆渝中店", "province": "重庆市", "city": "重庆市", "district": "渝中区"},
                    ],
                    "missing_snapshot_store_ids": [],
                },
            }
        )

        self.assertNotIn("customer_store_knowledge", payload)
        summary = payload["store_scope_summary"]
        self.assertEqual(summary["store_count"], 3)
        self.assertEqual(summary["province_counts"][0], {"province": "福建省", "store_count": 2})
        self.assertIn({"province": "福建省", "city": "厦门市", "store_count": 2}, summary["city_counts"])
        self.assertIn(
            {"province": "福建省", "city": "厦门市", "district": "湖里区", "store_count": 1},
            summary["district_counts"],
        )
        relevant = summary["relevant_regions"][0]
        self.assertEqual(relevant["city"], "厦门市")
        self.assertEqual(relevant["store_count"], 2)
        self.assertEqual(relevant["exact_area_store_count"], 0)
        self.assertEqual([item["store_id"] for item in relevant["stores"]], ["227", "386"])
        self.assertNotIn("stores", summary)

    def test_scope_backed_store_card_stays_direct_reply_without_forced_lookup(self) -> None:
        state = {
            "normalized_content": "不是说集美就有吗，我看广告",
            "customer_basic_info": {
                "city": "厦门市",
                "area_or_landmark": "集美区",
                "preferred_store_id": "227",
                "preferred_store_name": "厦门百星湖里店",
            },
            "customer_store_knowledge": {
                "stores": [
                    {
                        "store_id": "227",
                        "store_name": "厦门百星湖里店",
                        "province": "福建省",
                        "city": "厦门市",
                        "district": "湖里区",
                    }
                ]
            },
        }
        plan = build_planner_plan_v2(
            state,
            {
                "decision": "direct_reply",
                "stage": "S2",
                "sub_rule_id": "S2_LOCATION_DETAIL",
                "conversion_stage": "store_match",
                "customer_type": "distance",
                "main_blocker": "distance",
                "next_step": "confirm_store",
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "这个是平台同城展示定位，不代表每个区都有门店哈。厦门这边有真实门店，活动和到店检测服务都是一样的，我先把湖里这家发您看下顺不顺路。"}},
                    {"type": "store_address", "order": 2, "content": {"store_id": "227"}},
                ],
                "tool_calls": [],
                "handoff": {"needed": False, "reason": ""},
            },
        )

        self.assertEqual(plan["planner_decision"], "direct_reply")
        self.assertEqual(plan["planner_tool_calls"], [])
        self.assertEqual([item["type"] for item in plan["planner_reply_messages"]], ["text", "store_address"])
        self.assertFalse(plan["tool_policy_violations"])

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
