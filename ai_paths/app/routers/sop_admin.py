from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.config import Settings
from app.runtime_services import ControlServices

from .security import api_key_dependency


def create_sop_admin_router(settings: Settings, services: ControlServices) -> APIRouter:
    router = APIRouter()
    require_api_key = api_key_dependency(settings)

    @router.get("/admin/sop-reply-packs", dependencies=[Depends(require_api_key)])
    async def sop_reply_packs() -> dict[str, Any]:
        return services.sop_reply_pack_service.load()

    @router.put("/admin/sop-reply-packs", dependencies=[Depends(require_api_key)])
    async def update_sop_reply_packs(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            return services.sop_reply_pack_service.save(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/admin/sop-objection-materials", dependencies=[Depends(require_api_key)])
    async def sop_objection_materials() -> dict[str, Any]:
        return services.sop_objection_material_service.load()

    @router.put("/admin/sop-objection-materials", dependencies=[Depends(require_api_key)])
    async def update_sop_objection_materials(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            return services.sop_objection_material_service.save(payload)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/admin/sop-events", dependencies=[Depends(require_api_key)])
    async def sop_events(
        limit: int = 50,
        event_type: str = "",
        status: str = "",
        customer_id: str = "",
        external_userid: str = "",
        has_error: str = "",
        include_chat_gate: bool = False,
    ) -> dict[str, Any]:
        return {
            "items": services.repository.list_sop_events(
                limit=limit,
                event_type=event_type,
                status=status,
                customer_id=customer_id,
                external_userid=external_userid,
                has_error=has_error,
                include_chat_gate=include_chat_gate,
            )
        }

    @router.get("/admin/sop-events/{event_id:path}", dependencies=[Depends(require_api_key)])
    async def sop_event_detail(event_id: str) -> dict[str, Any]:
        detail = services.repository.get_sop_event_detail(event_id)
        if not detail:
            raise HTTPException(status_code=404, detail="SOP event not found")
        return detail

    @router.get("/admin/sop-platform-tasks", dependencies=[Depends(require_api_key)])
    async def sop_platform_tasks(
        limit: int = 100,
        bucket: str = "",
        decision: str = "",
        task_id: str = "",
        customer_id: str = "",
        refresh_platform: bool = True,
    ) -> dict[str, Any]:
        return await services.sop_platform_task_service.admin_task_logs(
            limit=limit,
            bucket=bucket,
            decision=decision,
            task_id=task_id,
            customer_id=customer_id,
            refresh_platform=refresh_platform,
        )

    @router.get("/admin/sop-platform-runs", dependencies=[Depends(require_api_key)])
    async def sop_platform_runs(
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
        return await services.sop_platform_task_service.admin_run_logs(
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

    @router.post(
        "/admin/sop-platform-tasks/{task_id}/resend",
        dependencies=[Depends(require_api_key)],
    )
    async def resend_sop_platform_task(task_id: str) -> dict[str, Any]:
        try:
            return await services.sop_platform_task_service.admin_resend_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
