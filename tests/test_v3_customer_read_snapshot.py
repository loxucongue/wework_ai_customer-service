from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402


def _repository(tmp_path: Path) -> AppRepository:
    settings = Settings().model_copy(
        update={"aics_storage_backend": "sqlite", "db_path": tmp_path / "state.db"}
    )
    store = SQLiteStore(settings)
    store.initialize()
    return AppRepository(store)


def _insert_sent_task(
    repository: AppRepository,
    *,
    task_id: str,
    external_userid: str,
    wechat: str,
    pack_id: str,
    category: str,
    payload: str = "{}",
) -> None:
    with repository.store.connect() as conn:
        conn.execute(
            """
            INSERT INTO sop_events
                (id, event_id, event_type, received_at, updated_at)
            VALUES (?, ?, 'test', ?, ?)
            """,
            (
                f"log-{task_id}",
                f"event-{task_id}",
                "2026-09-12T00:00:00+00:00",
                "2026-09-12T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO sop_send_tasks
                (id, event_id, idempotency_key, customer_id, external_userid,
                 corp_id, wechat, sop_pack_id, sop_category, status,
                 send_payload_json, created_at, updated_at, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'sent', ?, ?, ?, ?)
            """,
            (
                task_id,
                f"event-{task_id}",
                f"key-{task_id}",
                "platform-customer-1",
                external_userid,
                "corp-1",
                wechat,
                pack_id,
                category,
                payload,
                "2026-09-12T00:00:00+00:00",
                "2026-09-12T00:00:00+00:00",
                "2026-09-12T00:00:00+00:00",
            ),
        )


def test_reply_snapshot_matches_independent_reads_and_uses_one_connection(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    sales_contact_key = "corp-1|sl8003|external-1"
    repository.save_memory(
        sales_contact_key,
        {
            "portrait": {"skin": "dry"},
            "basic_info": {"city": "杭州"},
            "lifecycle_stage": "interested",
            "history_events": [
                {
                    "event_id": "history-1",
                    "event_type": "effect_delivered",
                    "facts": {"source": "test"},
                }
            ],
        },
    )
    _insert_sent_task(
        repository,
        task_id="task-1",
        external_userid="external-1",
        wechat="SL8003",
        pack_id="pack-1",
        category="effect",
        payload=(
            '{"selected_sop_pack_ids":["pack-2"],'
            '"selected_sop_categories":["activity"]}'
        ),
    )
    expected_memory = repository.load_memory(sales_contact_key)
    expected_pack_ids = repository.list_sent_sop_pack_ids_for_customer(
        customer_id="platform-customer-1",
        external_userid="external-1",
        corp_id="corp-1",
        wechat="SL8003",
    )
    expected_categories = repository.list_sent_sop_categories_for_customer(
        customer_id="platform-customer-1",
        external_userid="external-1",
        corp_id="corp-1",
        wechat="SL8003",
    )

    connect_calls = 0
    original_connect = repository.store.connect

    @contextmanager
    def counted_connect() -> Iterator[Any]:
        nonlocal connect_calls
        connect_calls += 1
        with original_connect() as conn:
            yield conn

    repository.store.connect = counted_connect  # type: ignore[method-assign]
    snapshot = repository.load_reply_customer_snapshot(
        sales_contact_key=sales_contact_key,
        customer_id="platform-customer-1",
        external_userid="external-1",
        corp_id="corp-1",
        wechat="SL8003",
    )

    assert connect_calls == 1
    assert snapshot["memory"] == expected_memory
    assert snapshot["completed_pack_ids"] == expected_pack_ids
    assert snapshot["completed_categories"] == expected_categories
    assert snapshot["storage_timing"]["connection_count"] == 1
    assert snapshot["storage_timing"]["statement_count"] == 3


def test_reply_snapshot_never_crosses_wechat_or_external_customer_scope(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _insert_sent_task(
        repository,
        task_id="same-external-other-wechat",
        external_userid="external-1",
        wechat="SL9999",
        pack_id="wrong-wechat",
        category="wrong-wechat-category",
    )
    _insert_sent_task(
        repository,
        task_id="same-wechat-other-external",
        external_userid="external-2",
        wechat="SL8003",
        pack_id="wrong-customer",
        category="wrong-customer-category",
    )
    _insert_sent_task(
        repository,
        task_id="matching",
        external_userid="external-1",
        wechat="SL8003",
        pack_id="matching-pack",
        category="matching-category",
    )

    snapshot = repository.load_reply_customer_snapshot(
        sales_contact_key="corp-1|sl8003|external-1",
        customer_id="platform-customer-1",
        external_userid="external-1",
        corp_id="corp-1",
        wechat="sl8003",
    )

    assert snapshot["completed_pack_ids"] == ["matching-pack"]
    assert snapshot["completed_categories"] == ["matching-category"]


def test_reply_snapshot_removes_two_remote_connection_waits(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    original_connect = repository.store.connect

    @contextmanager
    def delayed_connect() -> Iterator[Any]:
        time.sleep(0.03)
        with original_connect() as conn:
            yield conn

    repository.store.connect = delayed_connect  # type: ignore[method-assign]
    started = time.perf_counter()
    repository.load_memory("scope-1")
    repository.list_sent_sop_pack_ids_for_customer(
        customer_id="customer-1",
        external_userid="external-1",
        corp_id="corp-1",
        wechat="SL8003",
    )
    repository.list_sent_sop_categories_for_customer(
        customer_id="customer-1",
        external_userid="external-1",
        corp_id="corp-1",
        wechat="SL8003",
    )
    independent_elapsed = time.perf_counter() - started

    started = time.perf_counter()
    snapshot = repository.load_reply_customer_snapshot(
        sales_contact_key="scope-1",
        customer_id="customer-1",
        external_userid="external-1",
        corp_id="corp-1",
        wechat="SL8003",
    )
    snapshot_elapsed = time.perf_counter() - started

    assert independent_elapsed >= 0.09
    assert snapshot_elapsed < independent_elapsed - 0.04
    assert snapshot["storage_timing"]["connection_acquire_ms"] >= 25
