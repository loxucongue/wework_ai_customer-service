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
        self.tiers: list[str] = []

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
        self.calls += 1
        self.tiers.append(tier)
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
        self.tiers: list[str] = []

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
        self.calls += 1
        self.tiers.append(tier)
        if self.calls >= 3:
            return {
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "我在的，您把具体想核对的情况发我，我先把信息记录清楚。"}},
                    {"type": "human_handoff_notice", "order": 2, "content": {"handoff_reason": "客户要求人工处理"}},
                ]
            }
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


class FakePaymentExceptionModelClient:
    available = True

    def __init__(self) -> None:
        self.tiers: list[str] = []

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
        self.tiers.append(tier)
        return {
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "我先帮您核对这笔付款异常。您把付款时间、金额和付款方式发我一下。"},
                }
            ]
        }


class FakeUnavailableAppointmentModelClient:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return {
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "可以按明天到店检测来安排，厦门思明店这边先以门店现场确认为准。"}},
                    {"type": "text", "order": 2, "content": {"text": "您明天想上午还是下午去？"}},
                ]
            }
        if self.calls == 2:
            return {
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "明天去可以先安排到店检测，不过这边暂时没查到实时档期。"}},
                    {"type": "text", "order": 2, "content": {"text": "您明天想上午去还是下午去？"}},
                ]
            }
        return {
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "明天到店的意向我记下了。您更方便上午还是下午？"}},
            ]
        }


class FakeRejectedOrderRepairModelClient:
    available = True

    def __init__(self) -> None:
        self.calls = 0
        self.retry_messages: list[dict[str, Any]] = []

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return {
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "付款卡我再发您。"}},
                    {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
                ]
            }
        self.retry_messages = messages
        return {
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "可以的，您先订机票，这边按厦门百星湖里店继续给您核对预约入口。"},
                }
            ]
        }


class FakeFinalReplyModelClient:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
        self.calls += 1
        return {
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "这是最终回复模型生成的成品。"}}
            ]
        }


class ReplySynthRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_planner_direct_reply_still_uses_final_reply_model_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model = FakeFinalReplyModelClient()
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
                "request_id": "test-direct-final-model",
                "trace": [],
                "errors": [],
                "warnings": [],
                "planner_decision": "direct_reply",
                "planner_reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "这是 Planner 草稿。"}}
                ],
                "fact_envelope": {},
                "required_tools": [],
            }

            output = await node(state)

        self.assertEqual(model.calls, 1)
        self.assertEqual(output["reply_source"], "main_model")
        self.assertEqual(output["reply_messages"][0]["content"], "这是最终回复模型生成的成品。")

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
        self.assertEqual(model.tiers, ["reply", "reply"])
        self.assertEqual(output["errors"], [])
        self.assertEqual(output["reply_messages"][0]["content"], "我帮您核对一下更方便的门店。")
        retry_info = state["trace"][0]["tool_calls"][0].get("retry")
        self.assertIsInstance(retry_info, dict)
        self.assertIn("Model JSON missing reply_messages", retry_info.get("reason", ""))

    async def test_reply_synth_removes_payment_card_after_work_order_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model = FakeRejectedOrderRepairModelClient()
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
                "request_id": "test-order-rejected-repair",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "付款卡再发我一下",
                "normalized_content": "付款卡再发我一下",
                "planner_decision": "need_tools",
                "payment_action": "send_now",
                "payment_decision": {"action": "send_now", "amount": 10},
                "order_decision": {"action": "create_work", "store_id": "386"},
                "current_known_store": {"store_id": "386", "store_name": "厦门百星湖里店"},
                "fact_envelope": {
                    "structured_facts": {
                        "order_facts": [{"type": "work_order", "status": "rejected", "source": "platform_agent.order.check_customer"}],
                    }
                },
                "required_tools": [{"name": "create_work_order", "store_id": "386"}],
            }

            output = await node(state)

        self.assertEqual(model.calls, 2)
        self.assertEqual(output["errors"], [])
        self.assertEqual(output["reply_source"], "main_model")
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text"])
        self.assertNotEqual(model.retry_messages, [])

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

        self.assertEqual(model.calls, 3)
        self.assertEqual(model.tiers, ["strong", "strong", "fast"])
        self.assertEqual(output["errors"], [])
        self.assertEqual(output["reply_source"], "compact_recovery_model")
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text", "human_handoff_notice"])
        text = output["reply_messages"][0]["content"]
        self.assertIn("信息记录清楚", text)
        self.assertNotIn("专业顾问", text)
        self.assertNotIn("专人联系", text)
        self.assertNotIn("同步处理", text)
        recovery_info = state["trace"][0]["tool_calls"][0].get("recovery")
        self.assertIsInstance(recovery_info, dict)
        self.assertEqual(recovery_info.get("tier"), "fast")

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
        self.assertEqual(output["reply_source"], "deterministic_neutral_final_fallback")
        self.assertEqual(output["reply_messages"][0]["content"], "我在，继续帮您处理。")

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
        self.assertEqual(output["reply_source"], "deterministic_neutral_final_fallback")
        self.assertEqual(output["reply_messages"][0]["content"], "我在，继续帮您处理。")

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
        self.assertEqual(output["reply_source"], "deterministic_neutral_final_fallback")
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text", "human_handoff_notice"])
        self.assertEqual("我在，继续帮您处理。", output["reply_messages"][0]["content"])

    async def test_current_professional_assist_notice_is_not_removed_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model = FakePaymentExceptionModelClient()
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
                "request_id": "test-current-professional-assist",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "我付款扣了但是显示没成功",
                "normalized_content": "我付款扣了但是显示没成功",
                "planner_decision": "need_tools",
                "handoff": {"needed": True, "reason": "payment failure / payment exception"},
                "required_tools": [{"name": "professional_assist", "purpose": ""}],
                "tool_results": {
                    "professional_assist": {
                        "status": "requested",
                        "reason": "payment failure / payment exception",
                    }
                },
                "fact_envelope": {
                    "structured_facts": {
                        "professional_assist": {
                            "status": "requested",
                            "reason": "payment failure / payment exception",
                        }
                    }
                },
            }

            output = await node(state)

        self.assertEqual(output["errors"], [])
        self.assertEqual(model.tiers, ["strong"])
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text", "human_handoff_notice"])
        self.assertTrue(any(item.get("message") == "handoff_notice_appended" for item in output["warnings"]))
        self.assertFalse(any(item.get("message") == "stale_handoff_notice_removed" for item in output["warnings"]))

    async def test_unavailable_available_time_gets_visible_fact_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model = FakeUnavailableAppointmentModelClient()
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
                "request_id": "test-unavailable-time-fallback",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "明天可以去吗",
                "normalized_content": "明天可以去吗",
                "planner_decision": "need_tools",
                "required_tools": [{"name": "available_time", "store_id": "12", "date": "2026-07-10"}],
                "fact_envelope": {
                    "structured_facts": {
                        "appointment_facts": [
                            {
                                "type": "available_time",
                                "store": "厦门思明店",
                                "date": "2026-07-10",
                                "recommended_slot": "",
                                "backup_slots": [],
                                "slot_count": 0,
                            }
                        ],
                        "tool_errors": [{"tool": "available_time", "error": "platform error"}],
                    }
                },
            }

            output = await node(state)

        self.assertEqual(model.calls, 3)
        self.assertEqual(output["errors"], [])
        self.assertEqual(output["reply_source"], "compact_recovery_model")
        text = "\n".join(item["content"] for item in output["reply_messages"] if item["type"] == "text")
        self.assertIn("明天到店的意向我记下了", text)
        self.assertNotIn("可以约", text)
        self.assertNotIn("安排好", text)


if __name__ == "__main__":
    unittest.main()
