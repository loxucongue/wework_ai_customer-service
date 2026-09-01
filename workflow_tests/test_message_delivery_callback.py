from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.schemas import MessageDeliveryCallback
from app.services.async_reply_delivery import AsyncReplyDeliveryFinalizer
from app.services.message_delivery import MessageDeliveryService
from app.services.outreach_service import OutreachService
from app.services.sop.delivery_compatibility import SopDeliveryCompatibilityService
from app.services.sop_platform_task_service import SopPlatformTaskService
from app.services.storage import AppRepository
from app.services.storage.sqlite_store import SQLiteStore


class _DeliveryRepository:
    def __init__(self) -> None:
        self.assistant_messages: list[dict] = []

    def add_assistant_message(self, **payload: object) -> None:
        self.assistant_messages.append(dict(payload))


class _DeliveryMemory:
    def __init__(self) -> None:
        self.case_images: list[dict] = []
        self.activity_images: list[dict] = []
        self.store_facts: list[dict] = []

    def record_case_images_sent(self, key: str, **payload: object) -> None:
        self.case_images.append({"key": key, **payload})

    def record_activity_intro_image_sent(self, key: str, **payload: object) -> None:
        self.activity_images.append({"key": key, **payload})

    def record_store_fact(self, key: str, **payload: object) -> None:
        self.store_facts.append({"key": key, **payload})


def test_async_reply_delivery_finalizer_is_independent_from_reply_runtime() -> None:
    repository = _DeliveryRepository()
    memory = _DeliveryMemory()
    finalizer = AsyncReplyDeliveryFinalizer(repository, memory)  # type: ignore[arg-type]

    finalizer.finalize(
        {
            "status": "send_succeeded",
            "source_request_id": "request-1",
            "conversation_id": "conversation-1",
            "reply_messages": [{"type": "text", "content": "已发送"}],
            "source_context": {
                "assistant_request_id": "assistant-1",
                "memory_persist_allowed": True,
                "sales_contact_key": "corp:wechat:external",
                "case_image_record": {
                    "document_ids": ["case-1"],
                    "image_urls": ["https://example.com/case.jpg"],
                },
                "activity_intro_record": {"image_url": "https://example.com/activity.jpg"},
                "store_fact_record": {
                    "records": [{"store": {"store_id": "store-1"}, "event_type": "store_card_sent"}]
                },
            },
        }
    )

    assert repository.assistant_messages[0]["request_id"] == "assistant-1"
    assert memory.case_images[0]["key"] == "corp:wechat:external"
    assert memory.activity_images[0]["send_mode"] == "async"
    assert memory.store_facts[0]["store"]["store_id"] == "store-1"


def _service(tmp_path, *, callback_required: bool = True) -> tuple[MessageDeliveryService, AppRepository]:
    store = SQLiteStore(SimpleNamespace(db_path=tmp_path / "delivery.db"))
    store.initialize()
    repository = AppRepository(store)
    settings = SimpleNamespace(
        message_delivery_callback_required=callback_required,
        message_delivery_callback_public_url="https://ai.example.com/callbacks/v1/message-delivery",
        message_delivery_callback_token="callback-secret",
    )
    return MessageDeliveryService(settings, repository), repository


def _prepare(service: MessageDeliveryService, *, key: str = "request-1") -> dict:
    return service.prepare_dispatch(
        source_channel="async_reply",
        source_kind="ai_async_reply",
        source_request_id="request-1",
        source_task_id="request-1",
        conversation_id="conversation-1",
        identity={
            "corp_id": "corp-1",
            "customer_id": "customer-1",
            "external_userid": "external-1",
            "user_id": "user-1",
            "wechat": "wechat-1",
        },
        plan_id="",
        task_id="request-1",
        reply_messages=[
            {"type": "text", "content": "第一条"},
            {"type": "image", "content": "https://example.com/case.jpg"},
        ],
        source_context={"assistant_request_id": "request-1"},
        idempotency_key=key,
    )


def test_dispatch_is_idempotent_and_attaches_client_message_ids(tmp_path) -> None:
    service, _ = _service(tmp_path)

    first = _prepare(service)
    second = _prepare(service)

    assert first["dispatch_id"] == second["dispatch_id"]
    assert first["dispatch"]["created"] is True
    assert second["dispatch"]["created"] is False
    assert [item["client_message_id"] for item in first["reply_messages"]] == [
        f"{first['dispatch_id']}:1",
        f"{first['dispatch_id']}:2",
    ]


