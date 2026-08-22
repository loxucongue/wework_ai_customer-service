from typing import Any

import asyncio
import logging
import os
import re
import secrets
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse

from app.chat_runtime import ChatRuntime
from app.config import get_settings
from app.graph.graph_builder import build_reply_graphs
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationModeChangedEvent,
    MessageDeliveryCallback,
)
from app.services.coze_client import CozeClient
from app.services.conversation_mode_relay import (
    ConversationModeRelayService,
    ConversationModeWritebackRejected,
    ConversationModeWritebackTimeout,
    ConversationModeWritebackUnavailable,
)
from app.services.customer_context import CustomerContextService
from app.services.customer_store_knowledge import CustomerStoreKnowledgeService
from app.services.memory_store import CustomerMemoryStore
from app.services.message_delivery import MessageDeliveryService
from app.services.model_client import ModelClient
from app.services.outreach_service import OutreachService, classify_conversation_refresh_error
from app.services.outreach_send_client import OutreachSendClient
from app.services.outreach_system_client import OutreachSystemClient
from app.services.platform_reply_coordinator import PlatformReplyCoordinator
from app.services.platform_voice_batch import PlatformVoiceBatchCoordinator
from app.services.platform_agent_client import PlatformAgentClient
from app.services.precision_qa_playbook_service import PrecisionQaPlaybookService
from app.services.sop_event_service import SopEventService
from app.services.sop_execution_service import SopExecutionService
from app.services.sop_objection_material_service import SopObjectionMaterialService
from app.services.sop_platform_client import SopPlatformClient
from app.services.sop_platform_task_service import SopPlatformTaskService
from app.services.storage import AppRepository, build_store
from app.services.store_service import StoreService
from app.services.store_snapshot_service import StoreSnapshotService
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.trace_logger import TraceLogger
from app.services.voice_transcription import DoubaoAsrClient, transcribe_voice_request
from app.services.workflow_compat import (
    normalize_workflow_request,
    workflow_error_response,
    workflow_response_from_chat,
)

