from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.routers.outreach_admin import create_outreach_admin_router  # noqa: E402


class _RepositoryStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_outreach_customer_logs(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("list", dict(kwargs)))
        return {"items": [], "metrics": {}, "next_cursor": "", "has_more": False}

    def get_outreach_customer_log(self, contact_key: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("customer", {"contact_key": contact_key, **kwargs}))
        return {"contact_key": contact_key, "history": []}

    def get_outreach_customer_log_plan(self, contact_key: str, plan_id: str) -> dict[str, object]:
        self.calls.append(("plan", {"contact_key": contact_key, "plan_id": plan_id}))
        return {"contact_key": contact_key, "plan": {"id": plan_id}, "tasks": []}


def _client(*, api_key: str = "") -> tuple[TestClient, _RepositoryStub]:
    repository = _RepositoryStub()
    app = FastAPI()
    app.include_router(
        create_outreach_admin_router(
            Settings(_env_file=None, ai_paths_api_key=api_key),
            SimpleNamespace(repository=repository),  # type: ignore[arg-type]
        )
    )
    return TestClient(app), repository


def test_outreach_customer_log_routes_forward_filters_and_detail_scope() -> None:
    client, repository = _client()

    response = client.get(
        "/admin/outreach/customer-logs",
        params={
            "limit": 25,
            "cursor": "next-page",
            "started_from": "2026-09-01T00:00:00+00:00",
            "started_to": "2026-09-02T00:00:00+00:00",
            "identity_query": "external-1",
            "customer_id": "customer-1",
            "external_userid": "external-1",
            "corp_id": "corp-1",
            "wechat": "SL8003",
            "source_type": "first_day",
            "plan_status": "active",
            "task_status": "pending",
            "reason_code": "human_takeover",
            "identity_state": "complete",
        },
    )

    assert response.status_code == 200
    assert repository.calls[0] == (
        "list",
        {
            "limit": 25,
            "cursor": "next-page",
            "started_from": "2026-09-01T00:00:00+00:00",
            "started_to": "2026-09-02T00:00:00+00:00",
            "identity_query": "external-1",
            "customer_id": "customer-1",
            "external_userid": "external-1",
            "corp_id": "corp-1",
            "wechat": "SL8003",
            "source_type": "first_day",
            "plan_status": "active",
            "task_status": "pending",
            "reason_code": "human_takeover",
            "identity_state": "complete",
        },
    )

    customer_response = client.get(
        "/admin/outreach/customer-logs/contact-key",
        params={
            "started_from": "2026-09-01T00:00:00+00:00",
            "started_to": "2026-09-02T00:00:00+00:00",
        },
    )
    plan_response = client.get("/admin/outreach/customer-logs/contact-key/plans/plan-1")

    assert customer_response.status_code == 200
    assert plan_response.status_code == 200
    assert repository.calls[1] == (
        "customer",
        {
            "contact_key": "contact-key",
            "started_from": "2026-09-01T00:00:00+00:00",
            "started_to": "2026-09-02T00:00:00+00:00",
        },
    )
    assert repository.calls[2] == (
        "plan",
        {"contact_key": "contact-key", "plan_id": "plan-1"},
    )


def test_outreach_customer_log_routes_require_admin_api_key_when_configured() -> None:
    client, repository = _client(api_key="admin-token")

    assert client.get("/admin/outreach/customer-logs").status_code == 401
    response = client.get(
        "/admin/outreach/customer-logs",
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    assert repository.calls[0][0] == "list"
