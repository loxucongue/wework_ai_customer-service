from __future__ import annotations

import asyncio
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

from app.config import Settings
from app.services.sop_platform_client import SopPlatformClient
from app.services.sop_objection_material_service import SopObjectionMaterialService
from app.services.sop_platform_task_service import (
    SOP_PLATFORM_BATCH_SYSTEM_PROMPT,
    SOP_PLATFORM_TASK_SYSTEM_PROMPT,
    SopPlatformTaskService,
    _batch_decision_error,
    _resolve_compatible_pending_tasks,
    _sop_platform_batch_business_facts_for_model,
)
from app.services.sop_reply_pack_service import SopReplyPackService


class SopPlatformTaskFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_pending_inline_content_keeps_legacy_task_without_store_lookup_content(self) -> None:
        legacy = _batch_task("101", text="旧任务原文")
        store = _batch_task("201", text="新版消息组")

        resolved, unresolved = _resolve_compatible_pending_tasks([legacy], [store])

        self.assertEqual([item["task_id"] for item in resolved], [101])
        self.assertEqual(resolved[0]["_aics_content_source"], "pending_inline")
        self.assertEqual(unresolved, [])

    def test_empty_pending_trigger_loads_all_matching_store_visit_message_groups(self) -> None:
        trigger = _batch_task("101", text="")
        trigger["message_content"] = []
        first = _batch_task("201", text="第一组")
        second = _batch_task("202", text="第二组")
        other_customer = _batch_task("301", text="其他客户")
        other_customer["customer_wechat_id"] = "wm_other"

        resolved, unresolved = _resolve_compatible_pending_tasks(
            [trigger],
            [first, second, other_customer],
        )

        self.assertEqual([item["task_id"] for item in resolved], [201, 202])
        self.assertTrue(all(item["_aics_content_source"] == "store_visit_pending" for item in resolved))
        self.assertTrue(
            all(
                [task["task_id"] for task in item["_aics_compat_trigger_tasks"]] == [101]
                for item in resolved
            )
        )
        self.assertEqual(unresolved, [])

    def test_empty_pending_trigger_without_matching_content_remains_unresolved(self) -> None:
        trigger = _batch_task("101", text="")
        trigger["message_content"] = []

        resolved, unresolved = _resolve_compatible_pending_tasks([trigger], [])

        self.assertEqual(resolved, [])
        self.assertEqual([item["task_id"] for item in unresolved], [101])

    def test_platform_poll_default_is_ten_seconds(self) -> None:
        self.assertEqual(Settings.model_fields["sop_platform_poll_seconds"].default, 10.0)

    def test_platform_quiet_hours_defaults_match_production_contract(self) -> None:
        self.assertFalse(Settings.model_fields["sop_platform_quiet_hours_enabled"].default)
        self.assertFalse(Settings.model_fields["sop_platform_deferred_replay_enabled"].default)
        self.assertEqual(Settings.model_fields["sop_platform_quiet_start_hour"].default, 0)
        self.assertEqual(Settings.model_fields["sop_platform_quiet_end_hour"].default, 8)
        self.assertEqual(Settings.model_fields["sop_platform_quiet_first_add_grace_minutes"].default, 30)

    def test_platform_sequence_decision_uses_dedicated_deepseek_defaults(self) -> None:
        self.assertEqual(Settings.model_fields["sop_platform_decision_model"].default, "deepseek-v4-flash")
        self.assertEqual(
            Settings.model_fields["sop_platform_decision_model_fallbacks"].default,
            "gpt-5.4-mini,gpt-5.4",
        )

    def test_platform_sequence_model_receives_safety_boundaries_without_activity_facts(self) -> None:
        facts = _sop_platform_batch_business_facts_for_model()

        self.assertEqual(facts["scope"], "safety_boundaries_only")
        self.assertNotIn("offer", facts)
        self.assertNotIn("transaction_policy", facts)
        serialized = json.dumps(facts, ensure_ascii=False)
        for forbidden_key in (
            "new_customer_price",
            "prepay_amount",
            "tail_amount",
            "refund_rule",
            "quota",
            "registration_gift",
        ):
            self.assertNotIn(forbidden_key, serialized)

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

    async def test_model_input_labels_executable_content_and_supporting_scene(self) -> None:
        model = _Model([{"decision": "no_send", "reason": "test", "reply_messages": []}])
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
        self.assertEqual(payload["task"]["message_content_role"], "executable_candidate")
        self.assertEqual(payload["task"]["scene_role"], "supporting_context")
        self.assertEqual(payload["task"]["platform_metadata"]["rule_id"], 81)
        self.assertEqual(payload["task"]["platform_metadata"]["scene_id"], 23)
        self.assertEqual(payload["task"]["message_content"], [_text("平台原文")])
        self.assertNotIn("sender_type", payload["task"]["platform_metadata"])
        self.assertIn("authoritative_business_facts", payload)

    async def test_context_uses_80_message_timeline_with_beijing_time_scale(self) -> None:
        model = _Model([{"decision": "no_send", "reason": "test", "reply_messages": []}])
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

        self.assertEqual(system.conversation_limits, [80])
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

    async def test_material_library_is_available_for_ai_copy_conflict_repair(self) -> None:
        model = _Model([{"decision": "send", "reason": "material", "reply_messages": [_text("自然承接")]}])
        material_service = _Materials()
        service, _repo, _platform, system = _service(model=model, material_service=material_service)
        task = _task(use_ai_copy=True)
        task["triggerEvent"] = "add_wecom"

        await service.process_task(task)

        payload = json.loads(model.calls[0][1]["content"])
        self.assertEqual(payload["task"]["task_type"], "add_wecom")
        self.assertEqual(payload["material_library"][0]["material_id"], "price_001")
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("自然承接")])

    async def test_non_ai_copy_is_sent_exactly_and_completes_three_state_flow(self) -> None:
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
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("平台原文")])
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
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("平台原文")])
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
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False)
        task["scheduledAt"] = time.time() - 86400

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(system.conversation_calls, 2)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("平台原文")])

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
        self.assertEqual(payload["decision"]["reason"], "quiet_hours_all_sop_blocked")
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

    async def test_quiet_hours_blocks_recent_first_add_auto_opening(self) -> None:
        model = _Model([])
        settings = _settings(quiet_hours_enabled=True)
        service, _repo, platform, system = _service(model=model, settings=settings)
        task = _task(use_ai_copy=False)
        task["triggerEvent"] = "add_wecom"
        task["scheduledAt"] = _beijing_epoch("2026-08-05 01:00:00")
        system.conversation_payload["data"]["messages"] = [
            {
                "direction": "customer",
                "content": "我已经添加了你，现在我们可以开始聊天了。",
                "msgtime": _beijing_epoch("2026-08-05 00:31:00") * 1000,
            }
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        self.assertEqual(system.conversation_calls, 0)
        self.assertEqual(len(system.send_calls), 0)
        self.assertEqual(len(model.calls), 0)

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
        self.assertEqual(payload["decision"]["reason"], "quiet_hours_all_sop_blocked")
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
        self.assertEqual(payload["decision"]["reason"], "quiet_hours_all_sop_blocked")

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
        model = _Model([{"decision": "no_send", "reason": "already paid", "reply_messages": []}])
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
                {"decision": "no_send", "reason_code": "similar_content", "reason": "similar", "reply_messages": []},
            ]
        )
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=False, message_content=[{"type": "text", "content": "平台原文"}])
        task["triggerEvent"] = "add_wecom"

        result = await service.process_task(task)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("平台原文")])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["send_payload"]["decision"]["reason"], "first_add_default_send_after_invalid_no_send_reason")

    async def test_first_add_allowed_no_send_reason_still_completes_without_send(self) -> None:
        model = _Model(
            [
                {
                    "decision": "no_send",
                    "reason_code": "unresolved_customer_question",
                    "reason": "latest customer asks for store lookup and no assistant has answered",
                    "reply_messages": [],
                }
            ]
        )
        service, repo, platform, system = _service(model=model)
        task = _task(use_ai_copy=True)
        task["triggerEvent"] = "add_wecom"
        system.conversation_payload["data"]["messages"] = [
            {"direction": "customer", "content": "你们店在哪里", "msgtime": _beijing_epoch("2026-08-05 10:00:00") * 1000}
        ]

        result = await service.process_task(task)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(system.send_calls, [])
        self.assertEqual(platform.consume_calls, [("101", 20), ("101", 30)])
        stored = next(iter(repo.tasks.values()))
        self.assertEqual(stored["send_payload"]["decision"]["reason_code"], "unresolved_customer_question")

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
                {"decision": "no_send", "reason": "not now", "reply_messages": []},
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
        with self.assertRaisesRegex(ValueError, "20, 30, or 70"):
            import asyncio

            asyncio.run(client.consume(task_id=1, status=40))

    async def test_pending_uses_platform_due_queue_without_legacy_time_window(self) -> None:
        settings = _settings()
        settings.sop_platform_lookback_seconds = 604800
        settings.sop_platform_window_seconds = 60
        settings.sop_platform_batch_size = 500
        client = SopPlatformClient(settings)
        client._request = AsyncMock(return_value={"code": 200, "data": {"list": []}})  # type: ignore[method-assign]

        page = await client.pending()

        payload = client._request.await_args.kwargs["json_body"]  # type: ignore[union-attr]
        self.assertNotIn("start_time", payload)
        self.assertNotIn("end_time", payload)
        self.assertEqual(payload["corp_id"], "")
        self.assertEqual(payload["wechat"], "")
        self.assertEqual(payload["limit"], 500)
        self.assertEqual(page["items"], [])
        self.assertEqual(page["total"], 0)
        self.assertTrue(page["complete"])

    async def test_store_visit_pending_uses_dedicated_test_endpoint(self) -> None:
        client = SopPlatformClient(_settings())
        client._request = AsyncMock(return_value={"code": 200, "data": {"list": []}})  # type: ignore[method-assign]

        page = await client.store_visit_pending(limit=100)

        self.assertEqual(client._request.await_args.args[1], "/event/trigger/store-visit-pending")  # type: ignore[union-attr]
        self.assertEqual(page["biz_type"], "store_visit")

    async def test_consume_supports_no_send_remark_and_content_exhausted(self) -> None:
        client = SopPlatformClient(_settings())
        client._request = AsyncMock(
            return_value={"code": 200, "data": {"task_id": 101, "status": 70}}
        )  # type: ignore[method-assign]

        await client.consume(
            task_id=101,
            status=70,
            remark="客户已由人工接待，无需发送",
            content_exhausted=False,
        )

        payload = client._request.await_args.kwargs["json_body"]  # type: ignore[union-attr]
        self.assertEqual(
            payload,
            {
                "taskId": 101,
                "status": 70,
                "remark": "客户已由人工接待，无需发送",
                "contentExhausted": False,
            },
        )

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
        model = _Model([{"decision": "no_send", "reason": "test", "reply_messages": []}])
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

    async def test_admin_run_logs_projects_batch_sequence_and_legacy_record(self) -> None:
        model = _Model([])
        service, repo, platform, _system = _service(model=model, shadow_mode=True)
        tasks = [
            {
                **_task(message_content=[{"type": "text", "content": f"原始消息{task_id}"}]),
                "task_id": task_id,
                "scheduledAt": f"2026-08-27 09:0{index}:00",
                "sortOrder": index,
                "_aics_biz_type": "online_service",
            }
            for index, task_id in enumerate((101, 102, 103, 104), start=1)
        ]

        async def pending(*, limit=None):
            return {"items": tasks, "total": 4, "limit": limit}

        platform.pending = pending  # type: ignore[method-assign]
        decision = {
            "selected_task_id": "103",
            "evaluations": [
                {"task_id": "101", "decision": "skip", "reason": "门店已处理", "evidence_refs": ["msg_001"]},
                {"task_id": "102", "decision": "skip", "reason": "报价已发送", "evidence_refs": ["msg_002"]},
                {"task_id": "103", "decision": "send", "reason": "仍有新的唤醒目的", "evidence_refs": ["msg_003"]},
            ],
        }
        audit = {
            "audit_schema_version": 2,
            "processing_mode": "customer_batch_sequence",
            "batch_run_id": "online_service:101",
            "batch_key": "online_service|ww_corp|dy258|wm_external",
            "biz_type": "online_service",
            "batch_task_ids": ["101", "102", "103", "104"],
            "decision": decision,
            "skipped_prefix_task_ids": ["101", "102"],
            "transition_text": "前面您已经了解过门店和活动，我接着说下这次安排。",
            "final_messages": [_text("前面您已经了解过门店和活动，我接着说下这次安排。"), _text("原始消息103", 2)],
            "context": {
                "management_mode": "ai",
                "management_source": "conversation.ai_auto_reply",
                "customer_opened": True,
                "same_day_unopened": False,
                "timeline_structure": {"message_count": 8, "customer_message_count": 3},
            },
            "consume_results": [
                {"task_id": "101", "status": 70, "remark": "superseded_by_later_sendable_group"},
                {"task_id": "102", "status": 70, "remark": "superseded_by_later_sendable_group"},
                {"task_id": "103", "status": 30, "remark": ""},
            ],
        }

        def record(task, *, status, event_status, send_payload):
            task_id = str(task["task_id"])
            return {
                "event_id": f"platform_sop_task:{task_id}",
                "event_status": event_status,
                "event_error": "",
                "received_at": "2026-08-27T01:00:00+00:00",
                "event_updated_at": "2026-08-27T01:10:00+00:00",
                "platform_task": task,
                "local_task_id": f"local-{task_id}",
                "customer_id": "22000001",
                "external_userid": "wm_external",
                "corp_id": "ww_corp",
                "user_id": "7294",
                "wechat": "DY258",
                "sop_pack_name": "test rule",
                "task_status": status,
                "reply_messages": [_text(f"原始消息{task_id}")],
                "send_payload": send_payload,
                "send_response": (
                    {
                        "data": {
                            "delivery_status": "sent",
                            "system_msgid": "system-message-103",
                            "system_msgids": ["system-message-103", "system-message-104"],
                        }
                    }
                    if task_id == "103"
                    else {}
                ),
                "task_error": "",
                "task_created_at": "2026-08-27T01:00:00+00:00",
                "task_updated_at": "2026-08-27T01:10:00+00:00",
                "sent_at": "2026-08-27T01:10:00+00:00" if task_id == "103" else "",
            }

        legacy_task = {**_task(), "task_id": 99, "scheduledAt": "2026-08-26 18:00:00"}
        records = [
            record(tasks[0], status="completed_without_send", event_status="platform_completed", send_payload=audit),
            record(tasks[1], status="completed_without_send", event_status="platform_completed", send_payload=audit),
            record(tasks[2], status="sent", event_status="platform_completed", send_payload=audit),
            record(tasks[3], status="platform_queued", event_status="platform_queued", send_payload={}),
            record(
                legacy_task,
                status="sent",
                event_status="platform_completed",
                send_payload={"decision": {"decision": "send", "reason": "legacy", "reply_messages": [_text("旧版发送")] }},
            ),
        ]
        repository_filters = {}

        def list_records(**kwargs):
            repository_filters.update(kwargs)
            return records

        repo.list_platform_sop_task_records = list_records  # type: ignore[attr-defined]

        result = await service.admin_run_logs(
            limit=20,
            wechat="dy258",
            date_from="2026-08-26T08:00",
            date_to="2026-08-27T10:00",
        )

        batch = next(run for run in result["runs"] if run["log_version"] == "batch_v2")
        self.assertEqual(batch["status"], "completed")
        self.assertEqual(batch["summary_text"], "跳过 2 条，发送第 3 条，剩余 1 条未处理")
        self.assertEqual([item["sequence_state"] for item in batch["tasks"]], ["skipped", "skipped", "selected", "untouched"])
        self.assertEqual([item["consume_status"] for item in batch["tasks"]], [70, 70, 30, None])
        self.assertEqual(batch["customer_state"]["management_mode"], "ai")
        identifiers = {(item["key"], item["value"], item["source"]) for item in batch["identifiers"]}
        self.assertIn(("run_id", "online_service:101", "运行批次"), identifiers)
        self.assertIn(("platform_task.task_id", "101", "第三方任务"), identifiers)
        self.assertIn(("event_id", "platform_sop_task:101", "本地事件"), identifiers)
        self.assertIn(("local_task_id", "local-101", "本地发送任务"), identifiers)
        self.assertIn(("send_response.data.system_msgids[1]", "system-message-104", "消息发送"), identifiers)
        self.assertEqual(repository_filters["wechat"], "dy258")
        self.assertEqual(repository_filters["date_from"], "2026-08-26T00:00:00+00:00")
        self.assertEqual(repository_filters["date_to"], "2026-08-27T02:00:00+00:00")
        legacy = next(run for run in result["runs"] if run["log_version"] == "legacy_single")
        self.assertIn("顺序判断", legacy["missing_fields"])
        self.assertEqual(legacy["summary_text"], "历史单任务：发送完成")

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

    async def test_quiet_poll_persists_and_enqueues_for_no_replay_consumption(self) -> None:
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_quiet_start_hour = 0
        settings.sop_platform_quiet_end_hour = 0
        service, repo, platform, _system = _service(model=_Model([]), settings=settings)
        platform.pending = AsyncMock(return_value={"items": [_batch_task("1")], "total": 1})
        platform.store_visit_pending = AsyncMock(return_value={"items": [], "total": 0})

        result = await service.poll_once()

        self.assertEqual(result["pending_count"], 1)
        self.assertEqual(result["enqueued_count"], 1)
        self.assertEqual(len(repo.events), 1)
        self.assertEqual(len(repo.tasks), 1)
        self.assertEqual(platform.consume_calls, [])

    async def test_batch_queued_before_quiet_hours_is_archived_and_consumed_without_send(self) -> None:
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_quiet_start_hour = 0
        settings.sop_platform_quiet_end_hour = 0
        service, repo, platform, system = _service(model=_Model([]), settings=settings)

        result = await service.process_customer_batch(_customer_batch(_batch_task("1")))

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("1", 70)])
        self.assertEqual(platform.consume_details[0]["remark"], "quiet_hours_no_replay")
        self.assertEqual(system.conversation_calls, 0)
        self.assertEqual(system.send_calls, [])
        payload = next(iter(repo.tasks.values()))["send_payload"]
        archive = payload["quiet_hours_archive"]
        self.assertFalse(archive["no_replay"])
        self.assertEqual(payload["deferred_replay"]["status"], "pending")
        self.assertEqual(payload["deferred_replay"]["interval_seconds"], 600)
        self.assertEqual(archive["ordered_groups"][0]["task_id"], "1")
        self.assertEqual(
            archive["ordered_groups"][0]["original_messages"],
            [{"type": "text", "order": 1, "content": {"text": "平台原文"}}],
        )

    async def test_quiet_hours_archives_and_consumes_all_customer_groups_in_order(self) -> None:
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_quiet_start_hour = 0
        settings.sop_platform_quiet_end_hour = 0
        model = _Model([])
        service, repo, platform, system = _service(model=model, settings=settings)

        result = await service.process_customer_batch(
            _customer_batch(
                _batch_task("3", text="第三条，价格原文不改"),
                _batch_task("1", text="第一条，价格原文不改"),
                _batch_task("2", text="第二条，价格原文不改"),
            )
        )

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("1", 70), ("2", 70), ("3", 70)])
        self.assertTrue(all(item["remark"] == "quiet_hours_no_replay" for item in platform.consume_details))
        self.assertEqual(system.conversation_calls, 0)
        self.assertEqual(system.send_calls, [])
        self.assertEqual(model.calls, [])
        payload = next(iter(repo.tasks.values()))["send_payload"]
        groups = payload["quiet_hours_archive"]["ordered_groups"]
        self.assertEqual([item["task_id"] for item in groups], ["1", "2", "3"])
        self.assertEqual(
            [item["original_messages"][0]["content"]["text"] for item in groups],
            ["第一条，价格原文不改", "第二条，价格原文不改", "第三条，价格原文不改"],
        )

    async def test_deferred_replay_sends_only_earliest_original_group_per_interval(self) -> None:
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_quiet_start_hour = 0
        settings.sop_platform_quiet_end_hour = 0
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "send", "reason": "仍适合触达", "evidence_refs": ["task:1"]}
                    ],
                    "selected_task_id": "1",
                    "transition_text": "",
                },
                {
                    "evaluations": [
                        {"task_id": "2", "decision": "send", "reason": "仍适合触达", "evidence_refs": ["task:2"]}
                    ],
                    "selected_task_id": "2",
                    "transition_text": "",
                },
            ]
        )
        service, _repo, _platform, system = _service(model=model, settings=settings)
        await service.process_customer_batch(
            _customer_batch(
                _batch_task("1", text="first original message"),
                _batch_task("2", text="second original message"),
            )
        )
        settings.sop_platform_quiet_hours_enabled = False

        first = await service.process_deferred_replays()
        immediate_second = await service.process_deferred_replays()

        self.assertEqual(first, 1)
        self.assertEqual(immediate_second, 0)
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("first original message")])

        with patch(
            "app.services.sop_platform_task_service.time.time",
            return_value=time.time() + 601,
        ):
            second = await service.process_deferred_replays()

        self.assertEqual(second, 1)
        self.assertEqual(len(system.send_calls), 2)
        self.assertEqual(system.send_calls[1]["reply_messages"], [_text("second original message")])

    async def test_new_daytime_task_is_consumed_and_appended_behind_active_replay(self) -> None:
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_quiet_start_hour = 0
        settings.sop_platform_quiet_end_hour = 0
        service, repo, platform, system = _service(model=_Model([]), settings=settings)
        await service.process_customer_batch(_customer_batch(_batch_task("1", text="night backlog")))
        settings.sop_platform_quiet_hours_enabled = False
        service._refresh_deferred_replay_keys()

        result = await service.process_customer_batch(
            _customer_batch(_batch_task("2", text="new daytime task"))
        )

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("1", 70), ("2", 70)])
        self.assertEqual(system.send_calls, [])
        payload = repo.tasks["platform-sop:2"]["send_payload"]
        self.assertEqual(payload["reason"], "deferred_behind_quiet_backlog")
        self.assertEqual(payload["deferred_replay"]["status"], "pending")

    async def test_deferred_replay_waits_for_delivery_callback_before_next_group(self) -> None:
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_quiet_start_hour = 0
        settings.sop_platform_quiet_end_hour = 0
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "send", "reason": "仍适合触达", "evidence_refs": ["task:1"]}
                    ],
                    "selected_task_id": "1",
                    "transition_text": "",
                }
            ]
        )
        service, repo, _platform, system = _service(model=model, settings=settings)
        await service.process_customer_batch(
            _customer_batch(_batch_task("1"), _batch_task("2"))
        )
        settings.sop_platform_quiet_hours_enabled = False
        system.send_responses.append(
            {
                "code": 0,
                "data": {
                    "callback_required": True,
                    "delivery_status": "platform_accepted",
                },
            }
        )

        accepted = await service.process_deferred_replays()
        blocked_while_sending = await service.process_deferred_replays()

        self.assertEqual(accepted, 1)
        self.assertEqual(blocked_while_sending, 0)
        first = repo.tasks["platform-sop:1"]
        self.assertEqual(first["send_payload"]["deferred_replay"]["status"], "sending")

        await service.finalize_message_delivery(
            {
                "status": "send_succeeded",
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
                "source_task_id": first["id"],
                "source_context": {
                    "sop_send_task_id": first["id"],
                    "sop_event_id": "platform_sop_task:1",
                    "platform_task_id": "1",
                    "deferred_replay": True,
                },
            }
        )

        self.assertEqual(
            repo.tasks["platform-sop:1"]["send_payload"]["deferred_replay"]["status"],
            "sent",
        )

    async def test_deferred_replay_human_takeover_skips_queue_without_sending(self) -> None:
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_quiet_start_hour = 0
        settings.sop_platform_quiet_end_hour = 0
        service, repo, _platform, system = _service(model=_Model([]), settings=settings)
        await service.process_customer_batch(
            _customer_batch(_batch_task("1"), _batch_task("2"))
        )
        settings.sop_platform_quiet_hours_enabled = False
        system.conversation_payload["data"]["ai_auto_reply"] = False

        result = await service.process_deferred_replays()

        self.assertEqual(result, 0)
        self.assertEqual(system.send_calls, [])
        self.assertTrue(
            all(
                task["send_payload"]["deferred_replay"]["status"] == "skipped"
                for task in repo.tasks.values()
            )
        )

    async def test_deferred_replay_uses_ordered_model_filter_and_keeps_later_groups_pending(self) -> None:
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_quiet_start_hour = 0
        settings.sop_platform_quiet_end_hour = 0
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "skip", "reason": "门店已处理", "evidence_refs": ["task:1"]},
                        {"task_id": "2", "decision": "skip", "reason": "标准报价已完成", "evidence_refs": ["task:2"]},
                        {"task_id": "3", "decision": "send", "reason": "含预约金但仍有新的唤醒目的", "evidence_refs": ["task:3"]},
                    ],
                    "selected_task_id": "3",
                    "transition_text": "",
                }
            ]
        )
        service, repo, _platform, system = _service(model=model, settings=settings)
        await service.process_customer_batch(
            _customer_batch(
                _batch_task("1", text="重复门店询问"),
                _batch_task("2", text="重复标准报价"),
                _batch_task("3", text="预约金卡点唤醒内容"),
                _batch_task("4", text="后续尚未处理内容"),
            )
        )
        settings.sop_platform_quiet_hours_enabled = False

        result = await service.process_deferred_replays()

        self.assertEqual(result, 1)
        self.assertEqual(len(system.send_calls), 1)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("预约金卡点唤醒内容")])
        self.assertEqual(repo.tasks["platform-sop:1"]["send_payload"]["deferred_replay"]["status"], "skipped")
        self.assertEqual(repo.tasks["platform-sop:2"]["send_payload"]["deferred_replay"]["status"], "skipped")
        self.assertEqual(repo.tasks["platform-sop:3"]["send_payload"]["deferred_replay"]["status"], "sent")
        self.assertEqual(repo.tasks["platform-sop:4"]["send_payload"]["deferred_replay"]["status"], "pending")

    async def test_deferred_replay_all_filtered_skips_all_without_sending(self) -> None:
        settings = _settings(quiet_hours_enabled=True)
        settings.sop_platform_quiet_start_hour = 0
        settings.sop_platform_quiet_end_hour = 0
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "skip", "reason": "已处理", "evidence_refs": ["task:1"]},
                        {"task_id": "2", "decision": "skip", "reason": "已处理", "evidence_refs": ["task:2"]},
                    ],
                    "selected_task_id": "",
                    "transition_text": "",
                }
            ]
        )
        service, repo, _platform, system = _service(model=model, settings=settings)
        await service.process_customer_batch(
            _customer_batch(_batch_task("1"), _batch_task("2"))
        )
        settings.sop_platform_quiet_hours_enabled = False

        result = await service.process_deferred_replays()

        self.assertEqual(result, 0)
        self.assertEqual(system.send_calls, [])
        self.assertTrue(
            all(
                task["send_payload"]["deferred_replay"]["status"] == "skipped"
                for task in repo.tasks.values()
            )
        )

    async def test_incomplete_pending_page_is_not_processed(self) -> None:
        service, repo, platform, _system = _service(model=_Model([]))
        platform.pending = AsyncMock(return_value={"items": [_batch_task("1")], "total": 2})
        platform.store_visit_pending = AsyncMock(return_value={"items": [], "total": 0})

        result = await service.poll_once()

        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["enqueued_count"], 0)
        self.assertEqual(repo.events, {})

    async def test_poll_does_not_query_full_content_endpoint_when_pending_has_inline_content(self) -> None:
        service, _repo, platform, _system = _service(model=_Model([]), shadow_mode=True)
        platform.pending = AsyncMock(return_value={"items": [_batch_task("1")], "total": 1})
        platform.store_visit_pending = AsyncMock(side_effect=AssertionError("unexpected content lookup"))

        result = await service.poll_once()

        self.assertEqual(result["enqueued_count"], 1)
        platform.store_visit_pending.assert_not_awaited()

    async def test_poll_queries_full_content_endpoint_for_empty_pending_trigger(self) -> None:
        service, _repo, platform, _system = _service(model=_Model([]), shadow_mode=True)
        trigger = _batch_task("101", text="")
        trigger["message_content"] = []
        content = _batch_task("201", text="完整消息组")
        platform.pending = AsyncMock(return_value={"items": [trigger], "total": 1})
        platform.store_visit_pending = AsyncMock(return_value={"items": [content], "total": 1})

        result = await service.poll_once()

        self.assertEqual(result["enqueued_count"], 1)
        platform.store_visit_pending.assert_awaited_once_with(limit=500)
        queued = await service._queue.get()
        self.assertEqual([item["task_id"] for item in queued["tasks"]], [201])
        self.assertEqual([item["task_id"] for item in queued["compat_trigger_tasks"]], [101])
        service._queue.task_done()

    async def test_human_takeover_consumes_all_due_groups_without_model(self) -> None:
        service, _repo, platform, system = _service(model=_Model([]))
        system.conversation_payload["data"]["ai_auto_reply"] = False
        batch = _customer_batch(_batch_task("1"), _batch_task("2"))

        result = await service.process_customer_batch(batch)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("1", 70), ("2", 70)])
        self.assertTrue(all(item["remark"] == "human_takeover" for item in platform.consume_details))
        self.assertEqual(
            [item["content_exhausted"] for item in platform.consume_details],
            [None, True],
        )
        self.assertEqual(system.conversation_calls, 1)
        self.assertEqual(system.send_calls, [])

    async def test_management_mode_comes_from_status_endpoint_not_message_history(self) -> None:
        service, _repo, platform, system = _service(model=_Model([]))
        system.conversation_payload["data"].pop("ai_auto_reply")
        system.conversation_status = AsyncMock(
            return_value={
                "code": 0,
                "data": {
                    "takeover": {
                        "mode": "human",
                        "ai_auto_reply": False,
                        "handoff_status": "human_pending",
                    }
                },
            }
        )

        result = await service.process_customer_batch(
            _customer_batch(_batch_task("1"), _batch_task("2"))
        )

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("1", 70), ("2", 70)])
        self.assertEqual(system.conversation_calls, 1)
        system.conversation_status.assert_awaited_once()

    async def test_deleted_relation_has_priority_over_human_takeover_and_exhausts_future_tasks(self) -> None:
        service, _repo, platform, system = _service(model=_Model([]))
        system.conversation_payload["data"]["ai_auto_reply"] = False
        system.conversation_payload["data"]["customer_relation"] = {
            "status": "deleted",
            "is_deleted": True,
        }

        result = await service.process_customer_batch(
            _customer_batch(_batch_task("1"), _batch_task("2"))
        )

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("1", 70), ("2", 70)])
        self.assertTrue(
            all(item["remark"] == "customer_relation_deleted" for item in platform.consume_details)
        )
        self.assertEqual(
            [item["content_exhausted"] for item in platform.consume_details],
            [None, True],
        )
        self.assertEqual(system.conversation_calls, 1)
        self.assertEqual(system.send_calls, [])

    async def test_human_takeover_consumes_resolved_content_and_empty_trigger_without_send(self) -> None:
        service, _repo, platform, system = _service(model=_Model([]))
        system.conversation_payload["data"]["ai_auto_reply"] = False
        trigger = _batch_task("101", text="")
        trigger["message_content"] = []
        content = _batch_task("201", text="完整消息组")
        batch = _customer_batch(content)
        batch["compat_trigger_tasks"] = [trigger]

        result = await service.process_customer_batch(batch)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("201", 70), ("101", 70)])
        self.assertTrue(all(item["remark"] == "human_takeover" for item in platform.consume_details))
        self.assertEqual(
            [item["content_exhausted"] for item in platform.consume_details],
            [None, True],
        )
        self.assertEqual(system.send_calls, [])

    async def test_missing_customer_relation_does_not_send_or_consume(self) -> None:
        service, _repo, platform, system = _service(model=_Model([]))
        system.conversation_payload["data"].pop("customer_relation")

        with self.assertRaisesRegex(RuntimeError, "missing customer_relation"):
            await service.process_customer_batch(_customer_batch(_batch_task("1")))

        self.assertEqual(platform.consume_calls, [])
        self.assertEqual(system.send_calls, [])

    async def test_human_takeover_exhausts_each_distinct_platform_run_after_due_tasks(self) -> None:
        service, _repo, platform, system = _service(model=_Model([]))
        system.conversation_payload["data"]["ai_auto_reply"] = False
        first = _batch_task("1")
        second = _batch_task("2")
        first["runId"] = "run-a"
        second["runId"] = "run-b"

        await service.process_customer_batch(_customer_batch(first, second))

        self.assertEqual(
            platform.consume_calls,
            [("1", 70), ("2", 70)],
        )
        self.assertEqual(
            [item["content_exhausted"] for item in platform.consume_details],
            [True, True],
        )

    async def test_same_day_unopened_sends_only_earliest_group_verbatim(self) -> None:
        service, _repo, platform, system = _service(model=_Model([]))
        system.conversation_payload["data"]["messages"] = [
            {"direction": "customer", "content": "我已经添加了你，现在我们可以开始聊天了。"}
        ]
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        first = _batch_task("1", text="第一组原文", trigger_event="add_wecom", operate_time=today)
        second = _batch_task("2", text="第二组原文")

        result = await service.process_customer_batch(_customer_batch(first, second))

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("1", 20), ("1", 30)])
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("第一组原文")])

    async def test_same_day_unopened_maps_exact_reservation_marker_to_payment_card(self) -> None:
        service, _repo, platform, system = _service(model=_Model([]))
        system.conversation_payload["data"]["messages"] = [
            {"direction": "customer", "content": "我已经添加了你，现在我们可以开始聊天了。"}
        ]
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        task = _batch_task("1", trigger_event="add_wecom", operate_time=today)
        task["message_content"] = [
            {"type": "text", "content": "看一下报名方式"},
            {"type": "text", "content": " 预约卡片 "},
        ]

        result = await service.process_customer_batch(_customer_batch(task))

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("1", 20), ("1", 30)])
        self.assertEqual(
            system.send_calls[0]["reply_messages"],
            [
                _text("看一下报名方式"),
                {
                    "type": "payment_collection",
                    "order": 2,
                    "content": {"amount": 10, "remark": ""},
                },
            ],
        )

    async def test_reservation_marker_phrase_remains_plain_text(self) -> None:
        service, _repo, _platform, system = _service(model=_Model([]))
        system.conversation_payload["data"]["messages"] = [
            {"direction": "customer", "content": "我已经添加了你，现在我们可以开始聊天了。"}
        ]
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        task = _batch_task("1", trigger_event="add_wecom", operate_time=today)
        task["message_content"] = [
            {"type": "text", "content": "我给您说明一下预约卡片怎么使用。"}
        ]

        await service.process_customer_batch(_customer_batch(task))

        self.assertEqual(
            system.send_calls[0]["reply_messages"],
            [_text("我给您说明一下预约卡片怎么使用。")],
        )

    async def test_opened_customer_consumes_skipped_prefix_after_selected_send(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "skip", "reason": "已处理", "evidence_refs": ["msg_001"]},
                        {"task_id": "2", "decision": "skip", "reason": "已重复", "evidence_refs": ["task:2"]},
                        {"task_id": "3", "decision": "send", "reason": "仍适合", "evidence_refs": ["task:3"]},
                    ],
                    "selected_task_id": "3",
                    "transition_text": "",
                }
            ]
        )
        service, repo, platform, system = _service(model=model)
        tasks = [_batch_task(str(index), text=f"原文{index}") for index in range(1, 5)]

        result = await service.process_customer_batch(_customer_batch(*tasks))

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("3", 20), ("1", 70), ("2", 70), ("3", 30)])
        self.assertNotIn(("4", 70), platform.consume_calls)
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("原文3")])
        audit = repo.tasks["platform-sop:3"]["send_payload"]
        self.assertEqual(audit["audit_schema_version"], 2)
        self.assertEqual(audit["batch_task_ids"], ["1", "2", "3", "4"])
        self.assertEqual(audit["context"]["management_mode"], "ai")
        self.assertTrue(audit["context"]["customer_opened"])
        self.assertEqual(
            [(item["task_id"], item["status"]) for item in audit["consume_results"]],
            [("1", 70), ("2", 70), ("3", 30)],
        )

    async def test_successful_resolved_content_send_consumes_compat_trigger_after_delivery(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "201", "decision": "send", "reason": "仍适合", "evidence_refs": ["task:201"]}
                    ],
                    "selected_task_id": "201",
                    "transition_text": "",
                }
            ]
        )
        service, _repo, platform, system = _service(model=model)
        trigger = _batch_task("101", text="")
        trigger["message_content"] = []
        content = _batch_task("201", text="完整消息组")
        batch = _customer_batch(content)
        batch["compat_trigger_tasks"] = [trigger]

        result = await service.process_customer_batch(batch)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("201", 20), ("201", 30), ("101", 70)])
        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("完整消息组")])
        self.assertEqual(
            platform.consume_details[-1]["remark"],
            "content_resolved_from_store_visit_queue",
        )

    async def test_batch_model_call_uses_only_the_dedicated_model_order(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "send", "reason": "仍适合", "evidence_refs": ["task:1"]}
                    ],
                    "selected_task_id": "1",
                    "transition_text": "",
                }
            ]
        )
        settings = _settings()
        settings.sop_platform_decision_model = "deepseek-v4-flash"
        settings.sop_platform_decision_model_fallbacks = "gpt-5.4-mini,gpt-5.4"
        settings.sop_platform_decision_api_key = "dedicated-test-key"
        settings.sop_platform_decision_base_url = "https://api.deepseek.com"
        settings.sop_platform_decision_primary_timeout_seconds = 12
        service, _repo, _platform, _system = _service(model=model, settings=settings)

        await service.process_customer_batch(_customer_batch(_batch_task("1")))

        self.assertEqual(
            model.kwargs[0]["model_names_override"],
            ["deepseek-v4-flash"],
        )
        self.assertEqual(model.kwargs[0]["api_key_override"], "dedicated-test-key")
        self.assertEqual(model.kwargs[0]["base_url_override"], "https://api.deepseek.com")
        self.assertEqual(model.kwargs[0]["request_body_overrides"], {"thinking": {"type": "disabled"}})

    async def test_batch_model_failure_uses_gpt_fallback_without_deepseek_credentials(self) -> None:
        model = _Model(
            [
                TimeoutError("deepseek timeout"),
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "send", "reason": "仍适合", "evidence_refs": ["task:1"]}
                    ],
                    "selected_task_id": "1",
                    "transition_text": "",
                },
            ]
        )
        settings = _settings()
        settings.sop_platform_decision_model = "deepseek-v4-flash"
        settings.sop_platform_decision_model_fallbacks = "gpt-5.4-mini,gpt-5.4"
        settings.sop_platform_decision_api_key = "dedicated-test-key"
        settings.sop_platform_decision_base_url = "https://api.deepseek.com"
        settings.sop_platform_decision_primary_timeout_seconds = 12
        service, _repo, _platform, _system = _service(model=model, settings=settings)

        result = await service.process_customer_batch(_customer_batch(_batch_task("1")))

        self.assertEqual(result["status"], "sent")
        self.assertEqual(model.kwargs[0]["model_names_override"], ["deepseek-v4-flash"])
        self.assertEqual(model.kwargs[0]["api_key_override"], "dedicated-test-key")
        self.assertEqual(model.kwargs[1]["model_names_override"], ["gpt-5.4-mini", "gpt-5.4"])
        self.assertNotIn("api_key_override", model.kwargs[1])
        self.assertNotIn("base_url_override", model.kwargs[1])

    def test_batch_decision_cannot_skip_the_first_group_in_its_evaluation_order(self) -> None:
        tasks = [_batch_task(str(index)) for index in range(1, 5)]
        error = _batch_decision_error(
            {
                "evaluations": [
                    {"task_id": "2", "decision": "send", "reason": "错误跳号", "evidence_refs": ["task:2"]}
                ],
                "selected_task_id": "2",
                "transition_text": "",
            },
            tasks=tasks,
        )

        self.assertEqual(error, "evaluations must follow pending task order without gaps")

    def test_batch_decision_stops_after_the_only_send_group(self) -> None:
        tasks = [_batch_task(str(index)) for index in range(1, 5)]
        error = _batch_decision_error(
            {
                "evaluations": [
                    {"task_id": "1", "decision": "skip", "reason": "已处理", "evidence_refs": ["task:1"]},
                    {"task_id": "2", "decision": "send", "reason": "仍适合", "evidence_refs": ["task:2"]},
                    {"task_id": "3", "decision": "send", "reason": "非法第二次发送", "evidence_refs": ["task:3"]},
                ],
                "selected_task_id": "2",
                "transition_text": "",
            },
            tasks=tasks,
        )

        self.assertEqual(error, "the first send decision must end evaluations")

    def test_batch_decision_allows_one_send_after_a_contiguous_skipped_prefix(self) -> None:
        tasks = [_batch_task(str(index)) for index in range(1, 5)]
        error = _batch_decision_error(
            {
                "evaluations": [
                    {"task_id": "1", "decision": "skip", "reason": "已处理", "evidence_refs": ["task:1"]},
                    {"task_id": "2", "decision": "skip", "reason": "已重复", "evidence_refs": ["task:2"]},
                    {"task_id": "3", "decision": "send", "reason": "仍适合", "evidence_refs": ["task:3"]},
                ],
                "selected_task_id": "3",
                "transition_text": "",
            },
            tasks=tasks,
        )

        self.assertEqual(error, "")

    async def test_selected_send_failure_does_not_consume_skipped_prefix(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "skip", "reason": "已处理", "evidence_refs": ["task:1"]},
                        {"task_id": "2", "decision": "send", "reason": "可发送", "evidence_refs": ["task:2"]},
                    ],
                    "selected_task_id": "2",
                    "transition_text": "",
                }
            ]
        )
        service, _repo, platform, system = _service(model=model)
        system.send = AsyncMock(side_effect=RuntimeError("send failed"))

        result = await service.process_customer_batch(
            _customer_batch(_batch_task("1"), _batch_task("2"), _batch_task("3"))
        )

        self.assertEqual(result["status"], "processing_retry")
        self.assertEqual(platform.consume_calls, [("2", 20)])

    async def test_batch_crossing_into_quiet_hours_is_archived_and_consumed_without_send(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "send", "reason": "still relevant", "evidence_refs": ["task:1"]}
                    ],
                    "selected_task_id": "1",
                    "transition_text": "",
                }
            ]
        )
        service, _repo, platform, system = _service(model=model)

        with patch(
            "app.services.sop_platform_task_service._quiet_hours_base_summary",
            side_effect=[
                {"in_quiet_hours": False, "window": "00:00-08:00"},
                {"in_quiet_hours": True, "window": "00:00-08:00"},
            ],
        ):
            result = await service.process_customer_batch(_customer_batch(_batch_task("1")))

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("1", 70)])
        self.assertEqual(system.send_calls, [])

    async def test_resolved_content_send_failure_keeps_compat_trigger_unconsumed(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "201", "decision": "send", "reason": "可发送", "evidence_refs": ["task:201"]}
                    ],
                    "selected_task_id": "201",
                    "transition_text": "",
                }
            ]
        )
        service, _repo, platform, system = _service(model=model)
        system.send = AsyncMock(side_effect=RuntimeError("send failed"))
        trigger = _batch_task("101", text="")
        trigger["message_content"] = []
        batch = _customer_batch(_batch_task("201", text="完整消息组"))
        batch["compat_trigger_tasks"] = [trigger]

        result = await service.process_customer_batch(batch)

        self.assertEqual(result["status"], "processing_retry")
        self.assertEqual(platform.consume_calls, [("201", 20)])
        self.assertNotIn(("101", 70), platform.consume_calls)

    async def test_batch_send_retry_reuses_exact_messages_and_consumes_prefix_after_success(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "skip", "reason": "already handled", "evidence_refs": ["task:1"]},
                        {"task_id": "2", "decision": "send", "reason": "still relevant", "evidence_refs": ["task:2"]},
                    ],
                    "selected_task_id": "2",
                    "transition_text": "",
                }
            ]
        )
        service, repo, platform, system = _service(model=model)
        original_send = system.send
        calls = 0

        async def flaky_send(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                system.send_calls.append(kwargs)
                raise RuntimeError("outreach_system_http_503: temporarily unavailable")
            return await original_send(**kwargs)

        system.send = flaky_send
        second = _batch_task("2", text="平台原始第二组")

        first = await service.process_customer_batch(_customer_batch(_batch_task("1"), second))
        recovered = await service.process_task(second, recovery_status="platform_batch_send_retry")

        self.assertEqual(first["status"], "processing_retry")
        self.assertEqual(recovered["status"], "sent")
        self.assertEqual(platform.consume_calls, [("2", 20), ("1", 70), ("2", 30)])
        self.assertEqual(system.send_calls[0]["reply_messages"], system.send_calls[1]["reply_messages"])
        self.assertEqual(
            system.send_calls[0]["delivery_idempotency_key"],
            system.send_calls[1]["delivery_idempotency_key"],
        )
        self.assertEqual(len(model.calls), 1)
        self.assertTrue(repo.tasks["platform-sop:2"]["send_payload"]["delivery_retry"]["resolved_at"])

    async def test_interrupted_batch_send_confirms_delivery_before_consuming_without_resend(self) -> None:
        service, repo, platform, system = _service(model=_Model([]))
        selected = _batch_task("2", text="already delivered")
        service._ensure_local_task(selected, status="platform_processing")
        local_task = repo.tasks["platform-sop:2"]
        audit = {
            "processing_mode": "customer_batch_sequence",
            "batch_run_id": "online_service:1",
            "batch_key": "online_service|ww_corp|dy258|wm_external",
            "biz_type": "online_service",
            "batch_task_ids": ["1", "2", "3"],
            "selected_task_id": "2",
            "skipped_prefix_task_ids": ["1"],
            "compat_trigger_task_ids": [],
            "final_messages": [_text("already delivered")],
            "consume_results": [],
        }
        repo.update_sop_send_task(local_task["id"], status="sending", send_payload=audit)
        repo.create_sop_event(
            {
                "event_id": "platform_sop_task:1",
                "event_type": "platform_sop_task",
                "platform_task": _batch_task("1"),
            }
        )
        repo.update_sop_event_status("platform_sop_task:1", status="platform_processing")
        system.conversation_payload = {
            "code": 0,
            "data": {
                "ai_auto_reply": True,
                "customer_relation": {"status": "active", "is_deleted": False},
                "messages": [
                    {
                        "direction": "assistant",
                        "msgtype": "text",
                        "content": "already delivered",
                        "msgid": "platform-message-2001",
                    }
                ],
            },
        }

        result = await service.process_task(selected, recovery_status="platform_processing")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["delivery_recovery"]["match_type"], "platform_task_content")
        self.assertEqual(system.send_calls, [])
        self.assertEqual(platform.consume_calls, [("1", 70), ("2", 30)])
        self.assertEqual(repo.tasks["platform-sop:2"]["status"], "sent")
        self.assertEqual(repo.events["platform_sop_task:2"]["status"], "platform_completed")

    async def test_completed_event_with_sending_task_recovers_from_dispatch_and_consumes(self) -> None:
        service, repo, platform, system = _service(model=_Model([]))
        selected = _batch_task("2", text="already accepted")
        service._ensure_local_task(selected, status="platform_processing")
        local_task = repo.tasks["platform-sop:2"]
        repo.update_sop_send_task(
            local_task["id"],
            status="sending",
            send_payload={
                "processing_mode": "customer_batch_sequence",
                "selected_task_id": "2",
                "final_messages": [_text("already accepted")],
                "skipped_prefix_task_ids": [],
                "consume_results": [],
            },
        )
        repo.update_sop_event_status("platform_sop_task:2", status="platform_completed")
        system.delivery_dispatch = lambda _key: {
            "id": "dispatch-2",
            "status": "platform_accepted",
            "system_msgid": "system-message-2",
        }
        system.conversation = AsyncMock(side_effect=AssertionError("dispatch evidence should be preferred"))

        result = await service.process_task(selected, recovery_status="platform_processing")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["delivery_recovery"]["status"], "confirmed_from_dispatch")
        self.assertEqual(platform.consume_calls, [("2", 30)])
        self.assertEqual(system.send_calls, [])
        self.assertEqual(repo.tasks["platform-sop:2"]["status"], "sent")

    async def test_interrupted_batch_send_waits_when_delivery_check_is_unavailable(self) -> None:
        service, repo, platform, system = _service(model=_Model([]))
        selected = _batch_task("2", text="immutable message")
        service._ensure_local_task(selected, status="platform_processing")
        local_task = repo.tasks["platform-sop:2"]
        repo.update_sop_send_task(
            local_task["id"],
            status="sending",
            send_payload={
                "processing_mode": "customer_batch_sequence",
                "selected_task_id": "2",
                "final_messages": [_text("immutable message")],
                "skipped_prefix_task_ids": [],
            },
        )
        system.conversation = AsyncMock(side_effect=RuntimeError("conversation unavailable"))

        result = await service.process_task(selected, recovery_status="platform_processing")

        self.assertEqual(result["status"], "recovery_waiting")
        self.assertEqual(system.send_calls, [])
        self.assertEqual(platform.consume_calls, [])
        self.assertEqual(repo.tasks["platform-sop:2"]["status"], "sending")

    async def test_delivery_callback_consumes_prefix_only_after_confirmed_success(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "skip", "reason": "已处理", "evidence_refs": ["task:1"]},
                        {"task_id": "2", "decision": "send", "reason": "可发送", "evidence_refs": ["task:2"]},
                    ],
                    "selected_task_id": "2",
                    "transition_text": "",
                }
            ]
        )
        service, repo, platform, system = _service(model=model)
        system.send_responses.append(
            {
                "code": 0,
                "data": {"callback_required": True, "delivery_status": "platform_accepted"},
            }
        )

        result = await service.process_customer_batch(_customer_batch(_batch_task("1"), _batch_task("2")))

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(platform.consume_calls, [("2", 20)])
        source_context = system.send_calls[0]["source_context"]
        await service.finalize_message_delivery(
            {
                "status": "send_succeeded",
                "source_context": source_context,
                "source_task_id": source_context["sop_send_task_id"],
            }
        )
        self.assertEqual(platform.consume_calls, [("2", 20), ("1", 70), ("2", 30)])
        self.assertEqual(repo.events["platform_sop_task:1"]["status"], "platform_completed")
        self.assertEqual(repo.events["platform_sop_task:2"]["status"], "platform_completed")
        audit = repo.tasks["platform-sop:2"]["send_payload"]
        self.assertEqual(audit["batch_run_id"], "online_service:1")
        self.assertEqual(
            [(item["task_id"], item["status"]) for item in audit["consume_results"]],
            [("1", 70), ("2", 30)],
        )

    async def test_success_callback_during_quiet_hours_defers_consume_without_resend(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "skip", "reason": "already handled", "evidence_refs": ["task:1"]},
                        {"task_id": "2", "decision": "send", "reason": "still relevant", "evidence_refs": ["task:2"]},
                    ],
                    "selected_task_id": "2",
                    "transition_text": "",
                }
            ]
        )
        settings = _settings()
        service, repo, platform, system = _service(model=model, settings=settings)
        system.send_responses.append(
            {
                "code": 0,
                "data": {"callback_required": True, "delivery_status": "platform_accepted"},
            }
        )
        first = _batch_task("1")
        second = _batch_task("2")
        await service.process_customer_batch(_customer_batch(first, second))
        source_context = system.send_calls[0]["source_context"]
        settings.sop_platform_quiet_hours_enabled = True
        settings.sop_platform_quiet_start_hour = 0
        settings.sop_platform_quiet_end_hour = 0

        await service.finalize_message_delivery(
            {
                "status": "send_succeeded",
                "source_context": source_context,
                "source_task_id": source_context["sop_send_task_id"],
            }
        )

        self.assertEqual(platform.consume_calls, [("2", 20)])
        self.assertEqual(repo.events["platform_sop_task:2"]["status"], "platform_batch_consume_pending")
        self.assertEqual(len(system.send_calls), 1)

        settings.sop_platform_quiet_hours_enabled = False
        result = await service.process_task(second, recovery_status="platform_batch_consume_pending")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(platform.consume_calls, [("2", 20), ("1", 70), ("2", 30)])
        self.assertEqual(len(system.send_calls), 1)

    async def test_failed_delivery_callback_keeps_prefix_reserved_for_exact_retry(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "skip", "reason": "已处理", "evidence_refs": ["task:1"]},
                        {"task_id": "2", "decision": "send", "reason": "可发送", "evidence_refs": ["task:2"]},
                    ],
                    "selected_task_id": "2",
                    "transition_text": "",
                }
            ]
        )
        service, repo, platform, system = _service(model=model)
        system.send_responses.append(
            {
                "code": 0,
                "data": {"callback_required": True, "delivery_status": "platform_accepted"},
            }
        )
        await service.process_customer_batch(_customer_batch(_batch_task("1"), _batch_task("2")))
        source_context = system.send_calls[0]["source_context"]

        await service.finalize_message_delivery(
            {
                "status": "send_failed",
                "error_message": "platform rejected",
                "source_context": source_context,
                "source_task_id": source_context["sop_send_task_id"],
            }
        )

        self.assertEqual(platform.consume_calls, [("2", 20)])
        self.assertEqual(repo.events["platform_sop_task:2"]["status"], "platform_batch_send_retry")
        self.assertIn("1", service._reserved_prefix_ids)
        self.assertIn("2", service._reserved_prefix_ids)

    async def test_transition_fact_audit_failure_drops_bridge_and_preserves_original(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "send", "reason": "可发送", "evidence_refs": ["task:1"]}
                    ],
                    "selected_task_id": "1",
                    "transition_text": "这次只要199元",
                },
                {"status": "fail", "reason": "加入新价格"},
            ]
        )
        service, _repo, _platform, system = _service(model=model)

        await service.process_customer_batch(_customer_batch(_batch_task("1", text="平台268元原文")))

        self.assertEqual(system.send_calls[0]["reply_messages"], [_text("平台268元原文")])

    async def test_all_filtered_groups_are_consumed_as_no_send(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "1", "decision": "skip", "reason": "已处理", "evidence_refs": ["task:1"]},
                        {"task_id": "2", "decision": "skip", "reason": "已处理", "evidence_refs": ["task:2"]},
                    ],
                    "selected_task_id": "",
                    "transition_text": "",
                }
            ]
        )
        service, _repo, platform, system = _service(model=model)

        result = await service.process_customer_batch(_customer_batch(_batch_task("1"), _batch_task("2")))

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("1", 70), ("2", 70)])
        self.assertEqual(system.send_calls, [])

    async def test_all_filtered_resolved_groups_also_consume_empty_trigger(self) -> None:
        model = _Model(
            [
                {
                    "evaluations": [
                        {"task_id": "201", "decision": "skip", "reason": "已处理", "evidence_refs": ["task:201"]}
                    ],
                    "selected_task_id": "",
                    "transition_text": "",
                }
            ]
        )
        service, _repo, platform, system = _service(model=model)
        trigger = _batch_task("101", text="")
        trigger["message_content"] = []
        batch = _customer_batch(_batch_task("201", text="完整消息组"))
        batch["compat_trigger_tasks"] = [trigger]

        result = await service.process_customer_batch(batch)

        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(platform.consume_calls, [("201", 70), ("101", 70)])
        self.assertEqual(system.send_calls, [])

    def test_batch_prompt_forbids_platform_message_rewrite(self) -> None:
        self.assertIn("不可修改的事实载体", SOP_PLATFORM_BATCH_SYSTEM_PROMPT)
        self.assertIn("遇到第一条 `send` 立即停止", SOP_PLATFORM_BATCH_SYSTEM_PROMPT)
        self.assertNotIn("合并两个", SOP_PLATFORM_BATCH_SYSTEM_PROMPT)

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