settings = get_settings()
trace_logger = TraceLogger(settings)
storage_store = build_store(settings)
repository = AppRepository(storage_store)
message_delivery_service = MessageDeliveryService(settings, repository)
conversation_mode_relay_service = ConversationModeRelayService(settings)
coze_client = CozeClient(settings)
voice_transcription_client = DoubaoAsrClient(settings)
model_client = ModelClient(settings)
memory_store = CustomerMemoryStore(settings, repository)
platform_agent_client = PlatformAgentClient(settings)
outreach_send_client = OutreachSendClient(settings, delivery_service=message_delivery_service)
outreach_system_client = OutreachSystemClient(settings, delivery_service=message_delivery_service)
sop_platform_client = SopPlatformClient(settings)
platform_reply_coordinator = PlatformReplyCoordinator(settings)
platform_voice_batch_coordinator = PlatformVoiceBatchCoordinator(settings)
customer_context_service = CustomerContextService(platform_agent_client)
store_snapshot_service = StoreSnapshotService(settings, platform_agent_client)
customer_store_knowledge_service = CustomerStoreKnowledgeService(platform_agent_client, store_snapshot_service)
store_service = StoreService(platform_agent_client)
sop_reply_pack_service = SopReplyPackService(settings)
precision_qa_playbook_service = PrecisionQaPlaybookService(settings)
sop_objection_material_service = SopObjectionMaterialService(settings.sop_objection_materials_path)
outreach_service = OutreachService(
    repository=repository,
    model_client=model_client,
    system_client=outreach_system_client,
    customer_context_service=customer_context_service,
    precision_qa_playbook_service=precision_qa_playbook_service,
    sop_reply_pack_service=sop_reply_pack_service,
    coze_client=coze_client,
    before_send_retry_seconds=settings.outreach_before_send_retry_seconds,
)
sop_execution_service = SopExecutionService(
    repository=repository,
    sop_reply_pack_service=sop_reply_pack_service,
    model_client=model_client,
    memory_store=memory_store,
    customer_context_service=customer_context_service,
    event_model_retry_attempts=settings.sop_event_model_retry_attempts,
    event_model_retry_delay_seconds=settings.sop_event_model_retry_delay_seconds,
    event_model_attempt_timeout_seconds=settings.sop_event_model_attempt_timeout_seconds,
    event_model_total_timeout_seconds=settings.sop_event_model_total_timeout_seconds,
    chat_gate_total_timeout_seconds=settings.sop_chat_gate_total_timeout_seconds,
    event_model_max_concurrency=settings.sop_event_model_max_concurrency,
    model_semantic_routing_enabled=settings.reply_model_semantic_routing_enabled,
    event_schema_only_normalizer_enabled=settings.sop_event_schema_only_normalizer_enabled,
    governance_shadow_mode=settings.reply_governance_shadow_mode,
)
sop_event_service = SopEventService(
    repository=repository,
    sop_reply_pack_service=sop_reply_pack_service,
    outreach_send_client=outreach_send_client,
    sop_execution_service=sop_execution_service,
    memory_store=memory_store,
    customer_context_service=customer_context_service,
    personalized_outreach_service=outreach_service,
    daily_touch_soft_limit=settings.sop_event_daily_touch_soft_limit,
    default_identity={
        "corp_id": settings.platform_agent_default_corp_id,
        "user_id": settings.platform_agent_default_user_id,
        "wechat": settings.platform_agent_default_wechat,
    },
    persistent_retry_attempts=settings.sop_event_persistent_retry_attempts,
    persistent_retry_base_delay_seconds=settings.sop_event_persistent_retry_base_delay_seconds,
    persistent_retry_max_delay_seconds=settings.sop_event_persistent_retry_max_delay_seconds,
    retry_batch_size=settings.sop_event_retry_batch_size,
    quiet_backlog_fusion_enabled=settings.sop_quiet_backlog_fusion_enabled,
    quiet_backlog_fusion_time=settings.sop_quiet_backlog_fusion_time,
    quiet_backlog_fusion_batch_size=settings.sop_quiet_backlog_fusion_batch_size,
    quiet_backlog_fusion_model=settings.sop_quiet_backlog_fusion_model,
    quiet_backlog_fusion_timeout_seconds=settings.sop_quiet_backlog_fusion_timeout_seconds,
)
sop_platform_task_service = SopPlatformTaskService(
    settings=settings,
    repository=repository,
    platform_client=sop_platform_client,
    system_client=outreach_system_client,
    model_client=model_client,
    customer_context_service=customer_context_service,
    objection_material_service=sop_objection_material_service,
)
reply_graphs = build_reply_graphs(
    coze_client,
    trace_logger,
    model_client,
    memory_store,
    customer_context_service,
    customer_store_knowledge_service,
    store_service,
    outreach_send_client,
    platform_agent_client,
)
compiled_graph = reply_graphs.full_graph
chat_runtime = ChatRuntime(
    full_graph=reply_graphs.full_graph,
    planner_graph=reply_graphs.planner_graph,
    finalize_graph=reply_graphs.finalize_graph,
    trace_logger=trace_logger,
    repository=repository,
    outreach_send_client=outreach_send_client,
    memory_store=memory_store,
    platform_reply_coordinator=platform_reply_coordinator,
    sop_execution_service=sop_execution_service,
    profile_event_extractor=reply_graphs.profile_event_extractor,
    settings=settings,
)
app = FastAPI(title=settings.app_name)
logger = logging.getLogger(__name__)
sop_platform_pull_worker: asyncio.Task[None] | None = None
storage_retention_worker: asyncio.Task[None] | None = None
store_snapshot_refresh_worker: asyncio.Task[None] | None = None
outreach_plan_monitor_worker: asyncio.Task[None] | None = None
outreach_task_executor_worker: asyncio.Task[None] | None = None
first_day_retention_last_date = ""
FIRST_DAY_SETTINGS_ENV_KEYS = {
    "OUTREACH_FIRST_DAY_SILENCE_ENABLED",
    "OUTREACH_FIRST_DAY_SILENCE_MINUTES",
    "OUTREACH_FIRST_DAY_WECHAT_ALLOWLIST",
}


