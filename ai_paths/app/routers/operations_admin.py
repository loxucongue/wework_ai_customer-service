from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.config import Settings
from app.runtime_services import ControlServices
from app.services.run_observability_summary import build_run_observability

from .security import api_key_dependency


def create_operations_admin_router(settings: Settings, services: ControlServices) -> APIRouter:
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

    @router.get("/admin/v3-strategy-analytics/summary", dependencies=[Depends(require_api_key)])
    async def v3_strategy_analytics_summary(
        started_from: str = "",
        started_to: str = "",
        corp_id: str = "",
        wechat: str = "",
        checkpoint_code: str = "",
        sequence_id: str = "",
        script_id: str = "",
        action_code: str = "",
        fallback_used: bool | None = None,
        intent_code: str = "",
        emotion_code: str = "",
        closing_sequence_key: str = "",
        closing_action: str = "",
        decision_status: str = "",
    ) -> dict[str, Any]:
        return repository.v3_strategy_analytics_summary(
            started_from=started_from,
            started_to=started_to,
            corp_id=corp_id,
            wechat=wechat,
            checkpoint_code=checkpoint_code,
            sequence_id=sequence_id,
            script_id=script_id,
            action_code=action_code,
            fallback_used=fallback_used,
            intent_code=intent_code,
            emotion_code=emotion_code,
            closing_sequence_key=closing_sequence_key,
            closing_action=closing_action,
            decision_status=decision_status,
        )

    @router.get("/admin/v3-strategy-analytics/by-checkpoint", dependencies=[Depends(require_api_key)])
    async def v3_strategy_analytics_by_checkpoint(
        started_from: str = "",
        started_to: str = "",
        corp_id: str = "",
        wechat: str = "",
        checkpoint_code: str = "",
        sequence_id: str = "",
        script_id: str = "",
        action_code: str = "",
        fallback_used: bool | None = None,
        intent_code: str = "",
        emotion_code: str = "",
        closing_sequence_key: str = "",
        closing_action: str = "",
        decision_status: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return repository.v3_strategy_analytics_by_dimension(
            dimension="checkpoint",
            started_from=started_from,
            started_to=started_to,
            corp_id=corp_id,
            wechat=wechat,
            checkpoint_code=checkpoint_code,
            sequence_id=sequence_id,
            script_id=script_id,
            action_code=action_code,
            fallback_used=fallback_used,
            intent_code=intent_code,
            emotion_code=emotion_code,
            closing_sequence_key=closing_sequence_key,
            closing_action=closing_action,
            decision_status=decision_status,
            limit=limit,
        )

    @router.get("/admin/v3-strategy-analytics/by-sequence", dependencies=[Depends(require_api_key)])
    async def v3_strategy_analytics_by_sequence(
        started_from: str = "",
        started_to: str = "",
        corp_id: str = "",
        wechat: str = "",
        checkpoint_code: str = "",
        sequence_id: str = "",
        script_id: str = "",
        action_code: str = "",
        fallback_used: bool | None = None,
        intent_code: str = "",
        emotion_code: str = "",
        closing_sequence_key: str = "",
        closing_action: str = "",
        decision_status: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return repository.v3_strategy_analytics_by_dimension(
            dimension="sequence",
            started_from=started_from,
            started_to=started_to,
            corp_id=corp_id,
            wechat=wechat,
            checkpoint_code=checkpoint_code,
            sequence_id=sequence_id,
            script_id=script_id,
            action_code=action_code,
            fallback_used=fallback_used,
            intent_code=intent_code,
            emotion_code=emotion_code,
            closing_sequence_key=closing_sequence_key,
            closing_action=closing_action,
            decision_status=decision_status,
            limit=limit,
        )

    @router.get("/admin/v3-strategy-analytics/by-script", dependencies=[Depends(require_api_key)])
    async def v3_strategy_analytics_by_script(
        started_from: str = "",
        started_to: str = "",
        corp_id: str = "",
        wechat: str = "",
        checkpoint_code: str = "",
        sequence_id: str = "",
        script_id: str = "",
        action_code: str = "",
        fallback_used: bool | None = None,
        intent_code: str = "",
        emotion_code: str = "",
        closing_sequence_key: str = "",
        closing_action: str = "",
        decision_status: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return repository.v3_strategy_analytics_by_dimension(
            dimension="script",
            started_from=started_from,
            started_to=started_to,
            corp_id=corp_id,
            wechat=wechat,
            checkpoint_code=checkpoint_code,
            sequence_id=sequence_id,
            script_id=script_id,
            action_code=action_code,
            fallback_used=fallback_used,
            intent_code=intent_code,
            emotion_code=emotion_code,
            closing_sequence_key=closing_sequence_key,
            closing_action=closing_action,
            decision_status=decision_status,
            limit=limit,
        )

    @router.get("/admin/v3-strategy-analytics/failures", dependencies=[Depends(require_api_key)])
    async def v3_strategy_analytics_failures(
        started_from: str = "",
        started_to: str = "",
        corp_id: str = "",
        wechat: str = "",
        checkpoint_code: str = "",
        sequence_id: str = "",
        script_id: str = "",
        action_code: str = "",
        fallback_used: bool | None = None,
        intent_code: str = "",
        emotion_code: str = "",
        closing_sequence_key: str = "",
        closing_action: str = "",
        decision_status: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return repository.v3_strategy_analytics_failures(
            started_from=started_from,
            started_to=started_to,
            corp_id=corp_id,
            wechat=wechat,
            checkpoint_code=checkpoint_code,
            sequence_id=sequence_id,
            script_id=script_id,
            action_code=action_code,
            fallback_used=fallback_used,
            intent_code=intent_code,
            emotion_code=emotion_code,
            closing_sequence_key=closing_sequence_key,
            closing_action=closing_action,
            decision_status=decision_status,
            limit=limit,
        )

    def _decision_dimension(
        dimension: str,
        *,
        started_from: str,
        started_to: str,
        corp_id: str,
        wechat: str,
        checkpoint_code: str,
        sequence_id: str,
        script_id: str,
        action_code: str,
        fallback_used: bool | None,
        intent_code: str,
        emotion_code: str,
        closing_sequence_key: str,
        closing_action: str,
        decision_status: str,
        limit: int,
    ) -> dict[str, Any]:
        return repository.v3_strategy_analytics_by_dimension(
            dimension=dimension,
            started_from=started_from,
            started_to=started_to,
            corp_id=corp_id,
            wechat=wechat,
            checkpoint_code=checkpoint_code,
            sequence_id=sequence_id,
            script_id=script_id,
            action_code=action_code,
            fallback_used=fallback_used,
            intent_code=intent_code,
            emotion_code=emotion_code,
            closing_sequence_key=closing_sequence_key,
            closing_action=closing_action,
            decision_status=decision_status,
            limit=limit,
        )

    @router.get("/admin/v3-strategy-analytics/by-intent", dependencies=[Depends(require_api_key)])
    async def v3_strategy_analytics_by_intent(
        started_from: str = "", started_to: str = "", corp_id: str = "", wechat: str = "",
        checkpoint_code: str = "", sequence_id: str = "", script_id: str = "",
        action_code: str = "", fallback_used: bool | None = None,
        intent_code: str = "", emotion_code: str = "", closing_sequence_key: str = "",
        closing_action: str = "", decision_status: str = "", limit: int = 50,
    ) -> dict[str, Any]:
        return _decision_dimension(
            "intent", started_from=started_from, started_to=started_to, corp_id=corp_id,
            wechat=wechat, checkpoint_code=checkpoint_code, sequence_id=sequence_id,
            script_id=script_id, action_code=action_code, fallback_used=fallback_used,
            intent_code=intent_code, emotion_code=emotion_code,
            closing_sequence_key=closing_sequence_key, closing_action=closing_action,
            decision_status=decision_status, limit=limit,
        )

    @router.get("/admin/v3-strategy-analytics/by-emotion", dependencies=[Depends(require_api_key)])
    async def v3_strategy_analytics_by_emotion(
        started_from: str = "", started_to: str = "", corp_id: str = "", wechat: str = "",
        checkpoint_code: str = "", sequence_id: str = "", script_id: str = "",
        action_code: str = "", fallback_used: bool | None = None,
        intent_code: str = "", emotion_code: str = "", closing_sequence_key: str = "",
        closing_action: str = "", decision_status: str = "", limit: int = 50,
    ) -> dict[str, Any]:
        return _decision_dimension(
            "emotion", started_from=started_from, started_to=started_to, corp_id=corp_id,
            wechat=wechat, checkpoint_code=checkpoint_code, sequence_id=sequence_id,
            script_id=script_id, action_code=action_code, fallback_used=fallback_used,
            intent_code=intent_code, emotion_code=emotion_code,
            closing_sequence_key=closing_sequence_key, closing_action=closing_action,
            decision_status=decision_status, limit=limit,
        )

    @router.get("/admin/v3-strategy-analytics/by-closing", dependencies=[Depends(require_api_key)])
    async def v3_strategy_analytics_by_closing(
        started_from: str = "", started_to: str = "", corp_id: str = "", wechat: str = "",
        checkpoint_code: str = "", sequence_id: str = "", script_id: str = "",
        action_code: str = "", fallback_used: bool | None = None,
        intent_code: str = "", emotion_code: str = "", closing_sequence_key: str = "",
        closing_action: str = "", decision_status: str = "", limit: int = 50,
    ) -> dict[str, Any]:
        return _decision_dimension(
            "closing", started_from=started_from, started_to=started_to, corp_id=corp_id,
            wechat=wechat, checkpoint_code=checkpoint_code, sequence_id=sequence_id,
            script_id=script_id, action_code=action_code, fallback_used=fallback_used,
            intent_code=intent_code, emotion_code=emotion_code,
            closing_sequence_key=closing_sequence_key, closing_action=closing_action,
            decision_status=decision_status, limit=limit,
        )

    @router.get("/admin/v3-strategy-analytics/transitions", dependencies=[Depends(require_api_key)])
    async def v3_strategy_analytics_transitions(
        started_from: str = "", started_to: str = "", corp_id: str = "", wechat: str = "",
        checkpoint_code: str = "", sequence_id: str = "", script_id: str = "",
        action_code: str = "", fallback_used: bool | None = None,
        intent_code: str = "", emotion_code: str = "", closing_sequence_key: str = "",
        closing_action: str = "", decision_status: str = "", limit: int = 50,
    ) -> dict[str, Any]:
        return _decision_dimension(
            "transitions", started_from=started_from, started_to=started_to, corp_id=corp_id,
            wechat=wechat, checkpoint_code=checkpoint_code, sequence_id=sequence_id,
            script_id=script_id, action_code=action_code, fallback_used=fallback_used,
            intent_code=intent_code, emotion_code=emotion_code,
            closing_sequence_key=closing_sequence_key, closing_action=closing_action,
            decision_status=decision_status, limit=limit,
        )

    @router.post("/admin/v3-strategy-analytics/outcomes/refresh", dependencies=[Depends(require_api_key)])
    async def refresh_v3_strategy_outcomes(limit: int = 100) -> dict[str, Any]:
        provider = services.strategy_outcome_provider
        provider.reset_batch()
        result = await asyncio.to_thread(
            repository.refresh_v3_strategy_outcomes,
            limit=limit,
            order_snapshot_provider=provider if provider.enabled else None,
            order_provider_max_concurrency=settings.v3_strategy_analytics_outcome_max_concurrency,
        )
        return {**result, "order_provider": provider.runtime_status()}

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
