from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.services.outreach_send_client import OutreachSendClient
from app.services.outreach_service import OutreachService
from app.services.storage import AppRepository, SQLiteStore
from app.services.customer_scope import build_customer_scope


class OutreachAutoSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_day_legacy_payment_card_is_removed_before_send(self) -> None:
        repository = _ExecutionRepository(
            order_status="no_order",
            trigger_type="first_day_opened_silence",
        )
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )
        service._generate_task_messages = AsyncMock(
            return_value=[
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "亲，可以微信转账或发10元红包先预约。"},
                },
                {
                    "type": "payment_collection",
                    "order": 2,
                    "content": {"amount": 10, "remark": ""},
                },
            ]
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(system.sent), 1)
        self.assertEqual(
            system.sent[0]["reply_messages"],
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "亲，可以微信转账或发10元红包先预约。"},
                }
            ],
        )
        self.assertIn(
            "first_day_payment_card_removed",
            [event["event_type"] for event in repository.events],
        )

    async def test_first_day_legacy_payment_card_only_task_is_blocked(self) -> None:
        repository = _ExecutionRepository(
            order_status="no_order",
            trigger_type="first_day_opened_silence",
        )
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )
        service._generate_task_messages = AsyncMock(
            return_value=[
                {
                    "type": "payment_collection",
                    "order": 1,
                    "content": {"amount": 10, "remark": ""},
                }
            ]
        )

        result = await service.execute_task("task-1")

        self.assertEqual(
            result,
            {
                "ok": True,
                "status": "skipped",
                "reason": "first_day_payment_card_only_task_blocked",
            },
        )
        self.assertEqual(system.sent, [])

    async def test_unanswered_payment_card_is_not_sent_by_outreach(self) -> None:
        repository = _ExecutionRepository(
            order_status="no_order",
            recent_messages=[
                {"role": "user", "content": "我考虑一下"},
                {
                    "role": "assistant",
                    "reply_messages": [
                        {"type": "payment_collection", "content": {"amount": 10, "remark": ""}}
                    ],
                    "created_at": "2026-08-09T10:00:00+08:00",
                },
            ],
        )
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )
        service._generate_task_messages = AsyncMock(
            return_value=[
                {"type": "text", "order": 1, "content": {"text": "您点卡片把名额锁住。"}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ]
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result, {"ok": True, "status": "skipped", "reason": "unanswered_payment_card_duplicate"})
        self.assertEqual(system.sent, [])
        self.assertIn(("task-1", "skipped"), repository.task_statuses)
        self.assertEqual(repository.events[-1]["event_type"], "task_skipped_unanswered_payment_card")

    async def test_fresh_platform_payment_card_wins_over_stale_local_context(self) -> None:
        repository = _ExecutionRepository(order_status="no_order", recent_messages=[])
        system = _SystemClient(
            messages=[
                {
                    "direction": "staff",
                    "sender_type": "staff",
                    "msgtype": "payment_collection",
                    "content": {"amount": 10},
                    "created_at": "2026-08-09T10:00:00+08:00",
                }
            ]
        )
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )
        service._generate_task_messages = AsyncMock(
            return_value=[
                {"type": "text", "order": 1, "content": {"text": "您点卡片把名额锁住。"}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ]
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["reason"], "unanswered_payment_card_duplicate")
        self.assertEqual(system.sent, [])

    async def test_auto_approved_task_sends_after_fresh_conversation_and_order_checks(self) -> None:
        repository = _ExecutionRepository(order_status="no_order", remaining_tasks=True)
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=_MessageModelClient(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(system.sent), 1)
        self.assertIn(("task-1", "sent"), repository.task_statuses)
        self.assertIn(("plan-1", "waiting"), repository.plan_statuses)

    async def test_final_task_completes_the_cycle_instead_of_waiting_forever(self) -> None:
        repository = _ExecutionRepository(order_status="no_order", remaining_tasks=False)
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=_MessageModelClient(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-final")

        self.assertEqual(result["status"], "sent")
        self.assertIn(("plan-1", "completed"), repository.plan_statuses)
        self.assertEqual(repository.events[-1]["event_type"], "plan_cycle_completed")

    async def test_terminal_send_contract_failure_cancels_plan(self) -> None:
        repository = _ExecutionRepository(order_status="no_order")
        system = _SystemClient(
            send_error=RuntimeError(
                "outreach_system_http_422: contract validation failed: "
                "reply_messages must not be empty"
            )
        )
        service = OutreachService(
            repository=repository,
            model_client=_MessageModelClient(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "failed")
        self.assertIn(("task-1", "failed"), repository.task_statuses)
        self.assertIn(("plan-1", "cancelled"), repository.plan_statuses)
        self.assertEqual(repository.skipped_remaining[-1]["reason"], "send_contract_validation_failed")
        self.assertEqual(repository.outreach_state_updates[-1]["outreach_status"], "cancelled")
        self.assertEqual(repository.outreach_state_updates[-1]["outreach_plan_id"], "")
        self.assertEqual(repository.events[-1]["event_type"], "task_failed_terminal")

    async def test_auto_approved_task_is_skipped_when_order_became_booked(self) -> None:
        repository = _ExecutionRepository(order_status="no_order")
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(
                orders=[{"id": "order-1", "status": "waiting_schedule", "is_current_order": True}]
            ),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result, {"ok": True, "status": "skipped", "reason": "order_state_changed"})
        self.assertEqual(system.sent, [])
        self.assertIn(("plan-1", "cancelled"), repository.plan_statuses)
        self.assertEqual(
            repository.skipped_remaining,
            [
                {
                    "plan_id": "plan-1",
                    "reason": "customer_order_state_changed",
                    "exclude_task_id": "task-1",
                }
            ],
        )
        self.assertEqual(repository.events[-1]["event_type"], "task_skipped_order_state_changed")

    async def test_customer_reply_cancels_the_remaining_plan(self) -> None:
        repository = _ExecutionRepository(order_status="no_order")
        system = _SystemClient(
            messages=[
                {
                    "direction": "customer",
                    "content": "我再问一下",
                    "created_at": "2026-07-28T09:00:00+08:00",
                }
            ]
        )
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result, {"ok": True, "status": "skipped", "reason": "customer_replied"})
        self.assertEqual(system.sent, [])
        self.assertIn(("plan-1", "cancelled"), repository.plan_statuses)
        self.assertEqual(repository.skipped_remaining[0]["reason"], "customer_replied_after_plan_creation")

    async def test_first_day_second_task_is_cancelled_when_customer_replied_between_steps(self) -> None:
        repository = _ExecutionRepository(order_status="no_order", trigger_type="first_day_opened_silence")
        system = _SystemClient(
            messages=[
                {
                    "direction": "customer",
                    "content": "我刚才回复了",
                    "created_at": "2026-07-28T09:00:00+08:00",
                }
            ]
        )
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-2")

        self.assertEqual(result, {"ok": True, "status": "skipped", "reason": "customer_replied"})
        self.assertEqual(system.sent, [])
        self.assertIn(("plan-1", "cancelled"), repository.plan_statuses)
        self.assertEqual(
            repository.skipped_remaining,
            [
                {
                    "plan_id": "plan-1",
                    "reason": "customer_replied_after_plan_creation",
                    "exclude_task_id": "task-2",
                }
            ],
        )

    async def test_first_day_first_message_is_rewritten_when_it_repeats_history(self) -> None:
        repeated = "亲，效果和活动前面都给您介绍清楚了，您可以再考虑一下。"
        repository = _ExecutionRepository(
            order_status="no_order",
            trigger_type="first_day_opened_silence",
            recent_messages=[{"role": "assistant", "content": repeated}],
        )
        model = _SequenceMessageModelClient(
            [
                repeated,
                "亲，您平时主要在哪个区活动呀？我先记下您方便到店的区域。",
            ]
        )
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=model,
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(
            system.sent[0]["reply_messages"][0]["content"]["text"],
            "亲，您平时主要在哪个区活动呀？我先记下您方便到店的区域。",
        )

    async def test_first_day_repeated_message_is_blocked_after_one_rewrite(self) -> None:
        repeated = "亲，效果和活动前面都给您介绍清楚了，您可以再考虑一下。"
        repository = _ExecutionRepository(
            order_status="no_order",
            trigger_type="first_day_opened_silence",
            recent_messages=[{"role": "assistant", "content": repeated}],
        )
        model = _SequenceMessageModelClient([repeated, repeated])
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=model,
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "first_day_message_too_similar_to_history")
        self.assertEqual(system.sent, [])
        self.assertIn(("plan-1", "cancelled"), repository.plan_statuses)
        self.assertEqual(repository.events[-1]["event_type"], "task_skipped_message_policy")

    async def test_first_day_sop_pack_task_preserves_pack_messages_without_rewrite(self) -> None:
        pack_text = (
            "现在我们是周年庆线上淡斑活动，前30名登记的顾客到店可以享受268元淡斑特惠价。\n\n"
            "①268元活动价格仅限30名，套餐包括淡斑、检测皮肤、基础清洁和肌肤补水。\n"
            "②线上预定每位10元并登记姓名电话，到店抵扣10元；未做或不满意可退，实际按付款记录核对。"
        )
        repository = _ExecutionRepository(
            order_status="no_order",
            trigger_type="first_day_opened_silence",
            task_reply_messages=[
                {"type": "text", "order": 1, "content": {"text": pack_text}},
                {"type": "image", "order": 2, "content": {"url": "https://oss.example/activity.png"}},
            ],
            task_content_sources=[
                "sop-pack:s10_activity_intro",
                {"should_send_payment_collection": False},
                {
                    "outreach_task_metadata": {
                        "scene": "activity_intro",
                        "preserve_sop_pack_messages": True,
                        "sop_pack_reply_messages": [
                            {"type": "text", "order": 1, "text": pack_text},
                            {
                                "type": "image",
                                "order": 2,
                                "asset_id": "sop-pack:s10_activity_intro:2",
                                "url": "https://oss.example/activity.png",
                            },
                        ],
                    }
                },
                {
                    "resolved_asset": {
                        "asset_id": "sop-pack:s10_activity_intro:2",
                        "type": "image",
                        "url": "https://oss.example/activity.png",
                    }
                },
            ],
        )
        model = _SequenceMessageModelClient(["不应该调用模型"])
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=model,
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(model.calls, [])
        self.assertEqual(system.sent[0]["reply_messages"][0]["content"]["text"], pack_text)
        self.assertEqual(system.sent[0]["reply_messages"][1]["type"], "image")

    async def test_first_day_task_removes_media_already_sent_by_another_chain(self) -> None:
        media_path = "https://oss.example/appointment.png"
        repository = _ExecutionRepository(
            order_status="no_order",
            trigger_type="first_day_opened_silence",
            task_reply_messages=[
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "亲，可以微信转账或发10元红包预约。"},
                },
                {
                    "type": "image",
                    "order": 2,
                    "content": {"url": f"{media_path}?Expires=200&Signature=new"},
                },
            ],
            task_content_sources=[
                "appointment-blocker:deposit",
                {"should_send_payment_collection": False},
                {
                    "outreach_task_metadata": {
                        "scene": "deposit_close",
                        "preserve_sop_pack_messages": True,
                    }
                },
            ],
        )
        system = _SystemClient(
            messages=[
                {
                    "direction": "staff",
                    "sender_type": "ai",
                    "msgtype": "image",
                    "mediaUrl": f"{media_path}?Expires=100&Signature=old",
                    "created_at": "2026-08-13T21:55:00+08:00",
                }
            ]
        )
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-2")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(
            system.sent[0]["reply_messages"],
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "亲，可以微信转账或发10元红包预约。"},
                }
            ],
        )
        self.assertIn(
            "first_day_duplicate_media_removed",
            [event["event_type"] for event in repository.events],
        )

    async def test_first_day_sop_task_uses_fresh_platform_text_for_repeat_check(self) -> None:
        repeated = "亲，您现在在哪个省市、区县呢？"
        repository = _ExecutionRepository(
            order_status="no_order",
            trigger_type="first_day_opened_silence",
            task_reply_messages=[
                {"type": "text", "order": 1, "content": {"text": repeated}},
            ],
            task_content_sources=[
                "sop-pack:s10_store_prompt",
                {"should_send_payment_collection": False},
                {
                    "outreach_task_metadata": {
                        "scene": "store_area_request",
                        "preserve_sop_pack_messages": True,
                    }
                },
            ],
        )
        system = _SystemClient(
            messages=[
                {
                    "direction": "staff",
                    "sender_type": "ai",
                    "msgtype": "text",
                    "content": repeated,
                    "created_at": "2026-08-13T21:40:00+08:00",
                }
            ]
        )
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "first_day_message_too_similar_to_history")
        self.assertEqual(system.sent, [])

    async def test_first_day_gendered_customer_language_is_rewritten_to_neutral(self) -> None:
        repository = _ExecutionRepository(
            order_status="no_order",
            trigger_type="first_day_opened_silence",
        )
        model = _SequenceMessageModelClient(
            [
                "女孩子还是要多爱惜自己一点呀。",
                "很多顾客改善后都会更自信一些，您也可以先看看适合自己的方案。",
            ]
        )
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=model,
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(model.calls), 2)
        sent_text = system.sent[0]["reply_messages"][0]["content"]["text"]
        self.assertNotIn("女孩子", sent_text)
        self.assertIn("很多顾客", sent_text)

    async def test_unavailable_order_check_reschedules_instead_of_sending(self) -> None:
        repository = _ExecutionRepository(order_status="no_order")
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(
                context={"source": "local_memory_fallback", "orders_error": "upstream timeout"}
            ),
            before_send_retry_seconds=45,
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "rescheduled")
        self.assertTrue(result["retryable"])
        self.assertEqual(system.sent, [])
        self.assertEqual(repository.reschedules[-1]["delay_seconds"], 45)

    async def test_deleted_customer_cancels_plan_before_send(self) -> None:
        repository = _ExecutionRepository(order_status="no_order")
        system = _SystemClient(deleted=True)
        service = OutreachService(
            repository=repository,
            model_client=_MessageModelClient(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "customer_deleted")
        self.assertEqual(system.sent, [])
        self.assertIn(("plan-1", "cancelled"), repository.plan_statuses)
        self.assertEqual(repository.skipped_remaining[0]["reason"], "customer_deleted")
        self.assertEqual(repository.events[-1]["event_type"], "task_skipped_customer_deleted")

    async def test_missing_external_userid_is_blocked_before_platform_send(self) -> None:
        repository = _ExecutionRepository(order_status="no_order", external_userid="")
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=_MessageModelClient(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "invalid_outreach_identity")
        self.assertEqual(result["missing"], ["external_userid"])
        self.assertEqual(system.sent, [])
        self.assertIn(("task-1", "failed"), repository.task_statuses)
        self.assertIn(("plan-1", "cancelled"), repository.plan_statuses)
        self.assertEqual(repository.skipped_remaining[-1]["reason"], "invalid_outreach_identity")
        self.assertEqual(repository.events[-1]["event_type"], "task_failed_terminal")


class OutreachSendClientIdentityTests(unittest.TestCase):
    def test_payload_does_not_replace_customer_id_with_external_userid(self) -> None:
        payload = OutreachSendClient._payload(
            request_id="request-1",
            request_context={"external_userid": "external-real"},
            fallback_customer_id="15048961",
            fallback_corp_id="corp",
            fallback_user_id="7294",
            fallback_wechat="WW0601",
            fallback_external_userid="external-real",
            reply_messages=[{"type": "text", "content": {"text": "hi"}}],
        )

        self.assertEqual(payload["customer_id"], "15048961")
        self.assertEqual(payload["external_userid"], "external-real")

    def test_fetch_conversation_params_require_external_userid_without_customer_fallback(self) -> None:
        client = OutreachSendClient(
            SimpleNamespace(
                outreach_send_base_url="https://example.invalid",
                outreach_send_agent_token="token",
                outreach_send_timeout_seconds=1,
            )
        )

        result = asyncio.run(
            client.fetch_conversation(
                corp_id="corp",
                customer_id="15048961",
                external_userid="",
                user_id="7294",
                wechat="WW0601",
                limit=30,
            )
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "missing_required_fields")
        self.assertEqual(result["missing"], ["external_userid"])
        self.assertEqual(result["request"]["customer_id"], "15048961")
        self.assertEqual(result["request"]["external_userid"], "")


class OutreachRepositoryDueTaskTests(unittest.TestCase):
    def test_first_day_sop_candidate_uses_authoritative_conversation_customer_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "outreach.db"))
            store.initialize()
            repository = AppRepository(store)
            now = datetime.now(timezone.utc).isoformat()
            request = SimpleNamespace(
                customer_id="22420394",
                external_userid="external-22420394",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
            )
            repository.upsert_conversation(
                conversation_id="conversation-22420394",
                request=request,
                title="客户",
            )
            with store.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO sop_events
                        (event_id, event_type, source, received_at, updated_at)
                    VALUES ('event-first-day-id', 'sop_friend_added_schedule_batch', 'test', ?, ?)
                    """,
                    (now, now),
                )
                conn.execute(
                    """
                    INSERT INTO sop_send_tasks
                        (id, event_id, idempotency_key, customer_id, external_userid, corp_id,
                         user_id, wechat, sop_pack_id, sop_pack_name, sop_category,
                         reply_messages_json, status, trigger_source, created_at, updated_at)
                    VALUES
                        ('task-first-day-id', 'event-first-day-id', 'idem-first-day-id',
                         'external-22420394', 'external-22420394', 'corp', '7294', 'DY258',
                         'add_wecom', '加微开场', 'opening', '[]', 'sent',
                         'platform_auto_opening', ?, ?)
                    """,
                    (now, now),
                )

            candidates = repository.list_first_day_sop_contact_candidates(limit=10)

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["customer_id"], "22420394")
            self.assertEqual(candidates[0]["external_userid"], "external-22420394")

    def test_active_plan_lookup_is_case_insensitive_for_wechat(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "outreach.db"))
            store.initialize()
            repository = AppRepository(store)
            created = repository.create_outreach_plan(
                customer_id="customer-case",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
                external_userid="external-case",
                customer_stage="P2_OBJECTION",
                stall_reason="silent",
                customer_psychology="需要重新承接",
                plan_goal="重新开口",
                source_snapshot={},
                tasks=[_due_task(1, "第一步")],
            )

            active = repository.get_active_outreach_plan_for_customer(
                "customer-case",
                corp_id="corp",
                wechat="dy258",
                external_userid="external-case",
            )

            self.assertEqual(active["plan"]["id"], created["plan"]["id"])

    def test_completed_cycle_and_remaining_task_queries_are_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "outreach.db"))
            store.initialize()
            repository = AppRepository(store)
            created = repository.create_outreach_plan(
                customer_id="customer-cycle",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
                external_userid="external-cycle",
                customer_stage="P2_OBJECTION",
                stall_reason="silent",
                customer_psychology="需要递进价值",
                plan_goal="重新开口",
                source_snapshot={},
                tasks=[_due_task(1, "第一步"), _due_task(2, "第二步")],
            )
            plan_id = created["plan"]["id"]
            first_task_id = created["tasks"][0]["id"]
            second_task_id = created["tasks"][1]["id"]

            repository.update_outreach_task(first_task_id, status="sent")
            self.assertTrue(repository.outreach_plan_has_remaining_tasks(plan_id))
            repository.update_outreach_task(second_task_id, status="sent")
            self.assertFalse(repository.outreach_plan_has_remaining_tasks(plan_id))
            repository.update_outreach_plan_status(plan_id, "completed")

            completed = repository.get_latest_completed_outreach_plan_for_customer(
                "customer-cycle",
                corp_id="corp",
                wechat="dy258",
                external_userid="external-cycle",
            )

            self.assertEqual(completed["id"], plan_id)
            self.assertEqual(completed["status"], "completed")

    def test_customer_reply_cancels_active_plan_and_remaining_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "outreach.db"))
            store.initialize()
            repository = AppRepository(store)
            created = repository.create_outreach_plan(
                customer_id="customer-reply",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
                external_userid="external-reply",
                customer_stage="P2_OBJECTION",
                stall_reason="silent",
                customer_psychology="需要重新承接",
                plan_goal="重新开口",
                source_snapshot={},
                tasks=[_due_task(1, "第一步"), _due_task(2, "第二步")],
            )
            repository.update_outreach_plan_status(created["plan"]["id"], "active")

            result = repository.cancel_outreach_for_customer_reply(
                customer_id="customer-reply",
                corp_id="corp",
                wechat="dy258",
                external_userid="external-reply",
                request_id="request-reply",
            )

            detail = repository.get_outreach_plan(created["plan"]["id"])
            self.assertEqual(result, {"cancelled_plans": 1, "skipped_tasks": 2})
            self.assertEqual(detail["plan"]["status"], "cancelled")
            self.assertTrue(all(task["status"] == "skipped" for task in detail["tasks"]))
            self.assertIn(
                "plan_cancelled_customer_replied",
                [event["event_type"] for event in detail["events"]],
            )

    def test_recent_sop_delivery_is_scoped_and_available_for_model_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "outreach.db"))
            store.initialize()
            repository = AppRepository(store)
            now = datetime.now(timezone.utc).isoformat()
            with store.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO sop_events
                        (event_id, event_type, source, received_at, updated_at)
                    VALUES ('event-sop', 'sop_friend_added_schedule_batch', 'test', ?, ?)
                    """,
                    (now, now),
                )
                conn.execute(
                    """
                    INSERT INTO sop_send_tasks
                        (id, event_id, idempotency_key, customer_id, external_userid, corp_id,
                         user_id, wechat, sop_pack_id, sop_pack_name, sop_category,
                         reply_messages_json, status, created_at, updated_at, sent_at)
                    VALUES
                        ('task-sop', 'event-sop', 'idem-sop', 'customer-sop', 'external-sop', 'corp',
                         '7294', 'DY258', 's10_need_and_case', '需求案例', 'need_and_case',
                         '[{"type":"text","order":1,"content":{"text":"效果参考"}}]',
                         'sent', ?, ?, ?)
                    """,
                    (now, now, now),
                )

            deliveries = repository.recent_sop_delivery(
                customer_id="customer-sop",
                corp_id="corp",
                wechat="dy258",
                external_userid="external-sop",
                hours=72,
            )

            self.assertEqual(len(deliveries), 1)
            self.assertEqual(deliveries[0]["sop_pack_id"], "s10_need_and_case")
            self.assertEqual(deliveries[0]["reply_messages"][0]["type"], "text")

    def test_task_decoder_preserves_structured_outreach_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "outreach.db"))
            store.initialize()
            repository = AppRepository(store)
            created = repository.create_outreach_plan(
                customer_id="customer-meta",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
                external_userid="external-meta",
                customer_stage="P2_OBJECTION",
                stall_reason="effect_worry",
                customer_psychology="需要效果信任",
                plan_goal="重新开口",
                source_snapshot={},
                tasks=[
                    {
                        **_due_task(1, "参考"),
                        "content_sources": [
                            "s10_offer",
                            {"should_send_payment_collection": False},
                            {
                                "outreach_task_metadata": {
                                    "persuasion_angle": "proof",
                                    "new_value": "真实案例",
                                }
                            },
                            {
                                "resolved_asset": {
                                    "asset_id": "case:1",
                                    "type": "image",
                                    "url": "https://cdn.example/case.jpg",
                                }
                            },
                        ],
                    }
                ],
            )

            task = repository.get_outreach_task(created["tasks"][0]["id"])

            self.assertEqual(task["content_sources"], ["s10_offer"])
            self.assertEqual(
                task["content_source_metadata"][1]["outreach_task_metadata"]["persuasion_angle"],
                "proof",
            )
            self.assertEqual(
                task["content_source_metadata"][2]["resolved_asset"]["url"],
                "https://cdn.example/case.jpg",
            )

    def test_candidate_search_filters_before_limit_and_matches_customer_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "outreach.db"))
            store.initialize()
            repository = AppRepository(store)
            for index in range(3):
                request = SimpleNamespace(
                    customer_id=f"recent-{index}",
                    external_userid=f"external-recent-{index}",
                    corp_id="corp",
                    user_id="7294",
                    wechat="DY258",
                )
                repository.upsert_conversation(
                    conversation_id=f"conversation-recent-{index}",
                    request=request,
                    title=f"最近客户{index}",
                )
                repository.add_user_message(
                    conversation_id=f"conversation-recent-{index}",
                    request_id=f"request-recent-{index}",
                    content="普通消息",
                    file_image=None,
                )

            target_request = SimpleNamespace(
                customer_id="customer-22016906",
                external_userid="external-target",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
            )
            repository.upsert_conversation(
                conversation_id="conversation-target",
                request=target_request,
                title="张治萍",
            )
            repository.add_user_message(
                conversation_id="conversation-target",
                request_id="request-target",
                content="想了解荆州门店",
                file_image=None,
            )
            repository.create_sop_event(
                {
                    "event_id": "event-platform-name",
                    "event_type": "sop_platform_task",
                    "source": "test",
                    "customers": [
                        {
                            "conversation": {
                                "external_userid": "external-target",
                                "wework_user_id": "dy258",
                                "sender_name": "张治萍",
                            },
                            "customer": {
                                "name": "张治萍",
                                "remark": "张女士",
                            },
                        }
                    ],
                }
            )

            by_name = repository.list_outreach_candidates(
                limit=1,
                silent_minutes_min=0,
                keyword="张治萍",
            )
            by_customer_id = repository.list_outreach_candidates(
                limit=1,
                silent_minutes_min=0,
                keyword="22016906",
            )
            by_external_id = repository.list_outreach_candidates(
                limit=1,
                silent_minutes_min=0,
                keyword="external-target",
            )

            self.assertEqual([item["customer_id"] for item in by_name], ["customer-22016906"])
            self.assertEqual(by_name[0]["platform_customer_name"], "张治萍")
            self.assertEqual([item["customer_id"] for item in by_customer_id], ["customer-22016906"])
            self.assertEqual([item["customer_id"] for item in by_external_id], ["customer-22016906"])

    def test_auto_worker_selects_only_auto_approved_and_one_step_per_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "outreach.db"))
            store.initialize()
            repository = AppRepository(store)
            auto = repository.create_outreach_plan(
                customer_id="customer-auto",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
                external_userid="external-auto",
                customer_stage="P2_OBJECTION",
                stall_reason="silent",
                customer_psychology="仍有兴趣",
                plan_goal="重新开口",
                source_snapshot={
                    "trigger_context": {
                        "source": "sop_platform_task",
                        "activation_policy": "auto_approved",
                    }
                },
                tasks=[
                    _due_task(1, "第一步"),
                    _due_task(2, "第二步"),
                ],
            )
            manual = repository.create_outreach_plan(
                customer_id="customer-manual",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
                external_userid="external-manual",
                customer_stage="P2_OBJECTION",
                stall_reason="silent",
                customer_psychology="仍有兴趣",
                plan_goal="重新开口",
                source_snapshot={"trigger_context": {"activation_policy": "review_required"}},
                tasks=[_due_task(1, "人工计划")],
            )
            repository.update_outreach_plan_status(auto["plan"]["id"], "active")
            repository.update_outreach_plan_status(manual["plan"]["id"], "active")

            tasks = repository.list_due_outreach_tasks(
                limit=20,
                now="2030-01-01T00:00:00+00:00",
                auto_approved_only=True,
            )

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["customer_id"], "customer-auto")
            self.assertEqual(tasks[0]["step_index"], 1)

    def test_dashboard_stats_only_count_auto_approved_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "outreach.db"))
            store.initialize()
            repository = AppRepository(store)
            auto = repository.create_outreach_plan(
                customer_id="customer-auto",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
                external_userid="external-auto",
                customer_stage="P2_OBJECTION",
                stall_reason="silent",
                customer_psychology="仍有兴趣",
                plan_goal="重新开口",
                source_snapshot={
                    "trigger_context": {
                        "source": "sop_platform_task",
                        "activation_policy": "auto_approved",
                    }
                },
                tasks=[_due_task(1, "第一步"), _due_task(2, "第二步")],
            )
            repository.update_outreach_plan_status(auto["plan"]["id"], "active")
            manual = repository.create_outreach_plan(
                customer_id="customer-manual",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
                external_userid="external-manual",
                customer_stage="P2_OBJECTION",
                stall_reason="silent",
                customer_psychology="仍有兴趣",
                plan_goal="人工计划",
                source_snapshot={"trigger_context": {"activation_policy": "review_required"}},
                tasks=[_due_task(1, "不应计入")],
            )
            repository.update_outreach_plan_status(manual["plan"]["id"], "active")
            with store.connect() as conn:
                conn.execute(
                    "UPDATE outreach_plans SET created_at='2026-07-28T02:00:00+00:00'"
                )
                conn.execute(
                    """
                    INSERT INTO sop_events
                        (event_id, event_type, source, received_at, updated_at)
                    VALUES (?, 'sop_platform_task', 'test', ?, ?)
                    """,
                    (
                        "platform-event-1",
                        "2026-07-28T02:00:00+00:00",
                        "2026-07-28T02:00:00+00:00",
                    ),
                )

            stats = repository.outreach_dashboard_stats(now="2026-07-28T12:00:00+08:00")

            self.assertEqual(stats["metrics"]["platform_tasks_today"], 1)
            self.assertEqual(stats["metrics"]["personalized_plans_today"], 1)
            self.assertEqual(stats["metrics"]["active_plans"], 1)
            self.assertEqual(stats["metrics"]["pending_tasks"], 2)
            self.assertEqual(stats["metrics"]["due_tasks"], 2)

    def test_customer_detail_respects_sales_contact_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "outreach.db"))
            store.initialize()
            repository = AppRepository(store)
            scope_a = build_customer_scope(
                corp_id="corp",
                wechat="DY258",
                external_userid="external-same",
                customer_id="customer-same",
            )
            scope_b = build_customer_scope(
                corp_id="corp",
                wechat="CS001",
                external_userid="external-same",
                customer_id="customer-same",
            )
            repository.save_memory(
                scope_a.sales_contact_key,
                {
                    "portrait": {"summary": "A账号画像"},
                    "basic_info": {"city": "上海"},
                    "lifecycle_stage": "P2_OBJECTION",
                    "history_events": [
                        {
                            "event_type": "customer_psychology_update",
                            "summary": "A账号事件",
                        }
                    ],
                },
            )
            repository.save_memory(
                scope_b.sales_contact_key,
                {
                    "portrait": {"summary": "B账号画像"},
                    "basic_info": {"city": "广州"},
                    "lifecycle_stage": "P1_INTEREST",
                    "history_events": [
                        {
                            "event_type": "customer_psychology_update",
                            "summary": "B账号事件",
                        }
                    ],
                },
            )
            plan_a = repository.create_outreach_plan(
                customer_id="customer-same",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
                external_userid="external-same",
                customer_stage="P2_OBJECTION",
                stall_reason="silent",
                customer_psychology="仍有兴趣",
                plan_goal="重新开口",
                source_snapshot={"trigger_context": {"activation_policy": "auto_approved"}},
                tasks=[_due_task(1, "A账号任务")],
            )
            plan_b = repository.create_outreach_plan(
                customer_id="customer-same",
                corp_id="corp",
                user_id="7294",
                wechat="CS001",
                external_userid="external-same",
                customer_stage="P1_INTEREST",
                stall_reason="silent",
                customer_psychology="仍有兴趣",
                plan_goal="重新开口",
                source_snapshot={"trigger_context": {"activation_policy": "auto_approved"}},
                tasks=[_due_task(1, "B账号任务")],
            )
            repository.add_outreach_event(
                plan_id=plan_a["plan"]["id"],
                task_id="",
                customer_id="customer-same",
                event_type="plan_created",
                event_summary="A账号唤醒事件",
            )
            repository.add_outreach_event(
                plan_id=plan_b["plan"]["id"],
                task_id="",
                customer_id="customer-same",
                event_type="plan_created",
                event_summary="B账号唤醒事件",
            )

            detail = repository.get_outreach_customer_detail(
                customer_id="customer-same",
                corp_id="corp",
                wechat="DY258",
                external_userid="external-same",
            )

            self.assertEqual(detail["portrait"]["summary"], "A账号画像")
            self.assertEqual(detail["basic_info"]["city"], "上海")
            self.assertEqual([item["summary"] for item in detail["history_events"]], ["A账号事件"])
            event_summaries = [item["event_summary"] for item in detail["outreach_events"]]
            self.assertIn("A账号唤醒事件", event_summaries)
            self.assertNotIn("B账号唤醒事件", event_summaries)


