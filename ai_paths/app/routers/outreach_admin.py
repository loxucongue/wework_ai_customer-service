from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.config import Settings
from app.runtime_services import ControlServices

from .security import api_key_dependency


_FIRST_DAY_SETTINGS_ENV_KEYS = {
    "OUTREACH_FIRST_DAY_SILENCE_ENABLED",
    "OUTREACH_FIRST_DAY_SILENCE_MINUTES",
    "OUTREACH_FIRST_DAY_WECHAT_ALLOWLIST",
    "OUTREACH_SILENCE_ELIGIBLE_AFTER",
    "OUTREACH_QUIET_HOURS_START",
    "OUTREACH_QUIET_HOURS_END",
    "OUTREACH_QUIET_HOURS_RESUME",
    "OUTREACH_NIGHT_ACTIVE_WINDOW_MINUTES",
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


def _normalize_eligible_after(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="eligible_after must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _normalize_clock_setting(value: Any, *, field: str) -> str:
    raw = str(value or "").strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw):
        raise HTTPException(status_code=400, detail=f"{field} must use HH:MM")
    return raw


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
        "eligible_after": str(settings.outreach_silence_eligible_after or "").strip(),
        "contact_age_limited": False,
        "daily_plan_limit": None,
        "daily_task_limit": None,
        "task_count_source": "follow_sequence_nodes_or_selected_mainline_sources",
        "quiet_hours": {
            "start": settings.outreach_quiet_hours_start,
            "end": settings.outreach_quiet_hours_end,
            "resume": settings.outreach_quiet_hours_resume,
            "night_active_window_minutes": settings.outreach_night_active_window_minutes,
        },
    }


