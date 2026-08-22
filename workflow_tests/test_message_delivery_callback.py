from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas import MessageDeliveryCallback
from app.services.message_delivery import MessageDeliveryService
from app.services.storage import AppRepository
from app.services.storage.sqlite_store import SQLiteStore


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
