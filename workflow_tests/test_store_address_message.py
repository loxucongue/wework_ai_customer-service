from __future__ import annotations

import unittest

from app.graph.message_cards import append_store_address_card
from app.graph.nodes.reply_validation import validated_model_messages
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

    def test_reply_node_appends_store_address_for_dynamic_scope_match(self) -> None:
        messages = [{"type": "text", "order": 1, "content": {"text": "重庆百星渝中店地址我发您。"}}]
        state = {
            "planner_stage": "S2",
            "planner_sub_rule_id": "S2_LOCATION_DETAIL",
            "normalized_content": "渝中这边",
            "customer_store_knowledge": {
                "stores": [
                    {
                        "store_id": "467",
                        "store_name": "重庆百星渝中店",
                        "province": "重庆市",
                        "city": "重庆市",
                        "district": "渝中区",
                        "store_address": "重庆市渝中区解放碑步行街时代广场A座5楼",
                    }
                ]
            },
        }

        output = append_store_address_card(messages, state)

        self.assertEqual([item["type"] for item in output], ["text", "store_address"])
        self.assertEqual(output[1]["content"], {"store_id": "467"})

    def test_city_only_missing_store_does_not_append_preferred_store_card(self) -> None:
        messages = [{"type": "text", "order": 1, "content": {"text": "新疆这边目前没查到可直接发您的门店。您有其他常去地点在哪个城市或哪个区？"}}]
        state = {
            "planner_stage": "S2",
            "planner_sub_rule_id": "S2_CITY_ONLY",
            "normalized_content": "我在新疆，你们这边有门店吗",
            "customer_basic_info": {"preferred_store_id": "467", "preferred_store_name": "重庆百星渝中店"},
            "customer_store_knowledge": {
                "stores": [
                    {
                        "store_id": "467",
                        "store_name": "重庆百星渝中店",
                        "city": "重庆市",
                        "district": "渝中区",
                    }
                ]
            },
        }

        output = append_store_address_card(messages, state)

        self.assertEqual([item["type"] for item in output], ["text"])

    def test_planner_direct_reply_appends_store_address_card(self) -> None:
        plan = build_planner_plan_v2(
            {
                "normalized_content": "渝中这边",
                "customer_store_knowledge": {
                    "stores": [
                        {
                            "store_id": "467",
                            "store_name": "重庆百星渝中店",
                            "city": "重庆市",
                            "district": "渝中区",
                        }
                    ]
                },
            },
            {
                "decision": "direct_reply",
                "stage": "S2",
                "sub_rule_id": "S2_LOCATION_DETAIL",
                "reply_messages": [{"type": "text", "content": {"text": "重庆百星渝中店地址我发您。"}}],
                "tool_calls": [],
            },
        )

        self.assertEqual([item["type"] for item in plan["planner_reply_messages"]], ["text", "store_address"])
        self.assertEqual(plan["planner_reply_messages"][1]["content"], {"store_id": "467"})


if __name__ == "__main__":
    unittest.main()