def create_outreach_admin_router(
    settings: Settings,
    services: ControlServices,
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
        eligible_after = _normalize_eligible_after(
            payload.get("eligible_after", settings.outreach_silence_eligible_after)
        )
        quiet_hours = payload.get("quiet_hours") if isinstance(payload.get("quiet_hours"), dict) else {}
        quiet_start = _normalize_clock_setting(
            quiet_hours.get("start", settings.outreach_quiet_hours_start),
            field="quiet_hours.start",
        )
        quiet_end = _normalize_clock_setting(
            quiet_hours.get("end", settings.outreach_quiet_hours_end),
            field="quiet_hours.end",
        )
        quiet_resume = _normalize_clock_setting(
            quiet_hours.get("resume", settings.outreach_quiet_hours_resume),
            field="quiet_hours.resume",
        )
        try:
            night_active_window = int(
                quiet_hours.get(
                    "night_active_window_minutes",
                    settings.outreach_night_active_window_minutes,
                )
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="quiet_hours.night_active_window_minutes must be an integer",
            ) from exc
        if night_active_window < 1 or night_active_window > 240:
            raise HTTPException(
                status_code=400,
                detail="quiet_hours.night_active_window_minutes must be between 1 and 240",
            )
        updates = {
            "OUTREACH_FIRST_DAY_SILENCE_ENABLED": "true" if enabled else "false",
            "OUTREACH_FIRST_DAY_SILENCE_MINUTES": str(silence_minutes),
            "OUTREACH_FIRST_DAY_WECHAT_ALLOWLIST": allowlist_raw,
            "OUTREACH_SILENCE_ELIGIBLE_AFTER": eligible_after,
            "OUTREACH_QUIET_HOURS_START": quiet_start,
            "OUTREACH_QUIET_HOURS_END": quiet_end,
            "OUTREACH_QUIET_HOURS_RESUME": quiet_resume,
            "OUTREACH_NIGHT_ACTIVE_WINDOW_MINUTES": str(night_active_window),
        }
        await asyncio.to_thread(_write_settings_env, updates)
        os.environ.update(updates)
        object.__setattr__(settings, "outreach_first_day_silence_enabled", enabled)
        object.__setattr__(settings, "outreach_first_day_silence_minutes", silence_minutes)
        object.__setattr__(settings, "outreach_first_day_wechat_allowlist", allowlist_raw)
        object.__setattr__(settings, "outreach_silence_eligible_after", eligible_after)
        object.__setattr__(settings, "outreach_quiet_hours_start", quiet_start)
        object.__setattr__(settings, "outreach_quiet_hours_end", quiet_end)
        object.__setattr__(settings, "outreach_quiet_hours_resume", quiet_resume)
        object.__setattr__(settings, "outreach_night_active_window_minutes", night_active_window)
        services.outreach_service.first_day_wechat_allowlist = allowlist_raw
        services.outreach_service.planning.quiet_hours_start = quiet_start
        services.outreach_service.planning.quiet_hours_end = quiet_end
        services.outreach_service.planning.quiet_hours_resume = quiet_resume
        services.outreach_service.planning.night_active_window_minutes = night_active_window
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

    @router.get("/admin/outreach/customer-logs", dependencies=[Depends(require_api_key)])
    async def outreach_customer_logs(
        limit: int = 50,
        cursor: str = "",
        started_from: str = "",
        started_to: str = "",
        identity_query: str = "",
        customer_id: str = "",
        external_userid: str = "",
        corp_id: str = "",
        wechat: str = "",
        source_type: str = "",
        plan_status: str = "",
        task_status: str = "",
        reason_code: str = "",
        identity_state: str = "",
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                services.repository.list_outreach_customer_logs,
                limit=limit,
                cursor=cursor,
                started_from=started_from,
                started_to=started_to,
                identity_query=identity_query,
                customer_id=customer_id,
                external_userid=external_userid,
                corp_id=corp_id,
                wechat=wechat,
                source_type=source_type,
                plan_status=plan_status,
                task_status=task_status,
                reason_code=reason_code,
                identity_state=identity_state,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(
        "/admin/outreach/customer-logs/{contact_key}/plans/{plan_id}",
        dependencies=[Depends(require_api_key)],
    )
    async def outreach_customer_log_plan(contact_key: str, plan_id: str) -> dict[str, Any]:
        detail = await asyncio.to_thread(
            services.repository.get_outreach_customer_log_plan,
            contact_key,
            plan_id,
        )
        if not detail:
            raise HTTPException(status_code=404, detail="outreach plan log not found")
        return detail

    @router.get(
        "/admin/outreach/customer-logs/{contact_key}",
        dependencies=[Depends(require_api_key)],
    )
    async def outreach_customer_log(
        contact_key: str,
        started_from: str = "",
        started_to: str = "",
    ) -> dict[str, Any]:
        try:
            detail = await asyncio.to_thread(
                services.repository.get_outreach_customer_log,
                contact_key,
                started_from=started_from,
                started_to=started_to,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not detail:
            raise HTTPException(status_code=404, detail="outreach customer log not found")
        return detail

    @router.get("/admin/outreach/dashboard", dependencies=[Depends(require_api_key)])
    async def outreach_dashboard(
        started_from: str = "",
        started_to: str = "",
        corp_id: str = "",
        wechat: str = "",
        queue_limit: int = 50,
    ) -> dict[str, Any]:
        try:
            dashboard = await asyncio.to_thread(
                services.repository.outreach_bi_dashboard,
                started_from=started_from,
                started_to=started_to,
                corp_id=corp_id,
                wechat=wechat,
                queue_limit=queue_limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**dashboard, "settings": _settings_response(settings)}

    @router.get("/admin/outreach/first-day-runs/{workflow_run_id}", dependencies=[Depends(require_api_key)])
    async def first_day_run(workflow_run_id: str) -> dict[str, Any]:
        detail = services.repository.get_first_day_outreach_run(workflow_run_id)
        if not detail:
            raise HTTPException(status_code=404, detail="first-day outreach run not found")
        return detail

    return router
