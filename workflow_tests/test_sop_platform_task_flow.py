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
from app.services.sop_platform_client import SopPlatformClient
from app.services.sop_objection_material_service import SopObjectionMaterialService
from app.services.sop_platform_task_service import (
    SOP_PLATFORM_KNOWLEDGE_TASK_PROMPT,
    SOP_PLATFORM_TASK_SYSTEM_PROMPT,
    SopPlatformTaskService,
)
from app.services.sop_reply_pack_service import SopReplyPackService


class SopPlatformTaskFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_platform_poll_default_is_ten_seconds(self) -> None:
        self.assertEqual(Settings.model_fields["sop_platform_poll_seconds"].default, 10.0)

    def test_platform_quiet_hours_defaults_match_production_contract(self) -> None:
        self.assertTrue(Settings.model_fields["sop_platform_quiet_hours_enabled"].default)
        self.assertEqual(Settings.model_fields["sop_platform_quiet_start_hour"].default, 0)
        self.assertEqual(Settings.model_fields["sop_platform_quiet_end_hour"].default, 8)
        self.assertEqual(Settings.model_fields["sop_platform_quiet_first_add_grace_minutes"].default, 30)

    def test_platform_prompt_has_layered_send_audit_contract(self) -> None:
        required_sections = (
            "# 1. 角色与任务",
            "# 2. 业务目标",
            "# 3. 输入说明",
            "# 4. 事实与指令优先级",
            "# 5. 决策流程",
            "# 6. 特殊情况与 no_send 边界",
            "# 7. 内容冲突时的处理",
            "# 8. use_ai_copy 边界",
            "# 9. 风格",
            "# 10. 输出合同",
        )
        for section in required_sections:
            self.assertIn(section, SOP_PLATFORM_TASK_SYSTEM_PROMPT)
        self.assertIn("普通沉默不是拒发理由", SOP_PLATFORM_TASK_SYSTEM_PROMPT)
        self.assertIn("首次加微任务用于建立第一次有效接触", SOP_PLATFORM_TASK_SYSTEM_PROMPT)
        self.assertIn("第三方任务字段，不是 AI 系统配置", SOP_PLATFORM_TASK_SYSTEM_PROMPT)
        self.assertIn("false` 表示若发送则必须原样发送平台内容", SOP_PLATFORM_TASK_SYSTEM_PROMPT)
        self.assertIn("文案不完全一致、仅语义相近", SOP_PLATFORM_TASK_SYSTEM_PROMPT)
        self.assertIn("从 `material_library` 选择适合当前场景", SOP_PLATFORM_TASK_SYSTEM_PROMPT)
        self.assertIn("使用 `authoritative_business_facts`", SOP_PLATFORM_TASK_SYSTEM_PROMPT)
        self.assertIn("不能延期、重排或创建后续任务", SOP_PLATFORM_TASK_SYSTEM_PROMPT)
        self.assertIn("只返回小写 `json` 对象", SOP_PLATFORM_TASK_SYSTEM_PROMPT)

    def test_platform_knowledge_prompt_has_current_business_contract(self) -> None:
        required = (
            "忽略平台原始待发送话术",
            "只有硬边界才允许 `no_send`",
            "普通沉默、普通价格/效果/距离/时间顾虑、客户说考虑一下，都必须 `send`",
            "性别称谓必须统一改为中性称谓",
            "当前淡斑活动价 268 元",
            "10 元预约金，到店抵扣，做的话再付 258 元",
            "不主动强调具体原价金额",
            "风险承诺和退款口径由模型结合知识库与权威事实自然处理",
            "图片/视频是重要消息类型",
            "sceneName = 分类名 + \"｜\" + 知识库名称",
        )
        for item in required:
            self.assertIn(item, SOP_PLATFORM_KNOWLEDGE_TASK_PROMPT)

    async def test_model_input_labels_original_content_as_audit_only_and_supporting_scene(self) -> None:
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
        self.assertEqual(payload["task"]["original_message_content_role"], "audit_only_ignore_for_generation")
        self.assertEqual(payload["task"]["scene_role"], "supporting_context")
        self.assertEqual(payload["task"]["platform_metadata"]["rule_id"], 81)
        self.assertEqual(payload["task"]["platform_metadata"]["scene_id"], 23)
        self.assertEqual(payload["task"]["original_message_content"], [_text("平台原文")])
        self.assertNotIn("sender_type", payload["task"]["platform_metadata"])
        self.assertIn("knowledge_base", payload)
        self.assertIn("authoritative_business_facts", payload)

    async def test_context_uses_80_message_timeline_with_beijing_time_scale(self) -> None:
        model = _Model([{"decision": "send", "reason": "test", "reply_messages": [_text("知识库生成")]}])
        service, _repo, _platform, system = _service(model=model)
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

    async def test_platform_knowledge_base_is_available_for_model_decision(self) -> None:
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
        self.assertTrue(payload["knowledge_base"]["available"])
        self.assertEqual(payload["knowledge_base"]["categories"][0]["categoryName"], "价格异议")
        self.assertEqual(payload["knowledge_base"]["items"][0]["knowledgeName"], "价格疑问")
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("自然承接")])

    async def test_platform_original_copy_is_ignored_and_model_messages_are_sent(self) -> None:
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "still useful",
                    "reply_messages": [_text("模型擅自改写")],
                }
            ]
        )
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False, message_content=[{"type": "text", "content": "平台原文"}])

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("模型擅自改写")])
        self.assertEqual(system.send_calls[0]["plan_id"], "platform-sop-101")
        self.assertEqual(system.send_calls[0]["task_id"], "platform-sop-send-101")
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_completed")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(system.conversation_calls, 2)

    async def test_duplicate_non_ai_copy_platform_task_is_completed_without_resend(self) -> None:
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
        first["scheduledAt"] = "2026-08-04 22:39:25"
        second = _task(use_ai_copy=False, message_content=[{"type": "text", "content": "平台原文"}])
        second["task_id"] = 102
        second["scheduledAt"] = "2026-08-04 22:39:25"

        first_result = await service.process_task(first)
        second_result = await service.process_task(second)

        self.assertEqual(first_result["status"], "sent")
        self.assertEqual(second_result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30), ("102", 20), ("102", 30)])
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("模型擅自改写")])
        second_payload = repo.tasks["platform-sop:102"]["send_payload"]
        self.assertEqual(second_payload["decision"]["reason"], "duplicate_platform_task_content")
        self.assertEqual(repo.events["platform_sop_task:102"]["status"], "platform_completed")
        self.assertEqual(len(model.calls), 1)

    async def test_non_ai_copy_stale_task_still_uses_model_before_send(self) -> None:
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
        self.assertEqual(system.conversation_calls, 2)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("模型擅自改写")])

    async def test_manual_resend_pre_cutover_task_sends_original_without_reclaiming_platform(self) -> None:
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

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(system.send_calls, [])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        platform.consume_calls.clear()

        resend = await service.admin_resend_task("101")

        self.assertEqual(resend["status"], "sent")
        self.assertEqual(system.conversation_calls, 1)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("平台原文")])
        self.assertEqual(system.send_calls[0]["plan_id"], "platform-sop-101")
        self.assertEqual(system.send_calls[0]["task_id"], "platform-sop-send-101")
        self.assertEqual(platform.consume_calls, [])
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

    async def test_quiet_hours_blocks_fixed_marketing_before_direct_send(self) -> None:
        model = _Model([])
        settings = _settings(quiet_hours_enabled=True)
        service, repo, platform, system = _service(model=model, settings=settings)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "schedule"
        task["scheduledAt"] = _beijing_epoch("2026-08-05 01:00:00")

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.conversation_calls, 0)
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(payload["decision"]["reason"], "quiet_hours_marketing_blocked")
        self.assertEqual(payload["context"]["quiet_hours"]["window"], "00:00-08:00")

    async def test_quiet_hours_blocks_ai_copy_marketing_before_model(self) -> None:
        model = _Model([])
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_max_task_age_seconds = 0
        service, _repo, platform, system = _service(model=model, settings=settings)
        task = _task(use_ai_copy=True)
        task["triggerEvent"] = "schedule"
        task["scheduledAt"] = _beijing_epoch("2026-08-05 00:00:00")

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.conversation_calls, 0)
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])

    async def test_quiet_hours_allows_recent_first_add_auto_opening(self) -> None:
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "first add remains useful",
                    "reply_messages": [_text("model copy ignored")],
                }
            ]
        )
        settings = _settings(quiet_hours_enabled=True)
        service, _repo, platform, system = _service(model=model, settings=settings)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
        current_beijing = datetime.now(timezone(timedelta(hours=8)))
        scheduled_at = current_beijing.replace(hour=1, minute=0, second=0, microsecond=0)
        if scheduled_at > current_beijing:
            scheduled_at -= timedelta(days=1)
        task["scheduledAt"] = scheduled_at.timestamp()
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "我已经添加了你，现在我们可以开始聊天了。",
                "msgtime": (scheduled_at - timedelta(minutes=29)).timestamp() * 1000,
            }
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.conversation_calls, 4)
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("model copy ignored")])
        self.assertEqual(len(model.calls), 1)

    async def test_first_day_unopened_first_add_task_continues_normal_model_chain(self) -> None:
        model = _Model(
            [
                {
                    "decision": "send",
                    "reason": "first add remains useful",
                    "reply_messages": [_text("model copy ignored")],
                }
            ]
        )
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
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
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("model copy ignored")])
        self.assertEqual(len(model.calls), 1)
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(
            payload["decision"]["reason"],
            "first add remains useful",
        )
        self.assertEqual(
            payload["context"]["first_day_platform_sop_route"]["route_reason"],
            "first_add_unopened_continued_normal_model_chain",
        )
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_completed")

    async def test_first_day_unopened_first_add_can_be_filtered_by_normal_model_chain(self) -> None:
        model = _Model(
            [
                {
                    "decision": "no_send",
                    "reason": "customer has severe complaint",
                    "reason_code": "complaint_or_refund",
                    "reply_messages": [],
                }
            ]
        )
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
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
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(len(model.calls), 1)
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(payload["decision"]["reason_code"], "complaint_or_refund")
        self.assertEqual(
            payload["context"]["first_day_platform_sop_route"]["opening_state"],
            "unopened",
        )

    async def test_first_day_real_customer_opening_is_consumed_without_send_or_model(self) -> None:
        model = _Model([])
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=True)
        task["triggerEvent"] = "add_wecom"
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "你好，在吗",
                "msgtime": int(time.time() * 1000),
            }
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(
            payload["decision"]["reason"],
            "first_add_opened_platform_sop_consumed_no_send",
        )

    async def test_first_day_unknown_opening_state_is_consumed_without_send(self) -> None:
        model = _Model([])
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_error = RuntimeError("conversation timeout")

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(
            payload["decision"]["reason"],
            "first_add_opening_state_unavailable_consumed_no_send",
        )

    async def test_first_day_customer_message_without_time_is_not_treated_as_unopened(self) -> None:
        model = _Model([])
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
        task["scheduledAt"] = datetime.now(timezone.utc).isoformat()
        system.conversation_payload["data"]["messages"] = [
            {"direction": "customer", "content": "你好，在吗"},
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])
        payload = next(iter(repo.tasks.values()))["send_payload"]
        self.assertEqual(
            payload["decision"]["reason"],
            "first_add_opening_state_unavailable_consumed_no_send",
        )

    async def test_quiet_hours_blocks_inactive_first_add(self) -> None:
        model = _Model([])
        settings = _settings(quiet_hours_enabled=True)
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

    async def test_quiet_hours_blocks_first_add_with_unanswered_customer_message(self) -> None:
        model = _Model([])
        settings = _settings(quiet_hours_enabled=True)
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
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
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
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])

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
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["send_payload"]["decision"]["reason_code"], "explicit_stop_contact")

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

    async def test_ai_copy_can_generate_text_from_trusted_scene_when_content_is_empty(self) -> None:
        model = _Model([{"decision": "send", "reason": "scene", "reply_messages": [_text("结合场景生成")]}])
        service, _repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=True, message_content=[])
        task["scene"] = {"sceneDesc": "活动后续提醒", "knowledgeText": "仅说明检测流程"}

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("结合场景生成")])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])

    async def test_ai_copy_without_content_or_scene_is_completed_without_send(self) -> None:
        model = _Model([])
        service, _repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=True, message_content=[])
        task["scene"] = {}

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(model.calls, [])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])

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
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])

    async def test_model_failure_stays_local_retry_and_does_not_fake_success(self) -> None:
        model = _Model(
            [
                {"decision": "defer", "delay_minutes": 30, "reason": "later", "reply_messages": []},
                {"decision": "retry_later", "reason": "later", "reply_messages": []},
            ]
        )
        service, repo, platform, system = _service(model=model)

        with self.assertRaisesRegex(RuntimeError, "invalid_sop_platform_model_output"):
            await service.process_task(_task())

        self.assertEqual(platform.consume_calls, [("101", 20)])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_processing_retry")

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
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_complete_pending")

        platform.consume_responses = [{"code": 200, "data": {"task_id": 101, "status": 30}}]
        result = await service.process_task(_task(), recovery_status="platform_complete_pending")

        self.assertTrue(result["processed"])
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_completed")

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
                "msgid": "ai-outreach-platform-sop-101-platform-sop-send-101-0001-text-existing",
                "msgtype": "text",
                "content": "平台原文",
            }
        ]

        result = await service.process_task(_task(use_ai_copy=False))

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(system.send_calls, [])
        self.assertEqual(repo.events["platform_sop_task:101"]["status"], "platform_completed")

    def test_platform_client_rejects_non_contract_statuses(self) -> None:
        client = SopPlatformClient(_settings())
        with self.assertRaisesRegex(ValueError, "20 or 30"):
            import asyncio

            asyncio.run(client.consume(task_id=1, status=40))

    async def test_pending_uses_unix_seconds_and_documented_limit(self) -> None:
        settings = _settings()
        settings.sop_platform_lookback_seconds = 604800
        settings.sop_platform_window_seconds = 60
        settings.sop_platform_batch_size = 500
        client = SopPlatformClient(settings)
        client._request = AsyncMock(return_value={"code": 200, "data": {"list": []}})  # type: ignore[method-assign]

        page = await client.pending()

        payload = client._request.await_args.kwargs["json_body"]  # type: ignore[union-attr]
        self.assertIsInstance(payload["start_time"], int)
        self.assertIsInstance(payload["end_time"], int)
        self.assertEqual(payload["limit"], 500)
        self.assertEqual(page["items"], [])
        self.assertEqual(page["total"], 0)

    async def test_invalid_identity_is_completed_without_model_or_send(self) -> None:
        model = _Model([])
        service, _repo, platform, system = _service(model=model)
        task = _task()
        task["corp_id"] = ""

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(model.calls, [])
        self.assertEqual(system.send_calls, [])

    async def test_stale_shadow_task_is_audited_without_consuming_or_calling_model(self) -> None:
        model = _Model([])
        service, _repo, platform, system = _service(model=model, shadow_mode=True)
        task = _task()
        task["scheduledAt"] = time.time() - 21601

        result = await service.process_task(task)

        self.assertEqual(result["status"], "shadow_no_send")
        self.assertEqual(result["decision"]["reason"], "stale_task")
        self.assertEqual(platform.consume_calls, [])
        self.assertEqual(model.calls, [])
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
                "send_payload": {"decision": {"decision": "no_send", "reason": "duplicate", "reply_messages": []}},
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
        sop_platform_max_task_age_seconds=21600,
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


