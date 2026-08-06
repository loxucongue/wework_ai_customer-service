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
        self.assertEqual(preview["commit_boundary"]["schema_version"], "chat_gate_commit_boundary_v1")
        self.assertTrue(preview["commit_boundary"]["shadow_output_only"])
        self.assertFalse(preview["commit_boundary"]["this_shadow_creates_sop_task"])
        self.assertFalse(preview["commit_boundary"]["this_shadow_updates_send_once"])
        self.assertFalse(preview["commit_boundary"]["this_shadow_sends_customer_messages"])
        self.assertFalse(preview["commit_boundary"]["this_shadow_writes_database"])
        self.assertEqual(
            preview["commit_boundary"]["target_commit_owner"],
            "reply_chain_commit_phase_after_reply_validation",
        )
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
        self.assertTrue(preview["commit_boundary"]["target_direct_route_requires_commit_phase"])
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
