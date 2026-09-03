from __future__ import annotations

import copy
import time
from typing import Any, Callable

from app.graph.nodes.appointment_time_utils import normalize_time_text
from app.graph.nodes.common import model_call_metrics, model_recovery_attempts, model_usage_snapshot
from app.graph.nodes.material_selection import parallel_reply_payload
from app.graph.nodes.reply_admission import validate_model_led_reply_admission
from app.graph.nodes.reply_quality import collect_reply_observation_metrics
from app.graph.state import AgentState
from app.prompts.reply_synthesizer import alias_reply_reference_fields, restore_reply_output_references
from app.services.model_client import ModelClient
from app.services.runtime_budget import can_start_model_retry, model_deadline_monotonic, runtime_budget_snapshot
from app.services.trace_logger import TraceLogger
from app.graph.nodes.reply_nodes import (
    _capped_deadline,
    _chat_json_with_deadline,
    _model_budget_seconds,
    _parallel_content_selection_metrics,
    _parallel_reply_repair_context,
    _policy_safety_floor,
    _prepare_structural_messages,
    _reply_full_task_retry_messages,
    _reply_metadata_from_model_call,
    _reply_model_tier,
    _reply_retry_messages,
    _reply_validation_state,
    _resolve_selected_content_media_placeholders,
    _schedule_profile_event_background,
    _validate_parallel_raw_reply_schema,
    _validate_policy_reply_consistency,
    _validate_policy_safety_floor,
    _validate_selected_content_ids,
)


class ReplyModelPipelineError(RuntimeError):
    """Keep failed model payloads available to local traces and release audits."""

    def __init__(self, message: str, *, model_call: dict[str, Any]) -> None:
        super().__init__(message)
        self.model_call = model_call


