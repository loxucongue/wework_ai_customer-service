from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.services.storage.repositories import AppRepository
from app.services.storage.sqlite_store import SQLiteStore
from app.services.sop_platform_task_service import _platform_run_status, _platform_task_log_item


def _platform_sent_task(
    repository: AppRepository,
    *,
    platform_task_id: str,
    corp_id: str = "corp",
    wechat: str = "sl8003",
    external_userid: str = "external",
    customer_id: str = "customer",
    sent_at: str,
    status: str = "sent",
) -> dict:
    event_id = f"platform_sop_task:{platform_task_id}"
    repository.create_sop_event(
        {
            "event_id": event_id,
            "event_type": "platform_sop_task",
            "source": "test",
            "platform_task": {"taskId": platform_task_id},
        }
    )
    task = repository.create_sop_send_task(
        event_id=event_id,
        idempotency_key=f"platform-sop:{platform_task_id}",
        customer_id=customer_id,
        external_userid=external_userid,
        corp_id=corp_id,
        user_id="user",
        wechat=wechat,
        sop_pack_id="pack",
        sop_pack_name="pack",
        reply_messages=[{"type": "text", "content": "hello"}],
    )
    return repository.update_sop_send_task(task["id"], status=status, sent_at=sent_at)


def test_customer_reply_links_only_latest_prior_sent_platform_task_in_same_scope(tmp_path) -> None:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "sop.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    now = datetime.now(timezone.utc)
    earlier = (now - timedelta(minutes=10)).isoformat()
    latest = (now - timedelta(minutes=5)).isoformat()
    replied_at = now.isoformat()

    _platform_sent_task(repository, platform_task_id="201", sent_at=earlier)
    _platform_sent_task(repository, platform_task_id="202", sent_at=latest)
    _platform_sent_task(repository, platform_task_id="203", wechat="sl9001", sent_at=(now - timedelta(minutes=1)).isoformat())
    _platform_sent_task(repository, platform_task_id="204", external_userid="other", sent_at=(now - timedelta(minutes=1)).isoformat())
    _platform_sent_task(repository, platform_task_id="205", sent_at=(now + timedelta(minutes=1)).isoformat())
    _platform_sent_task(repository, platform_task_id="206", sent_at="", status="completed_without_send")

    matched = repository.find_latest_platform_task_for_customer_reply(
        customer_id="customer",
        external_userid="external",
        corp_id="corp",
        wechat="sl8003",
        replied_at=replied_at,
    )

    assert matched["task_id"] == "202"


def test_customer_reply_platform_task_lookup_requires_full_sales_contact_scope(tmp_path) -> None:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "sop.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)

    assert repository.find_latest_platform_task_for_customer_reply(
        customer_id="customer",
        external_userid="external",
        corp_id="",
        wechat="sl8003",
        replied_at=datetime.now(timezone.utc).isoformat(),
    ) == {}


def test_prepare_platform_sop_send_updates_event_and_task_together(tmp_path) -> None:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "sop.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    event_id = "platform_sop_task:101"
    repository.create_sop_event(
        {
            "event_id": event_id,
            "event_type": "platform_sop_task",
            "source": "test",
            "platform_task": {"taskId": "101"},
        }
    )
    repository.create_sop_send_task(
        event_id=event_id,
        idempotency_key="platform-sop:101",
        customer_id="customer",
        external_userid="external",
        corp_id="corp",
        user_id="user",
        wechat="wechat",
        sop_pack_id="pack",
        sop_pack_name="pack",
        reply_messages=[{"type": "text", "content": {"text": "hello"}}],
    )

    task = repository.prepare_platform_sop_send(
        event_id=event_id,
        idempotency_key="platform-sop:101",
        send_payload={"decision": {"decision": "send"}},
    )

    assert task["status"] == "sending"
    assert task["send_payload"] == {"decision": {"decision": "send"}}
    assert repository.get_sop_event(event_id)["status"] == "platform_processing"


def test_complete_platform_sop_task_without_send_updates_event_and_task_together(tmp_path) -> None:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "sop.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    event_id = "platform_sop_task:102"
    repository.create_sop_event(
        {
            "event_id": event_id,
            "event_type": "platform_sop_task",
            "source": "test",
            "platform_task": {"taskId": "102"},
        }
    )
    task = repository.create_sop_send_task(
        event_id=event_id,
        idempotency_key="platform-sop:102",
        customer_id="customer",
        external_userid="external",
        corp_id="corp",
        user_id="user",
        wechat="wechat",
        sop_pack_id="pack",
        sop_pack_name="pack",
        reply_messages=[],
        status="platform_queued",
    )

    repository.complete_platform_sop_task_without_send(
        platform_task_id="102",
        send_payload={"reason": "human_takeover"},
    )

    completed = repository.get_sop_send_task(task["id"])
    assert completed["status"] == "completed_without_send"
    assert completed["send_payload"] == {"reason": "human_takeover"}
    assert repository.get_sop_event(event_id)["status"] == "platform_completed"


