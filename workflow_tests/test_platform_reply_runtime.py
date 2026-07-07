from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from typing import Any

from app.chat_runtime import ChatRuntime, _planner_sync_reply_messages
from app.config import Settings
from app.schemas import ChatRequest
from app.services.platform_reply_coordinator import PlatformReplyCoordinator
from app.services.workflow_compat import workflow_response_from_chat


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

    async def test_need_tools_sync_reply_is_empty_for_two_thirds_probability(self) -> None:
        with patch("app.chat_runtime.random.random", return_value=0.2):
            messages = _planner_sync_reply_messages({"planner_decision": "need_tools", "planner_reply_messages": []})

        self.assertEqual(messages, [])

    async def test_need_tools_sync_reply_can_use_short_random_transition(self) -> None:
        with patch("app.chat_runtime.random.random", return_value=0.9), patch(
            "app.chat_runtime.random.choice", return_value="稍等哈"
        ):
            messages = _planner_sync_reply_messages({"planner_decision": "need_tools", "planner_reply_messages": []})

        self.assertEqual(messages, [{"type": "text", "order": 1, "content": {"text": "稍等哈"}}])

    async def test_professional_assist_need_tools_returns_notice_sync_reply(self) -> None:
        with patch("app.chat_runtime.random.random", return_value=0.9), patch(
            "app.chat_runtime.random.choice", return_value="稍等一下哈"
        ):
            messages = _planner_sync_reply_messages(
                {
                    "planner_decision": "need_tools",
                    "required_tools": [{"name": "professional_assist", "reason": "健康高风险"}],
                    "planner_reply_messages": [],
                }
            )

        self.assertEqual([item["type"] for item in messages], ["text", "human_handoff_notice"])
        self.assertEqual(messages[1]["content"]["handoff_reason"], "健康高风险")
        visible_text = messages[0]["content"]["text"]
        self.assertIn("到店先做检测", visible_text)
        self.assertNotIn("转人工", visible_text)
        self.assertNotIn("专业同事", visible_text)

    async def test_run_chat_graph_exception_returns_deterministic_reply_instead_of_502(self) -> None:
        repository = _Repository()
        runtime = ChatRuntime(
            full_graph=_ErrorGraph(),
            trace_logger=_TraceLogger(),
            repository=repository,
        )

        response = await runtime.run_chat(_request("这家地址发我一下"))

        self.assertEqual(len(response.reply_messages), 1)
        self.assertEqual(response.reply_messages[0].type, "text")
        self.assertIn("门店", str(response.reply_messages[0].content))
        self.assertTrue(repository.saved_states)
        self.assertEqual(repository.saved_states[-1]["reply_source"], "deterministic_runtime_exception_fallback")

    async def test_run_chat_empty_final_reply_returns_deterministic_reply_instead_of_502(self) -> None:
        repository = _Repository()
        runtime = ChatRuntime(
            full_graph=_EmptyReplyGraph(),
            trace_logger=_TraceLogger(),
            repository=repository,
        )

        response = await runtime.run_chat(_request("预约金入口发我"))

        self.assertEqual(len(response.reply_messages), 1)
        self.assertEqual(response.reply_messages[0].type, "text")
        self.assertIn("刚刚这条", str(response.reply_messages[0].content))
        self.assertEqual(repository.saved_states[-1]["reply_source"], "deterministic_empty_reply_fallback")

    async def test_platform_auto_opening_sop_gate_stops_before_planner(self) -> None:
        graph = _UnexpectedGraph()
        repository = _Repository()
        runtime = ChatRuntime(
            full_graph=graph,
            planner_graph=graph,
            trace_logger=_TraceLogger(),
            repository=repository,
            sop_execution_service=_IgnoredSopGate(),
            profile_event_extractor=_UnexpectedProfileExtractor(),
        )

        response = await runtime.run_platform_reply(_request("我已经添加了你，现在我们可以开始聊天了。"))

        self.assertEqual(response.reply_messages, [])
        self.assertFalse(graph.called)
        self.assertTrue(repository.saved_states)
        state = repository.saved_states[-1]
        self.assertEqual(state["reply_source"], "ignored_platform_auto_message")
        self.assertEqual(state["planner_decision"], "no_reply")
        self.assertEqual(state["planner_stage"], "SOP_GATE")
        self.assertEqual(state["async_final_reply"]["scheduled"], False)
        self.assertEqual(state["async_final_reply"]["status"], "not_required")
        self.assertEqual(state["reply_control"]["sync_return"]["type"], "empty")
        self.assertEqual(state["reply_messages"], [])
        workflow_body = workflow_response_from_chat(response)
        self.assertEqual(workflow_body["code"], 0)
        self.assertEqual(workflow_body["data"]["reply_messages"], [])


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


class _ErrorGraph:
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")


class _EmptyReplyGraph:
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        output = dict(state)
        output.update(
            {
                "planner_decision": "direct_reply",
                "planner_stage": "S1",
                "planner_sub_rule_id": "S1_EMPTY",
                "reply_messages": [],
                "trace": [],
                "errors": [],
            }
        )
        return output


class _UnexpectedGraph:
    def __init__(self) -> None:
        self.called = False

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        self.called = True
        raise AssertionError("planner should not run for ignored platform auto opening")


class _UnexpectedProfileExtractor:
    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("profile extractor should not run for ignored platform auto opening")


class _IgnoredSopGate:
    async def evaluate_chat_gate(
        self,
        request: ChatRequest,
        *,
        request_id: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "mode": "ignored_platform_auto_message",
            "send_sop": False,
            "need_ai_reply": False,
            "reason": "platform_auto_opening_message",
            "reply_messages": [],
        }


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
