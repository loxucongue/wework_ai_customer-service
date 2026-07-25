from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import Settings


class SimulationIsolationError(RuntimeError):
    pass


def assert_simulation_isolated(
    *,
    settings: Settings,
    run_dir: Path,
    adapters: list[Any],
    identity: dict[str, Any] | None = None,
) -> None:
    root = run_dir.resolve()
    if ".tmp_runtime" not in root.parts or "simulation" not in root.parts:
        raise SimulationIsolationError(f"simulation run_dir must be under .tmp_runtime/simulation: {root}")

    guarded_paths = {
        "database": Path(settings.db_path),
        "memory": Path(settings.memory_dir),
        "logs": Path(settings.log_dir),
        "store_snapshot": Path(settings.store_snapshot_path),
    }
    for label, value in guarded_paths.items():
        resolved = value.resolve()
        if not _is_within(resolved, root):
            raise SimulationIsolationError(f"{label} path escapes simulation run_dir: {resolved}")

    forbidden_secrets = {
        "PLATFORM_AGENT_TOKEN": settings.platform_agent_token,
        "OUTREACH_SEND_AGENT_TOKEN": settings.outreach_send_agent_token,
        "OUTREACH_SYSTEM_TOKEN": settings.outreach_system_token,
        "COZE_OAUTH_CLIENT_ID": settings.coze_oauth_client_id,
        "DOUBAO_ASR_API_KEY": settings.doubao_asr_api_key,
        "DOUBAO_ASR_APP_KEY": settings.doubao_asr_app_key,
        "DOUBAO_ASR_ACCESS_KEY": settings.doubao_asr_access_key,
        "DOUBAO_ASR_SECRET_KEY": settings.doubao_asr_secret_key,
    }
    configured = [name for name, value in forbidden_secrets.items() if str(value or "").strip()]
    if configured:
        raise SimulationIsolationError("real business connector credentials are forbidden: " + ",".join(configured))

    connector_urls = {
        "PLATFORM_AGENT_BASE_URL": settings.platform_agent_base_url,
        "OUTREACH_SEND_BASE_URL": settings.outreach_send_base_url,
        "OUTREACH_SYSTEM_BASE_URL": settings.outreach_system_base_url,
    }
    unsafe_urls = [
        f"{name}={value}"
        for name, value in connector_urls.items()
        if str(value or "").strip() and not str(value).strip().lower().startswith("simulation://")
    ]
    if unsafe_urls:
        raise SimulationIsolationError("real business connector URLs are forbidden: " + ",".join(unsafe_urls))

    for adapter in adapters:
        if not bool(getattr(adapter, "simulation_adapter", False)):
            raise SimulationIsolationError(f"non-simulation adapter rejected: {type(adapter).__name__}")

    if identity:
        for key in ("customer_id", "external_userid", "corp_id", "wechat"):
            value = str(identity.get(key) or "").strip()
            if not value.startswith("sim_"):
                raise SimulationIsolationError(f"{key} must start with sim_: {value!r}")


def assert_simulation_identity(identity: dict[str, Any]) -> None:
    for key in ("customer_id", "external_userid", "corp_id", "wechat"):
        value = str(identity.get(key) or "").strip()
        if not value.startswith("sim_"):
            raise SimulationIsolationError(f"{key} must start with sim_: {value!r}")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
