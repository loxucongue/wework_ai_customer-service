from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from app.services.outreach_service import (
    OutreachService,
    _normalize_outreach_schedule,
    _outreach_plan_structure_error,
    build_outreach_activity_quote_fact,
)


class PersonalizedOutreachPlanTests(unittest.IsolatedAsyncioTestCase):
    def test_single_step_plan_is_valid_for_low_intent_silence(self) -> None:
        response = {
            "should_create_plan": True,
            "plan_arc": "只轻触一次获取门店匹配所需区域，客户不回复则停止。",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 720,
                    "timing_reason": "客户意向较低，只安排一次低压力触达",
                    "urgency_level": "normal",
                    "content_mode": "value_only",
                    "persuasion_angle": "convenience",
                    "new_value": "只需提供城市或区域即可匹配真实门店",
                    "reply_messages": [
                        {
                            "type": "text",
                            "order": 1,
                            "content": {
                                "text": "您方便时发我城市或区域就行，我按真实门店帮您看下。"
                            },
                        }
                    ],
                    "asset_strategy": "none",
                    "cta": "提供城市或区域",
                    "payment_collection_basis": "none",
                    "payment_collection_evidence": {
                        "activity_quote_message_index": None
                    },
                    "should_send_payment_collection": False,
                }
            ],
        }

        self.assertEqual(_outreach_plan_structure_error(response), "")

    def test_schedule_supports_immediate_touch_and_daily_limit(self) -> None:
        schedule = _normalize_outreach_schedule(
            "2026-07-28T01:00:00+00:00",
            [
                {"delay_minutes": 0},
                {"delay_minutes": 360},
                {"delay_minutes": 720},
            ],
        )

        self.assertEqual(schedule[0]["scheduled_at"], "2026-07-28T01:00:00+00:00")
        self.assertEqual(schedule[1]["scheduled_at"], "2026-07-28T07:00:00+00:00")
        self.assertEqual(schedule[2]["scheduled_at"], "2026-07-29T00:30:00+00:00")

    def test_schedule_moves_quiet_hour_touch_to_beijing_0830(self) -> None:
        schedule = _normalize_outreach_schedule(
            "2026-07-28T13:30:00+00:00",
            [{"delay_minutes": 60}, {"delay_minutes": 600}],
        )

        self.assertEqual(schedule[0]["scheduled_at"], "2026-07-29T00:30:00+00:00")
        self.assertGreaterEqual(schedule[1]["normalized_delay_minutes"], 60 + 360)

    def test_activity_quote_fact_uses_visible_quote_or_structured_sop_progress(self) -> None:
        message_fact = build_outreach_activity_quote_fact(
            [
                {"direction": "staff", "content": "活动和流程已经介绍过。"},
                {"direction": "staff", "content": "周年庆活动总价268元，每位先付10元预约金。"},
            ],
            {},
        )
        self.assertTrue(message_fact["completed"])
        self.assertEqual(message_fact["message_indexes"], [1])

        progress_fact = build_outreach_activity_quote_fact(
            [{"direction": "staff", "content": "活动和流程已经介绍过。"}],
            {"sop_progress_evidence": {"completed_pack_ids": ["s10_activity_intro"]}},
        )
        self.assertTrue(progress_fact["completed"])
        self.assertEqual(progress_fact["structured_sources"], ["sop_progress"])

    def test_activity_quote_fact_rejects_generic_activity_summary(self) -> None:
        fact = build_outreach_activity_quote_fact(
            [{"direction": "staff", "content": "活动和到店流程已经介绍过。"}],
            {},
        )
        self.assertFalse(fact["completed"])
        self.assertEqual(fact["message_indexes"], [])

    def test_silence_monitor_prefilter_uses_latest_staff_reply(self) -> None:
        base = {
            "sales_contact_started_at": "2000-01-01T00:00:00+08:00",
            "last_customer_message_at": "2026-07-29T09:00:00+08:00",
            "awaiting_customer_reply": True,
        }

        self.assertEqual(
            OutreachService._rough_silence_candidate_reason(
                {**base, "reply_wait_minutes": 5},
                silent_minutes=10,
            ),
            "reply_wait_below_threshold",
        )
        self.assertEqual(
            OutreachService._rough_silence_candidate_reason(
                {**base, "reply_wait_minutes": 10},
                silent_minutes=10,
            ),
            "",
        )
        self.assertEqual(
            OutreachService._rough_silence_candidate_reason(
                {**base, "awaiting_customer_reply": False, "reply_wait_minutes": 30},
                silent_minutes=10,
            ),
            "not_waiting_for_customer_reply",
        )
        self.assertEqual(
            OutreachService._rough_silence_candidate_reason(
                {
                    **base,
                    "sales_contact_started_at": datetime.now(timezone.utc).isoformat(),
                    "reply_wait_minutes": 30,
                },
                silent_minutes=10,
            ),
            "not_proven_day2_plus",
        )

    async def test_silence_monitor_creates_and_activates_one_plan(self) -> None:
        now = datetime.now(timezone.utc)
        customer_at = (now - timedelta(minutes=30)).isoformat()
        staff_at = (now - timedelta(minutes=11)).isoformat()
        repository = _Repository()
        repository.candidates = [
            _monitor_candidate(
                customer_at=customer_at,
                staff_at=staff_at,
            )
        ]
        model = _ModelClient()
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "我考虑下", "created_at": customer_at},
                {"direction": "staff", "content": "您慢慢考虑", "created_at": staff_at},
            ],
        )

        result = await service.evaluate_silent_customers(
            limit=5,
            silent_minutes=10,
            auto_activate=True,
        )

        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(repository.updated_statuses, [("plan-created", "active")])
        self.assertEqual(
            repository.created_plan["source_snapshot"]["trigger_context"]["source"],
            "silence_monitor",
        )
        self.assertEqual(len(model.calls), 2)

    async def test_silence_monitor_model_rejection_is_idempotent_for_same_conversation(self) -> None:
        now = datetime.now(timezone.utc)
        customer_at = (now - timedelta(minutes=40)).isoformat()
        staff_at = (now - timedelta(minutes=20)).isoformat()
        repository = _Repository()
        repository.candidates = [
            _monitor_candidate(
                customer_at=customer_at,
                staff_at=staff_at,
            )
        ]
        model = _ModelClient(
            response={
                "should_create_plan": False,
                "stall_reason": "当前不适合主动触达",
                "customer_psychology": "需要空间",
            }
        )
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "先不用", "created_at": customer_at},
                {"direction": "staff", "content": "好的", "created_at": staff_at},
            ],
        )

        first = await service.evaluate_silent_customers(limit=5, silent_minutes=10)
        calls_after_first_scan = len(model.calls)
        second = await service.evaluate_silent_customers(limit=5, silent_minutes=10)

        self.assertEqual(first["rejected_count"], 1)
        self.assertEqual(second["evaluated_count"], 0)
        self.assertEqual(
            second["results"][0]["reason"],
            "conversation_fingerprint_already_evaluated",
        )
        self.assertEqual(calls_after_first_scan, 2)
        self.assertEqual(len(model.calls), calls_after_first_scan)

    async def test_deleted_customer_skips_plan_generation_before_model_call(self) -> None:
        repository = _Repository()
        model = _ModelClient()
        service = OutreachService(
            repository=repository,
            model_client=model,
            system_client=_ConversationSystemClient(deleted=True),
        )

        result = await service.generate_plan(
            customer_id="22000001",
            corp_id="corp-1",
            user_id="7294",
            wechat="DY258",
            external_userid="external-1",
        )

        self.assertEqual(result["reason"], "customer_deleted")
        self.assertEqual(model.calls, [])
        self.assertEqual(repository.created_plan, {})
        self.assertIn(
            "plan_skipped_customer_deleted",
            [event["event_type"] for event in repository.events],
        )

    async def test_silence_monitor_skips_deleted_customer_before_model_call(self) -> None:
        now = datetime.now(timezone.utc)
        customer_at = (now - timedelta(minutes=30)).isoformat()
        staff_at = (now - timedelta(minutes=11)).isoformat()
        repository = _Repository()
        repository.candidates = [_monitor_candidate(customer_at=customer_at, staff_at=staff_at)]
        model = _ModelClient()
        service = _MonitorOutreachService(
            repository=repository,
            model_client=model,
            refreshed_messages=[
                {"direction": "customer", "content": "我再看看", "created_at": customer_at},
                {"direction": "staff", "content": "好的", "created_at": staff_at},
            ],
            deleted=True,
        )

        result = await service.evaluate_silent_customers(limit=5, silent_minutes=10)

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["results"][0]["reason"], "customer_deleted")
        self.assertEqual(model.calls, [])
        self.assertIn(
            "plan_skipped_customer_deleted",
            [event["event_type"] for event in repository.events],
        )

    def test_sop_event_and_silence_monitor_share_contact_lock(self) -> None:
        service = OutreachService(
            repository=_Repository(),
            model_client=_ModelClient(),
            system_client=_ConversationSystemClient(),
        )
        first = service._plan_lock(
            {
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "wechat": "DY258",
                "external_userid": "external-1",
            }
        )
        second = service._plan_lock(
            {
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "wechat": "dy258",
                "external_userid": "external-1",
            }
        )
        self.assertIs(first, second)

    async def test_platform_task_plan_uses_latest_context_and_auto_queues_drafts(self) -> None:
        repository = _Repository()
        model = _ModelClient()
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        result = await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[
                {
                    "direction": "customer",
                    "content": "门店太远了，我再考虑下",
                    "created_at": "2026-07-27T10:00:00+08:00",
                },
                {
                    "direction": "staff",
                    "content": "活动价已经给您介绍过了",
                    "created_at": "2026-07-27T10:01:00+08:00",
                },
            ],
            conversation_activity={
                "real_customer_message_count": 1,
                "latest_customer_message_at": "2026-07-27T10:00:00+08:00",
            },
            customer_context={"orders": [], "deposit_state": "unknown"},
            platform_task={
                "event_id": "platform-task-1",
                "messages": [{"type": "text", "content": {"text": "平台统一跟进"}}],
            },
        )

        self.assertTrue(result["created"])
        self.assertFalse(result["reused"])
        self.assertEqual(len(model.calls), 2)
        model_input = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertEqual(model_input["recent_messages"][0]["content"], "门店太远了，我再考虑下")
        self.assertTrue(model_input["trigger_context"]["platform_task_filtered"])
        self.assertEqual(model_input["trigger_context"]["activation_policy"], "auto_approved")
        self.assertEqual(repository.created_plan["customer_id"], "22000001")
        self.assertEqual(
            repository.created_plan["tasks"][0]["reply_messages"][0]["content"]["text"],
            "亲，您上次主要是觉得距离不太方便。活动名额可以先留着，到店时间按您方便安排。",
        )
        self.assertTrue(repository.created_plan["tasks"][0]["before_send_check"])
        self.assertTrue(result["auto_approved"])
        self.assertEqual(result["plan"]["status"], "active")
        self.assertEqual(repository.updated_statuses, [("plan-created", "active")])
        self.assertEqual(repository.events[-1]["event_type"], "plan_auto_approved")

    async def test_existing_active_plan_is_reused_without_another_model_call(self) -> None:
        repository = _Repository()
        repository.active_plan = {
            "plan": {
                "id": "plan-existing",
                "status": "active",
                "created_at": "2026-07-28T10:00:00+08:00",
            },
            "tasks": [{"id": "task-existing"}],
            "events": [],
        }
        model = _ModelClient()
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        result = await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[],
            conversation_activity={
                "real_customer_message_count": 1,
                "latest_customer_message_at": "2026-07-28T09:00:00+08:00",
            },
            customer_context={"orders": []},
            platform_task={"event_id": "platform-task-2", "messages": []},
        )

        self.assertTrue(result["reused"])
        self.assertFalse(result["created"])
        self.assertEqual(model.calls, [])
        self.assertIn(
            "platform_task_filtered_plan_reused",
            [event["event_type"] for event in repository.events],
        )

    async def test_legacy_review_plan_is_cancelled_and_replaced_by_auto_approved_plan(self) -> None:
        repository = _Repository()
        repository.active_plan = {
            "plan": {
                "id": "plan-legacy",
                "status": "draft",
                "created_at": "2026-07-28T10:00:00+08:00",
                "source_snapshot": {
                    "trigger_context": {
                        "source": "sop_platform_task",
                        "activation_policy": "review_required",
                    }
                },
            },
            "tasks": [{"id": "task-legacy"}],
            "events": [],
        }
        model = _ModelClient()
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        result = await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[],
            conversation_activity={
                "real_customer_message_count": 1,
                "latest_customer_message_at": "2026-07-28T09:00:00+08:00",
            },
            customer_context={"orders": []},
            platform_task={"event_id": "platform-task-migrate", "messages": []},
        )

        self.assertTrue(result["created"])
        self.assertTrue(result["auto_approved"])
        self.assertEqual(
            repository.updated_statuses,
            [("plan-legacy", "cancelled"), ("plan-created", "active")],
        )
        self.assertIn(
            "legacy_review_plan_cancelled",
            [event["event_type"] for event in repository.events],
        )

    async def test_customer_reply_after_draft_plan_regenerates_from_latest_conversation(self) -> None:
        repository = _Repository()
        repository.active_plan = {
            "plan": {
                "id": "plan-old",
                "status": "draft",
                "created_at": "2026-07-27T10:00:00+08:00",
            },
            "tasks": [{"id": "task-old"}],
            "events": [],
        }
        model = _ModelClient()
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        result = await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[
                {
                    "direction": "customer",
                    "content": "我现在主要担心到店还要加钱",
                    "created_at": "2026-07-28T11:00:00+08:00",
                }
            ],
            conversation_activity={
                "real_customer_message_count": 2,
                "latest_customer_message_at": "2026-07-28T11:00:00+08:00",
            },
            customer_context={"orders": []},
            platform_task={"event_id": "platform-task-new-reply", "messages": []},
        )

        self.assertTrue(result["created"])
        self.assertFalse(result["reused"])
        self.assertEqual(
            repository.updated_statuses,
            [("plan-old", "cancelled"), ("plan-created", "active")],
        )
        self.assertIn(
            "platform_task_plan_superseded_by_customer_reply",
            [event["event_type"] for event in repository.events],
        )
        self.assertEqual(len(model.calls), 2)

    async def test_plan_without_reviewable_draft_fails_instead_of_creating_empty_task(self) -> None:
        repository = _Repository()
        model = _ModelClient(response={"should_create_plan": True, "steps": [{"step": 1, "delay_minutes": 30}]})
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        with self.assertRaisesRegex(RuntimeError, "invalid_structure"):
            await service.ensure_platform_task_plan(
                identity={
                    "customer_id": "22000001",
                    "corp_id": "corp-1",
                    "user_id": "7294",
                    "wechat": "DY258",
                    "external_userid": "external-1",
                },
                conversation_messages=[{"direction": "customer", "content": "我考虑一下"}],
                conversation_activity={"real_customer_message_count": 1},
                customer_context={"orders": []},
                platform_task={"event_id": "platform-task-3", "messages": []},
            )

        self.assertEqual(repository.created_plan, {})

    async def test_invalid_first_plan_response_is_repaired_once(self) -> None:
        valid = _ModelClient().response
        model = _SequenceModelClient(
            [
                {
                    "should_create_plan": True,
                    "steps": [
                        {
                            "step": 1,
                            "persuasion_angle": "empathy",
                            "reply_messages": [{"type": "text", "order": 1, "content": {"text": "只有一步"}}],
                        }
                    ],
                },
                valid,
            ]
        )
        repository = _Repository()
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        result = await service.generate_plan(
            customer_id="22000001",
            source_context={"memory": {}, "recent_messages": []},
        )

        self.assertTrue(result["created"])
        self.assertEqual(len(model.calls), 3)
        self.assertIn("不符合结构合同", model.calls[1]["messages"][-1]["content"])

    async def test_invalid_final_review_is_repaired_once(self) -> None:
        valid = _ModelClient().response
        model = _SequenceModelClient(
            [
                valid,
                {
                    "should_create_plan": True,
                    "steps": [
                        {
                            "step": 1,
                            "persuasion_angle": "unsupported_angle",
                            "reply_messages": [{"type": "text", "order": 1, "content": {"text": "结构错误"}}],
                        }
                    ],
                },
                valid,
            ]
        )
        repository = _Repository()
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())

        result = await service.generate_plan(
            customer_id="22000001",
            source_context={"memory": {}, "recent_messages": []},
        )

        self.assertTrue(result["created"])
        self.assertEqual(len(model.calls), 3)
        repair_payload = model.calls[2]["messages"][-1]["content"]
        self.assertIn("structure_error", repair_payload)
        self.assertIn("unsupported_angle", repair_payload)

    async def test_payment_card_can_be_selected_on_final_step_after_activity_quote(self) -> None:
        response = {
            "should_create_plan": True,
            "plan_arc": "先共情时间压力，再降低付款决策成本",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 360,
                    "timing_reason": "先降低客户时间压力",
                    "urgency_level": "same_day",
                    "content_mode": "value_only",
                    "intent": "time_reassurance",
                    "persuasion_angle": "empathy",
                    "new_value": "到店时间后定",
                    "avoid_repeating": ["完整活动规则"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，您时间没定也不影响，后面按您方便安排就行。"}}],
                    "asset_strategy": "none",
                    "cta": "回复大概方便的时间",
                    "payment_collection_basis": "none",
                    "payment_collection_evidence": {"activity_quote_message_index": None},
                    "should_send_payment_collection": False,
                },
                {
                    "step": 2,
                    "delay_minutes": 1440,
                    "timing_reason": "价值铺垫后再降低付款门槛",
                    "urgency_level": "normal",
                    "content_mode": "transaction",
                    "intent": "deposit_value",
                    "persuasion_angle": "low_risk_action",
                    "new_value": "先保留活动资格",
                    "avoid_repeating": ["距离顾虑"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，您可以先把活动资格留住，到店时间后面再定。"}}],
                    "asset_strategy": "none",
                    "cta": "支付10元预约金",
                    "payment_collection_basis": "model_selected_after_quote",
                    "payment_collection_evidence": {"activity_quote_message_index": 0},
                    "should_send_payment_collection": True,
                },
            ],
        }
        repository = _Repository()
        service = OutreachService(
            repository=repository,
            model_client=_ModelClient(response=response),
            system_client=_ConversationSystemClient(),
        )

        await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[
                {
                    "direction": "staff",
                    "content": "活动总价268，每位先交10元预约金。",
                    "created_at": "2026-07-28T09:00:00+08:00",
                },
                {
                    "direction": "customer",
                    "content": "可以，发入口吧。",
                    "created_at": "2026-07-28T09:01:00+08:00",
                },
            ],
            conversation_activity={"real_customer_message_count": 1},
            customer_context={"orders": []},
            platform_task={"event_id": "platform-task-card", "messages": []},
        )

        first_messages = repository.created_plan["tasks"][0]["reply_messages"]
        second_messages = repository.created_plan["tasks"][1]["reply_messages"]
        self.assertEqual([item["type"] for item in first_messages], ["text"])
        self.assertEqual([item["type"] for item in second_messages], ["text", "payment_collection"])

    async def test_payment_card_is_removed_when_evidence_indices_do_not_match_message_parties(self) -> None:
        response = {
            "should_create_plan": True,
            "plan_arc": "先专业解释，再降低行动门槛",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 360,
                    "timing_reason": "用专业信息先解除顾虑",
                    "urgency_level": "same_day",
                    "content_mode": "value_only",
                    "intent": "effect_reassurance",
                    "persuasion_angle": "professionalism",
                    "new_value": "到店先检测",
                    "avoid_repeating": ["反弹问题原话"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，前面的活动我再帮您接着留意。"}}],
                    "asset_strategy": "none",
                    "cta": "回复斑点情况",
                    "payment_collection_basis": "none",
                    "should_send_payment_collection": False,
                },
                {
                    "step": 2,
                    "delay_minutes": 1440,
                    "timing_reason": "隔天再提供低风险动作",
                    "urgency_level": "normal",
                    "content_mode": "transaction",
                    "intent": "deposit_value",
                    "persuasion_angle": "low_risk_action",
                    "new_value": "活动资格可先保留",
                    "avoid_repeating": ["检测流程"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，活动资格可以先保留，到店时间后面再定。"}}],
                    "asset_strategy": "none",
                    "cta": "支付10元预约金",
                    "payment_collection_basis": "model_selected_after_quote",
                    "payment_collection_evidence": {"activity_quote_message_index": 0},
                    "should_send_payment_collection": True,
                },
            ],
        }
        repaired_response = json.loads(json.dumps(response, ensure_ascii=False))
        repaired_response["steps"][1]["content_mode"] = "soft_conversion"
        repaired_response["steps"][1]["payment_collection_basis"] = "none"
        repaired_response["steps"][1]["payment_collection_evidence"] = {
            "activity_quote_message_index": None
        }
        repaired_response["steps"][1]["should_send_payment_collection"] = False
        repository = _Repository()
        service = OutreachService(
            repository=repository,
            model_client=_SequenceModelClient([response, repaired_response, repaired_response]),
            system_client=_ConversationSystemClient(),
        )

        await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[
                {"direction": "customer", "content": "会不会反弹"},
                {"direction": "staff", "content": "到店先检测"},
            ],
            conversation_activity={"real_customer_message_count": 1},
            customer_context={"orders": []},
            platform_task={"event_id": "platform-task-no-card", "messages": []},
        )

        self.assertEqual(
            [item["type"] for item in repository.created_plan["tasks"][1]["reply_messages"]],
            ["text"],
        )

    async def test_plan_resolves_configured_and_case_assets_without_model_urls(self) -> None:
        response = {
            "should_create_plan": True,
            "plan_arc": "先用配置素材科普，再用真实案例增强信任",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 360,
                    "timing_reason": "先提供操作知识",
                    "urgency_level": "same_day",
                    "content_mode": "value_only",
                    "intent": "education",
                    "persuasion_angle": "education",
                    "new_value": "解释操作过程",
                    "avoid_repeating": ["完整报价"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，我给您补一个操作过程参考，您看完会更直观。"}}],
                    "asset_strategy": "operation_video",
                    "asset_id": "operation_pack:1",
                    "cta": "看完回复感受",
                    "should_send_payment_collection": False,
                },
                {
                    "step": 2,
                    "delay_minutes": 1440,
                    "timing_reason": "隔天用真实案例增强信任",
                    "urgency_level": "normal",
                    "content_mode": "soft_conversion",
                    "intent": "effect_reassurance",
                    "persuasion_angle": "proof",
                    "new_value": "同类斑点参考",
                    "avoid_repeating": ["操作过程"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，我再给您看个同类情况参考，您主要担心的是效果对吧？"}}],
                    "asset_strategy": "case_search",
                    "case_query": "晒斑改善案例",
                    "fallback_asset_id": "effect_pack:2",
                    "cta": "回复主要顾虑",
                    "should_send_payment_collection": False,
                },
            ],
        }
        repository = _Repository()
        model = _ModelClient(response=response)
        service = OutreachService(
            repository=repository,
            model_client=model,
            system_client=_ConversationSystemClient(),
            outreach_asset_library_service=_OutreachAssetLibraryService(),
            coze_client=_CozeClient(),
        )

        await service.generate_plan(customer_id="22000001", source_context={"recent_messages": [], "memory": {}})

        self.assertEqual(
            [item["type"] for item in repository.created_plan["tasks"][0]["reply_messages"]],
            ["text", "video"],
        )
        self.assertEqual(
            repository.created_plan["tasks"][0]["reply_messages"][1]["content"]["url"],
            "https://cdn.example/operation.mp4",
        )
        self.assertEqual(
            repository.created_plan["tasks"][1]["reply_messages"][1]["content"]["url"],
            "https://cdn.example/kb-case.jpg",
        )
        model_input = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertNotIn("url", model_input["asset_catalog"][0])

    async def test_message_model_can_only_rewrite_text_and_cannot_replace_locked_asset(self) -> None:
        repository = _Repository()
        model = _ModelClient(
            response={
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "亲，我给您补个真实参考，您看完回我一句就行。"}},
                    {"type": "image", "order": 2, "content": {"url": "https://evil.example/fake.jpg"}},
                    {"type": "payment_collection", "order": 3, "content": {"amount": 40}},
                ]
            }
        )
        service = OutreachService(repository=repository, model_client=model, system_client=_ConversationSystemClient())
        task = {
            "customer_id": "22000001",
            "content_sources": [],
            "content_source_metadata": [
                {"should_send_payment_collection": False},
                {
                    "outreach_task_metadata": {
                        "persuasion_angle": "proof",
                        "new_value": "同类案例",
                        "avoid_repeating": ["完整报价"],
                        "cta": "回复主要顾虑",
                    }
                },
                {
                    "resolved_asset": {
                        "asset_id": "effect_pack:2",
                        "type": "image",
                        "url": "https://cdn.example/real.jpg",
                        "source": "outreach_asset_library",
                    }
                },
            ],
            "reply_messages": [{"type": "text", "order": 1, "content": {"text": "原草稿"}}],
            "should_send_payment_collection": False,
        }

        messages = await service._generate_task_messages(task=task, plan={})

        self.assertEqual(
            messages,
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "亲，我给您补个真实参考，您看完回我一句就行。"},
                },
                {
                    "type": "image",
                    "order": 2,
                    "content": {"url": "https://cdn.example/real.jpg"},
                },
            ],
        )

    async def test_case_search_failure_uses_model_selected_configured_fallback(self) -> None:
        response = {
            "should_create_plan": True,
            "plan_arc": "先补知识，再补效果参考",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 360,
                    "timing_reason": "先提供护理知识",
                    "urgency_level": "same_day",
                    "content_mode": "value_only",
                    "intent": "education",
                    "persuasion_angle": "education",
                    "new_value": "简单护理知识",
                    "avoid_repeating": ["价格"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，我给您补个简单护理知识，平时防晒也会影响色素状态。"}}],
                    "asset_strategy": "none",
                    "cta": "回复斑点时间",
                    "should_send_payment_collection": False,
                },
                {
                    "step": 2,
                    "delay_minutes": 1440,
                    "timing_reason": "隔天补充真实效果参考",
                    "urgency_level": "normal",
                    "content_mode": "soft_conversion",
                    "intent": "effect_reassurance",
                    "persuasion_angle": "proof",
                    "new_value": "效果参考",
                    "avoid_repeating": ["护理知识"],
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，我给您补个同类参考，您看完会更直观。"}}],
                    "asset_strategy": "case_search",
                    "case_query": "晒斑改善案例",
                    "fallback_asset_id": "effect_pack:2",
                    "cta": "回复主要顾虑",
                    "should_send_payment_collection": False,
                },
            ],
        }
        repository = _Repository()
        service = OutreachService(
            repository=repository,
            model_client=_ModelClient(response=response),
            system_client=_ConversationSystemClient(),
            outreach_asset_library_service=_OutreachAssetLibraryService(),
            coze_client=_FailingCozeClient(),
        )

        await service.generate_plan(customer_id="22000001", source_context={"recent_messages": [], "memory": {}})

        self.assertEqual(
            repository.created_plan["tasks"][1]["reply_messages"][1]["content"]["url"],
            "https://cdn.example/fallback.jpg",
        )


