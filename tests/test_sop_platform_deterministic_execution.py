from __future__ import annotations

import asyncio
import threading
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

    def update_sop_send_task(self, _task_id: str, *, status: str, **values: Any) -> dict[str, Any]:
        self.local.update({"status": status, **values})
        self.task_updates.append({"status": status, **values})
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
        gate_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.opened = opened
        self.deleted = deleted
        self.ai_auto_reply = ai_auto_reply
        self.send_error = send_error
        self.gate_error = gate_error
        self.send_calls: list[dict[str, Any]] = []

    async def conversation_status(self, **_values: Any) -> dict[str, Any]:
        self.events.append("conversation_status")
        if self.gate_error is not None:
            raise self.gate_error
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
    gate_error: Exception | None = None,
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
        gate_error=gate_error,
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


def test_content_failure_consumes_task_70_without_consuming_msg_id() -> None:
    empty_service, repository, empty_platform, empty_system, _events = _service(empty_content=True)
    empty_result = _run(empty_service)

    assert empty_result["reason"] == "sop_messages_empty"
    assert empty_result["status"] == "failed_consumed"
    assert empty_system.send_calls == []
    assert [(call["status"], call.get("messages")) for call in empty_platform.consume_calls] == [(70, None)]
    assert len(empty_platform.rule_calls) == 1
    assert repository.local["status"] == "failed_consumed"
    assert repository.local["send_payload"]["terminal_failure"]["task_consumed"] is True
    assert repository.local["send_payload"]["terminal_failure"]["message_ids_consumed"] == []


