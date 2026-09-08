from __future__ import annotations

import asyncio
from collections import Counter, deque
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.sop_platform_task_service import SopPlatformTaskService


def _task() -> dict[str, Any]:
    return {
        "taskId": "101",
        "eventLogId": "9001",
        "corp_id": "corp",
        "customer_id": "customer",
        "external_userid": "external",
        "user_id": "staff",
        "wechat": "staff",
        "scheduledAt": "2026-09-08T15:00:00+08:00",
    }


class _Repository:
    def __init__(self, task: dict[str, Any]) -> None:
        self.task = task
        self.local = {"id": "local-101", "status": "platform_queued", "send_payload": {}}
        self.task_updates: list[dict[str, Any]] = []
        self.event_updates: list[dict[str, Any]] = []

    def get_sop_send_task_by_idempotency_key(self, _key: str) -> dict[str, Any]:
        return dict(self.local)

    def prepare_platform_sop_send(self, **values: Any) -> dict[str, Any]:
        self.local.update({"status": "sending", "send_payload": values["send_payload"]})
        return dict(self.local)

    def update_sop_send_task(self, _task_id: str, **values: Any) -> dict[str, Any]:
        self.local.update(values)
        self.task_updates.append(dict(values))
        return dict(self.local)

    def update_sop_event_status(self, event_id: str, **values: Any) -> None:
        self.event_updates.append({"event_id": event_id, **values})

    def get_sop_event(self, _event_id: str) -> dict[str, Any]:
        return {"raw_payload": {"platform_task": self.task}}


class _Platform:
    def __init__(self, events: list[str], *, empty: bool = False) -> None:
        self.events = events
        self.empty = empty
        self.consume_calls: list[dict[str, Any]] = []
        self.rule_calls: list[dict[str, Any]] = []
        self.fail_consume_once = False

    async def sop_messages(self, **_values: Any) -> dict[str, Any]:
        self.events.append("sop_messages")
        if self.empty:
            return {"items": [], "next_item": None, "total": 0}
        group = {
            "id": 701,
            "message_content": [
                {"type": "text", "content": "第一组文本"},
                {"type": "image", "content": "https://example.com/a.png"},
            ],
        }
        already_consumed = {
            "id": 700,
            "status": 30,
            "message_content": [{"type": "text", "content": "已发生内容不得重发"}],
        }
        return {"items": [already_consumed, group], "next_item": group, "total": 2}

    async def consume(self, **values: Any) -> dict[str, Any]:
        self.consume_calls.append(values)
        if self.fail_consume_once:
            self.fail_consume_once = False
            raise TimeoutError("consume timeout")
        return {"code": 200, "data": {"status": values["status"]}}

    async def service_rule_data(self, **values: Any) -> dict[str, Any]:
        self.rule_calls.append(values)
        return {"code": 200, "data": {}}


