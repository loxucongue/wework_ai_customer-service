from __future__ import annotations

import copy
import re
import time
from typing import Any, Callable

from app.graph.nodes.common import model_call_metrics, model_recovery_attempts, model_usage_snapshot
from app.graph.nodes.material_selection import parallel_reply_payload
from app.graph.nodes.reply_admission import validate_model_led_reply_admission
from app.graph.nodes.reply_validation import _parallel_paid_deposit_context
from app.graph.nodes.reply_quality import collect_reply_observation_metrics
from app.graph.nodes.sales_fact_validation import validate_sales_price_fact_boundaries
from app.graph.state import AgentState
from app.prompts.reply_synthesizer import alias_reply_reference_fields, restore_reply_output_references
from app.services.model_client import ModelClient
from app.services.runtime_budget import can_start_model_retry, model_deadline_monotonic, runtime_budget_snapshot
from app.services.trace_logger import TraceLogger
from app.graph.nodes.reply_nodes import (
    _capped_deadline,
    _chat_json_with_deadline,
    _link_adopted_script_media,
    _model_budget_seconds,
    _model_reply_presentation_limits,
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

            messages = _low_information_input_recovery(state)
            if messages:
                reply_source = "low_information_input_recovery"
                model_call = {"name": "reply_synthesizer_model", "input": {}, "skipped": "low_information_input"}

            model_reply_ready = bool(model_client and model_client.available and should_use_model_reply(state))

            if messages:
                pass
            elif model_reply_ready and model_client is not None:
                try:
                    reply_state = dict(state)
                    reply_state["_reply_presentation_limits"] = (
                        _model_reply_presentation_limits(model_client)
                    )
                    messages, model_call, reply_source = await _run_reply_model_pipeline(
                        state=reply_state,
                        model_client=model_client,
                        model_messages=reply_messages_for_model(reply_state),
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
                        messages = [{"type": "text", "order": 1, "content": "您稍等一下"}]
                        model_call["validated_json_output"] = (
                            _fact_failure_recovery_observability_payload(
                                model_call,
                                messages,
                                reason="terminal_reply_failure_fallback",
                            )
                        )
                        warnings.append(
                            {
                                "node": "synthesize_reply",
                                "message": "terminal_reply_failure_fallback_used",
                                "detail": primary_error[:500],
                            }
                        )
                        reply_source = "failure_fallback"
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
            retry_call = (
                (model_call or {}).get("retry")
                if isinstance((model_call or {}).get("retry"), dict)
                else {}
            )
            recovery_reason = str(
                retry_call.get("error")
                or (model_call or {}).get("primary_error")
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
            reply_failure = _reply_failure_diagnostic(model_call)
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
                "mainline_delivery_state": (
                    parallel_reply_payload(state).get("mainline_delivery_state", {})
                    if state.get("evidence_join")
                    else {}
                ),
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
                "reply_failure": reply_failure,
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
    if "policy_decision_schema_invalid" in error_text:
        codes.append("policy_decision_schema_repaired")
    return codes


def _reply_failure_diagnostic(model_call: dict[str, Any] | None) -> dict[str, Any]:
    """Return a stable, redacted failure classification for evaluation and BI.

    Provider response bodies and model output are intentionally excluded.  The
    diagnostic says which attempt failed and why at an operational/contract
    level, so an evaluation report does not collapse every case into the
    unhelpful ``final_reply_failed`` label.
    """

    if not isinstance(model_call, dict):
        return {}
    retry = model_call.get("retry") if isinstance(model_call.get("retry"), dict) else {}
    primary_error = str(model_call.get("primary_error") or "")
    final_error = str(model_call.get("error") or "")
    repair_error = str(retry.get("error") or "")
    if not (primary_error or final_error or repair_error):
        return {}

    error_text = " ".join((primary_error, repair_error, final_error)).lower()
    if repair_error:
        stage = "repair"
    elif primary_error:
        stage = "primary"
    else:
        stage = "pipeline"
    recovered = bool(model_call.get("validated_json_output"))

    code = "unknown_reply_failure"
    category = "unknown"
    # Prefer the final repair's deterministic fact code over the broad
    # ``fact_required`` wrapper.  These identifiers contain no customer or
    # provider content and make evaluation/BI failures actionable.
    terminal_error = (repair_error or final_error or primary_error).lower()
    fact_codes = re.findall(
        r"\b([a-z][a-z0-9_]*(?:fact_required|fact_invalid|confirmation_required))\b",
        terminal_error,
    )
    if fact_codes:
        code = fact_codes[-1]
        category = "fact_validation"
    patterns = (
        ("policy_decision_explicit_exit_conflict", "policy_explicit_exit_conflict", "policy_safety"),
        ("policy_safety_floor_removed", "policy_safety_floor_removed", "policy_safety"),
        ("policy_decision_schema_invalid", "policy_schema_invalid", "policy_contract"),
        ("parking_fact_required", "parking_fact_required", "fact_validation"),
        ("business_hours_fact_required", "business_hours_fact_required", "fact_validation"),
        ("store_address_fact_required", "store_address_fact_required", "fact_validation"),
        ("store_availability_fact_required", "store_availability_fact_required", "fact_validation"),
        ("distance_fact_required", "distance_fact_required", "fact_validation"),
        ("fact_required", "authoritative_fact_required", "fact_validation"),
        ("unsupported_store", "authoritative_store_fact_invalid", "fact_validation"),
        ("store_address_text", "authoritative_store_fact_invalid", "fact_validation"),
        ("distance_value", "authoritative_store_fact_invalid", "fact_validation"),
        ("selected_content", "content_selection_invalid", "reply_contract"),
        ("reply_schema", "reply_schema_invalid", "reply_contract"),
        ("jsondecodeerror", "model_json_invalid", "model_protocol"),
        ("json output", "model_json_invalid", "model_protocol"),
        ("no model api key", "model_unavailable", "configuration"),
        ("reply_model_unavailable", "model_unavailable", "configuration"),
        ("timeout", "model_timeout", "provider"),
        ("deadline", "model_timeout", "provider"),
        ("http 429", "model_rate_limited", "provider"),
        ("http_status:429", "model_rate_limited", "provider"),
    )
    for marker, candidate_code, candidate_category in patterns:
        if code == "unknown_reply_failure" and marker in error_text:
            code = candidate_code
            category = candidate_category
            break
    if code == "unknown_reply_failure":
        http_match = re.search(r"(?:model http|http_status:)\s*(5\d\d)", error_text)
        if http_match:
            code = f"model_http_{http_match.group(1)}"
            category = "provider"
        elif "valueerror" in error_text or "validation" in error_text or "invalid" in error_text:
            code = "reply_validation_failed"
            category = "reply_contract"

    return {
        "status": "recovered" if recovered else "failed",
        "stage": stage,
        "category": category,
        "code": code,
        "repair_attempted": bool(retry),
        "primary_output_keys": sorted(
            str(key) for key in (model_call.get("raw_json_output") or {}).keys()
        )
        if isinstance(model_call.get("raw_json_output"), dict)
        else [],
        "repair_output_keys": sorted(
            str(key) for key in (retry.get("raw_json_output") or {}).keys()
        )
        if isinstance(retry.get("raw_json_output"), dict)
        else [],
    }


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
    elif "policy_decision_schema_invalid" in error_text:
        recovery_kind = "schema_invalid"
        text = "您稍等一下"
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


def _appointment_fact_failure_recovery(
    model_call: dict[str, Any],
    state: AgentState,
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """Keep a failed appointment claim safe without changing the customer's topic."""

    error_text = _model_call_error_text(model_call)
    if "appointment_confirmation_fact_required" in error_text:
        text = "您说的时间我先作为到店意向，具体接待安排还需要门店确认，确认后再过去，避免白跑。"
        reason = "appointment_confirmation_fact_recovery"
    elif "business_hours_fact_required" in error_text:
        text = "您说的时间我先作为到店意向，具体营业和接待安排需要以门店确认为准。"
        reason = "business_hours_fact_recovery"
    else:
        return None
    messages = [{"type": "text", "order": 1, "content": text}]
    try:
        validate_model_led_reply_admission(messages, state)
    except Exception:
        return None
    payload = _fact_failure_recovery_observability_payload(
        model_call,
        messages,
        reason=reason,
    )
    return messages, payload


def _store_failure_recovery_eligible(model_call: dict[str, Any]) -> bool:
    error_text = _model_call_error_text(model_call)
    return any(
        marker in error_text
        for marker in (
            "store_",
            "parking_fact_required",
            "distance_fact_required",
            "distance_value_not_customer_visible",
        )
    )


def _model_call_error_text(model_call: dict[str, Any]) -> str:
    retry = model_call.get("retry") if isinstance(model_call.get("retry"), dict) else {}
    return " ".join(
        str(value or "").lower()
        for value in (
            model_call.get("primary_error"),
            retry.get("error"),
            model_call.get("error"),
        )
    )


def _fact_failure_recovery_observability_payload(
    model_call: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    reason: str,
) -> dict[str, Any]:
    retry = model_call.get("retry") if isinstance(model_call.get("retry"), dict) else {}
    source = next(
        (
            item
            for item in (retry.get("raw_json_output"), model_call.get("raw_json_output"))
            if isinstance(item, dict) and isinstance(item.get("policy_decision"), dict)
        ),
        {},
    )
    policy_decision = copy.deepcopy(source.get("policy_decision") or {})
    closing = (
        policy_decision.get("closing_decision")
        if isinstance(policy_decision.get("closing_decision"), dict)
        else {}
    )
    if closing:
        closing.update({"action": "pause", "sequence_key": "none", "node_key": ""})
    return {
        "reply_messages": copy.deepcopy(messages),
        "action": "none",
        "selected_content_ids": [],
        "content_decisions": [],
        "commit_actions": [],
        "knowledge_use": {},
        "deposit_evidence": {},
        "sales_judgment": {
            "customer_goal": "",
            "primary_objective": "安全承接客户的到店时间意向",
            "customer_friction_observation": "",
            "posture": "answer",
            "reason": reason,
        },
        "policy_decision": policy_decision,
    }


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
    resolution = _store_resolution_for_recovery(state)
    text_store_summaries = [
        item
        for item in resolution.get("text_store_summaries") or []
        if isinstance(item, dict) and str(item.get("store_name") or item.get("name") or "").strip()
    ]
    if str(resolution.get("delivery_mode") or "").strip() == "text_store_list" and text_store_summaries:
        labels = [
            _store_text_list_line(item, index=index)
            for index, item in enumerate(text_store_summaries, start=1)
        ]
        midpoint = (len(labels) + 1) // 2
        messages = [
            {
                "type": "text",
                "order": 1,
                "content": "这个城市的全部门店如下：\n" + "\n".join(labels[:midpoint]),
            }
        ]
        if midpoint < len(labels):
            messages.append(
                {
                    "type": "text",
                    "order": 2,
                    "content": "\n".join(labels[midpoint:]) + "\n您想看哪一家，我再给您发门店位置。",
                }
            )
        else:
            messages[0]["content"] += "\n您想看哪一家，我再给您发门店位置。"
    elif (
        str(resolution.get("status") or "").strip() == "need_location"
        and resolution.get("available_districts")
    ):
        districts = [
            str(item).strip()
            for item in resolution.get("available_districts") or []
            if str(item).strip()
        ]
        examples = "、".join(districts[:6])
        suffix = "等区域" if len(districts) > 6 else ""
        city = str(resolution.get("city") or "这个城市").strip()
        messages = [
            {
                "type": "text",
                "order": 1,
                "content": (
                    f"{city}有门店，我们在{examples}{suffix}都有覆盖。"
                    "您在什么区，或者附近有什么地标？我帮您按位置找一家相对近的。"
                ),
            }
        ]
    elif message_payloads:
        messages = [
            {"type": "text", "order": 1, "content": "我把门店位置发您，您看下这个位置方便吗。"},
            *[
                {
                    "type": "store_address",
                    "order": index + 2,
                    "content": {"store_id": str(item["content"].get("store_id") or "").strip()},
                }
                for index, item in enumerate(message_payloads)
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


def _store_text_list_line(item: dict[str, Any], *, index: int) -> str:
    name = str(item.get("store_name") or item.get("name") or "").strip()
    district = str(item.get("district") or "").strip()
    address = str(item.get("store_address") or item.get("address") or "").strip()
    district_label = f"（{district}）" if district else ""
    address_label = f"，{address}" if address else ""
    return f"{index}. {name}{district_label}{address_label}"


def _verified_store_recovery_observability_payload(
    model_call: dict[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep an already-produced policy decision while disabling every action.

    Store recovery replaces only a fact-contract-invalid visible reply.  The
    model's intent/emotion observation remains useful for BI, but none of its
    action, content, payment, or write decisions may survive the recovery.
    """

    retry = model_call.get("retry") if isinstance(model_call.get("retry"), dict) else {}
    candidates = (retry.get("raw_json_output"), model_call.get("raw_json_output"))
    source = next(
        (
            item
            for item in candidates
            if isinstance(item, dict) and isinstance(item.get("policy_decision"), dict)
        ),
        None,
    )
    if not isinstance(source, dict):
        return {}
    return {
        "reply_messages": copy.deepcopy(messages),
        "action": "none",
        "selected_content_ids": [],
        "content_decisions": [],
        "commit_actions": [],
        "knowledge_use": {},
        "deposit_evidence": {},
        "sales_judgment": {
            "customer_goal": "",
            "primary_objective": "完成已核验的门店事实回复",
            "customer_friction_observation": "",
            "posture": "answer",
            "reason": "verified_store_delivery_failure_recovery",
        },
        "policy_decision": copy.deepcopy(source["policy_decision"]),
    }


def _store_resolution_for_recovery(state: AgentState) -> dict[str, Any]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = (
        fact_envelope.get("structured_facts")
        if isinstance(fact_envelope.get("structured_facts"), dict)
        else {}
    )
    resolution = structured.get("store_resolution_fact")
    if isinstance(resolution, dict):
        return resolution
    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    normalized = (
        joined.get("normalized_tool_facts")
        if isinstance(joined.get("normalized_tool_facts"), dict)
        else {}
    )
    structured = (
        normalized.get("structured_facts")
        if isinstance(normalized.get("structured_facts"), dict)
        else {}
    )
    resolution = structured.get("store_resolution_fact")
    return resolution if isinstance(resolution, dict) else {}


def _low_information_input_recovery(state: AgentState) -> list[dict[str, Any]]:
    """Return a safe reply for empty/pure-symbol standalone input.

    This is not a sales-intent shortcut.  It only handles requests that contain
    no user information for the model to reason over and no prior context that
    could make a short symbol meaningful.
    """

    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    if any(str(item or "").strip() for item in history):
        return []
    if state.get("location_card") or state.get("file_image") or state.get("image_urls"):
        return []
    content = str(state.get("normalized_content") or state.get("content") or "")
    compact = "".join(content.split())
    if compact and re.search(r"[\w\u4e00-\u9fff]", compact):
        return []
    messages = [{"type": "text", "order": 1, "content": "我在的，您可以直接把想了解的问题发我。"}]
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
    presentation_limits = _model_reply_presentation_limits(model_client)

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
            primary_warnings: list[dict[str, Any]] = []
            messages = _validated_parallel_reply_payload(
                state=state,
                payload=payload,
                validated_model_messages=validated_model_messages,
                warnings=primary_warnings,
                presentation_limits=presentation_limits,
            )
            warnings.extend(primary_warnings)
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
        # A transport/protocol retry remains on the customer-visible Reply
        # tier. Falling through to secondary/fast would bypass the configured
        # DeepSeek-only boundary and could launch a GPT recovery response.
        second_attempt_tier = tier
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
        repair_warnings: list[dict[str, Any]] = []
        messages = _validated_parallel_reply_payload(
            state=state,
            payload=repair_payload,
            validated_model_messages=validated_model_messages,
            warnings=repair_warnings,
            safety_floor=safety_floor,
            presentation_limits=presentation_limits,
        )
        warnings.extend(repair_warnings)
        model_call["validated_json_output"] = repair_payload
    except Exception as repair_error:
        salvage_attempts: list[tuple[str, dict[str, Any] | None, Exception]] = [
            ("repair", repair_payload, repair_error),
        ]
        if retry_mode == "targeted_repair" and previous_payload is not None:
            # A repair can collapse several valid customer sentences into one
            # still-invalid sentence. The original draft may be recoverable by
            # deleting only its rejected claims. Every candidate is validated
            # again against the complete current state, so this neither adds a
            # model call nor bypasses a fact/action boundary.
            salvage_attempts.append(("primary", previous_payload, primary_error))
        salvage_failures: list[dict[str, Any]] = []
        for salvage_source, salvage_input, salvage_input_error in salvage_attempts:
            salvaged_payload, salvage_codes = _salvage_repair_payload(
                salvage_input,
                salvage_input_error,
            )
            if retry_mode != "targeted_repair" or salvaged_payload is None:
                continue
            try:
                salvage_warnings: list[dict[str, Any]] = []
                messages = _validated_parallel_reply_payload(
                    state=state,
                    payload=salvaged_payload,
                    validated_model_messages=validated_model_messages,
                    warnings=salvage_warnings,
                    safety_floor=safety_floor,
                    presentation_limits=presentation_limits,
                )
            except Exception as salvage_error:
                salvage_failures.append(
                    {
                        "source": salvage_source,
                        "codes": salvage_codes,
                        "error": f"{type(salvage_error).__name__}: {salvage_error}",
                    }
                )
            else:
                repair_payload = salvaged_payload
                model_call["retry"] = {
                    **(model_call.get("retry") if isinstance(model_call.get("retry"), dict) else {}),
                    "initial_validation_error": f"{type(repair_error).__name__}: {repair_error}",
                    "salvage": {
                        "status": "accepted",
                        "source": salvage_source,
                        "codes": salvage_codes,
                    },
                }
                model_call["validated_json_output"] = repair_payload
                model_call["draft_messages"] = debug_message_contents(messages)
                model_call["output"] = {"messages": len(messages)}
                model_call["deadline"]["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
                warnings.extend(salvage_warnings)
                warnings.append(
                    {
                        "node": "synthesize_reply",
                        "message": "targeted_repair_invalid_sentences_removed",
                        "codes": salvage_codes,
                    }
                )
                return messages, model_call, "single_targeted_repair_model"
        if salvage_failures:
            model_call["retry"] = {
                **(model_call.get("retry") if isinstance(model_call.get("retry"), dict) else {}),
                "salvage": {
                    "status": "rejected",
                    "attempts": salvage_failures,
                },
            }
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
    presentation_limits: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    restore_reply_output_references(payload, parallel_reply_payload(state))
    if _normalize_post_payment_service_action(payload, state):
        warnings.append(
            {
                "node": "synthesize_reply",
                "message": "post_payment_service_action_normalized",
            }
        )
    linked_content_id = _link_adopted_script_media(payload, state)
    if linked_content_id:
        warnings.append(
            {
                "node": "synthesize_reply",
                "message": "adopted_script_media_linked",
                "content_id": linked_content_id,
            }
        )
    _validate_selected_content_ids(payload, state)
    if _resolve_selected_content_media_placeholders(payload, state):
        warnings.append(
            {
                "node": "synthesize_reply",
                "message": "selected_content_media_placeholder_resolved",
            }
        )
    validation_limits = dict(presentation_limits or {})
    policy_validation_state = dict(state)
    policy_validation_state["_reply_presentation_limits"] = validation_limits
    _validate_parallel_raw_reply_schema(
        payload,
        policy_validation_state,
    )
    _validate_policy_reply_consistency(payload, policy_validation_state)
    _validate_policy_safety_floor(payload, policy_validation_state, safety_floor)
    validation_state = _reply_validation_state(policy_validation_state, payload)
    messages = validated_model_messages(payload, validation_state)
    messages = _prepare_structural_messages(messages, validation_state, warnings)
    validate_model_led_reply_admission(messages, validation_state)
    return messages


def _normalize_post_payment_service_action(
    payload: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    """Keep an already-paid customer inside the post-payment service lane.

    The visible answer remains model-owned.  This only repairs the model's
    administrative action enum when its own policy decision already says the
    customer is in an authoritative post-payment state.  A price or fact
    question can therefore still be answered without failing the entire turn
    because the observation field accidentally says ``explain_activity``.
    """

    policy = payload.get("policy_decision") if isinstance(payload.get("policy_decision"), dict) else {}
    closing = policy.get("closing_decision") if isinstance(policy.get("closing_decision"), dict) else {}
    if (
        str(closing.get("customer_state") or "").strip() != "post_payment_service"
        or not _parallel_paid_deposit_context(state)
    ):
        return False
    sales = payload.get("sales_judgment") if isinstance(payload.get("sales_judgment"), dict) else {}
    next_action = sales.get("next_sales_action") if isinstance(sales.get("next_sales_action"), dict) else {}
    if str(next_action.get("type") or "").strip() in {
        "post_payment_service",
        "keep_open",
        "ask_missing_fact",
    }:
        return False
    payload["sales_judgment"] = {
        **sales,
        "next_sales_action": {
            **next_action,
            "type": "post_payment_service",
            "target_stage": "post_payment_service",
            "reason": str(next_action.get("reason") or "authoritative_paid_service_boundary"),
        },
    }
    return True


_REPAIR_SENTENCE_SALVAGE_CODES = {
    "case_image_structure_required_when_reply_promises_delivery",
    "customer_visible_false_human_identity_claim",
    "offer_268_full_face_claim_conflict",
    "offer_bilateral_cheek_split_price_conflict",
    "offer_face_hand_price_scope_ambiguous",
    "offer_face_hand_total_268_conflict",
    "offer_repeat_visit_268_unverified",
    "stale_historical_store_topic_leak",
    "store_address_text_without_card",
    "store_availability_fact_required",
    "store_scope_confirmed_same_region_requery",
    "terminal_store_distance_objection_restates_negative",
}


def _salvage_repair_payload(
    payload: dict[str, Any] | None,
    error: Exception,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Remove only invalid customer-visible sentences from a failed repair.

    The model has already had its single allowed repair attempt.  These narrow
    contracts describe claims that can be removed without inventing wording,
    selecting an asset, changing a sales action, or creating a business fact.
    The complete payload is validated again afterwards; any remaining mismatch
    still fails closed.
    """

    if not isinstance(payload, dict):
        return None, []
    codes = _reply_admission_violation_codes(error)
    if not codes or any(code not in _REPAIR_SENTENCE_SALVAGE_CODES for code in codes):
        return None, codes
    messages = payload.get("reply_messages")
    if not isinstance(messages, list):
        return None, codes
    sales = payload.get("sales_judgment") if isinstance(payload.get("sales_judgment"), dict) else {}
    next_action = sales.get("next_sales_action") if isinstance(sales.get("next_sales_action"), dict) else {}
    if (
        "case_image_structure_required_when_reply_promises_delivery" in codes
        and str(next_action.get("type") or "").strip() == "send_effect_material"
    ):
        return None, codes

    changed = False
    cleaned_messages: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict) or str(item.get("type") or "").strip() != "text":
            if isinstance(item, dict):
                cleaned_messages.append(copy.deepcopy(item))
            continue
        original = str(item.get("content") or "")
        cleaned = _remove_repair_violation_sentences(original, set(codes))
        if cleaned != original.strip():
            changed = True
        if cleaned:
            cleaned_messages.append({**copy.deepcopy(item), "content": cleaned})

    if not changed or not any(
        str(item.get("type") or "") == "text" and str(item.get("content") or "").strip()
        for item in cleaned_messages
    ):
        return None, codes
    for index, item in enumerate(cleaned_messages, start=1):
        item["order"] = index
    salvaged = copy.deepcopy(payload)
    salvaged["reply_messages"] = cleaned_messages
    return salvaged, codes


def _reply_admission_violation_codes(error: Exception) -> list[str]:
    raw = str(error)
    marker = "reply_admission_violations::"
    if marker not in raw:
        return []
    body = raw.split(marker, 1)[1]
    return [item.strip() for item in body.split(";;") if item.strip()]


def _remove_repair_violation_sentences(text: str, codes: set[str]) -> str:
    pieces = re.findall(r"[^。！？!?\n]+[。！？!?\n]?", str(text or ""))
    kept: list[str] = []
    for piece in pieces:
        if "store_availability_fact_required" in codes:
            piece = _strip_unconfirmed_store_availability_prefix(piece)
        compact = re.sub(r"\s+", "", piece)
        if not compact:
            continue
        if (
            "customer_visible_false_human_identity_claim" in codes
            and any(
                marker in compact.lower()
                for marker in (
                    "我不是机器人",
                    "不是机器人",
                    "我不是ai",
                    "不是ai",
                    "我是真人",
                    "真人客服",
                    "我是人工",
                    "人工客服",
                )
            )
        ):
            continue
        if (
            "terminal_store_distance_objection_restates_negative" in codes
            and any(
                marker in compact
                for marker in (
                    "距离",
                    "太远",
                    "有点远",
                    "确实远",
                    "折腾",
                    "麻烦",
                    "不方便",
                    "不太方便",
                    "路程",
                )
            )
        ):
            continue
        if (
            codes.intersection(
                {
                    "offer_268_full_face_claim_conflict",
                    "offer_bilateral_cheek_split_price_conflict",
                    "offer_face_hand_price_scope_ambiguous",
                    "offer_face_hand_total_268_conflict",
                    "offer_repeat_visit_268_unverified",
                }
            )
            and _sentence_has_selected_price_conflict(compact, codes)
        ):
            continue
        if (
            "store_address_text_without_card" in codes
            and _sentence_promises_store_card(compact)
        ):
            continue
        if (
            "store_scope_confirmed_same_region_requery" in codes
            and _sentence_requeries_same_store_scope(compact)
        ):
            continue
        if (
            "case_image_structure_required_when_reply_promises_delivery" in codes
            and _sentence_promises_case_media(compact)
        ):
            continue
        if (
            "stale_historical_store_topic_leak" in codes
            and _sentence_revives_historical_store_topic(compact)
        ):
            continue
        kept.append(piece.strip())
    return "".join(kept).strip()


def _strip_unconfirmed_store_availability_prefix(text: str) -> str:
    """Remove only a leading unsupported store-exists assertion.

    The remainder must already have been written by Reply (normally a city or
    district clarification).  The helper never invents a location question or
    a store fact.
    """

    value = str(text or "").strip()
    patterns = (
        r"^(?:有的|有哦|有呢|有啊|有哈|有呀|有)(?:[，,。！!～~：:]|\s)*",
        r"^(?:(?:这边|附近)(?:是)?有(?:店|门店)?(?:的|哦|呢|啊|哈|呀)?)(?:[，,。！!～~：:]|\s)*",
    )
    for pattern in patterns:
        cleaned = re.sub(pattern, "", value, count=1)
        if cleaned != value:
            return cleaned.strip()
    return value


def _sentence_has_selected_price_conflict(text: str, codes: set[str]) -> bool:
    try:
        validate_sales_price_fact_boundaries([{"type": "text", "content": text}])
    except ValueError as exc:
        return str(exc).strip() in codes
    return False


def _sentence_promises_store_card(text: str) -> bool:
    if any(
        marker in text
        for marker in (
            "不能发地址",
            "无法发地址",
            "不能发位置",
            "无法发位置",
            "没有可发送的门店",
        )
    ):
        return False
    return any(
        marker in text
        for marker in (
            "地址我发",
            "地址我再发",
            "地址发您",
            "地址发你",
            "地址再发您",
            "地址再发你",
            "位置我发",
            "位置我再发",
            "位置发您",
            "位置发你",
            "位置再发您",
            "位置再发你",
            "点开导航",
            "直接导航过去",
            "门店卡片",
            "位置卡",
            "定位卡",
        )
    )


def _sentence_requeries_same_store_scope(text: str) -> bool:
    return bool(
        re.search(
            r"(?:哪个|哪一个|具体|什么|其他|常去|方便)[^。！？!?]{0,12}"
            r"(?:区域|区县|商圈|地铁站|路口|楼栋|位置)",
            text,
        )
    )


def _sentence_promises_case_media(text: str) -> bool:
    delivery_terms = (
        "给您发",
        "给你发",
        "发您",
        "发你",
        "继续给您看",
        "继续给你看",
        "再给您接一组",
        "再给你接一组",
        "找一张",
        "找一组",
        "挑一张",
        "挑一组",
        "选一张",
        "选一组",
        "可以先发",
        "可以发",
        "可以提供",
        "能提供",
        "先发一些",
        "先发一张",
        "先发一组",
        "这就发",
        "马上发",
    )
    media_terms = (
        "效果图",
        "案例",
        "改善参考",
        "实际参考图",
        "参考图",
        "同类淡斑",
        "同类改善参考",
    )
    return any(term in text for term in delivery_terms) and any(term in text for term in media_terms)


def _sentence_revives_historical_store_topic(text: str) -> bool:
    return any(
        marker in text
        for marker in ("之前", "前面", "刚才", "刚刚", "已经", "已发", "发过")
    ) and any(
        marker in text
        for marker in ("门店", "店址", "地址", "位置", "定位", "导航", "门店卡")
    )