def create_synthesize_reply_node(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    reply_messages_for_model: Callable[[AgentState], list[dict[str, Any]]],
    should_use_model_reply: Callable[[AgentState], bool],
    validated_model_messages: Callable[..., list[dict[str, Any]]],
    schedule_background_task: Callable[[AgentState], Any] | None = None,
):
    async def synthesize_reply(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "synthesize_reply",
            {"fact_envelope": state.get("fact_envelope"), "required_tools": state.get("required_tools")},
        ) as span:
            errors = list(state.get("errors", []))
            warnings = list(state.get("warnings", []))
            messages: list[dict[str, Any]] = []
            reply_source = "main_model"
            model_call: dict[str, Any] | None = None

            model_reply_ready = bool(model_client and model_client.available and should_use_model_reply(state))

            if model_reply_ready and model_client is not None:
                try:
                    messages, model_call, reply_source = await _run_reply_model_pipeline(
                        state=state,
                        model_client=model_client,
                        model_messages=reply_messages_for_model(state),
                        validated_model_messages=validated_model_messages,
                        debug_message_contents=debug_message_contents,
                        warnings=warnings,
                    )
                except Exception as exc:
                    primary_error = f"{type(exc).__name__}: {exc}"
                    failed_model_call = getattr(exc, "model_call", None)
                    model_call = (
                        failed_model_call
                        if isinstance(failed_model_call, dict)
                        else model_call or {"name": "reply_synthesizer_model", "input": {}}
                    )
                    model_call["error"] = primary_error
                    errors.append(
                        {"node": "synthesize_reply", "message": "final_reply_failed", "detail": primary_error}
                    )
                    safety_recovery = _policy_safety_failure_recovery(model_call)
                    if safety_recovery:
                        messages, safe_payload = safety_recovery
                        model_call["validated_json_output"] = safe_payload
                        warnings.append(
                            {
                                "node": "synthesize_reply",
                                "message": "policy_safety_failure_recovery_used",
                                "detail": primary_error[:500],
                            }
                        )
                        reply_source = "policy_safety_failure_recovery"
                    else:
                        messages = _verified_store_delivery_failure_recovery(state)
                    if messages:
                        if reply_source != "policy_safety_failure_recovery":
                            warnings.append(
                                {
                                    "node": "synthesize_reply",
                                    "message": "verified_store_delivery_failure_recovery_used",
                                    "detail": primary_error[:500],
                                }
                            )
                            reply_source = "verified_store_delivery_failure_recovery"
                    else:
                        messages = _appointment_time_failure_recovery(state)
                        if messages:
                            warnings.append(
                                {
                                    "node": "synthesize_reply",
                                    "message": "appointment_time_failure_recovery_used",
                                    "detail": primary_error[:500],
                                }
                            )
                            reply_source = "appointment_time_failure_recovery"
                        else:
                            messages = []
                            reply_source = "reply_failed"
            else:
                reason = "reply_model_unavailable"
                errors.append({"node": "synthesize_reply", "message": "final_reply_failed", "detail": reason})
                reply_source = "reply_failed"
                model_call = {"name": "reply_synthesizer_model", "input": {}, "error": reason}

            fallback_source = ""
            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            context_metrics = dict(state.get("model_context_metrics") or {})
            context_metrics["reply"] = model_call_metrics(model_call, prompt_warning_threshold=16_000)
            recovery_attempts = [
                *list(state.get("recovery_attempts") or []),
                *model_recovery_attempts(model_call, node="synthesize_reply"),
            ]
            recovery_reason = str(
                (model_call or {}).get("primary_error")
                or (model_call or {}).get("error")
                or state.get("recovery_reason")
                or ""
            )[:500]
            reply_metadata = (
                _reply_metadata_from_model_call(model_call, state=state) if state.get("evidence_join") else {}
            )
            policy_correction_codes = _policy_correction_codes(model_call)
            decision_reasons = list(
                dict.fromkeys(
                    [
                        *[str(item) for item in reply_metadata.get("decision_reasons", []) if str(item)],
                        *policy_correction_codes,
                    ]
                )
            )
            decision_status = str(reply_metadata.get("decision_status") or "")
            if policy_correction_codes:
                decision_status = "degraded"
            content_selection_metrics = (
                _parallel_content_selection_metrics(
                    state,
                    messages=messages,
                    selected_ids=reply_metadata.get("selected_content_ids", []),
                    used_fact_refs=reply_metadata.get("used_fact_refs", []),
                )
                if state.get("evidence_join")
                else {}
            )
            reply_observation_metrics = (
                collect_reply_observation_metrics(messages, state) if state.get("evidence_join") else {}
            )
            output = {
                "reply_messages": messages,
                "used_fact_refs": reply_metadata.get("used_fact_refs", []),
                "selected_content_ids": reply_metadata.get("selected_content_ids", []),
                "reply_content_decisions": reply_metadata.get("content_decisions", []),
                "content_selection_metrics": content_selection_metrics,
                "reply_observation_metrics": reply_observation_metrics,
                "reply_action": reply_metadata.get("action", "none"),
                "reply_action_reason": reply_metadata.get("action_reason", ""),
                "reply_sales_judgment": reply_metadata.get("sales_judgment", {}),
                "reply_knowledge_use": reply_metadata.get("knowledge_use", {}),
                "policy_decision": reply_metadata.get("policy_decision", {}),
                "decision_status": decision_status,
                "decision_reasons": decision_reasons,
                "primary_task": reply_metadata.get("primary_task", {}),
                "secondary_tasks": reply_metadata.get("secondary_tasks", []),
                "realtime_intent": reply_metadata.get("realtime_intent", {}),
                "emotion_decision": reply_metadata.get("emotion_decision", {}),
                "closing_decision": reply_metadata.get("closing_decision", {}),
                "cardpoint_decision": reply_metadata.get("cardpoint_decision", {}),
                "reply_payment_assessment": reply_metadata.get("payment_assessment", {}),
                "reply_payment_channel": reply_metadata.get("payment_channel", "none"),
                "reply_deposit_evidence": reply_metadata.get("deposit_evidence", {}),
                "reply_safety_assessment": reply_metadata.get("safety_assessment", {}),
                "reply_party_size_assessment": reply_metadata.get("party_size_assessment", {}),
                "commit_actions": reply_metadata.get("commit_actions", []),
                "reply_source": reply_source,
                "postprocess_changed": False,
                "postprocess_reasons": [],
                "errors": errors,
                "warnings": warnings,
                "model_deadline": {
                    **dict(state.get("model_deadline") or {}),
                    "reply": dict((model_call or {}).get("deadline") or {}),
                },
                "model_context_metrics": context_metrics,
                "recovery_attempts": recovery_attempts,
                "recovery_reason": recovery_reason,
                "fallback_source": fallback_source,
                "fallback_failure_node": "synthesize_reply" if fallback_source else "",
                "fallback_retry_count": len(recovery_attempts) if fallback_source else 0,
                "fallback_violation": recovery_reason if fallback_source else "",
                "fallback_remaining_budget": (
                    runtime_budget_snapshot(state, tier=_reply_model_tier(state)) if fallback_source else {}
                ),
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            _schedule_profile_event_background(schedule_background_task, {**state, **output})
            return output

    return synthesize_reply


def _policy_correction_codes(model_call: dict[str, Any] | None) -> list[str]:
    if not isinstance(model_call, dict):
        return []
    retry = model_call.get("retry") if isinstance(model_call.get("retry"), dict) else {}
    error_text = " ".join(
        str(value or "")
        for value in (
            model_call.get("primary_error"),
            model_call.get("error"),
            retry.get("error"),
        )
    )
    codes: list[str] = []
    if (
        "policy_decision_explicit_exit_conflict" in error_text
        or "policy_safety_floor_removed:explicit_exit" in error_text
    ):
        codes.append("explicit_exit_same_turn_sales_conflict")
    if (
        "policy_decision_pause_marketing_conflict" in error_text
        or "policy_safety_floor_removed:pause_marketing" in error_text
    ):
        codes.append("pause_marketing_same_turn_sales_conflict")
    if (
        "policy_decision_active_cardpoint_conflict" in error_text
        or "policy_safety_floor_removed:active_cardpoint" in error_text
    ):
        codes.append("active_cardpoint_same_turn_sales_conflict")
    if "policy_decision_schema_invalid" in error_text:
        codes.append("policy_decision_schema_repaired")
    return codes


def _policy_safety_failure_recovery(
    model_call: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """Return a non-marketing close when both model attempts violate safety."""

    retry = model_call.get("retry") if isinstance(model_call.get("retry"), dict) else {}
    error_text = " ".join(
        str(value or "")
        for value in (
            model_call.get("primary_error"),
            model_call.get("error"),
            retry.get("error"),
        )
    )
    if (
        "policy_decision_explicit_exit_conflict" in error_text
        or "policy_safety_floor_removed:explicit_exit" in error_text
    ):
        recovery_kind = "explicit_exit"
        text = "好的，知道了，之后不再打扰您。"
    elif (
        "policy_decision_pause_marketing_conflict" in error_text
        or "policy_safety_floor_removed:pause_marketing" in error_text
    ):
        recovery_kind = "pause_marketing"
        text = "收到，我先不继续推了。您说的情况我记下了。"
    elif (
        "policy_decision_active_cardpoint_conflict" in error_text
        or "policy_safety_floor_removed:active_cardpoint" in error_text
    ):
        recovery_kind = "active_cardpoint"
        text = "收到，我先不着急往下推进。您把现在最担心的点告诉我，我先帮您说清楚。"
    elif "policy_decision_schema_invalid" in error_text:
        recovery_kind = "schema_invalid"
        text = "收到，我先不着急往下推进。您现在最想了解哪一点？我先按您当前的问题说清楚。"
    else:
        return None

    source = model_call.get("raw_json_output")
    if recovery_kind == "schema_invalid" and isinstance(retry.get("raw_json_output"), dict):
        source = retry.get("raw_json_output")
    if not isinstance(source, dict):
        source = retry.get("raw_json_output")
    if not isinstance(source, dict):
        return None
    safe_payload = copy.deepcopy(source)
    safe_payload.update(
        {
            "reply_messages": [{"type": "text", "order": 1, "content": text}],
            "action": "none",
            "selected_content_ids": [],
            "content_decisions": [],
            "commit_actions": [],
            "knowledge_use": {},
            "deposit_evidence": {},
            "sales_judgment": {
                "customer_goal": "",
                "primary_objective": "安全收尾",
                "customer_friction_observation": "",
                "posture": "close" if recovery_kind == "explicit_exit" else "pause",
                "reason": "policy_safety_failure_recovery",
            },
        }
    )
    return safe_payload["reply_messages"], safe_payload


def _verified_store_delivery_failure_recovery(state: AgentState) -> list[dict[str, Any]]:
    """Recover already-authorized current-turn store replies.

    This is a non-semantic failure guard: it does not choose a store, select a
    sales strategy, or infer customer intent. It only wraps verified
    ``structured_delivery_options.store_address.message_payloads`` or a verified
    current-turn store lookup status with the minimum visible text required by
    the external message contract.
    """

    if not state.get("evidence_join"):
        return []
    payload = parallel_reply_payload(state)
    delivery_options = (
        payload.get("structured_delivery_options")
        if isinstance(payload.get("structured_delivery_options"), dict)
        else {}
    )
    store_delivery = (
        delivery_options.get("store_address")
        if isinstance(delivery_options.get("store_address"), dict)
        else {}
    )
    message_payloads = [
        item
        for item in store_delivery.get("message_payloads") or []
        if isinstance(item, dict)
        and str(item.get("type") or "") == "store_address"
        and isinstance(item.get("content"), dict)
        and str(item["content"].get("store_id") or "").strip()
    ]
    if message_payloads:
        messages = [
            {"type": "text", "order": 1, "content": "我把门店位置发您，您看下这个位置方便吗。"},
            *[
                {
                    "type": "store_address",
                    "order": index + 2,
                    "content": {"store_id": str(item["content"].get("store_id") or "").strip()},
                }
                for index, item in enumerate(message_payloads[:3])
            ],
        ]
    else:
        lookup = _store_lookup_for_recovery(state)
        status = str(lookup.get("status") or "").strip()
        if status in {"need_location", "need_location_confirmation", "ambiguous_location", "geocode_query_conflict"}:
            messages = [
                {
                    "type": "text",
                    "order": 1,
                    "content": "可以帮您查附近是否有门店，不过还需要您补一下城市、区县或附近地标，这样我才能查准。",
                }
            ]
        elif status == "no_match":
            messages = [
                {
                    "type": "text",
                    "order": 1,
                    "content": "目前这个范围暂时没有查到可发送的本地门店，您也可以换一个附近城市或更具体位置，我再帮您核对。",
                }
            ]
        else:
            return []
    try:
        validate_model_led_reply_admission(messages, state)
    except Exception:
        return []
    return messages


def _appointment_time_failure_recovery(state: AgentState) -> list[dict[str, Any]]:
    """Recover a non-store appointment-time intent without asserting availability."""

    if not state.get("evidence_join"):
        return []
    content = str(state.get("normalized_content") or state.get("content") or "").strip()
    if not content or not normalize_time_text(content):
        return []
    messages = [
        {
            "type": "text",
            "order": 1,
            "content": "这个时间我先按您的到店意向理解，具体能不能安排还需要先确认门店和接待安排。您在哪个城市或区县？我先帮您查门店，避免白跑。",
        }
    ]
    try:
        validate_model_led_reply_admission(messages, state)
    except Exception:
        return []
    return messages


def _store_lookup_for_recovery(state: AgentState) -> dict[str, Any]:
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    lookup = tool_results.get("customer_store_lookup") if isinstance(tool_results.get("customer_store_lookup"), dict) else {}
    if lookup:
        return lookup
    readonly = tool_results.get("readonly_facts") if isinstance(tool_results.get("readonly_facts"), dict) else {}
    lookup = readonly.get("customer_store_lookup") if isinstance(readonly.get("customer_store_lookup"), dict) else {}
    if lookup:
        return lookup
    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    normalized = joined.get("normalized_tool_facts") if isinstance(joined.get("normalized_tool_facts"), dict) else {}
    lookup = normalized.get("customer_store_lookup") if isinstance(normalized.get("customer_store_lookup"), dict) else {}
    return lookup


async def _run_reply_model_pipeline(
    *,
    state: AgentState,
    model_client: ModelClient,
    model_messages: list[dict[str, Any]],
    validated_model_messages: Callable[..., list[dict[str, Any]]],
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    warnings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    if not state.get("evidence_join"):
        raise ReplyModelPipelineError(
            "v3_evidence_join_required",
            model_call={"name": "reply_synthesizer_model", "error": "v3_evidence_join_required"},
        )
    return await _run_model_led_reply_pipeline(
        state=state,
        model_client=model_client,
        model_messages=model_messages,
        validated_model_messages=validated_model_messages,
        debug_message_contents=debug_message_contents,
        warnings=warnings,
    )


async def _run_model_led_reply_pipeline(
    *,
    state: AgentState,
    model_client: ModelClient,
    model_messages: list[dict[str, Any]],
    validated_model_messages: Callable[..., list[dict[str, Any]]],
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    warnings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Run the final sales brain once, then allow one evidence-complete repair.

    Transport retries remain ModelClient-owned. This layer does not switch to a
    smaller scene prompt or manufacture a business reply after validation.
    """

    tier = _reply_model_tier(state)
    primary_budget = _model_budget_seconds(model_client, "model_reply_primary_budget_seconds", 30.0)
    repair_budget = _model_budget_seconds(model_client, "model_reply_recovery_budget_seconds", 25.0)
    started_at = time.monotonic()
    round_deadline = model_deadline_monotonic(state, tier=tier)
    repair_reserve_seconds = min(repair_budget, 9.0)
    primary_round_deadline = round_deadline - repair_reserve_seconds if round_deadline is not None else None
    primary_deadline = _capped_deadline(started_at + primary_budget, primary_round_deadline)
    model_call: dict[str, Any] = {
        "name": "reply_synthesizer_model",
        "input": {"tier": tier, "required": True, "messages": model_messages},
        "deadline": {
            "primary_budget_seconds": primary_budget,
            "repair_budget_seconds": repair_budget,
            "runtime_budget": runtime_budget_snapshot(state, tier=tier),
        },
    }

    primary_error: Exception
    if primary_deadline is not None and primary_deadline <= started_at + 1.0:
        primary_error = TimeoutError("reply_primary_skipped_to_preserve_single_repair_budget")
        model_call["primary_error"] = f"{type(primary_error).__name__}: {primary_error}"
    else:
        try:
            payload = await _chat_json_with_deadline(
                model_client,
                model_messages,
                tier=tier,
                deadline_monotonic=primary_deadline,
            )
            model_call["raw_json_output"] = copy.deepcopy(payload)
            model_call["usage"] = model_usage_snapshot(model_client)
            messages = _validated_parallel_reply_payload(
                state=state,
                payload=payload,
                validated_model_messages=validated_model_messages,
                warnings=warnings,
            )
            model_call["validated_json_output"] = payload
            model_call["draft_messages"] = debug_message_contents(messages)
            model_call["output"] = {"messages": len(messages)}
            model_call["deadline"]["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
            return messages, model_call, "main_model"
        except Exception as exc:
            primary_error = exc
            model_call["primary_error"] = f"{type(exc).__name__}: {exc}"

    if not can_start_model_retry(state, tier=tier):
        model_call["repair"] = {
            "status": "skipped_insufficient_round_budget",
            "reason": f"{type(primary_error).__name__}: {primary_error}",
            "runtime_budget": runtime_budget_snapshot(state, tier=tier),
        }
        model_call["deadline"]["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
        raise ReplyModelPipelineError(
            f"reply primary failed and repair budget is unavailable: "
            f"{type(primary_error).__name__}: {primary_error}",
            model_call=model_call,
        ) from primary_error

    previous_payload = (
        model_call.get("raw_json_output") if isinstance(model_call.get("raw_json_output"), dict) else None
    )
    safety_floor = _policy_safety_floor(previous_payload, state) if previous_payload else ""
    repair_validation_context = alias_reply_reference_fields(
        _parallel_reply_repair_context(state),
        parallel_reply_payload(state),
    )
    if previous_payload is None:
        # A transport timeout or protocol failure produced no business decision
        # to repair. Re-run the complete Reply task with its full evidence rather
        # than replacing it with the narrow structural-repair contract.
        repair_messages = _reply_full_task_retry_messages(model_messages, primary_error)
        retry_mode = "full_task_retry"
        second_attempt_budget = repair_budget
        second_attempt_tier = (
            "secondary"
            if bool(getattr(model_client, "secondary_available", False))
            and hasattr(model_client, "chat_json_secondary")
            else "fast"
        )
    else:
        repair_messages = _reply_retry_messages(
            model_messages,
            primary_error,
            previous_payload=previous_payload,
            validation_context=repair_validation_context,
        )
        retry_mode = "targeted_repair"
        second_attempt_budget = repair_budget
        second_attempt_tier = tier
    repair_deadline = _capped_deadline(
        time.monotonic() + second_attempt_budget,
        round_deadline,
    )
    repair_payload: dict[str, Any] | None = None
    try:
        if second_attempt_tier == "secondary":
            repair_payload = await model_client.chat_json_secondary(
                repair_messages,
                temperature=0,
                deadline_monotonic=repair_deadline,
            )
        else:
            repair_payload = await _chat_json_with_deadline(
                model_client,
                repair_messages,
                tier=second_attempt_tier,
                deadline_monotonic=repair_deadline,
            )
        model_call["retry"] = {
            "mode": retry_mode,
            "tier": second_attempt_tier,
            "reason": f"{type(primary_error).__name__}: {primary_error}",
            "messages": repair_messages,
            "raw_json_output": copy.deepcopy(repair_payload),
            "usage": model_usage_snapshot(model_client),
        }
        messages = _validated_parallel_reply_payload(
            state=state,
            payload=repair_payload,
            validated_model_messages=validated_model_messages,
            warnings=warnings,
            safety_floor=safety_floor,
        )
        model_call["validated_json_output"] = repair_payload
    except Exception as repair_error:
        model_call["retry"] = {
            **(model_call.get("retry") if isinstance(model_call.get("retry"), dict) else {}),
            "mode": retry_mode,
            "tier": second_attempt_tier,
            "reason": f"{type(primary_error).__name__}: {primary_error}",
            "error": f"{type(repair_error).__name__}: {repair_error}",
            "usage": model_usage_snapshot(model_client),
        }
        model_call["deadline"]["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
        raise ReplyModelPipelineError(
            f"reply primary failed: {type(primary_error).__name__}: {primary_error}; "
            f"single repair failed: {type(repair_error).__name__}: {repair_error}",
            model_call=model_call,
        ) from repair_error

    model_call["draft_messages"] = debug_message_contents(messages)
    model_call["output"] = {"messages": len(messages)}
    model_call["deadline"]["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
    reply_source = "single_full_task_retry_model" if retry_mode == "full_task_retry" else "single_targeted_repair_model"
    return messages, model_call, reply_source


def _validated_parallel_reply_payload(
    *,
    state: AgentState,
    payload: dict[str, Any],
    validated_model_messages: Callable[..., list[dict[str, Any]]],
    warnings: list[dict[str, Any]],
    safety_floor: str = "",
) -> list[dict[str, Any]]:
    restore_reply_output_references(payload, parallel_reply_payload(state))
    _validate_selected_content_ids(payload, state)
    if _resolve_selected_content_media_placeholders(payload, state):
        warnings.append(
            {
                "node": "synthesize_reply",
                "message": "selected_content_media_placeholder_resolved",
            }
        )
    _validate_parallel_raw_reply_schema(payload)
    _validate_policy_reply_consistency(payload, state)
    _validate_policy_safety_floor(payload, state, safety_floor)
    validation_state = _reply_validation_state(state, payload)
    messages = validated_model_messages(payload, validation_state)
    messages = _prepare_structural_messages(messages, validation_state, warnings)
    validate_model_led_reply_admission(messages, validation_state)
    return messages
