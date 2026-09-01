from typing import Any

import asyncio
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.runtime_services import build_runtime_services
from app.runtime_roles import RuntimeRole
from app.runtime_routes import apply_runtime_route_policy
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationModeChangedEvent,
    MessageDeliveryCallback,
)
from app.services.conversation_mode_relay import (
    ConversationModeWritebackRejected,
    ConversationModeWritebackTimeout,
    ConversationModeWritebackUnavailable,
)
from app.services.run_observability_summary import build_run_observability
from app.services.voice_transcription import transcribe_voice_request
from app.services.workflow_compat import (
    normalize_workflow_request,
    workflow_error_response,
    workflow_response_from_chat,
)

settings = get_settings()
services = build_runtime_services(settings)
runtime_role = settings.runtime_role
v3_evaluation_service = services.v3_evaluation_service
trace_logger = services.trace_logger
storage_store = services.storage_store
repository = services.repository
message_delivery_service = services.message_delivery_service
conversation_mode_relay_service = services.conversation_mode_relay_service
service_rule_data_service = services.service_rule_data_service
coze_client = services.coze_client
voice_transcription_client = services.voice_transcription_client
model_client = services.model_client
ai_sales_policy_service = services.ai_sales_policy_service
sales_strategy_service = services.sales_strategy_service
memory_store = services.memory_store
platform_agent_client = services.platform_agent_client
outreach_send_client = services.outreach_send_client
outreach_system_client = services.outreach_system_client
sop_platform_client = services.sop_platform_client
platform_reply_coordinator = services.platform_reply_coordinator
platform_voice_batch_coordinator = services.platform_voice_batch_coordinator
customer_context_service = services.customer_context_service
store_snapshot_service = services.store_snapshot_service
customer_store_knowledge_service = services.customer_store_knowledge_service
store_service = services.store_service
sop_reply_pack_service = services.sop_reply_pack_service
precision_qa_playbook_service = services.precision_qa_playbook_service
sop_objection_material_service = services.sop_objection_material_service
model_led_objection_playbook_service = services.model_led_objection_playbook_service
follow_knowledge_client = services.follow_knowledge_client
deepseek_semantic_client = services.deepseek_semantic_client
v3_semantic_router_service = services.v3_semantic_router_service
outreach_service = services.outreach_service
sop_execution_service = services.sop_execution_service
v3_sop_execution_service = services.v3_sop_execution_service
sop_event_service = services.sop_event_service
sop_platform_task_service = services.sop_platform_task_service
reply_graphs = services.reply_graphs
chat_runtime = services.chat_runtime
compiled_graph = reply_graphs.full_graph if reply_graphs is not None else None


@asynccontextmanager
async def lifespan(_: FastAPI):
    await startup()
    try:
        yield
    finally:
        await shutdown()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
logger = logging.getLogger(__name__)
sop_platform_pull_worker: asyncio.Task[None] | None = None
storage_retention_worker: asyncio.Task[None] | None = None
store_snapshot_refresh_worker: asyncio.Task[None] | None = None
outreach_plan_monitor_worker: asyncio.Task[None] | None = None
outreach_task_executor_worker: asyncio.Task[None] | None = None
strategy_data_callback_worker: asyncio.Task[None] | None = None
first_day_retention_last_date = ""
FIRST_DAY_SETTINGS_ENV_KEYS = {
    "OUTREACH_FIRST_DAY_SILENCE_ENABLED",
    "OUTREACH_FIRST_DAY_SILENCE_MINUTES",
    "OUTREACH_FIRST_DAY_WECHAT_ALLOWLIST",
}


async def _run_sop_platform_pull_worker() -> None:
    await sop_platform_task_service.run()


async def _run_strategy_data_callback_worker() -> None:
    await service_rule_data_service.run()