def test_dispatch_rejects_idempotency_key_reuse_with_different_payload(tmp_path) -> None:
    service, _ = _service(tmp_path)
    _prepare(service)

    with pytest.raises(ValueError, match="different payload"):
        service.prepare_dispatch(
            source_channel="async_reply",
            source_kind="ai_async_reply",
            source_request_id="request-1",
            source_task_id="request-1",
            conversation_id="conversation-1",
            identity={
                "corp_id": "corp-1",
                "customer_id": "customer-1",
                "external_userid": "external-1",
                "user_id": "user-1",
                "wechat": "wechat-1",
            },
            plan_id="",
            task_id="request-1",
            reply_messages=[{"type": "text", "content": "不同内容"}],
            source_context={},
            idempotency_key="request-1",
        )


def test_dispatches_can_be_loaded_by_source_request_id(tmp_path) -> None:
    service, repository = _service(tmp_path)
    prepared = _prepare(service)

    dispatches = repository.list_message_dispatches_for_request("request-1")

    assert [item["id"] for item in dispatches] == [prepared["dispatch_id"]]
    assert [item["message_type"] for item in dispatches[0]["items"]] == ["text", "image"]


def test_platform_acceptance_is_pending_until_success_callback(tmp_path) -> None:
    service, repository = _service(tmp_path)
    prepared = _prepare(service)
    dispatch_id = prepared["dispatch_id"]

    accepted = service.record_submission(
        dispatch_id,
        status="platform_accepted",
        platform_request_id="platform-request-1",
    )
    assert accepted["status"] == "platform_accepted"
    assert service.is_pending(accepted) is True
    assert service.is_terminal(accepted) is False

    result = service.accept_callback(
        MessageDeliveryCallback(
            event_id="event-success-1",
            dispatch_id=dispatch_id,
            task_id="request-1",
            status="send_succeeded",
            occurred_at="2026-08-22T10:00:00+08:00",
            platform_request_id="platform-request-1",
        )
    )

    assert result["duplicate"] is False
    assert result["dispatch"]["status"] == "send_succeeded"
    assert result["dispatch"]["succeeded_count"] == 2
    assert service.needs_finalization(result["dispatch"]) is True
    finalized = service.mark_finalized(dispatch_id)
    assert finalized["finalized_at"]
    assert repository.get_message_dispatch(dispatch_id)["status"] == "send_succeeded"


def test_duplicate_callback_is_idempotent(tmp_path) -> None:
    service, _ = _service(tmp_path)
    prepared = _prepare(service)
    callback = MessageDeliveryCallback(
        event_id="event-success-duplicate",
        dispatch_id=prepared["dispatch_id"],
        task_id="request-1",
        status="send_succeeded",
    )

    first = service.accept_callback(callback)
    second = service.accept_callback(callback)

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["dispatch"]["status"] == "send_succeeded"


def test_partial_failure_requires_items_and_preserves_per_message_result(tmp_path) -> None:
    service, _ = _service(tmp_path)
    prepared = _prepare(service)
    dispatch_id = prepared["dispatch_id"]

    with pytest.raises(ValueError, match="requires per-message items"):
        service.accept_callback(
            MessageDeliveryCallback(
                event_id="event-partial-invalid",
                dispatch_id=dispatch_id,
                task_id="request-1",
                status="partial_failed",
            )
        )

    with pytest.raises(ValueError, match="cover every dispatched message"):
        service.accept_callback(
            MessageDeliveryCallback(
                event_id="event-partial-incomplete",
                dispatch_id=dispatch_id,
                task_id="request-1",
                status="partial_failed",
                items=[
                    {
                        "client_message_id": f"{dispatch_id}:1",
                        "status": "send_succeeded",
                    }
                ],
            )
        )

    result = service.accept_callback(
        MessageDeliveryCallback(
            event_id="event-partial-valid",
            dispatch_id=dispatch_id,
            task_id="request-1",
            status="partial_failed",
            items=[
                {
                    "client_message_id": f"{dispatch_id}:1",
                    "status": "send_succeeded",
                    "platform_message_id": "platform-message-1",
                },
                {
                    "client_message_id": f"{dispatch_id}:2",
                    "status": "send_failed",
                    "error_code": "MEDIA_DOWNLOAD_FAILED",
                    "error_message": "image unavailable",
                },
            ],
        )
    )

    assert result["dispatch"]["status"] == "partial_failed"
    assert result["dispatch"]["succeeded_count"] == 1
    assert result["dispatch"]["failed_count"] == 1
    assert [item["status"] for item in result["dispatch"]["items"]] == [
        "send_succeeded",
        "send_failed",
    ]


