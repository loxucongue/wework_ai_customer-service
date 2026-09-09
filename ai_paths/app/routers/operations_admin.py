from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.config import Settings
from app.runtime_services import ControlServices
from app.services.run_observability import compact_admin_run_detail, enrich_admin_observability_v3
from app.services.run_observability_summary import (
    build_node_observability,
    build_run_observability,
    sanitize_debug_payload,
)

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

    @router.get("/admin/internal-work-items", dependencies=[Depends(require_api_key)])
    def internal_work_items(
        work_type: str = "",
        status: str = "",
        store_id: str = "",
        detail_kind: str = "",
        started_from: str = "",
        started_to: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        try:
            return repository.list_internal_work_items(
                work_type=work_type,
                status=status,
                store_id=store_id,
                detail_kind=detail_kind,
                started_from=started_from,
                started_to=started_to,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/admin/internal-work-items/{item_id}", dependencies=[Depends(require_api_key)])
    def update_internal_work_item(
        item_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        try:
            result = repository.update_internal_work_item(
                item_id=item_id,
                status=str(payload.get("status") or ""),
                resolved_by=str(payload.get("resolved_by") or ""),
                resolution_note=str(payload.get("resolution_note") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result.get("status") == "not_found":
            raise HTTPException(status_code=404, detail="Internal work item not found")
        return result

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
    def v3_strategy_analytics_summary(
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
    def v3_strategy_analytics_by_checkpoint(
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
    def v3_strategy_analytics_by_sequence(
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
    def v3_strategy_analytics_by_script(
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
    def v3_strategy_analytics_failures(
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
        retrieval_mode: str = "",
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
            retrieval_mode=retrieval_mode,
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
    def v3_strategy_analytics_by_intent(
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
    def v3_strategy_analytics_by_emotion(
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
    def v3_strategy_analytics_by_closing(
        started_from: str = "", started_to: str = "", corp_id: str = "", wechat: str = "",
        checkpoint_code: str = "", sequence_id: str = "", script_id: str = "",
        action_code: str = "", fallback_used: bool | None = None,
        intent_code: str = "", emotion_code: str = "", closing_sequence_key: str = "",
        closing_action: str = "", decision_status: str = "", retrieval_mode: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return repository.v3_strategy_analytics_by_dimension(
            dimension="closing", started_from=started_from, started_to=started_to, corp_id=corp_id,
            wechat=wechat, checkpoint_code=checkpoint_code, sequence_id=sequence_id,
            script_id=script_id, action_code=action_code, fallback_used=fallback_used,
            intent_code=intent_code, emotion_code=emotion_code,
            closing_sequence_key=closing_sequence_key, closing_action=closing_action,
            decision_status=decision_status, retrieval_mode=retrieval_mode, limit=limit,
        )

    @router.get("/admin/v3-strategy-analytics/by-closing-rule", dependencies=[Depends(require_api_key)])
    def v3_strategy_analytics_by_closing_rule(
        started_from: str = "",
        started_to: str = "",
        corp_id: str = "",
        wechat: str = "",
        closing_rule_id: str = "",
        closing_sequence_key: str = "",
        closing_action: str = "",
        closing_catalog_status: str = "",
        closing_rule_match_status: str = "",
        closing_constraint_status: str = "",
        decision_status: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return repository.v3_strategy_analytics_by_dimension(
            dimension="closing_rule",
            started_from=started_from,
            started_to=started_to,
            corp_id=corp_id,
            wechat=wechat,
            closing_rule_id=closing_rule_id,
            closing_sequence_key=closing_sequence_key,
            closing_action=closing_action,
            closing_catalog_status=closing_catalog_status,
            closing_rule_match_status=closing_rule_match_status,
            closing_constraint_status=closing_constraint_status,
            decision_status=decision_status,
            limit=limit,
        )

    @router.get("/admin/v3-strategy-analytics/transitions", dependencies=[Depends(require_api_key)])
    def v3_strategy_analytics_transitions(
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

    @router.get("/admin/runs/{request_id}/nodes/{node_id}", dependencies=[Depends(require_api_key)])
    async def run_node_detail(request_id: str, node_id: str) -> dict[str, Any]:
        trace = repository.get_run_node_trace(request_id=request_id, node_id=node_id)
        if not trace:
            raise HTTPException(status_code=404, detail="Run node trace not found or expired")
        return {
            "node": build_node_observability(trace),
            "trace": sanitize_debug_payload(trace),
            "data_availability": {
                "status": "available",
                "snapshot_compacted": True,
                "notice": "节点输入输出来自现有留存，已脱敏且可能截断。",
            },
        }

    @router.get("/admin/runs/{request_id}", dependencies=[Depends(require_api_key)])
    async def run_detail(request_id: str, include_debug: bool = True) -> dict[str, Any]:
        detail = repository.get_run(request_id, include_debug=include_debug)
        if not detail.get("run"):
            raise HTTPException(status_code=404, detail="Run not found")
        raw_log = services.trace_logger.read_run(request_id) if include_debug else {}
        dispatches = repository.list_message_dispatches_for_request(request_id)
        view = build_run_observability(
            detail,
            raw_log=raw_log,
            dispatches=dispatches,
        )
        detail["observability_view"] = enrich_admin_observability_v3(
            view,
            detail,
            trace_retention_days=settings.aics_trace_retention_days,
        )
        if include_debug:
            detail["raw_log"] = raw_log
            detail["message_dispatches"] = dispatches
        else:
            detail["run"] = compact_admin_run_detail(detail["run"])
            detail["node_traces"] = []
        return detail

    @router.get("/admin/runs", dependencies=[Depends(require_api_key)])
    async def runs(
        limit: int = 50,
        customer_id: str = "",
        conversation_id: str = "",
        has_error: bool | None = None,
        started_from: str = "",
        started_to: str = "",
        wechat: str = "",
        run_status: str = "",
        intent_code: str = "",
        emotion_code: str = "",
        checkpoint_code: str = "",
        decision_status: str = "",
        sequence_matched: bool | None = None,
        sequence_adopted: bool | None = None,
        script_adopted: bool | None = None,
        node_failed: bool | None = None,
    ) -> dict[str, Any]:
        return {
            "items": repository.list_runs(
                limit=limit,
                customer_id=customer_id,
                conversation_id=conversation_id,
                has_error=has_error,
                started_from=started_from,
                started_to=started_to,
                wechat=wechat,
                run_status=run_status,
                intent_code=intent_code,
                emotion_code=emotion_code,
                checkpoint_code=checkpoint_code,
                decision_status=decision_status,
                sequence_matched=sequence_matched,
                sequence_adopted=sequence_adopted,
                script_adopted=script_adopted,
                node_failed=node_failed,
            )
        }

    return router