def test_successful_send_cannot_be_downgraded_to_no_send(tmp_path) -> None:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "sop.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    event_id = "platform_sop_task:103"
    repository.create_sop_event(
        {"event_id": event_id, "event_type": "platform_sop_task", "source": "test"}
    )
    task = repository.create_sop_send_task(
        event_id=event_id,
        idempotency_key="platform-sop:103",
        customer_id="customer",
        external_userid="external",
        corp_id="corp",
        user_id="user",
        wechat="wechat",
        sop_pack_id="pack",
        sop_pack_name="pack",
        reply_messages=[],
    )
    sent_payload = {"decision": {"decision": "send"}}
    sent_response = {
        "data": {
            "send_status": "accepted",
            "delivery_status": "platform_accepted",
            "system_msgid": "msg-103",
        }
    }
    repository.update_sop_send_task(
        task["id"],
        status="sent",
        send_payload=sent_payload,
        send_response=sent_response,
        sent_at="2026-09-05T07:07:51+00:00",
    )

    downgraded = repository.update_sop_send_task(
        task["id"],
        status="completed_without_send",
        send_payload={"decision": {"decision": "no_send", "reason": "invalid_message_content"}},
    )

    assert downgraded["status"] == "sent"
    assert downgraded["send_payload"] == sent_payload
    assert downgraded["send_response"] == sent_response


def test_successful_send_cannot_be_downgraded_by_recovery_failure(tmp_path) -> None:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "sop.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    event_id = "platform_sop_task:recovery-race"
    repository.create_sop_event(
        {"event_id": event_id, "event_type": "platform_sop_task", "source": "test"}
    )
    task = repository.create_sop_send_task(
        event_id=event_id,
        idempotency_key="platform-sop:recovery-race",
        customer_id="customer",
        external_userid="external",
        corp_id="corp",
        user_id="user",
        wechat="wechat",
        sop_pack_id="pack",
        sop_pack_name="pack",
        reply_messages=[],
    )
    sent_response = {
        "data": {
            "delivery_status": "platform_accepted",
            "system_msgids": ["msg-1", "msg-2"],
        }
    }
    repository.update_sop_send_task(
        task["id"],
        status="sent",
        send_payload={"processing_mode": "deterministic_customer_gate"},
        send_response=sent_response,
        sent_at="2026-09-09T01:00:14+00:00",
    )

    downgraded = repository.update_sop_send_task(
        task["id"],
        status="processing_retry",
        send_payload={"platform_task_id": "recovery-race"},
        error="TimeoutError: total timeout 20.0s",
    )

    assert downgraded["status"] == "sent"
    assert downgraded["send_payload"] == {"processing_mode": "deterministic_customer_gate"}
    assert downgraded["send_response"] == sent_response
    assert downgraded["error"] == ""


def test_alert_delivery_claim_allows_one_sender_and_reclaims_only_stale_lease(tmp_path) -> None:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "sop.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    event_id = "sop_failure_alert:claim"
    repository.create_sop_event(
        {"event_id": event_id, "event_type": "sop_failure_alert", "source": "test"}
    )

    assert repository.claim_sop_failure_alert_delivery(
        event_id,
        stale_before=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
    ) is True
    assert repository.claim_sop_failure_alert_delivery(
        event_id,
        stale_before=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
    ) is False
    assert repository.claim_sop_failure_alert_delivery(
        event_id,
        stale_before=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
    ) is True


def test_atomic_no_send_completion_preserves_existing_send_success(tmp_path) -> None:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "sop.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    event_id = "platform_sop_task:104"
    repository.create_sop_event(
        {"event_id": event_id, "event_type": "platform_sop_task", "source": "test"}
    )
    task = repository.create_sop_send_task(
        event_id=event_id,
        idempotency_key="platform-sop:104",
        customer_id="customer",
        external_userid="external",
        corp_id="corp",
        user_id="user",
        wechat="wechat",
        sop_pack_id="pack",
        sop_pack_name="pack",
        reply_messages=[],
    )
    repository.update_sop_send_task(
        task["id"],
        status="sent",
        send_response={"data": {"delivery_status": "delivered"}},
        sent_at="2026-09-05T07:07:51+00:00",
    )

    repository.complete_platform_sop_task_without_send(
        platform_task_id="104", send_payload={"reason": "invalid_message_content"}
    )

    preserved = repository.get_sop_send_task(task["id"])
    assert preserved["status"] == "sent"
    assert preserved["send_payload"] == {}
    assert repository.get_sop_event(event_id)["status"] == "platform_completed"


