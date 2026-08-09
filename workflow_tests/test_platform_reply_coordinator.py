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

    async def test_regex_filter_matches_two_character_bracket_message(self) -> None:
        settings = _settings_with_filter(
            self,
            {
                "enabled": True,
                "match_mode": "contains",
                "words": ["[emotion消息]"],
                "regex_patterns": [r"^\[[^\[\]\r\n]{2}\]$"],
            },
        )
        coordinator = PlatformReplyCoordinator(settings)

        literal = await coordinator.begin(
            _request("[emotion消息]"),
            request_id="req-literal",
            request_context={"corp_id": "corp", "external_userid": "ext"},
        )
        bracket = await coordinator.begin(
            _request("[微笑]"),
            request_id="req-bracket",
            request_context={"corp_id": "corp", "external_userid": "ext2"},
        )
        normal = await coordinator.begin(
            _request("我想了解淡斑"),
            request_id="req-normal",
            request_context={"corp_id": "corp", "external_userid": "ext3"},
        )

        self.assertEqual(literal.mode, "filtered")
        self.assertEqual(literal.filter_hit["word"], "[emotion消息]")
        self.assertEqual(bracket.mode, "filtered")
        self.assertEqual(bracket.filter_hit["match_mode"], "regex")
        self.assertEqual(normal.mode, "normal")

    async def test_second_request_supersedes_first_and_merges_messages(self) -> None:
        settings = _settings_with_filter(self, {"enabled": True, "match_mode": "contains", "words": []})
        coordinator = PlatformReplyCoordinator(settings)
        first_context = {"corp_id": "corp", "external_userid": "ext", "msgid": "msg-a"}
        second_context = {"corp_id": "corp", "external_userid": "ext", "msgid": "msg-b"}

        first = await coordinator.begin(_request("question A"), request_id="req-a", request_context=first_context)
        second = await coordinator.begin(_request("question B"), request_id="req-b", request_context=second_context)

        self.assertEqual(first.mode, "normal")
        self.assertEqual(second.mode, "merged_latest")
        self.assertEqual(second.superseded_request_id, "req-a")
        self.assertEqual(second.merged_customer_messages, ["question A", "question B"])
        self.assertIn("1. question A", second.effective_content)
        self.assertIn("2. question B", second.effective_content)
        self.assertTrue(first.record.cancel_event.is_set())
        self.assertFalse(await coordinator.is_latest(first.record))
        self.assertTrue(await coordinator.is_superseded(first.record))
        self.assertEqual(first.record.superseded_by_message_id, "msg-b")
        self.assertTrue(await coordinator.is_latest(second.record))

    async def test_consecutive_images_preserve_urls_and_merge_as_image_markers(self) -> None:
        settings = _settings_with_filter(self, {"enabled": True, "match_mode": "contains", "words": []})
        coordinator = PlatformReplyCoordinator(settings)

        first = await coordinator.begin(
            _request("https://media.example/one.jpg", file_image="https://media.example/one.jpg"),
            request_id="req-image-a",
            request_context={"corp_id": "corp", "external_userid": "ext", "msgid": "msg-image-a"},
        )
        second = await coordinator.begin(
            _request("https://media.example/two.jpg", file_image="https://media.example/two.jpg"),
            request_id="req-image-b",
            request_context={"corp_id": "corp", "external_userid": "ext", "msgid": "msg-image-b"},
        )

        self.assertEqual(first.record.image_urls, ["https://media.example/one.jpg"])
        self.assertEqual(second.mode, "merged_latest")
        self.assertEqual(second.merged_customer_messages, ["[图片]", "[图片]"])
        self.assertEqual(
            second.image_urls,
            ["https://media.example/one.jpg", "https://media.example/two.jpg"],
        )
        self.assertEqual(second.effective_request_context["merged_image_urls"], second.image_urls)
        self.assertNotIn("https://media.example", second.effective_content)

    async def test_location_card_superseded_by_text_preserves_structured_event_fields(self) -> None:
        settings = _settings_with_filter(self, {"enabled": True, "match_mode": "contains", "words": []})
        coordinator = PlatformReplyCoordinator(settings)

        first = await coordinator.begin(
            _request("定位卡片：龙岗区美域蓝湾(官塘横街南50米)"),
            request_id="req-location",
            request_context={
                "corp_id": "corp",
                "external_userid": "ext",
                "msgid": "msg-location",
                "msgtime": "1786170440778",
                "msgtype": "location",
                "location": "22.711181641,114.211708069",
                "location_title": "龙岗区美域蓝湾(官塘横街南50米)",
                "location_address": "龙岗区官塘横街",
            },
        )
        second = await coordinator.begin(
            _request("这附近有门店吗"),
            request_id="req-text",
            request_context={"corp_id": "corp", "external_userid": "ext", "msgid": "msg-text", "msgtype": "text"},
        )

        self.assertEqual(first.mode, "normal")
        self.assertEqual(second.mode, "merged_latest")
        events = second.effective_request_context["merged_input_events"]
        self.assertEqual([event["msgid"] for event in events], ["msg-location", "msg-text"])
        self.assertEqual(events[0]["msgtype"], "location")
        self.assertEqual(events[0]["location"], "22.711181641,114.211708069")
        self.assertEqual(events[0]["location_title"], "龙岗区美域蓝湾(官塘横街南50米)")
        self.assertEqual(events[0]["location_address"], "龙岗区官塘横街")

    async def test_unknown_transfer_superseded_by_text_preserves_event_fact(self) -> None:
        settings = _settings_with_filter(self, {"enabled": True, "match_mode": "contains", "words": []})
        coordinator = PlatformReplyCoordinator(settings)

        await coordinator.begin(
            _request("【未知消息类型】"),
            request_id="req-transfer",
            request_context={"corp_id": "corp", "external_userid": "ext", "msgid": "msg-transfer", "msgtype": "unknown"},
        )
        second = await coordinator.begin(
            _request("我刚转了"),
            request_id="req-text",
            request_context={"corp_id": "corp", "external_userid": "ext", "msgid": "msg-text", "msgtype": "text"},
        )

        events = second.effective_request_context["merged_input_events"]
        self.assertEqual(events[0]["content"], "【未知消息类型】")
        self.assertEqual(events[1]["content"], "我刚转了")

    async def test_request_without_new_message_id_does_not_cancel_running_request(self) -> None:
        settings = _settings_with_filter(self, {"enabled": True, "match_mode": "contains", "words": []})
        coordinator = PlatformReplyCoordinator(settings)
        context = {"corp_id": "corp", "external_userid": "ext"}

        first = await coordinator.begin(_request("question A"), request_id="req-a", request_context=context)
        second = await coordinator.begin(_request("question B"), request_id="req-b", request_context=context)

        self.assertFalse(first.record.cancel_event.is_set())
        self.assertFalse(await coordinator.is_superseded(first.record))
        self.assertEqual(second.mode, "normal")


def _request(content: str, *, file_image: str = "") -> ChatRequest:
    return ChatRequest(
        content=content,
        customer_id="customer",
        corp_id="corp",
        conversation_history=[],
        external_userid="ext",
        file_image=file_image or None,
    )


def _settings_with_filter(testcase: unittest.TestCase, config: dict[str, object]) -> Settings:
    directory = tempfile.TemporaryDirectory()
    testcase.addCleanup(directory.cleanup)
    path = Path(directory.name) / "platform_filter_words.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return Settings(platform_filter_words_path=path)


if __name__ == "__main__":
    unittest.main()