class _System:
    def __init__(
        self,
        events: list[str],
        *,
        opened: bool = False,
        deleted: bool = False,
        ai_auto_reply: bool = True,
        send_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.opened = opened
        self.deleted = deleted
        self.ai_auto_reply = ai_auto_reply
        self.send_error = send_error
        self.send_calls: list[dict[str, Any]] = []

    async def conversation_status(self, **_values: Any) -> dict[str, Any]:
        self.events.append("conversation_status")
        return {"data": {"takeover": {"ai_auto_reply": self.ai_auto_reply}}}

    async def conversation(self, **_values: Any) -> dict[str, Any]:
        self.events.append("conversation")
        messages = [{"direction": "customer", "content": "你好"}] if self.opened else []
        return {
            "data": {
                "customer_relation": {
                    "status": "deleted" if self.deleted else "active",
                    "is_deleted": self.deleted,
                },
                "messages": messages,
            }
        }

    async def send(self, **values: Any) -> dict[str, Any]:
        self.events.append("send")
        self.send_calls.append(values)
        if self.send_error is not None:
            raise self.send_error
        return {"data": {"delivery_status": "submission_unknown", "callback_required": True}}


class _NoModel:
    async def chat_json(self, *_args: Any, **_values: Any) -> dict[str, Any]:
        raise AssertionError("deterministic SOP path must not call a model")


def _service(
    *,
    opened: bool = False,
    deleted: bool = False,
    ai_auto_reply: bool = True,
    empty_content: bool = False,
    send_error: Exception | None = None,
) -> tuple[SopPlatformTaskService, _Repository, _Platform, _System, list[str]]:
    events: list[str] = []
    task = _task()
    repository = _Repository(task)
    platform = _Platform(events, empty=empty_content)
    system = _System(
        events,
        opened=opened,
        deleted=deleted,
        ai_auto_reply=ai_auto_reply,
        send_error=send_error,
    )
    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.settings = SimpleNamespace(sop_platform_batch_size=50, sop_platform_shadow_mode=False)
    service.repository = repository
    service.platform_client = platform
    service.system_client = system
    service.model_client = _NoModel()
    service._counters = Counter()
    service._timings = {name: deque(maxlen=20) for name in ("send", "consume", "rule_data")}
    service._reserved_prefix_ids = set()
    service._terminal_ids = set()
    service._terminal_order = deque()
    service._locks = {}
    service._ensure_local_task = lambda _task, **_kwargs: ({}, dict(repository.local))
    service._log_task_phase = lambda **_kwargs: None
    return service, repository, platform, system, events


def _run(service: SopPlatformTaskService) -> dict[str, Any]:
    return asyncio.run(
        service._process_customer_batch_locked(
            [_task()],
            trigger_tasks=[],
            batch_key="online_service|corp|staff|external",
            biz_type="online_service",
        )
    )


def test_three_gates_pass_sends_first_group_without_model_and_consumes_exact_msg_id() -> None:
    service, repository, platform, system, events = _service()

    result = _run(service)

    assert result["status"] == "sent"
    assert events.index("sop_messages") > events.index("conversation_status")
    assert events.index("sop_messages") > events.index("conversation")
    assert events.index("send") > events.index("sop_messages")
    assert len(system.send_calls) == 1
    assert system.send_calls[0]["reply_messages"] == [
        {"type": "text", "order": 1, "content": {"text": "第一组文本"}},
        {"type": "image", "order": 2, "content": {"url": "https://example.com/a.png"}},
    ]
    assert system.send_calls[0]["delivery_idempotency_key"] == "sop_platform_message:701"
    assert len(platform.consume_calls) == 1
    assert platform.consume_calls[0]["task_id"] == "101"
    assert platform.consume_calls[0]["status"] == 30
    assert platform.consume_calls[0]["messages"] == [{"msgId": "701", "status": 30, "remark": ""}]
    assert platform.consume_calls[0]["content_exhausted"] is None
    assert repository.local["status"] == "sent"
    assert repository.event_updates[-1]["status"] == "platform_completed"
    assert len(platform.rule_calls) == 1


@pytest.mark.parametrize(
    ("opened", "deleted", "ai_auto_reply", "reason"),
    [
        (True, False, True, "customer_already_opened"),
        (False, True, True, "customer_relation_deleted"),
        (False, False, False, "human_takeover"),
    ],
)
def test_any_failed_customer_gate_consumes_task_70_without_loading_or_consuming_content(
    opened: bool,
    deleted: bool,
    ai_auto_reply: bool,
    reason: str,
) -> None:
    service, _repository, platform, system, events = _service(
        opened=opened,
        deleted=deleted,
        ai_auto_reply=ai_auto_reply,
    )

    result = _run(service)

    assert result["reason"] == reason
    assert "sop_messages" not in events
    assert system.send_calls == []
    assert [(call["status"], call.get("messages")) for call in platform.consume_calls] == [(70, None)]
    assert len(platform.rule_calls) == 1


def test_empty_content_and_send_exception_consume_only_task_70() -> None:
    empty_service, _repository, empty_platform, empty_system, _events = _service(empty_content=True)
    empty_result = _run(empty_service)

    assert empty_result["reason"] == "sop_messages_empty"
    assert empty_system.send_calls == []
    assert [(call["status"], call.get("messages")) for call in empty_platform.consume_calls] == [(70, None)]

    failed_service, _repository, failed_platform, failed_system, _events = _service(
        send_error=RuntimeError("send rejected")
    )
    failed_result = _run(failed_service)

    assert failed_result["status"] == "completed_without_send"
    assert len(failed_system.send_calls) == 1
    assert [(call["status"], call.get("messages")) for call in failed_platform.consume_calls] == [(70, None)]


def test_polling_does_not_load_sop_messages_before_customer_gates() -> None:
    events: list[str] = []

    class _PollPlatform:
        async def pending(self, **_values: Any) -> dict[str, Any]:
            return {"items": [_task()], "total": 1}

        async def sop_messages(self, **_values: Any) -> dict[str, Any]:
            events.append("sop_messages")
            raise AssertionError("polling must not load content")

    settings = SimpleNamespace(
        sop_platform_queue_size=10,
        sop_platform_batch_size=10,
        sop_platform_priority_wechats="",
        sop_platform_task_concurrency=1,
    )
    service = SopPlatformTaskService(
        settings=settings,
        repository=SimpleNamespace(),
        platform_client=_PollPlatform(),
        system_client=SimpleNamespace(),
        model_client=_NoModel(),
        customer_context_service=SimpleNamespace(),
    )
    service._restore_reserved_prefix_ids = lambda: None
    service._ensure_local_task = lambda task, **_kwargs: ({}, {"id": f"local-{task['taskId']}"})

    result = asyncio.run(service.poll_once())

    assert result["enqueued_count"] == 1
    assert events == []


def test_consume_retry_reuses_exact_msg_id_without_resending_customer_message() -> None:
    service, repository, platform, system, _events = _service()
    platform.fail_consume_once = True

    with pytest.raises(TimeoutError, match="consume timeout"):
        _run(service)

    assert len(system.send_calls) == 1
    assert repository.event_updates[-1]["status"] == "platform_complete_pending"
    audit = repository.local["send_payload"]
    assert len(audit["consume_results"]) == 1
    assert audit["consume_results"][0]["success"] is False
    assert audit["consume_results"][0]["messages"] == [{"msgId": "701", "status": 30, "remark": ""}]

    terminal_ids = asyncio.run(
        service._finalize_batch_prefix(
            selected_task_id="101",
            skipped_prefix_task_ids=[],
            audit=audit,
        )
    )

    assert terminal_ids == ["101"]
    assert len(system.send_calls) == 1
    assert len(repository.local["send_payload"]["consume_results"]) == 2
    assert repository.local["send_payload"]["consume_results"][-1]["success"] is True
    assert [call["messages"] for call in platform.consume_calls] == [
        [{"msgId": "701", "status": 30, "remark": ""}],
        [{"msgId": "701", "status": 30, "remark": ""}],
    ]


def test_single_task_entry_uses_deterministic_flow_and_legacy_recovery_is_quarantined() -> None:
    service, _repository, platform, system, _events = _service()

    result = asyncio.run(service.process_task(_task()))

    assert result["status"] == "sent"
    assert len(system.send_calls) == 1
    assert platform.consume_calls[-1]["messages"] == [{"msgId": "701", "status": 30, "remark": ""}]

    legacy_service, legacy_repository, legacy_platform, legacy_system, legacy_events = _service()
    legacy_repository.local["send_payload"] = {"processing_mode": "customer_batch_sequence"}

    legacy_result = asyncio.run(legacy_service.process_task(_task(), recovery_status="platform_processing"))

    assert legacy_result["reason"] == "legacy_execution_disabled"
    assert legacy_result["status"] == "legacy_recovery_quarantined"
    assert legacy_result["processed"] is False
    assert legacy_system.send_calls == []
    assert "sop_messages" not in legacy_events
    assert legacy_platform.consume_calls == []
    assert legacy_repository.event_updates[-1] == {
        "event_id": "platform_sop_task:101",
        "status": "platform_legacy_quarantined",
        "error": "legacy_execution_disabled",
    }


def test_manual_resend_is_disabled_to_prevent_implicit_message_consumption() -> None:
    service, _repository, _platform, _system, _events = _service()

    with pytest.raises(RuntimeError, match="manual resend is disabled"):
        asyncio.run(service.admin_resend_task("101"))