async def _run_sop_platform_pull_worker() -> None:
    await sop_platform_task_service.run()


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
            if settings.outreach_plan_monitor_enabled:
                await outreach_service.evaluate_silent_customers(
                    limit=settings.outreach_plan_monitor_batch_size,
                    silent_minutes=settings.outreach_plan_monitor_silent_minutes,
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
            if settings.outreach_auto_send_enabled:
                await outreach_service.execute_due_tasks(
                    limit=settings.outreach_auto_send_batch_size,
                    auto_approved_only=True,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Outreach task executor iteration failed")
        await asyncio.sleep(max(1.0, float(settings.outreach_auto_send_poll_seconds)))


@app.on_event("startup")
async def startup() -> None:
    global sop_platform_pull_worker, storage_retention_worker, store_snapshot_refresh_worker
    global outreach_plan_monitor_worker, outreach_task_executor_worker
    storage_store.initialize()
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
        or settings.outreach_plan_monitor_enabled
        or settings.sop_quiet_backlog_fusion_enabled
    ) and (outreach_plan_monitor_worker is None or outreach_plan_monitor_worker.done()):
        outreach_plan_monitor_worker = asyncio.create_task(_run_outreach_plan_monitor_worker())
    if (
        settings.outreach_first_day_silence_enabled
        or settings.outreach_auto_send_enabled
    ) and (outreach_task_executor_worker is None or outreach_task_executor_worker.done()):
        outreach_task_executor_worker = asyncio.create_task(_run_outreach_task_executor_worker())


@app.on_event("shutdown")
async def shutdown() -> None:
    global sop_platform_pull_worker, storage_retention_worker, store_snapshot_refresh_worker
    global outreach_plan_monitor_worker, outreach_task_executor_worker
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
    await platform_voice_batch_coordinator.aclose()
    await model_client.aclose()
    await coze_client.aclose()
    await voice_transcription_client.aclose()
    await outreach_send_client.aclose()
    await outreach_system_client.aclose()
    await conversation_mode_relay_service.aclose()
    await sop_platform_client.aclose()
    platform_agent_client.close()
    storage_store.close()


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
    if not settings.background_workers_enabled:
        return
    if (
        settings.outreach_first_day_silence_enabled
        or settings.outreach_plan_monitor_enabled
    ) and (outreach_plan_monitor_worker is None or outreach_plan_monitor_worker.done()):
        outreach_plan_monitor_worker = asyncio.create_task(_run_outreach_plan_monitor_worker())
    if (
        settings.outreach_first_day_silence_enabled
        or settings.outreach_auto_send_enabled
    ) and (outreach_task_executor_worker is None or outreach_task_executor_worker.done()):
        outreach_task_executor_worker = asyncio.create_task(_run_outreach_task_executor_worker())


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "platform_sop_worker": sop_platform_task_service.runtime_status(),
    }


@app.get("/chat")
async def chat_info() -> dict[str, str]:
    return {
        "status": "ok",
        "message": "Use POST /chat with content, customer_id, corp_id, conversation_history, and optional file_image.",
    }


@app.get("/reply")
async def reply_info() -> dict[str, str]:
    return {
        "status": "ok",
        "message": "Use POST /reply for system integrations, or POST /reply/workflow-compatible for Coze-style payloads.",
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


def _reject_legacy_outreach_mutation() -> None:
    raise HTTPException(status_code=410, detail="旧 Outreach 已转为历史只读")


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


@app.post("/admin/sop-reply-packs/event-first-add-templates", dependencies=[Depends(require_api_key)])
async def admin_append_event_first_add_templates() -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="主动事件话术已迁移到第三方 SOP 平台")


