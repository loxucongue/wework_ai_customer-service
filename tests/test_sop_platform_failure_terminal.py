from __future__ import annotations

import asyncio
import sys
import time
from collections import Counter, deque
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.services.sop_platform_task_service import (
    SopPlatformTaskService,
    _configured_priority_wechats,
    _is_priority_wechat,
    _outreach_system_identity,
    _outreach_send_request,
    _platform_message_error,
    _platform_task_is_already_no_send,
    _select_bulk_human_takeover_tasks,
    _send_result_requires_confirmation,
    _task_preflight_no_send_reason,
)
from app.services.sop_platform_client import SopPlatformTaskStateError


def test_terminal_platform_states_are_reconciled_without_retry() -> None:
    for state in ("无需发送", "已取消", "已完成", "已失败", "失败"):
        error = RuntimeError(f"sop_platform_task_terminal_state:{state}: 任务不可消费（当前状态：{state}）")
        assert _platform_task_is_already_no_send(error)

    assert not _platform_task_is_already_no_send(RuntimeError("connect timeout"))
    assert _platform_task_is_already_no_send(SopPlatformTaskStateError(state="无需发送", payload={"code": 400}))


def test_platform_queued_tasks_are_recovered_after_restart() -> None:
    assert "platform_queued" in SopPlatformTaskService.RECOVERY_STATUSES


def test_outreach_client_identity_excludes_canonical_audit_fields() -> None:
    assert _outreach_system_identity(
        {
            "corp_id": "corp",
            "customer_id": "12228418",
            "external_userid": "wm-external",
            "user_id": "DY258",
            "wechat": "DY258",
            "platform_customer_id": "12228418",
            "platform_user_id": "DY258",
            "customer_add_wechat_id": "22879512",
            "platform_customer_id_source": "legacy_customer_id",
        }
    ) == {
        "corp_id": "corp",
        "customer_id": "12228418",
        "external_userid": "wm-external",
        "user_id": "DY258",
        "wechat": "DY258",
    }


def test_platform_snake_case_messages_pass_preflight_validation() -> None:
    task = {
        "message_content": [
            {"msg_type": "text", "content_text": "介绍项目"},
            {
                "msg_type": "image",
                "media_url": "https://example.com/evidence.png",
                "media_urls_json": ["https://example.com/evidence.png"],
            },
            {"msg_type": "text", "content_text": "您想了解哪方面？"},
        ]
    }
    identity = {
        "corp_id": "corp",
        "customer_id": "12228418",
        "external_userid": "wm-external",
        "user_id": "DY258",
        "wechat": "DY258",
    }

    assert _platform_message_error(task) == ""
    assert (
        _task_preflight_no_send_reason(
            task,
            identity=identity,
            settings=SimpleNamespace(sop_platform_live_not_before=""),
        )
        == ""
    )


def test_platform_snake_case_invalid_content_remains_blocked() -> None:
    assert (
        _platform_message_error({"message_content": [{"msg_type": "file", "media_url": "https://example.com/a"}]})
        == "unsupported_message_type"
    )
    assert _platform_message_error({"message_content": [{"msg_type": "text", "content_text": ""}]}) == "empty_text"
    assert (
        _platform_message_error({"message_content": [{"msg_type": "image", "media_url": "not-a-url"}]})
        == "invalid_media_url"
    )


class _Repository:
    def __init__(self) -> None:
        self.task_updates: list[dict[str, object]] = []
        self.event_updates: list[dict[str, object]] = []

    def get_sop_event(self, _event_id: str) -> dict[str, object]:
        return {"raw_payload": {"platform_task": _task()}}

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
    service._timings = {name: deque(maxlen=500) for name in ("consume", "rule_data")}
    service._ensure_local_task = lambda task, **_kwargs: ({}, {"id": f"local-{task['taskId']}"})
    service._remember_terminal = lambda _task_id: None
    service._log_task_phase = lambda **_kwargs: None
    return service, repository, platform


def _task() -> dict[str, object]:
    return {"taskId": "101", "message_content": [{"type": "text", "content": "hello"}]}


def test_downstream_409_is_consumed_as_failed_without_consuming_msg_id() -> None:
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

    assert result["status"] == "failed_consumed"
    assert result["reason"] == "wecom_aggregate_send_failed:ai_automation_disabled"
    assert platform.consume_calls[0]["status"] == 70
    assert platform.consume_calls[0]["messages"] is None
    assert len(platform.rule_calls) == 1
    assert repository.task_updates[-1]["status"] == "failed_consumed"
    assert repository.event_updates[-1]["status"] == "platform_completed"


def test_downstream_account_unassigned_preserves_safe_reason_code() -> None:
    service, repository, platform = _service()
    result = asyncio.run(
        service._handle_batch_send_failure(
            platform_task=_task(),
            selected_task_id="101",
            local_task_id="local-101",
            audit={
                "context": {
                    "management_status": {
                        "send_allowed": False,
                        "reason_code": "unrelated_takeover_reason",
                        "send_reason_code": "account_unassigned",
                    }
                }
            },
            error=RuntimeError("aggregate send rejected without a detailed response body"),
        )
    )

    assert result["status"] == "failed_consumed"
    assert result["reason"] == "wecom_aggregate_send_failed:account_unassigned"
    assert platform.consume_calls[0]["status"] == 70
    assert platform.consume_calls[0]["messages"] is None
    assert len(platform.rule_calls) == 1
    assert repository.task_updates[-1]["status"] == "failed_consumed"


