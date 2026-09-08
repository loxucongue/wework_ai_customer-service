from __future__ import annotations

import asyncio
from collections import Counter, deque
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.services import sop_platform_task_service as sop_module  # noqa: E402
from app.services.sop_platform_task_service import (  # noqa: E402
    SopPlatformTaskService,
    _conversation_timeline,
    _first_add_customer_reply_guard,
)


def _task(**overrides: object) -> dict[str, object]:
    task: dict[str, object] = {
        "taskId": "task-1",
        "triggerEvent": "add_wecom",
        "operateTime": "2026-09-08T11:24:00+08:00",
        "scheduledAt": "2026-09-08T14:24:00+08:00",
        "corp_id": "corp-1",
        "customer_id": "customer-1",
        "external_userid": "external-1",
        "user_id": "staff-1",
        "wechat": "staff-1",
        "message_content": [{"type": "text", "content": "固定 SOP"}],
    }
    task.update(overrides)
    return task


def _timeline(*messages: dict[str, object]) -> list[dict[str, object]]:
    return _conversation_timeline(list(messages))


def test_first_add_reply_after_add_is_hard_blocked() -> None:
    guard = _first_add_customer_reply_guard(
        _task(),
        timeline=_timeline(
            {
                "direction": "customer",
                "content": "效果真的这么好吗",
                "msgtime": "2026-09-08T12:53:36+08:00",
            }
        ),
    )

    assert guard["blocked"] is True
    assert guard["reason"] == "customer_replied_after_add"
    assert guard["reply_time_confirmed"] is True


def test_first_add_automatic_opening_and_pre_add_history_do_not_block() -> None:
    guard = _first_add_customer_reply_guard(
        _task(),
        timeline=_timeline(
            {
                "direction": "customer",
                "content": "我已经添加了你，现在我们可以开始聊天了",
                "msgtime": "2026-09-08T11:24:01+08:00",
            },
            {
                "direction": "customer",
                "content": "旧会话",
                "msgtime": "2026-09-07T10:00:00+08:00",
            },
        ),
    )

    assert guard["blocked"] is False


def test_first_add_reply_with_unreliable_time_is_conservatively_blocked() -> None:
    guard = _first_add_customer_reply_guard(
        _task(operateTime="", createTime=""),
        timeline=_timeline({"direction": "customer", "content": "在吗"}),
    )

    assert guard["blocked"] is True
    assert guard["add_time_confirmed"] is False
    assert guard["reply_time_confirmed"] is False


def test_non_first_add_task_is_left_to_normal_decision_logic() -> None:
    guard = _first_add_customer_reply_guard(
        _task(triggerEvent="scheduled_followup"),
        timeline=_timeline({"direction": "customer", "content": "在吗"}),
    )

    assert guard == {}