async def _run_storage_retention_worker() -> None:
    global first_day_retention_last_date
    while True:
        try:
            result = await asyncio.to_thread(
                repository.prune_runtime_history,
                trace_days=settings.aics_trace_retention_days,
                run_days=settings.aics_run_retention_days,
            )
            if any(result.values()):
                logger.info("Pruned AICS runtime history: %s", result)
            beijing_date = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
            if first_day_retention_last_date != beijing_date:
                first_day_result = await asyncio.to_thread(
                    repository.prune_first_day_outreach_runs,
                    raw_days=30,
                    summary_days=90,
                )
                first_day_retention_last_date = beijing_date
                logger.info("Pruned first-day outreach run history: %s", first_day_result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AICS retention worker iteration failed")
        await asyncio.sleep(6 * 60 * 60)


async def _run_store_snapshot_refresh_worker() -> None:
    while True:
        try:
            snapshot = await asyncio.to_thread(store_snapshot_service.load_snapshot)
            logger.info(
                "Store snapshot ready: generated_at=%s stores=%s invalid=%s refresh_error=%s",
                snapshot.get("generated_at"),
                snapshot.get("store_count"),
                snapshot.get("invalid_store_count"),
                snapshot.get("refresh_error"),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Store snapshot refresh worker iteration failed")
        await asyncio.sleep(max(300, int(settings.store_snapshot_refresh_interval_seconds)))


async def _run_outreach_plan_monitor_worker() -> None:
    while True:
        try:
            await sop_event_service.process_due_quiet_backlog_fusions()
            if settings.outreach_first_day_silence_enabled:
                await outreach_service.evaluate_first_day_opened_silence_customers(
                    limit=settings.outreach_plan_monitor_batch_size,
                    silent_minutes=settings.outreach_first_day_silence_minutes,
                    auto_activate=settings.outreach_plan_monitor_auto_activate,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Outreach plan monitor iteration failed")
        await asyncio.sleep(max(5.0, float(settings.outreach_plan_monitor_poll_seconds)))


async def _run_outreach_task_executor_worker() -> None:
    while True:
        try:
            if settings.outreach_first_day_silence_enabled:
                await outreach_service.execute_due_first_day_tasks(
                    limit=settings.outreach_auto_send_batch_size,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Outreach task executor iteration failed")
        await asyncio.sleep(max(1.0, float(settings.outreach_auto_send_poll_seconds)))


async def startup() -> None:
    global sop_platform_pull_worker, storage_retention_worker, store_snapshot_refresh_worker
    global outreach_plan_monitor_worker, outreach_task_executor_worker, strategy_data_callback_worker
    storage_store.initialize()
    if runtime_role is not RuntimeRole.WORKER:
        return
    if service_rule_data_service.available and (
        strategy_data_callback_worker is None or strategy_data_callback_worker.done()
    ):
        strategy_data_callback_worker = asyncio.create_task(_run_strategy_data_callback_worker())
    if not settings.background_workers_enabled:
        logger.info("Background workers are disabled by AI_PATHS_BACKGROUND_WORKERS_ENABLED=false")
        return
    if settings.sop_platform_pull_enabled and (
        sop_platform_pull_worker is None or sop_platform_pull_worker.done()
    ):
        sop_platform_pull_worker = asyncio.create_task(_run_sop_platform_pull_worker())
    if storage_retention_worker is None or storage_retention_worker.done():
        storage_retention_worker = asyncio.create_task(_run_storage_retention_worker())
    if settings.store_snapshot_refresh_enabled and (
        store_snapshot_refresh_worker is None or store_snapshot_refresh_worker.done()
    ):
        store_snapshot_refresh_worker = asyncio.create_task(_run_store_snapshot_refresh_worker())
    if (
        settings.outreach_first_day_silence_enabled
        or settings.sop_quiet_backlog_fusion_enabled
    ) and (outreach_plan_monitor_worker is None or outreach_plan_monitor_worker.done()):
        outreach_plan_monitor_worker = asyncio.create_task(_run_outreach_plan_monitor_worker())
    if (
        settings.outreach_first_day_silence_enabled
    ) and (outreach_task_executor_worker is None or outreach_task_executor_worker.done()):
        outreach_task_executor_worker = asyncio.create_task(_run_outreach_task_executor_worker())


async def shutdown() -> None:
    global sop_platform_pull_worker, storage_retention_worker, store_snapshot_refresh_worker
    global outreach_plan_monitor_worker, outreach_task_executor_worker, strategy_data_callback_worker
    if sop_platform_pull_worker is not None:
        sop_platform_pull_worker.cancel()
        with suppress(asyncio.CancelledError):
            await sop_platform_pull_worker
        sop_platform_pull_worker = None
    if storage_retention_worker is not None:
        storage_retention_worker.cancel()
        with suppress(asyncio.CancelledError):
            await storage_retention_worker
        storage_retention_worker = None
    if store_snapshot_refresh_worker is not None:
        store_snapshot_refresh_worker.cancel()
        with suppress(asyncio.CancelledError):
            await store_snapshot_refresh_worker
        store_snapshot_refresh_worker = None
    if outreach_plan_monitor_worker is not None:
        outreach_plan_monitor_worker.cancel()
        with suppress(asyncio.CancelledError):
            await outreach_plan_monitor_worker
        outreach_plan_monitor_worker = None
    if outreach_task_executor_worker is not None:
        outreach_task_executor_worker.cancel()
        with suppress(asyncio.CancelledError):
            await outreach_task_executor_worker
        outreach_task_executor_worker = None
    if strategy_data_callback_worker is not None:
        service_rule_data_service.stop()
        strategy_data_callback_worker.cancel()
        with suppress(asyncio.CancelledError):
            await strategy_data_callback_worker
        strategy_data_callback_worker = None
    await services.aclose()


def _first_day_settings_env_path() -> Path:
    configured = os.environ.get("AI_PATHS_RUNTIME_ENV_FILE", "").strip()
    if configured:
        return Path(configured)
    production_env = Path("/opt/ai-paths/.env")
    if production_env.exists():
        return production_env
    return Path.cwd() / ".env"


def _normalize_first_day_wechat_allowlist(value: Any) -> tuple[str, list[str]]:
    if isinstance(value, list):
        raw = ",".join(str(item).strip() for item in value if str(item).strip())
    else:
        raw = str(value or "")
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
        if lowered in seen:
            continue
        seen.add(lowered)
        tokens.append(item)
    if len(tokens) > 200:
        raise HTTPException(status_code=400, detail="wechat allowlist supports at most 200 items")
    return ",".join(tokens), tokens


def _write_first_day_settings_env(updates: dict[str, str]) -> None:
    unknown = set(updates) - FIRST_DAY_SETTINGS_ENV_KEYS
    if unknown:
        raise ValueError(f"unsupported first-day setting keys: {sorted(unknown)}")
    env_path = _first_day_settings_env_path()
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
            continue
        output.append(line)
    for key in sorted(set(updates) - written):
        output.append(f"{key}={updates[key]}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    mode = env_path.stat().st_mode if env_path.exists() else None
    tmp_path = env_path.with_name(f"{env_path.name}.tmp")
    tmp_path.write_text("\n".join(output) + "\n", encoding="utf-8")
    if mode is not None:
        tmp_path.chmod(mode)
    tmp_path.replace(env_path)


def _first_day_settings_response() -> dict[str, Any]:
    raw_allowlist, allowlist = _normalize_first_day_wechat_allowlist(
        getattr(settings, "outreach_first_day_wechat_allowlist", "")
    )
    return {
        "enabled": bool(settings.outreach_first_day_silence_enabled),
        "silence_minutes": int(settings.outreach_first_day_silence_minutes),
        "wechat_allowlist": allowlist,
        "wechat_allowlist_raw": raw_allowlist,
        "empty_allowlist_means_all_allowed": True,
    }


async def _sync_outreach_workers_after_first_day_settings_update() -> None:
    global outreach_plan_monitor_worker, outreach_task_executor_worker
    if runtime_role is not RuntimeRole.WORKER or not settings.background_workers_enabled:
        return
    if (
        settings.outreach_first_day_silence_enabled
    ) and (outreach_plan_monitor_worker is None or outreach_plan_monitor_worker.done()):
        outreach_plan_monitor_worker = asyncio.create_task(_run_outreach_plan_monitor_worker())
    if (
        settings.outreach_first_day_silence_enabled
    ) and (outreach_task_executor_worker is None or outreach_task_executor_worker.done()):
        outreach_task_executor_worker = asyncio.create_task(_run_outreach_task_executor_worker())


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service_role": runtime_role.value,
        "configured_service_role": settings.service_role,
        "background_workers_enabled": settings.background_workers_enabled,
        "release": {
            "release_id": settings.release_id,
            "git_commit": settings.build_git_commit,
            "dirty": settings.build_dirty,
            "config_revision": settings.build_config_revision,
        },
        "platform_sop_worker": (
            sop_platform_task_service.runtime_status()
            if sop_platform_task_service is not None
            else {"enabled": False, "reason": "not_available_in_reply_role"}
        ),
        "strategy_data_callback": service_rule_data_service.status(),
    }


async def require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not settings.ai_paths_api_key:
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or token != settings.ai_paths_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )


async def require_external_api_key(authorization: str | None = Header(default=None)) -> None:
    if not settings.ai_external_api_key:
        if not settings.allow_missing_external_api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="External API token is not configured",
            )
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or token != settings.ai_external_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing external API token",
        )


async def require_v3_workflow_api_key(
    request: Request,
    authorization: str | None = Header(default=None),
    x_ai_paths_v3_trusted_proxy: str | None = Header(default=None),
) -> None:
    """Accept configured API tokens or the IP-restricted local V3 proxy."""
    client_host = str(request.client.host if request.client else "").strip()
    trusted_proxy_hosts = {"127.0.0.1", "::1", "120.26.43.96", "121.199.0.182"}
    if x_ai_paths_v3_trusted_proxy == "1" and client_host in trusted_proxy_hosts:
        return
    accepted_tokens = {
        token
        for token in (settings.ai_paths_api_key, settings.ai_external_api_key)
        if token
    }
    if not accepted_tokens:
        if not settings.allow_missing_external_api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Workflow API token is not configured",
            )
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or token not in accepted_tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing workflow API token",
        )


