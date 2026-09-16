"""Opt-in loopback-only MySQL contract tests, never production settings."""
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.reception_state import ReceptionConflict, ReceptionNotification
from app.routers.reception_state import create_reception_state_router
from app.services.reception_state_service import ReceptionStateService
from app.services.storage.mysql_store import MySQLStore


@pytest.fixture
def mysql():
    port = os.environ.get("RECEPTION_TEST_MYSQL_PORT")
    if not port:
        pytest.skip("requires disposable loopback MySQL")
    assert 10000 <= int(port) <= 65535
    store = MySQLStore(Settings(_env_file=None, AICS_MYSQL_HOST="127.0.0.1",
        AICS_MYSQL_PORT=int(port), AICS_MYSQL_USER="review",
        AICS_MYSQL_PASSWORD="review-isolated-only", AICS_MYSQL_DATABASE="wecom_cs",
        AICS_MYSQL_SSL_REQUIRED=False))
    store.initialize()
    yield store
    store.close()


def test_mysql_concurrent_versions_conflicts_and_reconnect(mysql):
    tag = uuid4().hex
    service = ReceptionStateService(mysql)
    payload = dict(event_id=tag, customer_id=100, customer_add_wechat_id=200, wecom_corp_id=tag,
        employee_wechat_id="member", customer_external_user_id=tag, state_version=1,
        occurred_at=100, data={"service_mode": 1, "is_deleted": False})
    event = ReceptionNotification.model_validate(payload)
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: service.apply(event), range(10)))
    assert sum(r["result"] == "applied" for r in results) == 1
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda version: service.apply(ReceptionNotification.model_validate(
            {**payload, "event_id": f"{tag}-{version}", "state_version": version})), [9, 3, 7, 2, 5, 10]))
    mysql.close()  # New physical connection, persisted event/state must survive.
    assert service.apply(event)["current_state_version"] == 10
    with pytest.raises(ReceptionConflict, match="event_id_conflict"):
        service.apply(ReceptionNotification.model_validate({**payload, "state_version": 11}))
    with pytest.raises(ReceptionConflict, match="state_version_conflict"):
        service.apply(ReceptionNotification.model_validate({**payload, "event_id": tag + "bad",
            "state_version": 10, "data": {"service_mode": 2, "is_deleted": False}}))
    assert service.apply(event)["current_state_version"] == 10


def test_mysql_migration_reentry_and_protected_rollback(mysql):
    path = Path(__file__).resolve().parents[1] / "ai_paths/migrations/versions/20260914_01_add_reception_state.py"
    spec = importlib.util.spec_from_file_location("reception_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with mysql.engine.begin() as conn:
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        module.upgrade()
        conn.execute(text("INSERT INTO aics_reception_events "
            "(event_key,contact_key,request_hash,created_at) VALUES (:id,'test','hash','now')"), {"id": uuid4().hex})
        with pytest.raises(RuntimeError, match="nonempty downgrade forbidden"):
            module.downgrade()
        assert conn.execute(text("SELECT COUNT(*) FROM aics_reception_events")).scalar() > 0


def test_mysql_http_committed_snapshot_and_replay(mysql):
    tag = uuid4().hex
    settings = Settings(_env_file=None).model_copy(update={"reception_state_api_key": "synthetic-token"})
    app = FastAPI()
    app.include_router(create_reception_state_router(settings, SimpleNamespace(storage_store=mysql)))
    payload = dict(event_id=tag, customer_id=100, customer_add_wechat_id=200, wecom_corp_id=tag,
        employee_wechat_id="member", customer_external_user_id=tag, state_version=1,
        occurred_at=100, data={"service_mode": 1, "is_deleted": False})
    with TestClient(app, headers={"Authorization": "Bearer synthetic-token"}) as client:
        first = client.post("/api/ai/customer/reception-state", json=payload)
        assert first.status_code == 200 and first.json()["data"]["result"] == "applied"
        mysql.close()
        again = client.post("/api/ai/customer/reception-state", json=payload)
        assert again.status_code == 200 and again.json()["data"]["result"] == "duplicate"
