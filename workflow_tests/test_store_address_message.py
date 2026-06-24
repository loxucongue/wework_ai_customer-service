from __future__ import annotations

import unittest

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

    def test_planner_keeps_model_emitted_store_address_card(self) -> None:
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

        self.assertEqual([item["type"] for item in plan["planner_reply_messages"]], ["text", "store_address"])
        self.assertEqual(plan["planner_reply_messages"][1]["content"], {"store_id": "467"})


if __name__ == "__main__":
    unittest.main()
