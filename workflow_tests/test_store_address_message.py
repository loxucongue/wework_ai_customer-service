from __future__ import annotations

import unittest

from app.graph.message_cards import append_store_address_card
from app.graph.message_sanitizer import normalize_store_address_card_ids, sanitize_unsupported_placeholder_text
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

    def test_placeholder_store_address_text_replaced_with_fact_address(self) -> None:
        messages = [
            {"type": "text", "order": 1, "content": {"text": "重庆百星渝中店地址：重庆市渝中区解放碑步行街XX号。"}},
            {"type": "store_address", "order": 2, "content": {"store_id": "467"}},
        ]
        state = {
            "fact_envelope": {
                "structured_facts": {
                    "store_facts": [
                        {
                            "id": "467",
                            "name": "重庆百星渝中店",
                            "address": "重庆市渝中区解放碑步行街时代广场A座5楼",
                            "business_hours": "09:00-19:00",
                        }
                    ]
                }
            }
        }

        output = sanitize_unsupported_placeholder_text(messages, state)

        text = output[0]["content"]["text"]
        self.assertIn("时代广场A座5楼", text)
        self.assertNotIn("XX号", text)
        self.assertEqual(output[1]["type"], "store_address")

    def test_store_address_card_id_follows_current_text_store_name(self) -> None:
        messages = [
            {"type": "text", "order": 1, "content": {"text": "重庆百星渝中店有嘉陵中心地下停车场。"}},
            {"type": "store_address", "order": 2, "content": {"store_id": "147"}},
        ]
        state = {
            "normalized_content": "渝中这边能停车吗",
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "467", "store_name": "重庆百星渝中店", "district": "渝中区"},
                    {"store_id": "147", "store_name": "重庆南岸店", "district": "南岸区"},
                ]
            },
        }

        output = normalize_store_address_card_ids(messages, state)

        self.assertEqual(output[1]["content"], {"store_id": "467"})

    def test_unanchored_parking_question_removes_store_address_card(self) -> None:
        messages = [
            {"type": "text", "order": 1, "content": {"text": "停车信息需要结合具体门店确认，您在重庆哪个区？"}},
            {"type": "store_address", "order": 2, "content": {"store_id": "369"}},
        ]
        state = {
            "normalized_content": "能停车吗",
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "467", "store_name": "重庆百星渝中店", "district": "渝中区"},
                    {"store_id": "369", "store_name": "银川兴庆店", "district": "兴庆区"},
                ]
            },
        }

        output = normalize_store_address_card_ids(messages, state)

        self.assertEqual([item["type"] for item in output], ["text"])
        self.assertIn("具体门店", output[0]["content"]["text"])

    def test_explicit_address_request_without_selected_store_asks_to_confirm_store(self) -> None:
        messages = [
            {"type": "text", "order": 1, "content": {"text": "好的，这是银川兴庆店的详细地址。"}},
            {"type": "store_address", "order": 2, "content": {"store_id": "369"}},
        ]
        state = {
            "normalized_content": "发一下地址",
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "467", "store_name": "重庆百星渝中店", "district": "渝中区"},
                    {"store_id": "369", "store_name": "银川兴庆店", "district": "兴庆区"},
                ]
            },
        }

        output = normalize_store_address_card_ids(messages, state)

        self.assertEqual([item["type"] for item in output], ["text"])
        self.assertIn("哪家门店", output[0]["content"]["text"])
        self.assertNotIn("银川", output[0]["content"]["text"])

    def test_store_address_card_id_can_follow_recent_history_for_resend(self) -> None:
        messages = [
            {"type": "text", "order": 1, "content": {"text": "门店位置卡片我发您，点开可以查看地址和导航。"}},
            {"type": "store_address", "order": 2, "content": {"store_id": "369"}},
        ]
        state = {
            "normalized_content": "发一下地址",
            "conversation_history": [
                "小贝: 重庆百星渝中店有嘉陵中心地下停车场。",
                '小贝: [门店卡片]{"store_id": "467"}',
            ],
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "467", "store_name": "重庆百星渝中店"},
                    {"store_id": "369", "store_name": "重庆星星中店"},
                ]
            },
        }

        output = normalize_store_address_card_ids(messages, state)

        self.assertEqual(output[1]["content"], {"store_id": "467"})

    def test_store_detail_card_id_can_follow_recent_history_card(self) -> None:
        messages = [
            {"type": "text", "order": 1, "content": {"text": "重庆百星渝中店有嘉陵中心地下停车场，您可以停车。"}},
            {"type": "store_address", "order": 2, "content": {"store_id": "147"}},
        ]
        state = {
            "normalized_content": "有停车吗",
            "conversation_history": [
                "小贝: 重庆百星渝中店地址：重庆市渝中区瑞天路10号嘉陵中心A馆。",
                '小贝: [门店卡片]{"store_id": "467"}',
            ],
            "fact_envelope": {
                "structured_facts": {
                    "recommended_store": {
                        "id": "147",
                        "name": "重庆南岸店",
                        "reason": "distance_calculate_rank_1",
                    }
                }
            },
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "467", "store_name": "重庆百星渝中店"},
                    {"store_id": "147", "store_name": "重庆南岸店"},
                ]
            },
        }

        output = normalize_store_address_card_ids(messages, state)

        self.assertEqual(output[1]["content"], {"store_id": "467"})

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
