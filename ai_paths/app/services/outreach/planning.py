from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .first_day import (
    FIRST_DAY_CONTRACT_VERIFIER_PROMPT,
    FIRST_DAY_CONTRACT_VERIFIER_PROMPT_VERSION,
    FIRST_DAY_PLAN_WRITER_PROMPT,
    FIRST_DAY_PLAN_WRITER_PROMPT_VERSION,
    FIRST_DAY_SCENE_ANALYST_PROMPT,
    FIRST_DAY_SCENE_ANALYST_PROMPT_VERSION,
    FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT,
    FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT_VERSION,
    FIRST_DAY_SILENCE_TRIGGER_TYPE,
    FIRST_DAY_SOP_CATEGORY_ORDER,
    FIRST_DAY_SOP_SCENE_BY_CATEGORY,
    OUTREACH_ASSET_STRATEGIES,
    OUTREACH_PLAN_REVIEW_SYSTEM_PROMPT,
    OUTREACH_PLAN_SCHEMA_REPAIR_SYSTEM_PROMPT,
    OUTREACH_PLAN_SYSTEM_PROMPT,
    S10_OUTREACH_CONTEXT,
    _add_minutes,
    _authoritative_first_added_at,
    _bool,
    _completed_cycle_blocks_automatic_replan,
    _compose_outreach_messages,
    _conversation_activity_from_context,
    _conversation_fingerprint,
    _conversation_id_from_response,
    _first_day_available_sources_by_scene,
    _first_day_configured_assets_for_step,
    _first_day_final_plan_error,
    _first_day_full_retry_delay_seconds,
    _first_day_internal_activity_quote_evidence,
    _first_day_materialized_sop_messages,
    _first_day_message_policy_error,
    _first_day_outreach_plan_error,
    _first_day_scene_analysis_error,
    _first_day_sop_pack_for_step,
    _first_day_sop_pack_texts,
    _first_day_upgrade_scene_repeat_repair_to_replan,
    _first_day_verifier_error,
    _first_day_writer_payload,
    _int,
    _is_first_day_opened_silence_trigger,
    _list_strings,
    _media_url_identity,
    _merge_first_day_scene_schema_repair,
    _message_time_iso,
    _missing_outreach_identity_fields,
    _normalize_first_day_outreach_schedule,
    _normalize_first_day_repaired_plan,
    _normalize_first_day_scene_analysis,
    _normalize_outreach_plan_response,
    _normalize_outreach_schedule,
    _outreach_plan_context_error,
    _outreach_plan_structure_error,
    _parse_iso,
    _plan_step_texts,
    _scheduled_at_for_strategy_step,
    _selected_strategy,
    _selected_strategy_steps,
    _string,
    _task_content_sources,
    _valid_activity_quote_evidence,
    appointment_blocker_materials,
    asyncio,
    build_appointment_blocker_asset_catalog,
    build_appointment_blocker_scene_index,
    build_customer_scope,
    build_outreach_activity_quote_fact,
    customer_relation_is_deleted,
    dumps,
    enrich_recent_outreach_media,
    hashlib,
    normalize_customer_relation,
    outreach_customer_fact_snapshot,
    personalized_order_eligibility,
    personalized_payment_collection_eligibility,
    recent_outreach_media,
    resolve_case_asset,
    resolve_configured_asset,
    time,
    utc_now_iso,
)


