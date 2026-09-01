from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends
from fastapi.responses import JSONResponse

from app.config import Settings
from app.runtime_services import RuntimeServices
from app.schemas import ChatRequest
from app.services.workflow_compat import (
    normalize_workflow_request,
    workflow_error_response,
    workflow_response_from_chat,
)

from .security import workflow_api_key_dependency


def attach_request_interface_version(request: ChatRequest, interface_version: str) -> None:
    candidate = str(interface_version).strip().lower()
    version = candidate if candidate in {"v1", "v2", "v3"} else "v3"
    context = dict(request.request_context or {})
    context["interface_version"] = version
    context["api_version"] = version
    if version == "v3":
        context["reply_chain_mode"] = "model_led_sales_brain_v3"
        context["v3_sidecar"] = True
    request.request_context = context


def create_reply_router(settings: Settings, services: RuntimeServices) -> APIRouter:
    router = APIRouter()
    require_workflow_api_key = workflow_api_key_dependency(settings)
    chat_runtime = services.chat_runtime

    def record_http_response(request_id: str, response_body: dict[str, Any]) -> None:
        try:
            services.repository.update_run_http_response(
                request_id=request_id,
                response_body=response_body,
            )
        except Exception:
            return

    async def workflow_reply(
        payload: dict[str, Any],
        *,
        background_tasks: BackgroundTasks | None,
    ) -> JSONResponse:
        try:
            request = normalize_workflow_request(payload)
        except ValueError as exc:
            return JSONResponse(status_code=400, content=workflow_error_response(str(exc)))
        attach_request_interface_version(request, "v3")
        takeover_response = await chat_runtime.run_v3_takeover_guard(request)
        if takeover_response is not None:
            response_body = workflow_response_from_chat(takeover_response)
            record_http_response(takeover_response.request_id, response_body)
            return JSONResponse(content=response_body)
        request = await services.platform_voice_batch_coordinator.prepare(
            request,
            services.voice_transcription_client,
        )
        response = await chat_runtime.run_platform_reply(
            request,
            background_tasks=background_tasks,
        )
        response_body = workflow_response_from_chat(response)
        record_http_response(response.request_id, response_body)
        return JSONResponse(content=response_body)

    @router.post("/reply/workflow-compatible-v3")
    async def reply_workflow_compatible_v3(
        payload: dict[str, Any] = Body(...),
        background_tasks: BackgroundTasks = None,
        _: None = Depends(require_workflow_api_key),
    ) -> JSONResponse:
        return await workflow_reply(payload, background_tasks=background_tasks)

    return router
