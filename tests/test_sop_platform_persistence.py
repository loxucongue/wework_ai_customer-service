from __future__ import annotations

from app.config import Settings
from app.services.storage.repositories import AppRepository
from app.services.storage.sqlite_store import SQLiteStore


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