class _ModelClient:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {
            "should_create_plan": True,
            "conversion_stage": "P3_STORE_MATCH",
            "stall_reason": "store_unclear",
            "customer_psychology": "距离顾虑",
            "plan_goal": "让客户重新开口并保留活动资格",
            "plan_arc": "先共情距离顾虑，再用专业检测价值推进",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 360,
                    "timing_reason": "客户刚结束对话，先低压力承接",
                    "urgency_level": "same_day",
                    "content_mode": "soft_conversion",
                    "intent": "store_convenience",
                    "persuasion_angle": "empathy",
                    "new_value": "到店时间可以后定",
                    "avoid_repeating": ["门店距离"],
                    "before_send_check": True,
                    "message_goal": "化解距离顾虑",
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，您上次主要是觉得距离不太方便。活动名额可以先留着，到店时间按您方便安排。"}}],
                    "asset_strategy": "none",
                    "cta": "回复是否愿意继续了解",
                    "should_send_payment_collection": False,
                    "content_sources": ["s10_offer"],
                },
                {
                    "step": 2,
                    "delay_minutes": 1440,
                    "timing_reason": "隔天补充专业流程价值",
                    "urgency_level": "normal",
                    "content_mode": "value_only",
                    "intent": "professional_value",
                    "persuasion_angle": "professionalism",
                    "new_value": "到店先检测再决定",
                    "avoid_repeating": ["活动名额"],
                    "before_send_check": True,
                    "message_goal": "用专业流程降低到店顾虑",
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，到店会先看斑点情况和适合的方向，合适再决定，您主要是哪类斑点呢？"}}],
                    "asset_strategy": "none",
                    "cta": "回复斑点类型",
                    "should_send_payment_collection": False,
                    "content_sources": ["s10_offer"],
                },
            ],
        }
        self.calls: list[dict[str, Any]] = []

    async def chat_json(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": messages, **kwargs})
        return dict(self.response)


class _SequenceModelClient(_ModelClient):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(response=responses[0])
        self.responses = responses

    async def chat_json(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": messages, **kwargs})
        return dict(self.responses[min(len(self.calls) - 1, len(self.responses) - 1)])


class _OutreachAssetLibraryService:
    def load(self) -> dict[str, Any]:
        return {
            "assets": [
                {
                    "id": "operation_pack:1",
                    "enabled": True,
                    "type": "video",
                    "name": "操作视频",
                    "annotation": "展示真实操作过程，用于客户不了解操作方式时做专业科普。",
                    "use_cases": ["操作方式", "专业流程"],
                    "avoid_when": ["近期已发送操作视频"],
                    "tags": ["操作", "视频"],
                    "url": "https://cdn.example/operation.mp4",
                },
                {
                    "id": "effect_pack:2",
                    "enabled": True,
                    "type": "image",
                    "name": "效果参考",
                    "annotation": "用于客户担心效果时增强真实信任。",
                    "use_cases": ["效果顾虑"],
                    "avoid_when": ["近期已发送同类案例"],
                    "tags": ["效果", "案例"],
                    "url": "https://cdn.example/fallback.jpg",
                },
            ]
        }


class _CozeClient:
    async def search_kb(self, kb_name: str, query: str) -> Any:
        assert kb_name == "case_studies"
        assert query == "晒斑改善案例"
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    content='<img src="https://cdn.example/kb-case.jpg"> 同类参考',
                    document_id="case-doc-1",
                )
            ]
        )


