from __future__ import annotations

import copy
import sys
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))
from app.config import Settings
from app.reception_state import ReceptionConflict, ReceptionNotification
from app.routers.reception_state import create_reception_state_router
from app.services.reception_state_service import ReceptionStateService, digest
from app.services.storage import AppRepository, SQLiteStore

PAYLOAD = {
    "event_id": "event-1", "customer_id": 10001, "customer_add_wechat_id": 90001,
    "wecom_corp_id": "corp-test", "employee_wechat_id": "employee-test",
    "customer_external_user_id": "external-test", "state_version": 1,
    "occurred_at": 1788919200,
    "data": {"service_mode": 1, "is_deleted": False, "ai_version": "v3"},
}
BINDINGS = [{"corp_id": "corp-test", "employee_wechat_id": "employee-test", "wechat": "account-test"}]


def bind(store, relation=90001, external="external-test", wechat="account-test", customer=10001):
    AppRepository(store).observe_customer_identity(
        corp_id="corp-test", wechat=wechat, external_userid=external,
        customer_id=str(customer), customer_add_wechat_id=str(relation),
        source="synthetic_verified_directory", verified=True,
    )


@pytest.fixture
def store(tmp_path):
    result = SQLiteStore(Settings(_env_file=None).model_copy(update={"db_path": tmp_path / "state.db"}))
    result.initialize()
    bind(result)
    return result


@pytest.fixture
def client(store):
    settings = Settings(_env_file=None).model_copy(update={
        "reception_state_api_key": "test-token", "reception_state_allowed_corps": ["corp-test"],
        "reception_state_member_bindings": BINDINGS,
    })
    app = FastAPI()
    app.include_router(create_reception_state_router(settings, SimpleNamespace(storage_store=store)))
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as result:
        yield result


def send(client, payload=None):
    return client.post("/api/ai/customer/reception-state", json=payload or copy.deepcopy(PAYLOAD))


def test_apply_duplicate_stale_and_version_conflicts(client):
    assert send(client).json()["data"]["result"] == "applied"
    assert send(client).json()["data"]["result"] == "duplicate"
    newer = copy.deepcopy(PAYLOAD)
    newer.update(event_id="event-2", state_version=3)
    newer["data"].update(service_mode=2, ai_version=None)
    assert send(client, newer).status_code == 200
    assert send(client).json()["data"]["current_state_version"] == 3
    stale = {**PAYLOAD, "event_id": "stale", "state_version": 2}
    assert send(client, stale).json()["data"]["result"] == "stale_ignored"
    conflict = {**PAYLOAD, "event_id": "conflict", "state_version": 3}
    assert send(client, conflict).json()["message"] == "state_version_conflict"
    assert send(client, {**PAYLOAD, "state_version": 4}).json()["message"] == "event_id_conflict"


@pytest.mark.parametrize("field,value", [
    ("customer_id", True), ("customer_id", "1"), ("customer_id", 0),
    ("state_version", 1.0), ("state_version", 9007199254740992),
    ("occurred_at", -1), ("event_id", " x"), ("employee_wechat_id", ""),
    ("customer_external_user_id", "x" * 129), ("unknown", "value"),
])
def test_strict_fields(client, field, value):
    assert send(client, {**PAYLOAD, field: value}).status_code == 400


@pytest.mark.parametrize("field,value", [("service_mode", True), ("service_mode", 3),
                                          ("is_deleted", 0), ("ai_version", "v4")])
def test_strict_data(client, field, value):
    payload = copy.deepcopy(PAYLOAD)
    payload["data"][field] = value
    assert send(client, payload).status_code == 400


@pytest.mark.parametrize("version", ["v1", "v2", "v3", None])
def test_version_record_only(client, version):
    payload = copy.deepcopy(PAYLOAD)
    payload["data"]["ai_version"] = version
    data = send(client, payload).json()["data"]
    assert data["requested_ai_version"] == version
    assert data["effective_ai_version"] == "v3"
    assert data["version_switch_enabled"] is False


