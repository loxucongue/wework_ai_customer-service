from __future__ import annotations

import unittest
import json

from app.graph.nodes.reply_chain_shadow_context import build_reply_chain_shadow_context
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model


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
        self.assertEqual(context["authoritative_facts"]["orders"]["items"][0]["order_id"], "o1")
        self.assertEqual(context["authoritative_facts"]["visible_store_scope"]["store_count"], 1)
        self.assertEqual(context["authoritative_facts"]["visible_store_scope"]["store_ids"], ["101"])
        self.assertEqual(context["authoritative_facts"]["sop_delivery"]["history_event_count"], 1)
        self.assertEqual(context["authoritative_facts"]["sop_deliveries"]["recent_event_types"], ["x"])
        self.assertTrue(context["authoritative_facts"]["registration"]["phone_present"] is False)
        self.assertEqual(context["authority_audit"]["schema_version"], "reply_chain_authority_audit_v1")
        self.assertTrue(context["authority_audit"]["complete_chat_is_primary_authority"])
        self.assertEqual(
            context["authority_audit"]["current_message_audit"]["schema_version"],
            "reply_chain_current_message_audit_v1",
        )
        self.assertEqual(
            context["authority_audit"]["timeline_window_audit"]["schema_version"],
            "reply_chain_timeline_window_audit_v1",
        )
        self.assertTrue(context["authority_audit"]["timeline_window_audit"]["source_window_complete"])
        self.assertFalse(context["authority_audit"]["timeline_window_audit"]["truncated"])
        self.assertTrue(context["authority_audit"]["current_message_audit"]["current_message_in_timeline"])
        self.assertTrue(context["authority_audit"]["current_message_audit"]["current_message_is_last"])
        self.assertTrue(context["authority_audit"]["current_message_audit"]["ready_for_authoritative_model_input"])
        self.assertEqual(
            context["authority_audit"]["fact_snapshot"]["schema_version"],
            "reply_chain_fact_snapshot_audit_v1",
        )
        self.assertTrue(
            context["authority_audit"]["fact_snapshot"]["section_status"]["visible_store_scope"]["present"]
        )
        self.assertTrue(context["authority_audit"]["fact_snapshot"]["section_status"]["orders"]["present"])
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
        self.assertTrue(context["authority_audit"]["current_message_audit"]["current_message_in_timeline"])
        self.assertTrue(context["authority_audit"]["current_message_audit"]["current_message_is_last"])
        self.assertFalse(context["conversation"]["policy"]["all_messages_have_sent_at"])
        self.assertIn("history_001", context["conversation"]["policy"]["missing_time_message_refs"])
        self.assertEqual(messages[0]["time_status"], "missing")

    def test_non_text_current_request_without_text_is_still_authoritative_message(self) -> None:
        context = build_reply_chain_shadow_context(
            {
                "content": "",
                "normalized_content": "",
                "request_context": {
                    "msgid": "img_current",
                    "msgtype": "image",
                    "msgtime": "1785225414095",
                },
            },
            identity={},
            customer_result={},
            store_knowledge={},
            conversation_result={},
            memory={},
        )

        messages = context["conversation"]["messages"]
        self.assertEqual(messages[-1]["message_ref"], "img_current")
        self.assertEqual(messages[-1]["message_type"], "image")
        self.assertEqual(messages[-1]["content"], "[non-text current message: image]")
        audit = context["authority_audit"]["current_message_audit"]
        self.assertTrue(audit["current_message_required"])
        self.assertTrue(audit["current_message_in_timeline"])
        self.assertTrue(audit["current_message_is_last"])
        self.assertTrue(audit["ready_for_authoritative_model_input"])

    def test_location_current_request_uses_raw_workflow_payload_content(self) -> None:
        context = build_reply_chain_shadow_context(
            {
                "content": "",
                "normalized_content": "",
                "request_context": {
                    "msgid": "loc_current",
                    "msgtype": "location",
                    "msgtime": "1785225414095",
                    "raw_workflow_payload": {
                        "parameters": {
                            "content": {
                                "msgtype": "location",
                                "content": "",
                                "location_title": "萤火虫大厦",
                                "location_address": "福建省厦门市湖里区岐山北二路1000号",
                            }
                        }
                    },
                },
            },
            identity={},
            customer_result={},
            store_knowledge={},
            conversation_result={},
            memory={},
        )

        messages = context["conversation"]["messages"]
        self.assertEqual(messages[-1]["message_ref"], "loc_current")
        self.assertEqual(messages[-1]["message_type"], "location")
        self.assertEqual(messages[-1]["content"], "萤火虫大厦")
        self.assertTrue(context["authority_audit"]["current_message_audit"]["request_content_present"])

    def test_timeline_window_audit_allows_only_documented_truncation_above_limit(self) -> None:
        turns = [
            {
                "message_ref": f"m{i}",
                "role": "customer" if i % 2 else "assistant",
                "content": f"message {i}",
                "occurred_at": f"2026-08-01T10:{i % 60:02d}:00+08:00",
            }
            for i in range(105)
        ]
        context = build_reply_chain_shadow_context(
            {
                "content": "current question",
                "normalized_content": "current question",
                "request_context": {"msgid": "current", "msgtype": "text", "msgtime": "1785225414095"},
            },
            identity={},
            customer_result={},
            store_knowledge={},
            conversation_result={"conversation_turns": turns},
            memory={},
        )

        messages = context["conversation"]["messages"]
        audit = context["authority_audit"]["timeline_window_audit"]
        self.assertEqual(len(messages), 100)
        self.assertEqual(audit["source_message_count"], 105)
        self.assertEqual(audit["appended_current_request_count"], 1)
        self.assertEqual(audit["available_timeline_count"], 106)
        self.assertTrue(audit["truncated"])
        self.assertEqual(audit["dropped_message_count"], 6)
        self.assertTrue(audit["ready_for_authoritative_model_input"])
        self.assertEqual(messages[-1]["message_ref"], "current")

    def test_authority_audit_blocks_when_current_message_is_not_latest_timeline_item(self) -> None:
        context = build_reply_chain_shadow_context(
            {
                "content": "怎么付费",
                "normalized_content": "怎么付费",
                "request_context": {"msgid": "current", "msgtype": "text", "msgtime": "1785225414095"},
            },
            identity={},
            customer_result={},
            store_knowledge={},
            conversation_result={
                "conversation_turns": [
                    {
                        "message_ref": "current",
                        "role": "customer",
                        "content": "怎么付费",
                        "occurred_at": "2026-08-01T10:00:00+08:00",
                    },
                    {
                        "message_ref": "later_staff",
                        "role": "assistant",
                        "content": "历史里后面还有一条",
                        "occurred_at": "2026-08-01T10:01:00+08:00",
                    },
                ]
            },
            memory={},
        )

        audit = context["authority_audit"]["current_message_audit"]
        self.assertTrue(audit["current_message_in_timeline"])
        self.assertFalse(audit["current_message_is_last"])
        self.assertFalse(audit["ready_for_authoritative_model_input"])
        self.assertIn("current_message_not_last_in_timeline", audit["blockers"])

    def test_authoritative_facts_include_structured_delivery_and_risk_hold(self) -> None:
        context = build_reply_chain_shadow_context(
            {
                "content": "我过敏了还能做吗",
                "normalized_content": "我过敏了还能做吗",
                "request_context": {"msgid": "risk1", "msgtype": "text", "msgtime": "1785225414095"},
            },
            identity={},
            customer_result={},
            store_knowledge={},
            conversation_result={},
            memory={
                "history_events": [
                    {
                        "event_type": "payment_collection_sent",
                        "event_id": "pay1",
                        "created_at": "2026-08-01T10:00:00+08:00",
                        "facts": {"amount": 10},
                    },
                    {
                        "event_type": "case_image_sent",
                        "created_at": "2026-08-01T10:01:00+08:00",
                        "facts": {"image_urls": ["https://example.test/case.jpg"]},
                    },
                ]
            },
        )

        facts = context["authoritative_facts"]
        self.assertEqual(facts["payment"]["payment_collection"]["total_count"], 1)
        self.assertEqual(facts["structured_messages"]["case_image_delivery"]["last_image_count"], 1)
        self.assertEqual(facts["risk_holds"]["risk_hold"], "health_check_required")
        self.assertEqual(facts["risk_holds"]["source"], "current_message")

    def test_fact_snapshot_audit_records_source_errors_without_business_inference(self) -> None:
        context = build_reply_chain_shadow_context(
            {
                "content": "门店在哪里",
                "request_context": {"msgid": "current", "msgtype": "text", "msgtime": "1785225414095"},
            },
            identity={"error": "missing identity"},
            customer_result={"orders_error": "timeout"},
            store_knowledge={"error": "store scope timeout"},
            conversation_result={"conversation_fetch": {"status": "failed", "error": "fetch timeout"}},
            memory={},
        )

        snapshot = context["authority_audit"]["fact_snapshot"]
        self.assertIn("orders", snapshot["sections_with_error"])
        self.assertIn("visible_store_scope", snapshot["sections_with_error"])
        self.assertIn("identity", snapshot["sections_with_error"])
        self.assertTrue(snapshot["section_status"]["orders"]["has_error"])
        self.assertTrue(snapshot["section_status"]["visible_store_scope"]["has_error"])
        self.assertTrue(snapshot["section_status"]["identity"]["has_error"])

    def test_authority_audit_records_soft_profile_fields_without_promoting_them(self) -> None:
        context = build_reply_chain_shadow_context(
            {
                "content": "下午不确定",
                "request_context": {"msgid": "current", "msgtype": "text", "msgtime": "1785225414095"},
            },
            identity={},
            customer_result={},
            store_knowledge={},
            conversation_result={
                "conversation_turns": [
                    {
                        "message_ref": "m1",
                        "role": "customer",
                        "content": "我时间说不准",
                        "occurred_at": "2026-08-01T10:00:00+08:00",
                    }
                ]
            },
            memory={
                "customer_profile": {
                    "next_sales_strategy": "继续追问具体几点",
                    "decision_stage": "预约推进",
                    "main_concern": "时间不确定",
                }
            },
        )

        audit = context["authority_audit"]
        self.assertTrue(audit["complete_chat_is_primary_authority"])
        self.assertTrue(audit["soft_profile_excluded_from_authority"])
        self.assertEqual(
            audit["soft_profile_fields_seen"],
            ["next_sales_strategy", "decision_stage", "main_concern"],
        )
        self.assertIn("customer_profile.next_sales_strategy", context["excluded_as_authority"])

    def test_shadow_context_is_not_consumed_by_current_model_payloads(self) -> None:
        state = {
            "normalized_content": "效果怎么样",
            "conversation_history": ["用户: 想淡斑"],
            "reply_chain_shadow_context": {
                "schema_version": "reply_chain_shadow_v1",
                "purpose": "shadow_only_no_model_input_no_customer_effect",
                "conversation": {"messages": [{"content": "shadow-only"}]},
            },
            "request_context": {},
        }

        planner_payload = _planner_payload_for_model(state)
        reply_payload = reply_user_payload_for_model(state)
        combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

        self.assertNotIn("reply_chain_shadow_context", planner_payload)
        self.assertNotIn("reply_chain_shadow_context", reply_payload)
        self.assertNotIn("shadow-only", combined)


if __name__ == "__main__":
    unittest.main()
