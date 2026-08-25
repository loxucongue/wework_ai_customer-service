from __future__ import annotations

import asyncio
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

from app.config import Settings
from app.services.sop_platform_client import SopPlatformClient, SopPlatformTaskStateError
from app.services.sop_objection_material_service import SopObjectionMaterialService
from app.services.sop_platform_task_service import (
    SOP_PLATFORM_KNOWLEDGE_TASK_PROMPT,
    SOP_PLATFORM_TASK_SYSTEM_PROMPT,
    SopPlatformTaskService,
    _task_timing,
)
from app.services.sop_platform_scenes import (
    SOP_PLATFORM_CALLBACK_SCENES,
    SOP_PLATFORM_KNOWLEDGE_SCENE_CODES,
    SOP_PLATFORM_MODEL_SCENE_CODES,
    SOP_PLATFORM_SCENES,
    SOP_PLATFORM_TECHNICAL_SCENE_CODES,
    sop_platform_callback_scene,
)
from app.services.sop_reply_pack_service import SopReplyPackService


class SopPlatformTaskFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_platform_scene_registry_has_30_stable_combinations(self) -> None:
        self.assertEqual(len(SOP_PLATFORM_SCENES), 30)
        self.assertEqual(len(SOP_PLATFORM_MODEL_SCENE_CODES), 23)
        self.assertEqual(len(SOP_PLATFORM_TECHNICAL_SCENE_CODES), 7)
        self.assertNotIn("platform_direct", SOP_PLATFORM_SCENES)
        self.assertNotIn("first_day_opened_no_send", SOP_PLATFORM_SCENES)
        self.assertIn("quiet_first_add_backlog", SOP_PLATFORM_TECHNICAL_SCENE_CODES)
        self.assertIn("no_send_contact_cooldown", SOP_PLATFORM_TECHNICAL_SCENE_CODES)
        self.assertIn("no_send_contact_send_limit", SOP_PLATFORM_TECHNICAL_SCENE_CODES)
        self.assertIn("no_send_platform_content_conflict", SOP_PLATFORM_MODEL_SCENE_CODES)

    def test_platform_callback_registry_only_exposes_customer_and_runtime_states(self) -> None:
        self.assertEqual(len(SOP_PLATFORM_CALLBACK_SCENES), 9)
        self.assertEqual(
            sop_platform_callback_scene(
                internal_scene_code="ai_service_unopened_passthrough",
                sent=True,
            ).code,
            "customer_unopened",
        )
        self.assertEqual(
            sop_platform_callback_scene(
                internal_scene_code="normal_activity_price",
                sent=True,
            ).code,
            "customer_opened",
        )
        self.assertEqual(
            sop_platform_callback_scene(
                internal_scene_code="no_send_downstream_rejected",
                sent=False,
            ).code,
            "rejected",
        )
        for internal_code in (
            "no_send_explicit_stop_contact",
            "no_send_complaint_or_refund",
            "no_send_health_risk",
            "no_send_paid_or_appointment_conflict",
        ):
            self.assertEqual(
                sop_platform_callback_scene(
                    internal_scene_code=internal_code,
                    sent=False,
                ).code,
                "customer_state_blocked",
            )

    def test_platform_knowledge_mapping_uses_real_knowledge_semantics(self) -> None:
        self.assertEqual(SOP_PLATFORM_KNOWLEDGE_SCENE_CODES[8], "objection_effect_recurrence")
        self.assertEqual(SOP_PLATFORM_KNOWLEDGE_SCENE_CODES[9], "objection_price_hidden_charge")
        self.assertEqual(SOP_PLATFORM_KNOWLEDGE_SCENE_CODES[10], "objection_price_hidden_charge")

    def test_platform_poll_default_is_ten_seconds(self) -> None:
        self.assertEqual(Settings.model_fields["sop_platform_poll_seconds"].default, 10.0)

    def test_platform_quiet_hours_defaults_match_production_contract(self) -> None:
        self.assertTrue(Settings.model_fields["sop_platform_quiet_hours_enabled"].default)
        self.assertEqual(Settings.model_fields["sop_platform_quiet_start_hour"].default, 0)
        self.assertEqual(Settings.model_fields["sop_platform_quiet_end_hour"].default, 8)
        self.assertEqual(Settings.model_fields["sop_platform_quiet_first_add_grace_minutes"].default, 30)
        self.assertEqual(Settings.model_fields["sop_platform_max_task_age_seconds"].default, 600)

    async def test_user_wechat_scope_no_longer_filters_ai_service_tasks(self) -> None:
        model = _Model([{"decision": "send", "reason": "handled", "reply_messages": [_text("模型回复")]}])
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=True)
        task["user_wechat"] = "SL0069"

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertGreater(system.conversation_calls, 0)
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(repo.tasks["platform-sop:101"]["status"], "sent")

    async def test_missing_scope_configuration_does_not_hold_task(self) -> None:
        model = _Model([{"decision": "send", "reason": "handled", "reply_messages": [_text("模型回复")]}])
        service, repo, platform, system = _service(model=model)

        result = await service.process_task(_task(use_ai_copy=True))

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertGreater(system.conversation_calls, 0)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_completed")
        audit = repo.tasks["platform-sop:101"]["send_payload"]
        callback = platform.rule_data_calls[-1]
        self.assertEqual(
            audit["rule_data_request"],
            {
                "taskId": callback["task_id"],
                "sceneName": callback["scene_name"],
                "sceneCode": callback["scene_code"],
                "sendStatus": callback["send_status"],
                "remark": callback["remark"],
                "sendContent": callback["send_content"],
            },
        )
        self.assertEqual(audit["rule_data_response"]["code"], 200)

    async def test_cancelled_platform_task_becomes_terminal_instead_of_recovering(self) -> None:
        service, repo, platform, system = _service(model=_Model([]))
        platform.consume_responses.append(
            SopPlatformTaskStateError(
                state="已取消",
                payload={"code": 400, "message": "任务不可消费（当前状态：已取消）", "data": None},
            )
        )

        result = await service.process_task(_task(), recovery_status="platform_claiming")

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(result["reason"], "platform_task_cancelled")
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_cancelled")
        self.assertEqual(repo.tasks["platform-sop:101"]["status"], "completed_without_send")
        self.assertEqual(repo.tasks["platform-sop:101"]["error"], "platform_task_cancelled")
        self.assertEqual(system.send_calls, [])

    async def test_completed_platform_task_becomes_terminal_instead_of_recovering(self) -> None:
        service, repo, platform, system = _service(model=_Model([]))
        platform.consume_responses.append(
            SopPlatformTaskStateError(
                state="已完成",
                payload={"code": 400, "message": "任务不可消费（当前状态：已完成）", "data": None},
            )
        )

        result = await service.process_task(_task(), recovery_status="platform_claiming")

        self.assertEqual(result["reason"], "platform_task_already_completed")
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_completed")
        self.assertEqual(repo.tasks["platform-sop:101"]["status"], "completed_without_send")
        self.assertEqual(system.send_calls, [])

    async def test_any_user_wechat_continues_existing_model_chain(self) -> None:
        model = _Model([{"decision": "send", "reason": "test", "reply_messages": [_text("正常发送")] }])
        service, _repo, platform, system = _service(model=model)

        result = await service.process_task(_task(use_ai_copy=True))

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertGreater(system.conversation_calls, 0)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(system.send_calls), 1)

    def test_task_payload_times_are_not_used_as_first_add_time(self) -> None:
        task = {
            "firstAddedAt": datetime.now(timezone.utc).isoformat(),
            "operateTime": datetime.now(timezone.utc).isoformat(),
            "createTime": datetime.now(timezone.utc).isoformat(),
            "scene": {"createTime": datetime.now(timezone.utc).isoformat()},
            "scheduledAt": datetime.now(timezone.utc).isoformat(),
        }

        timing = _task_timing(task)

        self.assertEqual(timing["first_added_at"], "")
        self.assertEqual(timing["first_added_at_source"], "")

    def test_first_add_time_uses_conversation_added_at_only(self) -> None:
        task = {
            "firstAddedAt": "2026-08-11 09:30:00",
            "operateTime": "2026-08-11 09:31:00",
            "scheduledAt": "2026-08-11 10:00:00",
        }

        timing = _task_timing(
            task,
            conversation_added_at="2026-07-16T10:32:22+08:00",
        )

        self.assertEqual(timing["first_added_at_source"], "conversation.added_at")
        self.assertEqual(timing["first_added_at"], "2026-07-16T10:32:22+08:00")

    def test_platform_prompt_deprecates_dispatch_mode_and_use_ai_copy(self) -> None:
        self.assertIs(SOP_PLATFORM_TASK_SYSTEM_PROMPT, SOP_PLATFORM_KNOWLEDGE_TASK_PROMPT)
        self.assertNotIn("dispatchMode", SOP_PLATFORM_TASK_SYSTEM_PROMPT)
        self.assertNotIn("useAiCopy", SOP_PLATFORM_TASK_SYSTEM_PROMPT)
        self.assertIn("图片、视频、链接和预约金卡", SOP_PLATFORM_TASK_SYSTEM_PROMPT)

    def test_platform_knowledge_prompt_has_current_business_contract(self) -> None:
        for item in (
            "发送审核与轻量润色节点",
            "`task.required_delivery` 是本次必须审核的唯一消息来源",
            "默认发送",
            "普通沉默、考虑、距离、价格、效果或时间顾虑都不是不发送理由",
            "`scene_catalog` 是唯一合法业务场景",
            "不得创造新标签",
            "不得生成新素材或卡片",
            "使用中性称谓",
            "必须与所选 `sceneCode`",
            "不要输出 `sceneName`",
        ):
            self.assertIn(item, SOP_PLATFORM_KNOWLEDGE_TASK_PROMPT)

    async def test_model_input_labels_original_content_as_prioritized_campaign_intent(self) -> None:
        model = _Model([{"decision": "send", "reason": "test", "reply_messages": [_text("知识库生成")]}])
        service, _repo, _platform, _system = _service(model=model)
        task = _task(use_ai_copy=True)
        task.update(
            {
                "ruleId": 81,
                "ruleName": "沉默客户价值触达",
                "sceneId": 23,
                "senderType": "online_service",
                "dispatchMode": "scheduled",
                "scene": {"sceneName": "效果价值", "sceneDesc": "补充真实效果价值"},
            }
        )

        await service.process_task(task)

        self.assertEqual(len(model.calls), 1)
        payload = json.loads(model.calls[0][1]["content"])
        self.assertEqual(
            payload["task"]["original_message_content_role"],
            "locked_platform_delivery_model_may_only_review_or_polish",
        )
        self.assertNotIn("dispatch_mode", payload["task"])
        self.assertEqual(payload["task"]["scene_role"], "current_delivery_context")
        self.assertEqual(payload["task"]["platform_metadata"]["rule_id"], 81)
        self.assertEqual(payload["task"]["platform_metadata"]["scene_id"], 23)
        self.assertEqual(payload["task"]["original_message_content"], [_text("平台原文")])
        self.assertEqual(payload["task"]["required_delivery"], [_text("平台原文")])
        self.assertNotIn("sender_type", payload["task"]["platform_metadata"])
        self.assertNotIn("knowledge_base", payload)
        self.assertIn("authoritative_business_facts", payload)
        self.assertEqual(len(payload["scene_catalog"]), 23)
        self.assertTrue(
            {item["sceneCode"] for item in payload["scene_catalog"]}.isdisjoint(
                SOP_PLATFORM_TECHNICAL_SCENE_CODES
            )
        )
        self.assertEqual(len(payload["knowledge_scene_catalog"]), 14)

    async def test_context_uses_80_message_timeline_with_beijing_time_scale(self) -> None:
        model = _Model([{"decision": "send", "reason": "test", "reply_messages": [_text("知识库生成")]}])
        service, _repo, _platform, system = _service(model=model)
        system.conversation_payload["data"]["added_at"] = "2026-07-16T10:32:22+08:00"
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer" if index % 2 else "assistant",
                "content": f"message-{index}",
                "msgtime": 1_785_397_200_000 + index * 60_000,
            }
            for index in range(90)
        ]

        await service.process_task(_task(use_ai_copy=True))

        self.assertEqual(system.conversation_limits[0], 80)
        payload = json.loads(model.calls[0][1]["content"])
        timeline = payload["latest_context"]["conversation_timeline"]
        self.assertEqual(len(timeline), 80)
        self.assertEqual(timeline[0]["content"], "message-10")
        self.assertIn("occurred_at_beijing", timeline[0])
        self.assertIn("time_ago", timeline[0])
        self.assertIn("gap_from_previous", timeline[1])
        self.assertEqual(payload["latest_context"]["timeline_structure"]["latest_message_role"], "customer")
        self.assertFalse(payload["latest_context"]["timeline_structure"]["assistant_after_latest_customer"])
        self.assertNotIn("task_timing", payload["latest_context"])
        self.assertEqual(payload["task"]["timing"]["first_added_at"], "2026-07-16T10:32:22+08:00")
        self.assertEqual(payload["task"]["timing"]["first_added_at_source"], "conversation.added_at")

    async def test_platform_knowledge_base_is_not_loaded_for_model_decision(self) -> None:
        model = _Model([{"decision": "send", "reason": "material", "reply_messages": [_text("自然承接")]}])
        material_service = _Materials()
        service, _repo, _platform, system = _service(model=model, material_service=material_service)
        task = _task(use_ai_copy=True)
        task["triggerEvent"] = "add_wecom"
        task["firstAddedAt"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()

        await service.process_task(task)

        payload = json.loads(model.calls[0][1]["content"])
        self.assertEqual(payload["task"]["task_type"], "add_wecom")
        self.assertNotIn("knowledge_base", payload)
        self.assertEqual(_platform.knowledge_base_calls, 0)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("自然承接")])

    async def test_opened_customer_ignores_use_ai_copy_and_uses_model_final_messages(self) -> None:
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "still useful",
                    "reply_messages": [
                        _text("模型自然润色"),
                        _image("https://cdn.example/platform-original.jpg", order=2),
                    ],
                }
            ]
        )
        service, repo, platform, system = _service(model=model)
        task = _task(
            use_ai_copy=False,
            message_content=[
                {"type": "text", "content": "平台原文"},
                {"type": "image", "content": "https://cdn.example/platform-original.jpg"},
            ],
        )

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(
            system.send_calls[0]["reply_messages"],
            [
                _text("模型自然润色"),
                _image("https://cdn.example/platform-original.jpg", order=2),
            ],
        )
        self.assertEqual(system.send_calls[0]["plan_id"], "platform-sop-101")
        self.assertEqual(system.send_calls[0]["task_id"], "101")
        self.assertEqual(system.send_calls[0]["run_id"], 11)
        self.assertEqual(system.send_calls[0]["rule_id"], 3)
        self.assertEqual(system.send_calls[0]["rule_name"], "test rule")
        self.assertEqual(system.send_calls[0]["rule_task_id"], 15)
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_completed")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(system.conversation_calls, 2)

    async def test_duplicate_ai_service_platform_task_is_completed_without_resend(self) -> None:
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "first useful",
                    "reply_messages": [_text("模型擅自改写")],
                }
            ]
        )
        service, repo, platform, system = _service(model=model)
        first = _task(use_ai_copy=False, message_content=[{"type": "text", "content": "平台原文"}])
        first["task_id"] = 101
        first["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        second = _task(use_ai_copy=False, message_content=[{"type": "text", "content": "平台原文"}])
        second["task_id"] = 102
        second["scheduledAt"] = first["scheduledAt"]

        first_result = await service.process_task(first)
        second_result = await service.process_task(second)

        self.assertEqual(first_result["status"], "sent")
        self.assertEqual(second_result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30), ("102", 20), ("102", 70)])
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("模型擅自改写")])
        second_payload = repo.tasks["platform-sop:102"]["send_payload"]
        self.assertEqual(second_payload["decision"]["reason"], "duplicate_platform_task_content")
        self.assertEqual(platform.rule_data_calls[-1]["task_id"], "102")
        self.assertEqual(platform.rule_data_calls[-1]["scene_code"], "duplicate_blocked")
        self.assertEqual(platform.rule_data_calls[-1]["send_status"], 20)
        self.assertEqual(platform.rule_data_calls[-1]["send_content"], "")
        self.assertEqual(repo.events["platform_sop_task:102"]["status"], "platform_no_send")
        self.assertEqual(len(model.calls), 1)

    async def test_stale_task_still_follows_current_opening_route(self) -> None:
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "still useful",
                    "reply_messages": [_text("模型擅自改写")],
                }
            ]
        )
        service, _repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False)
        task["scheduledAt"] = time.time() - 86400

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(len(model.calls), 1)
        self.assertGreater(system.conversation_calls, 0)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("模型擅自改写")])

    async def test_ai_service_task_nine_minutes_late_is_still_processed(self) -> None:
        model = _Model([{"decision": "send", "reason": "within grace", "reply_messages": [_text("正常发送")]}])
        service, _repo, platform, system = _service(model=model)
        task = _task()
        task["scheduledAt"] = time.time() - 9 * 60

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("正常发送")])

    async def test_task_more_than_ten_minutes_late_is_still_processed(self) -> None:
        model = _Model([{"decision": "send", "reason": "still due", "reply_messages": [_text("继续处理")]}])
        service, _repo, platform, system = _service(model=model)
        task = _task()
        task["scheduledAt"] = time.time() - 10 * 60 - 1

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(len(model.calls), 1)
        self.assertGreater(system.conversation_calls, 0)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("继续处理")])

    async def test_pre_cutover_task_is_no_longer_suppressed(self) -> None:
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "manual resend",
                    "reply_messages": [_text("模型擅自改写")],
                }
            ]
        )
        settings = _settings()
        settings.sop_platform_live_not_before = "2999-01-01T00:00:00+08:00"
        service, repo, platform, system = _service(model=model, settings=settings)
        task = _task(use_ai_copy=False)

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("模型擅自改写")])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("模型擅自改写")])
        self.assertEqual(system.send_calls[0]["plan_id"], "platform-sop-101")
        self.assertEqual(system.send_calls[0]["task_id"], "101")
        self.assertEqual(system.send_calls[0]["run_id"], 11)
        self.assertEqual(system.send_calls[0]["rule_id"], 3)
        self.assertEqual(system.send_calls[0]["rule_name"], "test rule")
        self.assertEqual(system.send_calls[0]["rule_task_id"], 15)
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_completed")
        self.assertEqual(next(iter(repo.tasks.values()))["status"], "sent")

    async def test_manual_resend_rejects_already_sent_task(self) -> None:
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "send",
                    "reply_messages": [_text("模型擅自改写")],
                }
            ]
        )
        service, _repo, _platform, _system = _service(model=model)
        await service.process_task(_task(use_ai_copy=False))

        with self.assertRaisesRegex(RuntimeError, "already sent"):
            await service.admin_resend_task("101")

    async def test_manual_resend_allows_stale_failed_task(self) -> None:
        model = _Model([{"decision": "send", "reason": "resend", "reply_messages": [_text("补发内容")]}])
        service, repo, platform, system = _service(model=model)
        settings = service.settings
        settings.sop_platform_max_task_age_seconds = 1
        task = _task(use_ai_copy=False)
        task["scheduledAt"] = time.time() - 3600

        _event, local_task = service._ensure_local_task(task, status="completed_without_send")
        repo.update_sop_send_task(str(local_task["id"]), status="completed_without_send", error="downstream_delivery_rejected")
        repo.update_sop_event_status("platform_sop_task:101", status="platform_completed")

        result = await service.admin_resend_task("101")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("补发内容")])
        self.assertEqual(platform.consume_calls, [])
        self.assertEqual(repo.tasks["platform-sop:101"]["status"], "sent")

    async def test_deprecated_direct_mode_still_obeys_quiet_hours(self) -> None:
        model = _Model([])
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_max_task_age_seconds = 0
        service, repo, platform, system = _service(model=model, settings=settings)
        task = _task(use_ai_copy=False, dispatch_mode="direct")
        task["triggerEvent"] = "schedule"
        task["scheduledAt"] = _beijing_epoch("2026-08-05 01:00:00")

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(system.conversation_calls, 0)
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(payload["decision"]["reason"], "quiet_hours_marketing_blocked")
        self.assertEqual(payload["context"]["dispatch_mode"], "deprecated_ignored")

    async def test_quiet_hours_blocks_ai_copy_marketing_before_model(self) -> None:
        model = _Model([{"decision": "send", "reason": "recent first add", "reply_messages": [_text("模型触达")]}])
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_max_task_age_seconds = 0
        service, _repo, platform, system = _service(model=model, settings=settings)
        task = _task(use_ai_copy=True)
        task["triggerEvent"] = "schedule"
        task["scheduledAt"] = _beijing_epoch("2026-08-05 00:00:00")

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(system.conversation_calls, 0)
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])

    async def test_quiet_hours_allows_recent_first_add_auto_opening_then_sends_platform_original(self) -> None:
        model = _Model([])
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_max_task_age_seconds = 0
        service, _repo, platform, system = _service(model=model, settings=settings)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
        current_beijing = datetime.now(timezone(timedelta(hours=8)))
        scheduled_at = current_beijing.replace(hour=1, minute=0, second=0, microsecond=0)
        if scheduled_at > current_beijing:
            scheduled_at -= timedelta(days=1)
        task["scheduledAt"] = scheduled_at.timestamp()
        task["operateTime"] = (scheduled_at - timedelta(minutes=29)).timestamp()
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "我已经添加了你，现在我们可以开始聊天了。",
                "msgtime": (scheduled_at - timedelta(minutes=29)).timestamp() * 1000,
            }
        ]
        system.conversation_payload["data"]["added_at"] = scheduled_at.isoformat()

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertGreaterEqual(system.conversation_calls, 2)
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("平台原文")])
        self.assertEqual(len(model.calls), 0)

    async def test_deprecated_direct_mode_unopened_sends_original_without_model(self) -> None:
        model = _Model([])
        service, repo, platform, system = _service(model=model)
        original_messages = [
            {"type": "text", "content": "平台首日原文"},
            {"type": "image", "content": "https://cdn.example/first-day.jpg"},
        ]
        task = _task(use_ai_copy=True, dispatch_mode="direct", message_content=original_messages)
        task["triggerEvent"] = "add_wecom"
        task["operateTime"] = datetime.now(timezone.utc).isoformat()
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "我已经添加了你，现在我们可以开始聊天了。",
                "msgtime": int(time.time() * 1000),
            }
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(
            system.send_calls[0]["reply_messages"],
            [_text("平台首日原文"), {"type": "image", "order": 2, "content": {"url": "https://cdn.example/first-day.jpg"}}],
        )
        self.assertEqual(model.calls, [])
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(
            payload["decision"]["reason"],
            "unopened_or_conversation_unavailable_platform_passthrough",
        )
        self.assertEqual(
            payload["context"]["dispatch_mode"],
            "deprecated_ignored",
        )

    async def test_deprecated_direct_mode_does_not_bypass_media_deduplication(self) -> None:
        media_url = "https://cdn.example/first-day.jpg"
        model = _Model([])
        service, repo, platform, system = _service(model=model)
        repo.list_platform_sop_task_records = lambda **_kwargs: [  # type: ignore[attr-defined]
            _sent_platform_record(task_id="100", messages=[_image(media_url)])
        ]
        task = _task(
            use_ai_copy=True,
            dispatch_mode="direct",
            message_content=[
                {"type": "text", "content": "平台首日原文"},
                {"type": "image", "content": media_url},
            ],
        )
        task["triggerEvent"] = "add_wecom"
        task["operateTime"] = datetime.now(timezone.utc).isoformat()
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "我已经添加了你，现在我们可以开始聊天了。",
                "msgtime": int(time.time() * 1000),
            }
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(model.calls, [])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["send_payload"]["decision"]["reason"], "duplicate_media_delivery")
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_no_send")

    async def test_first_day_payment_card_marker_becomes_payment_collection(self) -> None:
        model = _Model([])
        service, _repo, platform, system = _service(model=model)
        task = _task(
            use_ai_copy=False,
            dispatch_mode="direct",
            message_content=[
                {"type": "text", "content": "活动规则已经给您说明了。"},
                {"type": "text", "content": " 预约卡片 "},
            ],
        )
        task["triggerEvent"] = "add_wecom"
        task["operateTime"] = datetime.now(timezone.utc).isoformat()
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "我已经添加了你，现在我们可以开始聊天了。",
                "msgtime": int(time.time() * 1000),
            }
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(model.calls, [])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(
            system.send_calls[0]["reply_messages"],
            [
                _text("活动规则已经给您说明了。"),
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ],
        )

    async def test_payment_card_phrase_is_not_treated_as_marker(self) -> None:
        model = _Model([])
        service, _repo, _platform, system = _service(model=model)
        task = _task(
            use_ai_copy=False,
            dispatch_mode="direct",
            message_content=[{"type": "text", "content": "我给您说明一下预约卡片怎么使用。"}],
        )
        task["triggerEvent"] = "add_wecom"
        task["operateTime"] = datetime.now(timezone.utc).isoformat()
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "我已经添加了你，现在我们可以开始聊天了。",
                "msgtime": int(time.time() * 1000),
            }
        ]

        await service.process_task(task)

        self.assertEqual(
            system.send_calls[0]["reply_messages"],
            [_text("我给您说明一下预约卡片怎么使用。")],
        )

    async def test_second_day_unopened_ai_service_still_sends_platform_original(self) -> None:
        model = _Model([])
        service, _repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
        task["operateTime"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["added_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat()
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "我已经添加了你，现在我们可以开始聊天了。",
                "msgtime": int(time.time() * 1000),
            }
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("平台原文")])
        self.assertEqual(len(model.calls), 0)

    async def test_second_day_opened_ai_service_uses_knowledge_model_chain(self) -> None:
        model = _Model([{"decision": "send", "reason": "second day effect", "reply_messages": [_text("轻触达效果")] }])
        service, _repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["added_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat()
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "之前咨询过效果",
                "msgtime": int((time.time() - 24 * 60 * 60) * 1000),
            }
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("轻触达效果")])
        self.assertEqual(len(model.calls), 1)

    async def test_second_day_model_can_preserve_payment_collection_marker(self) -> None:
        payment_message = {
            "type": "payment_collection",
            "order": 2,
            "content": {"amount": 10, "remark": ""},
        }
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "preserve platform payment intent",
                    "reply_messages": [_text("活动规则已经给您说明了。"), payment_message],
                }
            ]
        )
        service, _repo, _platform, system = _service(model=model)
        task = _task(
            use_ai_copy=True,
            message_content=[
                {"type": "text", "content": "活动规则已经给您说明了。"},
                {"type": "text", "content": "预约卡片"},
            ],
        )
        task["triggerEvent"] = "add_wecom"
        task["operateTime"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["added_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat()

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        payload = json.loads(model.calls[0][1]["content"])
        self.assertEqual(payload["task"]["original_message_content"][1], payment_message)
        self.assertEqual(
            payload["task"]["original_message_content_role"],
            "locked_platform_delivery_model_may_only_review_or_polish",
        )
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("活动规则已经给您说明了。"), payment_message])

    async def test_model_cannot_remove_platform_payment_collection_candidate(self) -> None:
        payment_message = {
            "type": "payment_collection",
            "order": 2,
            "content": {"amount": 10, "remark": ""},
        }
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "marker omitted",
                    "reply_messages": [_text("活动规则已经给您说明了。")],
                },
                {
                    "decision": "send",
                    "reason": "marker restored",
                    "reply_messages": [_text("活动规则已经给您说明了。"), payment_message],
                },
            ]
        )
        service, _repo, _platform, system = _service(model=model)
        task = _task(
            use_ai_copy=True,
            message_content=[
                {"type": "text", "content": "活动规则已经给您说明了。"},
                {"type": "text", "content": "预约卡片"},
            ],
        )
        task["triggerEvent"] = "add_wecom"
        task["operateTime"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["added_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat()

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(
            system.send_calls[0]["reply_messages"],
            [_text("活动规则已经给您说明了。"), payment_message],
        )

    async def test_first_day_opened_customer_uses_model_chain(self) -> None:
        model = _Model([{"decision": "send", "reason": "reviewed", "reply_messages": [_text("模型最终消息")]}])
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=True)
        task["triggerEvent"] = "add_wecom"
        task["operateTime"] = datetime.now(timezone.utc).isoformat()
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["added_at"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "你好，在吗",
                "msgtime": int(time.time() * 1000),
            }
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("模型最终消息")])
        self.assertEqual(len(model.calls), 1)
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(payload["decision"]["reason"], "reviewed")
        self.assertTrue(payload["context"]["opening_state"]["first_added_today"])

    async def test_ai_service_without_real_customer_message_sends_platform_original_without_model(self) -> None:
        model = _Model([])
        service, repo, platform, system = _service(model=model)
        original_messages = [
            {"type": "text", "content": "平台未开口原文"},
            {"type": "image", "content": "https://cdn.example/unopened.jpg"},
        ]
        task = _task(use_ai_copy=True, message_content=original_messages)
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["added_at"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "我已经添加了你，现在我们可以开始聊天了。",
                "msgtime": int(time.time() * 1000),
            }
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(model.calls, [])
        self.assertEqual(
            system.send_calls[0]["reply_messages"],
            [_text("平台未开口原文"), _image("https://cdn.example/unopened.jpg", order=2)],
        )
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(
            payload["decision"]["reason"],
            "unopened_or_conversation_unavailable_platform_passthrough",
        )
        callback = platform.rule_data_calls[-1]
        self.assertEqual(callback["task_id"], "101")
        self.assertEqual(callback["scene_name"], "客户未开口")
        self.assertEqual(callback["scene_code"], "customer_unopened")
        self.assertEqual(callback["send_status"], 10)
        self.assertIsNone(callback["knowledge_id"])
        self.assertIsNone(callback["knowledge_paragraph_no"])
        self.assertEqual(
            callback["send_content"],
            "平台未开口原文\n[image]https://cdn.example/unopened.jpg",
        )

    async def test_unopened_customer_sends_platform_original_without_loading_paid_state(self) -> None:
        model = _Model([])
        service, repo, platform, system = _service(model=model)
        service.customer_context_service.payload = {
            "source": "platform_agent",
            "orders": [
                {
                    "id": 88,
                    "status": "pending",
                    "prepay_paid": 10,
                    "paid_protection_status": "protected",
                }
            ],
            "appointment": {},
        }
        system.conversation_payload["data"]["messages"] = []

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("平台原文")])
        self.assertEqual(model.calls, [])
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(payload["decision"]["reason_code"], "send")
        self.assertEqual(service.customer_context_service.calls, 0)

    async def test_conversation_failure_sends_platform_original_and_consumes(self) -> None:
        model = _Model([])
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
        task["operateTime"] = datetime.now(timezone.utc).isoformat()
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_error = RuntimeError("conversation timeout")

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("平台原文")])
        self.assertEqual(model.calls, [])
        self.assertEqual(next(iter(repo.tasks.values()))["status"], "sent")

    async def test_terminal_downstream_send_rejection_is_audited_and_consumed(self) -> None:
        model = _Model([{"decision": "send", "reason": "handled", "reply_messages": [_text("reply")]}])
        service, repo, platform, system = _service(model=model)
        system.send_error = RuntimeError(
            'outreach_system_http_409: {"detail":"conversation is in manual handoff mode"}'
        )

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(result["delivery_failure"]["http_status"], 409)
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["status"], "completed_without_send")
        self.assertEqual(stored["send_payload"]["decision"]["reason_code"], "no_send_downstream_rejected")
        self.assertEqual(stored["send_payload"]["attempted_decision"]["decision"], "send")
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_no_send")

    async def test_transient_downstream_send_failure_is_released_without_retry(self) -> None:
        model = _Model([{"decision": "send", "reason": "handled", "reply_messages": [_text("reply")]}])
        service, repo, platform, system = _service(model=model)
        system.send_error = RuntimeError('outreach_system_http_503: {"detail":"temporarily unavailable"}')

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "failed")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(next(iter(repo.tasks.values()))["status"], "failed")
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_failed")

    async def test_downstream_conversation_not_found_is_consumed_as_terminal_rejection(self) -> None:
        model = _Model([{"decision": "send", "reason": "handled", "reply_messages": [_text("reply")]}])
        service, repo, platform, system = _service(model=model)
        system.send_error = RuntimeError(
            'outreach_system_http_404: {"code":40402,"msg":"conversation not found","data":{}}'
        )

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "failed")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(next(iter(repo.tasks.values()))["status"], "failed")
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_failed")
        self.assertEqual(len(system.send_calls), 1)

    async def test_target_ai_disabled_is_consumed_as_no_send(self) -> None:
        model = _Model([{"decision": "send", "reason": "handled", "reply_messages": [_text("reply")]}])
        service, repo, platform, system = _service(model=model)
        system.send_error = RuntimeError(
            'outreach_system_http_409: {"code":40908,"msg":"AI outreach target is outside enabled AI scope",'
            '"data":{"reason_code":"target_ai_disabled"}}'
        )

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(next(iter(repo.tasks.values()))["status"], "completed_without_send")
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_no_send")

    async def test_human_takeover_status_is_consumed_without_model_or_send(self) -> None:
        model = _Model([{"decision": "send", "reason": "must not run", "reply_messages": [_text("reply")]}])
        service, repo, platform, system = _service(model=model)
        system.conversation_status_payload = {
            "code": 0,
            "data": {
                "takeover": {
                    "mode": "human",
                    "is_human": True,
                    "is_ai": False,
                    "handoff_status": "human_pending",
                },
                "ai_outreach": {
                    "send_allowed": False,
                    "reason_code": "handoff_human_active",
                },
            },
        }

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(system.conversation_status_calls, 1)
        self.assertEqual(system.conversation_calls, 0)
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])
        decision = repo.tasks["platform-sop:101"]["send_payload"]["decision"]
        self.assertEqual(decision["sceneCode"], "no_send_human_takeover")

    async def test_takeover_status_failure_is_consumed_without_send(self) -> None:
        model = _Model([{"decision": "send", "reason": "must not run", "reply_messages": [_text("reply")]}])
        service, repo, platform, system = _service(model=model)
        system.conversation_status_error = TimeoutError("status timeout")

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])
        decision = repo.tasks["platform-sop:101"]["send_payload"]["decision"]
        self.assertEqual(decision["reason"], "takeover_status_unavailable")

    async def test_same_contact_sop_send_within_five_minutes_is_consumed_without_send(self) -> None:
        model = _Model([{"decision": "send", "reason": "should not run", "reply_messages": [_text("unused")]}])
        service, repo, platform, system = _service(model=model)
        previous = _sent_platform_record(task_id="100", messages=[_text("previous")])
        previous["sent_at"] = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        repo.list_platform_sop_task_records = lambda **_kwargs: [previous]  # type: ignore[attr-defined]

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(model.calls, [])
        self.assertEqual(system.send_calls, [])
        stored = repo.tasks["platform-sop:101"]["send_payload"]
        self.assertEqual(stored["decision"]["reason"], "platform_contact_send_cooldown")
        self.assertEqual(stored["decision"]["sceneCode"], "no_send_contact_cooldown")
        self.assertEqual(platform.rule_data_calls[-1]["scene_code"], "frequency_blocked")
        self.assertEqual(platform.rule_data_calls[-1]["scene_name"], "频控拦截")
        self.assertEqual(
            stored["context"]["platform_contact_delivery_guard"]["successful_send_count_since_last_customer_reply"],
            1,
        )

    async def test_same_contact_can_send_more_than_two_sops_after_cooldown(self) -> None:
        model = _Model([{"decision": "send", "reason": "allowed", "reply_messages": [_text("new delivery")]}])
        service, repo, platform, system = _service(model=model)
        first = _sent_platform_record(task_id="99", messages=[_text("first")])
        second = _sent_platform_record(task_id="100", messages=[_text("second")])
        first["sent_at"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        second["sent_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        repo.list_platform_sop_task_records = lambda **_kwargs: [second, first]  # type: ignore[attr-defined]

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(system.send_calls), 1)

    async def test_non_sent_sop_tasks_do_not_count_toward_contact_limit(self) -> None:
        model = _Model([{"decision": "send", "reason": "allowed", "reply_messages": [_text("new delivery")]}])
        service, repo, platform, system = _service(model=model)
        first = _sent_platform_record(task_id="99", messages=[_text("first")])
        second = _sent_platform_record(task_id="100", messages=[_text("second")])
        first.update({"task_status": "completed_without_send", "sent_at": ""})
        second.update({"task_status": "failed", "sent_at": ""})
        repo.list_platform_sop_task_records = lambda **_kwargs: [second, first]  # type: ignore[attr-defined]

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(system.send_calls), 1)

    async def test_customer_reply_resets_contact_sop_send_limit(self) -> None:
        model = _Model([{"decision": "send", "reason": "allowed after reply", "reply_messages": [_text("new delivery")]}])
        service, repo, platform, system = _service(model=model)
        first = _sent_platform_record(task_id="99", messages=[_text("first")])
        second = _sent_platform_record(task_id="100", messages=[_text("second")])
        first["sent_at"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        second["sent_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        repo.list_platform_sop_task_records = lambda **_kwargs: [second, first]  # type: ignore[attr-defined]
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "new customer reply",
                "msgtime": int((time.time() - 30) * 1000),
            }
        ]

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(len(model.calls), 1)
        guard = repo.tasks["platform-sop:101"]["send_payload"]["context"]["platform_contact_delivery_guard"]
        self.assertEqual(guard["successful_send_count_since_last_customer_reply"], 0)

    async def test_customer_reply_does_not_bypass_absolute_five_minute_cooldown(self) -> None:
        model = _Model([{"decision": "send", "reason": "should not run", "reply_messages": [_text("unused")]}])
        service, repo, platform, system = _service(model=model)
        previous = _sent_platform_record(task_id="100", messages=[_text("previous")])
        previous["sent_at"] = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        repo.list_platform_sop_task_records = lambda **_kwargs: [previous]  # type: ignore[attr-defined]
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "new customer reply",
                "msgtime": int((time.time() - 30) * 1000),
            }
        ]

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(model.calls, [])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(
            repo.tasks["platform-sop:101"]["send_payload"]["decision"]["reason"],
            "platform_contact_send_cooldown",
        )

    async def test_transient_failure_is_attempted_once_then_released(self) -> None:
        model = _Model(
            [{"decision": "send", "reason": "handled", "reply_messages": [_text("reply")]}]
        )
        service, repo, platform, system = _service(model=model)
        system.send_error = RuntimeError('outreach_system_http_503: {"detail":"temporarily unavailable"}')
        task = _task()

        result = await service.process_task(task)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_failed")
        self.assertEqual(repo.events["platform_sop_task:101"]["retry_count"], 0)
        self.assertEqual(len(system.send_calls), 1)

    async def test_ai_service_customer_message_without_time_still_uses_model(self) -> None:
        model = _Model([{"decision": "send", "reason": "model owns semantics", "reply_messages": [_text("自然承接")]}])
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
        task["operateTime"] = datetime.now(timezone.utc).isoformat()
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["messages"] = [
            {"direction": "customer", "content": "你好，在吗"},
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("自然承接")])
        self.assertEqual(len(model.calls), 1)

    async def test_quiet_hours_blocks_inactive_first_add(self) -> None:
        model = _Model([])
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_max_task_age_seconds = 0
        service, repo, platform, system = _service(model=model, settings=settings)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
        task["scheduledAt"] = "2026-08-05 01:00:00"
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "我已经添加了你，现在我们可以开始聊天了。",
                "msgtime": _beijing_epoch("2026-08-05 00:30:00") * 1000,
            }
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(payload["decision"]["reason"], "quiet_hours_first_add_inactive")
        self.assertEqual(payload["context"]["quiet_hours"]["reference_at_beijing"], "2026-08-05 01:00:00")
        self.assertEqual(platform.rule_data_calls[-1]["scene_code"], "night_blocked")
        self.assertIn("次日08:30融合", platform.rule_data_calls[-1]["remark"])
        self.assertEqual(platform.rule_data_calls[-1]["send_content"], "")

    async def test_quiet_hours_blocks_first_add_with_unanswered_customer_message(self) -> None:
        model = _Model([])
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_max_task_age_seconds = 0
        service, repo, platform, system = _service(model=model, settings=settings)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
        task["scheduledAt"] = _beijing_epoch("2026-08-05 01:00:00")
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "你们店在哪里",
                "msgtime": _beijing_epoch("2026-08-05 00:55:00") * 1000,
            }
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(system.send_calls, [])
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(payload["decision"]["reason"], "quiet_hours_customer_pending_reply")

    async def test_quiet_hours_end_boundary_sends_fixed_copy(self) -> None:
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "business hours",
                    "reply_messages": [_text("模型擅自改写")],
                }
            ]
        )
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_max_task_age_seconds = 0
        service, _repo, platform, system = _service(model=model, settings=settings)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "schedule"
        task["scheduledAt"] = _beijing_epoch("2026-08-05 08:00:00")

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.conversation_calls, 2)
        self.assertEqual(len(system.send_calls), 1)

    async def test_no_send_is_business_success_without_customer_message(self) -> None:
        model = _Model(
            [
                {
                    "decision": "no_send",
                    "reason": "already paid",
                    "reason_code": "paid_or_appointment_conflict",
                    "reply_messages": [],
                }
            ]
        )
        service, repo, platform, system = _service(model=model)

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(next(iter(repo.tasks.values()))["status"], "completed_without_send")

    async def test_deleted_relation_skips_model_and_completes_task(self) -> None:
        model = _Model([])
        service, _repo, platform, system = _service(model=model)
        system.conversation_payload["data"]["customer_relation"] = {"status": "deleted", "is_deleted": True}

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(model.calls, [])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])

    async def test_first_add_invalid_no_send_reason_repairs_then_defaults_to_send(self) -> None:
        model = _Model(
            [
                {"decision": "no_send", "reason_code": "no_expressed_demand", "reason": "silent", "reply_messages": []},
                {"decision": "send", "reason": "repair to send", "reply_messages": [_text("修复后发送")]},
            ]
        )
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False, message_content=[{"type": "text", "content": "平台原文"}])
        task["triggerEvent"] = "add_wecom"

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("修复后发送")])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["send_payload"]["decision"]["reason"], "repair to send")

    async def test_first_add_allowed_no_send_reason_still_completes_without_send(self) -> None:
        model = _Model(
            [
                {
                    "decision": "no_send",
                    "reason_code": "explicit_stop_contact",
                    "reason": "customer explicitly asked to stop contact",
                    "reply_messages": [],
                }
            ]
        )
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=True)
        task["triggerEvent"] = "add_wecom"
        system.conversation_payload["data"]["messages"] = [
            {"direction": "customer", "content": "不要再联系我", "msgtime": _beijing_epoch("2026-08-05 10:00:00") * 1000}
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(system.send_calls, [])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["send_payload"]["decision"]["reason_code"], "no_send_explicit_stop_contact")

    async def test_ai_copy_can_only_rewrite_text_and_preserves_media(self) -> None:
        original_image = {"type": "image", "order": 2, "content": {"url": "https://cdn.example/a.jpg"}}
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "rewrite",
                    "reply_messages": [_text("自然改写"), original_image],
                }
            ]
        )
        service, _repo, _platform, system = _service(model=model)

        await service.process_task(
            _task(
                use_ai_copy=True,
                message_content=[
                    {"type": "text", "content": "平台文案"},
                    {"type": "image", "content": "https://cdn.example/a.jpg"},
                ],
            )
        )

        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("自然改写"), original_image])

    async def test_empty_platform_content_is_consumed_without_model(self) -> None:
        model = _Model([{"decision": "send", "reason": "scene", "reply_messages": [_text("结合场景生成")]}])
        service, _repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=True, message_content=[])
        task["scene"] = {"sceneDesc": "活动后续提醒", "knowledgeText": "仅说明检测流程"}

        result = await service.process_task(task)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])

    async def test_empty_platform_content_never_falls_back_to_knowledge_model(self) -> None:
        model = _Model([{"decision": "send", "reason": "knowledge fallback", "reply_messages": [_text("知识库轻触达")]}])
        service, _repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=True, message_content=[])
        task["scene"] = {}

        result = await service.process_task(task)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(model.calls, [])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])

    async def test_defer_output_is_repaired_and_never_becomes_platform_state(self) -> None:
        model = _Model(
            [
                {"decision": "defer", "delay_minutes": 30, "reason": "later", "reply_messages": []},
                {
                    "decision": "no_send",
                    "reason": "human takeover",
                    "reason_code": "human_takeover",
                    "reply_messages": [],
                },
            ]
        )
        service, _repo, platform, _system = _service(model=model)

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])

    async def test_model_failure_is_released_and_does_not_fake_success(self) -> None:
        model = _Model(
            [
                {"decision": "defer", "delay_minutes": 30, "reason": "later", "reply_messages": []},
                {"decision": "retry_later", "reason": "later", "reply_messages": []},
                {"decision": "defer_again", "reason": "later", "reply_messages": []},
            ]
        )
        service, repo, platform, system = _service(model=model)

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "failed")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_failed")

    async def test_illegal_scene_code_gets_two_repairs_then_is_released(self) -> None:
        invalid = {
            "decision": "send",
            "sceneCode": "invented_scene",
            "sceneEvidence": "invalid fixture",
            "reason": "invalid",
            "reply_messages": [_text("候选")],
        }
        model = _Model([invalid, invalid, invalid])
        service, repo, platform, system = _service(model=model)

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_failed")

    async def test_knowledge_scene_mismatch_is_repaired(self) -> None:
        model = _Model(
            [
                {
                    "decision": "send",
                    "sceneCode": "objection_effect_recurrence",
                    "sceneEvidence": "客户担心反弹",
                    "knowledgeId": 9,
                    "knowledgeParagraphNo": 1,
                    "reason": "wrong mapping",
                    "reply_messages": [_text("候选")],
                },
                {
                    "decision": "send",
                    "sceneCode": "objection_effect_recurrence",
                    "sceneEvidence": "客户担心反弹",
                    "knowledgeId": 8,
                    "knowledgeParagraphNo": 1,
                    "reason": "fixed mapping",
                    "reply_messages": [_text("修复候选")],
                },
            ]
        )
        service, repo, platform, system = _service(model=model)

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(model.calls), 2)
        decision = repo.tasks["platform-sop:101"]["send_payload"]["decision"]
        self.assertEqual(decision["sceneName"], "效果异议｜担心反弹或再长")
        self.assertEqual(decision["knowledgeId"], 8)
        self.assertEqual(platform.rule_data_calls[-1]["knowledge_id"], 8)
        self.assertEqual(len(system.send_calls), 1)

    async def test_model_cannot_select_technical_scene(self) -> None:
        model = _Model(
            [
                {
                    "decision": "no_send",
                    "sceneCode": "no_send_duplicate",
                    "sceneEvidence": "model attempted a technical status",
                    "reason": "invalid ownership",
                    "reply_messages": [],
                },
                {
                    "decision": "send",
                    "sceneCode": "normal_platform_intent",
                    "sceneEvidence": "平台任务内容与当前会话不冲突",
                    "reason": "repaired ownership",
                    "reply_messages": [_text("平台内容")],
                },
            ]
        )
        service, _repo, _platform, system = _service(model=model)

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len(system.send_calls), 1)

    async def test_shadow_mode_never_claims_or_sends(self) -> None:
        model = _Model([{"decision": "send", "reason": "ok", "reply_messages": [_text("发送")]}])
        service, repo, platform, system = _service(model=model, shadow_mode=True)

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "shadow_send")
        self.assertEqual(platform.consume_calls, [])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "shadow_send")

    async def test_complete_pending_recovery_only_writes_platform_success(self) -> None:
        model = _Model([])
        service, repo, platform, system = _service(model=model)
        task = _task()
        repo.create_sop_event(
            {
                "event_id": "platform_sop_task:101",
                "event_type": "platform_sop_task",
                "platform_task": task,
            }
        )
        repo.events["platform_sop_task:101"]["status"] = "platform_complete_pending"

        result = await service.process_task(task, recovery_status="platform_complete_pending")

        self.assertTrue(result["processed"])
        self.assertEqual(platform.consume_calls, [("101", 30)])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])

    async def test_recovery_missing_task_payload_finishes_as_platform_failure(self) -> None:
        service, repo, platform, system = _service(model=_Model([]))
        repo.create_sop_event(
            {
                "event_id": "platform_sop_task:101",
                "event_type": "platform_sop_task",
            }
        )
        repo.events["platform_sop_task:101"]["status"] = "platform_processing_retry"

        processed = await service.process_recoveries()

        self.assertEqual(processed, 1)
        self.assertEqual(platform.consume_calls, [("101", 70)])
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_failed")
        self.assertEqual(system.send_calls, [])

    async def test_existing_processing_retry_is_released_without_model_or_send(self) -> None:
        model = _Model([])
        service, repo, platform, system = _service(model=model)

        result = await service.process_task(_task(), recovery_status="platform_processing_retry")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(platform.consume_calls, [("101", 70)])
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_failed")
        self.assertEqual(repo.tasks["platform-sop:101"]["status"], "failed")
        self.assertEqual(model.calls, [])
        self.assertEqual(system.send_calls, [])

    async def test_claim_must_confirm_processing_before_context_or_send(self) -> None:
        model = _Model([{"decision": "send", "reason": "ok", "reply_messages": [_text("不应发送")]}])
        service, repo, platform, system = _service(model=model)
        platform.consume_responses = [{"code": 200, "data": {"task_id": 101, "status": 10}}]

        with self.assertRaisesRegex(RuntimeError, "expected 20, got 10"):
            await service.process_task(_task())

        self.assertEqual(system.conversation_calls, 0)
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_claiming")

    async def test_completion_retry_does_not_resend_customer_message(self) -> None:
        model = _Model([{"decision": "send", "reason": "ok", "reply_messages": [_text("只发一次")]}])
        service, repo, platform, system = _service(model=model)
        platform.consume_responses = [
            {"code": 200, "data": {"task_id": 101, "status": 20}},
            {"code": 200, "data": {"task_id": 101, "status": 20}},
        ]

        with self.assertRaisesRegex(RuntimeError, "expected 30, got 20"):
            await service.process_task(_task())

        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(next(iter(repo.tasks.values()))["status"], "sent")
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_terminal_pending")

        platform.consume_responses = [{"code": 200, "data": {"task_id": 101, "status": 30}}]
        result = await service.process_task(_task(), recovery_status="platform_terminal_pending")

        self.assertTrue(result["processed"])
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_completed")

    async def test_no_send_completion_retry_preserves_status_70(self) -> None:
        model = _Model(
            [
                {
                    "decision": "no_send",
                    "reason": "human takeover",
                    "reason_code": "human_takeover",
                    "remark": "人工正在连续接待",
                    "reply_messages": [],
                }
            ]
        )
        service, repo, platform, system = _service(model=model)
        platform.consume_responses = [
            {"code": 200, "data": {"task_id": 101, "status": 20}},
            {"code": 200, "data": {"task_id": 101, "status": 20}},
        ]

        with self.assertRaisesRegex(RuntimeError, "expected 70, got 20"):
            await service.process_task(_task())

        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_terminal_pending")
        self.assertEqual(system.send_calls, [])
        platform.consume_responses = [{"code": 200, "data": {"task_id": 101, "status": 70}}]

        result = await service.process_task(_task(), recovery_status="platform_terminal_pending")

        self.assertTrue(result["processed"])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70), ("101", 70)])
        self.assertEqual(platform.consume_remarks, ["", "人工正在连续接待", "人工正在连续接待"])
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_no_send")
        self.assertEqual(len(model.calls), 1)

    async def test_legacy_no_send_recovery_prefers_local_terminal_status_over_error_text(self) -> None:
        service, _repo, _platform, _system = _service(model=_Model([]))

        terminal_status = service._stored_terminal_status(
            {
                "status": "completed_without_send",
                "error": "downstream_delivery_rejected",
                "send_payload": {},
            }
        )

        self.assertEqual(terminal_status, 70)

    async def test_accepted_no_response_is_recorded_as_sent(self) -> None:
        model = _Model([{"decision": "send", "reason": "ok", "reply_messages": [_text("只生成一次")]}])
        service, repo, platform, system = _service(model=model)
        system.send_responses = [
            {"code": 0, "data": {"send_status": "accepted_no_response"}},
        ]

        result = await service.process_task(_task())

        self.assertTrue(result["processed"])
        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["status"], "sent")
        self.assertEqual(stored["send_response"]["msg"], "accepted_no_response_assumed_sent")
        self.assertTrue(stored["send_response"]["data"]["assumed_sent"])
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_completed")

    async def test_near_duplicate_platform_delivery_is_consumed_without_send(self) -> None:
        current = (
            "亲，您放心，咱们这次活动是明码标价的，包含检测、基础清洁和肌肤补水这些内容，没有额外捆绑消费。\n\n"
            "现在活动价268元，先付10元预约金就能帮您锁名额，到店直接抵扣，做的话再补258元就行。\n\n"
            "当前登记还会赠送一次价值180元的美白管理，名额满了就恢复原价了。\n\n"
            "您如果想做，我先帮您留一个，到店时间后面按您方便安排就可以。"
        )
        previous = (
            "亲，您放心，咱们这次活动是明码标价的，包含检测、基础清洁和肌肤补水这些内容，没有额外捆绑消费。\n\n"
            "现在活动价268元，先付10元预约金就能帮您锁名额，到店直接抵扣，做的话再补258元就行。\n\n"
            "现在登记还赠送一次价值180元的美白管理，名额满了就恢复原价了。\n\n"
            "您如果想做，我先帮您留一个，到店时间后面按您方便安排就行。"
        )
        model = _Model([{"decision": "send", "reason": "ok", "reply_messages": [_text(current)]}])
        service, repo, platform, system = _service(model=model)
        repo.list_platform_sop_task_records = lambda **_kwargs: [  # type: ignore[attr-defined]
            {
                "event_id": "platform_sop_task:100",
                "platform_task": {**_task(), "task_id": 100},
                "customer_id": "22000001",
                "external_userid": "wm_external",
                "corp_id": "ww_corp",
                "wechat": "DY258",
                "task_status": "sent",
                "sent_at": "2026-08-09T01:55:43+00:00",
                "reply_messages": [_text(previous)],
                "send_payload": {"request": {"reply_messages": [_text(previous)]}},
            }
        ]

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(system.send_calls, [])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(platform.rule_data_calls[-1]["scene_code"], "duplicate_blocked")
        self.assertEqual(platform.rule_data_calls[-1]["send_content"], "")
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["status"], "completed_without_send")
        self.assertEqual(stored["send_payload"]["decision"]["reason"], "near_duplicate_platform_delivery")
        self.assertTrue(stored["send_payload"]["context"]["near_duplicate_delivery"]["found"])

    async def test_ai_service_signed_image_duplicate_is_consumed_without_send(self) -> None:
        old_url = "https://oss.example/media/effect.png?OSSAccessKeyId=old&Expires=1&Signature=old"
        new_url = "https://oss.example/media/effect.png?Signature=new&Expires=2&OSSAccessKeyId=new"
        model = _Model(
            [
                {"decision": "send", "reason": "ok", "reply_messages": [_text("模型候选"), _image(new_url, order=2)]},
                {"decision": "no_send", "reason": "no unused media", "reply_messages": []},
            ]
        )
        service, repo, platform, system = _service(model=model)
        repo.list_platform_sop_task_records = lambda **_kwargs: [  # type: ignore[attr-defined]
            _sent_platform_record(task_id="100", messages=[_image(old_url)])
        ]
        task = _task(
            use_ai_copy=False,
            message_content=[
                {"type": "text", "content": "短文案"},
                {"type": "image", "content": new_url},
            ],
        )

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(system.send_calls, [])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["send_payload"]["decision"]["reason"], "duplicate_media_delivery")
        duplicate = stored["send_payload"]["context"]["near_duplicate_delivery"]
        self.assertEqual(duplicate["match_type"], "duplicate_media")
        self.assertEqual(duplicate["duplicate_task_id"], "100")

    async def test_opened_model_cannot_replace_duplicate_platform_media(self) -> None:
        duplicate_url = "https://cdn.example/effect-a.png"
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "initial",
                    "reply_messages": [_text("效果参考"), _image(duplicate_url, order=2)],
                },
            ]
        )
        service, repo, platform, system = _service(model=model)
        repo.list_platform_sop_task_records = lambda **_kwargs: [  # type: ignore[attr-defined]
            _sent_platform_record(task_id="100", messages=[_image(duplicate_url)])
        ]

        result = await service.process_task(
            _task(
                use_ai_copy=True,
                message_content=[
                    {"type": "text", "content": "效果参考"},
                    {"type": "image", "content": duplicate_url},
                ],
            )
        )

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(system.send_calls, [])
        first_model_input = json.loads(model.calls[0][-1]["content"])
        self.assertEqual(len(first_model_input["latest_context"]["recent_sent_media"]), 1)
        self.assertEqual(
            first_model_input["latest_context"]["recent_sent_media"][0]["canonical_url"],
            duplicate_url,
        )
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["send_payload"]["decision"]["reason"], "duplicate_media_delivery")
        self.assertEqual(stored["send_payload"]["context"]["duplicate_media_repair"], {})

    async def test_model_invented_replacement_media_repairs_to_no_send(self) -> None:
        duplicate_url = "https://cdn.example/effect-a.png"
        replacement_url = "https://cdn.example/effect-b.png"
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "invented replacement",
                    "reply_messages": [_text("效果参考"), _image(replacement_url, order=2)],
                },
                {
                    "decision": "send",
                    "reason_code": "exact_duplicate",
                    "reason": "preserve platform media for deterministic duplicate check",
                    "sceneCode": "normal_light_effect",
                    "sceneEvidence": "平台任务要求发送效果素材",
                    "reply_messages": [_text("效果参考"), _image(duplicate_url, order=2)],
                },
            ]
        )
        service, repo, platform, system = _service(model=model)
        repo.list_platform_sop_task_records = lambda **_kwargs: [  # type: ignore[attr-defined]
            _sent_platform_record(task_id="100", messages=[_image(duplicate_url)])
        ]

        result = await service.process_task(
            _task(
                use_ai_copy=True,
                message_content=[
                    {"type": "text", "content": "效果参考"},
                    {"type": "image", "content": duplicate_url},
                ],
            )
        )

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(system.send_calls, [])
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["send_payload"]["decision"]["reason"], "duplicate_media_delivery")

    async def test_duplicate_video_is_isolated_by_receiving_wechat(self) -> None:
        video_url = "https://cdn.example/case.mp4?x-oss-process=video/snapshot"
        model = _Model(
            [{"decision": "send", "reason": "ok", "reply_messages": [_video("https://cdn.example/case.mp4")]}]
        )
        service, repo, _platform, system = _service(model=model)
        other_wechat = _sent_platform_record(task_id="100", messages=[_video(video_url)])
        other_wechat["wechat"] = "OTHER"
        repo.list_platform_sop_task_records = lambda **_kwargs: [other_wechat]  # type: ignore[attr-defined]
        task = _task(
            use_ai_copy=False,
            message_content=[{"type": "video", "content": "https://cdn.example/case.mp4"}],
        )

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(system.send_calls[0]["reply_messages"], [_video("https://cdn.example/case.mp4")])

    async def test_same_contact_concurrent_tasks_cannot_send_the_same_media_twice(self) -> None:
        media_url = "https://cdn.example/shared-effect.png"
        model = _Model(
            [
                {"decision": "send", "reason": "first", "reply_messages": [_text("first model copy"), _image(media_url, order=2)]},
                {"decision": "send", "reason": "second", "reply_messages": [_text("second model copy"), _image(media_url, order=2)]},
            ]
        )
        service, repo, _platform, system = _service(model=model)

        def list_records(**_kwargs):
            records = []
            for task in repo.tasks.values():
                event = repo.events[task["event_id"]]
                platform_task = event["raw_payload"]["platform_task"]
                records.append(
                    {
                        "event_id": task["event_id"],
                        "platform_task": platform_task,
                        "customer_id": task["customer_id"],
                        "external_userid": task["external_userid"],
                        "corp_id": task["corp_id"],
                        "wechat": task["wechat"],
                        "task_status": task["status"],
                        "sent_at": task.get("sent_at") or "",
                        "reply_messages": task.get("reply_messages") or [],
                        "send_payload": task.get("send_payload") or {},
                    }
                )
            return records

        repo.list_platform_sop_task_records = list_records  # type: ignore[attr-defined]
        first = _task(
            use_ai_copy=False,
            message_content=[
                {"type": "text", "content": "第一条短文案"},
                {"type": "image", "content": media_url},
            ],
        )
        second = _task(
            use_ai_copy=False,
            message_content=[
                {"type": "text", "content": "第二条不同短文案"},
                {"type": "image", "content": media_url},
            ],
        )
        second["task_id"] = 102

        results = await asyncio.gather(service.process_task(first), service.process_task(second))

        self.assertEqual([result["status"] for result in results], ["sent", "completed_without_send"])
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(
            repo.tasks["platform-sop:102"]["send_payload"]["decision"]["reason"],
            "platform_contact_send_cooldown",
        )

    async def test_rule_data_failure_does_not_retry_after_customer_message_sent(self) -> None:
        model = _Model([{"decision": "send", "reason": "ok", "reply_messages": [_text("只生成一次")]}])
        service, repo, platform, system = _service(model=model)
        platform.service_rule_data = AsyncMock(side_effect=RuntimeError("rule data timeout"))  # type: ignore[method-assign]

        result = await service.process_task(_task())

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["status"], "sent")
        self.assertEqual(
            stored["send_payload"]["rule_data_response"]["error"],
            "service_rule_data_failed",
        )

    async def test_legacy_uncertain_recovery_marks_sent_without_resend(self) -> None:
        model = _Model([{"decision": "send", "reason": "ok", "reply_messages": [_text("只生成一次")]}])
        service, repo, platform, system = _service(model=model)
        _, local_task = service._ensure_local_task(_task(), status="sending")
        repo.update_sop_send_task(
            local_task["id"],
            status="sending",
            send_payload={
                "decision": {"decision": "send", "reason": "ok", "reply_messages": [_text("只生成一次")]},
                "request": {
                    "corp_id": "c1",
                    "external_userid": "u1",
                    "wechat": "w1",
                    "user_id": "u",
                    "plan_id": "platform-sop-101",
                    "task_id": "platform-sop-send-101",
                    "reply_messages": [_text("只生成一次")],
                },
            },
        )
        repo.update_sop_event_status("platform_sop_task:101", status="platform_send_uncertain")
        result = await service.process_task(_task(), recovery_status="platform_send_uncertain")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(model.calls), 0)
        self.assertEqual(len(system.send_calls), 0)
        self.assertEqual(platform.consume_calls, [("101", 30)])
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["status"], "sent")
        self.assertEqual(stored["send_response"]["msg"], "accepted_no_response_assumed_sent")

    async def test_existing_platform_delivery_prevents_normal_resend(self) -> None:
        model = _Model([{"decision": "send", "reason": "ok", "reply_messages": [_text("平台原文")]}])
        service, repo, platform, system = _service(model=model)
        system.conversation_payload["data"]["messages"] = [
            {
                "from": "staff",
                "msgid": "ai-outreach-platform-sop-101-101-0001-text-existing",
                "msgtype": "text",
                "content": "平台原文",
            }
        ]

        result = await service.process_task(_task(use_ai_copy=False))

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(system.send_calls, [])
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_no_send")

    def test_platform_client_rejects_non_contract_statuses(self) -> None:
        client = SopPlatformClient(_settings())
        with self.assertRaisesRegex(ValueError, "20, 30, or 70"):
            import asyncio

            asyncio.run(client.consume(task_id=1, status=60))

    async def test_platform_client_accepts_no_send_and_rejects_internal_failure(self) -> None:
        client = SopPlatformClient(_settings())
        client._request = AsyncMock(
            return_value={"code": 200, "data": {"taskId": 2, "status": 70}}
        )  # type: ignore[method-assign]

        with self.assertRaisesRegex(ValueError, "20, 30, or 70"):
            await client.consume(task_id=1, status=40)
        await client.consume(task_id=2, status=70, remark="客户明确停止联系")

        self.assertEqual(
            client._request.await_args.kwargs["json_body"],
            {"taskId": 2, "status": 70, "remark": "客户明确停止联系"},
        )  # type: ignore[union-attr]

    async def test_pending_relies_on_upstream_due_time_and_uses_documented_limit(self) -> None:
        settings = _settings()
        settings.sop_platform_lookback_seconds = 604800
        settings.sop_platform_window_seconds = 60
        settings.sop_platform_batch_size = 500
        client = SopPlatformClient(settings)
        upstream_task = {
            "task_id": 101,
            "runId": 11,
            "ruleId": 3,
            "ruleName": "加微后强触约策略A",
            "ruleTaskId": 15,
        }
        client._request = AsyncMock(  # type: ignore[method-assign]
            return_value={"code": 200, "data": {"list": [upstream_task]}},
        )

        page = await client.pending()

        payload = client._request.await_args.kwargs["json_body"]  # type: ignore[union-attr]
        self.assertNotIn("start_time", payload)
        self.assertNotIn("end_time", payload)
        self.assertEqual(payload["limit"], 500)
        self.assertEqual(page["items"], [upstream_task])
        self.assertEqual(page["total"], 1)

    async def test_invalid_identity_is_consumed_as_failure_without_model(self) -> None:
        model = _Model([])
        service, _repo, platform, system = _service(model=model)
        task = _task()
        task["corp_id"] = ""

        result = await service.process_task(task)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 70)])
        self.assertEqual(model.calls, [])
        self.assertEqual(system.send_calls, [])

    async def test_rule_data_client_sends_current_callback_contract(self) -> None:
        client = SopPlatformClient(_settings())
        client._request = AsyncMock(return_value={"code": 200, "data": {"taskId": 101}})  # type: ignore[method-assign]

        await client.service_rule_data(
            task_id=101,
            scene_name="正常推进｜平台任务内容",
            scene_code="normal_platform_intent",
            send_status=10,
            remark="客户已开口，模型审核后发送",
            send_content="最终文本\n[image]https://cdn.example/a.jpg",
        )

        request = client._request.await_args  # type: ignore[union-attr]
        self.assertEqual(request.args[:2], ("POST", "/event/trigger/service-rule-data"))
        self.assertEqual(
            request.kwargs["json_body"],
            {
                "taskId": 101,
                "sceneName": "正常推进｜平台任务内容",
                "sceneCode": "normal_platform_intent",
                "sendStatus": 10,
                "remark": "客户已开口，模型审核后发送",
                "sendContent": "最终文本\n[image]https://cdn.example/a.jpg",
            },
        )

    async def test_stale_shadow_task_follows_current_model_route_without_consuming(self) -> None:
        model = _Model([{"decision": "send", "reason": "reviewed", "reply_messages": [_text("模型消息")]}])
        service, _repo, platform, system = _service(model=model, shadow_mode=True)
        task = _task()
        task["scheduledAt"] = time.time() - 21601

        result = await service.process_task(task)

        self.assertEqual(result["status"], "shadow_send")
        self.assertEqual(result["decision"]["reason"], "reviewed")
        self.assertEqual(platform.consume_calls, [])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(system.send_calls, [])

    async def test_ai_decision_disables_parallel_model_candidates(self) -> None:
        model = _Model([{"decision": "send", "reason": "test", "reply_messages": [_text("发送")]}])
        service, _repo, _platform, _system = _service(model=model)

        await service.process_task(_task())

        self.assertEqual(model.kwargs[0]["max_parallel_candidates"], 1)

    async def test_poll_persists_task_before_putting_it_in_memory_queue(self) -> None:
        model = _Model([])
        service, repo, platform, _system = _service(model=model, shadow_mode=True)

        async def pending(*, limit=None):
            return {"items": [_task()], "total": 1, "limit": limit}

        platform.pending = pending  # type: ignore[method-assign]
        result = await service.poll_once()

        self.assertEqual(result["enqueued_count"], 1)
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_queued")
        self.assertEqual(next(iter(repo.tasks.values()))["status"], "platform_queued")
        self.assertEqual(service.runtime_status()["queue_depth"], 1)

    async def test_poll_reacks_terminal_duplicates_and_keeps_scanning_pending_page(self) -> None:
        model = _Model([])
        settings = _settings()
        settings.sop_platform_batch_size = 1
        settings.sop_platform_queue_size = 1
        service, repo, platform, _system = _service(model=model, settings=settings)
        service._remember_terminal("101")
        seen_limits: list[int | None] = []

        async def pending(*, limit=None):
            seen_limits.append(limit)
            return {"items": [_task(), {**_task(), "task_id": 202}], "total": 2, "limit": limit}

        platform.pending = pending  # type: ignore[method-assign]
        result = await service.poll_once()

        self.assertEqual(seen_limits, [20])
        self.assertEqual(platform.consume_calls, [("101", 30)])
        self.assertEqual(result["enqueued_count"], 1)
        self.assertIn("platform_sop_task:202", repo.events)
        self.assertEqual(repo.events["platform_sop_task:202"]["status"], "platform_queued")
        self.assertEqual(service.runtime_status()["counters"]["terminal_reack"], 1)

    async def test_completed_local_event_reacks_if_platform_returns_pending_after_restart(self) -> None:
        model = _Model([])
        service, repo, platform, system = _service(model=model)
        repo.create_sop_event(
            {
                "event_id": "platform_sop_task:101",
                "event_type": "platform_sop_task",
                "platform_task": _task(),
            }
        )
        repo.events["platform_sop_task:101"]["status"] = "platform_completed"

        result = await service.process_task(_task())

        self.assertFalse(result["processed"])
        self.assertEqual(result["status"], "platform_completed")
        self.assertEqual(platform.consume_calls, [("101", 30)])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])

    async def test_poll_loop_survives_transient_pending_error(self) -> None:
        model = _Model([])
        service, _repo, _platform, _system = _service(model=model, shadow_mode=True)
        service.settings.sop_platform_poll_seconds = 0.2
        service.process_recoveries = AsyncMock(return_value={"processed": 0})  # type: ignore[method-assign]
        calls = 0
        second_poll = asyncio.Event()

        async def poll_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("temporary platform timeout")
            second_poll.set()
            return {
                "pending_count": 0,
                "enqueued_count": 0,
                "queue_depth": 0,
                "in_flight_count": 0,
                "error_count": 0,
            }

        service.poll_once = poll_once  # type: ignore[method-assign]
        worker = asyncio.create_task(service.run())
        try:
            await asyncio.wait_for(second_poll.wait(), timeout=2)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

        self.assertGreaterEqual(calls, 2)
        self.assertEqual(service.runtime_status()["counters"]["poll_loop_error"], 1)

    async def test_admin_logs_merge_platform_pending_and_local_decision(self) -> None:
        model = _Model([])
        service, repo, platform, _system = _service(model=model, shadow_mode=True)
        platform_task = _task()

        async def pending(*, limit=None):
            return {"items": [platform_task, {**_task(), "task_id": 202}], "total": 2, "limit": limit}

        platform.pending = pending  # type: ignore[method-assign]
        repo.list_platform_sop_task_records = lambda **_kwargs: [  # type: ignore[attr-defined]
            {
                "event_id": "platform_sop_task:101",
                "event_status": "shadow_no_send",
                "event_error": "",
                "received_at": "2026-08-04T00:00:00+00:00",
                "event_updated_at": "2026-08-04T00:00:01+00:00",
                "platform_task": platform_task,
                "local_task_id": "local-1",
                "customer_id": "22000001",
                "external_userid": "wm_external",
                "corp_id": "ww_corp",
                "user_id": "7294",
                "wechat": "DY258",
                "sop_pack_name": "test rule",
                "task_status": "shadow_no_send",
                "reply_messages": [_text("original")],
                "send_payload": {
                    "decision": {"decision": "no_send", "reason": "duplicate", "reply_messages": []},
                    "rule_data_response": {
                        "code": 200,
                        "message": "操作成功",
                        "data": {
                            "id": 14983,
                            "taskId": 101,
                            "sceneTypeName": "客户未开口",
                            "sceneTypeCode": "customer_unopened",
                            "sendStatus": 10,
                            "remark": "平台原文发送",
                            "sendContent": "[image]https://cdn.example/a.png?Signature=secret&Expires=123",
                        },
                    },
                },
                "send_response": {},
                "task_error": "",
                "task_created_at": "2026-08-04T00:00:00+00:00",
                "task_updated_at": "2026-08-04T00:00:01+00:00",
                "sent_at": "",
            }
        ]

        result = await service.admin_task_logs()

        self.assertEqual(result["summary"]["platform_pending_total"], 2)
        self.assertEqual(result["summary"]["judged_no_send"], 1)
        self.assertEqual(result["summary"]["platform_pending"], 1)
        self.assertEqual({item["task_id"] for item in result["items"]}, {"101", "202"})
        local_item = next(item for item in result["items"] if item["task_id"] == "101")
        callback = local_item["rule_data_callback"]
        self.assertTrue(callback["success"])
        self.assertEqual(callback["request_source"], "response_echo")
        self.assertEqual(callback["request"]["sceneCode"], "customer_unopened")
        self.assertIn("%5BREDACTED%5D", callback["request"]["sendContent"])
        self.assertNotIn("secret", callback["request"]["sendContent"])
        self.assertEqual(callback["response"]["data"]["id"], 14983)
        self.assertEqual(local_item["decision_reason_cn"], "审核未通过，本次平台 SOP 不发送")
        self.assertEqual(local_item["decision_reason_code"], "duplicate")

    async def test_queue_workers_never_exceed_configured_concurrency(self) -> None:
        settings = _settings(shadow_mode=True)
        settings.sop_platform_task_concurrency = 6
        settings.sop_platform_queue_size = 1000
        service = SopPlatformTaskService(
            settings=settings,
            repository=_Repo(),
            platform_client=_Platform(),
            system_client=_System(),
            model_client=_Model([]),
            customer_context_service=_CustomerContext(),
        )
        active = 0
        maximum = 0

        async def fake_process(task, **_kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.001)
            active -= 1
            return {"processed": True, "status": "shadow_no_send", "task_id": str(task["task_id"])}

        service.process_task = fake_process  # type: ignore[method-assign]
        workers = [asyncio.create_task(service._queue_worker(index)) for index in range(6)]
        for task_id in range(1000):
            task = {"task_id": str(task_id)}
            service._queued_ids.add(str(task_id))
            service._queue.put_nowait(task)
        await service._queue.join()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

        self.assertEqual(maximum, 6)
        self.assertEqual(service.runtime_status()["queue_depth"], 0)

    async def test_platform_client_rejects_non_json_success_response(self) -> None:
        client = SopPlatformClient(_settings())
        response = _HttpResponse(status_code=200, text="<html>redirect page</html>")
        http_client = SimpleNamespace(request=AsyncMock(return_value=response))
        client._http_client = lambda: http_client  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "invalid_json_response"):
            await client.pending()

    async def test_platform_client_exposes_cancelled_task_as_terminal_state(self) -> None:
        client = SopPlatformClient(_settings())
        payload = {"code": 400, "message": "任务不可消费（当前状态：已取消）", "data": None}
        response = SimpleNamespace(
            status_code=200,
            text=json.dumps(payload, ensure_ascii=False),
            json=lambda: payload,
        )
        client._http_client = lambda: SimpleNamespace(request=AsyncMock(return_value=response))  # type: ignore[method-assign]

        with self.assertRaises(SopPlatformTaskStateError) as raised:
            await client.consume(task_id=8474, status=20)

        self.assertEqual(raised.exception.state, "已取消")
        self.assertEqual(raised.exception.payload, payload)


