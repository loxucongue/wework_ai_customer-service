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
