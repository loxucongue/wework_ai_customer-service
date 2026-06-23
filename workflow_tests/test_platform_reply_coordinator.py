from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.schemas import ChatRequest
from app.services.platform_reply_coordinator import PlatformReplyCoordinator


class PlatformReplyCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_filter_word_returns_filtered_decision(self) -> None:
        settings = _settings_with_filter(self, {"enabled": True, "match_mode": "contains", "words": ["ignore-me"]})
        coordinator = PlatformReplyCoordinator(settings)

        decision = await coordinator.begin(
            _request("please ignore-me"),
            request_id="req-filter",
            request_context={"corp_id": "corp", "external_userid": "ext"},
        )

        self.assertEqual(decision.mode, "filtered")
        self.assertFalse(decision.should_run_graph)
        self.assertEqual(decision.filter_hit["word"], "ignore-me")

    async def test_second_request_supersedes_first_and_merges_messages(self) -> None:
        settings = _settings_with_filter(self, {"enabled": True, "match_mode": "contains", "words": []})
        coordinator = PlatformReplyCoordinator(settings)
        context = {"corp_id": "corp", "external_userid": "ext"}

        first = await coordinator.begin(_request("question A"), request_id="req-a", request_context=context)
        second = await coordinator.begin(_request("question B"), request_id="req-b", request_context=context)

        self.assertEqual(first.mode, "normal")
        self.assertEqual(second.mode, "merged_latest")
        self.assertEqual(second.superseded_request_id, "req-a")
        self.assertEqual(second.merged_customer_messages, ["question A", "question B"])
        self.assertIn("1. question A", second.effective_content)
        self.assertIn("2. question B", second.effective_content)
        self.assertTrue(first.record.cancel_event.is_set())
        self.assertFalse(await coordinator.is_latest(first.record))
        self.assertTrue(await coordinator.is_latest(second.record))


def _request(content: str) -> ChatRequest:
    return ChatRequest(
        content=content,
        customer_id="customer",
        corp_id="corp",
        conversation_history=[],
        external_userid="ext",
    )


def _settings_with_filter(testcase: unittest.TestCase, config: dict[str, object]) -> Settings:
    directory = tempfile.TemporaryDirectory()
    testcase.addCleanup(directory.cleanup)
    path = Path(directory.name) / "platform_filter_words.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return Settings(platform_filter_words_path=path)


if __name__ == "__main__":
    unittest.main()
