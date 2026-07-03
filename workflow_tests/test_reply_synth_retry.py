from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.config import Settings
from app.graph.nodes.reply_nodes import create_synthesize_reply_node
from app.graph.nodes.reply_validation import debug_message_contents, validated_model_messages
from app.services.trace_logger import TraceLogger


class FakeRetryModelClient:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return {"message": "missing schema"}
        return {
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "我帮您核对一下更方便的门店。"}}
            ]
        }


class FakeBadHandoffModelClient:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
        self.calls += 1
        return {
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "我马上帮您同步给专业顾问，稍后会有专人联系您。"},
                },
                {
                    "type": "human_handoff_notice",
                    "order": 2,
                    "content": {"handoff_reason": "客户要求人工处理"},
                },
            ]
        }


class ReplySynthRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_synth_retries_once_when_json_missing_reply_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model = FakeRetryModelClient()
            node = create_synthesize_reply_node(
                trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
                model_client=model,
                debug_message_contents=debug_message_contents,
                reply_messages_for_model=lambda _state: [
                    {"role": "system", "content": "output json"},
                    {"role": "user", "content": "{}"},
                ],
                should_use_model_reply=lambda _state: True,
                validated_model_messages=validated_model_messages,
            )
            state: dict[str, Any] = {
                "request_id": "test-retry",
                "trace": [],
                "errors": [],
                "warnings": [],
                "planner_decision": "need_tools",
                "fact_envelope": {},
                "required_tools": [],
            }

            output = await node(state)

        self.assertEqual(model.calls, 2)
        self.assertEqual(output["errors"], [])
        self.assertEqual(output["reply_messages"][0]["content"], "我帮您核对一下更方便的门店。")
        retry_info = state["trace"][0]["tool_calls"][0].get("retry")
        self.assertIsInstance(retry_info, dict)
        self.assertIn("Model JSON missing reply_messages", retry_info.get("reason", ""))

    async def test_reply_synth_uses_handoff_notice_fallback_after_bad_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model = FakeBadHandoffModelClient()
            node = create_synthesize_reply_node(
                trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
                model_client=model,
                debug_message_contents=debug_message_contents,
                reply_messages_for_model=lambda _state: [
                    {"role": "system", "content": "output json"},
                    {"role": "user", "content": "{}"},
                ],
                should_use_model_reply=lambda _state: True,
                validated_model_messages=validated_model_messages,
            )
            state: dict[str, Any] = {
                "request_id": "test-handoff-fallback",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "我要人工",
                "normalized_content": "我要人工",
                "planner_decision": "need_tools",
                "handoff": {"needed": True, "reason": "客户要求人工处理"},
                "required_tools": [{"name": "professional_assist", "reason": "客户要求人工处理"}],
                "fact_envelope": {
                    "structured_facts": {
                        "professional_assist": {"status": "requested", "reason": "客户要求人工处理"}
                    }
                },
            }

            output = await node(state)

        self.assertEqual(model.calls, 2)
        self.assertEqual(output["errors"], [])
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text", "human_handoff_notice"])
        text = output["reply_messages"][0]["content"]
        self.assertIn("具体问题", text)
        self.assertNotIn("专业顾问", text)
        self.assertNotIn("专人联系", text)
        self.assertNotIn("同步处理", text)
        fallback_info = state["trace"][0]["tool_calls"][0].get("fallback")
        self.assertEqual(fallback_info.get("strategy"), "deterministic_handoff_notice")

    async def test_no_reply_strong_dissatisfaction_gets_visible_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            node = create_synthesize_reply_node(
                trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
                model_client=None,
                debug_message_contents=debug_message_contents,
                reply_messages_for_model=lambda _state: [],
                should_use_model_reply=lambda _state: False,
                validated_model_messages=validated_model_messages,
            )
            state: dict[str, Any] = {
                "request_id": "test-no-reply-dissatisfaction",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "已经说了三遍了你还问，烦死了",
                "normalized_content": "已经说了三遍了你还问，烦死了",
                "planner_decision": "no_reply",
                "fact_envelope": {},
                "required_tools": [],
            }

            output = await node(state)

        self.assertEqual(output["errors"], [])
        self.assertEqual(output["reply_source"], "deterministic_dissatisfaction_fallback")
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text", "human_handoff_notice"])
        self.assertIn("不再重复问", output["reply_messages"][0]["content"])

    async def test_no_reply_explicit_stop_stays_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            node = create_synthesize_reply_node(
                trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
                model_client=None,
                debug_message_contents=debug_message_contents,
                reply_messages_for_model=lambda _state: [],
                should_use_model_reply=lambda _state: False,
                validated_model_messages=validated_model_messages,
            )
            state: dict[str, Any] = {
                "request_id": "test-no-reply-stop",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "别回了",
                "normalized_content": "别回了",
                "planner_decision": "no_reply",
                "fact_envelope": {},
                "required_tools": [],
            }

            output = await node(state)

        self.assertEqual(output["errors"], [])
        self.assertEqual(output["reply_source"], "planner_no_reply")
        self.assertEqual(output["reply_messages"], [])

    async def test_handoff_notice_fallback_when_reply_model_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            node = create_synthesize_reply_node(
                trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
                model_client=None,
                debug_message_contents=debug_message_contents,
                reply_messages_for_model=lambda _state: [],
                should_use_model_reply=lambda _state: True,
                validated_model_messages=validated_model_messages,
            )
            state: dict[str, Any] = {
                "request_id": "test-handoff-no-model",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "我要人工",
                "normalized_content": "我要人工",
                "planner_decision": "direct_reply",
                "planner_reply_messages": [
                    {"type": "human_handoff", "order": 1, "content": {"handoff_reason": "模型不可用"}}
                ],
                "handoff": {"needed": True, "reason": "模型不可用"},
                "fact_envelope": {},
                "required_tools": [],
            }

            output = await node(state)

        self.assertEqual(output["errors"], [])
        self.assertEqual(output["reply_source"], "deterministic_handoff_notice_fallback")
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text", "human_handoff_notice"])


if __name__ == "__main__":
    unittest.main()
