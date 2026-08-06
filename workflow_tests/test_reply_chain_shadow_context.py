from __future__ import annotations

import unittest

from app.graph.nodes.reply_chain_shadow_context import build_reply_chain_shadow_context


class ReplyChainShadowContextTests(unittest.TestCase):
    def test_builds_timestamped_timeline_and_authoritative_facts(self) -> None:
        state = {
            "content": "怎么付费",
            "normalized_content": "怎么付费",
            "customer_scope": {"sales_contact_key": "corp:wechat:external"},
            "request_context": {
                "msgid": "m_current",
                "msgtype": "text",
                "msgtime": "1785225414095",
            },
        }
        context = build_reply_chain_shadow_context(
            state,
            identity={"request_context": {"customer_add_wechat_id": "1"}},
            customer_result={
                "customer_context": {
                    "source": "platform_order_index",
                    "orders": [{"order_id": "o1"}],
                    "deposit_state": {"status": "required_unpaid"},
                }
            },
            store_knowledge={"source": "visible_scope", "stores": [{"store_id": "101"}]},
            conversation_result={
                "conversation_turns": [
                    {
                        "message_ref": "m1",
                        "role": "customer",
                        "content": "我想预约",
                        "occurred_at": "2026-08-01T10:00:00+08:00",
                    },
                    {
                        "message_ref": "m2",
                        "role": "assistant",
                        "content": "可以的",
                        "occurred_at": "2026-08-01T10:01:00+08:00",
                    },
                ],
                "conversation_fetch": {"status": "ok", "used_message_count": 2},
            },
            memory={"customer_basic_info": {"customer_name": "张三"}, "history_events": [{"event_type": "x"}]},
        )

        self.assertEqual(context["schema_version"], "reply_chain_shadow_v1")
        self.assertEqual([item["message_ref"] for item in context["conversation"]["messages"]], ["m1", "m2", "m_current"])
        self.assertEqual(context["conversation"]["messages"][-1]["sent_at"], "2026-07-28T15:56:54+08:00")
        self.assertEqual(context["authoritative_facts"]["orders"]["count"], 1)
        self.assertEqual(context["authoritative_facts"]["visible_store_scope"]["store_count"], 1)
        self.assertEqual(context["authoritative_facts"]["sop_delivery"]["history_event_count"], 1)
        self.assertIn("customer_profile.next_sales_strategy", context["excluded_as_authority"])

    def test_falls_back_to_request_history_without_timestamps(self) -> None:
        context = build_reply_chain_shadow_context(
            {
                "conversation_history": ["用户: 洪湖市", "小贝: 我发您门店"],
                "content": "发我",
                "request_context": {"msgid": "m3", "msgtype": "text"},
            },
            identity={},
            customer_result={},
            store_knowledge={},
            conversation_result={},
            memory={},
        )

        messages = context["conversation"]["messages"]
        self.assertEqual(messages[0]["sender"], "customer")
        self.assertEqual(messages[0]["content"], "洪湖市")
        self.assertEqual(messages[1]["sender"], "assistant")
        self.assertEqual(messages[-1]["message_ref"], "m3")
        self.assertEqual(messages[-1]["content"], "发我")


if __name__ == "__main__":
    unittest.main()
