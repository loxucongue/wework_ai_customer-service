from __future__ import annotations

import unittest

from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.appointment_time_utils import normalize_time_text, summarize_available_slots
from app.graph.nodes.reply_context import reply_user_payload_for_model


class ReplyAvailableTimeFactTests(unittest.TestCase):
    def test_chinese_time_text_is_normalized_for_available_time(self) -> None:
        cases = {
            "太早了十点有空吗": "10:00",
            "明天九点半可以吗": "09:30",
            "下午三点能去吗": "15:00",
            "十点十五有空吗": "10:15",
            "十点15有空吗": "10:15",
            "10点十五能约吗": "10:15",
            "十一点一刻可以吗": "11:15",
            "下午两点三刻呢": "14:45",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_time_text(raw), expected)

    def test_available_time_summary_uses_chinese_target_time(self) -> None:
        slots = {
            "new": ["09:00", "09:15", "09:30", "09:45", "10:00", "10:15"],
            "pre": ["09:00", "09:30", "10:00", "10:30"],
        }

        summary = summarize_available_slots(slots, "太早了十点有空吗")

        self.assertEqual(summary["target_time"], "10:00")
        self.assertIs(summary["target_time_available"], True)
        self.assertEqual(summary["recommended_slot"], "10:00")

    def test_fact_envelope_preserves_available_chinese_target_time(self) -> None:
        fact_output = build_planner_fact_output(
            {
                "available_time": {
                    "source": "platform_agent.available_time",
                    "date": "2026-07-10",
                    "store_id": "467",
                    "slots": {
                        "new": ["09:00", "09:15", "09:30", "09:45", "10:00", "10:15"],
                        "pre": ["09:00", "09:30", "10:00", "10:30"],
                    },
                }
            },
            {"normalized_content": "太早了十点有空吗"},
        )

        appointment_fact = fact_output["fact_envelope"]["structured_facts"]["appointment_facts"][0]

        self.assertEqual(appointment_fact["target_time"], "10:00")
        self.assertIs(appointment_fact["target_time_available"], True)
        self.assertEqual(appointment_fact["recommended_slot"], "10:00")
        self.assertIn("target=10:00", fact_output["fact_envelope"]["usable_facts"][0])
        self.assertIn("target_available=True", fact_output["fact_envelope"]["usable_facts"][0])

    def test_reply_payload_summarizes_available_time_by_customer_preference(self) -> None:
        payload = reply_user_payload_for_model(
            {
                "normalized_content": "明天下午有时间吗",
                "conversation_history": [],
                "planner_decision": "need_tools",
                "planner_stage": "S3",
                "planner_sub_rule_id": "S3_APPOINTMENT_TIME",
                "primary_task": {},
                "secondary_tasks": [],
                "required_tools": [{"name": "available_time", "store_id": "467", "date": "2026-06-24"}],
                "fact_envelope": {
                    "structured_facts": {
                        "appointment_facts": [
                            {
                                "type": "available_time",
                                "store": "467",
                                "date": "2026-06-24",
                                "slots": {
                                    "new": ["09:00", "09:30", "15:00", "15:30", "16:00"],
                                },
                            }
                        ]
                    }
                },
            }
        )

        notes = "\n".join(payload["fact_notes"])

        self.assertIn("已有档期事实", notes)
        self.assertIn("15:00", notes)
        self.assertIn("15:30", notes)
        self.assertNotIn("09:00", notes)

    def test_reply_payload_marks_requested_target_time_unavailable(self) -> None:
        payload = reply_user_payload_for_model(
            {
                "normalized_content": "明天下午3点能约吗",
                "conversation_history": [],
                "planner_decision": "need_tools",
                "planner_stage": "S3",
                "planner_sub_rule_id": "S3_APPOINTMENT_TIME",
                "primary_task": {},
                "secondary_tasks": [],
                "required_tools": [{"name": "available_time", "store_id": "467", "date": "2026-06-24"}],
                "fact_envelope": {
                    "structured_facts": {
                        "appointment_facts": [
                            {
                                "type": "available_time",
                                "store": "467",
                                "date": "2026-06-24",
                                "slots": {"new": ["09:00", "09:30", "10:00", "10:30"]},
                                "target_time": "15:00",
                                "target_time_available": False,
                                "nearby_times": ["10:30", "10:00"],
                            }
                        ]
                    }
                },
            }
        )

        notes = "\n".join(payload["fact_notes"])

        self.assertIn("15:00", notes)
        self.assertIn("不在可约时间内", notes)
        self.assertIn("不能说该时间可以约", notes)


if __name__ == "__main__":
    unittest.main()
