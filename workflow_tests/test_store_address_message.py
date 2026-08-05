from __future__ import annotations

import unittest

from app.graph.nodes.reply_validation import validate_reply_consistency, validated_model_messages
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.schemas import ChatResponse, ReplyMessage
from app.services.workflow_compat import workflow_response_from_chat


class StoreAddressMessageTests(unittest.TestCase):
    def test_validation_keeps_store_address_message(self) -> None:
        messages = validated_model_messages(
            {
                "reply_messages": [
                    {"type": "text", "content": {"text": "地址我发您，点开可以导航。"}},
                    {"type": "store_address", "content": {"store_id": "467"}},
                ]
            }
        )

        self.assertEqual([item["type"] for item in messages], ["text", "store_address"])
        self.assertEqual(messages[1]["content"], {"store_id": "467"})

    def test_workflow_compat_outputs_store_address_payload(self) -> None:
        response = ChatResponse(
            request_id="req-store-address",
            reply_messages=[
                ReplyMessage(type="text", order=1, content={"text": "地址我发您。"}),
                ReplyMessage(type="store_address", order=2, content={"store_id": "467"}),
            ],
            intent="store_inquiry",
            scene="S2_store_address",
            subflow="direct_reply",
            meta={},
        )

        payload = workflow_response_from_chat(response)
        reply_messages = payload["data"]["reply_messages"]

        self.assertEqual([item["type"] for item in reply_messages], ["text", "store_address"])
        self.assertEqual(reply_messages[1]["content"], {"store_id": "467"})

    def test_planner_requires_lookup_for_model_emitted_store_address_card(self) -> None:
        plan = build_planner_plan_v2(
            {"normalized_content": "渝中这边"},
            {
                "decision": "direct_reply",
                "stage": "S2",
                "sub_rule_id": "S2_LOCATION_DETAIL",
                "reply_messages": [
                    {"type": "text", "content": {"text": "重庆百星渝中店地址我发您。"}},
                    {"type": "store_address", "content": {"store_id": "467"}},
                ],
                "tool_calls": [],
            },
        )

        self.assertEqual(plan["planner_decision"], "direct_reply")
        self.assertEqual(plan["planner_tool_calls"], [])
        self.assertTrue(
            any(
                item.get("missing") == "store_detail_tool_required"
                for item in plan["tool_policy_violations"]
            )
        )

    def test_planner_keeps_requested_district_store_cards_without_lookup(self) -> None:
        state = {
            "normalized_content": "门店位置：双流人民广场",
            "store_scope_summary": {
                "relevant_regions": [
                    {
                        "city": "成都市",
                        "requested_district_stores": [
                            {"store_id": "280", "store_name": "成都天府新区店", "district": "双流区"},
                            {"store_id": "379", "store_name": "成都双流店", "district": "双流区"},
                            {"store_id": "522", "store_name": "成都双流高新店", "district": "双流区"},
                        ],
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
                "reply_messages": [
                    {"type": "text", "content": {"text": "双流这边这几家门店我都发您看下。"}},
                    {"type": "store_address", "content": {"store_id": "280"}},
                    {"type": "store_address", "content": {"store_id": "379"}},
                    {"type": "store_address", "content": {"store_id": "522"}},
                    {"type": "text", "content": {"text": "顺便问下，您脸上的斑大概有多久了？"}},
                ],
                "tool_calls": [],
            },
        )

        self.assertEqual(plan["planner_decision"], "direct_reply")
        self.assertEqual(plan["planner_tool_calls"], [])
        self.assertEqual(
            [item["type"] for item in plan["planner_reply_messages"]],
            ["text", "store_address", "store_address", "store_address", "text"],
        )

    def test_planner_rejects_repeating_history_store_card_for_unrelated_short_reply(self) -> None:
        state = {
            "normalized_content": "好的",
            "conversation_history": [
                "用户: 我在浙江省温州市龙湾区",
                "小贝: 龙湾区这边我记下了。",
            ],
            "customer_store_knowledge": {
                "stores": [
                    {
                        "store_id": "701",
                        "store_name": "温州龙湾店",
                        "province": "浙江省",
                        "city": "温州市",
                        "district": "龙湾区",
                    }
                ]
            },
        }
        plan = build_planner_plan_v2(
            state,
            {
                "decision": "direct_reply",
                "stage": "S2",
                "sub_rule_id": "S2_STORE_MATCH",
                "reply_messages": [
                    {"type": "text", "content": {"text": "温州龙湾店的位置发您。"}},
                    {"type": "store_address", "content": {"store_id": "701"}},
                ],
                "tool_calls": [],
            },
        )

        self.assertTrue(
            any(
                item.get("missing") == "store_card_requires_current_turn_support"
                for item in plan["tool_policy_violations"]
            )
        )

    def test_reply_validation_allows_requested_district_cards_with_followup_text(self) -> None:
        messages = [
            {"type": "text", "order": 1, "content": "双流这边这几家门店我都发您看下。"},
            {"type": "store_address", "order": 2, "content": {"store_id": "280"}},
            {"type": "store_address", "order": 3, "content": {"store_id": "379"}},
            {"type": "store_address", "order": 4, "content": {"store_id": "522"}},
            {"type": "text", "order": 5, "content": "顺便问下，您脸上的斑大概有多久了？"},
        ]
        state = {
            "store_scope_summary": {
                "relevant_regions": [
                    {
                        "requested_district_stores": [
                            {"store_id": "280"},
                            {"store_id": "379"},
                            {"store_id": "522"},
                        ]
                    }
                ]
            }
        }

        validate_reply_consistency(messages, state)


if __name__ == "__main__":
    unittest.main()
