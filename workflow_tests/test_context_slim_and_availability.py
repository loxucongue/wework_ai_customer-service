from __future__ import annotations

import unittest

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.sop_event_decision import normalize_event_decision
from app.services.outreach_service import outreach_customer_fact_snapshot


class ContextSlimmingTests(unittest.TestCase):
    def test_planner_and_reply_use_fifty_messages_without_soft_profile(self) -> None:
        history = [f"用户: 消息{i}" for i in range(60)]
        state = {
            "content": "当前问题",
            "normalized_content": "当前问题",
            "conversation_history": history,
            "customer_profile": {"main_concern": "旧画像顾虑", "decision_stage": "旧阶段"},
            "customer_basic_info": {"city": "成都", "province": "四川省"},
            "history_events": [],
            "request_context": {},
        }

        planner = _planner_payload_for_model(state)
        reply = reply_user_payload_for_model(state)

        self.assertEqual(planner["conversation_history"], history[-50:])
        self.assertNotIn("customer_profile", planner)
        self.assertEqual(reply["conversation_history"], history[-50:])
        self.assertNotIn("profile", reply.get("customer_background_facts", {}))
        self.assertEqual(reply["customer_background_facts"]["basic_location"]["city"], "成都")

    def test_outreach_snapshot_keeps_durable_facts_without_soft_portrait(self) -> None:
        snapshot = outreach_customer_fact_snapshot(
            {
                "portrait": {"main_concern": "旧模型顾虑", "next_strategy": "旧策略"},
                "basic_info": {"city": "成都", "deposit_state": {"status": "unpaid"}},
                "history_events": [
                    {
                        "event_type": "customer_psychology_update",
                        "summary": "旧软画像",
                    },
                    {
                        "event_type": "voice_transcript_received",
                        "event_time": "2026-07-31T09:00:00+08:00",
                        "facts": {"transcript": "我下午有空"},
                        "source": "voice_transcription",
                    },
                    {
                        "event_type": "store_address_sent",
                        "event_time": "2026-07-31T09:02:00+08:00",
                        "facts": {"store_id": "101"},
                        "source": "reply_delivery",
                    },
                ],
            }
        )

        self.assertNotIn("portrait", snapshot)
        self.assertEqual(snapshot["basic_facts"]["city"], "成都")
        self.assertEqual(
            [item["event_type"] for item in snapshot["history_events"]],
            ["voice_transcript_received", "store_address_sent"],
        )


class ContactAvailabilityDecisionTests(unittest.TestCase):
    def _selector_input(self, *, minutes: int, customer_after: bool = False) -> dict:
        recent = [
            {
                "message_ref": "conv_1",
                "direction": "customer",
                "content": "我先忙一会儿",
                "message_time": "2026-07-31T09:00:00+08:00",
            },
            {
                "message_ref": "conv_2",
                "direction": "assistant",
                "content": "好，您先忙",
                "message_time": "2026-07-31T09:01:00+08:00",
            },
        ]
        if customer_after:
            recent.append(
                {
                    "message_ref": "conv_3",
                    "direction": "customer",
                    "content": "现在有空了",
                    "message_time": "2026-07-31T09:10:00+08:00",
                }
            )
        return {
            "mode": "platform_actions",
            "recent_conversation": recent,
            "contact_availability_evidence": {
                "latest_customer_message_ref": "conv_3" if customer_after else "conv_1",
                "latest_assistant_message_ref": "conv_2",
                "assistant_waiting_customer": not customer_after,
                "minutes_since_latest_assistant": minutes,
                "customer_messages_after_latest_assistant": 1 if customer_after else 0,
            },
            "platform_actions": {
                "editable_text_messages": [{"order": 1, "text": "平台催付内容"}],
                "readonly_messages": [{"order": 2, "type": "payment_collection"}],
            },
            "candidate_sops": [],
            "completed_sop_pack_ids": [],
            "completed_sop_categories": [],
            "event_policy_evidence": {},
        }

    @staticmethod
    def _busy_decision(decision: str, *, touch: bool = False) -> dict:
        output = {
            "decision": decision,
            "strategy": "availability_guard",
            "contact_availability_decision": {
                "status": "busy_now",
                "customer_evidence_ref": "conv_1",
                "assistant_acknowledgement_ref": "conv_2",
                "reason": "客户表示当前忙，助手已承接等待",
            },
        }
        if touch:
            output["ai_touch_messages"] = [
                {"type": "text", "content": {"text": "您先忙，方便时回我一句就行。"}}
            ]
        return output

    def test_busy_within_six_hours_blocks_platform_actions(self) -> None:
        normalized, violations = normalize_event_decision(
            self._busy_decision("defer"),
            self._selector_input(minutes=45),
        )
        self.assertEqual(violations, [])
        self.assertEqual(normalized["decision"], "defer")
        self.assertFalse(normalized["send_sop"])
        self.assertTrue(normalized["availability_guard"]["active"])

    def test_busy_after_six_hours_allows_one_low_pressure_text(self) -> None:
        normalized, violations = normalize_event_decision(
            self._busy_decision("send_ai_touch", touch=True),
            self._selector_input(minutes=420),
        )
        self.assertEqual(violations, [])
        self.assertEqual(len(normalized["ai_touch_messages"]), 1)
        self.assertEqual(normalized["ai_touch_messages"][0]["type"], "text")

    def test_busy_acknowledgement_can_be_cited_before_a_later_assistant_followup(self) -> None:
        selector_input = self._selector_input(minutes=20)
        selector_input["recent_conversation"].append(
            {
                "message_ref": "conv_3",
                "direction": "assistant",
                "content": "您忙完再说就好",
                "message_time": "2026-07-31T15:40:00+08:00",
            }
        )
        evidence = selector_input["contact_availability_evidence"]
        evidence["latest_assistant_message_ref"] = "conv_3"
        evidence["assistant_message_elapsed_minutes"] = {"conv_2": 420, "conv_3": 20}

        normalized, violations = normalize_event_decision(
            self._busy_decision("send_ai_touch", touch=True),
            selector_input,
        )

        self.assertEqual(violations, [])
        self.assertEqual(normalized["availability_guard"]["minutes_since_ack"], 420)

    def test_new_customer_message_invalidates_old_busy_state(self) -> None:
        _, violations = normalize_event_decision(
            self._busy_decision("defer"),
            self._selector_input(minutes=15, customer_after=True),
        )
        self.assertIn("busy_availability_evidence_invalid", violations)

    def test_busy_state_cannot_send_payment_or_platform_pack(self) -> None:
        raw = self._busy_decision("send")
        raw["reply_messages"] = [{"type": "payment_collection", "content": {"amount": 10}}]
        _, violations = normalize_event_decision(raw, self._selector_input(minutes=30))
        self.assertIn("busy_availability_decision_not_allowed", violations)
        self.assertIn("busy_availability_forbids_structured_messages", violations)


if __name__ == "__main__":
    unittest.main()