def test_batch_opened_customer_never_reaches_model_or_send(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.settings = SimpleNamespace()
    service.system_client = SimpleNamespace()
    service._counters = Counter()
    service._timings = {name: deque(maxlen=5) for name in ("context", "model")}
    service._find_earlier_unresolved_sequence_task = lambda *_args, **_kwargs: {}
    service._ensure_local_task = lambda task, **_kwargs: ({}, {"id": f"local-{task['taskId']}"})
    service._log_task_phase = lambda **_kwargs: None

    async def conversation_status(**_kwargs: object) -> dict[str, object]:
        return {"data": {"takeover": {"ai_auto_reply": True}}}

    async def conversation(**_kwargs: object) -> dict[str, object]:
        return {
            "data": {
                "customer_relation": {"status": "active", "is_deleted": False},
                "messages": [
                    {
                        "direction": "customer",
                        "content": "后面会反弹吗",
                        "msgtime": "2026-09-08T12:55:41+08:00",
                    }
                ],
            }
        }

    service.system_client.conversation_status = conversation_status
    service.system_client.conversation = conversation
    model_called = False

    async def decide(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal model_called
        model_called = True
        return {"selected_task_id": "task-1"}

    service._decide_customer_batch = decide
    captured: dict[str, object] = {}

    async def block(tasks: list[dict[str, object]], **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"processed": False, "status": "send_failed", "task_id": tasks[0]["taskId"]}

    service._consume_batch_without_send = block
    monkeypatch.setattr(sop_module, "_platform_task_is_stale", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(sop_module, "_in_configured_quiet_hours", lambda **_kwargs: False)
    monkeypatch.setattr(sop_module, "_quiet_hours_base_summary", lambda *_args, **_kwargs: {})

    result = asyncio.run(
        service._process_customer_batch_locked(
            [_task()],
            trigger_tasks=[],
            batch_key="contact-1",
            biz_type="online_service",
        )
    )

    assert result["status"] == "send_failed"
    assert captured["reason"] == "customer_already_opened"
    assert model_called is False


def test_send_path_requires_selected_sop_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Repository:
        def get_sop_send_task_by_idempotency_key(self, _key: str) -> dict[str, object]:
            return {}

    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.settings = SimpleNamespace(sop_platform_shadow_mode=False)
    service.repository = _Repository()
    service._counters = Counter()
    send_called = False

    async def block(tasks: list[dict[str, object]], **kwargs: object) -> dict[str, object]:
        return {
            "processed": False,
            "status": "send_failed",
            "task_id": tasks[0]["taskId"],
            "reason": kwargs["reason"],
        }

    async def send(**_kwargs: object) -> dict[str, object]:
        nonlocal send_called
        send_called = True
        return {}

    service._consume_batch_without_send = block
    service.system_client = SimpleNamespace(send=send)
    monkeypatch.setattr(sop_module, "_in_configured_quiet_hours", lambda **_kwargs: False)
    monkeypatch.setattr(sop_module, "_quiet_hours_base_summary", lambda *_args, **_kwargs: {})

    result = asyncio.run(
        service._send_selected_batch_task(
            _task(),
            skipped_prefix=[],
            trigger_tasks=[],
            transition_text="",
            decision={"selected_task_id": "task-1", "evaluations": []},
            context={},
            identity={
                "corp_id": "corp-1",
                "customer_id": "customer-1",
                "external_userid": "external-1",
                "user_id": "staff-1",
                "wechat": "staff-1",
            },
            batch_key="contact-1",
            biz_type="online_service",
            batch_run_id="run-1",
            batch_task_ids=["task-1"],
        )
    )

    assert result["reason"] == "missing_sop_message_id"
    assert send_called is False


def test_legacy_recovery_path_blocks_opened_customer_without_model_or_send() -> None:
    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.settings = SimpleNamespace(sop_platform_shadow_mode=False)
    service._counters = Counter()
    service._timings = {name: deque(maxlen=5) for name in ("context", "model")}
    model_called = False
    send_called = False

    async def load_context(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "customer_relation": {"status": "active"},
            "conversation_timeline": _timeline(
                {
                    "direction": "customer",
                    "content": "效果真的这么好吗",
                    "msgtime": "2026-09-08T12:53:36+08:00",
                }
            ),
        }

    async def decide(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal model_called
        model_called = True
        return {"decision": "send", "reply_messages": []}

    async def defer(tasks: list[dict[str, object]], **kwargs: object) -> dict[str, object]:
        return {
            "processed": False,
            "status": "send_failed",
            "task_id": tasks[0]["taskId"],
            "reason": kwargs["failure_reason"],
        }

    async def send(**_kwargs: object) -> dict[str, object]:
        nonlocal send_called
        send_called = True
        return {}

    service._load_context = load_context
    service._decide = decide
    service._defer_sequence_blocked = defer
    service.system_client = SimpleNamespace(send=send)

    result = asyncio.run(
        service._execute_platform_task(
            {
                "platform_task": _task(),
                "task_id": "task-1",
                "event_id": "platform_sop_task:task-1",
                "identity": {},
                "local_task": {"id": "local-1"},
                "preflight_reason": "",
                "quiet_hours": {},
            }
        )
    )

    assert result["reason"] == "customer_replied_after_add"
    assert model_called is False
    assert send_called is False


def test_first_add_presend_guard_blocks_missing_messages() -> None:
    async def conversation(**_kwargs: object) -> dict[str, object]:
        return {"data": {"customer_relation": {"status": "active"}}}

    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.system_client = SimpleNamespace(conversation=conversation)

    guard = asyncio.run(
        service._load_first_add_send_guard(
            _task(),
            identity={
                "corp_id": "corp-1",
                "customer_id": "customer-1",
                "external_userid": "external-1",
                "user_id": "staff-1",
                "wechat": "staff-1",
            },
        )
    )

    assert guard["blocked"] is True
    assert guard["reason"] == "first_add_conversation_invalid"
    assert guard["error"] == "messages_missing"


def test_batch_retry_rechecks_conversation_before_resending(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.settings = SimpleNamespace()
    service._counters = Counter()
    send_called = False

    async def guard(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"required": True, "blocked": True, "reason": "customer_replied_after_add"}

    async def defer(tasks: list[dict[str, object]], **kwargs: object) -> dict[str, object]:
        return {
            "processed": False,
            "status": "send_failed",
            "task_id": tasks[0]["taskId"],
            "reason": kwargs["failure_reason"],
        }

    async def send(**_kwargs: object) -> dict[str, object]:
        nonlocal send_called
        send_called = True
        return {}

    service._load_first_add_send_guard = guard
    service._defer_sequence_blocked = defer
    service.system_client = SimpleNamespace(send=send)
    monkeypatch.setattr(sop_module, "_delivery_retry_expired", lambda *_args, **_kwargs: False)

    result = asyncio.run(
        service._retry_batch_send(
            _task(),
            local_task={
                "id": "local-1",
                "send_payload": {
                    "final_messages": [{"type": "text", "content": {"text": "固定 SOP"}}],
                    "batch_key": "contact-1",
                    "batch_run_id": "run-1",
                    "biz_type": "online_service",
                },
            },
        )
    )

    assert result["reason"] == "customer_replied_after_add"
    assert send_called is False


def test_manual_resend_cannot_bypass_first_add_reply_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Repository:
        @staticmethod
        def get_sop_event(_event_id: str) -> dict[str, object]:
            return {
                "status": "platform_sequence_blocked",
                "raw_payload": {"platform_task": _task()},
            }

        @staticmethod
        def get_sop_send_task_by_idempotency_key(_key: str) -> dict[str, object]:
            return {"id": "local-1", "status": "send_failed", "send_payload": {}}

    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.repository = _Repository()
    service.settings = SimpleNamespace()
    service._find_earlier_unresolved_sequence_task = lambda *_args, **_kwargs: {}

    async def blocked_guard(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"required": True, "blocked": True, "reason": "customer_replied_after_add"}

    service._load_first_add_send_guard = blocked_guard
    monkeypatch.setattr(sop_module, "_task_preflight_no_send_reason", lambda *_args, **_kwargs: "")

    with pytest.raises(RuntimeError, match="customer_replied_after_add"):
        asyncio.run(service._admin_resend_task_locked("task-1"))


def test_queued_recovery_reenters_batch_path(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task()

    class _Repository:
        def list_sop_events_by_statuses(self, *_args: object, **_kwargs: object) -> list[dict[str, object]]:
            return [
                {
                    "event_id": "platform_sop_task:task-1",
                    "status": "platform_queued",
                    "raw_payload": {"platform_task": task},
                    "retry_count": 0,
                }
            ]

        def list_orphaned_platform_sop_events(self, **_kwargs: object) -> list[dict[str, object]]:
            return []

    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.settings = SimpleNamespace(sop_platform_recovery_batch_size=10, sop_platform_recovery_concurrency=1)
    service.repository = _Repository()
    service.failure_alert_service = None
    batch_called = False
    legacy_called = False

    async def process_batch(batch: dict[str, object]) -> dict[str, object]:
        nonlocal batch_called
        batch_called = True
        assert batch["tasks"] == [task]
        return {"processed": True, "status": "sent", "task_id": "task-1"}

    async def process_legacy(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal legacy_called
        legacy_called = True
        return {"processed": True, "status": "sent", "task_id": "task-1"}

    async def alert(*_args: object, **_kwargs: object) -> None:
        return None

    service.process_customer_batch = process_batch
    service.process_task = process_legacy
    service._record_result = lambda _result: None
    service._alert_result = alert
    monkeypatch.setattr(sop_module, "_in_configured_quiet_hours", lambda **_kwargs: False)

    recovered = asyncio.run(service.process_recoveries())

    assert recovered == 1
    assert batch_called is True
    assert legacy_called is False
