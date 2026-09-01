from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.config import Settings
from app.runtime_services import RuntimeServices
from app.workers.supervisor import WorkerSupervisor

from .security import api_key_dependency


_FIRST_DAY_SETTINGS_ENV_KEYS = {
    "OUTREACH_FIRST_DAY_SILENCE_ENABLED",
    "OUTREACH_FIRST_DAY_SILENCE_MINUTES",
    "OUTREACH_FIRST_DAY_WECHAT_ALLOWLIST",
}


def _settings_env_path() -> Path:
    configured = os.environ.get("AI_PATHS_RUNTIME_ENV_FILE", "").strip()
    if configured:
        return Path(configured)
    production_env = Path("/opt/ai-paths/.env")
    if production_env.exists():
        return production_env
    return Path.cwd() / ".env"


def _normalize_allowlist(value: Any) -> tuple[str, list[str]]:
    raw = ",".join(str(item).strip() for item in value if str(item).strip()) if isinstance(value, list) else str(value or "")
    tokens: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[,;\s]+", raw):
        item = token.strip()
        if not item:
            continue
        if any(char.isspace() for char in item):
            raise HTTPException(status_code=400, detail="wechat allowlist item must not contain whitespace")
        if len(item) > 80:
            raise HTTPException(status_code=400, detail="wechat allowlist item is too long")
        lowered = item.lower()
        if lowered not in seen:
            seen.add(lowered)
            tokens.append(item)
    if len(tokens) > 200:
        raise HTTPException(status_code=400, detail="wechat allowlist supports at most 200 items")
    return ",".join(tokens), tokens


def _write_settings_env(updates: dict[str, str]) -> None:
    unknown = set(updates) - _FIRST_DAY_SETTINGS_ENV_KEYS
    if unknown:
        raise ValueError(f"unsupported first-day setting keys: {sorted(unknown)}")
    env_path = _settings_env_path()
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    output: list[str] = []
    written: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            if key not in written:
                output.append(f"{key}={updates[key]}")
                written.add(key)
        else:
            output.append(line)
    output.extend(f"{key}={updates[key]}" for key in sorted(set(updates) - written))
    env_path.parent.mkdir(parents=True, exist_ok=True)
    mode = env_path.stat().st_mode if env_path.exists() else None
    tmp_path = env_path.with_name(f"{env_path.name}.tmp")
    tmp_path.write_text("\n".join(output) + "\n", encoding="utf-8")
    if mode is not None:
        tmp_path.chmod(mode)
    tmp_path.replace(env_path)


def _settings_response(settings: Settings) -> dict[str, Any]:
    raw_allowlist, allowlist = _normalize_allowlist(settings.outreach_first_day_wechat_allowlist)
    return {
        "enabled": bool(settings.outreach_first_day_silence_enabled),
        "silence_minutes": int(settings.outreach_first_day_silence_minutes),
        "wechat_allowlist": allowlist,
        "wechat_allowlist_raw": raw_allowlist,
        "empty_allowlist_means_all_allowed": True,
    }


def create_outreach_admin_router(
    settings: Settings,
    services: RuntimeServices,
    supervisor: WorkerSupervisor,
) -> APIRouter:
    router = APIRouter()
    require_api_key = api_key_dependency(settings)

    @router.get("/admin/outreach/first-day-settings", dependencies=[Depends(require_api_key)])
    async def first_day_settings() -> dict[str, Any]:
        return _settings_response(settings)

    @router.put("/admin/outreach/first-day-settings", dependencies=[Depends(require_api_key)])
    async def update_first_day_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        if "enabled" in payload and not isinstance(payload.get("enabled"), bool):
            raise HTTPException(status_code=400, detail="enabled must be boolean")
        enabled = bool(payload.get("enabled", settings.outreach_first_day_silence_enabled))
        try:
            silence_minutes = int(payload.get("silence_minutes", settings.outreach_first_day_silence_minutes))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="silence_minutes must be an integer") from exc
        if silence_minutes < 1 or silence_minutes > 120:
            raise HTTPException(status_code=400, detail="silence_minutes must be between 1 and 120")
        allowlist_raw, _ = _normalize_allowlist(
            payload.get("wechat_allowlist", payload.get("wechat_allowlist_raw", settings.outreach_first_day_wechat_allowlist))
        )
        updates = {
            "OUTREACH_FIRST_DAY_SILENCE_ENABLED": "true" if enabled else "false",
            "OUTREACH_FIRST_DAY_SILENCE_MINUTES": str(silence_minutes),
            "OUTREACH_FIRST_DAY_WECHAT_ALLOWLIST": allowlist_raw,
        }
        await asyncio.to_thread(_write_settings_env, updates)
        os.environ.update(updates)
        object.__setattr__(settings, "outreach_first_day_silence_enabled", enabled)
        object.__setattr__(settings, "outreach_first_day_silence_minutes", silence_minutes)
        object.__setattr__(settings, "outreach_first_day_wechat_allowlist", allowlist_raw)
        services.outreach_service.first_day_wechat_allowlist = allowlist_raw
        await supervisor.sync_outreach_workers()
        return _settings_response(settings)

    @router.get("/admin/outreach/first-day-runs", dependencies=[Depends(require_api_key)])
    async def first_day_runs(
        limit: int = 50,
        cursor: str = "",
        started_from: str = "",
        started_to: str = "",
        customer_id: str = "",
        external_userid: str = "",
        corp_id: str = "",
        wechat: str = "",
        plan_id: str = "",
        status: str = "",
        reason_code: str = "",
        first_scene: str = "",
        second_scene: str = "",
        failed: bool | None = None,
    ) -> dict[str, Any]:
        return services.repository.list_first_day_outreach_runs(
            limit=limit,
            cursor=cursor,
            started_from=started_from,
            started_to=started_to,
            customer_id=customer_id,
            external_userid=external_userid,
            corp_id=corp_id,
            wechat=wechat,
            plan_id=plan_id,
            status=status,
            reason_code=reason_code,
            first_scene=first_scene,
            second_scene=second_scene,
            failed=failed,
        )

    @router.get("/admin/outreach/first-day-runs/{workflow_run_id}", dependencies=[Depends(require_api_key)])
    async def first_day_run(workflow_run_id: str) -> dict[str, Any]:
        detail = services.repository.get_first_day_outreach_run(workflow_run_id)
        if not detail:
            raise HTTPException(status_code=404, detail="first-day outreach run not found")
        return detail

    return router
