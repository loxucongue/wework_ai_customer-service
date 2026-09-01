from __future__ import annotations

import unittest

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.runtime_plan import planner_public_route


class ReplyContextPayloadTests(unittest.TestCase):














    def test_reply_payload_includes_conversion_psychology_fields(self) -> None:
        payload = reply_user_payload_for_model(
            {
                "content": "多少钱",
                "normalized_content": "多少钱",
                "planner_decision": "direct_reply",
                "planner_stage": "S3",
                "planner_sub_rule_id": "S3_PRICE",
                "conversion_stage": "objection_resolution",
                "customer_type": "price",
                "main_blocker": "price",
                "next_step": "solve_blocker",
                "fact_envelope": {},
            }
        )

        self.assertEqual(payload["conversion_stage"], "objection_resolution")
        self.assertEqual(payload["customer_type"], "price")
        self.assertEqual(payload["main_blocker"], "price")
        self.assertEqual(payload["next_step"], "solve_blocker")


if __name__ == "__main__":
    unittest.main()
