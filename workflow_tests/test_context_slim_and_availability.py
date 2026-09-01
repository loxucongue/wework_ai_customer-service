from __future__ import annotations

import unittest

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.services.outreach_service import outreach_customer_fact_snapshot


class ContextSlimmingTests(unittest.TestCase):

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


if __name__ == "__main__":
    unittest.main()
