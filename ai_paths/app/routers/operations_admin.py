from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.config import Settings
from app.runtime_services import RuntimeServices
from app.services.run_observability_summary import build_run_observability

from .security import api_key_dependency


def create_operations_admin_router(settings: Settings, services: RuntimeServices) -> APIRouter:
    router = APIRouter()
    require_api_key = api_key_dependency(settings)
    repository = services.repository

    @router.get("/admin/message-deliveries/{dispatch_id}", dependencies=[Depends(require_api_key)])
    async def message_delivery(dispatch_id: str) -> dict[str, Any]:
        dispatch = repository.get_message_dispatch(dispatch_id)
        if not dispatch:
            raise HTTPException(status_code=404, detail="Message delivery dispatch not found")
        return dispatch

    @router.post("/admin/store-snapshot/refresh", dependencies=[Depends(require_api_key)])
    async def refresh_store_snapshot() -> dict[str, Any]:
        snapshot = services.store_snapshot_service.refresh_snapshot(allow_existing_on_error=False)
        return {
            "status": "ok" if not snapshot.get("refresh_error") else "error",
            "generated_at": snapshot.get("generated_at", ""),
            "store_count": snapshot.get("store_count", 0),
            "refresh_error": snapshot.get("refresh_error", ""),
        }

    @router.get("/admin/precision-qa-playbook", dependencies=[Depends(require_api_key)])
    async def precision_qa_playbook() -> dict[str, Any]:
        return services.precision_qa_playbook_service.load()

    @router.put("/admin/precision-qa-playbook", dependencies=[Depends(require_api_key)])
    async def update_precision_qa_playbook(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            return services.precision_qa_playbook_service.save(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/admin/ai-sales-policy", dependencies=[Depends(require_api_key)])
    async def ai_sales_policy() -> dict[str, Any]:
        try:
            return services.ai_sales_policy_service.runtime_snapshot()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/admin/ai-sales-strategy-catalog", dependencies=[Depends(require_api_key)])
    async def ai_sales_strategy_catalog() -> dict[str, Any]:
        try:
            return services.sales_strategy_service.admin_view()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/admin/operations-dashboard", dependencies=[Depends(require_api_key)])
    async def operations_dashboard(
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

    @router.get("/admin/runs/{request_id}", dependencies=[Depends(require_api_key)])
    async def run_detail(request_id: str) -> dict[str, Any]:
        detail = repository.get_run(request_id)
        raw_log = services.trace_logger.read_run(request_id)
        dispatches = repository.list_message_dispatches_for_request(request_id)
        detail["raw_log"] = raw_log
        detail["message_dispatches"] = dispatches
        detail["observability_view"] = build_run_observability(
            detail,
            raw_log=raw_log,
            dispatches=dispatches,
        )
        return detail

    @router.get("/admin/runs", dependencies=[Depends(require_api_key)])
    async def runs(
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

    return router