def test_callback_rejects_unknown_dispatch_and_wrong_task(tmp_path) -> None:
    service, _ = _service(tmp_path)
    prepared = _prepare(service)

    with pytest.raises(LookupError, match="unknown dispatch_id"):
        service.accept_callback(
            MessageDeliveryCallback(
                event_id="event-unknown",
                dispatch_id="missing-dispatch",
                status="send_failed",
            )
        )

    with pytest.raises(ValueError, match="task_id does not match"):
        service.accept_callback(
            MessageDeliveryCallback(
                event_id="event-wrong-task",
                dispatch_id=prepared["dispatch_id"],
                task_id="another-task",
                status="send_succeeded",
            )
        )

    with pytest.raises(ValueError, match="task_id does not match"):
        service.accept_callback(
            MessageDeliveryCallback(
                event_id="event-missing-task",
                dispatch_id=prepared["dispatch_id"],
                status="send_succeeded",
            )
        )


def test_required_callback_configuration_fails_closed(tmp_path) -> None:
    store = SQLiteStore(SimpleNamespace(db_path=tmp_path / "invalid.db"))
    store.initialize()
    repository = AppRepository(store)

    with pytest.raises(RuntimeError, match="PUBLIC_URL"):
        MessageDeliveryService(
            SimpleNamespace(
                message_delivery_callback_required=True,
                message_delivery_callback_public_url="",
                message_delivery_callback_token="secret",
            ),
            repository,
        )


def test_optional_callback_without_complete_configuration_stays_disabled(tmp_path) -> None:
    store = SQLiteStore(SimpleNamespace(db_path=tmp_path / "disabled.db"))
    store.initialize()
    repository = AppRepository(store)

    service = MessageDeliveryService(
        SimpleNamespace(
            message_delivery_callback_required=False,
            message_delivery_callback_public_url="",
            message_delivery_callback_token="",
        ),
        repository,
    )

    assert service.enabled is False


def test_sop_delivery_callback_preserves_existing_send_audit(tmp_path) -> None:
    _, repository = _service(tmp_path)
    repository.create_sop_event(
        {
            "event_id": "sop-event-1",
            "event_type": "sop_friend_added_schedule_batch",
            "source": "test",
        }
    )
    task = repository.create_sop_send_task(
        event_id="sop-event-1",
        idempotency_key="sop-task-1",
        customer_id="customer-1",
        external_userid="external-1",
        corp_id="corp-1",
        user_id="user-1",
        wechat="wechat-1",
        sop_pack_id="pack-1",
        sop_pack_name="pack",
        reply_messages=[{"type": "text", "content": "hello"}],
        status="sending",
    )
    repository.update_sop_send_task(
        task["id"],
        status="sending",
        send_payload={"decision": {"decision": "send"}},
        send_response={"platform_request_id": "platform-request-1"},
    )
    service = SopDeliveryCompatibilityService(repository=repository)

    service.finalize_message_delivery(
        {
            "status": "send_succeeded",
            "confirmed_at": "2026-08-22T10:00:00+08:00",
            "source_context": {"sop_send_task_id": task["id"]},
        }
    )

    finalized = repository.get_sop_send_task(task["id"])
    assert finalized["status"] == "sent"
    assert finalized["send_payload"]["decision"]["decision"] == "send"
    assert finalized["send_response"]["platform_request_id"] == "platform-request-1"
    assert finalized["send_response"]["message_delivery"]["status"] == "send_succeeded"


def test_sop_task_partial_update_does_not_erase_send_audit(tmp_path) -> None:
    _, repository = _service(tmp_path)
    repository.create_sop_event(
        {"event_id": "sop-event-2", "event_type": "platform_sop_task", "source": "test"}
    )
    task = repository.create_sop_send_task(
        event_id="sop-event-2",
        idempotency_key="sop-task-2",
        customer_id="customer-2",
        external_userid="external-2",
        corp_id="corp-1",
        user_id="user-1",
        wechat="wechat-1",
        sop_pack_id="pack-2",
        sop_pack_name="pack",
        reply_messages=[{"type": "text", "content": "hello"}],
        status="sending",
    )
    repository.update_sop_send_task(
        task["id"],
        status="sending",
        send_payload={"decision": {"decision": "send"}},
        send_response={"accepted": True},
    )

    repository.update_sop_send_task(task["id"], status="sent", sent_at="2026-08-22T10:00:00+08:00")

    finalized = repository.get_sop_send_task(task["id"])
    assert finalized["send_payload"] == {"decision": {"decision": "send"}}
    assert finalized["send_response"] == {"accepted": True}