async def require_message_delivery_callback_token(
    x_callback_token: str | None = Header(default=None, alias="X-Callback-Token"),
) -> None:
    expected = str(settings.message_delivery_callback_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Message delivery callback token is not configured",
        )
    if not x_callback_token or not secrets.compare_digest(x_callback_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing message delivery callback token",
        )


async def require_conversation_mode_callback_token(
    x_callback_token: str | None = Header(default=None, alias="X-Callback-Token"),
) -> None:
    expected = str(
        settings.conversation_mode_callback_token
        or settings.message_delivery_callback_token
        or ""
    ).strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conversation mode callback token is not configured",
        )
    if not x_callback_token or not secrets.compare_digest(x_callback_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing conversation mode callback token",
        )


async def _finalize_message_delivery(dispatch: dict[str, Any]) -> None:
    source_kind = str(dispatch.get("source_kind") or "").strip()
    if source_kind == "ai_async_reply":
        chat_runtime.finalize_async_message_delivery(dispatch)
    elif source_kind == "sop_event":
        sop_event_service.finalize_message_delivery(dispatch)
    elif source_kind == "outreach_task":
        outreach_service.finalize_message_delivery(dispatch)
    elif source_kind == "sop_platform_task":
        await sop_platform_task_service.finalize_message_delivery(dispatch)
    else:
        raise ValueError(f"unsupported message delivery source_kind: {source_kind or '<empty>'}")
    message_delivery_service.mark_finalized(str(dispatch.get("id") or ""))