def _due_task(step: int, text: str) -> dict[str, Any]:
    return {
        "step_index": step,
        "scheduled_at": "2020-01-01T00:00:00+00:00",
        "intent": "silence_probe",
        "message_goal": "重新开口",
        "content_sources": [],
        "before_send_check": True,
        "reply_messages": [{"type": "text", "order": 1, "content": {"text": text}}],
    }


class _CustomerContextService:
    def __init__(self, orders: list[dict[str, Any]] | None = None, context: dict[str, Any] | None = None) -> None:
        self.context = context or {"source": "platform_agent", "orders": orders or []}

    def load(self, **_kwargs: Any) -> dict[str, Any]:
        return dict(self.context)


class _SystemClient:
    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        *,
        deleted: bool = False,
        send_error: Exception | None = None,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.messages = messages or []
        self.deleted = deleted
        self.send_error = send_error

    async def conversation(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "data": {
                "messages": self.messages,
                "customer_relation": {
                    "status": "deleted" if self.deleted else "active",
                    "is_deleted": self.deleted,
                    "deleted_at": "2026-07-29T10:00:00+08:00" if self.deleted else None,
                    "updated_at": "2026-07-29T10:00:00+08:00",
                },
            }
        }

    async def send(self, **kwargs: Any) -> dict[str, Any]:
        if self.send_error:
            raise self.send_error
        self.sent.append(kwargs)
        return {"code": 0, "data": {"send_status": "accepted", "system_msgid": "msg-1"}}