class SopReplyPackScopeTests(unittest.TestCase):
    def test_load_hides_legacy_event_packs_and_save_rejects_event_scope(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "packs.json"
            path.write_text(
                """{
  "version": 1,
  "packs": [
    {"id":"chat","enabled":false,"scope":"chat_gate","scopes":["chat_gate"],"reply_messages":[]},
    {"id":"event","enabled":false,"scope":"event_first_add","scopes":["event_first_add"],"reply_messages":[]}
  ]
}
""",
                encoding="utf-8",
            )
            service = SopReplyPackService(SimpleNamespace(sop_reply_packs_path=path))
            self.assertEqual([item["id"] for item in service.load()["packs"]], ["chat"])
            with self.assertRaisesRegex(ValueError, "event scopes"):
                service.save(
                    {
                        "version": 1,
                        "packs": [
                            {
                                "id": "mixed",
                                "enabled": False,
                                "scope": "chat_gate",
                                "scopes": ["chat_gate", "event_first_add"],
                                "reply_messages": [],
                            }
                        ],
                    }
                )

    def test_objection_material_catalog_uses_simple_slice_schema(self) -> None:
        with TemporaryDirectory() as directory:
            service = SopObjectionMaterialService(Path(directory) / "materials.json")
            saved = service.save(
                {
                    "version": 1,
                    "materials": [
                        {
                            "material_id": "effect_001",
                            "name": "效果顾虑",
                            "category": "effect",
                            "tags": ["效果", "信任"],
                            "applicable_scenes": ["客户担心没效果"],
                            "response_approach": "先共情，再用真实事实建立信任。",
                            "example_contents": ["先给您看同类情况。"],
                        }
                    ],
                }
            )

            self.assertEqual(
                set(saved["materials"][0]),
                {
                    "material_id",
                    "name",
                    "category",
                    "tags",
                    "applicable_scenes",
                    "response_approach",
                    "example_contents",
                },
            )

    def test_repository_objection_material_catalog_has_initial_business_slices(self) -> None:
        path = Path(__file__).resolve().parents[1] / "config" / "sop_objection_materials.json"
        catalog = SopObjectionMaterialService(path).load()
        material_ids = {item["material_id"] for item in catalog["materials"]}

        self.assertGreaterEqual(len(material_ids), 10)
        self.assertIn("price_transparency_001", material_ids)
        self.assertIn("effect_confidence_001", material_ids)
        self.assertIn("scope_spots_acne_001", material_ids)
        self.assertIn("brand_trust_001", material_ids)


def _service(
    *,
    model: Any,
    shadow_mode: bool = False,
    material_service: Any | None = None,
    settings: Any | None = None,
):
    repo = _Repo()
    platform = _Platform()
    system = _System()
    service = SopPlatformTaskService(
        settings=settings or _settings(shadow_mode=shadow_mode),
        repository=repo,
        platform_client=platform,
        system_client=system,
        model_client=model,
        customer_context_service=_CustomerContext(),
        objection_material_service=material_service,
    )
    return service, repo, platform, system


def _settings(*, shadow_mode: bool = False, quiet_hours_enabled: bool = False):
    return SimpleNamespace(
        sop_platform_token="token",
        sop_platform_base_url="http://example.invalid",
        sop_platform_timeout_seconds=2,
        sop_platform_lookback_seconds=300,
        sop_platform_window_seconds=60,
        sop_platform_batch_size=20,
        sop_platform_task_concurrency=6,
        sop_platform_queue_size=24,
        sop_platform_recovery_concurrency=2,
        sop_platform_recovery_batch_size=10,
        sop_platform_shadow_mode=shadow_mode,
        sop_platform_model_timeout_seconds=10,
        sop_platform_max_task_age_seconds=600,
        sop_platform_live_not_before="",
        sop_platform_quiet_hours_enabled=quiet_hours_enabled,
        sop_platform_quiet_start_hour=0,
        sop_platform_quiet_end_hour=8,
        sop_platform_quiet_first_add_grace_minutes=30,
    )


def _beijing_epoch(value: str) -> float:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone(timedelta(hours=8)),
    ).timestamp()


