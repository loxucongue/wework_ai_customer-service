from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.sop_failure_alert_service import SopFailureAlertService


class _Repository:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, str, str]] = []
        self.retry_events: list[dict[str, Any]] = []
        self.alerts_by_task: dict[str, dict[str, Any]] = {}
        self.claimed_events: set[str] = set()
        self.local_task: dict[str, Any] = {}

    def create_sop_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        alert = payload.get("alert") if isinstance(payload.get("alert"), dict) else {}
        task_id = str(alert.get("task_id") or "")
        if task_id in self.alerts_by_task:
            return {**self.alerts_by_task[task_id], "created": False}
        self.created.append(payload)
        event = {**payload, "raw_payload": payload, "created": True, "retry_count": 0}
        if task_id:
            self.alerts_by_task[task_id] = event
        return event

    def update_sop_event_status(self, event_id: str, *, status: str, error: str = "") -> None:
        self.updated.append((event_id, status, error))

    def claim_sop_failure_alert_delivery(self, event_id: str, *, stale_before: str) -> bool:
        del stale_before
        if event_id in self.claimed_events:
            return False
        self.claimed_events.add(event_id)
        self.updated.append((event_id, "alert_sending", ""))
        return True

    def get_sop_send_task_by_idempotency_key(self, _key: str) -> dict[str, Any]:
        return dict(self.local_task)

    def find_sop_failure_alert_by_task_id(self, task_id: str) -> dict[str, Any]:
        return dict(self.alerts_by_task.get(task_id) or {})

    def list_sop_events_by_statuses(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return list(self.retry_events)


class _Client:
    available = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_markdown(self, *, title: str, text: str) -> None:
        self.sent.append((title, text))


def _service() -> tuple[SopFailureAlertService, _Repository, _Client]:
    repository = _Repository()
    client = _Client()
    service = SopFailureAlertService(
        settings=SimpleNamespace(sop_failure_alert_retry_batch_size=20, release_id="test-release"),
        repository=repository,
        client=client,
    )
    return service, repository, client


@pytest.mark.parametrize(
    "result",
    [
        {"status": "send_failed", "decision": {"failure_reason": "customer_relation_deleted"}},
        {"status": "send_failed", "decision": {"failure_reason": "human_takeover"}},
        {"status": "accepted"},
        {"status": "platform_delivery_pending"},
        {"status": "platform_send_uncertain"},
        {"status": "completed_without_send", "reason": "customer_already_opened"},
        {"status": "platform_completed"},
        {"status": "send_failed", "reason": "customer_already_opened"},
        {"status": "recovery_waiting", "reason": "delivery_not_confirmed"},
    ],
)
def test_notify_result_suppresses_non_failure_outcomes(result: dict[str, Any]) -> None:
    service, repository, client = _service()

    delivered = asyncio.run(
        service.notify_result(
            {"task_id": "task-1", **result},
            tasks=[{"taskId": "task-1"}],
            phase="queue_process",
        )
    )

    assert delivered == 0
    assert repository.created == []
    assert client.sent == []


@pytest.mark.parametrize(
    "reason",
    [
        "customer_relation_deleted",
        "task cannot be resent: customer_relation_deleted",
        "human_takeover",
        "delivery_not_confirmed",
        "TimeoutError: active_send_timeout_unknown_result",
    ],
)
def test_direct_failure_notification_suppresses_non_failure_reasons(reason: str) -> None:
    service, repository, client = _service()

    delivered = asyncio.run(
        service.notify_task_failure(
            task_id="task-1",
            status="send_failed",
            reason=reason,
            phase="recovery_exception",
        )
    )

    assert delivered == 0
    assert repository.created == []
    assert client.sent == []


def test_actionable_send_failure_still_creates_and_delivers_alert() -> None:
    service, repository, client = _service()

    delivered = asyncio.run(
        service.notify_result(
            {"task_id": "task-1", "status": "send_failed", "reason": "wecom_send_rejected"},
            tasks=[{"taskId": "task-1"}],
            phase="queue_process",
        )
    )

    assert delivered == 1
    assert len(repository.created) == 1
    assert len(client.sent) == 1
    assert repository.updated[-1][1] == "alert_sent"
    assert "责任方向：我方主动发送链路" in client.sent[0][1]


@pytest.mark.parametrize(
    ("reason", "phase", "failure_type", "responsibility"),
    [
        ("send_interface_timeout:TimeoutError", "queue_process", "接口超时", "我方主动发送链路"),
        ("missing_identity:corp_id", "queue_process", "任务缺少必填参数", "第三方 SOP 平台"),
        ("sop_messages_empty", "queue_process", "第三方任务内容不完整", "第三方 SOP 平台"),
        (
            "customer_gate_query_failed:ConnectTimeout",
            "queue_process",
            "接口超时",
            "我方客户状态/会话接口",
        ),
    ],
)
def test_alert_classifies_failure_type_and_responsibility(
    reason: str,
    phase: str,
    failure_type: str,
    responsibility: str,
) -> None:
    service, _repository, client = _service()

    delivered = asyncio.run(
        service.notify_task_failure(
            task_id=f"task-{reason}",
            status="send_failed",
            reason=reason,
            phase=phase,
        )
    )

    assert delivered == 1
    assert f"失败类型：{failure_type}" in client.sent[0][1]
    assert f"责任方向：{responsibility}" in client.sent[0][1]


def test_same_task_is_alerted_only_once_across_reason_and_phase_changes() -> None:
    service, repository, client = _service()

    first = asyncio.run(
        service.notify_task_failure(
            task_id="task-1",
            status="send_failed",
            reason="send_interface_timeout:TimeoutError",
            phase="queue_process",
        )
    )
    second = asyncio.run(
        service.notify_task_failure(
            task_id="task-1",
            status="send_failed",
            reason="completed_without_send",
            phase="recovery_process",
        )
    )

    assert first == 1
    assert second == 0
    assert len(repository.created) == 1
    assert len(client.sent) == 1


def test_concurrent_immediate_and_retry_delivery_send_one_alert() -> None:
    service, repository, client = _service()
    event = {
        "event_id": "sop_failure_alert:one",
        "status": "accepted",
        "raw_payload": {
            "alert": {
                "task_id": "task-1",
                "status": "send_failed",
                "reason": "send_failed",
            }
        },
    }

    async def deliver_concurrently() -> list[bool]:
        return list(await asyncio.gather(service._deliver_event(event), service._deliver_event(event)))

    results = asyncio.run(deliver_concurrently())

    assert sorted(results) == [False, True]
    assert len(client.sent) == 1


def test_overwritten_failure_status_is_suppressed_when_send_response_has_message_ids() -> None:
    service, repository, client = _service()
    repository.local_task = {
        "status": "processing_retry",
        "sent_at": "2026-09-09T01:00:14+00:00",
        "send_response": {
            "data": {
                "delivery_status": "platform_accepted",
                "system_msgids": ["msg-1", "msg-2"],
            }
        },
    }

    delivered = asyncio.run(
        service.notify_task_failure(
            task_id="task-1",
            status="send_failed",
            reason="TimeoutError",
            phase="recovery_exception",
        )
    )

    assert delivered == 0
    assert repository.created == []
    assert client.sent == []


def test_retry_worker_suppresses_preexisting_excluded_alert() -> None:
    service, repository, client = _service()
    repository.retry_events = [
        {
            "event_id": "sop_failure_alert:old",
            "status": "alert_retry",
            "raw_payload": {
                "alert": {
                    "status": "send_failed",
                    "reason": "human_takeover",
                }
            },
        }
    ]

    delivered = asyncio.run(service.retry_pending())

    assert delivered == 0
    assert client.sent == []
    assert repository.updated == [("sop_failure_alert:old", "alert_suppressed", "")]


def test_unknown_delivery_callback_status_remains_actionable() -> None:
    service, repository, client = _service()

    delivered = asyncio.run(
        service.notify_task_failure(
            task_id="task-1",
            status="send_failed",
            reason="unexpected_delivery_callback_status:empty",
            phase="message_delivery_callback",
        )
    )

    assert delivered == 1
    assert len(repository.created) == 1
    assert len(client.sent) == 1
