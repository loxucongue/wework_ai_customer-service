from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from typing import Any

from app.chat_runtime import (
    ChatRuntime,
    _merge_ai_then_sop_reply_messages,
    _planner_sync_reply_messages,
    _should_run_async_finalize,
    _sop_gate_direct_reply,
    _sop_gate_terminal_no_reply,
)
from app.config import Settings
from app.schemas import ChatRequest
from app.services.platform_reply_coordinator import PlatformReplyCoordinator
from app.services.trace_logger import TraceLogger as FileTraceLogger
from app.services.workflow_compat import workflow_response_from_chat


class PlatformReplyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_model_confirmed_safety_stop_is_terminal(self) -> None:
        self.assertTrue(
            _sop_gate_terminal_no_reply(
                {
                    "mode": "safety_stop_no_reply",
                    "send_sop": False,
                    "need_ai_reply": False,
                }
            )
        )

    def test_runtime_initial_state_contains_shared_round_budget(self) -> None:
        runtime = ChatRuntime(
            full_graph=_EmptyReplyGraph(),
            trace_logger=_TraceLogger(),
            repository=_Repository(),
            settings=Settings(
                _env_file=None,
                model_round_budget_enforced=True,
                model_round_timeout_seconds=60,
                model_reply_reserve_seconds=15,
            ),
        )

        state = runtime._initial_state(_request("测试"), "request-id", {})

        self.assertEqual(state["runtime_budget"]["mode"], "enforced")
        self.assertEqual(state["runtime_budget"]["ordinary_timeout_seconds"], 60)
        self.assertEqual(state["runtime_budget"]["reply_reserve_seconds"], 15)

    async def test_second_platform_request_preempts_first_and_uses_merged_content(self) -> None:
        graph = _SlowPlannerGraph()
        runtime = ChatRuntime(
            full_graph=graph,
            planner_graph=graph,
            trace_logger=_TraceLogger(),
            repository=_Repository(),
            platform_reply_coordinator=PlatformReplyCoordinator(_settings_with_empty_filter(self)),
        )

        first_task = asyncio.create_task(runtime.run_platform_reply(_request("question A", msgid="msg-a")))
        await graph.started.wait()
        second_task = asyncio.create_task(runtime.run_platform_reply(_request("question B", msgid="msg-b")))

        first_response = await asyncio.wait_for(first_task, timeout=2)
        self.assertEqual(first_response.reply_messages, [])

        graph.release.set()
        second_response = await asyncio.wait_for(second_task, timeout=2)
        self.assertEqual([message.type for message in second_response.reply_messages], ["text"])
        self.assertTrue(any("1. question A" in state["content"] and "2. question B" in state["content"] for state in graph.states))

    async def test_same_platform_message_id_reuses_single_execution(self) -> None:
        graph = _SlowPlannerGraph()
        runtime = ChatRuntime(
            full_graph=graph,
            planner_graph=graph,
            trace_logger=_TraceLogger(),
            repository=_Repository(),
            platform_reply_coordinator=PlatformReplyCoordinator(_settings_with_empty_filter(self)),
        )
        request = _request("same question", msgid="same-msg")

        first_task = asyncio.create_task(runtime.run_platform_reply(request))
        await graph.started.wait()
        second_task = asyncio.create_task(runtime.run_platform_reply(request))
        graph.release.set()

        first_response, second_response = await asyncio.gather(first_task, second_task)

        self.assertEqual(len(graph.states), 1)
        self.assertEqual(first_response.model_dump(), second_response.model_dump())
        cached_response = await runtime.run_platform_reply(request)
        self.assertEqual(len(graph.states), 1)
        self.assertEqual(first_response.model_dump(), cached_response.model_dump())

    async def test_need_tools_sync_reply_is_always_empty(self) -> None:
        messages = _planner_sync_reply_messages({"planner_decision": "need_tools", "planner_reply_messages": []})
        self.assertEqual(messages, [])

    async def test_need_tools_does_not_emit_intermediate_transition(self) -> None:
        messages = _planner_sync_reply_messages({"planner_decision": "need_tools", "planner_reply_messages": []})
        self.assertEqual(messages, [])

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

    async def test_valid_direct_reply_also_waits_for_final_reply_model(self) -> None:
        state = {
            "planner_decision": "direct_reply",
            "tool_policy_violations": [],
            "planner_reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "planner draft"}}
            ],
        }

        self.assertTrue(_should_run_async_finalize(state))

    async def test_professional_assist_waits_for_final_reply(self) -> None:
        state = {
            "planner_decision": "need_tools",
            "required_tools": [{"name": "professional_assist", "reason": "健康高风险"}],
            "planner_tool_calls": [{"name": "professional_assist", "reason": "健康高风险"}],
            "planner_reply_messages": [],
        }
        messages = _planner_sync_reply_messages(state)
        self.assertEqual(messages, [])
        self.assertTrue(_should_run_async_finalize(state))

    async def test_malformed_need_tools_without_executable_call_still_runs_final_reply(self) -> None:
        state = {
            "planner_decision": "need_tools",
            "planner_tool_calls": [],
            "tool_policy_violations": [
                {
                    "task_type": "tool_structure",
                    "subtype": "need_tools",
                    "missing": "need_tools_requires_executable_tool",
                }
            ],
        }

        self.assertTrue(_should_run_async_finalize(state))

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
        self.assertTrue(response.reply_messages[0].content.get("text"))
        self.assertNotEqual(response.reply_messages[0].content, {"text": "我在，继续帮您处理。"})
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
        self.assertTrue(response.reply_messages[0].content.get("text"))
        self.assertNotEqual(response.reply_messages[0].content, {"text": "我在，继续帮您处理。"})
        self.assertEqual(repository.saved_states[-1]["reply_source"], "deterministic_empty_reply_fallback")

    async def test_superseded_final_state_does_not_inject_empty_reply_fallback(self) -> None:
        repository = _Repository()
        runtime = ChatRuntime(
            full_graph=_EmptyReplyGraph(),
            trace_logger=_TraceLogger(),
            repository=repository,
        )
        state = runtime._initial_state(_request("older message"), "request-id", {})
        state["reply_messages"] = []
        state["reply_control"] = {"mode": "superseded", "sync_return": {"type": "empty", "reply_messages": []}}
        state["async_final_reply"] = {"scheduled": False, "status": "superseded"}

        response = runtime._persist_and_build_response(
            request=_request("older message"),
            request_id="request-id",
            conversation_id="conversation-id",
            final_state=state,
            allow_empty_reply=False,
        )

        self.assertEqual(response.reply_messages, [])
        self.assertEqual(repository.saved_states[-1]["reply_messages"], [])
        self.assertEqual(repository.saved_states[-1]["reply_source"], "platform_superseded")
        self.assertEqual(repository.saved_states[-1]["reply_control"]["sync_return"]["type"], "empty")

    async def test_platform_auto_opening_sop_returns_configured_messages_without_models(self) -> None:
        graph = _OpeningUnifiedGraph()
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

        self.assertEqual([message.type for message in response.reply_messages], ["text", "image"])
        self.assertEqual(response.reply_messages[0].content["text"], "新客破冰话术")
        self.assertEqual(response.reply_messages[1].content["url"], "https://example.com/opening.png")
        self.assertFalse(graph.called)
        self.assertTrue(repository.saved_states)
        state = repository.saved_states[-1]
        self.assertEqual(state["reply_source"], "sop_gate")
        self.assertEqual(state["planner_decision"], "direct_reply")
        self.assertEqual(state["planner_stage"], "SOP")
        self.assertEqual(state["async_final_reply"]["scheduled"], False)
        self.assertEqual(state["async_final_reply"]["status"], "not_required")
        self.assertEqual(state["reply_control"]["sync_return"]["type"], "sop_reply")
        self.assertEqual(len(state["reply_messages"]), 2)
        workflow_body = workflow_response_from_chat(response)
        self.assertEqual(workflow_body["code"], 0)
        self.assertEqual(len(workflow_body["data"]["reply_messages"]), 2)

    def test_only_explicit_opening_passthrough_can_bypass_planner(self) -> None:
        messages = [{"type": "text", "content": {"text": "配置内容"}}]
        self.assertTrue(
            _sop_gate_direct_reply(
                {
                    "mode": "platform_auto_opening_sop",
                    "delivery_mode": "configured_passthrough",
                    "send_sop": True,
                    "need_ai_reply": False,
                    "reply_messages": messages,
                }
            )
        )
        self.assertFalse(
            _sop_gate_direct_reply(
                {
                    "mode": "sop_only",
                    "send_sop": True,
                    "need_ai_reply": False,
                    "reply_messages": messages,
                }
            )
        )

    async def test_precision_ai_reply_precedes_selected_sop_and_confirms_task(self) -> None:
        repository = _Repository()
        gate = _PrecisionSopGate()
        graph = _PrecisionReplyGraph()
        runtime = ChatRuntime(
            full_graph=graph,
            planner_graph=graph,
            trace_logger=_TraceLogger(),
            repository=repository,
            sop_execution_service=gate,
        )

        response = await runtime.run_platform_reply(_request("是不是做一次就可以"))

        self.assertEqual(
            [message.content["text"] for message in response.reply_messages if message.type == "text"],
            ["多数客户做一次就能看到改善，具体程度要到店检测后判断。", "我给您发一组同类案例参考。"],
        )
        self.assertTrue(gate.confirmed)
        self.assertFalse(gate.failed)
        self.assertEqual(graph.states[0]["sop_gate_decision"]["priority_question_id"], "one_session_effect")
        self.assertEqual(set(graph.states[0]["sop_gate_decision"]["sop_message_types"]), {"text", "image"})
        self.assertEqual(graph.states[0]["sop_gate_decision"]["sop_image_count"], 1)

    async def test_precision_ai_failure_withholds_sop_and_returns_nonempty_fallback(self) -> None:
        repository = _Repository()
        gate = _PrecisionSopGate()
        runtime = ChatRuntime(
            full_graph=_ErrorGraph(),
            planner_graph=_ErrorGraph(),
            trace_logger=_TraceLogger(),
            repository=repository,
            sop_execution_service=gate,
        )

        response = await runtime.run_platform_reply(_request("是不是做一次就可以"))

        self.assertEqual(len(response.reply_messages), 1)
        self.assertTrue(response.reply_messages[0].content.get("text"))
        self.assertNotEqual(response.reply_messages[0].content["text"], "我在，继续帮您处理。")
        self.assertFalse(gate.confirmed)
        self.assertTrue(gate.failed)

    async def test_finalize_empty_reply_is_recovered_and_returned_synchronously(self) -> None:
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

        response = await runtime.run_platform_reply(_request("集美附近门店帮我看下"))

        self.assertEqual(len(response.reply_messages), 1)
        self.assertEqual(response.reply_messages[0].type, "text")
        self.assertFalse(outreach.sent.is_set())
        saved = repository.saved_states[-1]
        self.assertEqual(saved["async_final_reply"]["status"], "completed_sync")
        self.assertEqual(saved["reply_control"]["sync_return"]["type"], "final_reply")
        self.assertEqual(saved["reply_source"], "deterministic_sync_empty_reply_fallback")

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

    async def test_trace_file_preserves_model_prompt_raw_json_and_recovery(self) -> None:
        prompt = "模型系统规则" * 3000
        raw_output = {"decision": "direct_reply", "detail": "输出事实" * 2000}
        with tempfile.TemporaryDirectory() as directory:
            logger = FileTraceLogger(Settings(log_dir=Path(directory)))
            state = {
                "request_id": "trace-model-details",
                "trace": [
                    {
                        "node": "planner_brain",
                        "tool_calls": [
                            {
                                "name": "planner_brain_v2",
                                "input": {"messages": [{"role": "system", "content": prompt}]},
                                "raw_json_output": raw_output,
                                "usage": {"winner_model": "gpt-5.4", "attempts": 2},
                                "recovery": {
                                    "name": "planner_brain_timeout_retry",
                                    "input": {"messages": [{"role": "system", "content": "compact recovery"}]},
                                    "raw_json_output": {"decision": "direct_reply"},
                                },
                            }
                        ],
                    }
                ],
            }
            saved = json.loads(logger.write_run(state).read_text(encoding="utf-8"))

        model_call = saved["trace"][0]["tool_calls"][0]
        self.assertEqual(model_call["input"]["messages"][0]["content"], prompt)
        self.assertEqual(model_call["raw_json_output"], raw_output)
        self.assertEqual(model_call["recovery"]["raw_json_output"], {"decision": "direct_reply"})

    async def test_finalize_exception_is_recovered_and_returned_synchronously(self) -> None:
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

        response = await runtime.run_platform_reply(_request("集美附近门店帮我看下"))

        self.assertEqual(len(response.reply_messages), 1)
        self.assertEqual(response.reply_messages[0].type, "text")
        self.assertFalse(outreach.sent.is_set())
        recovered = [state for state in repository.saved_states if state.get("reply_source") == "deterministic_runtime_exception_fallback"]
        self.assertTrue(recovered)
        self.assertEqual(recovered[-1]["async_final_reply"]["status"], "error_recovered_sync")

    def test_ai_then_sop_merge_keeps_structural_material_without_sop_text_stack(self) -> None:
        ai_messages = [
            {"type": "text", "order": 1, "content": {"text": "answer current question"}},
            {"type": "store_address", "order": 2, "content": {"store_id": "216"}},
            {"type": "text", "order": 3, "content": {"text": "closing action"}},
        ]
        sop_messages = [
            {"type": "text", "order": 1, "content": {"text": "fixed sop explanation"}},
            {"type": "image", "order": 2, "content": {"url": "https://example.invalid/a.jpg"}},
            {"type": "image", "order": 3, "content": {"url": "https://example.invalid/b.jpg"}},
            {"type": "image", "order": 4, "content": {"url": "https://example.invalid/c.jpg"}},
            {"type": "text", "order": 5, "content": {"text": "fixed sop question"}},
        ]

        merged = _merge_ai_then_sop_reply_messages(ai_messages, sop_messages)

        self.assertEqual([message["type"] for message in merged], ["text", "store_address", "image", "image", "image", "text"])
        self.assertNotIn("fixed sop explanation", json.dumps(merged))
        self.assertNotIn("fixed sop question", json.dumps(merged))
        self.assertEqual([message["order"] for message in merged], [1, 2, 3, 4, 5, 6])

    def test_ai_then_sop_merge_keeps_only_latest_ai_payment_card(self) -> None:
        ai_messages = [
            {"type": "text", "order": 1, "content": {"text": "按您一位先留名额"}},
            {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
        ]
        sop_messages = [
            {"type": "text", "order": 1, "content": {"text": "固定活动说明"}},
            {"type": "payment_collection", "order": 2, "content": {"amount": 20}},
            {"type": "image", "order": 3, "content": {"url": "https://example.invalid/activity.jpg"}},
        ]

        merged = _merge_ai_then_sop_reply_messages(ai_messages, sop_messages)

        cards = [message for message in merged if message["type"] == "payment_collection"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["content"]["amount"], 10)
        self.assertEqual([message["order"] for message in merged], list(range(1, len(merged) + 1)))


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


class _OpeningUnifiedGraph:
    def __init__(self) -> None:
        self.called = False

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        self.called = True
        output = dict(state)
        messages = list(state.get("sop_gate_candidate_messages") or [])
        output.update(
            {
                "planner_decision": "direct_reply",
                "planner_stage": "S1",
                "reply_source": "reply_synthesizer",
                "planner_reply_messages": messages,
                "reply_messages": messages,
                "trace": [],
                "errors": [],
            }
        )
        return output


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
            "delivery_mode": "configured_passthrough",
            "send_sop": True,
            "need_ai_reply": False,
            "reason": "platform_auto_opening_first_add_sop",
            "sop_pack_id": "s10_new_customer_opening",
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "新客破冰话术"}},
                {
                    "type": "image",
                    "order": 2,
                    "content": {"url": "https://example.com/opening.png"},
                },
            ],
        }


