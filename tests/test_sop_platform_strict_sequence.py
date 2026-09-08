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


def test_no_send_gate_is_preserved_as_failure_without_platform_consume() -> None:
    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    captured: dict[str, object] = {}

    async def defer(tasks: list[dict[str, object]], **values: object) -> dict[str, object]:
        captured.update(values)
        return {
            "processed": False,
            "status": "send_failed",
            "task_id": tasks[0]["taskId"],
            "terminal_task_ids": [],
        }

    service._defer_sequence_blocked = defer  # type: ignore[method-assign]
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

    assert result["status"] == "send_failed"
    assert result["terminal_task_ids"] == []
    assert captured["failure_reason"] == "human_takeover"
    assert captured["trigger_tasks"] == [{"taskId": "trigger"}]


def test_compat_trigger_is_completed_as_sent_after_confirmed_content_delivery() -> None:
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
            audit={"consume_results": []},
        )
    )

    assert terminal_ids == ["selected", "trigger"]
    assert [call["status"] for call in consume_calls] == [30, 30]
    assert all(call["status"] != 70 for call in consume_calls)


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
