from __future__ import annotations

from typing import Any


_LEGACY_DEPENDENCIES = (
    "FIRST_DAY_CONTRACT_VERIFIER_PROMPT",
    "FIRST_DAY_CONTRACT_VERIFIER_PROMPT_VERSION",
    "FIRST_DAY_PLAN_WRITER_PROMPT",
    "FIRST_DAY_PLAN_WRITER_PROMPT_VERSION",
    "FIRST_DAY_SCENE_ANALYST_PROMPT",
    "FIRST_DAY_SCENE_ANALYST_PROMPT_VERSION",
    "FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT",
    "FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT_VERSION",
    "OUTREACH_PLAN_REVIEW_SYSTEM_PROMPT",
    "OUTREACH_PLAN_SCHEMA_REPAIR_SYSTEM_PROMPT",
    "OUTREACH_PLAN_SYSTEM_PROMPT",
    "S10_OUTREACH_CONTEXT",
    "_bool",
    "_compose_outreach_messages",
    "_conversation_activity_from_context",
    "_first_day_available_sources_by_scene",
    "_first_day_configured_assets_for_step",
    "_first_day_final_plan_error",
    "_first_day_internal_activity_quote_evidence",
    "_first_day_materialized_sop_messages",
    "_first_day_message_policy_error",
    "_first_day_outreach_plan_error",
    "_first_day_scene_analysis_error",
    "_first_day_sop_pack_for_step",
    "_first_day_sop_pack_texts",
    "_first_day_upgrade_scene_repeat_repair_to_replan",
    "_first_day_verifier_error",
    "_first_day_writer_payload",
    "_int",
    "_is_first_day_opened_silence_trigger",
    "_list_strings",
    "_media_url_identity",
    "_merge_first_day_scene_schema_repair",
    "_normalize_first_day_outreach_schedule",
    "_normalize_first_day_repaired_plan",
    "_normalize_first_day_scene_analysis",
    "_normalize_outreach_plan_response",
    "_normalize_outreach_schedule",
    "_outreach_plan_context_error",
    "_outreach_plan_structure_error",
    "_plan_step_texts",
    "_string",
    "_task_content_sources",
    "_valid_activity_quote_evidence",
    "appointment_blocker_materials",
    "asyncio",
    "build_appointment_blocker_asset_catalog",
    "build_appointment_blocker_scene_index",
    "build_outreach_activity_quote_fact",
    "customer_relation_is_deleted",
    "dumps",
    "enrich_recent_outreach_media",
    "outreach_customer_fact_snapshot",
    "personalized_order_eligibility",
    "personalized_payment_collection_eligibility",
    "recent_outreach_media",
    "utc_now_iso",
)


def _bind_legacy_dependencies() -> None:
    # Imported lazily to keep the public compatibility module acyclic at load time.
    from .. import outreach_service as compatibility

    namespace = globals()
    for name in _LEGACY_DEPENDENCIES:
        namespace[name] = getattr(compatibility, name)


class PlanGenerator:
    """Focused implementation component backed by the compatibility facade."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)

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
        _bind_legacy_dependencies()
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
                return result
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
            return result
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
            return result
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
        if first_day_trigger and not unopened_first_day:
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

            source_snapshot["first_day_workflow"] = {
                "scene_analysis": scene_analysis,
                "writer_result": {},
                "verifier_result": {},
                "traces": {"scene_analyst": analyst_trace},
            }
            if not _bool(scene_analysis.get("eligible")):
                response = {
                    "should_create_plan": False,
                    "stall_reason": _string(scene_analysis.get("suppress_reason"))
                    or "first_day_scene_analyst_suppressed",
                    "plan_arc": "",
                    "steps": [],
                }
            else:
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
        elif not first_day_trigger:
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

