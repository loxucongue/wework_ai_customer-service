from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.routers.operations_admin import create_operations_admin_router  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402
from app.services.storage.mysql_schema import (  # noqa: E402
    EXPECTED_COLUMNS,
    EXPECTED_INDEXES,
    EXPECTED_UNIQUE_INDEXES,
)
from app.services.trace_logger import TraceLogger  # noqa: E402
from app.services.v3_reply_finalization_service import V3ReplyFinalizationService  # noqa: E402


def _migration_module():
    path = (
        PROJECT_ROOT
        / "ai_paths"
        / "migrations"
        / "versions"
        / "20260909_02_add_internal_work_items.py"
    )
    spec = importlib.util.spec_from_file_location("migration_20260909_02", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repository(tmp_path: Path) -> AppRepository:
    settings = Settings().model_copy(
        update={"aics_storage_backend": "sqlite", "db_path": tmp_path / "work-items.db"}
    )
    store = SQLiteStore(settings)
    store.initialize()
    return AppRepository(store)


def _followup(*, store_id: str = "store-1", detail_kind: str = "hours") -> dict:
    return {
        "schema_version": "store_fact_followup_v1",
        "internal_followup": {
            "task_type": "store_fact_followup",
            "status": "pending",
            "store_id": store_id,
            "detail_kind": detail_kind,
            "missing_fact_keys": ["business_hours"],
            "customer_send_authorized": False,
        },
    }


def _upsert(repository: AppRepository, *, request_id: str = "request-1", **kwargs: str) -> dict:
    return repository.upsert_store_fact_followup_work_item(
        followup=_followup(**kwargs),
        request_id=request_id,
        conversation_id=f"conversation-{request_id}",
        corp_id="corp-1",
        wechat="sl8003",
        external_userid=f"external-{request_id}",
        customer_id=f"customer-{request_id}",
    )


def test_store_fact_followup_is_persisted_and_worker_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)

    first = _upsert(repository)
    retry = _upsert(repository)
    second_customer = _upsert(repository, request_id="request-2")

    assert first["created"] is True
    assert retry["created"] is False
    assert retry["occurrence_count"] == 1
    assert second_customer["occurrence_count"] == 2
    listed = repository.list_internal_work_items(status="pending")
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["store_id"] == "store-1"
    assert item["detail_kind"] == "hours"
    assert item["missing_fact_keys"] == ["business_hours"]
    assert item["occurrence_count"] == 2
    assert item["source_request_id"] == "request-2"
    assert "source_request_ids" not in item["payload"]