def _task(
    *,
    use_ai_copy: bool = True,
    dispatch_mode: str = "ai_service",
    message_content: list[dict[str, Any]] | None = None,
):
    return {
        "task_id": 101,
        "runId": 11,
        "ruleId": 3,
        "ruleName": "test rule",
        "ruleTaskId": 15,
        "customerId": 22000001,
        "customer_wechat_id": "wm_external",
        "corp_id": "ww_corp",
        "user_wechat_id": "7294",
        "user_wechat": "DY258",
        "useAiCopy": use_ai_copy,
        "dispatchMode": dispatch_mode,
        "scene": {"name": "test"},
        "message_content": (
            message_content if message_content is not None else [{"type": "text", "content": "平台原文"}]
        ),
    }


def _text(value: str, order: int = 1):
    return {"type": "text", "order": order, "content": {"text": value}}


def _image(url: str, order: int = 1):
    return {"type": "image", "order": order, "content": {"url": url}}


def _video(url: str, order: int = 1):
    return {"type": "video", "order": order, "content": {"url": url}}


def _sent_platform_record(*, task_id: str, messages: list[dict[str, Any]]):
    return {
        "event_id": f"platform_sop_task:{task_id}",
        "platform_task": {**_task(), "task_id": int(task_id)},
        "customer_id": "22000001",
        "external_userid": "wm_external",
        "corp_id": "ww_corp",
        "wechat": "DY258",
        "task_status": "sent",
        "sent_at": "2026-08-09T01:55:43+00:00",
        "reply_messages": messages,
        "send_payload": {"request": {"reply_messages": messages}},
    }