@app.post(
    "/callbacks/v1/message-delivery",
    dependencies=[Depends(require_message_delivery_callback_token)],
)
async def message_delivery_callback(payload: MessageDeliveryCallback) -> dict[str, Any]:
    try:
        result = message_delivery_service.accept_callback(payload)
        dispatch = result.get("dispatch") if isinstance(result.get("dispatch"), dict) else {}
        if message_delivery_service.needs_finalization(dispatch):
            await _finalize_message_delivery(dispatch)
            dispatch = repository.get_message_dispatch(str(dispatch.get("id") or ""))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        logger.exception("Message delivery callback finalization failed: dispatch_id=%s", payload.dispatch_id)
        raise
    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "event_id": payload.event_id,
            "dispatch_id": payload.dispatch_id,
            "duplicate": bool(result.get("duplicate")),
            "status": str(dispatch.get("status") or ""),
            "finalized": bool(str(dispatch.get("finalized_at") or "").strip()),
        },
    }


@app.post(
    "/callbacks/v1/conversation-mode",
    dependencies=[Depends(require_conversation_mode_callback_token)],
)
async def conversation_mode_callback(payload: ConversationModeChangedEvent) -> dict[str, Any]:
    try:
        result = await conversation_mode_relay_service.forward(payload)
    except ConversationModeWritebackUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ConversationModeWritebackTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except ConversationModeWritebackRejected as exc:
        logger.warning(
            "Conversation mode strategy writeback rejected: event_id=%s status=%s error=%s",
            payload.event_id,
            exc.status_code,
            exc,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "event_id": payload.event_id,
            "forwarded": True,
            "writeback_http_status": int(result.get("http_status") or 0),
            "writeback_response": result.get("response"),
        },
    }