def test_expired_delivery_retry_remains_unconsumed_and_recoverable() -> None:
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

    assert result["status"] == "send_failed"
    assert platform.consume_calls == []
    assert platform.rule_calls == []
    assert repository.task_updates[-1]["status"] == "processing_retry"
    assert repository.event_updates[-1]["status"] == "platform_processing_retry"


def test_uncertain_or_unidentified_send_never_counts_as_confirmed() -> None:
    assert _send_result_requires_confirmation(
        {"data": {"delivery_status": "submission_unknown", "callback_required": False}}
    )
    assert _send_result_requires_confirmation(
        {"data": {"delivery_status": "platform_accepted", "callback_required": False}}
    )
    assert not _send_result_requires_confirmation(
        {
            "data": {
                "delivery_status": "platform_accepted",
                "callback_required": False,
                "system_msgid": "msg-1",
            }
        }
    )


def test_historical_retry_request_is_sanitized_before_strict_client_call() -> None:
    assert _outreach_send_request(
        {
            "corp_id": "corp",
            "task_id": "task",
            "reply_messages": [],
            "platform_customer_id": "audit-only",
            "identity_source": "audit-only",
        }
    ) == {"corp_id": "corp", "task_id": "task", "reply_messages": []}


def test_send_timeout_consumes_exact_msg_id_without_retrying() -> None:
    service, repository, platform = _service()
    result = asyncio.run(
        service._handle_batch_send_failure(
            platform_task=_task(),
            selected_task_id="101",
            local_task_id="local-101",
            audit={
                "consume_results": [],
                "content_message_results": [{"msgId": "701", "status": 30, "remark": ""}],
                "final_messages": [{"type": "text", "content": {"text": "hello"}}],
            },
            error=TimeoutError("connect timeout"),
        )
    )

    assert result["status"] == "sent"
    assert platform.consume_calls[0]["status"] == 30
    assert platform.consume_calls[0]["messages"] == [{"msgId": "701", "status": 30, "remark": ""}]
    assert len(platform.rule_calls) == 1
    assert repository.task_updates[-1]["status"] == "sent_recovered"
    assert repository.task_updates[-1]["send_response"]["data"]["delivery_status"] == "submission_unconfirmed"
    assert repository.event_updates[-1]["status"] == "platform_completed"


def test_uncertain_send_recovery_consumes_without_evidence_check_or_resend() -> None:
    service, _repository, platform = _service()

    async def missing_delivery(**_values: object) -> dict[str, object]:
        return {"found": False, "checked_at": "2026-09-08T12:00:00+00:00"}

    service._existing_platform_delivery = missing_delivery
    result = asyncio.run(
        service._recover_interrupted_batch_send(
            _task(),
            local_task={
                "id": "local-101",
                "send_payload": {
                    "processing_mode": "deterministic_customer_gate",
                    "send_invoked_at": "2026-09-08T12:00:00+00:00",
                    "delivery_uncertain": True,
                    "delivery_idempotency_key": "sop_platform_message:701",
                    "final_messages": [{"type": "text", "content": {"text": "hello"}}],
                    "content_message_results": [{"msgId": "701", "status": 30, "remark": ""}],
                },
            },
        )
    )

    assert result["status"] == "sent"
    assert platform.consume_calls[0]["status"] == 30
    assert platform.consume_calls[0]["messages"] == [{"msgId": "701", "status": 30, "remark": ""}]
    assert len(platform.rule_calls) == 1


def test_fixed_content_task_is_not_rejected_because_scheduled_time_is_old() -> None:
    task = {
        **_task(),
        "scheduledAt": time.time() - 601,
        "useAiCopy": False,
    }
    identity = {
        "corp_id": "corp",
        "customer_id": "customer",
        "external_userid": "external",
        "user_id": "user",
        "wechat": "wechat",
    }

    assert (
        _task_preflight_no_send_reason(
            task,
            identity=identity,
            settings=SimpleNamespace(sop_platform_live_not_before=""),
        )
        == ""
    )


def test_bulk_human_takeover_excludes_kept_wechat_and_new_tasks() -> None:
    cutoff = time.time() - 60
    settings = SimpleNamespace(
        sop_platform_bulk_human_takeover_exclude="SL0906,DY8808",
        sop_platform_bulk_human_takeover_before=cutoff,
    )
    selected = _select_bulk_human_takeover_tasks(
        [
            {"task_id": 101, "scheduledAt": cutoff - 1, "user_wechat_id": "SL8003"},
            {"task_id": 102, "scheduledAt": cutoff - 1, "user_wechat_id": "sl0906"},
            {"task_id": 103, "scheduledAt": cutoff + 1, "user_wechat_id": "SL8003"},
        ],
        settings=settings,
    )

    assert [task["task_id"] for task in selected] == [101]
    assert selected[0]["_aics_terminal_outcome"] == "human_takeover"


def test_priority_wechats_are_trimmed_and_deduplicated_in_order() -> None:
    settings = SimpleNamespace(sop_platform_priority_wechats="SL0906, DY8808,SL0906,,SL8004")

    assert _configured_priority_wechats(settings) == ["SL0906", "DY8808", "SL8004"]


def test_priority_wechat_matches_platform_user_id_without_remote_filter() -> None:
    assert _is_priority_wechat(
        {"user_wechat_id": "sl0906", "user_wechat": "internal-name"},
        ["SL0906", "DY8808"],
    )
    assert not _is_priority_wechat({"user_wechat_id": "SL8003"}, ["SL0906", "DY8808"])
