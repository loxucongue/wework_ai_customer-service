from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.services.storage.repositories import AppRepository
from app.services.storage.sqlite_store import SQLiteStore


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
    _platform_sent_task(repository, platform_task_id="206", sent_at=(now - timedelta(minutes=1)).isoformat(), status="completed_without_send")

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
