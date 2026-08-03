from __future__ import annotations

import unittest
from unittest.mock import AsyncMock
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

from app.services.sop_platform_client import SopPlatformClient
from app.services.sop_platform_task_service import SopPlatformTaskService
from app.services.sop_reply_pack_service import SopReplyPackService


class SopPlatformTaskFlowTests(unittest.IsolatedAsyncioTestCase):
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

        await client.pending()

        payload = client._request.await_args.kwargs["json_body"]  # type: ignore[union-attr]
        self.assertIsInstance(payload["start_time"], int)
        self.assertIsInstance(payload["end_time"], int)
        self.assertEqual(payload["limit"], 500)

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


def _service(*, model: Any, shadow_mode: bool = False):
    repo = _Repo()
    platform = _Platform()
    system = _System()
    service = SopPlatformTaskService(
        settings=_settings(shadow_mode=shadow_mode),
        repository=repo,
        platform_client=platform,
        system_client=system,
        model_client=model,
        customer_context_service=_CustomerContext(),
    )
    return service, repo, platform, system


def _settings(*, shadow_mode: bool = False):
    return SimpleNamespace(
        sop_platform_token="token",
        sop_platform_base_url="http://example.invalid",
        sop_platform_timeout_seconds=2,
        sop_platform_lookback_seconds=300,
        sop_platform_window_seconds=60,
        sop_platform_batch_size=20,
        sop_platform_recovery_batch_size=10,
        sop_platform_shadow_mode=shadow_mode,
        sop_platform_model_timeout_seconds=10,
    )


def _task(*, use_ai_copy: bool = False, message_content: list[dict[str, Any]] | None = None):
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

    async def consume(self, *, task_id, status):
        self.consume_calls.append((str(task_id), status))
        if self.consume_responses:
            return self.consume_responses.pop(0)
        return {"code": 200, "data": {"task_id": task_id, "status": status}}

    async def pending(self):
        return []


class _System:
    def __init__(self):
        self.send_calls: list[dict[str, Any]] = []
        self.conversation_calls = 0
        self.conversation_payload = {
            "code": 0,
            "data": {
                "customer_relation": {"status": "active", "is_deleted": False},
                "messages": [{"direction": "customer", "content": "你好"}],
            },
        }

    async def conversation(self, **_kwargs):
        self.conversation_calls += 1
        return self.conversation_payload

    async def send(self, **kwargs):
        self.send_calls.append(kwargs)
        return {"code": 0, "data": {"send_status": "sent"}}


class _CustomerContext:
    def load(self, **_kwargs):
        return {"source": "test", "orders": [], "appointment": {}}


class _Model:
    def __init__(self, outputs: list[dict[str, Any]]):
        self.outputs = list(outputs)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_json(self, messages, **_kwargs):
        self.calls.append(messages)
        if not self.outputs:
            raise AssertionError("unexpected model call")
        return self.outputs.pop(0)


class _HttpResponse:
    def __init__(self, *, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self):
        raise ValueError("not json")
