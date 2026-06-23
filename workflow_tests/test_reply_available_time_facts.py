from __future__ import annotations

import unittest

from app.graph.nodes.reply_context import reply_user_payload_for_model


class ReplyAvailableTimeFactTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
