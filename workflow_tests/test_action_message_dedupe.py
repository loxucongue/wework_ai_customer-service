from __future__ import annotations

import unittest

from app.graph.message_cards import append_store_address_card
from app.graph.message_send_policy import suppress_repeated_action_messages
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2


class ActionMessageDedupeTests(unittest.TestCase):
    def test_payment_collection_not_repeated_after_sent_event(self) -> None:
        plan = build_planner_plan_v2(
            {
                "normalized_content": "我想报名",
                "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 10}}],
            },
            {
                "decision": "direct_reply",
                "stage": "S3",
                "sub_rule_id": "S3_PAYMENT_COLLECTION",
                "reply_messages": [{"type": "text", "content": {"text": "我先帮您继续确认名额。"}}],
                "tool_calls": [],
            },
        )

        self.assertEqual([item["type"] for item in plan["planner_reply_messages"]], ["text"])

    def test_payment_collection_not_repeated_from_assistant_history_marker(self) -> None:
        output = suppress_repeated_action_messages(
            [
                {"type": "text", "order": 1, "content": {"text": "我继续帮您确认名额。"}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ],
            {
                "normalized_content": "我想报名",
                "conversation_history": [
                    "小贝: 我先把10元预约金入口发您，到店抵扣。",
                    "小贝: 付款给：黛伊科技",
                ],
            },
        )

        self.assertEqual([item["type"] for item in output], ["text"])

    def test_payment_collection_can_be_resent_when_customer_explicitly_asks(self) -> None:
        messages = [
            {"type": "text", "order": 1, "content": {"text": "可以，我再发您一次。"}},
            {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
        ]
        output = suppress_repeated_action_messages(
            messages,
            {
                "normalized_content": "没收到 再发一下付款入口",
                "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 10}}],
            },
        )

        self.assertEqual([item["type"] for item in output], ["text", "payment_collection"])

    def test_store_address_card_not_repeated_for_same_store_without_explicit_request(self) -> None:
        output = append_store_address_card(
            [{"type": "text", "order": 1, "content": {"text": "这家楼下可以停车。"}}],
            {
                "planner_stage": "S2",
                "planner_sub_rule_id": "S2_PARKING_OR_HOURS",
                "normalized_content": "能停车吗",
                "history_events": [{"event_type": "store_address_sent", "facts": {"store_id": "467"}}],
                "fact_envelope": {"structured_facts": {"recommended_store": {"id": "467"}}},
            },
        )

        self.assertEqual([item["type"] for item in output], ["text"])

    def test_store_address_card_can_be_resent_when_customer_explicitly_asks(self) -> None:
        output = append_store_address_card(
            [{"type": "text", "order": 1, "content": {"text": "可以，我再发您一次地址。"}}],
            {
                "planner_stage": "S2",
                "planner_sub_rule_id": "S2_LOCATION_DETAIL",
                "normalized_content": "没收到 再发一下地址",
                "history_events": [{"event_type": "store_address_sent", "facts": {"store_id": "467"}}],
                "fact_envelope": {"structured_facts": {"recommended_store": {"id": "467"}}},
            },
        )

        self.assertEqual([item["type"] for item in output], ["text", "store_address"])

    def test_store_address_card_can_be_resent_for_address_give_me_wording(self) -> None:
        output = append_store_address_card(
            [{"type": "text", "order": 1, "content": {"text": "地址我发您。"}}],
            {
                "planner_stage": "S2",
                "planner_sub_rule_id": "S2_ADDRESS_DETAIL",
                "normalized_content": "门店地址给我一下",
                "history_events": [{"event_type": "store_address_sent", "facts": {"store_id": "12"}}],
                "fact_envelope": {"structured_facts": {"recommended_store": {"id": "12"}}},
            },
        )

        self.assertEqual([item["type"] for item in output], ["text", "store_address"])


if __name__ == "__main__":
    unittest.main()
