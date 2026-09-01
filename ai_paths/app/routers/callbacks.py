from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings
from app.runtime_services import RuntimeServices
from app.schemas import MessageDeliveryCallback

from .security import delivery_callback_dependency


logger = logging.getLogger(__name__)


def create_callbacks_router(settings: Settings, services: RuntimeServices) -> APIRouter:
    router = APIRouter()
    require_callback_token = delivery_callback_dependency(settings)

    async def finalize_delivery(dispatch: dict[str, Any]) -> None:
        source_kind = str(dispatch.get("source_kind") or "").strip()
        if source_kind == "ai_async_reply":
            services.chat_runtime.finalize_async_message_delivery(dispatch)
        elif source_kind == "sop_event":
            services.sop_delivery_compatibility_service.finalize_message_delivery(dispatch)
        elif source_kind == "outreach_task":
            services.outreach_service.finalize_message_delivery(dispatch)
        elif source_kind == "sop_platform_task":
            await services.sop_platform_task_service.finalize_message_delivery(dispatch)
        else:
            raise ValueError(f"unsupported message delivery source_kind: {source_kind or '<empty>'}")
        services.message_delivery_service.mark_finalized(str(dispatch.get("id") or ""))

    @router.post(
        "/callbacks/v1/message-delivery",
        dependencies=[Depends(require_callback_token)],
    )
    async def message_delivery_callback(payload: MessageDeliveryCallback) -> dict[str, Any]:
        try:
            result = services.message_delivery_service.accept_callback(payload)
            dispatch = result.get("dispatch") if isinstance(result.get("dispatch"), dict) else {}
            if services.message_delivery_service.needs_finalization(dispatch):
                await finalize_delivery(dispatch)
                dispatch = services.repository.get_message_dispatch(str(dispatch.get("id") or ""))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception:
            logger.exception(
                "Message delivery callback finalization failed: dispatch_id=%s",
                payload.dispatch_id,
            )
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

    return router
