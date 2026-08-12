from __future__ import annotations

import tempfile
import time
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


class FakeStoreAddressRepairTwiceModelClient:
    available = True

    def __init__(self) -> None:
        self.calls = 0
        self.tiers: list[str] = []

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
        self.calls += 1
        self.tiers.append(tier)
        if self.calls == 1:
            return {
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "好的，我发您昆明门店地址，您点开导航就可以。"}}
                ]
            }
        if self.calls == 2:
            return {
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "好的，我把地址发您，您看下哪家更方便。"}}
                ]
            }
        return {
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "昆明这边有几家，您明天更方便到哪个区或常去哪个片区？我先按您方便的区域给您匹配。"}}
            ]
        }


class FakeAlwaysFailReplyModelClient:
    available = True

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
        raise RuntimeError("Model HTTP 502: upstream unavailable")


class FakeIncompleteActivityModelClient:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
        self.calls += 1
        return {
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "活动价268元，线上名额有限。"}},
                {"type": "image", "order": 2, "content": {"url": "https://example.test/activity.png"}},
                {"type": "text", "order": 3, "content": {"text": "我接着给您说明。"}},
            ]
        }


class FakeNearbyStoreActivityRepairModelClient:
    available = True

    def __init__(self) -> None:
        self.calls = 0
        self.retry_messages: list[dict[str, Any]] = []

    async def chat_json(self, messages: list[dict[str, Any]], *, tier: str) -> dict[str, Any]:
        self.calls += 1
        activity_messages = [
            {
                "type": "text",
                "order": 1,
                "content": {
                    "text": "现在是线上活动，前30名登记到店可以享受268元淡斑活动，包含淡斑、检测皮肤、基础清洁和肌肤补水。"
                },
            },
            {"type": "image", "order": 2, "content": {"url": "https://example.test/activity.png"}},
            {
                "type": "text",
                "order": 3,
                "content": {
                    "text": "线上每位先付10元登记，到店抵扣，做的话再付258元；未做或不满意可退，实际按付款记录核对。线上名额有限，到店时间按您方便安排。"
                },
            },
        ]
        if self.calls == 1:
            return {
                "reply_messages": [
                    *activity_messages,
                    {
                        "type": "text",
                        "order": 4,
                        "content": {"text": "您把地址发我，我再帮您看附近门店。"},
                    },
                ]
            }
        self.retry_messages = messages
        return {
            "reply_messages": [
                *activity_messages,
                {
                    "type": "text",
                    "order": 4,
                    "content": {"text": "您在哪个城市或区域？我再按这个范围帮您看门店。"},
                },
            ]
        }


class ReplySynthRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_direct_reply_preserves_authorized_manifest_images_when_model_unavailable(self) -> None:
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
                "request_id": "test-planner-direct-image-preserved",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "这个活动多少钱",
                "normalized_content": "这个活动多少钱",
                "planner_decision": "direct_reply",
                "planner_reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "亲，我把活动完整发您看下。"}},
                    {"type": "image", "order": 2, "content": {"url": "https://example.test/activity.png"}},
                    {"type": "text", "order": 3, "content": {"text": "到店时间可以按您方便安排。"}},
                ],
                "fact_envelope": {},
                "required_tools": [],
                "authorized_sop_delivery_manifest": {
                    "active": True,
                    "core_fact_contract": "",
                    "messages": [
                        {
                            "source_order": 1,
                            "message_type": "text",
                            "required": True,
                            "content": {"text": "亲，我把活动完整发您看下。"},
                        },
                        {
                            "source_order": 2,
                            "message_type": "image",
                            "required": True,
                            "content": {"url": "https://example.test/activity.png"},
                        },
                        {
                            "source_order": 3,
                            "message_type": "text",
                            "required": True,
                            "content": {"text": "到店时间可以按您方便安排。"},
                        },
                    ],
                },
                "reply_contract": {
                    "required_deliveries": [
                        {"message_type": "text"},
                        {"message_type": "image"},
                        {"message_type": "text"},
                    ]
                },
            }

            output = await node(state)

        self.assertFalse(output["reply_blocked"])
        self.assertEqual(output["reply_source"], "planner_direct_reply_model_unavailable_fallback")
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text", "image", "text"])
        self.assertEqual(output["reply_messages"][1]["content"], "https://example.test/activity.png")

    async def test_nearby_store_repair_preserves_authorized_activity_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model = FakeNearbyStoreActivityRepairModelClient()
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
            manifest_messages = [
                {
                    "source_order": 1,
                    "message_type": "text",
                    "required": True,
                    "content": {"text": "完整活动介绍"},
                },
                {
                    "source_order": 2,
                    "message_type": "image",
                    "required": True,
                    "content": {"url": "https://example.test/activity.png"},
                },
                {
                    "source_order": 3,
                    "message_type": "text",
                    "required": True,
                    "content": {"text": "活动补充说明"},
                },
            ]
            state: dict[str, Any] = {
                "request_id": "test-nearby-store-activity-repair",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "介绍一下活动",
                "normalized_content": "介绍一下活动",
                "planner_decision": "direct_reply",
                "planner_reply_messages": [],
                "fact_envelope": {},
                "required_tools": [],
                "authorized_sop_delivery_manifest": {
                    "active": True,
                    "core_fact_contract": "activity_intro_v1",
                    "messages": manifest_messages,
                },
                "reply_contract": {
                    "required_deliveries": [
                        {"message_type": item["message_type"]}
                        for item in manifest_messages
                    ]
                },
            }

            output = await node(state)

        self.assertEqual(model.calls, 2)
        self.assertFalse(output.get("reply_blocked"))
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text", "image", "text", "text"])
        self.assertNotIn("附近门店", "".join(str(item.get("content") or "") for item in output["reply_messages"]))
        retry_context = "\n".join(str(item.get("content") or "") for item in model.retry_messages)
        self.assertIn("authorized_sop_delivery_manifest", retry_context)
        self.assertIn("只最小修改违规收尾", retry_context)
        self.assertEqual(output["reply_review"]["repair_attempts"], 1)

    async def test_exhausted_contract_repairs_remain_visible_in_review_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model = FakeIncompleteActivityModelClient()
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
            manifest_messages = [
                {
                    "source_order": 1,
                    "message_type": "text",
                    "required": True,
                    "content": {"text": "完整活动介绍"},
                },
                {
                    "source_order": 2,
                    "message_type": "image",
                    "required": True,
                    "content": {"url": "https://example.test/activity.png"},
                },
                {
                    "source_order": 3,
                    "message_type": "text",
                    "required": True,
                    "content": {"text": "活动补充说明"},
                },
            ]
            state: dict[str, Any] = {
                "request_id": "test-activity-contract-repair-audit",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "这个活动怎么参加",
                "normalized_content": "这个活动怎么参加",
                "planner_decision": "direct_reply",
                "planner_reply_messages": [],
                "fact_envelope": {},
                "required_tools": [],
                "authorized_sop_delivery_manifest": {
                    "active": True,
                    "core_fact_contract": "activity_intro_v1",
                    "messages": manifest_messages,
                },
                "reply_contract": {
                    "required_deliveries": [
                        {"message_type": item["message_type"]}
                        for item in manifest_messages
                    ]
                },
            }

            output = await node(state)

        self.assertEqual(model.calls, 3)
        self.assertFalse(output["reply_blocked"])
        self.assertEqual(output["reply_source"], "deterministic_neutral_final_fallback")
        self.assertEqual(output["reply_messages"], [{"type": "text", "order": 1, "content": "您稍等一下"}])
        self.assertEqual(output["reply_review"]["repair_attempts"], 2)
        self.assertGreater(output["model_context_metrics"]["reply"]["message_count"], 0)

    async def test_model_failure_uses_valid_authorized_manifest_without_replanning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            node = create_synthesize_reply_node(
                trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
                model_client=FakeAlwaysFailReplyModelClient(),
                debug_message_contents=debug_message_contents,
                reply_messages_for_model=lambda _state: [
                    {"role": "system", "content": "output json"},
                    {"role": "user", "content": "{}"},
                ],
                should_use_model_reply=lambda _state: True,
                validated_model_messages=validated_model_messages,
            )
            manifest_messages = [
                {
                    "source_order": 1,
                    "message_type": "text",
                    "required": True,
                    "content": {"text": "现在把完整活动内容发您。"},
                },
                {
                    "source_order": 2,
                    "message_type": "image",
                    "required": True,
                    "content": {"url": "https://example.test/activity.png"},
                },
                {
                    "source_order": 3,
                    "message_type": "text",
                    "required": True,
                    "content": {"text": "到店时间按您方便安排。"},
                },
            ]
            state: dict[str, Any] = {
                "request_id": "test-authorized-manifest-fallback",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "先问问价格",
                "normalized_content": "先问问价格",
                "planner_decision": "direct_reply",
                "planner_reply_messages": [],
                "fact_envelope": {},
                "required_tools": [],
                "authorized_sop_delivery_manifest": {
                    "active": True,
                    "core_fact_contract": "",
                    "messages": manifest_messages,
                },
                "reply_contract": {
                    "required_deliveries": [
                        {"message_type": item["message_type"]}
                        for item in manifest_messages
                    ]
                },
            }

            output = await node(state)

        self.assertFalse(output["reply_blocked"])
        self.assertEqual(output["reply_source"], "deterministic_authorized_sop_manifest_fallback")
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text", "image", "text"])

    async def test_model_failure_uses_complete_activity_manifest_even_with_turn_question_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            node = create_synthesize_reply_node(
                trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
                model_client=FakeAlwaysFailReplyModelClient(),
                debug_message_contents=debug_message_contents,
                reply_messages_for_model=lambda _state: [
                    {"role": "system", "content": "output json"},
                    {"role": "user", "content": "{}"},
                ],
                should_use_model_reply=lambda _state: True,
                validated_model_messages=validated_model_messages,
            )
            manifest_messages = [
                {
                    "source_order": 1,
                    "message_type": "text",
                    "required": True,
                    "content": {
                        "text": (
                            "现在我们是线上抢购活动，前30名抢到的顾客到店可以享受268元淡斑特惠价，我把活动给您发一下。\n"
                            "抖音合作线上秒杀活动（淡斑套餐）\n"
                            "①268元活动价格仅限30名，套餐包括淡斑、检测皮肤、基础清洁和肌肤补水。"
                        )
                    },
                },
                {
                    "source_order": 2,
                    "message_type": "image",
                    "required": True,
                    "content": {"url": "https://example.test/activity.png"},
                },
                {
                    "source_order": 3,
                    "message_type": "text",
                    "required": True,
                    "content": {
                        "text": (
                            "②线上预定每位10元并登记姓名电话，到店抵扣10元，做的话再付258元；"
                            "未做或不满意可退，实际按付款记录核对。\n"
                            "③仅限线上报名客户有效，名额满活动结束并恢复原价；线下客户到店按原价。"
                            "预定后到店时间可以按您方便安排。"
                        )
                    },
                },
            ]
            state: dict[str, Any] = {
                "request_id": "test-complete-activity-manifest-with-turn-question",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "做这个要多少钱",
                "normalized_content": "做这个要多少钱",
                "planner_decision": "direct_reply",
                "planner_reply_messages": [],
                "fact_envelope": {},
                "required_tools": [],
                "authorized_sop_delivery_manifest": {
                    "active": True,
                    "core_fact_contract": "activity_intro_v1",
                    "required_fact_ids": [
                        "activity_price",
                        "package_items",
                        "quota",
                        "deposit_redemption",
                        "refund_policy",
                        "visit_flexibility",
                    ],
                    "messages": manifest_messages,
                },
                "reply_contract": {
                    "required_deliveries": [
                        {"message_type": item["message_type"]}
                        for item in manifest_messages
                    ],
                    "required_fact_ids": [
                        "turn_question_1",
                        "activity_price",
                        "package_items",
                        "quota",
                        "deposit_redemption",
                        "refund_policy",
                        "visit_flexibility",
                    ],
                },
                "governance_flags": {"semantic_contract_enabled": True},
            }

            output = await node(state)

        self.assertFalse(output["reply_blocked"])
        self.assertEqual(output["reply_source"], "deterministic_authorized_sop_manifest_fallback")
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text", "image", "text"])
        combined = "".join(str(item.get("content") or "") for item in output["reply_messages"])
        self.assertIn("268", combined)
        self.assertIn("到店抵扣", combined)

    async def test_low_round_budget_skips_primary_and_runs_compact_recovery(self) -> None:
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
            now = time.monotonic()
            state: dict[str, Any] = {
                "request_id": "test-low-budget-compact-recovery",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "行",
                "normalized_content": "行",
                "planner_decision": "direct_reply",
                "planner_reply_messages": [],
                "fact_envelope": {},
                "required_tools": [],
                "runtime_budget": {
                    "mode": "enforced",
                    "enforced": True,
                    "started_monotonic": now - 50.0,
                    "ordinary_deadline_monotonic": now + 8.5,
                    "strong_deadline_monotonic": now + 8.5,
                    "min_retry_remaining_seconds": 8.0,
                },
            }

            output = await node(state)

        self.assertEqual(model.calls, 1)
        self.assertEqual(output["reply_source"], "compact_recovery_model")
        trace_entry = next(item for item in output["trace"] if item.get("node") == "synthesize_reply")
        model_call = trace_entry["tool_calls"][0]
        self.assertEqual(
            model_call["primary"]["status"],
            "skipped_to_preserve_compact_recovery_budget",
        )

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

    async def test_reply_model_failure_can_use_valid_planner_draft_with_non_visible_tool_policy_violations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            node = create_synthesize_reply_node(
                trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
                model_client=FakeAlwaysFailReplyModelClient(),
                debug_message_contents=debug_message_contents,
                reply_messages_for_model=lambda _state: [
                    {"role": "system", "content": "output json"},
                    {"role": "user", "content": "{}"},
                ],
                should_use_model_reply=lambda _state: True,
                validated_model_messages=validated_model_messages,
            )
            state: dict[str, Any] = {
                "request_id": "test-direct-fallback-with-policy-warning",
                "trace": [],
                "errors": [],
                "warnings": [],
                "planner_decision": "direct_reply",
                "planner_reply_messages": [
                    {
                        "type": "text",
                        "order": 1,
                        "content": {"text": "收到亲，姓名和电话我看到了。您大概想今天、明天还是周末到店？"},
                    }
                ],
                "tool_policy_violations": [
                    {
                        "task_type": "transaction_consistency",
                        "missing": "accepted_implicit_requires_eligible_store_anchor_fact",
                    }
                ],
                "fact_envelope": {},
                "required_tools": [],
            }

            output = await node(state)

        self.assertEqual(output["reply_source"], "planner_direct_reply_after_model_failure")
        self.assertEqual(output["errors"], [])
        self.assertEqual(output["reply_messages"][0]["content"]["text"], "收到亲，姓名和电话我看到了。您大概想今天、明天还是周末到店？")
        self.assertTrue(
            any(
                item.get("message") == "planner_direct_reply_used_despite_non_visible_tool_policy_violations"
                for item in output["warnings"]
            )
        )

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

    async def test_reply_synth_uses_multiple_targeted_repairs_before_compact_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model = FakeStoreAddressRepairTwiceModelClient()
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
                "request_id": "test-store-address-multi-repair",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "发个昆明地址",
                "normalized_content": "发个昆明地址",
                "planner_decision": "need_tools",
                "fact_envelope": {
                    "structured_facts": {
                        "store_facts": [
                            {"store_id": "119", "store_name": "昆明五华店", "city": "昆明市", "district": "五华区"},
                            {"store_id": "169", "store_name": "昆明官渡店", "city": "昆明市", "district": "官渡区"},
                            {"store_id": "175", "store_name": "昆明五华二店", "city": "昆明市", "district": "五华区"},
                            {"store_id": "535", "store_name": "昆明呈贡店", "city": "昆明市", "district": "呈贡区"},
                        ]
                    }
                },
                "required_tools": [{"name": "customer_store_lookup", "query": "昆明", "purpose": "existence"}],
            }

            output = await node(state)

        self.assertEqual(model.calls, 3)
        self.assertEqual(model.tiers, ["reply", "reply", "reply"])
        self.assertEqual(output["reply_source"], "main_model")
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text"])
        self.assertIn("哪个区", output["reply_messages"][0]["content"])
        tool_call = state["trace"][0]["tool_calls"][0]
        self.assertIn("repair_retries", tool_call)
        self.assertEqual(len(tool_call["repair_retries"]), 2)
        self.assertNotIn("recovery", tool_call)

    async def test_reply_synth_keeps_payment_card_after_work_order_rejection(self) -> None:
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
                "sop_progress_evidence": {
                    "completed_pack_ids": ["s10_activity_intro"],
                    "completed_categories": ["activity_intro"],
                },
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

        self.assertEqual(model.calls, 1)
        self.assertEqual(output["errors"], [])
        self.assertEqual(output["reply_source"], "main_model")
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text", "payment_collection"])
        self.assertEqual(model.retry_messages, [])

    async def test_reply_synth_accepts_natural_handoff_wording_without_literal_retry(self) -> None:
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

        self.assertEqual(model.calls, 1)
        self.assertEqual(model.tiers, ["strong"])
        self.assertEqual(output["errors"], [])
        self.assertEqual(output["reply_source"], "main_model")
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text", "human_handoff_notice"])
        text = output["reply_messages"][0]["content"]
        self.assertIn("专业顾问", text)
        self.assertIn("专人联系", text)
        self.assertNotIn("recovery", state["trace"][0]["tool_calls"][0])

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
        self.assertEqual(
            output["reply_messages"][0]["content"],
            "您稍等一下",
        )

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
        self.assertEqual(
            output["reply_messages"][0]["content"],
            "您稍等一下",
        )

    async def test_reply_failure_does_not_turn_tool_candidates_into_store_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            node = create_synthesize_reply_node(
                trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
                model_client=FakeAlwaysFailReplyModelClient(),
                debug_message_contents=debug_message_contents,
                reply_messages_for_model=lambda _state: [
                    {"role": "system", "content": "output json"},
                    {"role": "user", "content": "{}"},
                ],
                should_use_model_reply=lambda _state: True,
                validated_model_messages=validated_model_messages,
            )
            state: dict[str, Any] = {
                "request_id": "test-store-resolution-fallback",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "我在武平，店在哪里",
                "normalized_content": "我在武平，店在哪里",
                "planner_decision": "need_tools",
                "planner_reply_messages": [],
                "fact_envelope": {
                    "structured_facts": {
                        "store_lookup_status": {
                            "status": "ok",
                            "raw_query": "武平",
                            "query": "武平",
                            "resolved_admin_level": "district",
                            "scope_match_level": "city_fallback",
                            "exact_scope_has_store": False,
                            "candidate_count": 1,
                        },
                        "store_resolution_fact": {
                            "raw_place": "武平",
                            "delivery_mode": "send_all_candidates",
                            "visible_candidate_ids": ["321"],
                        },
                        "store_facts": [
                            {
                                "store_id": "321",
                                "store_name": "龙岩新罗店",
                                "city": "龙岩市",
                                "district": "新罗区",
                                "store_address": "龙岩市新罗区西陂街道龙岩大道326号水晶兰天商务楼",
                                "store_fact_integrity": "valid",
                                "scope_authorized": True,
                            }
                        ],
                    }
                },
                "customer_store_knowledge": {
                    "stores": [
                        {
                            "store_id": "321",
                            "store_name": "龙岩新罗店",
                            "city": "龙岩市",
                            "district": "新罗区",
                        }
                    ]
                },
                "required_tools": [{"name": "customer_store_lookup", "purpose": "existence", "query": "武平"}],
            }

            output = await node(state)

        self.assertEqual(output["errors"], [])
        self.assertEqual(output["reply_source"], "deterministic_neutral_final_fallback")
        self.assertEqual([item["type"] for item in output["reply_messages"]], ["text"])

    async def test_store_lookup_timeout_uses_safe_area_question_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            node = create_synthesize_reply_node(
                trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
                model_client=FakeAlwaysFailReplyModelClient(),
                debug_message_contents=debug_message_contents,
                reply_messages_for_model=lambda _state: [
                    {"role": "system", "content": "output json"},
                    {"role": "user", "content": "{}"},
                ],
                should_use_model_reply=lambda _state: True,
                validated_model_messages=validated_model_messages,
            )
            state: dict[str, Any] = {
                "request_id": "test-store-lookup-timeout-fallback",
                "trace": [],
                "errors": [],
                "warnings": [],
                "content": "你们在哪里",
                "normalized_content": "你们在哪里",
                "planner_decision": "need_tools",
                "planner_reply_messages": [],
                "fact_envelope": {
                    "structured_facts": {
                        "store_resolution_fact": {
                            "status": "need_location",
                            "delivery_store_ids": [],
                        }
                    }
                },
                "customer_store_knowledge": {"error": "timeout_after_5s"},
                "required_tools": [{"name": "customer_store_lookup", "purpose": "location"}],
                "reply_contract": {
                    "required_deliveries": [{"message_type": "store_address"}],
                    "required_fact_ids": ["turn_question_1"],
                },
            }

            output = await node(state)

        self.assertFalse(output["reply_blocked"])
        self.assertEqual(output["reply_source"], "deterministic_store_lookup_unavailable_fallback")
        self.assertEqual(output["reply_messages"], [
            {
                "type": "text",
                "order": 1,
                "content": "亲，您现在在哪个城市或哪个区县呢？我按您方便过去的区域给您看门店位置。",
            }
        ])

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
        self.assertEqual(
            "您稍等一下",
            output["reply_messages"][0]["content"],
        )

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
        self.assertEqual(output["reply_source"], "main_model")
        text = "\n".join(item["content"] for item in output["reply_messages"] if item["type"] == "text")
        self.assertIn("明天到店的意向我记下了", text)
        self.assertNotIn("可以约", text)
        self.assertNotIn("安排好", text)


if __name__ == "__main__":
    unittest.main()
