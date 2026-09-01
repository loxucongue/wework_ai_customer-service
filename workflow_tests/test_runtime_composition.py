from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app import runtime_services
from app.config import Settings
from app.runtime_roles import RuntimeRole, normalize_runtime_role


def _settings(tmp_path, role: str, *, workers: bool) -> Settings:
    return Settings(
        _env_file=None,
        AI_PATHS_SERVICE_ROLE=role,
        AI_PATHS_BACKGROUND_WORKERS_ENABLED=workers,
        AI_PATHS_DB_PATH=tmp_path / f"{role}.db",
        AICS_STORAGE_BACKEND="sqlite",
        SOP_PLATFORM_PULL_ENABLED=False,
        STORE_SNAPSHOT_REFRESH_ENABLED=False,
    )


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("reply", RuntimeRole.REPLY),
        ("control", RuntimeRole.CONTROL),
        ("worker", RuntimeRole.WORKER),
        ("model_led_sales_brain_v3", RuntimeRole.REPLY),
        ("primary", RuntimeRole.CONTROL),
        ("workers", RuntimeRole.WORKER),
    ],
)
def test_runtime_role_normalization(configured: str, expected: RuntimeRole) -> None:
    assert normalize_runtime_role(configured) is expected


def test_runtime_role_rejects_worker_boolean_mismatch() -> None:
    with pytest.raises(ValidationError, match="reply role requires"):
        Settings(
            _env_file=None,
            AI_PATHS_SERVICE_ROLE="reply",
            AI_PATHS_BACKGROUND_WORKERS_ENABLED=True,
        )


def test_callback_required_rejects_missing_callback_token() -> None:
    with pytest.raises(ValidationError, match="MESSAGE_DELIVERY_CALLBACK_TOKEN"):
        Settings(
            _env_file=None,
            AI_PATHS_SERVICE_ROLE="control",
            AI_PATHS_BACKGROUND_WORKERS_ENABLED=False,
            MESSAGE_DELIVERY_CALLBACK_REQUIRED=True,
            MESSAGE_DELIVERY_CALLBACK_TOKEN="",
        )


def test_sop_pull_is_worker_only_and_requires_token() -> None:
    with pytest.raises(ValidationError, match="only valid for the worker role"):
        Settings(
            _env_file=None,
            AI_PATHS_SERVICE_ROLE="control",
            AI_PATHS_BACKGROUND_WORKERS_ENABLED=False,
            SOP_PLATFORM_PULL_ENABLED=True,
            SOP_PLATFORM_TOKEN="token",
        )
    with pytest.raises(ValidationError, match="requires SOP_PLATFORM_TOKEN"):
        Settings(
            _env_file=None,
            AI_PATHS_SERVICE_ROLE="worker",
            AI_PATHS_BACKGROUND_WORKERS_ENABLED=True,
            SOP_PLATFORM_PULL_ENABLED=True,
            SOP_PLATFORM_TOKEN="",
        )
    with pytest.raises(ValidationError, match="worker role requires"):
        Settings(
            _env_file=None,
            AI_PATHS_SERVICE_ROLE="worker",
            AI_PATHS_BACKGROUND_WORKERS_ENABLED=False,
        )


@pytest.mark.parametrize("role", ["control", "worker"])
def test_non_reply_roles_do_not_build_reply_graph(monkeypatch, tmp_path, role: str) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("non-reply role must not build Reply graph")

    monkeypatch.setattr(runtime_services, "build_reply_graphs", fail_if_called)
    services = runtime_services.build_runtime_services(
        _settings(tmp_path, role, workers=role == "worker")
    )
    try:
        assert services.reply_graphs is None
        assert services.chat_runtime is None
        assert services.v3_sop_execution_service is None
    finally:
        asyncio.run(services.aclose())


def test_reply_role_builds_no_background_worker_contract(tmp_path) -> None:
    settings = _settings(tmp_path, "reply", workers=False)
    assert settings.runtime_role is RuntimeRole.REPLY
    assert settings.background_workers_enabled is False