class _MessageModelClient:
    async def chat_json(self, _messages: list[dict[str, Any]], **_kwargs: Any) -> dict[str, Any]:
        return {
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "亲，前面您主要担心效果，我再给您说清楚一点。"},
                }
            ]
        }


class _SequenceMessageModelClient:
    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_json(self, messages: list[dict[str, Any]], **_kwargs: Any) -> dict[str, Any]:
        self.calls.append(messages)
        if not self.texts:
            raise AssertionError("unexpected model call")
        return {
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": self.texts.pop(0)},
                }
            ]
        }


class _ExecutionRepository:
    def __init__(
        self,
        order_status: str,
        *,
        remaining_tasks: bool = False,
        trigger_type: str = "",
        recent_messages: list[dict[str, Any]] | None = None,
        task_reply_messages: list[dict[str, Any]] | None = None,
        task_content_sources: list[Any] | None = None,
        external_userid: str = "external-1",
    ) -> None:
        self.order_status = order_status
        self.remaining_tasks = remaining_tasks
        self.trigger_type = trigger_type
        self.recent_messages = recent_messages or []
        self.task_reply_messages = task_reply_messages
        self.task_content_sources = task_content_sources
        self.external_userid = external_userid
        self.task_statuses: list[tuple[str, str]] = []
        self.plan_statuses: list[tuple[str, str]] = []
        self.events: list[dict[str, Any]] = []
        self.reschedules: list[dict[str, Any]] = []
        self.skipped_remaining: list[dict[str, str]] = []
        self.outreach_state_updates: list[dict[str, Any]] = []

    def get_outreach_task(self, task_id: str) -> dict[str, Any]:
        return {
            "id": task_id,
            "plan_id": "plan-1",
            "customer_id": "customer-1",
            "corp_id": "corp",
            "user_id": "7294",
            "wechat": "DY258",
            "external_userid": self.external_userid,
            "status": "pending",
            "step_index": 2 if task_id == "task-2" else 1,
            "before_send_check": True,
            "content_sources": self.task_content_sources or [],
            "content_source_metadata": [
                item for item in self.task_content_sources or [] if isinstance(item, dict)
            ],
            "reply_messages": self.task_reply_messages
            or [{"type": "text", "order": 1, "content": {"text": "亲，前面您主要担心效果，我再给您说清楚一点。"}}],
        }

    def claim_outreach_task(self, _task_id: str) -> bool:
        return True

    def get_outreach_plan(self, _plan_id: str) -> dict[str, Any]:
        return {
            "plan": {
                "id": "plan-1",
                "customer_id": "customer-1",
                "corp_id": "corp",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": self.external_userid,
                "created_at": "2026-07-28T08:00:00+08:00",
                "source_snapshot": {
                    "memory": {"last_customer_message_at": "2026-07-28T08:00:00+08:00"},
                    "recent_messages": self.recent_messages,
                    "trigger_context": {
                        "source": "sop_platform_task",
                        "activation_policy": "auto_approved",
                        "trigger_type": self.trigger_type,
                    },
                },
            }
        }

    def recent_customer_context(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "memory": {"last_customer_message_at": "2026-07-28T08:00:00+08:00"},
            "recent_messages": self.recent_messages,
        }

    def touch_customer_message_time(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def update_customer_outreach_state(self, *_args: Any, **kwargs: Any) -> None:
        self.outreach_state_updates.append(kwargs)

    def update_outreach_task(self, task_id: str, *, status: str, **_kwargs: Any) -> dict[str, Any]:
        self.task_statuses.append((task_id, status))
        return self.get_outreach_task(task_id)

    def update_outreach_plan_status(self, plan_id: str, status: str) -> dict[str, Any]:
        self.plan_statuses.append((plan_id, status))
        return {"plan": {"id": plan_id, "status": status}}

    def outreach_plan_has_remaining_tasks(self, _plan_id: str) -> bool:
        return self.remaining_tasks

    def skip_remaining_outreach_tasks(
        self,
        plan_id: str,
        *,
        reason: str,
        exclude_task_id: str = "",
    ) -> int:
        self.skipped_remaining.append(
            {
                "plan_id": plan_id,
                "reason": reason,
                "exclude_task_id": exclude_task_id,
            }
        )
        return 1

    def add_outreach_event(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(kwargs)
        return kwargs

    def reschedule_outreach_task(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        self.reschedules.append({"task_id": task_id, **kwargs})
        return self.get_outreach_task(task_id)


if __name__ == "__main__":
    unittest.main()