def _batch_task(
    task_id: str,
    *,
    text: str = "平台原文",
    trigger_event: str = "schedule",
    operate_time: str = "2026-08-20 10:00:00",
) -> dict[str, Any]:
    numeric_id = int(task_id)
    return {
        **_task(message_content=[{"type": "text", "content": text}]),
        "task_id": numeric_id,
        "triggerEvent": trigger_event,
        "operateTime": operate_time,
        "scheduledAt": f"2026-08-20 10:{numeric_id:02d}:00",
        "sortOrder": numeric_id,
        "runId": "run-1",
        "_aics_biz_type": "online_service",
    }


def _customer_batch(*tasks: dict[str, Any]) -> dict[str, Any]:
    return {
        "_aics_customer_batch": True,
        "batch_key": "online_service|ww_corp|dy258|wm_external",
        "biz_type": "online_service",
        "tasks": list(tasks),
    }


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
        sop_platform_decision_model="",
        sop_platform_decision_model_fallbacks="",
        sop_platform_decision_api_key="",
        sop_platform_decision_base_url="",
        sop_platform_decision_primary_timeout_seconds=12,
        sop_platform_max_task_age_seconds=21600,
        sop_platform_live_not_before="",
        sop_platform_quiet_hours_enabled=quiet_hours_enabled,
        sop_platform_quiet_start_hour=0,
        sop_platform_quiet_end_hour=8,
        sop_platform_deferred_replay_enabled=True,
        sop_platform_deferred_replay_interval_seconds=600,
        sop_platform_deferred_replay_concurrency=6,
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

    def get_sop_send_task(self, task_id):
        return dict(next((item for item in self.tasks.values() if item["id"] == task_id), {}))

    def list_deferred_platform_sop_tasks(self, *, start_at, end_at, limit=5000):
        del start_at, end_at, limit
        records = []
        for task in self.tasks.values():
            event = self.events.get(task.get("event_id"), {})
            raw = event.get("raw_payload") if isinstance(event.get("raw_payload"), dict) else {}
            platform_task = raw.get("platform_task") if isinstance(raw.get("platform_task"), dict) else {}
            records.append(
                {
                    "event_id": task.get("event_id"),
                    "local_task_id": task.get("id"),
                    "task_status": task.get("status"),
                    "send_payload": task.get("send_payload") or {},
                    "send_response": task.get("send_response") or {},
                    "sent_at": task.get("sent_at") or "",
                    "platform_task": platform_task,
                }
            )
        return records

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

        self.consume_details: list[dict[str, Any]] = []

    async def consume(self, *, task_id, status, remark="", content_exhausted=None):
        self.consume_calls.append((str(task_id), status))
        self.consume_details.append(
            {
                "task_id": str(task_id),
                "status": status,
                "remark": remark,
                "content_exhausted": content_exhausted,
            }
        )
        if self.consume_responses:
            return self.consume_responses.pop(0)
        return {"code": 200, "data": {"task_id": task_id, "status": status}}

    async def pending(self, *, limit=None):
        return {"items": [], "total": 0, "limit": limit}

    async def store_visit_pending(self, *, limit=None):
        return {"items": [], "total": 0, "limit": limit, "biz_type": "store_visit"}


class _System:
    def __init__(self):
        self.send_calls: list[dict[str, Any]] = []
        self.send_responses: list[dict[str, Any]] = []
        self.conversation_calls = 0
        self.conversation_limits: list[int | None] = []
        self.conversation_payload = {
            "code": 0,
            "data": {
                "ai_auto_reply": True,
                "customer_relation": {"status": "active", "is_deleted": False},
                "messages": [{"direction": "customer", "content": "你好"}],
            },
        }

    async def conversation_status(self, **_kwargs):
        ai_auto_reply = self.conversation_payload["data"].get("ai_auto_reply")
        return {
            "code": 0,
            "data": {
                "takeover": {
                    "mode": "ai" if ai_auto_reply else "human",
                    "ai_auto_reply": ai_auto_reply,
                }
            },
        }

    async def conversation(self, **_kwargs):
        self.conversation_calls += 1
        self.conversation_limits.append(_kwargs.get("limit"))
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
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


class _HttpResponse:
    def __init__(self, *, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self):
        raise ValueError("not json")