def _task(*, use_ai_copy: bool = True, message_content: list[dict[str, Any]] | None = None):
    return {
        "task_id": 101,
        "ruleName": "test rule",
        "customerId": 22000001,
        "customer_wechat_id": "wm_external",
        "corp_id": "ww_corp",
        "user_wechat_id": "7294",
        "user_wechat": "DY258",
        "useAiCopy": use_ai_copy,
        "scene": {"name": "test"},
        "message_content": (
            message_content if message_content is not None else [{"type": "text", "content": "平台原文"}]
        ),
    }


def _text(value: str, order: int = 1):
    return {"type": "text", "order": order, "content": {"text": value}}


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
        self.consume_responses: list[dict[str, Any]] = []
        self.rule_data_calls: list[dict[str, Any]] = []

    async def consume(self, *, task_id, status):
        self.consume_calls.append((str(task_id), status))
        if self.consume_responses:
            return self.consume_responses.pop(0)
        return {"code": 200, "data": {"task_id": task_id, "status": status}}

    async def pending(self, *, limit=None):
        return {"items": [], "total": 0, "limit": limit}

    async def knowledge_categories(self, **_kwargs):
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
        self.conversation_calls = 0
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

    async def send(self, **kwargs):
        self.send_calls.append(kwargs)
        if self.send_responses:
            return self.send_responses.pop(0)
        return {"code": 0, "data": {"send_status": "sent"}}


class _CustomerContext:
    def load(self, **_kwargs):
        return {"source": "test", "orders": [], "appointment": {}}


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
        return self.outputs.pop(0)


class _HttpResponse:
    def __init__(self, *, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self):
        raise ValueError("not json")
