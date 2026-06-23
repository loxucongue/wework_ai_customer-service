from typing import Any

from fastapi import BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse

from app.chat_runtime import ChatRuntime
from app.config import get_settings
from app.graph.graph_builder import build_reply_graphs
from app.schemas import ChatRequest, ChatResponse
from app.services.coze_client import CozeClient
from app.services.customer_context import CustomerContextService
from app.services.customer_store_knowledge import CustomerStoreKnowledgeService
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.outreach_service import OutreachService
from app.services.outreach_send_client import OutreachSendClient
from app.services.outreach_system_client import OutreachSystemClient
from app.services.platform_agent_client import PlatformAgentClient
from app.services.storage import AppRepository, SQLiteStore
from app.services.store_service import StoreService
from app.services.store_snapshot_service import StoreSnapshotService
from app.services.trace_logger import TraceLogger
from app.services.workflow_compat import (
    normalize_workflow_request,
    workflow_error_response,
    workflow_response_from_chat,
)

settings = get_settings()
trace_logger = TraceLogger(settings)
sqlite_store = SQLiteStore(settings)
repository = AppRepository(sqlite_store)
coze_client = CozeClient(settings)
model_client = ModelClient(settings)
memory_store = CustomerMemoryStore(settings, repository)
platform_agent_client = PlatformAgentClient(settings)
outreach_send_client = OutreachSendClient(settings)
outreach_system_client = OutreachSystemClient(settings)
customer_context_service = CustomerContextService(platform_agent_client)
store_snapshot_service = StoreSnapshotService(settings, platform_agent_client)
customer_store_knowledge_service = CustomerStoreKnowledgeService(platform_agent_client, store_snapshot_service)
store_service = StoreService(platform_agent_client)
reply_graphs = build_reply_graphs(
    coze_client,
    trace_logger,
    model_client,
    memory_store,
    customer_context_service,
    customer_store_knowledge_service,
    store_service,
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
)
outreach_service = OutreachService(
    repository=repository,
    model_client=model_client,
    system_client=outreach_system_client,
)

app = FastAPI(title=settings.app_name)


@app.on_event("startup")
async def startup() -> None:
    sqlite_store.initialize()


@app.on_event("shutdown")
async def shutdown() -> None:
    await model_client.aclose()
    await coze_client.aclose()
    await outreach_send_client.aclose()
    await outreach_system_client.aclose()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


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
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or token != settings.ai_external_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing external API token",
        )


@app.post("/admin/store-snapshot/refresh", dependencies=[Depends(require_api_key)])
async def admin_refresh_store_snapshot() -> dict[str, Any]:
    snapshot = store_snapshot_service.refresh_snapshot(allow_existing_on_error=False)
    return {
        "status": "ok" if not snapshot.get("refresh_error") else "error",
        "generated_at": snapshot.get("generated_at", ""),
        "store_count": snapshot.get("store_count", 0),
        "refresh_error": snapshot.get("refresh_error", ""),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, _: None = Depends(require_api_key)) -> ChatResponse:
    response = await run_chat(request)
    _record_http_response_body(response.request_id, response.model_dump())
    return response


@app.post("/reply", response_model=ChatResponse)
async def reply(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_external_api_key),
) -> ChatResponse:
    response = await chat_runtime.run_platform_reply(request, background_tasks=background_tasks)
    _record_http_response_body(response.request_id, response.model_dump())
    return response


@app.post("/chat/workflow-compatible")
async def chat_workflow_compatible(
    payload: dict[str, Any] = Body(...),
    _: None = Depends(require_api_key),
) -> JSONResponse:
    return await workflow_compatible_reply(payload)


@app.post("/reply/workflow-compatible")
async def reply_workflow_compatible(
    payload: dict[str, Any] = Body(...),
    background_tasks: BackgroundTasks = None,
    _: None = Depends(require_external_api_key),
) -> JSONResponse:
    return await workflow_compatible_reply(payload, platform_async=True, background_tasks=background_tasks)