def test_work_items_are_separated_by_store_and_detail_and_ignore_empty_followup(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    assert repository.upsert_store_fact_followup_work_item(
        followup={},
        request_id="request-empty",
        conversation_id="conversation-empty",
        corp_id="corp-1",
        wechat="sl8003",
        external_userid="external-empty",
        customer_id="customer-empty",
    )["status"] == "skipped"

    _upsert(repository, request_id="request-a", store_id="store-a")
    _upsert(repository, request_id="request-b", store_id="store-b")
    _upsert(repository, request_id="request-c", store_id="store-a", detail_kind="parking")

    assert repository.list_internal_work_items()["total"] == 3
    assert repository.list_internal_work_items(store_id="store-a")["total"] == 2
    assert repository.list_internal_work_items(detail_kind="parking")["total"] == 1


def test_internal_work_item_resolution_is_idempotent_and_not_retargetable(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    created = _upsert(repository)

    resolved = repository.update_internal_work_item(
        item_id=created["id"],
        status="resolved",
        resolved_by="operator-1",
        resolution_note="平台资料已补齐",
    )
    repeated = repository.update_internal_work_item(
        item_id=created["id"],
        status="resolved",
        resolved_by="operator-1",
        resolution_note="平台资料已补齐",
    )

    assert resolved["status"] == "updated"
    assert repeated["status"] == "unchanged"
    assert repeated["item"]["resolved_by"] == "operator-1"
    with pytest.raises(ValueError, match="already resolved"):
        repository.update_internal_work_item(
            item_id=created["id"], status="dismissed"
        )


def test_new_occurrence_reopens_resolved_store_fact_work_item(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    created = _upsert(repository, request_id="request-first")
    repository.update_internal_work_item(
        item_id=created["id"],
        status="resolved",
        resolved_by="operator",
        resolution_note="fact expected to be fixed",
    )

    reopened = _upsert(repository, request_id="request-after-resolution")

    assert reopened["status"] == "pending"
    assert reopened["created"] is False
    item = repository.list_internal_work_items(status="pending")["items"][0]
    assert item["occurrence_count"] == 2
    assert item["resolved_at"] == ""
    assert item["resolved_by"] == ""
    assert item["resolution_note"] == ""


def test_new_occurrence_does_not_reopen_dismissed_store_fact_work_item(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    created = _upsert(repository, request_id="request-first")
    repository.update_internal_work_item(
        item_id=created["id"],
        status="dismissed",
        resolved_by="operator",
        resolution_note="business chose not to provide this fact",
    )

    repeated = _upsert(repository, request_id="request-after-dismissal")

    assert repeated["status"] == "dismissed"
    assert repository.list_internal_work_items(status="pending")["total"] == 0


def test_mysql_contract_contains_internal_work_item_table_and_indexes() -> None:
    table = "aics_internal_work_items"
    assert "idempotency_key" in EXPECTED_COLUMNS[table]
    assert EXPECTED_INDEXES[table]["idx_aics_internal_work_items_queue"] == (
        "work_type",
        "status",
        "last_seen_at",
    )
    assert "uq_aics_internal_work_items_idempotency" in EXPECTED_UNIQUE_INDEXES[table]


def test_internal_work_item_migration_repairs_a_missing_index(monkeypatch) -> None:
    migration = _migration_module()
    indexes = {
        "idx_aics_internal_work_items_store_detail": {
            "name": "idx_aics_internal_work_items_store_detail",
            "column_names": ["store_id", "detail_kind", "status"],
            "unique": False,
        },
        "idx_aics_internal_work_items_contact": {
            "name": "idx_aics_internal_work_items_contact",
            "column_names": [
                "corp_id",
                "wechat",
                "external_userid",
                "customer_id",
                "last_seen_at",
            ],
            "unique": False,
        },
    }
    uniques = {
        "uq_aics_internal_work_items_idempotency": {
            "name": "uq_aics_internal_work_items_idempotency",
            "column_names": ["idempotency_key"],
        }
    }
    inspector = SimpleNamespace(
        get_indexes=lambda _table: list(indexes.values()),
        get_unique_constraints=lambda _table: list(uniques.values()),
    )
    created: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(
            create_index=lambda name, _table, columns, **_kwargs: created.append(
                (name, tuple(columns))
            ),
            create_unique_constraint=lambda *_args, **_kwargs: pytest.fail(
                "unique constraint must not be recreated"
            ),
        ),
    )

    migration._ensure_indexes(inspector)

    assert created == [
        (
            "idx_aics_internal_work_items_queue",
            ("work_type", "status", "last_seen_at"),
        )
    ]


def test_internal_work_item_migration_rejects_incompatible_index() -> None:
    migration = _migration_module()
    inspector = SimpleNamespace(
        get_indexes=lambda _table: [
            {
                "name": "idx_aics_internal_work_items_queue",
                "column_names": ["status", "work_type", "last_seen_at"],
                "unique": False,
            }
        ],
        get_unique_constraints=lambda _table: [
            {
                "name": "uq_aics_internal_work_items_idempotency",
                "column_names": ["idempotency_key"],
            }
        ],
    )

    with pytest.raises(RuntimeError, match="Incompatible existing index"):
        migration._ensure_indexes(inspector)


def test_durable_reply_finalization_creates_store_fact_work_item(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = ChatRequest(
        content="门店几点营业？",
        customer_id="customer-finalizer",
        platform_customer_id="customer-finalizer",
        corp_id="corp-1",
        wechat="sl8003",
        external_userid="external-finalizer",
    )
    repository.upsert_conversation(
        conversation_id="conversation-finalizer",
        request=request,
        title="门店事实缺失",
    )
    repository.start_run(
        request_id="request-finalizer",
        conversation_id="conversation-finalizer",
        customer_id="customer-finalizer",
        input_snapshot=request.model_dump(),
        interface_version="v3",
    )
    state = {
        "request_id": "request-finalizer",
        "conversation_id": "conversation-finalizer",
        "customer_id": "customer-finalizer",
        "platform_customer_id": "customer-finalizer",
        "corp_id": "corp-1",
        "wechat": "sl8003",
        "external_userid": "external-finalizer",
        "request_context": {"interface_version": "v3"},
        "reply_source": "main_model",
        "reply_messages": [{"type": "text", "content": "营业时间还没有同步。"}],
        "store_fact_followup": _followup(),
        "trace": [],
    }
    repository.save_v3_reply_core(
        conversation_id="conversation-finalizer",
        final_state=state,
        reply_messages=state["reply_messages"],
        token_usage={},
        deferred_payload=state,
    )
    settings = Settings(_env_file=None).model_copy(
        update={"trace_log_dir": tmp_path / "traces"}
    )
    service = V3ReplyFinalizationService(
        repository=repository,
        trace_logger=TraceLogger(settings),
        service_rule_data_service=None,
        outreach_service=None,
        memory_store=None,
    )

    assert service.process_batch() == {"claimed": 1, "completed": 1, "failed": 0}
    listed = repository.list_internal_work_items(status="pending")
    assert listed["total"] == 1
    assert listed["items"][0]["source_request_id"] == "request-finalizer"


def test_internal_work_item_admin_api_is_authenticated_and_does_not_expose_history(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    created = _upsert(repository)
    services = SimpleNamespace(repository=repository)
    settings = Settings(_env_file=None).model_copy(
        update={"ai_paths_api_key": "test-admin-key"}
    )
    app = FastAPI()
    app.include_router(create_operations_admin_router(settings, services))  # type: ignore[arg-type]
    client = TestClient(app)

    assert client.get("/admin/internal-work-items").status_code in {401, 403}
    headers = {"Authorization": "Bearer test-admin-key"}
    response = client.get(
        "/admin/internal-work-items",
        params={"status": "pending", "detail_kind": "hours"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert "source_request_ids" not in str(response.json())

    updated = client.patch(
        f"/admin/internal-work-items/{created['id']}",
        headers=headers,
        json={
            "status": "resolved",
            "resolved_by": "operator-1",
            "resolution_note": "已补齐",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["item"]["status"] == "resolved"