@app.get("/admin/message-deliveries/{dispatch_id}", dependencies=[Depends(require_api_key)])
async def admin_message_delivery(dispatch_id: str) -> dict[str, Any]:
    dispatch = repository.get_message_dispatch(dispatch_id)
    if not dispatch:
        raise HTTPException(status_code=404, detail="Message delivery dispatch not found")
    return dispatch


@app.post("/admin/store-snapshot/refresh", dependencies=[Depends(require_api_key)])
async def admin_refresh_store_snapshot() -> dict[str, Any]:
    snapshot = store_snapshot_service.refresh_snapshot(allow_existing_on_error=False)
    return {
        "status": "ok" if not snapshot.get("refresh_error") else "error",
        "generated_at": snapshot.get("generated_at", ""),
        "store_count": snapshot.get("store_count", 0),
        "refresh_error": snapshot.get("refresh_error", ""),
    }


@app.get("/admin/sop-reply-packs", dependencies=[Depends(require_api_key)])
async def admin_sop_reply_packs() -> dict[str, Any]:
    return sop_reply_pack_service.load()


@app.put("/admin/sop-reply-packs", dependencies=[Depends(require_api_key)])
async def admin_update_sop_reply_packs(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return sop_reply_pack_service.save(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/admin/precision-qa-playbook", dependencies=[Depends(require_api_key)])
async def admin_precision_qa_playbook() -> dict[str, Any]:
    return precision_qa_playbook_service.load()


@app.put("/admin/precision-qa-playbook", dependencies=[Depends(require_api_key)])
async def admin_update_precision_qa_playbook(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return precision_qa_playbook_service.save(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/admin/ai-sales-policy", dependencies=[Depends(require_api_key)])
async def admin_ai_sales_policy() -> dict[str, Any]:
    try:
        return ai_sales_policy_service.runtime_snapshot()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/admin/ai-sales-strategy-catalog", dependencies=[Depends(require_api_key)])
async def admin_ai_sales_strategy_catalog() -> dict[str, Any]:
    try:
        return sales_strategy_service.admin_view()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/admin/operations-dashboard", dependencies=[Depends(require_api_key)])
async def admin_operations_dashboard(
    started_from: str = "",
    started_to: str = "",
    corp_id: str = "",
    wechat: str = "",
) -> dict[str, Any]:
    try:
        return repository.operations_dashboard(
            started_from=started_from,
            started_to=started_to,
            corp_id=corp_id,
            wechat=wechat,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/admin/sop-objection-materials", dependencies=[Depends(require_api_key)])
async def admin_sop_objection_materials() -> dict[str, Any]:
    return sop_objection_material_service.load()


@app.put("/admin/sop-objection-materials", dependencies=[Depends(require_api_key)])
async def admin_update_sop_objection_materials(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return sop_objection_material_service.save(payload)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/admin/sop-events", dependencies=[Depends(require_api_key)])
async def admin_sop_events(
    limit: int = 50,
    event_type: str = "",
    status: str = "",
    customer_id: str = "",
    external_userid: str = "",
    has_error: str = "",
    include_chat_gate: bool = False,
) -> dict[str, Any]:
    return {
        "items": repository.list_sop_events(
            limit=limit,
            event_type=event_type,
            status=status,
            customer_id=customer_id,
            external_userid=external_userid,
            has_error=has_error,
            include_chat_gate=include_chat_gate,
        )
    }


@app.get("/admin/sop-events/{event_id:path}", dependencies=[Depends(require_api_key)])
async def admin_sop_event_detail(event_id: str) -> dict[str, Any]:
    detail = repository.get_sop_event_detail(event_id)
    if not detail:
        raise HTTPException(status_code=404, detail="SOP event not found")
    return detail


@app.get("/admin/sop-platform-tasks", dependencies=[Depends(require_api_key)])
async def admin_sop_platform_tasks(
    limit: int = 100,
    bucket: str = "",
    decision: str = "",
    task_id: str = "",
    customer_id: str = "",
    refresh_platform: bool = True,
) -> dict[str, Any]:
    return await sop_platform_task_service.admin_task_logs(
        limit=limit,
        bucket=bucket,
        decision=decision,
        task_id=task_id,
        customer_id=customer_id,
        refresh_platform=refresh_platform,
    )


@app.get("/admin/sop-platform-tasks/quiet-backlog", dependencies=[Depends(require_api_key)])
async def admin_sop_platform_quiet_backlog(
    local_date: str = "",
    status: str = "",
    customer_id: str = "",
    wechat: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    try:
        return sop_event_service.admin_quiet_backlog_logs(
            local_date=local_date,
            status=status,
            customer_id=customer_id,
            wechat=wechat,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/admin/sop-platform-tasks/quiet-backlog/{event_id:path}", dependencies=[Depends(require_api_key)])
async def admin_sop_platform_quiet_backlog_detail(event_id: str) -> dict[str, Any]:
    detail = sop_event_service.admin_quiet_backlog_detail(event_id)
    if not detail:
        raise HTTPException(status_code=404, detail="quiet backlog fusion event not found")
    return detail


@app.get("/admin/sop-platform-runs", dependencies=[Depends(require_api_key)])
async def admin_sop_platform_runs(
    limit: int = 100,
    status: str = "",
    log_version: str = "",
    biz_type: str = "",
    task_id: str = "",
    customer_id: str = "",
    external_userid: str = "",
    wechat: str = "",
    query: str = "",
    date_from: str = "",
    date_to: str = "",
    refresh_platform: bool = True,
) -> dict[str, Any]:
    return await sop_platform_task_service.admin_run_logs(
        limit=limit,
        status=status,
        log_version=log_version,
        biz_type=biz_type,
        task_id=task_id,
        customer_id=customer_id,
        external_userid=external_userid,
        wechat=wechat,
        query=query,
        date_from=date_from,
        date_to=date_to,
        refresh_platform=refresh_platform,
    )


@app.post("/admin/sop-platform-tasks/{task_id}/resend", dependencies=[Depends(require_api_key)])
async def admin_sop_platform_task_resend(task_id: str) -> dict[str, Any]:
    try:
        return await sop_platform_task_service.admin_resend_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/sop/events", dependencies=[Depends(require_external_api_key)])
async def sop_events(
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] = Body(...),
) -> JSONResponse:
    try:
        result = await sop_event_service.accept_audit_only(payload)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"code": 400, "msg": str(exc), "data": {"accepted": False}})
    return JSONResponse(content={"code": 0, "msg": "ok", "data": result})


@app.post("/reply/workflow-compatible-v3")
async def reply_workflow_compatible_v3(
    payload: dict[str, Any] = Body(...),
    background_tasks: BackgroundTasks = None,
    _: None = Depends(require_v3_workflow_api_key),
) -> JSONResponse:
    if runtime_role is not RuntimeRole.REPLY:
        raise HTTPException(status_code=404, detail="Reply chain V3 is not enabled on this service")
    return await workflow_compatible_reply(
        payload,
        platform_async=True,
        background_tasks=background_tasks,
        interface_version="v3",
    )


async def workflow_compatible_reply(
    payload: dict[str, Any],
    *,
    platform_async: bool = False,
    background_tasks: BackgroundTasks | None = None,
    interface_version: str = "v3",
) -> JSONResponse:
    try:
        request = normalize_workflow_request(payload)
    except ValueError as exc:
        return JSONResponse(status_code=400, content=workflow_error_response(str(exc)))
    _attach_request_interface_version(request, interface_version)
    if str(interface_version).strip().lower() == "v3":
        takeover_response = await chat_runtime.run_v3_takeover_guard(request)
        if takeover_response is not None:
            response_body = workflow_response_from_chat(takeover_response)
            _record_http_response_body(takeover_response.request_id, response_body)
            return JSONResponse(content=response_body)
    request = (
        await platform_voice_batch_coordinator.prepare(request, voice_transcription_client)
        if platform_async
        else await transcribe_voice_request(request, voice_transcription_client)
    )
    response = (
        await chat_runtime.run_platform_reply(request, background_tasks=background_tasks)
        if platform_async
        else await chat_runtime.run_chat(request)
    )
    response_body = workflow_response_from_chat(response)
    _record_http_response_body(response.request_id, response_body)
    return JSONResponse(content=response_body)


def _attach_request_interface_version(request: ChatRequest, interface_version: str) -> None:
    candidate = str(interface_version).strip().lower()
    version = candidate if candidate in {"v1", "v2", "v3"} else "v3"
    context = dict(request.request_context or {})
    context["interface_version"] = version
    context["api_version"] = version
    if version == "v3":
        context["reply_chain_mode"] = "model_led_sales_brain_v3"
        context["v3_sidecar"] = True
    request.request_context = context


async def run_chat(request: ChatRequest) -> ChatResponse:
    return await chat_runtime.run_chat(request)


def _record_http_response_body(request_id: str, response_body: dict[str, Any]) -> None:
    try:
        repository.update_run_http_response(request_id=request_id, response_body=response_body)
    except Exception:
        return


@app.get("/admin/conversations", dependencies=[Depends(require_api_key)])
async def admin_conversations(limit: int = 50) -> dict[str, Any]:
    return {"items": repository.list_conversations(limit=limit)}


@app.get("/admin/conversations/{conversation_id}", dependencies=[Depends(require_api_key)])
async def admin_conversation(conversation_id: str) -> dict[str, Any]:
    return repository.get_conversation(conversation_id)


@app.get("/admin/customers/{customer_id}/memory", dependencies=[Depends(require_api_key)])
async def admin_customer_memory(
    customer_id: str,
    wechat: str,
    corp_id: str = "",
    external_userid: str = "",
) -> dict[str, Any]:
    scope = repository.resolve_customer_account_scope(
        customer_id,
        wechat=wechat,
        corp_id=corp_id,
        external_userid=external_userid,
    )
    if scope.get("status") == "ambiguous_scope":
        raise HTTPException(status_code=409, detail=scope)
    sales_contact_key = str(scope.get("sales_contact_key") or "")
    return repository.load_memory(sales_contact_key) or {} if sales_contact_key else {}


@app.delete("/admin/customers/{customer_id}/memory", dependencies=[Depends(require_api_key)])
async def admin_clear_customer_memory(
    customer_id: str,
    wechat: str,
    corp_id: str = "",
    external_userid: str = "",
) -> dict[str, Any]:
    scope = repository.resolve_customer_account_scope(
        customer_id,
        wechat=wechat,
        corp_id=corp_id,
        external_userid=external_userid,
    )
    if scope.get("status") == "ambiguous_scope":
        raise HTTPException(status_code=409, detail=scope)
    sales_contact_key = str(scope.get("sales_contact_key") or "")
    if not sales_contact_key:
        raise HTTPException(status_code=409, detail={"error": "customer account scope could not be resolved", "scope": scope})
    memory_store.clear(sales_contact_key)
    return {"status": "ok", "customer_id": customer_id, "wechat": wechat, "scope": scope}


@app.get("/admin/customer-records", dependencies=[Depends(require_api_key)])
async def admin_customer_records(
    customer_id: str,
    wechat: str,
    corp_id: str = "",
    external_userid: str = "",
) -> dict[str, Any]:
    customer = str(customer_id or "").strip()
    if not customer:
        raise HTTPException(status_code=400, detail="customer_id is required")
    account = str(wechat or "").strip()
    if not account:
        raise HTTPException(status_code=400, detail="wechat is required")
    result = repository.inspect_customer_records(
        customer,
        wechat=account,
        corp_id=corp_id,
        external_userid=external_userid,
    )
    if (result.get("scope") or {}).get("status") == "ambiguous_scope":
        raise HTTPException(status_code=409, detail=result.get("scope"))
    return result


@app.post("/admin/customer-records/clear", dependencies=[Depends(require_api_key)])
async def admin_clear_customer_records(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    customer = str(payload.get("customer_id") or "").strip()
    if not customer:
        raise HTTPException(status_code=400, detail="customer_id is required")
    account = str(payload.get("wechat") or "").strip()
    if not account:
        raise HTTPException(status_code=400, detail="wechat is required")
    result = repository.clear_customer_records(
        customer,
        wechat=account,
        corp_id=str(payload.get("corp_id") or "").strip(),
        external_userid=str(payload.get("external_userid") or "").strip(),
        clear_memory=bool(payload.get("clear_memory", True)),
        clear_sop=bool(payload.get("clear_sop", True)),
        clear_conversations=bool(payload.get("clear_conversations", False)),
        clear_outreach=bool(payload.get("clear_outreach", False)),
    )
    if result.get("status") == "ambiguous_scope":
        raise HTTPException(status_code=409, detail=result.get("scope"))
    return result


@app.get("/admin/runs/{request_id}", dependencies=[Depends(require_api_key)])
async def admin_run(request_id: str) -> dict[str, Any]:
    detail = repository.get_run(request_id)
    raw_log = trace_logger.read_run(request_id)
    dispatches = repository.list_message_dispatches_for_request(request_id)
    detail["raw_log"] = raw_log
    detail["message_dispatches"] = dispatches
    detail["observability_view"] = build_run_observability(
        detail,
        raw_log=raw_log,
        dispatches=dispatches,
    )
    return detail


@app.get("/admin/runs", dependencies=[Depends(require_api_key)])
async def admin_runs(
    limit: int = 50,
    customer_id: str = "",
    conversation_id: str = "",
    has_error: bool | None = None,
) -> dict[str, Any]:
    return {
        "items": repository.list_runs(
            limit=limit,
            customer_id=customer_id,
            conversation_id=conversation_id,
            has_error=has_error,
        )
    }


@app.get("/admin/outreach/first-day-settings", dependencies=[Depends(require_api_key)])
async def admin_first_day_outreach_settings() -> dict[str, Any]:
    return _first_day_settings_response()


@app.put("/admin/outreach/first-day-settings", dependencies=[Depends(require_api_key)])
async def admin_update_first_day_outreach_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if "enabled" in payload and not isinstance(payload.get("enabled"), bool):
        raise HTTPException(status_code=400, detail="enabled must be boolean")
    enabled = bool(payload.get("enabled", settings.outreach_first_day_silence_enabled))
    silence_minutes_value = payload.get("silence_minutes", settings.outreach_first_day_silence_minutes)
    try:
        silence_minutes = int(silence_minutes_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="silence_minutes must be an integer") from exc
    if silence_minutes < 1 or silence_minutes > 120:
        raise HTTPException(status_code=400, detail="silence_minutes must be between 1 and 120")
    allowlist_raw, _allowlist = _normalize_first_day_wechat_allowlist(
        payload.get("wechat_allowlist", payload.get("wechat_allowlist_raw", settings.outreach_first_day_wechat_allowlist))
    )
    updates = {
        "OUTREACH_FIRST_DAY_SILENCE_ENABLED": "true" if enabled else "false",
        "OUTREACH_FIRST_DAY_SILENCE_MINUTES": str(silence_minutes),
        "OUTREACH_FIRST_DAY_WECHAT_ALLOWLIST": allowlist_raw,
    }
    await asyncio.to_thread(_write_first_day_settings_env, updates)
    os.environ.update(updates)
    object.__setattr__(settings, "outreach_first_day_silence_enabled", enabled)
    object.__setattr__(settings, "outreach_first_day_silence_minutes", silence_minutes)
    object.__setattr__(settings, "outreach_first_day_wechat_allowlist", allowlist_raw)
    outreach_service.first_day_wechat_allowlist = allowlist_raw
    await _sync_outreach_workers_after_first_day_settings_update()
    return _first_day_settings_response()


@app.get("/admin/outreach/first-day-runs", dependencies=[Depends(require_api_key)])
async def admin_first_day_outreach_runs(
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
    return repository.list_first_day_outreach_runs(
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


@app.get("/admin/outreach/first-day-runs/{workflow_run_id}", dependencies=[Depends(require_api_key)])
async def admin_first_day_outreach_run(workflow_run_id: str) -> dict[str, Any]:
    detail = repository.get_first_day_outreach_run(workflow_run_id)
    if not detail:
        raise HTTPException(status_code=404, detail="first-day outreach run not found")
    return detail


apply_runtime_route_policy(app, runtime_role)