@app.get("/admin/precision-qa-playbook", dependencies=[Depends(require_api_key)])
async def admin_precision_qa_playbook() -> dict[str, Any]:
    return precision_qa_playbook_service.load()


@app.put("/admin/precision-qa-playbook", dependencies=[Depends(require_api_key)])
async def admin_update_precision_qa_playbook(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return precision_qa_playbook_service.save(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, _: None = Depends(require_api_key)) -> ChatResponse:
    request = await transcribe_voice_request(request, voice_transcription_client)
    response = await run_chat(request)
    _record_http_response_body(response.request_id, response.model_dump())
    return response


@app.post("/reply", response_model=ChatResponse)
async def reply(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_external_api_key),
) -> ChatResponse:
    request = await platform_voice_batch_coordinator.prepare(request, voice_transcription_client)
    response = await chat_runtime.run_platform_reply(request, background_tasks=background_tasks)
    _record_http_response_body(response.request_id, response.model_dump())
    return response


@app.post("/chat/workflow-compatible")
async def chat_workflow_compatible(
    payload: dict[str, Any] = Body(...),
    _: None = Depends(require_api_key),
) -> JSONResponse:
    return await workflow_compatible_reply(payload, interface_version="v1")


@app.post("/reply/workflow-compatible")
async def reply_workflow_compatible(
    payload: dict[str, Any] = Body(...),
    background_tasks: BackgroundTasks = None,
    _: None = Depends(require_external_api_key),
) -> JSONResponse:
    return await workflow_compatible_reply(payload, platform_async=True, background_tasks=background_tasks, interface_version="v1")


async def workflow_compatible_reply(
    payload: dict[str, Any],
    *,
    platform_async: bool = False,
    background_tasks: BackgroundTasks | None = None,
    interface_version: str = "v1",
) -> JSONResponse:
    try:
        request = normalize_workflow_request(payload)
    except ValueError as exc:
        return JSONResponse(status_code=400, content=workflow_error_response(str(exc)))
    _attach_request_interface_version(request, interface_version)
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
    version = "v2" if str(interface_version).strip().lower() == "v2" else "v1"
    context = dict(request.request_context or {})
    context["interface_version"] = version
    context["api_version"] = version
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
    detail["raw_log"] = trace_logger.read_run(request_id)
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


@app.get("/admin/outreach/candidates", dependencies=[Depends(require_api_key)])
async def admin_outreach_candidates(
    limit: int = 50,
    silent_minutes_min: int = 60,
    outreach_status: str = "",
    lifecycle_stage: str = "",
    no_plan_only: bool = False,
    keyword: str = "",
) -> dict[str, Any]:
    return {
        "items": outreach_service.list_candidates(
            limit=limit,
            silent_minutes_min=silent_minutes_min,
            outreach_status=outreach_status,
            lifecycle_stage=lifecycle_stage,
            no_plan_only=no_plan_only,
            keyword=keyword,
        )
    }


@app.get("/admin/outreach/dashboard", dependencies=[Depends(require_api_key)])
async def admin_outreach_dashboard() -> dict[str, Any]:
    return {
        **outreach_service.dashboard_stats(),
        "worker": {
            "enabled": False,
            "mode": "retired_read_only",
            "poll_seconds": settings.outreach_auto_send_poll_seconds,
            "batch_size": settings.outreach_auto_send_batch_size,
            "before_send_retry_seconds": settings.outreach_before_send_retry_seconds,
        },
        "plan_monitor": {
            "enabled": settings.outreach_plan_monitor_enabled or settings.outreach_first_day_silence_enabled,
            "day2_plus_enabled": settings.outreach_plan_monitor_enabled,
            "first_day_enabled": settings.outreach_first_day_silence_enabled,
            "poll_seconds": settings.outreach_plan_monitor_poll_seconds,
            "silent_minutes": settings.outreach_plan_monitor_silent_minutes,
            "first_day_silent_minutes": settings.outreach_first_day_silence_minutes,
            "batch_size": settings.outreach_plan_monitor_batch_size,
            "auto_activate": settings.outreach_plan_monitor_auto_activate,
            **outreach_service.monitor_status(),
        },
        "platform_sop_worker": {
            "enabled": settings.sop_platform_pull_enabled,
            "shadow_mode": settings.sop_platform_shadow_mode,
            "poll_seconds": settings.sop_platform_poll_seconds,
            "batch_size": settings.sop_platform_batch_size,
            "task_concurrency": settings.sop_platform_task_concurrency,
            "queue_size": settings.sop_platform_queue_size,
            "recovery_concurrency": settings.sop_platform_recovery_concurrency,
            "model_timeout_seconds": settings.sop_platform_model_timeout_seconds,
            "max_task_age_seconds": settings.sop_platform_max_task_age_seconds,
            **sop_platform_task_service.runtime_status(),
        },
    }