def test_admin_log_prefers_send_evidence_over_stale_no_send_status() -> None:
    item = _platform_task_log_item(
        platform_task={"taskId": "80473", "customerId": "15171717"},
        local_record={
            "event_status": "platform_completed",
            "task_status": "completed_without_send",
            "sent_at": "2026-09-05T07:07:51+00:00",
            "send_payload": {
                "decision": {"decision": "no_send", "reason": "invalid_message_content"}
            },
            "send_response": {
                "data": {
                    "send_status": "accepted",
                    "delivery_status": "platform_accepted",
                    "system_msgid": "msg-80473",
                }
            },
        },
        platform_visible=False,
    )

    assert item["task_status"] == "sent"
    assert item["decision"] == "send"
    assert item["bucket"] == "sent"
    assert item["decision_reason"] == "successful_send_evidence"


def test_admin_log_treats_post_send_legacy_error_as_completed_audit_history() -> None:
    item = _platform_task_log_item(
        platform_task={"taskId": "82716", "customerId": "15215324"},
        local_record={
            "event_status": "platform_legacy_quarantined",
            "event_error": "legacy_execution_disabled",
            "task_status": "processing_retry",
            "task_error": "RuntimeError: model HTTP 503",
            "sent_at": "2026-09-08T18:10:56+00:00",
            "send_payload": {"platform_task_id": "82716"},
            "send_response": {
                "data": {
                    "delivery_status": "platform_accepted",
                    "system_msgid": "msg-82716",
                }
            },
        },
        platform_visible=False,
    )

    task = {
        "task_id": item["task_id"],
        "task_status": item["task_status"],
        "event_status": item["event_status"],
        "consume_status": None,
        "error": item["error"],
    }

    assert item["task_status"] == "sent"
    assert item["error"] == ""
    assert item["raw"]["local_event"]["task_error"] == "RuntimeError: model HTTP 503"
    assert _platform_run_status(version="legacy_single", representative=item, tasks=[task]) == "completed"


def test_recovery_query_skips_deferred_events_unless_explicitly_requested(tmp_path) -> None:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "sop.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    event_id = "platform_sop_task:deferred"
    repository.create_sop_event(
        {"event_id": event_id, "event_type": "platform_sop_task", "source": "test"}
    )
    repository.schedule_sop_event_retry(
        event_id,
        status="platform_processing_retry",
        error="temporary",
        next_retry_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )

    assert repository.list_sop_events_by_statuses(
        ["platform_processing_retry"], event_type="platform_sop_task"
    ) == []
    assert [
        item["event_id"]
        for item in repository.list_sop_events_by_statuses(
            ["platform_processing_retry"],
            event_type="platform_sop_task",
            include_deferred=True,
        )
    ] == [event_id]


def test_duplicate_lookup_ignores_unconfirmed_or_sending_attempts(tmp_path) -> None:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "sop.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    event_id = "platform_sop_task:unknown"
    repository.create_sop_event(
        {"event_id": event_id, "event_type": "platform_sop_task", "source": "test"}
    )
    task = repository.create_sop_send_task(
        event_id=event_id,
        idempotency_key="platform-sop:unknown",
        send_once_key="same-content",
        customer_id="customer",
        external_userid="external",
        corp_id="corp",
        user_id="user",
        wechat="wechat",
        sop_pack_id="pack",
        sop_pack_name="pack",
        reply_messages=[],
    )
    repository.update_sop_send_task(
        task["id"],
        status="sending",
        send_response={"data": {"delivery_status": "submission_unknown"}},
        error="active_send_timeout_unknown_result",
    )

    assert repository.find_sop_send_task_delivery_duplicate("same-content") == {}


def test_platform_task_records_can_filter_unresolved_statuses_for_bulk_restore(tmp_path) -> None:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "sop.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    for task_id, status in (("blocked", "platform_sequence_blocked"), ("done", "platform_completed")):
        event_id = f"platform_sop_task:{task_id}"
        repository.create_sop_event(
            {
                "event_id": event_id,
                "event_type": "platform_sop_task",
                "source": "test",
                "platform_task": {"taskId": task_id},
            }
        )
        repository.create_sop_send_task(
            event_id=event_id,
            idempotency_key=f"platform-sop:{task_id}",
            customer_id="customer",
            external_userid="external",
            corp_id="corp",
            user_id="user",
            wechat="wechat",
            sop_pack_id="pack",
            sop_pack_name="pack",
            reply_messages=[],
        )
        repository.update_sop_event_status(event_id, status=status)

    records = repository.list_platform_sop_task_records(
        limit=500,
        event_statuses=["platform_sequence_blocked"],
        oldest_first=True,
    )

    assert [record["event_id"] for record in records] == ["platform_sop_task:blocked"]