class _FailingCozeClient:
    async def search_kb(self, _kb_name: str, _query: str) -> Any:
        raise RuntimeError("kb unavailable")


class _Repository:
    def __init__(self) -> None:
        self.active_plan: dict[str, Any] = {}
        self.created_plan: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []
        self.updated_statuses: list[tuple[str, str]] = []
        self.candidates: list[dict[str, Any]] = []
        self.evaluated_fingerprints: set[str] = set()

    def list_outreach_candidates(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [dict(item) for item in self.candidates]

    def get_active_outreach_plan_for_customer(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(self.active_plan)

    def recent_customer_context(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"memory": {"last_customer_message_at": "2026-07-27T10:00:00+08:00"}}

    def add_outreach_event(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(kwargs)
        payload = kwargs.get("payload") if isinstance(kwargs.get("payload"), dict) else {}
        trigger = payload.get("trigger_context") if isinstance(payload.get("trigger_context"), dict) else {}
        fingerprint = str(trigger.get("conversation_fingerprint") or "")
        if fingerprint:
            self.evaluated_fingerprints.add(fingerprint)
        return {"event_id": f"event-{len(self.events)}"}

    def has_outreach_evaluation_fingerprint(
        self,
        *,
        conversation_fingerprint: str,
        **_kwargs: Any,
    ) -> bool:
        return conversation_fingerprint in self.evaluated_fingerprints

    def update_outreach_plan_status(self, plan_id: str, status: str) -> dict[str, Any]:
        self.updated_statuses.append((plan_id, status))
        if status in {"cancelled", "completed"}:
            self.active_plan = {}
        else:
            self.active_plan = {
                "plan": {"id": plan_id, "status": status},
                "tasks": self.created_plan.get("tasks") or [],
                "events": [],
            }
        return dict(self.active_plan) if self.active_plan else {"plan": {"id": plan_id, "status": status}}

    def get_outreach_plan(self, plan_id: str) -> dict[str, Any]:
        if self.active_plan and (self.active_plan.get("plan") or {}).get("id") == plan_id:
            return dict(self.active_plan)
        if plan_id == "plan-created":
            return {
                "plan": {
                    "id": plan_id,
                    "status": "draft",
                    "customer_id": self.created_plan.get("customer_id"),
                },
                "tasks": self.created_plan.get("tasks") or [],
                "events": [],
            }
        return {}

    def create_outreach_plan(self, **kwargs: Any) -> dict[str, Any]:
        self.created_plan = kwargs
        snapshot = kwargs.get("source_snapshot") if isinstance(kwargs.get("source_snapshot"), dict) else {}
        trigger = snapshot.get("trigger_context") if isinstance(snapshot.get("trigger_context"), dict) else {}
        fingerprint = str(trigger.get("conversation_fingerprint") or "")
        if fingerprint:
            self.evaluated_fingerprints.add(fingerprint)
        return {
            "plan": {"id": "plan-created", "status": "draft"},
            "tasks": kwargs["tasks"],
            "events": [],
        }


def _monitor_candidate(*, customer_at: str, staff_at: str) -> dict[str, Any]:
    return {
        "customer_id": "22000001",
        "corp_id": "corp-1",
        "user_id": "7294",
        "wechat": "DY258",
        "external_userid": "external-1",
        "sales_contact_started_at": "2000-01-01T00:00:00+08:00",
        "last_customer_message_at": customer_at,
        "latest_outbound_message_at": staff_at,
        "reply_wait_minutes": 10,
        "awaiting_customer_reply": True,
        "last_manual_takeover_at": "",
    }


class _ConversationSystemClient:
    def __init__(self, *, deleted: bool = False) -> None:
        self.deleted = deleted

    async def conversation(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "data": {
                "messages": [],
                "customer_relation": {
                    "status": "deleted" if self.deleted else "active",
                    "is_deleted": self.deleted,
                    "deleted_at": "2026-07-29T10:00:00+08:00" if self.deleted else None,
                    "updated_at": "2026-07-29T10:00:00+08:00",
                },
            }
        }


class _MonitorOutreachService(OutreachService):
    def __init__(
        self,
        *,
        repository: _Repository,
        model_client: _ModelClient,
        refreshed_messages: list[dict[str, Any]],
        deleted: bool = False,
    ) -> None:
        super().__init__(
            repository=repository,
            model_client=model_client,
            system_client=_ConversationSystemClient(),
        )
        self.refreshed_messages = refreshed_messages
        self.deleted = deleted

    async def refresh_customer_conversation(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "messages": list(self.refreshed_messages),
            "customer_relation": {
                "available": True,
                "status": "deleted" if self.deleted else "active",
                "is_deleted": self.deleted,
                "deleted_at": "2026-07-29T10:00:00+08:00" if self.deleted else "",
                "updated_at": "2026-07-29T10:00:00+08:00",
            },
            "latest_customer_message_at": self._latest_message_time(
                self.refreshed_messages,
                sender="customer",
            ),
            "latest_staff_message_at": self._latest_message_time(
                self.refreshed_messages,
                sender="staff",
            ),
        }

    async def _load_monitor_customer_context(
        self,
        *,
        identity: dict[str, Any],
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        del identity, memory
        return {"source": "platform_agent", "orders": []}


if __name__ == "__main__":
    unittest.main()
