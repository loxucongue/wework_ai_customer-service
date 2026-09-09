from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.routers.callbacks import create_callbacks_router  # noqa: E402
from app.services.message_delivery import MessageDeliveryService  # noqa: E402
from app.services.memory_store import CustomerMemoryStore  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402
from app.services.storage.serialization import dumps  # noqa: E402
from app.services.v3_reply_recovery_delivery import (  # noqa: E402
    V3ReplyRecoveryDeliveryFinalizer,
)


def _stack(tmp_path: Path) -> tuple[Settings, AppRepository, MessageDeliveryService]:
    settings = Settings().model_copy(
        update={
            "aics_storage_backend": "sqlite",
            "db_path": tmp_path / "recovery-delivery.db",
            "message_delivery_callback_token": "callback-secret",
        }
    )
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    return settings, repository, MessageDeliveryService(settings, repository)


def _seed_run(
    repository: AppRepository,
    *,
    request_id: str,
    generation_status: str,
    recovery_dispatch_id: str = "",
) -> None:
    conversation_id = f"conversation-{request_id}"
    with repository.store.connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations
                (id, customer_id, external_userid, corp_id, user_id, wechat,
                 title, created_at, updated_at)
            VALUES (?, 'customer-1', 'external-1', 'corp-1', 'staff-1', 'SL8003',
                    'test', '2026-09-09T00:00:00+00:00', '2026-09-09T00:00:00+00:00')
            """,
            (conversation_id,),
        )
        conn.execute(
            """
            INSERT INTO runs
                (request_id, conversation_id, customer_id, generation_status,
                 recovery_kind, recovery_dispatch_id, recovery_error,
                 output_snapshot, created_at)
            VALUES (?, ?, 'customer-1', ?, 'reply_timeout', ?, '', ?,
                    '2026-09-09T00:00:00+00:00')
            """,
            (
                request_id,
                conversation_id,
                generation_status,
                recovery_dispatch_id,
                dumps({"v3_recovery_payload": {"content": "original"}}),
            ),
        )


def _prepare_dispatch(
    delivery: MessageDeliveryService,
    *,
    request_id: str,
    message_count: int = 1,
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prepared = delivery.prepare_dispatch(
        source_channel="v3_reply_recovery",
        source_kind="v3_reply_recovery",
        source_request_id=request_id,
        source_task_id=request_id,
        conversation_id=f"conversation-{request_id}",
        identity={
            "corp_id": "corp-1",
            "customer_id": "customer-1",
            "external_userid": "external-1",
            "user_id": "staff-1",
            "wechat": "SL8003",
        },
        plan_id="",
        task_id=request_id,
        reply_messages=[
            {"type": "text", "order": index + 1, "content": f"recovery-{index + 1}"}
            for index in range(message_count)
        ],
        source_context=source_context or {"original_request_id": request_id},
        idempotency_key=f"v3-recovery:{request_id}",
    )
    delivery.record_submission(prepared["dispatch_id"], status="platform_accepted")
    return delivery.get_dispatch_by_idempotency_key(f"v3-recovery:{request_id}")


def _client(
    settings: Settings,
    repository: AppRepository,
    delivery: MessageDeliveryService,
) -> TestClient:
    services = SimpleNamespace(
        repository=repository,
        message_delivery_service=delivery,
        v3_reply_recovery_delivery_finalizer=V3ReplyRecoveryDeliveryFinalizer(
            repository,
            CustomerMemoryStore(settings, repository),
        ),
    )
    app = FastAPI()
    app.include_router(create_callbacks_router(settings, services))
    return TestClient(app)


def _callback(
    client: TestClient,
    *,
    event_id: str,
    dispatch: dict[str, Any],
    status: str,
    items: list[dict[str, Any]] | None = None,
) -> Any:
    return client.post(
        "/callbacks/v1/message-delivery",
        headers={"X-Callback-Token": "callback-secret"},
        json={
            "event_id": event_id,
            "dispatch_id": dispatch["id"],
            "task_id": dispatch["task_id"],
            "status": status,
            "occurred_at": "2026-09-09T00:01:00+00:00",
            "error_code": "platform_rejected" if status != "send_succeeded" else "",
            "error_message": "delivery failed" if status != "send_succeeded" else "",
            "items": items or [],
        },
    )


def test_success_callback_is_the_only_completion_and_is_idempotent(tmp_path: Path) -> None:
    settings, repository, delivery = _stack(tmp_path)
    _seed_run(repository, request_id="request-success", generation_status="recovery_claimed")
    dispatch = _prepare_dispatch(delivery, request_id="request-success")
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE runs SET recovery_dispatch_id=? WHERE request_id='request-success'",
            (dispatch["id"],),
        )
    client = _client(settings, repository, delivery)

    first = _callback(
        client,
        event_id="event-success",
        dispatch=dispatch,
        status="send_succeeded",
    )
    duplicate = _callback(
        client,
        event_id="event-success",
        dispatch=dispatch,
        status="send_succeeded",
    )

    assert first.status_code == 200
    assert first.json()["data"]["finalized"] is True
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["duplicate"] is True
    run = repository.get_v3_generation_result(request_id="request-success")
    assert run["generation_status"] == "recovered"
    stored = repository.get_run("request-success")["run"]
    assert stored["output_snapshot"]["v3_recovery_delivery"]["status"] == "send_succeeded"
    with repository.store.connect() as conn:
        messages = conn.execute(
            "SELECT role, content FROM messages WHERE request_id='request-success' ORDER BY created_at"
        ).fetchall()
    assert [(row["role"], row["content"]) for row in messages] == [("assistant", "recovery-1")]


def test_success_callback_records_declared_activity_stage_once(tmp_path: Path) -> None:
    settings, repository, delivery = _stack(tmp_path)
    request_id = "request-stage"
    sales_contact_key = "sales-contact-stage"
    _seed_run(repository, request_id=request_id, generation_status="recovery_claimed")
    dispatch = _prepare_dispatch(
        delivery,
        request_id=request_id,
        source_context={
            "original_request_id": request_id,
            "memory_persist_allowed": True,
            "sales_contact_key": sales_contact_key,
            "sales_stage_record": {
                "stage": "activity_offer",
                "action_type": "explain_activity",
            },
        },
    )
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE runs SET recovery_dispatch_id=? WHERE request_id=?",
            (dispatch["id"], request_id),
        )
    client = _client(settings, repository, delivery)

    first = _callback(
        client,
        event_id="event-stage",
        dispatch=dispatch,
        status="send_succeeded",
    )
    duplicate = _callback(
        client,
        event_id="event-stage",
        dispatch=dispatch,
        status="send_succeeded",
    )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    memory = CustomerMemoryStore(settings, repository).load(sales_contact_key)
    stage_events = [
        item
        for item in memory.get("history_events") or []
        if item.get("event_type") == "v3_sales_stage_delivered"
    ]
    assert len(stage_events) == 1
    assert stage_events[0]["facts"]["stage"] == "activity_offer"


def test_failed_callback_never_records_sales_stage(tmp_path: Path) -> None:
    settings, repository, delivery = _stack(tmp_path)
    request_id = "request-stage-failed"
    sales_contact_key = "sales-contact-stage-failed"
    _seed_run(repository, request_id=request_id, generation_status="recovery_claimed")
    dispatch = _prepare_dispatch(
        delivery,
        request_id=request_id,
        source_context={
            "original_request_id": request_id,
            "memory_persist_allowed": True,
            "sales_contact_key": sales_contact_key,
            "sales_stage_record": {
                "stage": "activity_offer",
                "action_type": "explain_activity",
            },
        },
    )
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE runs SET recovery_dispatch_id=? WHERE request_id=?",
            (dispatch["id"], request_id),
        )

    response = _callback(
        _client(settings, repository, delivery),
        event_id="event-stage-failed",
        dispatch=dispatch,
        status="send_failed",
    )

    assert response.status_code == 200
    memory = CustomerMemoryStore(settings, repository).load(sales_contact_key)
    assert not any(
        item.get("event_type") == "v3_sales_stage_delivered"
        for item in memory.get("history_events") or []
    )


def test_terminal_failure_callback_moves_active_run_to_manual_review(tmp_path: Path) -> None:
    settings, repository, delivery = _stack(tmp_path)
    _seed_run(repository, request_id="request-failed", generation_status="recovery_claimed")
    dispatch = _prepare_dispatch(delivery, request_id="request-failed")
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE runs SET recovery_dispatch_id=? WHERE request_id='request-failed'",
            (dispatch["id"],),
        )

    response = _callback(
        _client(settings, repository, delivery),
        event_id="event-failed",
        dispatch=dispatch,
        status="send_failed",
    )

    assert response.status_code == 200
    run = repository.get_v3_generation_result(request_id="request-failed")
    assert run["generation_status"] == "manual_review"
    assert run["recovery_kind"] == "manual_review"
    assert run["recovery_dispatch_id"] == dispatch["id"]
    assert "recovery_dispatch_send_failed" in run["recovery_error"]
    assert "delivery failed" in run["recovery_error"]
    stored = repository.get_run("request-failed")["run"]
    assert "v3_recovery_payload" not in stored["output_snapshot"]


def test_partial_failure_and_callback_race_move_active_run_to_manual_review(tmp_path: Path) -> None:
    settings, repository, delivery = _stack(tmp_path)
    _seed_run(repository, request_id="request-race", generation_status="recovery_claimed")
    dispatch = _prepare_dispatch(delivery, request_id="request-race", message_count=2)
    items = [
        {
            "client_message_id": dispatch["items"][0]["client_message_id"],
            "status": "send_succeeded",
            "platform_message_id": "platform-1",
        },
        {
            "client_message_id": dispatch["items"][1]["client_message_id"],
            "status": "send_failed",
            "error_code": "item_failed",
            "error_message": "second message failed",
        },
    ]

    response = _callback(
        _client(settings, repository, delivery),
        event_id="event-partial",
        dispatch=dispatch,
        status="partial_failed",
        items=items,
    )

    assert response.status_code == 200
    run = repository.get_v3_generation_result(request_id="request-race")
    assert run["generation_status"] == "manual_review"
    assert run["recovery_dispatch_id"] == dispatch["id"]
    assert "recovery_dispatch_partial_failed" in run["recovery_error"]
    assert "second message failed" in run["recovery_error"]
    late_complete = repository.complete_v3_fallback_recovery(
        request_id="request-race",
        reply_messages=[{"type": "text", "order": 1, "content": "must not overwrite failure"}],
        dispatch_id=dispatch["id"],
    )
    assert late_complete["status"] == "not_recoverable"
    assert repository.get_v3_generation_result(request_id="request-race")["generation_status"] == "manual_review"


def test_callback_source_request_cannot_mutate_another_recovery_run(tmp_path: Path) -> None:
    settings, repository, delivery = _stack(tmp_path)
    _seed_run(repository, request_id="request-owner", generation_status="recovered")
    dispatch = _prepare_dispatch(delivery, request_id="request-other")
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE runs SET recovery_dispatch_id=? WHERE request_id='request-owner'",
            (dispatch["id"],),
        )

    response = _callback(
        _client(settings, repository, delivery),
        event_id="event-wrong-owner",
        dispatch=dispatch,
        status="send_failed",
    )

    assert response.status_code == 404
    run = repository.get_v3_generation_result(request_id="request-owner")
    assert run["generation_status"] == "recovered"
    assert repository.get_message_dispatch(dispatch["id"])["finalized_at"] == ""


def test_repository_rejects_a_different_dispatch_for_the_same_run(tmp_path: Path) -> None:
    _, repository, _ = _stack(tmp_path)
    _seed_run(
        repository,
        request_id="request-mismatch",
        generation_status="recovered",
        recovery_dispatch_id="dispatch-owner",
    )

    result = repository.finalize_v3_recovery_delivery(
        request_id="request-mismatch",
        dispatch_id="dispatch-other",
        delivery_status="send_failed",
        error="must not apply",
    )

    assert result["status"] == "dispatch_mismatch"
    run = repository.get_v3_generation_result(request_id="request-mismatch")
    assert run["generation_status"] == "recovered"
    assert run["recovery_dispatch_id"] == "dispatch-owner"
