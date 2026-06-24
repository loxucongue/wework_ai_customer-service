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


if __name__ == "__main__":
    unittest.main()
