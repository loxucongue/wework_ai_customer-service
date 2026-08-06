from __future__ import annotations

import unittest

from app.services.chat_gate_preview import chat_gate_preview_from_result


class ChatGatePreviewTests(unittest.TestCase):
    def test_direct_sop_is_direct_content_and_already_committed(self) -> None:
        preview = chat_gate_preview_from_result(
            {
                "mode": "sop_only",
                "send_sop": True,
                "need_ai_reply": False,
                "sop_pack_id": "s10_activity_intro",
                "reply_messages": [{"type": "text", "content": "activity"}],
            }
        )

        self.assertEqual(preview["route"], "direct_content")
        self.assertEqual(preview["commit_policy"], "already_committed_by_chat_gate")
        self.assertEqual(preview["content_candidate"]["message_types"], ["text"])

    def test_ai_then_sop_defers_commit_until_ai_reply_is_usable(self) -> None:
        preview = chat_gate_preview_from_result(
            {
                "mode": "ai_then_sop",
                "send_sop": True,
                "need_ai_reply": True,
                "sop_pack_id": "s10_need_and_case",
                "reply_messages": [
                    {"type": "text", "content": "case intro"},
                    {"type": "image", "content": "https://example.invalid/case.jpg"},
                ],
            }
        )

        self.assertEqual(preview["route"], "content_and_ai_graph")
        self.assertEqual(preview["commit_policy"], "defer_sop_commit_until_ai_reply_is_usable")
        self.assertTrue(preview["has_content_candidate"])
        self.assertEqual(preview["content_candidate"]["message_count"], 2)

    def test_ai_only_and_terminal_no_reply_are_distinct(self) -> None:
        ai_preview = chat_gate_preview_from_result(
            {"mode": "skipped", "send_sop": False, "need_ai_reply": True}
        )
        no_reply = chat_gate_preview_from_result(
            {
                "mode": "ignored_platform_auto_message",
                "send_sop": False,
                "need_ai_reply": False,
            }
        )

        self.assertEqual(ai_preview["route"], "ai_reply")
        self.assertEqual(no_reply["route"], "no_reply")


if __name__ == "__main__":
    unittest.main()
