from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from typing import Any

from app.chat_runtime import ChatRuntime, _planner_sync_reply_messages, _should_run_async_finalize
from app.config import Settings
from app.schemas import ChatRequest
from app.services.platform_reply_coordinator import PlatformReplyCoordinator
from app.services.trace_logger import TraceLogger as FileTraceLogger
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

    async def test_rejected_direct_reply_schedules_async_finalize(self) -> None:
        state = {
            "planner_decision": "direct_reply",
            "tool_policy_violations": [
                {
                    "task_type": "tool_required",
                    "subtype": "customer_store_lookup",
                    "missing": "store_detail_tool_required",
                }
            ],
        }

        self.assertTrue(_should_run_async_finalize(state))

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

    async def test_platform_auto_opening_returns_sop_before_planner(self) -> None:
        graph = _UnexpectedGraph()
        repository = _Repository()
        runtime = ChatRuntime(
            full_graph=graph,
            planner_graph=graph,
            trace_logger=_TraceLogger(),
            repository=repository,
            sop_execution_service=_OpeningSopGate(),
            profile_event_extractor=_UnexpectedProfileExtractor(),
        )

        response = await runtime.run_platform_reply(_request("我已经添加了你，现在我们可以开始聊天了。"))

        self.assertEqual([message.type for message in response.reply_messages], ["text"])
        self.assertEqual(response.reply_messages[0].content["text"], "新客破冰话术")
        self.assertFalse(graph.called)
        self.assertTrue(repository.saved_states)
        state = repository.saved_states[-1]
        self.assertEqual(state["reply_source"], "sop_gate")
        self.assertEqual(state["planner_decision"], "direct_reply")
        self.assertEqual(state["planner_stage"], "SOP")
        self.assertEqual(state["async_final_reply"]["scheduled"], False)
        self.assertEqual(state["async_final_reply"]["status"], "not_required")
        self.assertEqual(state["reply_control"]["sync_return"]["type"], "sop_reply")
        self.assertEqual(len(state["reply_messages"]), 1)
        workflow_body = workflow_response_from_chat(response)
        self.assertEqual(workflow_body["code"], 0)
        self.assertEqual(len(workflow_body["data"]["reply_messages"]), 1)

    async def test_async_finalize_empty_reply_is_recovered_and_sent(self) -> None:
        repository = _Repository()
        outreach = _OutreachSendClient()
        runtime = ChatRuntime(
            full_graph=_NeedToolsPlannerGraph(),
            planner_graph=_NeedToolsPlannerGraph(),
            finalize_graph=_EmptyReplyGraph(),
            trace_logger=_TraceLogger(),
            repository=repository,
            outreach_send_client=outreach,
        )

        with patch("app.chat_runtime.random.random", return_value=0.2):
            response = await runtime.run_platform_reply(_request("集美附近门店帮我看下"))

        self.assertEqual(response.reply_messages, [])
        await asyncio.wait_for(outreach.sent.wait(), timeout=2)
        self.assertEqual(len(outreach.reply_messages), 1)
        self.assertEqual(outreach.reply_messages[0]["type"], "text")
        self.assertIn("继续帮您", str(outreach.reply_messages[0]["content"]))
        await asyncio.sleep(0)
        async_states = [state for state in repository.saved_states if state.get("async_final_reply", {}).get("status") == "sent"]
        self.assertTrue(async_states)
        self.assertEqual(async_states[-1]["reply_source"], "deterministic_async_empty_reply_fallback")

    async def test_trace_file_preserves_terminal_reply_fields_after_top_level_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = FileTraceLogger(Settings(log_dir=Path(directory)))
            state = {f"leading_{index}": index for index in range(30)}
            state.update(
                {
                    "request_id": "trace-terminal-fields",
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "已经回复"}}],
                    "reply_source": "planner_direct_reply",
                    "reply_control": {"sync_return": {"type": "direct_reply"}},
                    "trace": [],
                }
            )

            path = logger.write_run(state)
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(saved["reply_messages"][0]["content"]["text"], "已经回复")
        self.assertEqual(saved["reply_source"], "planner_direct_reply")
        self.assertEqual(saved["reply_control"]["sync_return"]["type"], "direct_reply")

    async def test_async_finalize_exception_is_recovered_and_sent(self) -> None:
        repository = _Repository()
        outreach = _OutreachSendClient()
        runtime = ChatRuntime(
            full_graph=_NeedToolsPlannerGraph(),
            planner_graph=_NeedToolsPlannerGraph(),
            finalize_graph=_ErrorGraph(),
            trace_logger=_TraceLogger(),
            repository=repository,
            outreach_send_client=outreach,
        )

        with patch("app.chat_runtime.random.random", return_value=0.2):
            response = await runtime.run_platform_reply(_request("集美附近门店帮我看下"))

        self.assertEqual(response.reply_messages, [])
        await asyncio.wait_for(outreach.sent.wait(), timeout=2)
        self.assertEqual(outreach.reply_messages[0]["type"], "text")
        await asyncio.sleep(0)
        recovered = [state for state in repository.saved_states if state.get("reply_source") == "deterministic_async_exception_fallback"]
        self.assertTrue(recovered)
        self.assertEqual(recovered[-1]["async_final_reply"]["status"], "sent")


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


class _NeedToolsPlannerGraph:
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        output = dict(state)
        output.update(
            {
                "planner_decision": "need_tools",
                "planner_stage": "S2",
                "planner_sub_rule_id": "S2_LOCATION_DETAIL",
                "planner_reply_messages": [],
                "planner_tool_calls": [{"name": "customer_store_lookup", "query": "厦门集美", "purpose": "existence"}],
                "required_tools": [{"name": "customer_store_lookup", "query": "厦门集美", "purpose": "existence"}],
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


class _OpeningSopGate:
    async def evaluate_chat_gate(
        self,
        request: ChatRequest,
        *,
        request_id: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "mode": "platform_auto_opening_sop",
            "send_sop": True,
            "need_ai_reply": False,
            "reason": "platform_auto_opening_first_add_sop",
            "sop_pack_id": "s10_new_customer_opening",
            "reply_messages": [{"type": "text", "order": 1, "content": {"text": "新客破冰话术"}}],
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


class _OutreachSendClient:
    def __init__(self) -> None:
        self.sent = asyncio.Event()
        self.reply_messages: list[dict[str, Any]] = []

    async def send_reply_messages(self, **kwargs: Any) -> dict[str, Any]:
        self.reply_messages = list(kwargs.get("reply_messages") or [])
        self.sent.set()
        return {"status": "sent"}


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
