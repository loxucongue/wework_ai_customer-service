from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.routers.operations_admin import create_operations_admin_router  # noqa: E402


class _RepositoryStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def v3_strategy_analytics_by_dimension(self, *, dimension: str, **filters: object) -> dict:
        self.calls.append((dimension, dict(filters)))
        return {"dimension": dimension, "filters": filters, "items": []}

    def refresh_v3_strategy_outcomes(self, **kwargs: object) -> dict:
        self.calls.append(("refresh", dict(kwargs)))
        return {"updated": 0}


class _ProviderStub:
    enabled = True
    available = True

    def reset_batch(self) -> None:
        return None

    def runtime_status(self) -> dict:
        return {"enabled": True, "available": True, "status": "ready"}


def _client() -> tuple[TestClient, _RepositoryStub]:
    repository = _RepositoryStub()
    services = SimpleNamespace(
        repository=repository,
        strategy_outcome_provider=_ProviderStub(),
    )
    app = FastAPI()
    app.include_router(
        create_operations_admin_router(
            Settings(_env_file=None),
            services,  # type: ignore[arg-type]
        )
    )
    return TestClient(app), repository


def test_decision_dimension_routes_forward_filters() -> None:
    client, repository = _client()

    for suffix, dimension in (
        ("by-intent", "intent"),
        ("by-emotion", "emotion"),
        ("by-closing", "closing"),
        ("transitions", "transitions"),
    ):
        response = client.get(
            f"/admin/v3-strategy-analytics/{suffix}",
            params={
                "intent_code": "defer",
                "emotion_code": "hesitant",
                "closing_action": "pause",
                "decision_status": "ok",
                "checkpoint_code": "price",
                "sequence_id": "sequence-1",
                "script_id": "script-1",
                "action_code": "explain_value",
                "fallback_used": "true",
            },
        )
        assert response.status_code == 200
        assert response.json()["dimension"] == dimension

    assert repository.calls[0][1]["intent_code"] == "defer"
    assert repository.calls[0][1]["emotion_code"] == "hesitant"
    assert repository.calls[0][1]["checkpoint_code"] == "price"
    assert repository.calls[0][1]["fallback_used"] is True

    response = client.get(
        "/admin/v3-strategy-analytics/by-closing-rule",
        params={
            "closing_rule_id": "external:rule:101",
            "closing_catalog_status": "stale",
            "closing_rule_match_status": "matched",
            "closing_constraint_status": "passed",
        },
    )
    assert response.status_code == 200
    assert response.json()["dimension"] == "closing_rule"
    assert repository.calls[-1][1]["closing_rule_id"] == "external:rule:101"


def test_manual_outcome_refresh_uses_platform_provider() -> None:
    client, repository = _client()

    response = client.post("/admin/v3-strategy-analytics/outcomes/refresh", params={"limit": 12})

    assert response.status_code == 200
    assert repository.calls[-1][0] == "refresh"
    assert repository.calls[-1][1]["limit"] == 12
    assert repository.calls[-1][1]["order_snapshot_provider"].available is True
    assert response.json()["order_provider"]["status"] == "ready"