class _PrecisionSopGate:
    def __init__(self) -> None:
        self.confirmed = False
        self.failed = False

    async def evaluate_chat_gate(
        self,
        request: ChatRequest,
        *,
        request_id: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "mode": "ai_then_sop",
            "route": "ai_then_sop",
            "coverage": "partial",
            "priority_question_id": "one_session_effect",
            "resume_stage": "need_and_case",
            "send_sop": True,
            "need_ai_reply": True,
            "sop_pack_id": "s10_need_and_case",
            "reply_messages": [
                {
                    "type": "image",
                    "order": 2,
                    "content": {"url": "https://example.invalid/sop-case.jpg"},
                },
                {"type": "text", "order": 1, "content": {"text": "我给您发一组同类案例参考。"}}
            ],
            "task": {"id": "pending-task", "status": "pending", "created": True},
            "sop_progress_evidence": {},
        }

    def confirm_chat_gate_task_sent(self, task: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.confirmed = True
        return {**task, "status": "sent"}

    def fail_chat_gate_task(self, task: dict[str, Any], *, error: str) -> dict[str, Any]:
        self.failed = True
        return {**task, "status": "failed", "error": error}


class _PrecisionReplyGraph:
    def __init__(self) -> None:
        self.states: list[dict[str, Any]] = []

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        self.states.append(dict(state))
        output = dict(state)
        candidate_messages = list(state.get("sop_gate_candidate_messages") or [])
        final_messages = [
            {
                "type": "text",
                "order": 1,
                "content": {"text": "多数客户做一次就能看到改善，具体程度要到店检测后判断。"},
            },
            *candidate_messages,
        ]
        output.update(
            {
                "planner_decision": "direct_reply",
                "reply_source": "reply_synthesizer",
                "planner_reply_messages": final_messages,
                "reply_messages": final_messages,
                "authorized_sop_delivery_manifest": {
                    **dict(state.get("sop_delivery_manifest") or {}),
                    "active": True,
                    "delivery_decision": {"action": "deliver_now"},
                },
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


class _OutreachSendClient:
    def __init__(self) -> None:
        self.sent = asyncio.Event()
        self.reply_messages: list[dict[str, Any]] = []

    async def send_reply_messages(self, **kwargs: Any) -> dict[str, Any]:
        self.reply_messages = list(kwargs.get("reply_messages") or [])
        self.sent.set()
        return {"status": "sent"}


def _request(content: str, *, msgid: str = "") -> ChatRequest:
    return ChatRequest(
        content=content,
        customer_id="customer",
        corp_id="corp",
        conversation_history=[],
        external_userid="ext",
        wechat="DY258",
        request_context={"msgid": msgid} if msgid else {},
    )


def _settings_with_empty_filter(testcase: unittest.TestCase) -> Settings:
    directory = tempfile.TemporaryDirectory()
    testcase.addCleanup(directory.cleanup)
    path = Path(directory.name) / "platform_filter_words.json"
    path.write_text(json.dumps({"enabled": True, "match_mode": "contains", "words": []}), encoding="utf-8")
    return Settings(platform_filter_words_path=path)


if __name__ == "__main__":
    unittest.main()