async def workflow_compatible_reply(
    payload: dict[str, Any],
    *,
    platform_async: bool = False,
    background_tasks: BackgroundTasks | None = None,
) -> JSONResponse:
    try:
        request = normalize_workflow_request(payload)
    except ValueError as exc:
        return JSONResponse(status_code=400, content=workflow_error_response(str(exc)))
    response = (
        await chat_runtime.run_platform_reply(request, background_tasks=background_tasks)
        if platform_async
        else await chat_runtime.run_chat(request)
    )
    response_body = workflow_response_from_chat(response)
    _record_http_response_body(response.request_id, response_body)
    return JSONResponse(content=response_body)


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
async def admin_customer_memory(customer_id: str) -> dict[str, Any]:
    return repository.load_memory(customer_id) or {}


@app.delete("/admin/customers/{customer_id}/memory", dependencies=[Depends(require_api_key)])
async def admin_clear_customer_memory(customer_id: str) -> dict[str, Any]:
    memory_store.clear(customer_id)
    return {"status": "ok", "customer_id": customer_id}


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
) -> dict[str, Any]:
    return {
        "items": outreach_service.list_candidates(
            limit=limit,
            silent_minutes_min=silent_minutes_min,
            outreach_status=outreach_status,
            lifecycle_stage=lifecycle_stage,
            no_plan_only=no_plan_only,
        )
    }


@app.post("/admin/outreach/customers/{customer_id}/refresh-conversation", dependencies=[Depends(require_api_key)])
async def admin_outreach_refresh_conversation(
    customer_id: str,
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    payload = payload or {}
    try:
        return await outreach_service.refresh_customer_conversation(
            customer_id=customer_id,
            corp_id=str(payload.get("corp_id") or ""),
            user_id=str(payload.get("user_id") or ""),
            wechat=str(payload.get("wechat") or ""),
            external_userid=str(payload.get("external_userid") or ""),
            limit=int(payload.get("limit") or 10),
        )
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        cached = outreach_service.cached_customer_conversation(
            customer_id,
            limit=int(payload.get("limit") or 10),
            error=detail,
        )
        if cached:
            return cached
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "error": "conversation_refresh_failed",
                "detail": detail,
            },
        )


@app.post("/admin/outreach/plans/generate", dependencies=[Depends(require_api_key)])
async def admin_outreach_generate_plan(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
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
    return outreach_service.activate_plan(plan_id)


@app.post("/admin/outreach/plans/{plan_id}/pause", dependencies=[Depends(require_api_key)])
async def admin_outreach_pause_plan(plan_id: str) -> dict[str, Any]:
    return outreach_service.pause_plan(plan_id)


@app.post("/admin/outreach/plans/{plan_id}/resume", dependencies=[Depends(require_api_key)])
async def admin_outreach_resume_plan(plan_id: str) -> dict[str, Any]:
    return outreach_service.resume_plan(plan_id)


@app.post("/admin/outreach/plans/{plan_id}/cancel", dependencies=[Depends(require_api_key)])
async def admin_outreach_cancel_plan(plan_id: str) -> dict[str, Any]:
    return outreach_service.cancel_plan(plan_id)


@app.post("/admin/outreach/tasks/{task_id}/preview", dependencies=[Depends(require_api_key)])
async def admin_outreach_preview_task(task_id: str) -> dict[str, Any]:
    return await outreach_service.preview_task(task_id)


@app.post("/admin/outreach/tasks/{task_id}/execute", dependencies=[Depends(require_api_key)])
async def admin_outreach_execute_task(task_id: str) -> dict[str, Any]:
    return await outreach_service.execute_task(task_id)


@app.post("/admin/outreach/run-due", dependencies=[Depends(require_api_key)])
async def admin_outreach_run_due(limit: int = 20) -> dict[str, Any]:
    return await outreach_service.execute_due_tasks(limit=limit)


@app.get("/admin/outreach/events", dependencies=[Depends(require_api_key)])
async def admin_outreach_events(
    limit: int = 100,
    customer_id: str = "",
    plan_id: str = "",
) -> dict[str, Any]:
    return {"items": outreach_service.list_events(limit=limit, customer_id=customer_id, plan_id=plan_id)}