class _Repo:
    def __init__(self):
        self.events: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, dict[str, Any]] = {}

    def create_sop_event(self, payload):
        event_id = payload["event_id"]
        created = event_id not in self.events
        if created:
            self.events[event_id] = {
                "event_id": event_id,
                "event_type": payload.get("event_type"),
                "status": "accepted",
                "raw_payload": dict(payload),
                "retry_count": 0,
            }
        return {**self.events[event_id], "created": created}

    def get_sop_event(self, event_id):
        return dict(self.events.get(event_id) or {})

    def get_sop_send_task_by_idempotency_key(self, idempotency_key):
        return dict(self.tasks.get(idempotency_key) or {})

    def find_sop_send_task_delivery_duplicate(self, send_once_key, *, exclude_idempotency_key=""):
        for task in self.tasks.values():
            if task.get("idempotency_key") == exclude_idempotency_key:
                continue
            if task.get("send_once_key") != send_once_key:
                continue
            if task.get("status") in {"sent", "sending"} or task.get("error") == "active_send_timeout_unknown_result":
                return dict(task)
        return {}

    def update_sop_event_status(self, event_id, *, status, error=""):
        self.events[event_id]["status"] = status
        self.events[event_id]["error"] = error
        return dict(self.events[event_id])

    def schedule_platform_sop_retry(self, event_id, *, error, delays_seconds=(10, 30, 60)):
        event = self.events[event_id]
        retry_count = int(event.get("retry_count") or 0) + 1
        event["retry_count"] = retry_count
        event["error"] = error if retry_count > len(delays_seconds) else ""
        event["status"] = "platform_retry_exhausted" if retry_count > len(delays_seconds) else "platform_processing_retry"
        return dict(event)

    def create_sop_send_task(self, **payload):
        key = payload["idempotency_key"]
        created = key not in self.tasks
        if created:
            self.tasks[key] = {"id": f"local-{len(self.tasks) + 1}", **payload}
        return {**self.tasks[key], "created": created}

    def update_sop_send_task(self, task_id, **payload):
        task = next(value for value in self.tasks.values() if value["id"] == task_id)
        task.update(payload)
        return dict(task)

    def list_sop_events_by_statuses(self, statuses, *, limit, event_type=""):
        return [
            dict(item)
            for item in self.events.values()
            if item["status"] in statuses and (not event_type or item["event_type"] == event_type)
        ][:limit]


