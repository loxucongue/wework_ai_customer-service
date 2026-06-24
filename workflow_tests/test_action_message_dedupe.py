from __future__ import annotations

import unittest

from app.graph.message_cards import append_store_address_card
from app.graph.message_send_policy import suppress_repeated_action_messages
from app.graph.message_sanitizer import sanitize_unsupported_placeholder_text
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

    def test_payment_collection_not_auto_added_for_deposit_explanation(self) -> None:
        plan = build_planner_plan_v2(
            {"normalized_content": "预约金能退吗"},
            {
                "decision": "direct_reply",
                "stage": "S3",
                "sub_rule_id": "S3_DEPOSIT",
                "reply_messages": [{"type": "text", "content": {"text": "10元预约金到店抵扣，不做可以退。"}}],
                "tool_calls": [],
            },
        )

        self.assertEqual([item["type"] for item in plan["planner_reply_messages"]], ["text"])

    def test_payment_collection_removed_for_deposit_refusal_turn(self) -> None:
        output = suppress_repeated_action_messages(
            [
                {"type": "text", "order": 1, "content": {"text": "可以先到店了解，不强制。"}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ],
            {"normalized_content": "不想付预约金可以直接去吗"},
        )

        self.assertEqual([item["type"] for item in output], ["text"])

    def test_payment_collection_removed_for_do_not_send_deposit_wording(self) -> None:
        output = suppress_repeated_action_messages(
            [
                {"type": "text", "order": 1, "content": {"text": "可以，先不发入口。"}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ],
            {"normalized_content": "先不要发预约金，我到店再看"},
        )

        self.assertEqual([item["type"] for item in output], ["text"])

    def test_payment_collection_can_be_sent_when_customer_asks_for_entry(self) -> None:
        output = suppress_repeated_action_messages(
            [
                {"type": "text", "order": 1, "content": {"text": "可以，我把入口发您。"}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ],
            {"normalized_content": "发一下预约金入口"},
        )

        self.assertEqual([item["type"] for item in output], ["text", "payment_collection"])

    def test_payment_collection_auto_added_for_direct_send_deposit_wording(self) -> None:
        plan = build_planner_plan_v2(
            {"normalized_content": "直接发预约金吧"},
            {
                "decision": "direct_reply",
                "stage": "S3",
                "sub_rule_id": "S3_PAYMENT_COLLECTION",
                "reply_messages": [{"type": "text", "content": {"text": "可以，我把入口发您。"}}],
                "tool_calls": [],
            },
        )

        self.assertEqual([item["type"] for item in plan["planner_reply_messages"]], ["text", "payment_collection"])

    def test_appointment_cancel_completion_claim_is_rewritten_without_success_fact(self) -> None:
        output = sanitize_unsupported_placeholder_text(
            [{"type": "text", "order": 1, "content": {"text": "可以，我帮您取消预约。"}}],
            {"normalized_content": "不行的话先取消可以吗"},
        )

        self.assertEqual(output[0]["content"]["text"], "可以，我先帮您核对当前预约，再同步取消处理。")

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

    def test_store_address_card_can_use_customer_basic_preferred_store(self) -> None:
        output = append_store_address_card(
            [{"type": "text", "order": 1, "content": {"text": "厦门思明店地址我发您。"}}],
            {
                "planner_stage": "S2",
                "planner_sub_rule_id": "S2_ADDRESS_DETAIL",
                "normalized_content": "我在厦门，门店地址给我一下",
                "customer_basic_info": {"preferred_store_id": "12", "preferred_store_name": "厦门思明店"},
                "history_events": [{"event_type": "store_address_sent", "facts": {"store_id": "12"}}],
            },
        )

        self.assertEqual([item["type"] for item in output], ["text", "store_address"])
        self.assertEqual(output[1]["content"], {"store_id": "12"})


if __name__ == "__main__":
    unittest.main()