def test_pre_send_gate_failure_retries_twice_then_consumes_task_without_msg_id() -> None:
    service, repository, platform, system, events = _service(gate_error=TimeoutError("gate timeout"))
    retry_count = 0

    def ensure_local(_task: dict[str, Any], **_values: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return {"retry_count": retry_count}, dict(repository.local)

    service._ensure_local_task = ensure_local

    first = _run(service)
    assert first["status"] == "send_failed"
    assert first["retry_scheduled"] is True
    assert platform.consume_calls == []

    retry_count = 1
    second = _run(service)
    assert second["status"] == "send_failed"
    assert second["retry_scheduled"] is True
    assert platform.consume_calls == []

    retry_count = 2
    third = _run(service)
    assert third["status"] == "failed_consumed"
    assert third["reason"] == (
        "customer_gate_query_failed:"
        "interface=conversation_status;phase=timeout;http_status=none;error=TimeoutError"
    )
    assert system.send_calls == []
    assert "sop_messages" not in events
    assert [(call["status"], call.get("messages")) for call in platform.consume_calls] == [(70, None)]
    assert len(platform.rule_calls) == 1
    assert repository.local["send_payload"]["decision"]["pre_send_retry"] == {
        "attempt_count": 3,
        "max_attempts": 3,
        "exhausted": True,
    }
    assert repository.local["send_payload"]["terminal_failure"]["message_ids_consumed"] == []


def test_existing_pre_send_failure_beyond_retry_cap_is_closed_immediately() -> None:
    service, repository, platform, system, _events = _service(gate_error=RuntimeError("gate unavailable"))
    service._ensure_local_task = lambda _task, **_values: (
        {"retry_count": 326},
        dict(repository.local),
    )

    result = _run(service)

    assert result["status"] == "failed_consumed"
    assert result["reason"] == (
        "customer_gate_query_failed:"
        "interface=conversation_status;phase=request;http_status=none;error=RuntimeError"
    )
    assert system.send_calls == []
    assert [(call["status"], call.get("messages")) for call in platform.consume_calls] == [(70, None)]
    assert repository.local["status"] == "failed_consumed"


@pytest.mark.parametrize(
    "reason",
    [
        "missing_identity:corp_id",
        "missing_event_log_id",
        "invalid_conversation_status",
        "missing_ai_auto_reply",
        "invalid_conversation",
        "missing_customer_relation",
        "missing_conversation_messages",
        "sop_messages_failed:ConnectTimeout",
    ],
)
def test_all_pre_send_failures_close_after_retry_cap(reason: str) -> None:
    service, repository, platform, system, _events = _service()
    service._ensure_local_task = lambda _task, **_values: (
        {"retry_count": 2},
        dict(repository.local),
    )

    result = asyncio.run(
        service._defer_batch_failure(
            [_task()],
            reason=reason,
            batch_key="online_service|corp|staff|external",
            biz_type="online_service",
            batch_run_id="online_service:101",
        )
    )

    assert result["status"] == "failed_consumed"
    assert result["reason"] == reason
    assert system.send_calls == []
    assert [(call["status"], call.get("messages")) for call in platform.consume_calls] == [(70, None)]
    assert repository.local["send_payload"]["terminal_failure"]["message_ids_consumed"] == []


def test_send_call_exception_is_terminal_and_consumes_exact_msg_id_once() -> None:
    failed_service, failed_repository, failed_platform, failed_system, _events = _service(
        send_error=RuntimeError("send result unavailable")
    )
    failed_result = _run(failed_service)

    assert failed_result["status"] == "sent"
    assert len(failed_system.send_calls) == 1
    assert failed_platform.consume_calls[0]["status"] == 30
    assert failed_platform.consume_calls[0]["messages"] == [{"msgId": "701", "status": 30, "remark": ""}]
    assert len(failed_platform.rule_calls) == 1
    assert failed_repository.local["status"] == "sent_recovered"
    assert failed_repository.local["send_response"]["data"]["delivery_status"] == "submission_unconfirmed"
    assert failed_repository.event_updates[-1]["status"] == "platform_completed"


def test_customer_gate_failure_records_interface_http_status_and_phase_without_body() -> None:
    secret_body = "private-upstream-response"
    service, _repository, _platform, _system, _events = _service(
        gate_error=RuntimeError(f"outreach_system_http_503: {secret_body}")
    )

    result = _run(service)

    assert result["reason"] == (
        "customer_gate_query_failed:"
        "interface=conversation_status;phase=http_response;http_status=503;error=RuntimeError"
    )
    assert secret_body not in result["reason"]


def test_task_concurrency_is_capped_at_four_even_when_configured_higher() -> None:
    service = SopPlatformTaskService(
        settings=SimpleNamespace(
            sop_platform_queue_size=10,
            sop_platform_task_concurrency=8,
        ),
        repository=SimpleNamespace(),
        platform_client=SimpleNamespace(),
        system_client=SimpleNamespace(),
        model_client=_NoModel(),
        customer_context_service=SimpleNamespace(),
    )

    assert service._configured_task_concurrency == 8
    assert service._task_concurrency == 4


def test_pending_timeout_alerts_only_on_third_consecutive_failed_poll_and_resets_after_recovery() -> None:
    class _Alerts:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def notify_system_failure(self, **values: Any) -> None:
            self.calls.append(values)

    alerts = _Alerts()
    service = SopPlatformTaskService(
        settings=SimpleNamespace(
            sop_platform_queue_size=10,
            sop_platform_task_concurrency=4,
        ),
        repository=SimpleNamespace(),
        platform_client=SimpleNamespace(),
        system_client=SimpleNamespace(),
        model_client=_NoModel(),
        customer_context_service=SimpleNamespace(),
        failure_alert_service=alerts,
    )

    async def exercise() -> None:
        await service._handle_poll_failure(TimeoutError("first"))
        await service._handle_poll_failure(TimeoutError("second"))
        assert alerts.calls == []
        await service._handle_poll_failure(TimeoutError("third"))
        await service._handle_poll_failure(TimeoutError("fourth"))
        assert len(alerts.calls) == 1
        service._record_poll_success()
        await service._handle_poll_failure(TimeoutError("new-first"))

    asyncio.run(exercise())

    assert len(alerts.calls) == 1
    assert service._consecutive_poll_timeouts == 1
    assert service._counters["poll_timeout_alerted"] == 1
    assert service._counters["poll_timeout_recovered"] == 1


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


def test_poll_reserves_new_task_before_persistence_can_be_seen_by_recovery() -> None:
    persistence_started = threading.Event()
    release_persistence = threading.Event()

    class _PollPlatform:
        async def pending(self, **_values: Any) -> dict[str, Any]:
            return {"items": [_task()], "total": 1}

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

    def persist(task: dict[str, Any], **_values: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        persistence_started.set()
        assert release_persistence.wait(timeout=2)
        return {}, {"id": f"local-{task['taskId']}"}

    service._ensure_local_task = persist

    async def run_poll() -> dict[str, Any]:
        poll = asyncio.create_task(service.poll_once())
        assert await asyncio.to_thread(persistence_started.wait, 2)
        assert "101" in service._queued_ids
        release_persistence.set()
        return await poll

    result = asyncio.run(run_poll())

    assert result["enqueued_count"] == 1
    assert "101" in service._queued_ids


def test_new_task_persists_deterministic_marker_before_recoverable_event_status() -> None:
    operations: list[tuple[str, str]] = []
    local = {
        "id": "local-101",
        "status": "platform_queued",
        "error": "",
        "send_payload": {},
        "created": True,
    }

    class _PersistenceRepository:
        def create_sop_event(self, _payload: dict[str, Any]) -> dict[str, Any]:
            operations.append(("event_create", "accepted"))
            return {"created": True, "status": "accepted"}

        def create_sop_send_task(self, **_values: Any) -> dict[str, Any]:
            operations.append(("task_create", "platform_queued"))
            return dict(local)

        def update_sop_send_task(self, _task_id: str, **values: Any) -> dict[str, Any]:
            processing_mode = str(values["send_payload"].get("processing_mode") or "")
            operations.append(("task_marker", processing_mode))
            local.update(values)
            return dict(local)

        def update_sop_event_status(self, _event_id: str, *, status: str, **_values: Any) -> dict[str, Any]:
            operations.append(("event_status", status))
            assert local["send_payload"]["processing_mode"] == "deterministic_customer_gate"
            return {"created": True, "status": status}

    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.repository = _PersistenceRepository()

    event, persisted = service._ensure_local_task(_task(), status="platform_queued")

    assert event["status"] == "platform_queued"
    assert persisted["send_payload"]["processing_mode"] == "deterministic_customer_gate"
    assert operations == [
        ("event_create", "accepted"),
        ("task_create", "platform_queued"),
        ("task_marker", "deterministic_customer_gate"),
        ("event_status", "platform_queued"),
    ]


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


def test_concurrent_recovery_observes_completed_send_without_replaying_task() -> None:
    service, repository, platform, system, events = _service()
    repository.local.update(
        {
            "status": "sent",
            "sent_at": "2026-09-09T01:00:14+00:00",
            "send_response": {
                "data": {
                    "delivery_status": "platform_accepted",
                    "system_msgids": ["msg-1"],
                }
            },
        }
    )
    service._ensure_local_task = lambda _task, **_kwargs: (
        {"status": "platform_completed"},
        dict(repository.local),
    )

    result = asyncio.run(
        service._process_customer_batch_locked(
            [_task()],
            trigger_tasks=[],
            batch_key="online_service|corp|staff|external",
            biz_type="online_service",
            recovery_status="platform_processing",
        )
    )

    assert result["status"] == "sent"
    assert result["reason"] == "already_terminal_after_concurrent_processing"
    assert events == []
    assert system.send_calls == []
    assert platform.consume_calls == []


def test_ordinary_pending_poll_closes_recorded_send_invocation_without_replaying() -> None:
    service, repository, platform, system, events = _service()
    repository.local.update(
        {
            "status": "sending",
            "send_payload": {
                "processing_mode": "deterministic_customer_gate",
                "send_invoked_at": "2026-09-09T01:00:14+00:00",
                "delivery_uncertain": True,
                "final_messages": [{"type": "text", "content": {"text": "第一组文本"}}],
                "content_message_results": [{"msgId": "701", "status": 30, "remark": ""}],
                "consume_results": [],
            },
        }
    )
    service._ensure_local_task = lambda _task, **_kwargs: (
        {"status": "platform_sequence_waiting"},
        dict(repository.local),
    )

    result = _run(service)

    assert result["status"] == "sent"
    assert system.send_calls == []
    assert "send" not in events
    assert platform.consume_calls[0]["status"] == 30
    assert platform.consume_calls[0]["messages"] == [{"msgId": "701", "status": 30, "remark": ""}]
    assert repository.local["send_response"]["data"]["delivery_status"] == "submission_unconfirmed"


def test_terminal_send_rejection_recovery_consumes_without_resending() -> None:
    service, repository, platform, system, events = _service()
    repository.local.update(
        {
            "status": "processing_retry",
            "error": "wecom_aggregate_send_failed:account_unassigned",
            "send_payload": {
                "processing_mode": "deterministic_customer_gate",
                "reason": "wecom_aggregate_send_failed:account_unassigned",
                "send_invoked_at": "2026-09-09T01:00:14+00:00",
                "content_message_results": [{"msgId": "701", "status": 30, "remark": ""}],
                "consume_results": [],
            },
        }
    )
    service._ensure_local_task = lambda _task, **_kwargs: (
        {"status": "platform_sequence_waiting"},
        dict(repository.local),
    )

    result = asyncio.run(
        service._process_customer_batch_locked(
            [_task()],
            trigger_tasks=[],
            batch_key="online_service|corp|staff|external",
            biz_type="online_service",
            recovery_status="platform_sequence_waiting",
        )
    )

    assert result["status"] == "failed_consumed"
    assert result["reason"] == "wecom_aggregate_send_failed:account_unassigned"
    assert system.send_calls == []
    assert "send" not in events
    assert "sop_messages" not in events
    assert platform.consume_calls[0]["status"] == 70
    assert platform.consume_calls[0]["messages"] is None
    assert repository.local["send_payload"]["terminal_failure"]["message_ids_consumed"] == []


def test_msg_id_binding_conflict_closes_task_without_consuming_another_message() -> None:
    service, repository, platform, system, _events = _service(send_error=TimeoutError("connect timeout"))

    async def consume_with_binding_conflict(**values: Any) -> dict[str, Any]:
        platform.consume_calls.append(values)
        if values["status"] == 30:
            raise RuntimeError("sop_platform_error: 队列消息已绑定其它任务")
        return {"code": 200, "data": {"status": values["status"]}}

    platform.consume = consume_with_binding_conflict  # type: ignore[method-assign]

    result = _run(service)

    assert result["status"] == "failed_consumed"
    assert result["reason"] == "sop_message_binding_conflict"
    assert len(system.send_calls) == 1
    assert [(call["status"], call.get("messages")) for call in platform.consume_calls] == [
        (30, [{"msgId": "701", "status": 30, "remark": ""}]),
        (70, None),
    ]
    assert repository.local["status"] == "failed_consumed"
    assert repository.local["send_payload"]["terminal_failure"]["message_ids_consumed"] == []
    assert repository.local["send_payload"]["terminal_failure"]["unconsumed_message_ids"] == ["701"]


def test_live_pending_legacy_task_is_consumed_without_replaying_or_consuming_content() -> None:
    service, repository, platform, system, events = _service()
    repository.local.update(
        {
            "status": "platform_queued",
            "send_payload": {
                "processing_mode": "customer_batch_sequence",
                "content_message_results": [{"msgId": "701", "status": 30, "remark": ""}],
                "consume_results": [],
            },
        }
    )
    service._ensure_local_task = lambda _task, **_kwargs: (
        {"status": "platform_legacy_quarantined"},
        dict(repository.local),
    )

    result = _run(service)

    assert result["status"] == "failed_consumed"
    assert result["reason"] == "legacy_execution_disabled"
    assert system.send_calls == []
    assert "send" not in events
    assert "sop_messages" not in events
    assert [(call["status"], call.get("messages")) for call in platform.consume_calls] == [(70, None)]
    assert repository.local["send_payload"]["terminal_failure"]["message_ids_consumed"] == []
    assert repository.local["send_payload"]["terminal_failure"]["unconsumed_message_ids"] == ["701"]


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


def test_deterministic_recovery_reenters_customer_batch_without_legacy_model_path() -> None:
    service, repository, platform, system, _events = _service()
    repository.local["send_payload"] = {"processing_mode": "deterministic_customer_gate"}

    async def forbidden_legacy_path(*_args: Any, **_values: Any) -> dict[str, Any]:
        raise AssertionError("deterministic recovery must not enter the legacy model path")

    service._process_locked = forbidden_legacy_path  # type: ignore[method-assign]

    result = asyncio.run(service.process_task(_task(), recovery_status="platform_processing_retry"))

    assert result["status"] == "sent"
    assert len(system.send_calls) == 1
    assert platform.consume_calls[-1]["messages"] == [{"msgId": "701", "status": 30, "remark": ""}]


def test_terminal_failure_recovery_finishes_task_without_sending_or_consuming_content() -> None:
    service, repository, platform, system, events = _service()
    repository.local.update(
        {
            "status": "failure_consume_pending",
            "error": "customer_gate_query_failed:RuntimeError",
            "send_payload": {
                "processing_mode": "deterministic_task_failure",
                "reason": "customer_gate_query_failed:RuntimeError",
                "content_message_results": [],
                "consume_results": [],
            },
        }
    )

    result = asyncio.run(service.process_task(_task(), recovery_status="platform_complete_pending"))

    assert result["status"] == "failed_consumed"
    assert result["reason"] == "customer_gate_query_failed:RuntimeError"
    assert system.send_calls == []
    assert "send" not in events
    assert "sop_messages" not in events
    assert [(call["status"], call.get("messages")) for call in platform.consume_calls] == [(70, None)]
    assert repository.local["status"] == "failed_consumed"


def test_empty_pending_page_is_a_noop_without_failure_alert() -> None:
    class _EmptyPlatform:
        async def pending(self, **_values: Any) -> dict[str, Any]:
            return {"items": [], "total": 0, "complete": True}

    class _Alerts:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def notify_system_failure(self, **values: Any) -> None:
            self.calls.append(values)

    alerts = _Alerts()
    service = SopPlatformTaskService(
        settings=SimpleNamespace(
            sop_platform_queue_size=10,
            sop_platform_batch_size=10,
            sop_platform_priority_wechats="",
            sop_platform_task_concurrency=1,
        ),
        repository=SimpleNamespace(),
        platform_client=_EmptyPlatform(),
        system_client=SimpleNamespace(),
        model_client=_NoModel(),
        customer_context_service=SimpleNamespace(),
        failure_alert_service=alerts,
    )
    service._restore_reserved_prefix_ids = lambda: None

    result = asyncio.run(service.poll_once())

    assert result["pending_count"] == 0
    assert result["enqueued_count"] == 0
    assert result["error_count"] == 0
    assert alerts.calls == []


def test_manual_resend_is_disabled_to_prevent_implicit_message_consumption() -> None:
    service, _repository, _platform, _system, _events = _service()

    with pytest.raises(RuntimeError, match="manual resend is disabled"):
        asyncio.run(service.admin_resend_task("101"))