def test_platform_sop_callback_reports_rule_data_and_preserves_audit() -> None:
    class Repository:
        def __init__(self) -> None:
            self.task = {
                "id": "local-task-1",
                "event_id": "platform-event-1",
                "idempotency_key": "platform-sop:platform-task-1",
                "status": "sending",
                "send_payload": {
                    "decision": {
                        "decision": "send",
                        "sceneCode": "normal_activity_price",
                        "remark": "send",
                        "reply_messages": [{"type": "text", "content": "hello"}],
                    }
                },
                "send_response": {"accepted": True},
            }
            self.event_statuses: list[str] = []

        def get_sop_send_task(self, task_id: str) -> dict:
            return dict(self.task) if task_id == self.task["id"] else {}

        def get_sop_send_task_by_idempotency_key(self, key: str) -> dict:
            return dict(self.task) if key == self.task["idempotency_key"] else {}

        def get_sop_event(self, event_id: str) -> dict:
            return {
                "event_id": event_id,
                "raw_payload": {"platform_task": {"taskId": "platform-task-1"}},
            }

        def update_sop_send_task(self, task_id: str, **changes) -> dict:
            assert task_id == self.task["id"]
            for key, value in changes.items():
                if value is not None:
                    self.task[key] = value
            return dict(self.task)

        def update_sop_event_status(self, event_id: str, *, status: str, **_kwargs) -> dict:
            self.event_statuses.append(status)
            return {"event_id": event_id, "status": status}

    class PlatformClient:
        def __init__(self) -> None:
            self.rule_calls: list[dict] = []
            self.consume_calls: list[dict] = []

        async def service_rule_data(self, **kwargs) -> dict:
            self.rule_calls.append(kwargs)
            return {"code": 0}

        async def consume(self, **kwargs) -> dict:
            self.consume_calls.append(kwargs)
            return {"data": {"status": kwargs["status"]}}

    repository = Repository()
    platform_client = PlatformClient()
    service = SopPlatformTaskService.__new__(SopPlatformTaskService)
    service.repository = repository
    service.platform_client = platform_client
    service.settings = SimpleNamespace(sop_platform_shadow_mode=False)
    service._terminal_ids = set()
    service._terminal_order = []
    service._terminal_reack_at = {}

    asyncio.run(
        service.finalize_message_delivery(
            {
                "status": "send_succeeded",
                "confirmed_at": "2026-08-22T10:00:00+08:00",
                "source_context": {
                    "sop_send_task_id": "local-task-1",
                    "sop_event_id": "platform-event-1",
                    "platform_task_id": "platform-task-1",
                },
            }
        )
    )

    assert len(platform_client.rule_calls) == 1
    assert platform_client.consume_calls[0]["status"] == 30
    rule_data_audit = repository.task["send_payload"]["rule_data_response"]
    assert rule_data_audit["rule_data_response"] == {"code": 0}
    assert rule_data_audit["rule_data_request"]["taskId"] == "platform-task-1"
    assert repository.task["send_response"]["accepted"] is True
    assert repository.task["send_response"]["message_delivery"]["status"] == "send_succeeded"


def test_first_day_outreach_failure_callback_closes_plan_and_run() -> None:
    class Repository:
        def __init__(self) -> None:
            self.task = {
                "id": "outreach-task-1",
                "plan_id": "outreach-plan-1",
                "customer_id": "customer-1",
            }
            self.plan_status = "active"
            self.skipped = 0
            self.run_updates: list[dict] = []
            self.events: list[str] = []

        def get_outreach_task(self, task_id: str) -> dict:
            return dict(self.task) if task_id == self.task["id"] else {}

        def get_outreach_plan(self, _plan_id: str) -> dict:
            return {
                "plan": {
                    "id": "outreach-plan-1",
                    "source_snapshot": {
                        "workflow_run_id": "run-1",
                        "trigger_context": {"trigger_type": "first_day_opened_silence"},
                    },
                }
            }

        def update_outreach_task(self, _task_id: str, **changes) -> dict:
            self.task.update(changes)
            return dict(self.task)

        def skip_remaining_outreach_tasks(self, *_args, **_kwargs) -> int:
            self.skipped += 1
            return 1

        def update_outreach_plan_status(self, _plan_id: str, status: str) -> dict:
            self.plan_status = status
            return {"status": status}

        def add_outreach_event(self, **kwargs) -> dict:
            self.events.append(kwargs["event_type"])
            return {}

        def get_first_day_outreach_run(self, *_args, **_kwargs) -> dict:
            return {"started_at": "2026-08-22T01:00:00+00:00"}

        def update_first_day_outreach_run(self, _run_id: str, **changes) -> dict:
            self.run_updates.append(changes)
            return changes

    repository = Repository()
    service = OutreachService(
        repository=repository,
        model_client=object(),
        system_client=object(),
    )

    service.finalize_message_delivery(
        {
            "status": "send_failed",
            "error_message": "platform rejected",
            "source_context": {"outreach_task_id": "outreach-task-1"},
        }
    )

    assert repository.task["status"] == "failed"
    assert repository.skipped == 1
    assert repository.plan_status == "cancelled"
    assert repository.run_updates[-1]["reason_code"] == "message_delivery_failed"
    assert repository.run_updates[-1]["status"] == "failed"
