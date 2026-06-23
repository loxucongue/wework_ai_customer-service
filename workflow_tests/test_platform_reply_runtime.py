from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.chat_runtime import ChatRuntime
from app.config import Settings
from app.schemas import ChatRequest
from app.services.platform_reply_coordinator import PlatformReplyCoordinator


class PlatformReplyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_platform_request_preempts_first_and_uses_merged_content(self) -> None:
        graph = _SlowPlannerGraph()
        runtime = ChatRuntime(
            full_graph=graph,
            planner_graph=graph,
            trace_logger=_TraceLogger(),
            repository=_Repository(),
            platform_reply_coordinator=PlatformReplyCoordinator(_settings_with_empty_filter(self)),
        )

        first_task = asyncio.create_task(runtime.run_platform_reply(_request("question A")))
        await graph.started.wait()
        second_task = asyncio.create_task(runtime.run_platform_reply(_request("question B")))

        first_response = await asyncio.wait_for(first_task, timeout=2)
        self.assertEqual(first_response.reply_messages, [])

        graph.release.set()
        second_response = await asyncio.wait_for(second_task, timeout=2)
        self.assertEqual([message.type for message in second_response.reply_messages], ["text"])
        self.assertTrue(any("1. question A" in state["content"] and "2. question B" in state["content"] for state in graph.states))


class _SlowPlannerGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.states: list[dict[str, Any]] = []

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        self.states.append(dict(state))
        self.started.set()
        await self.release.wait()
        output = dict(state)
        output.update(
            {
                "planner_decision": "direct_reply",
                "planner_stage": "S1",
                "planner_sub_rule_id": "S1_GREETING",
                "planner_reply_messages": [{"type": "text", "order": 1, "content": {"text": "reply"}}],
                "reply_messages": [{"type": "text", "order": 1, "content": {"text": "reply"}}],
                "trace": [],
                "errors": [],
            }
        )
        return output


class _TraceLogger:
    def write_run(self, state: dict[str, Any]) -> str:
        return f"logs/runs/{state.get('request_id')}.json"


class _Repository:
    def __init__(self) -> None:
        self.saved_states: list[dict[str, Any]] = []

    def upsert_conversation(self, **kwargs: Any) -> None:
        return None

    def add_user_message(self, **kwargs: Any) -> None:
        return None

    def add_assistant_message(self, **kwargs: Any) -> None:
        return None

    def save_run(self, *, conversation_id: str, final_state: dict[str, Any], token_usage: dict[str, Any]) -> None:
        self.saved_states.append(dict(final_state))


def _request(content: str) -> ChatRequest:
    return ChatRequest(
        content=content,
        customer_id="customer",
        corp_id="corp",
        conversation_history=[],
        external_userid="ext",
    )


def _settings_with_empty_filter(testcase: unittest.TestCase) -> Settings:
    directory = tempfile.TemporaryDirectory()
    testcase.addCleanup(directory.cleanup)
    path = Path(directory.name) / "platform_filter_words.json"
    path.write_text(json.dumps({"enabled": True, "match_mode": "contains", "words": []}), encoding="utf-8")
    return Settings(platform_filter_words_path=path)


if __name__ == "__main__":
    unittest.main()
