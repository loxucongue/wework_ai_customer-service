from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import runtime_services
from app.config import Settings
from app.runtime_roles import RuntimeRole, normalize_runtime_role


ROOT = Path(__file__).resolve().parents[1]


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


@pytest.mark.parametrize(
    ("role", "workers", "required_path", "forbidden_prefixes"),
    [
        ("reply", False, "/reply/workflow-compatible-v3", ("/admin/", "/callbacks/")),
        ("control", False, "/callbacks/v1/message-delivery", ("/reply/",)),
        ("worker", True, "/health", ("/reply/", "/admin/", "/callbacks/")),
    ],
)
def test_runtime_role_mounts_only_owned_routes(
    tmp_path: Path,
    role: str,
    workers: bool,
    required_path: str,
    forbidden_prefixes: tuple[str, ...],
) -> None:
    probe = """
import json
from app.main import app
print(json.dumps(sorted(route.path for route in app.routes)))
"""
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT / "ai_paths"),
            "AI_PATHS_SERVICE_ROLE": role,
            "AI_PATHS_BACKGROUND_WORKERS_ENABLED": "true" if workers else "false",
            "SOP_PLATFORM_PULL_ENABLED": "false",
            "STORE_SNAPSHOT_REFRESH_ENABLED": "false",
            "OUTREACH_FIRST_DAY_SILENCE_ENABLED": "false",
            "AICS_STORAGE_BACKEND": "sqlite",
            "AICS_DB_PATH": str(tmp_path / f"{role}.sqlite3"),
            "V3_EVALUATION_DIR": str(tmp_path / f"{role}-evaluations"),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    paths = json.loads(completed.stdout.strip().splitlines()[-1])
    assert required_path in paths
    assert all(not path.startswith(forbidden_prefixes) for path in paths)
