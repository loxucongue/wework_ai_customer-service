from __future__ import annotations

import asyncio
from collections import Counter, deque

import pytest

from app.services.sop_platform_task_service import (
    SopPlatformTaskService,
    _load_sop_message_groups_for_events,
)


class _MessageGroupsPlatform:
    async def sop_messages(self, **_values: object) -> dict[str, object]:
        first = {"id": "group-1", "message_content": [{"type": "text", "content": "first"}]}
        second = {"id": "group-2", "message_content": [{"type": "text", "content": "second"}]}
        return {
            "items": [first, second],
            "next_item": first,
            "total": 2,
            "complete": True,
        }


def test_content_lookup_exposes_only_next_group_for_each_due_trigger() -> None:
    page = asyncio.run(
        _load_sop_message_groups_for_events(
            _MessageGroupsPlatform(),
            [{"taskId": "task-1", "eventLogId": "event-1"}],
            limit=100,
        )
    )

    assert page["total"] == 1
    assert page["content_group_total"] == 2
    assert [item["task_id"] for item in page["items"]] == ["task-1"]
    assert page["items"][0]["_aics_sop_message_wait_msg_id"] == "group-1"
    assert page["items"][0]["_aics_sop_message_group_ids"] == ["group-1", "group-2"]


def test_finalize_rejects_unsent_prefix_before_any_platform_consume() -> None:
    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service._counters = Counter()
    service._timings = {"consume": deque(maxlen=10)}

    with pytest.raises(RuntimeError, match="strict SOP sequence"):
        asyncio.run(
            service._finalize_batch_prefix(
                selected_task_id="later-task",
                skipped_prefix_task_ids=["earlier-task"],
                audit={"consume_results": []},
            )
        )


def test_no_send_gate_consumes_only_tasks_with_status_70() -> None:
    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    consume_calls: list[dict[str, object]] = []

    async def consume(**values: object) -> dict[str, object]:
        consume_calls.append(values)
        return {"code": 200, "data": {"status": values["status"]}}

    async def report(*_args: object, **_values: object) -> dict[str, object]:
        return {"rule_data_response": {"code": 200}}

    class _Repository:
        def update_sop_send_task(self, *_args: object, **_values: object) -> None:
            return None

        def update_sop_event_status(self, *_args: object, **_values: object) -> None:
            return None

    service.repository = _Repository()
    service._ensure_local_task = lambda task, **_kwargs: ({}, {"id": f"local-{task['taskId']}"})
    service._consume_with_audit = consume  # type: ignore[method-assign]
    service._report_terminal_rule_data = report  # type: ignore[method-assign]
    service._remember_terminal = lambda _task_id: None
    result = asyncio.run(
        service._consume_batch_without_send(
            [{"taskId": "earliest"}],
            trigger_tasks=[{"taskId": "trigger"}],
            reason="human_takeover",
            batch_key="contact",
            biz_type="online_service",
            batch_run_id="run-1",
        )
    )

    assert result["status"] == "completed_without_send"
    assert result["terminal_task_ids"] == ["earliest", "trigger"]
    assert [(call["task_id"], call["status"], call.get("messages")) for call in consume_calls] == [
        ("earliest", 70, None),
        ("trigger", 70, None),
    ]


def test_finalize_consumes_only_selected_task_and_exact_message_id() -> None:
    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    consume_calls: list[dict[str, object]] = []

    async def consume(**values: object) -> dict[str, object]:
        consume_calls.append(values)
        return {"code": 200, "data": {"status": values["status"]}}

    async def report(*_args: object, **_values: object) -> dict[str, object]:
        return {}

    class _Repository:
        def update_sop_event_status(self, *_args: object, **_values: object) -> None:
            return None

    service._consume_with_audit = consume  # type: ignore[method-assign]
    service._platform_task_from_local = lambda _task_id: {}  # type: ignore[method-assign]
    service._report_terminal_rule_data = report  # type: ignore[method-assign]
    service.repository = _Repository()
    service._reserved_prefix_ids = {"selected", "trigger"}

    terminal_ids = asyncio.run(
        service._finalize_batch_prefix(
            selected_task_id="selected",
            skipped_prefix_task_ids=[],
            compat_trigger_task_ids=["trigger"],
            audit={
                "consume_results": [],
                "content_message_results": [{"msgId": 9, "status": 30, "remark": ""}],
            },
        )
    )

    assert terminal_ids == ["selected"]
    assert len(consume_calls) == 1
    assert consume_calls[0]["task_id"] == "selected"
    assert consume_calls[0]["status"] == 30
    assert consume_calls[0]["messages"] == [{"msgId": 9, "status": 30, "remark": ""}]
    assert service._reserved_prefix_ids == {"trigger"}


def test_durable_sequence_guard_finds_earlier_unconfirmed_task() -> None:
    class _Repository:
        def list_platform_sop_task_records(self, **_values: object) -> list[dict[str, object]]:
            return [
                {
                    "event_id": "platform_sop_task:earlier",
                    "event_status": "platform_failed",
                    "task_status": "send_failed",
                    "corp_id": "corp",
                    "received_at": "2026-09-08T01:00:00+00:00",
                    "platform_task": {
                        "taskId": "earlier",
                        "scheduledAt": "2026-09-08T09:00:00+08:00",
                    },
                }
            ]

    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.repository = _Repository()
    current = {
        "taskId": "later",
        "scheduledAt": "2026-09-08T10:00:00+08:00",
        "corp_id": "corp",
        "customer_id": "customer",
        "external_userid": "external",
        "user_id": "user",
        "wechat": "wechat",
    }

    blocker = service._find_earlier_unresolved_sequence_task(
        current,
        current_task_ids={"later"},
    )

    assert blocker["task_id"] == "earlier"
    assert blocker["event_status"] == "platform_failed"


def test_reserved_prefix_restore_uses_one_filtered_joined_query() -> None:
    class _Repository:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def list_platform_sop_task_records(self, **values: object) -> list[dict[str, object]]:
            self.calls.append(values)
            return [
                {
                    "event_id": "platform_sop_task:selected",
                    "event_status": "platform_sequence_blocked",
                    "platform_task": {"taskId": "selected"},
                    "send_payload": {
                        "sequence_reserved_task_ids": ["selected", "later"],
                        "compat_trigger_task_ids": ["trigger"],
                    },
                }
            ]

    repository = _Repository()
    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.repository = repository
    service._reserved_prefix_ids = set()

    service._restore_reserved_prefix_ids()

    assert len(repository.calls) == 1
    assert repository.calls[0]["limit"] == 500
    assert repository.calls[0]["oldest_first"] is True
    assert "platform_sequence_blocked" in repository.calls[0]["event_statuses"]
    assert "platform_legacy_quarantined" in repository.calls[0]["event_statuses"]
    assert service._reserved_prefix_ids == {"selected", "later", "trigger"}
