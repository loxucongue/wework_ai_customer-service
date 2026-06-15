from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse

from app.chat_runtime import ChatRuntime
from app.config import get_settings
from app.graph.graph_builder import build_graph
from app.schemas import ChatRequest, ChatResponse
from app.services.coze_client import CozeClient
from app.services.customer_context import CustomerContextService
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.appointment_opening_service import AppointmentOpeningService
from app.services.platform_agent_client import PlatformAgentClient
from app.services.storage import AppRepository, SQLiteStore
from app.services.store_service import StoreService
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
customer_context_service = CustomerContextService(platform_agent_client)
store_service = StoreService(platform_agent_client)
appointment_opening_service = AppointmentOpeningService(platform_agent_client)
compiled_graph = build_graph(
    coze_client,
    trace_logger,
    model_client,
    memory_store,
    customer_context_service,
    store_service,
    appointment_opening_service,
)
chat_runtime = ChatRuntime(compiled_graph, trace_logger, repository)

app = FastAPI(title=settings.app_name)


@app.on_event("startup")
async def startup() -> None:
    sqlite_store.initialize()


@app.on_event("shutdown")
async def shutdown() -> None:
    await model_client.aclose()
    await coze_client.aclose()


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


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, _: None = Depends(require_api_key)) -> ChatResponse:
    return await run_chat(request)


@app.post("/reply", response_model=ChatResponse)
async def reply(request: ChatRequest, _: None = Depends(require_external_api_key)) -> ChatResponse:
    return await run_chat(request)


@app.post("/chat/workflow-compatible")
async def chat_workflow_compatible(
    payload: dict[str, Any] = Body(...),
    _: None = Depends(require_api_key),
) -> JSONResponse:
    return await workflow_compatible_reply(payload)


@app.post("/reply/workflow-compatible")
async def reply_workflow_compatible(
    payload: dict[str, Any] = Body(...),
    _: None = Depends(require_external_api_key),
) -> JSONResponse:
    return await workflow_compatible_reply(payload)


async def workflow_compatible_reply(payload: dict[str, Any]) -> JSONResponse:
    try:
        request = normalize_workflow_request(payload)
    except ValueError as exc:
        return JSONResponse(status_code=400, content=workflow_error_response(str(exc)))
    response = await chat_runtime.run_chat(request)
    return JSONResponse(content=workflow_response_from_chat(response))


async def run_chat(request: ChatRequest) -> ChatResponse:
    return await chat_runtime.run_chat(request)


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
