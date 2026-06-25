from __future__ import annotations

import unittest

from app.config import Settings
from app.graph.nodes.image_info import fallback_image_info
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.model_client import ModelClient
from app.services.model_selection import model_names


class ModelTimeoutAndPlannerPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_client_uses_five_second_connect_timeout(self) -> None:
        client = ModelClient(Settings(model_timeout_seconds=45))

        http_client = client._http_client()

        self.assertEqual(http_client.timeout.connect, 5)
        self.assertEqual(http_client.timeout.read, 45)
        await client.aclose()

    def test_planner_retries_primary_once_then_falls_back_to_qwen_turbo(self) -> None:
        settings = Settings(model_planner="qwen-plus", model_planner_fallbacks="")

        self.assertEqual(model_names(settings, "planner"), ["qwen-plus", "qwen-plus", "qwen-turbo"])

    def test_planner_keeps_qwen_turbo_after_custom_fallbacks(self) -> None:
        settings = Settings(model_planner="qwen-plus", model_planner_fallbacks="custom-model")

        self.assertEqual(model_names(settings, "planner"), ["qwen-plus", "qwen-plus", "custom-model", "qwen-turbo"])

    def test_reply_uses_qwen_plus_multiple_times_without_turbo(self) -> None:
        settings = Settings(model_reply="qwen-plus", model_reply_fallbacks="qwen-plus,qwen-plus")

        self.assertEqual(model_names(settings, "reply"), ["qwen-plus", "qwen-plus", "qwen-plus"])

    def test_planner_payload_drops_empty_optional_sections(self) -> None:
        payload = _planner_payload_for_model(
            {
                "normalized_content": "多少钱",
                "conversation_history": [],
                "image_info": {},
                "request_context": {"category_id": ""},
                "customer_profile": {},
                "history_events": [],
                "customer_context": {},
                "customer_store_knowledge": {},
                "sent_message_summary": {},
            }
        )

        self.assertEqual(payload["current_message"], "多少钱")
        self.assertNotIn("conversation_history", payload)
        self.assertNotIn("image_info", payload)
        self.assertNotIn("category_id", payload)
        self.assertNotIn("customer_profile", payload)
        self.assertNotIn("history_events", payload)
        self.assertNotIn("customer_context", payload)
        self.assertNotIn("store_scope_summary", payload)
        self.assertIn("available_tools", payload)

    def test_planner_payload_drops_no_image_fact_when_image_info_is_normalized(self) -> None:
        payload = _planner_payload_for_model(
            {
                "normalized_content": "你好",
                "image_info": fallback_image_info(has_image=False),
                "customer_store_knowledge": {},
            }
        )

        self.assertNotIn("image_info", payload)

    def test_planner_payload_keeps_loaded_empty_store_scope(self) -> None:
        payload = _planner_payload_for_model(
            {
                "normalized_content": "store?",
                "customer_store_knowledge": {
                    "source": "platform_scope",
                    "store_count": 0,
                    "stores": [],
                    "missing_snapshot_store_ids": [],
                },
            }
        )

        self.assertEqual(payload["store_scope_summary"], {"source": "platform_scope", "store_count": 0})


if __name__ == "__main__":
    unittest.main()