@app.get("/admin/outreach/customers/{customer_id}/detail", dependencies=[Depends(require_api_key)])
async def admin_outreach_customer_detail(
    customer_id: str,
    corp_id: str = "",
    wechat: str = "",
    external_userid: str = "",
) -> dict[str, Any]:
    detail = outreach_service.customer_detail(
        customer_id=customer_id,
        corp_id=corp_id,
        wechat=wechat,
        external_userid=external_userid,
    )
    if not detail:
        raise HTTPException(status_code=400, detail="完整的客服账号边界是读取客户详情的前提")
    return detail


@app.get("/admin/outreach/sops", dependencies=[Depends(require_api_key)])
async def admin_outreach_sop_plans(limit: int = 100) -> dict[str, Any]:
    return {"items": outreach_service.list_sop_plans(limit=limit)}


@app.post("/admin/outreach/sops", dependencies=[Depends(require_api_key)])
async def admin_outreach_create_sop_plan(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _reject_legacy_outreach_mutation()
    try:
        return outreach_service.create_sop_plan(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/admin/outreach/sops/{sop_plan_id}", dependencies=[Depends(require_api_key)])
async def admin_outreach_update_sop_plan(sop_plan_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _reject_legacy_outreach_mutation()
    try:
        return outreach_service.update_sop_plan(sop_plan_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="SOP plan not found") from exc


@app.delete("/admin/outreach/sops/{sop_plan_id}", dependencies=[Depends(require_api_key)])
async def admin_outreach_delete_sop_plan(sop_plan_id: str) -> dict[str, Any]:
    _reject_legacy_outreach_mutation()
    if not outreach_service.delete_sop_plan(sop_plan_id):
        raise HTTPException(status_code=404, detail="SOP plan not found")
    return {"ok": True, "id": sop_plan_id}


@app.post("/admin/outreach/sops/{sop_plan_id}/run", dependencies=[Depends(require_api_key)])
async def admin_outreach_run_sop_plan(
    sop_plan_id: str,
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    _reject_legacy_outreach_mutation()
    payload = payload or {}
    try:
        return await outreach_service.run_sop_plan(
            sop_plan_id,
            limit=int(payload.get("limit") or 20),
            activate=bool(payload.get("activate")),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="SOP plan not found") from exc


@app.post("/admin/outreach/customers/{customer_id}/refresh-conversation", dependencies=[Depends(require_api_key)])
async def admin_outreach_refresh_conversation(
    customer_id: str,
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    payload = payload or {}
    try:
        limit = max(1, min(int(payload.get("limit") or 50), 50))
    except (TypeError, ValueError):
        limit = 50
    try:
        return await outreach_service.refresh_customer_conversation(
            customer_id=customer_id,
            corp_id=str(payload.get("corp_id") or ""),
            user_id=str(payload.get("user_id") or ""),
            wechat=str(payload.get("wechat") or ""),
            external_userid=str(payload.get("external_userid") or ""),
            limit=limit,
        )
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        error_code, _warning = classify_conversation_refresh_error(exc)
        cached = outreach_service.cached_customer_conversation(
            customer_id,
            corp_id=str(payload.get("corp_id") or ""),
            wechat=str(payload.get("wechat") or ""),
            external_userid=str(payload.get("external_userid") or ""),
            limit=limit,
            error=detail,
        )
        if cached:
            return cached
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "error": error_code,
                "detail": detail,
            },
        )


@app.post("/admin/outreach/plans/generate", dependencies=[Depends(require_api_key)])
async def admin_outreach_generate_plan(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _reject_legacy_outreach_mutation()
    customer_id = str(payload.get("customer_id") or "").strip()
    if not customer_id:
        raise HTTPException(status_code=400, detail="customer_id is required")
    try:
        return await outreach_service.generate_plan(
            customer_id=customer_id,
            corp_id=str(payload.get("corp_id") or ""),
            user_id=str(payload.get("user_id") or ""),
            wechat=str(payload.get("wechat") or ""),
            external_userid=str(payload.get("external_userid") or ""),
            current_stage=str(payload.get("current_stage") or ""),
            business_goal=str(payload.get("business_goal") or ""),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "error": "outreach_plan_generation_failed",
                "detail": f"{type(exc).__name__}: {exc}",
            },
        )


@app.get("/admin/outreach/plans/{plan_id}", dependencies=[Depends(require_api_key)])
async def admin_outreach_plan(plan_id: str) -> dict[str, Any]:
    detail = outreach_service.get_plan(plan_id)
    if not detail:
        raise HTTPException(status_code=404, detail="plan not found")
    return detail


@app.post("/admin/outreach/plans/{plan_id}/activate", dependencies=[Depends(require_api_key)])
async def admin_outreach_activate_plan(plan_id: str) -> dict[str, Any]:
    _reject_legacy_outreach_mutation()
    return outreach_service.activate_plan(plan_id)


@app.post("/admin/outreach/plans/{plan_id}/pause", dependencies=[Depends(require_api_key)])
async def admin_outreach_pause_plan(plan_id: str) -> dict[str, Any]:
    _reject_legacy_outreach_mutation()
    return outreach_service.pause_plan(plan_id)


@app.post("/admin/outreach/plans/{plan_id}/resume", dependencies=[Depends(require_api_key)])
async def admin_outreach_resume_plan(plan_id: str) -> dict[str, Any]:
    _reject_legacy_outreach_mutation()
    return outreach_service.resume_plan(plan_id)


@app.post("/admin/outreach/plans/{plan_id}/cancel", dependencies=[Depends(require_api_key)])
async def admin_outreach_cancel_plan(plan_id: str) -> dict[str, Any]:
    _reject_legacy_outreach_mutation()
    return outreach_service.cancel_plan(plan_id)


@app.post("/admin/outreach/tasks/{task_id}/preview", dependencies=[Depends(require_api_key)])
async def admin_outreach_preview_task(task_id: str) -> dict[str, Any]:
    _reject_legacy_outreach_mutation()
    return await outreach_service.preview_task(task_id)


@app.post("/admin/outreach/tasks/{task_id}/execute", dependencies=[Depends(require_api_key)])
async def admin_outreach_execute_task(task_id: str) -> dict[str, Any]:
    _reject_legacy_outreach_mutation()
    return await outreach_service.execute_task(task_id)


@app.post("/admin/outreach/run-due", dependencies=[Depends(require_api_key)])
async def admin_outreach_run_due(limit: int = 20) -> dict[str, Any]:
    _reject_legacy_outreach_mutation()
    return await outreach_service.execute_due_tasks(limit=limit)


@app.get("/admin/outreach/events", dependencies=[Depends(require_api_key)])
async def admin_outreach_events(
    limit: int = 100,
    customer_id: str = "",
    plan_id: str = "",
) -> dict[str, Any]:
    return {"items": outreach_service.list_events(limit=limit, customer_id=customer_id, plan_id=plan_id)}


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
