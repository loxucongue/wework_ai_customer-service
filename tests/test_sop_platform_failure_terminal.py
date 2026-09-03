from __future__ import annotations

import asyncio
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.services.sop_platform_task_service import SopPlatformTaskService


class _Repository:
    def __init__(self) -> None:
        self.task_updates: list[dict[str, object]] = []
        self.event_updates: list[dict[str, object]] = []

    def update_sop_send_task(self, task_id: str, **values: object) -> None:
        self.task_updates.append({"task_id": task_id, **values})

    def update_sop_event_status(self, event_id: str, **values: object) -> None:
        self.event_updates.append({"event_id": event_id, **values})


class _Platform:
    def __init__(self) -> None:
        self.consume_calls: list[dict[str, object]] = []
        self.rule_calls: list[dict[str, object]] = []

    async def consume(self, **values: object) -> dict[str, object]:
        self.consume_calls.append(values)
        return {"code": 200, "data": {"status": values["status"]}}

    async def service_rule_data(self, **values: object) -> dict[str, object]:
        self.rule_calls.append(values)
        return {"code": 200, "data": {}}


class _NoSendSystem:
    async def send(self, **_values: object) -> dict[str, object]:
        raise AssertionError("expired retry must not submit another customer message")


def _service() -> tuple[SopPlatformTaskService, _Repository, _Platform]:
    repository = _Repository()
    platform = _Platform()
    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.settings = SimpleNamespace(sop_platform_send_retry_timeout_seconds=600)
    service.repository = repository
    service.platform_client = platform
    service.system_client = _NoSendSystem()
    service._reserved_prefix_ids = {"prefix-task", "101", "content-msg-id"}
    service._counters = Counter()
    return service, repository, platform


def _task() -> dict[str, object]:
    return {"taskId": "101", "message_content": [{"type": "text", "content": "hello"}]}


def test_downstream_409_consumes_only_platform_task_and_reports_aggregate_failure() -> None:
    service, repository, platform = _service()
    result = asyncio.run(
        service._handle_batch_send_failure(
            platform_task=_task(),
            selected_task_id="101",
            local_task_id="local-101",
            audit={
                "consume_results": [],
                "skipped_prefix_task_ids": ["prefix-task"],
                "compat_trigger_task_ids": ["content-msg-id"],
            },
            error=RuntimeError("outreach_system_http_409: AI automation master switch is disabled"),
        )
    )

    assert result["status"] == "completed_without_send"
    assert platform.consume_calls == [
        {
            "task_id": "101",
            "status": 70,
            "remark": "企微聚合平台发送失败，任务已消费且未发送",
            "content_exhausted": None,
        }
    ]
    assert platform.rule_calls[0]["task_id"] == "101"
    assert platform.rule_calls[0]["scene_code"] == "wecom_aggregate_send_failed"
    assert repository.task_updates[-1]["status"] == "completed_without_send"
    assert repository.event_updates[-1]["status"] == "platform_completed"


def test_expired_delivery_retry_consumes_task_without_resending_or_consuming_content_ids() -> None:
    service, repository, platform = _service()
    result = asyncio.run(
        service._retry_batch_send(
            _task(),
            local_task={
                "id": "local-101",
                "send_payload": {
                    "final_messages": [{"type": "text", "content": {"text": "hello"}}],
                    "skipped_prefix_task_ids": ["prefix-task"],
                    "compat_trigger_task_ids": ["content-msg-id"],
                    "delivery_retry": {"first_failure_at": time.time() - 601},
                    "consume_results": [],
                },
            },
        )
    )

    assert result["status"] == "completed_without_send"
    assert [call["task_id"] for call in platform.consume_calls] == ["101"]
    assert platform.consume_calls[0]["content_exhausted"] is None
    assert platform.rule_calls[0]["scene_code"] == "sop_send_failed"
    assert repository.event_updates[-1]["status"] == "platform_completed"


def test_transient_failure_starts_bounded_retry_window_without_consuming() -> None:
    service, repository, platform = _service()
    result = asyncio.run(
        service._handle_batch_send_failure(
            platform_task=_task(),
            selected_task_id="101",
            local_task_id="local-101",
            audit={"consume_results": []},
            error=TimeoutError("connect timeout"),
        )
    )

    assert result["status"] == "processing_retry"
    assert result["retry"]["first_failure_at"]
    assert result["retry"]["retry_deadline_at"]
    assert platform.consume_calls == []
    assert repository.task_updates[-1]["status"] == "processing_retry"
    assert repository.event_updates[-1]["status"] == "platform_batch_send_retry"
