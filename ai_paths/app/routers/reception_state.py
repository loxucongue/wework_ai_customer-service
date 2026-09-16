from __future__ import annotations

import logging
import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.reception_state import ReceptionConflict, ReceptionNotification
from app.services.reception_state_service import ReceptionStateService

logger = logging.getLogger(__name__)


def create_reception_state_router(settings, services) -> APIRouter:
    router = APIRouter()
    service = ReceptionStateService(services.storage_store)

    def error(code: int, reason: str) -> JSONResponse:
        return JSONResponse(status_code=code, content={"code": code, "message": reason, "data": None})

    @router.post("/api/ai/customer/reception-state")
    async def reception_state(request: Request):
        started = time.perf_counter()
        expected = settings.reception_state_api_key
        if not expected:
            return error(503, "reception_state_not_configured")
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(token.encode(), expected.encode()):
            return error(401, "invalid_token")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                return error(400, "invalid_payload")
        try:
            event = ReceptionNotification.model_validate_json(bytes(body))
        except ValidationError:
            return error(400, "invalid_payload")
        try:
            result = await run_in_threadpool(service.apply, event)
        except ReceptionConflict as exc:
            logger.info("reception_state result=conflict reason=%s", str(exc))
            return error(409, str(exc))
        except Exception:
            # Do not log driver messages/SQL parameters: they can contain identities.
            logger.warning("reception_state result=storage_unavailable")
            return error(503, "state_storage_unavailable")
        logger.info("reception_state result=%s duration_ms=%d", result["result"],
                    int((time.perf_counter() - started) * 1000))
        return {"code": 200, "message": "状态已保存", "data": result}

    return router