def test_auth_unknown_identity_and_oversize(client):
    assert client.post("/api/ai/customer/reception-state", json=PAYLOAD,
                       headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert send(client, {**PAYLOAD, "wecom_corp_id": "other"}).status_code == 403
    assert send(client, {**PAYLOAD, "employee_wechat_id": "account-test"}).status_code == 409
    assert send(client, {**PAYLOAD, "customer_id": 111}).status_code == 409
    assert client.post("/api/ai/customer/reception-state", content=b"x" * 17000).status_code == 400


def test_delete_readd_retired_and_invalidation(store):
    service = ReceptionStateService(store, BINDINGS)
    payload = copy.deepcopy(PAYLOAD)
    service.apply(ReceptionNotification.model_validate(payload))
    payload.update(event_id="deleted", state_version=2)
    payload["data"]["is_deleted"] = True
    service.apply(ReceptionNotification.model_validate(payload))
    payload.update(event_id="revive", state_version=3)
    payload["data"]["is_deleted"] = False
    with pytest.raises(ReceptionConflict, match="cannot_revive"):
        service.apply(ReceptionNotification.model_validate(payload))
    bind(store, relation=12)  # Lower numeric ID can still be the new authoritative relation.
    payload.update(event_id="readd", customer_add_wechat_id=12)
    assert service.apply(ReceptionNotification.model_validate(payload))["result"] == "applied"
    bind(store, relation=90001)
    payload.update(event_id="old-relation", state_version=4, customer_add_wechat_id=90001)
    with pytest.raises(ReceptionConflict, match="retired_relationship"):
        service.apply(ReceptionNotification.model_validate(payload))
    with store.connect() as conn:
        state = dict(conn.execute("SELECT * FROM reception_states").fetchone())
        assert state["version"] == 3 and state["invalidated_through_version"] == 2
        assert conn.execute("SELECT COUNT(*) FROM reception_events").fetchone()[0] == 3


def test_concurrent_retries_and_restart(store):
    service = ReceptionStateService(store, BINDINGS)
    event = ReceptionNotification.model_validate(PAYLOAD)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: service.apply(event), range(12)))
    assert sum(item["result"] == "applied" for item in results) == 1
    restarted = ReceptionStateService(store, BINDINGS)
    assert restarted.apply(event)["result"] == "duplicate"
    versions = [9, 2, 7, 4, 10, 3, 5]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda v: restarted.apply(ReceptionNotification.model_validate(
            {**PAYLOAD, "event_id": f"v-{v}", "state_version": v})), versions))
    assert restarted.apply(event)["current_state_version"] == 10


def test_case_sensitive_event_id_and_identity_isolation(store):
    service = ReceptionStateService(store, BINDINGS)
    service.apply(ReceptionNotification.model_validate(PAYLOAD))
    bind(store, external="external-other", relation=90002)
    other = {**PAYLOAD, "event_id": "EVENT-1", "customer_external_user_id": "external-other",
             "customer_add_wechat_id": 90002}
    assert service.apply(ReceptionNotification.model_validate(other))["result"] == "applied"
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reception_states").fetchone()[0] == 2
    assert digest("event-1") != digest("EVENT-1")


def test_same_snapshot_new_event_time_and_omitted_version(client):
    assert send(client).status_code == 200
    equal = {**PAYLOAD, "event_id": "new-event", "occurred_at": 999}
    assert send(client, equal).json()["data"]["result"] == "duplicate"
    equal = copy.deepcopy(equal)
    equal.update(event_id="clear-version", state_version=2)
    del equal["data"]["ai_version"]
    assert send(client, equal).json()["data"]["requested_ai_version"] is None


def test_commit_failure_returns_503_and_rolls_back(store):
    class FailCommit:
        dialect = "sqlite"

        @contextmanager
        def connect(self):
            with store.connect() as conn:
                yield conn
                raise RuntimeError("synthetic commit failure with sensitive detail")

    settings = Settings(_env_file=None).model_copy(update={
        "reception_state_api_key": "test-token", "reception_state_allowed_corps": ["corp-test"],
        "reception_state_member_bindings": BINDINGS,
    })
    app = FastAPI()
    app.include_router(create_reception_state_router(settings, SimpleNamespace(storage_store=FailCommit())))
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        response = send(client)
        assert response.status_code == 503
        assert "sensitive" not in response.text
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reception_states").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM reception_events").fetchone()[0] == 0
    assert ReceptionStateService(store, BINDINGS).apply(ReceptionNotification.model_validate(PAYLOAD))["result"] == "applied"


def test_notification_has_no_business_side_effects(store, client):
    assert send(client).status_code == 200
    with store.connect() as conn:
        for table in ("runs", "messages", "outreach_tasks", "strategy_data_outbox", "message_dispatches"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        import json
        persisted = json.loads(conn.execute("SELECT payload_json FROM reception_events").fetchone()[0])
        assert persisted == PAYLOAD


def test_missing_credentials_fail_closed(store):
    app = FastAPI()
    app.include_router(create_reception_state_router(Settings(_env_file=None), SimpleNamespace(storage_store=store)))
    with TestClient(app) as client:
        assert send(client).status_code == 503
