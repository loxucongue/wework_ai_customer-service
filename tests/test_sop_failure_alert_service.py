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

    def create_sop_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.created.append(payload)
        return {**payload, "raw_payload": payload, "created": True, "retry_count": 0}

    def update_sop_event_status(self, event_id: str, *, status: str, error: str = "") -> None:
        self.updated.append((event_id, status, error))

    def get_sop_send_task_by_idempotency_key(self, _key: str) -> dict[str, Any]:
        return {}

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