class _Platform:
    def __init__(self):
        self.consume_calls: list[tuple[str, int]] = []
        self.consume_remarks: list[str] = []
        self.consume_responses: list[dict[str, Any]] = []
        self.rule_data_calls: list[dict[str, Any]] = []
        self.knowledge_category_calls = 0
        self.knowledge_base_calls = 0

    async def consume(self, *, task_id, status, remark=""):
        self.consume_calls.append((str(task_id), status))
        self.consume_remarks.append(str(remark or ""))
        if self.consume_responses:
            response = self.consume_responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        return {"code": 200, "data": {"task_id": task_id, "status": status}}

    async def pending(self, *, limit=None):
        return {"items": [], "total": 0, "limit": limit}

    async def knowledge_categories(self, **_kwargs):
        self.knowledge_category_calls += 1
        return {
            "code": 200,
            "data": {
                "total": 1,
                "list": [
                    {
                        "id": 5,
                        "categoryName": "价格异议",
                        "meta": "价格/套餐",
                        "description": "价格顾虑话术",
                        "sortOrder": 1,
                        "groupCount": 1,
                    }
                ],
            },
        }

    async def knowledge_base(self, **_kwargs):
        self.knowledge_base_calls += 1
        return {
            "code": 200,
            "data": {
                "total": 1,
                "list": [
                    {
                        "id": 11,
                        "categoryId": 5,
                        "categoryName": "价格异议",
                        "knowledgeName": "价格疑问",
                        "sortOrder": 1,
                        "paragraphs": [
                            {
                                "paragraphNo": 1,
                                "messages": [
                                    {
                                        "id": 101,
                                        "msgType": "text",
                                        "contentText": "活动价268元，10元预约金到店抵扣。",
                                        "sortOrder": 1,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        }

    async def service_rule_data(self, **kwargs):
        self.rule_data_calls.append(kwargs)
        return {"code": 200, "data": {"taskId": kwargs.get("task_id")}}


class _System:
    def __init__(self):
        self.send_calls: list[dict[str, Any]] = []
        self.send_responses: list[dict[str, Any]] = []
        self.send_error: Exception | None = None
        self.conversation_calls = 0
        self.conversation_status_calls = 0
        self.conversation_status_error: Exception | None = None
        self.conversation_status_payload = {
            "code": 0,
            "data": {
                "takeover": {"mode": "ai", "is_human": False, "is_ai": True},
                "ai_outreach": {"send_allowed": True, "reason_code": "ai_active"},
            },
        }
        self.conversation_error: Exception | None = None
        self.conversation_limits: list[int | None] = []
        self.conversation_payload = {
            "code": 0,
            "data": {
                "customer_relation": {"status": "active", "is_deleted": False},
                "messages": [{"direction": "customer", "content": "你好"}],
            },
        }

    async def conversation(self, **_kwargs):
        self.conversation_calls += 1
        self.conversation_limits.append(_kwargs.get("limit"))
        if self.conversation_error is not None:
            raise self.conversation_error
        return self.conversation_payload

    async def conversation_status(self, **_kwargs):
        self.conversation_status_calls += 1
        if self.conversation_status_error is not None:
            raise self.conversation_status_error
        return self.conversation_status_payload

    async def send(self, **kwargs):
        self.send_calls.append(kwargs)
        if self.send_error is not None:
            raise self.send_error
        if self.send_responses:
            return self.send_responses.pop(0)
        return {"code": 0, "data": {"send_status": "sent"}}


class _CustomerContext:
    def __init__(self):
        self.calls = 0
        self.payload = {"source": "test", "orders": [], "appointment": {}}

    def load(self, **_kwargs):
        self.calls += 1
        return self.payload


class _Materials:
    def load(self):
        return {
            "version": 1,
            "materials": [
                {
                    "material_id": "price_001",
                    "name": "价格顾虑",
                    "category": "price",
                    "tags": ["价格", "信任"],
                    "applicable_scenes": ["客户担心加价"],
                    "response_approach": "先承接担心，再说明透明事实。",
                    "example_contents": ["费用按活动事实说清楚。"],
                }
            ],
        }


class _Model:
    def __init__(self, outputs: list[dict[str, Any]]):
        self.outputs = list(outputs)
        self.calls: list[list[dict[str, Any]]] = []
        self.kwargs: list[dict[str, Any]] = []

    async def chat_json(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs.append(kwargs)
        if not self.outputs:
            raise AssertionError("unexpected model call")
        output = dict(self.outputs.pop(0))
        decision = str(output.get("decision") or "")
        if "sceneCode" not in output:
            legacy_no_send_scenes = {
                "complaint_or_refund": "no_send_complaint_or_refund",
                "explicit_stop_contact": "no_send_explicit_stop_contact",
                "customer_deleted": "no_send_customer_deleted",
                "health_risk": "no_send_health_risk",
                "paid_or_appointment_conflict": "no_send_paid_or_appointment_conflict",
                "human_takeover": "no_send_human_takeover",
                "platform_content_conflict": "no_send_platform_content_conflict",
                "exact_duplicate": "no_send_duplicate",
            }
            if decision == "send":
                output["sceneCode"] = "normal_platform_intent"
            elif decision == "no_send":
                reason_code = str(output.get("reason_code") or "")
                output["sceneCode"] = legacy_no_send_scenes.get(
                    reason_code,
                    reason_code or "no_send_platform_content_conflict",
                )
        output.setdefault("sceneEvidence", "test fixture evidence")
        output.setdefault("knowledgeId", 0)
        output.setdefault("knowledgeParagraphNo", 0)
        return output


class _HttpResponse:
    def __init__(self, *, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self):
        raise ValueError("not json")