class PlanGenerator:
    def __init__(
        self,
        *,
        repository: Any,
        model_client: Any,
        system_client: Any,
        customer_context_service: Any,
        precision_qa_playbook_service: Any,
        sop_reply_pack_service: Any,
        coze_client: Any,
        sales_strategy_service: Any,
    ) -> None:
        self.repository = repository
        self.model_client = model_client
        self.system_client = system_client
        self.customer_context_service = customer_context_service
        self.precision_qa_playbook_service = precision_qa_playbook_service
        self.sop_reply_pack_service = sop_reply_pack_service
        self.coze_client = coze_client
        self.sales_strategy_service = sales_strategy_service
        self._plan_locks: dict[str, asyncio.Lock] = {}

    async def generate_plan(
        self,
        *,
        customer_id: str,
        corp_id: str = "",
        user_id: str = "",
        wechat: str = "",
        external_userid: str = "",
        current_stage: str = "",
        business_goal: str = "",
        sop_plan_id: str = "",
        source_context: dict[str, Any] | None = None,
        trigger_context: dict[str, Any] | None = None,
        workflow_run_id: str = "",
    ) -> dict[str, Any]:
        started = time.perf_counter()
        first_day_trigger = _is_first_day_opened_silence_trigger(trigger_context)
        run_creator = getattr(self.repository, "create_first_day_outreach_run", None)
        if first_day_trigger and not workflow_run_id and callable(run_creator):
            trigger = trigger_context if isinstance(trigger_context, dict) else {}
            run = run_creator(
                customer_id=customer_id,
                corp_id=corp_id,
                user_id=user_id,
                wechat=wechat,
                external_userid=external_userid,
                trigger_type=FIRST_DAY_SILENCE_TRIGGER_TYPE,
                conversation_fingerprint=_string(trigger.get("conversation_fingerprint")),
                input_snapshot={"trigger_context": trigger},
            )
            workflow_run_id = _string(run.get("workflow_run_id"))
        try:
            return await self._build_plan(
                customer_id=customer_id,
                corp_id=corp_id,
                user_id=user_id,
                wechat=wechat,
                external_userid=external_userid,
                current_stage=current_stage,
                business_goal=business_goal,
                sop_plan_id=sop_plan_id,
                source_context=source_context,
                trigger_context=trigger_context,
                workflow_run_id=workflow_run_id,
            )
        except Exception as exc:
            if workflow_run_id:
                current = self.repository.get_first_day_outreach_run(
                    workflow_run_id,
                    include_related=False,
                )
                retry_count = int(current.get("retry_count") or 0)
                retry_delay = _first_day_full_retry_delay_seconds(str(exc), retry_count)
                terminal = {"blocked", "sent", "cancelled", "completed"}
                if _string(current.get("status")) not in terminal:
                    self.repository.update_first_day_outreach_run(
                        workflow_run_id,
                        status="failed",
                        reason_code="workflow_retry_scheduled" if retry_delay else "workflow_failed",
                        final_decision="retry_pending" if retry_delay else "failed",
                        next_retry_at=(
                            datetime.now(timezone.utc) + timedelta(seconds=retry_delay)
                        ).isoformat() if retry_delay else "",
                        error_node=_string(current.get("error_node")) or "plan_generation",
                        error_type=_string(current.get("error_type")) or type(exc).__name__,
                        error_message=_string(current.get("error_message")) or str(exc)[:4000],
                        finished_at=utc_now_iso(),
                    )
            raise
        finally:
            if workflow_run_id:
                self.repository.update_first_day_outreach_run(
                    workflow_run_id,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                )

    async def _build_plan(
        self,
        *,
        customer_id: str,
        corp_id: str = "",
        user_id: str = "",
        wechat: str = "",
        external_userid: str = "",
        current_stage: str = "",
        business_goal: str = "",
        sop_plan_id: str = "",
        source_context: dict[str, Any] | None = None,
        trigger_context: dict[str, Any] | None = None,
        workflow_run_id: str = "",
    ) -> dict[str, Any]:
        prepared = await self._prepare_plan_context(
            customer_id=customer_id,
            corp_id=corp_id,
            user_id=user_id,
            wechat=wechat,
            external_userid=external_userid,
            current_stage=current_stage,
            business_goal=business_goal,
            sop_plan_id=sop_plan_id,
            source_context=source_context,
            trigger_context=trigger_context,
            workflow_run_id=workflow_run_id,
        )
        if "terminal_result" in prepared:
            return prepared["terminal_result"]
        response = await self._decide_plan(prepared)
        return await self._materialize_plan(prepared, response)

    async def _prepare_plan_context(
        self,
        *,
        customer_id: str,
        corp_id: str,
        user_id: str,
        wechat: str,
        external_userid: str,
        current_stage: str,
        business_goal: str,
        sop_plan_id: str,
        source_context: dict[str, Any] | None,
        trigger_context: dict[str, Any] | None,
        workflow_run_id: str,
    ) -> dict[str, Any]:
        first_day_trigger = _is_first_day_opened_silence_trigger(trigger_context)
        context = dict(
            source_context
            or self.repository.recent_customer_context(
                customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
            )
        )
        customer_relation = (
            context.get("customer_relation")
            if isinstance(context.get("customer_relation"), dict)
            else {}
        )
        if not customer_relation.get("available"):
            try:
                refreshed = await self.refresh_customer_conversation(
                    customer_id=customer_id,
                    corp_id=corp_id,
                    user_id=user_id,
                    wechat=wechat,
                    external_userid=external_userid,
                    limit=50,
                )
            except Exception as exc:
                result = self._relation_plan_skip(
                    customer_id=customer_id,
                    corp_id=corp_id,
                    wechat=wechat,
                    external_userid=external_userid,
                    reason="customer_relation_check_failed",
                    relation={
                        "available": False,
                        "status": "unknown",
                        "is_deleted": False,
                        "deleted_at": "",
                        "updated_at": "",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    trigger_context=trigger_context or {},
                )
                if workflow_run_id:
                    self.repository.update_first_day_outreach_run(
                        workflow_run_id,
                        status="failed",
                        reason_code="customer_relation_check_failed",
                        final_decision="failed",
                        error_node="conversation_refresh",
                        error_type=type(exc).__name__,
                        error_message=str(exc)[:4000],
                        finished_at=utc_now_iso(),
                    )
                return {"terminal_result": result}
            customer_relation = (
                refreshed.get("customer_relation")
                if isinstance(refreshed.get("customer_relation"), dict)
                else {}
            )
            refreshed_messages = refreshed.get("messages")
            if isinstance(refreshed_messages, list):
                context["recent_messages"] = refreshed_messages[-50:]
        context["customer_relation"] = customer_relation
        if not customer_relation.get("available"):
            result = self._relation_plan_skip(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                reason="customer_relation_unavailable",
                relation=customer_relation,
                trigger_context=trigger_context or {},
            )
            if workflow_run_id:
                self.repository.update_first_day_outreach_run(
                    workflow_run_id,
                    status="blocked",
                    reason_code="customer_relation_unavailable",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
            return {"terminal_result": result}
        if customer_relation_is_deleted(customer_relation):
            result = self._relation_plan_skip(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                reason="customer_deleted",
                relation=customer_relation,
                trigger_context=trigger_context or {},
            )
            if workflow_run_id:
                self.repository.update_first_day_outreach_run(
                    workflow_run_id,
                    status="blocked",
                    reason_code="customer_deleted",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
            return {"terminal_result": result}
        memory = context.get("memory") or {}
        recent_messages = context.get("recent_messages") or []
        conversation_activity = _conversation_activity_from_context(
            existing=context.get("conversation_activity"),
            memory=memory,
            recent_messages=recent_messages,
        )
        reply_wait_minutes = _int(conversation_activity.get("reply_wait_minutes"), 0)
        customer_silence_minutes = _int(
            conversation_activity.get("customer_silence_minutes"),
            0,
        )
        goal = business_goal or "推动客户重新开口，并逐步推进到店或支付10元预约金"
        appointment_playbook = self._appointment_blocker_playbook()
        appointment_material_catalog = appointment_blocker_materials(appointment_playbook)
        first_day_sop_sequence = self._first_day_sop_sequence(required=first_day_trigger)
        asset_catalog = (
            build_appointment_blocker_asset_catalog(appointment_playbook)
            + self._first_day_sop_asset_catalog(first_day_sop_sequence)
        )
        recent_media = enrich_recent_outreach_media(
            recent_outreach_media(recent_messages, hours=72),
            asset_catalog,
        )
        activity_quote_fact = build_outreach_activity_quote_fact(recent_messages, memory)
        personalized_order_gate = personalized_order_eligibility(context.get("customer_context") or {})
        payment_collection_gate = personalized_payment_collection_eligibility(
            context.get("customer_context") or {},
            amount=10,
        )
        recent_sop_delivery = []
        recent_sop_delivery_loader = getattr(self.repository, "recent_sop_delivery", None)
        if callable(recent_sop_delivery_loader):
            recent_sop_delivery = recent_sop_delivery_loader(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                hours=72,
            )
        appointment_blocker_scene_index = build_appointment_blocker_scene_index(
            appointment_playbook
        )
        source_snapshot = {
            "workflow_run_id": workflow_run_id,
            "customer_id": customer_id,
            "corp_id": corp_id,
            "user_id": user_id,
            "wechat": wechat,
            "external_userid": external_userid,
            "conversation_id": _string(context.get("conversation_id")) or _string(
                (trigger_context or {}).get("conversation_id")
            ),
            "customer_fact_snapshot": outreach_customer_fact_snapshot(memory),
            "recent_messages": recent_messages,
            "conversation_activity": conversation_activity,
            "current_stage": current_stage,
            "business_goal": goal,
            "sop_plan_id": sop_plan_id,
            "offer_context": S10_OUTREACH_CONTEXT,
            "activity_quote_fact": activity_quote_fact,
            "personalized_order_gate": personalized_order_gate,
            "payment_collection_gate": payment_collection_gate,
            "trigger_context": trigger_context or {},
            "customer_context": context.get("customer_context") or {},
            "customer_relation": customer_relation,
            "asset_catalog": [
                {
                    key: asset.get(key)
                    for key in (
                        "asset_id",
                        "type",
                        "name",
                        "annotation",
                        "use_cases",
                        "avoid_when",
                        "tags",
                    )
                }
                for asset in asset_catalog
            ],
            "recent_media_delivery": recent_media,
            "recent_sop_delivery": recent_sop_delivery,
            "first_day_sop_sequence": first_day_sop_sequence,
            "appointment_blocker_scene_index": appointment_blocker_scene_index,
        }
        source_snapshot["available_sources_by_scene"] = _first_day_available_sources_by_scene(
            source_snapshot
        )
        if workflow_run_id:
            self.repository.update_first_day_outreach_run(
                workflow_run_id,
                input_snapshot_json=source_snapshot,
            )
        return {
            "customer_id": customer_id,
            "corp_id": corp_id,
            "user_id": user_id,
            "wechat": wechat,
            "external_userid": external_userid,
            "sop_plan_id": sop_plan_id,
            "trigger_context": trigger_context,
            "workflow_run_id": workflow_run_id,
            "first_day_trigger": first_day_trigger,
            "conversation_activity": conversation_activity,
            "reply_wait_minutes": reply_wait_minutes,
            "customer_silence_minutes": customer_silence_minutes,
            "appointment_material_catalog": appointment_material_catalog,
            "asset_catalog": asset_catalog,
            "recent_media": recent_media,
            "activity_quote_fact": activity_quote_fact,
            "payment_collection_gate": payment_collection_gate,
            "source_snapshot": source_snapshot,
        }

    async def _decide_plan(self, prepared: dict[str, Any]) -> dict[str, Any]:
        first_day_trigger = prepared["first_day_trigger"]
        conversation_activity = prepared["conversation_activity"]
        source_snapshot = prepared["source_snapshot"]
        unopened_first_day = first_day_trigger and _int(
            conversation_activity.get("real_customer_message_count"),
            -1,
        ) == 0
        if unopened_first_day:
            scene_analysis = _normalize_first_day_scene_analysis(
                {},
                message_count=len(source_snapshot.get("recent_messages") or []),
                source_snapshot=source_snapshot,
            )
            source_snapshot["first_day_workflow"] = {
                "scene_analysis": scene_analysis,
                "writer_result": {},
                "verifier_result": {},
                "traces": {},
                "routing_decision": "first_day_customer_not_opened",
            }
            response = {
                "should_create_plan": False,
                "stall_reason": "first_day_customer_not_opened",
                "plan_arc": "",
                "steps": [],
            }
            return response
        if first_day_trigger:
            return await self._decide_first_day_plan(prepared)
        return await self._decide_standard_plan(prepared)

    async def _decide_first_day_plan(self, prepared: dict[str, Any]) -> dict[str, Any]:
        appointment_material_catalog = prepared["appointment_material_catalog"]
        source_snapshot = prepared["source_snapshot"]
        first_day_model_snapshot, scene_analysis, analyst_trace = await self._analyze_first_day_scene(
            source_snapshot
        )
        source_snapshot["first_day_workflow"] = {
            "scene_analysis": scene_analysis,
            "writer_result": {},
            "verifier_result": {},
            "traces": {"scene_analyst": analyst_trace},
        }
        if not _bool(scene_analysis.get("eligible")):
            return {
                "should_create_plan": False,
                "stall_reason": _string(scene_analysis.get("suppress_reason"))
                or "first_day_scene_analyst_suppressed",
                "plan_arc": "",
                "steps": [],
            }
        return await self._write_first_day_plan(
            source_snapshot=source_snapshot,
            model_snapshot=first_day_model_snapshot,
            scene_analysis=scene_analysis,
            appointment_material_catalog=appointment_material_catalog,
        )

    async def _analyze_first_day_scene(
        self, source_snapshot: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        first_day_model_snapshot = dict(source_snapshot)
        scene_analysis, analyst_trace = await self._run_first_day_model_node(
            node="scene_analyst",
            prompt=FIRST_DAY_SCENE_ANALYST_PROMPT,
            prompt_version=FIRST_DAY_SCENE_ANALYST_PROMPT_VERSION,
            payload={"source_snapshot": first_day_model_snapshot},
        )
        scene_analysis = _normalize_first_day_scene_analysis(
            scene_analysis,
            message_count=len(first_day_model_snapshot.get("recent_messages") or []),
            source_snapshot=first_day_model_snapshot,
        )
        scene_error = _first_day_scene_analysis_error(
            scene_analysis,
            source_snapshot=first_day_model_snapshot,
        )
        if scene_error:
            invalid_scene_analysis = scene_analysis
            scene_analysis, repair_trace = await self._run_first_day_model_node(
                node="scene_analyst_schema_repair",
                prompt=FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT,
                prompt_version=FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT_VERSION,
                payload={
                    "source_snapshot": first_day_model_snapshot,
                    "invalid_scene_analysis": scene_analysis,
                    "schema_error": scene_error,
                    "locked_scenes": {
                        "step1": _string(scene_analysis.get("step1_scene")),
                        "step2": _string(scene_analysis.get("step2_scene")),
                    },
                    "available_sources_by_scene": first_day_model_snapshot.get(
                        "available_sources_by_scene"
                    ) or {},
                    "instruction": "只修复 JSON 结构合同并返回完整场景分析，不得改变已有事实证据。",
                },
            )
            scene_analysis = _merge_first_day_scene_schema_repair(
                invalid_scene_analysis,
                scene_analysis,
            )
            scene_analysis = _normalize_first_day_scene_analysis(
                scene_analysis,
                message_count=len(first_day_model_snapshot.get("recent_messages") or []),
                source_snapshot=first_day_model_snapshot,
            )
            analyst_trace["schema_repair"] = repair_trace
            scene_error = _first_day_scene_analysis_error(
                scene_analysis,
                source_snapshot=first_day_model_snapshot,
            )
        if scene_error:
            invalid_scene_analysis = scene_analysis
            scene_analysis, second_repair_trace = await self._run_first_day_model_node(
                node="scene_analyst_schema_repair_2",
                prompt=FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT,
                prompt_version=FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT_VERSION,
                payload={
                    "source_snapshot": first_day_model_snapshot,
                    "invalid_scene_analysis": scene_analysis,
                    "schema_error": scene_error,
                    "locked_scenes": {
                        "step1": _string(scene_analysis.get("step1_scene")),
                        "step2": _string(scene_analysis.get("step2_scene")),
                    },
                    "available_sources_by_scene": first_day_model_snapshot.get(
                        "available_sources_by_scene"
                    ) or {},
                    "instruction": "再次只修复剩余 JSON 结构错误，保留已有业务判断并返回完整对象。",
                },
            )
            scene_analysis = _merge_first_day_scene_schema_repair(
                invalid_scene_analysis,
                scene_analysis,
            )
            scene_analysis = _normalize_first_day_scene_analysis(
                scene_analysis,
                message_count=len(first_day_model_snapshot.get("recent_messages") or []),
                source_snapshot=first_day_model_snapshot,
            )
            analyst_trace["schema_repair_2"] = second_repair_trace
            scene_error = _first_day_scene_analysis_error(
                scene_analysis,
                source_snapshot=first_day_model_snapshot,
            )
        if scene_error:
            raise RuntimeError(f"first_day_scene_analysis_invalid: {scene_error}")
        return first_day_model_snapshot, scene_analysis, analyst_trace

    async def _write_first_day_plan(
        self,
        *,
        source_snapshot: dict[str, Any],
        model_snapshot: dict[str, Any],
        scene_analysis: dict[str, Any],
        appointment_material_catalog: list[dict[str, Any]],
    ) -> dict[str, Any]:
        first_day_model_snapshot = model_snapshot
        writer_payload = _first_day_writer_payload(
            first_day_model_snapshot,
            scene_analysis,
            appointment_material_catalog=appointment_material_catalog,
        )
        writer_result, writer_trace = await self._run_first_day_model_node(
            node="plan_writer",
            prompt=FIRST_DAY_PLAN_WRITER_PROMPT,
            prompt_version=FIRST_DAY_PLAN_WRITER_PROMPT_VERSION,
            payload=writer_payload,
        )
        source_snapshot["first_day_workflow"]["writer_result"] = writer_result
        source_snapshot["first_day_workflow"]["traces"]["plan_writer"] = writer_trace
        normalized_writer_result = _normalize_outreach_plan_response(dict(writer_result))
        writer_structure_error = _first_day_final_plan_error(
            normalized_writer_result,
            scene_analysis=scene_analysis,
            source_snapshot=first_day_model_snapshot,
        )
        source_snapshot["first_day_workflow"]["writer_structure_error"] = writer_structure_error
        verifier_result, verifier_trace = await self._run_first_day_model_node(
            node="contract_verifier",
            prompt=FIRST_DAY_CONTRACT_VERIFIER_PROMPT,
            prompt_version=FIRST_DAY_CONTRACT_VERIFIER_PROMPT_VERSION,
            payload={
                "source_snapshot": first_day_model_snapshot,
                "scene_contract": scene_analysis,
                "candidate_plan": writer_result,
                "candidate_structure_error": writer_structure_error,
            },
        )
        verifier_result = _first_day_upgrade_scene_repeat_repair_to_replan(
            verifier_result,
            scene_analysis=scene_analysis,
        )
        verifier_error = _first_day_verifier_error(verifier_result)
        if verifier_error:
            verifier_result, verifier_repair_trace = await self._run_first_day_model_node(
                node="contract_verifier_schema_repair",
                prompt=FIRST_DAY_CONTRACT_VERIFIER_PROMPT,
                prompt_version=FIRST_DAY_CONTRACT_VERIFIER_PROMPT_VERSION,
                payload={
                    "source_snapshot": first_day_model_snapshot,
                    "scene_contract": scene_analysis,
                    "candidate_plan": writer_result,
                    "invalid_verifier_result": verifier_result,
                    "schema_error": verifier_error,
                    "instruction": "只修复审核结果 JSON 合同，不得输出或改写客户计划。",
                },
            )
            verifier_trace["schema_repair"] = verifier_repair_trace
            verifier_result = _first_day_upgrade_scene_repeat_repair_to_replan(
                verifier_result,
                scene_analysis=scene_analysis,
            )
            verifier_error = _first_day_verifier_error(verifier_result)
        if verifier_error:
            raise RuntimeError(f"first_day_contract_verifier_invalid: {verifier_error}")
        if _string(verifier_result.get("decision")) == "replan":
            original_scene_analysis = scene_analysis
            original_verifier_result = verifier_result
            original_verifier_trace = verifier_trace
            replanned_snapshot = dict(first_day_model_snapshot)
            replanned_snapshot["scene_replan_feedback"] = {
                "rejected_scene_contract": original_scene_analysis,
                "violations": list(verifier_result.get("violations") or []),
                "instructions": list(verifier_result.get("replan_instructions") or []),
                "require_different_scenes": True,
            }
            scene_analysis, replan_trace = await self._run_first_day_model_node(
                node="scene_analyst_replan",
                prompt=FIRST_DAY_SCENE_ANALYST_PROMPT,
                prompt_version=FIRST_DAY_SCENE_ANALYST_PROMPT_VERSION,
                payload={"source_snapshot": replanned_snapshot},
            )
            scene_analysis = _normalize_first_day_scene_analysis(
                scene_analysis,
                message_count=len(replanned_snapshot.get("recent_messages") or []),
                source_snapshot=replanned_snapshot,
            )
            scene_error = _first_day_scene_analysis_error(
                scene_analysis,
                source_snapshot=replanned_snapshot,
            )
            if scene_error:
                raise RuntimeError(f"first_day_scene_replan_invalid: {scene_error}")
            if (
                _string(scene_analysis.get("step1_scene"))
                == _string(original_scene_analysis.get("step1_scene"))
                and _string(scene_analysis.get("step2_scene"))
                == _string(original_scene_analysis.get("step2_scene"))
            ):
                raise RuntimeError("first_day_scene_replan_unchanged")
            writer_payload = _first_day_writer_payload(
                replanned_snapshot,
                scene_analysis,
                appointment_material_catalog=appointment_material_catalog,
            )
            writer_result, writer_trace = await self._run_first_day_model_node(
                node="plan_writer_after_replan",
                prompt=FIRST_DAY_PLAN_WRITER_PROMPT,
                prompt_version=FIRST_DAY_PLAN_WRITER_PROMPT_VERSION,
                payload=writer_payload,
            )
            normalized_writer_result = _normalize_outreach_plan_response(dict(writer_result))
            writer_structure_error = _first_day_final_plan_error(
                normalized_writer_result,
                scene_analysis=scene_analysis,
                source_snapshot=replanned_snapshot,
            )
            verifier_result, verifier_trace = await self._run_first_day_model_node(
                node="contract_verifier_after_replan",
                prompt=FIRST_DAY_CONTRACT_VERIFIER_PROMPT,
                prompt_version=FIRST_DAY_CONTRACT_VERIFIER_PROMPT_VERSION,
                payload={
                    "source_snapshot": replanned_snapshot,
                    "scene_contract": scene_analysis,
                    "candidate_plan": writer_result,
                    "candidate_structure_error": writer_structure_error,
                },
            )
            verifier_result = _first_day_upgrade_scene_repeat_repair_to_replan(
                verifier_result,
                scene_analysis=scene_analysis,
            )
            verifier_error = _first_day_verifier_error(verifier_result)
            if verifier_error:
                raise RuntimeError(
                    f"first_day_contract_verifier_after_replan_invalid: {verifier_error}"
                )
            if _string(verifier_result.get("decision")) == "replan":
                raise RuntimeError("first_day_scene_replan_exhausted")
            source_snapshot["first_day_workflow"].update(
                {
                    "original_scene_analysis": original_scene_analysis,
                    "scene_replan_verifier_result": original_verifier_result,
                    "scene_analysis": scene_analysis,
                    "writer_result": writer_result,
                    "verifier_result": verifier_result,
                    "writer_structure_error": writer_structure_error,
                }
            )
            source_snapshot["first_day_workflow"]["traces"].update(
                {
                    "contract_verifier_before_replan": original_verifier_trace,
                    "scene_analyst_replan": replan_trace,
                    "plan_writer_after_replan": writer_trace,
                    "contract_verifier_after_replan": verifier_trace,
                }
            )
        source_snapshot["first_day_workflow"]["verifier_result"] = verifier_result
        source_snapshot["first_day_workflow"]["traces"]["contract_verifier"] = verifier_trace
        if _string(verifier_result.get("decision")) == "block":
            violations = verifier_result.get("violations") or []
            response = {
                "should_create_plan": False,
                "stall_reason": _string((violations[0] if violations else {}).get("code"))
                or "first_day_contract_verifier_blocked",
                "plan_arc": "",
                "steps": [],
            }
        else:
            needs_repair = bool(writer_structure_error) or (
                _string(verifier_result.get("decision")) == "repair"
            )
            if needs_repair:
                violations = list(verifier_result.get("violations") or [])
                repair_instructions = list(verifier_result.get("repair_instructions") or [])
                if writer_structure_error and not repair_instructions:
                    violations.append(
                        {
                            "code": "deterministic_contract_error",
                            "field": "candidate_plan",
                            "evidence": writer_structure_error,
                        }
                    )
                    repair_instructions.append(
                        {
                            "field": "candidate_plan",
                            "instruction": "修复确定性合同错误，严格保留两个锁定场景和业务目标。",
                        }
                    )
                repaired_writer_result, repair_trace = await self._run_first_day_model_node(
                    node="plan_writer_repair",
                    prompt=FIRST_DAY_PLAN_WRITER_PROMPT,
                    prompt_version=FIRST_DAY_PLAN_WRITER_PROMPT_VERSION,
                    payload=_first_day_writer_payload(
                        first_day_model_snapshot,
                        scene_analysis,
                        appointment_material_catalog=appointment_material_catalog,
                        candidate_plan=writer_result,
                        violations=violations,
                        repair_instructions=repair_instructions,
                        deterministic_error=writer_structure_error,
                    ),
                )
                source_snapshot["first_day_workflow"]["writer_repair_result"] = repaired_writer_result
                source_snapshot["first_day_workflow"]["traces"]["plan_writer_repair"] = repair_trace
                response = _normalize_first_day_repaired_plan(
                    _normalize_outreach_plan_response(dict(repaired_writer_result)),
                    scene_analysis=scene_analysis,
                )
            else:
                response = normalized_writer_result
            final_error = _first_day_final_plan_error(
                response,
                scene_analysis=scene_analysis,
                source_snapshot=first_day_model_snapshot,
            )
            source_snapshot["first_day_workflow"]["final_contract_error"] = final_error
            if final_error:
                response = {
                    "should_create_plan": False,
                    "stall_reason": "first_day_plan_repair_failed",
                    "plan_arc": "",
                    "steps": [],
                    "final_contract_error": final_error,
                }
        return response

    async def _decide_standard_plan(self, prepared: dict[str, Any]) -> dict[str, Any]:
        source_snapshot = prepared["source_snapshot"]
        activity_quote_fact = prepared["activity_quote_fact"]
        reply_wait_minutes = prepared["reply_wait_minutes"]
        customer_silence_minutes = prepared["customer_silence_minutes"]
        model_messages = [
            {"role": "system", "content": OUTREACH_PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": dumps(source_snapshot)},
        ]
        response = await self.model_client.chat_json(
            model_messages,
            tier="strong",
            temperature=0.0,
        )
        response = _normalize_outreach_plan_response(response)
        structure_error = _outreach_plan_structure_error(response) or _outreach_plan_context_error(
            response,
            activity_quote_fact=activity_quote_fact,
            reply_wait_minutes=reply_wait_minutes,
            customer_silence_minutes=customer_silence_minutes,
        )
        if structure_error:
            response = await self.model_client.chat_json(
                [
                    *model_messages,
                    {"role": "assistant", "content": dumps(response)},
                    {
                        "role": "user",
                        "content": (
                            "上一个 json 不符合结构合同。"
                            f"错误：{structure_error}。"
                            "请保留事实和销售判断，重新输出完整有效 json；不要解释。"
                        ),
                    },
                ],
                tier="strong",
                temperature=0.0,
            )
            response = _normalize_outreach_plan_response(response)
        response = await self.model_client.chat_json(
            [
                {"role": "system", "content": OUTREACH_PLAN_REVIEW_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": dumps(
                        {
                            "source_snapshot": source_snapshot,
                            "candidate_plan": response,
                        }
                    ),
                },
            ],
            tier="strong",
            temperature=0.0,
        )
        response = _normalize_outreach_plan_response(response)
        structure_error = _outreach_plan_structure_error(response) or _outreach_plan_context_error(
            response,
            activity_quote_fact=activity_quote_fact,
            reply_wait_minutes=reply_wait_minutes,
            customer_silence_minutes=customer_silence_minutes,
        )
        for _repair_attempt in range(3):
            if not structure_error:
                break
            response = await self.model_client.chat_json(
                [
                    {"role": "system", "content": OUTREACH_PLAN_SCHEMA_REPAIR_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": dumps(
                            {
                                "source_snapshot": source_snapshot,
                                "candidate_plan": response,
                                "structure_error": structure_error,
                                "repair_instruction": (
                                    "严格按 structure_error 修复完整 json；保留现有业务语义和客户可见文字，"
                                    "不要重新判断是否创建计划，不要解释。"
                                ),
                            }
                        ),
                    },
                ],
                tier="strong",
                temperature=0.0,
            )
            response = _normalize_outreach_plan_response(response)
            structure_error = _outreach_plan_structure_error(response) or _outreach_plan_context_error(
                response,
                activity_quote_fact=activity_quote_fact,
                reply_wait_minutes=reply_wait_minutes,
                customer_silence_minutes=customer_silence_minutes,
            )
        return response

    async def _materialize_plan(
        self, prepared: dict[str, Any], response: dict[str, Any]
    ) -> dict[str, Any]:
        customer_id = prepared["customer_id"]
        corp_id = prepared["corp_id"]
        user_id = prepared["user_id"]
        wechat = prepared["wechat"]
        external_userid = prepared["external_userid"]
        sop_plan_id = prepared["sop_plan_id"]
        trigger_context = prepared["trigger_context"]
        workflow_run_id = prepared["workflow_run_id"]
        first_day_trigger = prepared["first_day_trigger"]
        reply_wait_minutes = prepared["reply_wait_minutes"]
        customer_silence_minutes = prepared["customer_silence_minutes"]
        activity_quote_fact = prepared["activity_quote_fact"]
        source_snapshot = prepared["source_snapshot"]
        if not bool(response.get("should_create_plan", True)):
            self.repository.add_outreach_event(
                plan_id="",
                task_id="",
                customer_id=customer_id,
                event_type="plan_rejected",
                event_summary=str(response.get("stall_reason") or "AI decided not to create outreach plan"),
                payload={
                    "identity": {
                        "customer_id": customer_id,
                        "corp_id": corp_id,
                        "wechat": wechat,
                        "external_userid": external_userid,
                    },
                    "trigger_context": trigger_context or {},
                    "ai_result": response,
                    "first_day_workflow": source_snapshot.get("first_day_workflow") or {},
                    "workflow_run_id": workflow_run_id,
                },
            )
            if workflow_run_id:
                workflow = source_snapshot.get("first_day_workflow") or {}
                scene_analysis = workflow.get("scene_analysis") if isinstance(workflow, dict) else {}
                current_run = self.repository.get_first_day_outreach_run(
                    workflow_run_id,
                    include_related=False,
                )
                recorded_workflow = dict(current_run.get("workflow") or {})
                recorded_workflow["summary"] = workflow
                self.repository.update_first_day_outreach_run(
                    workflow_run_id,
                    status="blocked",
                    reason_code=str(response.get("stall_reason") or "plan_rejected"),
                    final_decision="no_plan",
                    first_scene=_string((scene_analysis or {}).get("step1_scene")),
                    second_scene=_string((scene_analysis or {}).get("step2_scene")),
                    workflow_json=recorded_workflow,
                    final_plan_json=response,
                    finished_at=utc_now_iso(),
                )
            return {"created": False, "ai_result": response}
        structure_error = (
            _first_day_outreach_plan_error(response)
            if first_day_trigger
            else _outreach_plan_structure_error(response) or _outreach_plan_context_error(
                response,
                activity_quote_fact=activity_quote_fact,
                reply_wait_minutes=reply_wait_minutes,
                customer_silence_minutes=customer_silence_minutes,
                allow_first_day_internal_activity_quote=first_day_trigger,
            )
        )
        if structure_error:
            raise RuntimeError(f"outreach_plan_model_invalid_structure: {structure_error}")
        raw_steps, tasks = await self._materialize_tasks(prepared, response)
        source_snapshot["ai_result"] = response
        created_plan = self.repository.create_outreach_plan(
                customer_id=customer_id,
                corp_id=corp_id,
                user_id=user_id,
                wechat=wechat,
                external_userid=external_userid,
                customer_stage=str(response.get("conversion_stage") or response.get("customer_stage") or ""),
                stall_reason=str(response.get("stall_reason") or ""),
                customer_psychology=str(response.get("customer_psychology") or ""),
                plan_goal=str(response.get("plan_goal") or ""),
                source_snapshot=source_snapshot,
                tasks=tasks[:3],
                sop_plan_id=sop_plan_id,
                workflow_run_id=workflow_run_id,
            )
        if workflow_run_id:
            plan = created_plan.get("plan") if isinstance(created_plan.get("plan"), dict) else {}
            created_tasks = created_plan.get("tasks") if isinstance(created_plan.get("tasks"), list) else []
            current_run = self.repository.get_first_day_outreach_run(
                workflow_run_id,
                include_related=False,
            )
            recorded_workflow = dict(current_run.get("workflow") or {})
            recorded_workflow["summary"] = source_snapshot.get("first_day_workflow") or {}
            updates: dict[str, Any] = {
                "plan_id": _string(plan.get("id")),
                "first_task_id": _string((created_tasks[0] if created_tasks else {}).get("id")),
                "second_task_id": _string((created_tasks[1] if len(created_tasks) > 1 else {}).get("id")),
                "first_scene": _string((raw_steps[0] if raw_steps else {}).get("scene")),
                "second_scene": _string((raw_steps[1] if len(raw_steps) > 1 else {}).get("scene")),
                "workflow_json": recorded_workflow,
                "final_plan_json": response,
            }
            if _string(current_run.get("status")) not in {
                "blocked", "sent", "cancelled", "failed", "completed"
            }:
                updates.update(
                    status="created",
                    reason_code="plan_created",
                    final_decision="send_pending",
                )
            self.repository.update_first_day_outreach_run(workflow_run_id, **updates)
        return {"created": True, **created_plan}

    async def _materialize_tasks(
        self, prepared: dict[str, Any], response: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        first_day_trigger = prepared["first_day_trigger"]
        asset_catalog = prepared["asset_catalog"]
        recent_media = prepared["recent_media"]
        activity_quote_fact = prepared["activity_quote_fact"]
        payment_collection_gate = prepared["payment_collection_gate"]
        source_snapshot = prepared["source_snapshot"]
        raw_steps = [step for step in response.get("steps") or [] if isinstance(step, dict)][:2 if first_day_trigger else 3]

        primary_resolved_assets = await asyncio.gather(
            *[
                self._resolve_outreach_asset(
                    step,
                    asset_catalog=asset_catalog,
                    recent_media=recent_media,
                )
                for step in raw_steps
            ]
        )

        now = utc_now_iso()
        tasks = []
        payment_collection_added = False
        used_asset_keys: set[str] = set()
        used_media_urls: set[str] = {
            identity
            for identity in (
                _media_url_identity(url) for url in recent_media.get("urls") or []
            )
            if identity
        }
        normalized_schedule = (
            _normalize_first_day_outreach_schedule(now, raw_steps)
            if first_day_trigger
            else _normalize_outreach_schedule(now, raw_steps)
        )
        for index, step in enumerate(raw_steps, start=1):
            schedule = normalized_schedule[index - 1]
            content_mode = _string(step.get("content_mode"))
            payment_collection_basis = _string(step.get("payment_collection_basis"))
            activity_quote_ready = _valid_activity_quote_evidence(
                activity_quote_fact
            ) or (
                first_day_trigger
                and _first_day_internal_activity_quote_evidence(
                    raw_steps,
                    before_step_index=index - 1,
                )
            )
            should_send_payment_collection = (
                False
                if first_day_trigger
                else (
                    _bool(step.get("should_send_payment_collection"))
                    and index == len(raw_steps)
                    and content_mode == "transaction"
                    and payment_collection_basis == "model_selected_after_quote"
                    and not payment_collection_added
                    and activity_quote_ready
                    and bool(payment_collection_gate.get("eligible"))
                )
            )
            payment_collection_added = payment_collection_added or should_send_payment_collection
            sop_pack = (
                _first_day_sop_pack_for_step(
                    source_snapshot,
                    step_index=index,
                    scene=_string(step.get("scene")),
                )
                if first_day_trigger
                else {}
            )
            sop_pack_messages = [
                dict(message)
                for message in sop_pack.get("reply_messages") or []
                if isinstance(message, dict)
            ]
            sop_pack_texts = _first_day_sop_pack_texts(sop_pack_messages)
            writer_texts = _plan_step_texts(step)
            use_writer_text = bool(first_day_trigger and writer_texts)
            sop_pack_policy_error = ""
            if first_day_trigger and sop_pack_texts and not use_writer_text:
                sop_pack_policy_error, _ = _first_day_message_policy_error(
                    sop_pack_texts,
                    step_index=index,
                    plan={"source_snapshot": source_snapshot},
                    context={},
                )
            preserve_sop_pack_messages = bool(sop_pack_messages and not sop_pack_policy_error)
            draft_texts = (
                writer_texts
                if use_writer_text
                else sop_pack_texts
                if preserve_sop_pack_messages
                else writer_texts
            )
            if not draft_texts:
                continue
            resolved_assets_for_step: list[dict[str, Any]] = []
            if first_day_trigger and preserve_sop_pack_messages:
                reply_messages = _first_day_materialized_sop_messages(
                    sop_pack_messages,
                    allow_payment_collection=should_send_payment_collection,
                    text_overrides=writer_texts if use_writer_text else None,
                    sent_urls=set(recent_media.get("urls") or []),
                    used_urls=used_media_urls,
                )
                delivered_urls = {
                    _string((message.get("content") or {}).get("url"))
                    for message in reply_messages
                    if isinstance(message, dict) and isinstance(message.get("content"), dict)
                    and _string((message.get("content") or {}).get("url"))
                }
                resolved_assets_for_step = [
                    dict(asset)
                    for asset in asset_catalog
                    if isinstance(asset, dict) and _string(asset.get("url")) in delivered_urls
                ]
                for asset in resolved_assets_for_step:
                    asset_key = _string(
                        asset.get("document_id") or asset.get("url") or asset.get("asset_id")
                    )
                    if asset_key:
                        used_asset_keys.add(asset_key)
            else:
                candidate_assets = (
                    _first_day_configured_assets_for_step(
                        source_snapshot,
                        step_index=index,
                        asset_catalog=asset_catalog,
                        recent_media=recent_media,
                    )
                    if first_day_trigger
                    else []
                )
                primary_asset = primary_resolved_assets[index - 1]
                if primary_asset:
                    candidate_assets.append(primary_asset)
                for asset in candidate_assets:
                    asset_key = _string(
                        asset.get("document_id") or asset.get("url") or asset.get("asset_id")
                    )
                    asset_url = _string(asset.get("url"))
                    asset_url_identity = _media_url_identity(asset_url)
                    if (asset_key and asset_key in used_asset_keys) or (
                        asset_url_identity and asset_url_identity in used_media_urls
                    ):
                        continue
                    if asset_key:
                        used_asset_keys.add(asset_key)
                    if asset_url_identity:
                        used_media_urls.add(asset_url_identity)
                    resolved_assets_for_step.append(dict(asset))
                reply_messages = _compose_outreach_messages(
                    draft_texts,
                    resolved_assets=resolved_assets_for_step,
                    should_send_payment_collection=should_send_payment_collection,
                    text_limit=None if sop_pack else 2,
                )
            resolved_asset = resolved_assets_for_step[0] if resolved_assets_for_step else {}
            selected_source_ids = _list_strings(
                (((source_snapshot.get("first_day_workflow") or {}).get("scene_analysis") or {}).get(
                    "selected_source_ids"
                ) or {}).get(f"step{index}")
            )
            main_source_id = _string(sop_pack.get("source_id")) or next(
                (
                    source_id
                    for source_id in selected_source_ids
                    if source_id.startswith("appointment-blocker:") and source_id.count(":") == 1
                ),
                "",
            )
            task_metadata = {
                "scene": _string(step.get("scene")),
                "content_mode": content_mode,
                "persuasion_angle": _string(step.get("persuasion_angle")),
                "new_value": _string(step.get("new_value")),
                "avoid_repeating": _list_strings(step.get("avoid_repeating")),
                "timing_reason": _string(step.get("timing_reason")),
                "urgency_level": _string(step.get("urgency_level")),
                "no_reply_action": _string(step.get("no_reply_action")),
                "no_reply_strategy": _string(step.get("no_reply_strategy")),
                "requested_delay_minutes": schedule["requested_delay_minutes"],
                "normalized_delay_minutes": schedule["normalized_delay_minutes"],
                "asset_strategy": _string(step.get("asset_strategy")) or "none",
                "asset_id": _string(step.get("asset_id")),
                "case_query": _string(step.get("case_query")),
                "cta": _string(step.get("cta")),
                "plan_arc": _string(response.get("plan_arc")),
                "source_kind": (
                    "mainline_sop"
                    if sop_pack
                    else "appointment_blocker"
                    if main_source_id.startswith("appointment-blocker:")
                    else ""
                ),
                "source_id": main_source_id,
                "sop_pack_id": _string(sop_pack.get("pack_id")),
                "sop_category": _string(sop_pack.get("sop_category")),
                "preserve_sop_pack_messages": preserve_sop_pack_messages,
                "sop_pack_rewrite_reason": sop_pack_policy_error,
                "sop_pack_reply_messages": sop_pack_messages,
                "resolved_assets": resolved_assets_for_step,
            }
            tasks.append(
                {
                    "step_index": int(step.get("step") or index),
                    "scheduled_at": schedule["scheduled_at"],
                    "intent": str(step.get("intent") or "outreach"),
                    "message_goal": str(step.get("message_goal") or ""),
                    "content_sources": _task_content_sources(
                        step.get("content_sources"),
                        should_send_payment_collection=should_send_payment_collection,
                        task_metadata=task_metadata,
                        resolved_asset=resolved_asset,
                        resolved_assets=resolved_assets_for_step,
                    ),
                    "should_send_payment_collection": should_send_payment_collection,
                    "before_send_check": bool(step.get("before_send_check", True)),
                    "reply_messages": reply_messages,
                }
            )
        if not tasks:
            raise RuntimeError("outreach_plan_model_missing_reviewable_drafts")
        return raw_steps, tasks

    async def _run_first_day_model_node(
        self,
        *,
        node: str,
        prompt: str,
        prompt_version: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        started = time.perf_counter()
        attempts: list[dict[str, Any]] = []
        response: dict[str, Any] = {}
        for attempt in range(1, 4):
            attempt_started = time.perf_counter()
            try:
                response = await self.model_client.chat_json(
                    [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": dumps(payload)},
                    ],
                    tier="strong",
                    temperature=0.0,
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "elapsed_ms": round((time.perf_counter() - attempt_started) * 1000, 3),
                        "status": "completed",
                    }
                )
                break
            except Exception as exc:
                normalized_error = f"{type(exc).__name__}: {exc}"
                is_timeout = "timeout" in normalized_error.lower()
                attempts.append(
                    {
                        "attempt": attempt,
                        "elapsed_ms": round((time.perf_counter() - attempt_started) * 1000, 3),
                        "status": "timeout" if is_timeout else "failed",
                        "error": normalized_error[:800],
                    }
                )
                if attempt >= 3 or not is_timeout:
                    workflow_run_id = self._first_day_run_id_from_value(payload)
                    if workflow_run_id:
                        current = self.repository.get_first_day_outreach_run(
                            workflow_run_id,
                            include_related=False,
                        )
                        workflow = dict(current.get("workflow") or {})
                        workflow[node] = {"input": payload, "attempts": attempts}
                        self.repository.update_first_day_outreach_run(
                            workflow_run_id,
                            status="failed",
                            reason_code="model_node_failed",
                            final_decision="failed",
                            model_attempt_count=int(current.get("model_attempt_count") or 0) + len(attempts),
                            workflow_json=workflow,
                            error_node=node,
                            error_type=type(exc).__name__,
                            error_message=str(exc)[:4000],
                            duration_ms=round((time.perf_counter() - started) * 1000),
                            finished_at=utc_now_iso(),
                        )
                    raise
        trace = {
            "node": node,
            "prompt_version": prompt_version,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "attempt_count": len(attempts),
            "attempts": attempts,
            "model_usage": dict(getattr(self.model_client, "last_usage", None) or {}),
        }
        workflow_run_id = self._first_day_run_id_from_value(payload)
        if workflow_run_id:
            current = self.repository.get_first_day_outreach_run(
                workflow_run_id,
                include_related=False,
            )
            workflow = dict(current.get("workflow") or {})
            workflow[node] = {"input": payload, "output": response, "trace": trace}
            self.repository.update_first_day_outreach_run(
                workflow_run_id,
                workflow_json=workflow,
                model_attempt_count=int(current.get("model_attempt_count") or 0) + len(attempts),
                retry_count=int(current.get("retry_count") or 0) + max(0, len(attempts) - 1),
            )
        return response if isinstance(response, dict) else {}, trace

    @classmethod
    def _first_day_run_id_from_value(cls, value: Any) -> str:
        if isinstance(value, dict):
            direct = _string(value.get("workflow_run_id"))
            if direct:
                return direct
            for item in value.values():
                nested = cls._first_day_run_id_from_value(item)
                if nested:
                    return nested
        elif isinstance(value, list):
            for item in value:
                nested = cls._first_day_run_id_from_value(item)
                if nested:
                    return nested
        return ""

    def list_candidates(
        self,
        *,
        limit: int = 50,
        silent_minutes_min: int = 60,
        outreach_status: str = "",
        lifecycle_stage: str = "",
        no_plan_only: bool = False,
        keyword: str = "",
    ) -> list[dict[str, Any]]:
        candidates = self.repository.list_outreach_candidates(
            limit=limit,
            silent_minutes_min=silent_minutes_min,
            outreach_status=outreach_status,
            lifecycle_stage=lifecycle_stage,
            no_plan_only=no_plan_only,
            keyword=keyword,
        )
        if not keyword:
            return candidates
        return [item for item in candidates if self._candidate_matches_keyword(item, keyword)]

    async def refresh_customer_conversation(
        self,
        *,
        customer_id: str,
        corp_id: str,
        user_id: str,
        wechat: str,
        external_userid: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        identity = {
            "corp_id": corp_id,
            "customer_id": customer_id,
            "external_userid": external_userid,
            "user_id": user_id,
            "wechat": wechat,
        }
        missing = _missing_outreach_identity_fields(identity)
        if missing:
            raise ValueError(f"invalid_outreach_identity: missing {','.join(missing)}")
        payload = await self.system_client.conversation(
            corp_id=corp_id,
            customer_id=customer_id,
            external_userid=external_userid,
            user_id=user_id,
            wechat=wechat,
            limit=limit,
        )
        messages = self._conversation_messages(payload)
        customer_relation = normalize_customer_relation(payload)
        first_added_at = _authoritative_first_added_at(payload)
        conversation_id = _conversation_id_from_response(payload)
        scope = build_customer_scope(
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            customer_id=customer_id,
        )
        latest_customer = self._latest_message_time(messages, sender="customer")
        latest_staff = self._latest_message_time(messages, sender="staff")
        if latest_customer and scope.persistence_allowed:
            self.repository.touch_customer_message_time(
                scope.sales_contact_key,
                field="last_customer_message_at",
                value=latest_customer,
            )
        if latest_staff and scope.persistence_allowed:
            self.repository.touch_customer_message_time(
                scope.sales_contact_key,
                field="last_staff_message_at",
                value=latest_staff,
            )
        self.repository.add_outreach_event(
            plan_id="",
            task_id="",
            customer_id=customer_id,
            event_type="conversation_refreshed",
            event_summary="Refreshed customer conversation from system API",
            payload={
                "latest_customer_message_at": latest_customer,
                "message_count": len(messages),
                "customer_relation": customer_relation,
                "first_added_at": first_added_at,
                "conversation_id": conversation_id,
            },
        )
        return {
            "raw": payload,
            "messages": messages,
            "latest_customer_message_at": latest_customer,
            "latest_staff_message_at": latest_staff,
            "customer_relation": customer_relation,
            "first_added_at": first_added_at,
            "conversation_id": conversation_id,
        }

    async def generate_configured_strategy_shadow_plan(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str,
        user_id: str = "",
        query: str,
        memory: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Let the model select configured follow-up steps, persisted as no-send shadow tasks."""

        if self.sales_strategy_service is None:
            return {"created": False, "error": "sales_strategy_service_unavailable"}
        catalog = self.sales_strategy_service.runtime_summary()
        if str(catalog.get("runtime_mode") or "off") == "off":
            return {"created": False, "error": "sales_strategy_catalog_disabled"}
        scope = build_customer_scope(
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            customer_id=customer_id,
        )
        if not scope.persistence_allowed:
            return {"created": False, "error": "invalid_customer_scope"}
        candidates = self.sales_strategy_service.retrieve_strategy_pool(
            query=query,
            limit=8,
        )
        if not candidates:
            return {"created": False, "error": "no_strategy_candidates"}
        response = await self.model_client.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "你只从候选中选择一条跟进策略及同一策略内1至3个步骤，不写客户话术。"
                        "不得虚构key、步骤、延迟或事实。只返回JSON："
                        '{"selected_strategy_key":"","selected_step_keys":[],"reason":""}'
                    ),
                },
                {
                    "role": "user",
                    "content": dumps({"query": query, "memory": memory or {}, "strategy_candidates": candidates}),
                },
            ],
            tier="strong",
            temperature=0.0,
        )
        selected = _selected_strategy(response, candidates)
        selected_steps = _selected_strategy_steps(response, selected)
        if selected is None:
            return {"created": False, "error": "invalid_strategy_selection", "ai_result": response}
        if not selected_steps:
            return {"created": False, "error": "invalid_strategy_step_selection", "ai_result": response}
        now = utc_now_iso()
        memory_value = memory or {}
        appointment_at = _string(memory_value.get("appointment_time") or memory_value.get("visit_time"))
        last_customer_at = _string(memory_value.get("last_customer_message_at")) or now
        contact_added_at = _string(memory_value.get("customer_added_at") or memory_value.get("added_at")) or now
        previous_scheduled_at = now
        tasks: list[dict[str, Any]] = []
        for index, step in enumerate(selected_steps, start=1):
            trigger_base = _string(step.get("trigger_base"))
            schedule_base = (
                last_customer_at
                if trigger_base == "customer_reply"
                else contact_added_at
                if trigger_base == "contact_added"
                else previous_scheduled_at
                if trigger_base == "previous_step"
                else now
            )
            scheduled_at = _scheduled_at_for_strategy_step(
                schedule_base,
                step,
                appointment_at=appointment_at,
            )
            if not scheduled_at:
                continue
            tasks.append(
                {
                    "step_index": index,
                    "scheduled_at": scheduled_at,
                    "intent": _string(step.get("step_key")),
                    "message_goal": _string(step.get("node_goal")),
                    "content_sources": _list_strings(step.get("tactic_tags")),
                    "reply_messages": [],
                    "before_send_check": True,
                    "should_send_payment_collection": False,
                }
            )
            previous_scheduled_at = scheduled_at
        if not tasks:
            return {
                "created": False,
                "error": "strategy_steps_missing_required_schedule_fact",
                "ai_result": response,
            }
        source_snapshot = {
            "plan_type": "followup_strategy",
            "runtime_mode": "shadow",
            "sales_contact_key": scope.sales_contact_key,
            "query": query,
            "memory": memory_value,
            "sales_strategy_catalog": catalog,
            "strategy_candidates": candidates,
            "selected_strategy": selected,
            "selected_step_keys": [step.get("step_key") for step in selected_steps],
            "ai_result": response,
        }
        created = self.repository.create_outreach_plan(
            customer_id=customer_id,
            corp_id=corp_id,
            user_id=user_id,
            wechat=wechat,
            external_userid=external_userid,
            customer_stage="",
            stall_reason=query[:500],
            customer_psychology="",
            plan_goal=_string(selected.get("name")),
            source_snapshot=source_snapshot,
            tasks=tasks,
            sop_plan_id=f"followup_strategy:{scope.sales_contact_key}:{selected.get('strategy_key')}:{hashlib.sha256(query.encode('utf-8')).hexdigest()[:16]}",
        )
        return {"created": True, **created}

    def _relation_plan_skip(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str,
        reason: str,
        relation: dict[str, Any],
        trigger_context: dict[str, Any],
    ) -> dict[str, Any]:
        active_loader = getattr(self.repository, "get_active_outreach_plan_for_customer", None)
        active = (
            active_loader(
                customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
            )
            if callable(active_loader)
            else {}
        )
        plan = active.get("plan") if isinstance(active.get("plan"), dict) else {}
        plan_id = _string(plan.get("id"))
        if plan_id and reason == "customer_deleted":
            skip_remaining = getattr(self.repository, "skip_remaining_outreach_tasks", None)
            if callable(skip_remaining):
                skip_remaining(plan_id, reason="customer_deleted")
            update_plan_status = getattr(self.repository, "update_outreach_plan_status", None)
            if callable(update_plan_status):
                update_plan_status(plan_id, "cancelled")
        event_type = (
            "plan_skipped_customer_deleted"
            if reason == "customer_deleted"
            else "plan_skipped_customer_relation_unavailable"
        )
        add_event = getattr(self.repository, "add_outreach_event", None)
        if callable(add_event):
            add_event(
                plan_id=plan_id,
                task_id="",
                customer_id=customer_id,
                event_type=event_type,
                event_summary=(
                    "Customer relation is deleted; personalized plan generation skipped"
                    if reason == "customer_deleted"
                    else "Customer relation could not be verified; personalized plan generation skipped"
                ),
                payload={
                    "identity": {
                        "customer_id": customer_id,
                        "corp_id": corp_id,
                        "wechat": wechat,
                        "external_userid": external_userid,
                    },
                    "reason": reason,
                    "customer_relation": relation,
                    "trigger_context": trigger_context,
                },
            )
        return {
            "created": False,
            "skipped": True,
            "reason": reason,
            "customer_relation": relation,
        }

    def _outreach_asset_catalog(self) -> list[dict[str, Any]]:
        return build_appointment_blocker_asset_catalog(self._appointment_blocker_playbook())

    def _first_day_sop_sequence(self, *, required: bool = False) -> list[dict[str, Any]]:
        if self.sop_reply_pack_service is None:
            return []
        try:
            config = self.sop_reply_pack_service.load()
        except Exception as exc:
            if required:
                raise RuntimeError(
                    f"first_day_sop_context_load_failed: {type(exc).__name__}: {exc}"
                ) from exc
            return []
        packs = config.get("packs") if isinstance(config.get("packs"), list) else []
        output: list[dict[str, Any]] = []
        for pack in packs:
            if not isinstance(pack, dict) or not _bool(pack.get("enabled")):
                continue
            pack_id = _string(pack.get("id"))
            raw_scopes = pack.get("scopes")
            scopes = [
                _string(item)
                for item in raw_scopes
                if _string(item)
            ] if isinstance(raw_scopes, list) else []
            scope = _string(pack.get("scope"))
            if "chat_gate" not in set(scopes + ([scope] if scope else [])):
                continue
            if pack_id == "s10_new_customer_opening":
                continue
            day_stage = _string(pack.get("day_stage"))
            if day_stage and not day_stage.startswith("day1"):
                continue
            messages = [
                dict(message)
                for message in pack.get("reply_messages") or []
                if isinstance(message, dict) and _string(message.get("type"))
            ]
            if not messages:
                continue
            category = _string(pack.get("sop_category"))
            mapped_scene = FIRST_DAY_SOP_SCENE_BY_CATEGORY.get(category, "")
            if not mapped_scene:
                continue
            media_asset_ids: list[str] = []
            compact_messages: list[dict[str, Any]] = []
            for order, message in enumerate(messages, start=1):
                message_type = _string(message.get("type"))
                content = message.get("content") if isinstance(message.get("content"), dict) else {}
                if message_type == "text":
                    compact_messages.append(
                        {
                            "type": "text",
                            "order": _int(message.get("order"), order),
                            "text": _string(content.get("text") if isinstance(content, dict) else ""),
                        }
                    )
                elif message_type in {"image", "video"}:
                    asset_id = f"sop-pack:{pack_id}:{_int(message.get('order'), order)}"
                    media_asset_ids.append(asset_id)
                    url = _string(content.get("url") if isinstance(content, dict) else "")
                    compact_messages.append(
                        {
                            "type": message_type,
                            "order": _int(message.get("order"), order),
                            "asset_id": asset_id,
                            "url": url,
                        }
                    )
                elif message_type == "payment_collection":
                    compact_messages.append(
                        {
                            "type": "payment_collection",
                            "order": _int(message.get("order"), order),
                            "amount": _int(content.get("amount") if isinstance(content, dict) else 10, 10),
                        }
                    )
            output.append(
                {
                    "source_id": f"sop-pack:{pack_id}",
                    "pack_id": pack_id,
                    "name": _string(pack.get("name")),
                    "sop_category": category,
                    "mapped_scene": mapped_scene,
                    "order": _int(
                        pack.get("order"),
                        FIRST_DAY_SOP_CATEGORY_ORDER.get(category, 999),
                    ),
                    "day_stage": day_stage,
                    "purpose": _string(pack.get("purpose")),
                    "reply_messages": compact_messages,
                    "asset_ids": media_asset_ids,
                }
            )
        output.sort(
            key=lambda item: (
                _int(item.get("order"), 9999),
                FIRST_DAY_SOP_CATEGORY_ORDER.get(_string(item.get("sop_category")), 999),
                _string(item.get("pack_id")),
            )
        )
        if required:
            available_scenes = {
                _string(item.get("mapped_scene"))
                for item in output
                if isinstance(item, dict) and _string(item.get("mapped_scene"))
            }
            missing_scenes = sorted({"effect_proof", "activity_intro"} - available_scenes)
            if missing_scenes:
                raise RuntimeError(
                    "first_day_sop_context_incomplete: missing_scenes="
                    + ",".join(missing_scenes)
                )
        return output

    @staticmethod
    def _first_day_sop_asset_catalog(sop_sequence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        for pack in sop_sequence:
            if not isinstance(pack, dict):
                continue
            pack_id = _string(pack.get("pack_id"))
            for message in pack.get("reply_messages") or []:
                if not isinstance(message, dict):
                    continue
                asset_id = _string(message.get("asset_id"))
                message_type = _string(message.get("type"))
                if not asset_id or message_type not in {"image", "video"}:
                    continue
                url = _string(message.get("url"))
                if not url:
                    continue
                assets.append(
                    {
                        "asset_id": asset_id,
                        "type": message_type,
                        "url": url,
                        "source": "first_day_sop_pack",
                        "name": pack_id,
                        "annotation": _string(pack.get("name")),
                        "use_cases": [_string(pack.get("purpose"))],
                        "avoid_when": ["近期已经发送相同 SOP 或相同素材"],
                        "tags": [_string(pack.get("sop_category")), pack_id],
                    }
                )
        return assets

    def _appointment_blocker_playbook(self) -> dict[str, Any]:
        if self.precision_qa_playbook_service is None:
            return {"version": 4, "items": []}
        try:
            return self.precision_qa_playbook_service.load()
        except Exception:
            return {"version": 4, "items": []}

    async def _resolve_outreach_asset(
        self,
        step: dict[str, Any],
        *,
        asset_catalog: list[dict[str, Any]],
        recent_media: dict[str, list[str]],
    ) -> dict[str, Any]:
        strategy = _string(step.get("asset_strategy")) or "none"
        if strategy not in OUTREACH_ASSET_STRATEGIES:
            return {}
        sent_urls = set(recent_media.get("urls") or [])
        sent_document_ids = set(recent_media.get("document_ids") or [])
        if strategy == "configured_image":
            return resolve_configured_asset(
                asset_catalog,
                _string(step.get("asset_id")),
                sent_urls=sent_urls,
                expected_type="image",
            )
        if strategy == "operation_video":
            return resolve_configured_asset(
                asset_catalog,
                _string(step.get("asset_id")),
                sent_urls=sent_urls,
                expected_type="video",
            )
        if strategy != "case_search":
            return {}

        query = _string(step.get("case_query"))
        if self.coze_client is not None and query:
            try:
                result = await asyncio.wait_for(
                    self.coze_client.search_kb("case_studies", query),
                    timeout=12.0,
                )
                case_asset = resolve_case_asset(
                    result,
                    sent_urls=sent_urls,
                    sent_document_ids=sent_document_ids,
                )
                if case_asset:
                    return case_asset
            except (TimeoutError, ValueError, RuntimeError):
                pass
        return resolve_configured_asset(
            asset_catalog,
            _string(step.get("fallback_asset_id")),
            sent_urls=sent_urls,
            expected_type="image",
        )

    async def ensure_platform_task_plan(
        self,
        *,
        identity: dict[str, Any],
        conversation_messages: list[dict[str, Any]],
        conversation_activity: dict[str, Any],
        customer_context: dict[str, Any],
        platform_task: dict[str, Any],
        customer_relation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one auto-approved day-2 personalized plan and reuse it on later platform triggers."""
        lock = self._plan_lock(identity)
        async with lock:
            return await self._ensure_platform_task_plan_locked(
                identity=identity,
                conversation_messages=conversation_messages,
                conversation_activity=conversation_activity,
                customer_context=customer_context,
                platform_task=platform_task,
                customer_relation=customer_relation or {},
            )

    async def _ensure_platform_task_plan_locked(
        self,
        *,
        identity: dict[str, Any],
        conversation_messages: list[dict[str, Any]],
        conversation_activity: dict[str, Any],
        customer_context: dict[str, Any],
        platform_task: dict[str, Any],
        customer_relation: dict[str, Any],
    ) -> dict[str, Any]:
        customer_id = _string(identity.get("customer_id"))
        corp_id = _string(identity.get("corp_id"))
        wechat = _string(identity.get("wechat"))
        external_userid = _string(identity.get("external_userid"))
        if not customer_relation.get("available"):
            try:
                refreshed = await self.refresh_customer_conversation(
                    customer_id=customer_id,
                    corp_id=corp_id,
                    user_id=_string(identity.get("user_id")),
                    wechat=wechat,
                    external_userid=external_userid,
                    limit=50,
                )
                customer_relation = (
                    refreshed.get("customer_relation")
                    if isinstance(refreshed.get("customer_relation"), dict)
                    else {}
                )
            except Exception as exc:
                return self._relation_plan_skip(
                    customer_id=customer_id,
                    corp_id=corp_id,
                    wechat=wechat,
                    external_userid=external_userid,
                    reason="customer_relation_check_failed",
                    relation={
                        "available": False,
                        "status": "unknown",
                        "is_deleted": False,
                        "deleted_at": "",
                        "updated_at": "",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    trigger_context={
                        "source": "sop_platform_task",
                        "platform_task": platform_task,
                    },
                )
        if not customer_relation.get("available"):
            return self._relation_plan_skip(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                reason="customer_relation_unavailable",
                relation=customer_relation,
                trigger_context={
                    "source": "sop_platform_task",
                    "platform_task": platform_task,
                },
            )
        if customer_relation_is_deleted(customer_relation):
            return self._relation_plan_skip(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                reason="customer_deleted",
                relation=customer_relation,
                trigger_context={
                    "source": "sop_platform_task",
                    "platform_task": platform_task,
                },
            )
        conversation_fingerprint = _conversation_fingerprint(
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            customer_id=customer_id,
            latest_customer_message_at=_string(conversation_activity.get("latest_customer_message_at")),
            latest_staff_message_at=_string(conversation_activity.get("latest_staff_message_at")),
        )
        active = self.repository.get_active_outreach_plan_for_customer(
            customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
        )
        if active:
            plan = active.get("plan") if isinstance(active.get("plan"), dict) else {}
            latest_customer_at = _parse_iso(_string(conversation_activity.get("latest_customer_message_at")))
            plan_created_at = _parse_iso(_string(plan.get("created_at")))
            if latest_customer_at and plan_created_at and latest_customer_at > plan_created_at:
                self.repository.update_outreach_plan_status(_string(plan.get("id")), "cancelled")
                self.repository.add_outreach_event(
                    plan_id=_string(plan.get("id")),
                    task_id="",
                    customer_id=customer_id,
                    event_type="platform_task_plan_superseded_by_customer_reply",
                    event_summary="Customer replied after plan creation; regenerate from latest conversation",
                    payload={
                        "latest_customer_message_at": latest_customer_at.isoformat(),
                        "plan_created_at": plan_created_at.isoformat(),
                        "platform_task": platform_task,
                    },
                )
            else:
                trigger_context = (
                    plan.get("source_snapshot", {}).get("trigger_context")
                    if isinstance(plan.get("source_snapshot"), dict)
                    and isinstance(plan.get("source_snapshot", {}).get("trigger_context"), dict)
                    else {}
                )
                legacy_review_draft = (
                    _string(plan.get("status")) == "draft"
                    and _string(trigger_context.get("source")) == "sop_platform_task"
                    and _string(trigger_context.get("activation_policy")) != "auto_approved"
                )
                if legacy_review_draft:
                    self.repository.update_outreach_plan_status(_string(plan.get("id")), "cancelled")
                    self.repository.add_outreach_event(
                        plan_id=_string(plan.get("id")),
                        task_id="",
                        customer_id=customer_id,
                        event_type="legacy_review_plan_cancelled",
                        event_summary="Cancelled legacy review-required plan before creating auto-approved plan",
                        payload={"platform_task": platform_task},
                    )
                else:
                    if (
                        _string(plan.get("status")) == "draft"
                        and _string(trigger_context.get("activation_policy")) == "auto_approved"
                    ):
                        active = self._auto_approve_plan(_string(plan.get("id")))
                    self.repository.add_outreach_event(
                        plan_id=_string(plan.get("id")),
                        task_id="",
                        customer_id=customer_id,
                        event_type="platform_task_filtered_plan_reused",
                        event_summary="Filtered platform task and reused personalized outreach plan",
                        payload={"platform_task": platform_task, "conversation_activity": conversation_activity},
                    )
                    return {"created": False, "reused": True, **active}

        if self._completed_cycle_blocks_auto_plan(
            customer_id=customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            latest_customer_message_at=_string(conversation_activity.get("latest_customer_message_at")),
        ):
            return {
                "created": False,
                "reused": False,
                "skipped": True,
                "reason": "outreach_cycle_completed_without_new_customer_reply",
            }

        if self.repository.has_outreach_evaluation_fingerprint(
            customer_id=customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            conversation_fingerprint=conversation_fingerprint,
        ):
            return {
                "created": False,
                "reused": False,
                "skipped": True,
                "reason": "conversation_fingerprint_already_evaluated",
            }

        local_context = self.repository.recent_customer_context(
            customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
        )
        source_context = {
            "memory": local_context.get("memory") or {},
            "recent_messages": conversation_messages[-50:],
            "conversation_activity": conversation_activity,
            "customer_context": customer_context,
            "customer_relation": customer_relation,
        }
        result = await self.generate_plan(
            customer_id=customer_id,
            corp_id=corp_id,
            user_id=_string(identity.get("user_id")),
            wechat=wechat,
            external_userid=external_userid,
            current_stage="day2_personalized_spoken_unbooked",
            business_goal="从第二天起用不同心理角度递进唤醒客户，促使客户重新开口并推进到店或预约金",
            source_context=source_context,
            trigger_context={
                "source": "sop_platform_task",
                "platform_task_filtered": True,
                "platform_task": platform_task,
                "activation_policy": "auto_approved",
                "conversation_fingerprint": conversation_fingerprint,
            },
        )
        if not result.get("created"):
            return {"reused": False, **result}
        plan_id = _string((result.get("plan") or {}).get("id") or result.get("id"))
        if not plan_id:
            raise RuntimeError("personalized_outreach_plan_missing_id")
        activated = self._auto_approve_plan(plan_id)
        return {"reused": False, "auto_approved": True, **result, **activated}

    def record_closing_sequence_shadow(self, state: dict[str, Any]) -> dict[str, Any]:
        """Persist delayed closing nodes for audit only; never authorize a send."""

        if bool(state.get("test_isolated")) or not bool(state.get("memory_persist_allowed")):
            return {"created": False, "reason": "persistence_disabled"}
        scope = build_customer_scope(
            corp_id=state.get("corp_id"),
            wechat=state.get("wechat"),
            external_userid=state.get("external_userid"),
            customer_id=state.get("customer_id"),
        )
        if not scope.persistence_allowed:
            return {"created": False, "reason": "invalid_customer_scope"}
        policy = state.get("ai_sales_policy") if isinstance(state.get("ai_sales_policy"), dict) else {}
        closing = policy.get("closing") if isinstance(policy.get("closing"), dict) else {}
        decision = state.get("closing_decision") if isinstance(state.get("closing_decision"), dict) else {}
        identity = {
            "corp_id": _string(state.get("corp_id")),
            "wechat": _string(state.get("wechat")),
            "external_userid": _string(state.get("external_userid")),
            "customer_id": _string(state.get("customer_id")),
        }
        schedulable = (
            str(policy.get("runtime_mode") or "off") != "off"
            and str(closing.get("silent_tasks_mode") or "off") == "shadow"
            and decision.get("action") in {"enter", "advance", "fallback"}
            and decision.get("customer_state") not in {"hard_stop", "new_blocker"}
        )
        if not schedulable:
            cancelled = self.repository.cancel_open_closing_sequence_plans(
                **identity,
                reason="customer_reply_requires_fresh_planner_decision",
            )
            return {"created": False, "cancelled": cancelled, "reason": "closing_not_schedulable"}
        sequence_key = _string(decision.get("sequence_key"))
        sequence = next(
            (
                item
                for item in closing.get("sequences") or []
                if isinstance(item, dict)
                and item.get("enabled")
                and _string(item.get("sequence_key")) == sequence_key
            ),
            None,
        )
        if not sequence:
            cancelled = self.repository.cancel_open_closing_sequence_plans(
                **identity,
                reason="customer_reply_requires_fresh_planner_decision",
            )
            return {"created": False, "cancelled": cancelled, "reason": "sequence_not_found"}
        nodes = [item for item in sequence.get("nodes") or [] if isinstance(item, dict)]
        current_node_key = _string(decision.get("node_key"))
        current_index = next(
            (index for index, item in enumerate(nodes) if _string(item.get("node_key")) == current_node_key),
            -1,
        )
        delayed_nodes = [
            item
            for index, item in enumerate(nodes)
            if index > current_index
            and item.get("timing") == "silent_after"
            and _int(item.get("delay_minutes"), 0) > 0
        ]
        if not delayed_nodes:
            cancelled = self.repository.cancel_open_closing_sequence_plans(
                **identity,
                reason="customer_reply_requires_fresh_planner_decision",
            )
            return {"created": False, "cancelled": cancelled, "reason": "no_delayed_nodes"}
        request_id = _string(state.get("request_id"))
        sop_plan_id = f"closing_sequence:{scope.sales_contact_key}:{sequence_key}:{request_id}"
        existing = self.repository.find_open_outreach_plan_by_sop_plan_id(
            sop_plan_id,
            **identity,
        )
        if existing:
            return {"created": False, "cancelled": 0, "reason": "idempotent_existing", "plan": existing}
        cancelled = self.repository.cancel_open_closing_sequence_plans(
            **identity,
            reason="customer_reply_requires_fresh_planner_decision",
        )
        now = utc_now_iso()
        tasks = [
            {
                "step_index": index,
                "scheduled_at": _add_minutes(now, _int(node.get("delay_minutes"), 0)),
                "intent": _string(node.get("node_key")),
                "message_goal": _string(node.get("goal")),
                "content_sources": [
                    str(item).strip()
                    for item in node.get("material_sources") or []
                    if str(item).strip()
                ],
                "reply_messages": [],
                "before_send_check": True,
                "should_send_payment_collection": False,
            }
            for index, node in enumerate(delayed_nodes, start=1)
        ]
        source_snapshot = {
            "plan_type": "closing_sequence",
            "runtime_mode": "shadow",
            "request_id": request_id,
            "sales_contact_key": scope.sales_contact_key,
            "policy_version": policy.get("policy_version"),
            "policy_checksum": policy.get("checksum"),
            "sequence_key": sequence_key,
            "current_node_key": current_node_key,
            "closing_decision": decision,
            "cardpoint_decision": state.get("cardpoint_decision") or {},
            "authoritative_facts": state.get("fact_envelope") or {},
        }
        created = self.repository.create_outreach_plan(
            customer_id=identity["customer_id"],
            corp_id=identity["corp_id"],
            user_id=_string(state.get("user_id")),
            wechat=identity["wechat"],
            external_userid=identity["external_userid"],
            customer_stage=_string(state.get("conversion_stage")),
            stall_reason=_string((state.get("cardpoint_decision") or {}).get("scenario_query")),
            customer_psychology=_string((state.get("emotion_decision") or {}).get("label")),
            plan_goal=_string(sequence.get("positioning") or sequence.get("name")),
            source_snapshot=source_snapshot,
            tasks=tasks,
            sop_plan_id=sop_plan_id,
        )
        return {"created": True, "cancelled": cancelled, **created}

    def _completed_cycle_blocks_auto_plan(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str,
        latest_customer_message_at: str,
    ) -> bool:
        loader = getattr(self.repository, "get_latest_completed_outreach_plan_for_customer", None)
        if not callable(loader):
            return False
        completed_plan = loader(
            customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
        )
        return _completed_cycle_blocks_automatic_replan(
            completed_plan if isinstance(completed_plan, dict) else {},
            latest_customer_message_at=latest_customer_message_at,
        )

    def _plan_lock(self, identity: dict[str, Any]) -> asyncio.Lock:
        scope = build_customer_scope(
            corp_id=identity.get("corp_id"),
            wechat=_string(identity.get("wechat")).lower(),
            external_userid=identity.get("external_userid"),
            customer_id=identity.get("customer_id"),
        )
        key = scope.sales_contact_key or "|".join(
            _string(identity.get(field)).lower()
            for field in ("corp_id", "wechat", "external_userid", "customer_id")
        )
        lock = self._plan_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._plan_locks[key] = lock
        return lock

    def _auto_approve_plan(self, plan_id: str) -> dict[str, Any]:
        customer_id = self._plan_customer_id(plan_id)
        self.repository.add_outreach_event(
            plan_id=plan_id,
            task_id="",
            customer_id=customer_id,
            event_type="plan_auto_approved",
            event_summary="Personalized outreach plan auto-approved and queued",
        )
        return self.repository.update_outreach_plan_status(plan_id, "active")

    def _plan_customer_id(self, plan_id: str) -> str:
        detail = self.repository.get_outreach_plan(plan_id)
        return str(detail.get("plan", {}).get("customer_id") or "")

    @staticmethod
    def _candidate_matches_keyword(candidate: dict[str, Any], keyword: str) -> bool:
        needle = keyword.strip().lower()
        if not needle:
            return True
        parts = [
            candidate.get("customer_id"),
            candidate.get("external_userid"),
            candidate.get("wechat"),
            candidate.get("platform_customer_name"),
            candidate.get("title"),
            candidate.get("last_customer_message"),
            candidate.get("latest_event_summary"),
            candidate.get("lifecycle_stage"),
        ]
        for field in ("portrait", "basic_info"):
            value = candidate.get(field)
            if isinstance(value, dict):
                parts.extend(str(item) for item in value.values())
        return needle in " ".join(_string(part).lower() for part in parts if part is not None)

    @staticmethod
    def _conversation_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        messages = data.get("messages") if isinstance(data, dict) else []
        return [item for item in messages if isinstance(item, dict)] if isinstance(messages, list) else []

    @staticmethod
    def _local_context_messages(recent_messages: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for item in recent_messages[-max(1, min(limit, 50)):]:
            role = _string(item.get("role"))
            created_at = _string(item.get("created_at"))
            if role == "user":
                content = _string(item.get("content"))
                if content:
                    output.append(
                        {
                            "direction": "customer",
                            "sender_type": "customer",
                            "sender_name": "客户",
                            "content": content,
                            "msgtype": "text",
                            "created_at": created_at,
                        }
                    )
                continue
            reply_messages = item.get("reply_messages") if isinstance(item.get("reply_messages"), list) else []
            if reply_messages:
                for reply in reply_messages:
                    if not isinstance(reply, dict):
                        continue
                    output.append(
                        {
                            "direction": "staff",
                            "sender_type": "staff",
                            "sender_name": "员工",
                            "content": reply.get("content"),
                            "msgtype": _string(reply.get("type")) or "text",
                            "created_at": created_at,
                        }
                    )
                continue
            content = _string(item.get("content"))
            if content:
                output.append(
                    {
                        "direction": "staff",
                        "sender_type": "staff",
                        "sender_name": "员工",
                        "content": content,
                        "msgtype": "text",
                        "created_at": created_at,
                    }
                )
        return output

    @staticmethod
    def _latest_message_time(messages: list[dict[str, Any]], *, sender: str) -> str:
        candidates = []
        for item in messages:
            direction = _string(item.get("direction") or item.get("from") or item.get("sender_type")).lower()
            if sender == "customer" and direction not in {"customer", "user", "external"}:
                continue
            if sender == "staff" and direction not in {"staff", "assistant", "service", "ai"}:
                continue
            value = _message_time_iso(item.get("msgtime") or item.get("created_at") or item.get("send_time"))
            if value:
                candidates.append(value)
        return max(candidates) if candidates else ""

    @staticmethod
    def _customer_replied_after_plan(plan: dict[str, Any], latest_customer_message_at: Any) -> bool:
        latest = _parse_iso(_string(latest_customer_message_at))
        source_snapshot = plan.get("source_snapshot") if isinstance(plan.get("source_snapshot"), dict) else {}
        fact_snapshot = (
            source_snapshot.get("customer_fact_snapshot")
            if isinstance(source_snapshot.get("customer_fact_snapshot"), dict)
            else source_snapshot.get("memory")
            if isinstance(source_snapshot.get("memory"), dict)
            else {}
        )
        anchor = _parse_iso(_string(fact_snapshot.get("last_customer_message_at")))
        if not anchor:
            anchor = _parse_iso(_string(plan.get("created_at")))
        return bool(latest and anchor and latest > anchor)

