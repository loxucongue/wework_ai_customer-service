from __future__ import annotations

import json
import time
from typing import Any, Callable

from app.graph.nodes.activity_intro_image import activity_intro_image_url, append_activity_intro_image
from app.graph.nodes.common import model_call_metrics, model_recovery_attempts, model_usage_snapshot
from app.graph.nodes.reply_quality import collect_reply_soft_warnings
from app.graph.nodes.reply_validation import validate_reply_consistency
from app.graph.nodes.reply_context import reply_recovery_payload_for_model
from app.graph.nodes.store_scope_summary import region_mentioned_in_text
from app.policies.constants import KNOWN_STORE_NAMES
from app.services.payment_collection import (
    normalize_payment_amount_text,
    payment_collection_content,
    payment_collection_context,
)
from app.services.risk_hold import explicit_professional_assist_reason, health_risk_hold, is_hard_health_risk_hold
from app.services.runtime_budget import can_start_model_retry, model_deadline_monotonic, runtime_budget_snapshot
from app.graph.state import AgentState
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


REPLY_RECOVERY_SYSTEM_PROMPT = """你是企业微信淡斑活动的真人销售回复模型。完整 Reply 已超时或未通过硬事实校验，请根据去重后的完整业务事实重新生成客户可见回复。精简只删除重复字段，不代表可以忽略业务规则、最近历史或结构事实。

要求：
- 只输出 JSON 对象：{\"reply_messages\":[{\"type\":\"text\",\"order\":1,\"content\":\"...\"}]}。
- 当前消息优先，结合最近12条原序历史。先直接解决本轮问题，再自然承接一个销售动作；“人呢/在吗”等短催促直接续最近未完动作，不列选项重问意图；不要复读整套规则，不要说“继续处理、安排下一步、温馨提醒、尊敬的客户”。
- 像真人微信聊天：短句、口语、具体，不暴露“事实、排序、工具、系统、流程、状态”等内部表达。客户只回“好/嗯”时，确认并轻推下一步，不重播上一轮顾虑、案例、价格和预约金全套内容。
- 只能使用输入中的工具、门店、订单、支付、图片和档期事实；没有事实就不要编。
- 发过 payment_collection 只代表发过卡，不代表已支付。只有成功支付截图或订单 prepay_paid 才是权威已付；客户口头说已付只能按声明承接，不能说已核款。
- 人数按到店总人数理解：“我朋友也一起”通常是本人+1位朋友=2位；“我带两个朋友”是本人+2位朋友=3位。卡片金额必须服从 Planner 的人数和金额决策。
- 客户明确要入口/预约时，不要因为缺订单或开单失败暴露“入口没对上/不能发卡”，也不能再反问“如果你要我再发”；活动报价已铺垫且无硬阻断时按当前结构事实发卡，否则只补最小必要信息。
- 没有真实 case_facts/image 不能说“我给您发图/图发您了”；有图且当前明确要图时才输出 image。上一轮顾虑已回答、客户只确认时不要擅自重发案例。
- 退款、扣款异常只能先核对门店、时间、金额、项目或截图；不能说已经同意/正在办理退款，也不能承诺自动退回、原路到账或处理时效。
- 不输出公里、分钟、车程；不承诺绝对效果；没有真实预约事实不能说已经安排好。
- payment_collection、store_address、image、human_handoff_notice 必须使用输入中已核验的结构事实。
- 使用自然微信口吻，不解释系统故障，不输出 markdown 或内部分析。
"""


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
            planner_messages: list[dict[str, Any]] = []
            reply_source = "main_model"
            model_call: dict[str, Any] | None = None

            planner_decision = str(state.get("planner_decision") or "").strip()
            planner_messages = _normalize_planner_reply_messages(state.get("planner_reply_messages"), state=state)
            planner_direct_valid = _planner_direct_reply_is_valid(planner_decision, planner_messages, state, warnings)
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
                    model_call = model_call or {"name": "reply_synthesizer_model", "input": {}}
                    model_call["error"] = primary_error
                    if planner_direct_valid:
                        messages = _prepare_structural_messages(planner_messages, state, warnings)
                        validate_reply_consistency(messages, state)
                        reply_source = "planner_direct_reply_after_model_failure"
                        model_call["fallback"] = {"strategy": "validated_planner_direct_reply", "reason": primary_error}
                    else:
                        errors.append({"node": "synthesize_reply", "message": "final_reply_failed", "detail": primary_error})
                        messages = []
            elif planner_direct_valid:
                messages = _prepare_structural_messages(planner_messages, state, warnings)
                validate_reply_consistency(messages, state)
                reply_source = "planner_direct_reply_model_unavailable_fallback"
                model_call = {
                    "name": "planner_direct_reply",
                    "input": {"decision": planner_decision, "messages": len(planner_messages), "reason": "reply_model_unavailable"},
                    "output": {"messages": len(messages)},
                }
            else:
                reason = "planner_no_reply_not_allowed_for_customer_turn" if planner_decision == "no_reply" else "reply_model_unavailable"
                errors.append({"node": "synthesize_reply", "message": "final_reply_failed", "detail": reason})
                model_call = {"name": "reply_synthesizer_model", "input": {}, "error": reason}

            messages, handoff_notice_appended = _ensure_required_handoff_notice(messages, state)
            if handoff_notice_appended:
                validate_reply_consistency(messages, state)
                warnings.append({"node": "synthesize_reply", "message": "handoff_notice_appended"})
                if model_call:
                    model_call["handoff_notice_appended"] = True
            messages, stale_handoff_removed = _suppress_stale_handoff_notice(messages, state)
            if stale_handoff_removed:
                validate_reply_consistency(messages, state)
                warnings.append({"node": "synthesize_reply", "message": "stale_handoff_notice_removed"})
                if model_call:
                    model_call["stale_handoff_notice_removed"] = True
            if not messages and errors:
                messages = _neutral_final_fallback_messages()
                reply_source = "deterministic_neutral_final_fallback"
                recovered_error = errors.pop() if errors else None
                if recovered_error:
                    warnings.append(
                        {
                            "node": "synthesize_reply",
                            "message": "final_reply_recovered_by_neutral_fallback",
                            "detail": str(recovered_error.get("detail") if isinstance(recovered_error, dict) else recovered_error),
                        }
                    )
                warnings.append({"node": "synthesize_reply", "message": "neutral_final_fallback_used"})
                if model_call:
                    model_call["fallback"] = {"strategy": "deterministic_neutral_final"}
                    model_call["output"] = {"messages": len(messages)}
                messages, handoff_notice_appended_after_fallback = _ensure_required_handoff_notice(messages, state)
                if handoff_notice_appended_after_fallback:
                    validate_reply_consistency(messages, state)
                    warnings.append({"node": "synthesize_reply", "message": "handoff_notice_appended"})
                    if model_call:
                        model_call["handoff_notice_appended"] = True
            if messages:
                warnings.extend(collect_reply_soft_warnings(messages, state))
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
            output = {
                "reply_messages": messages,
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
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            _schedule_profile_event_background(schedule_background_task, {**state, **output})
            return output

    return synthesize_reply


def _planner_direct_reply_is_valid(
    planner_decision: str,
    planner_messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[dict[str, Any]],
) -> bool:
    if planner_decision != "direct_reply" or not planner_messages:
        return False
    try:
        validate_reply_consistency(planner_messages, state)
        if state.get("tool_policy_violations"):
            warnings.append(
                {
                    "node": "synthesize_reply",
                    "message": "planner_direct_reply_used_despite_non_visible_tool_policy_violations",
                    "detail": "Planner draft passed final reply consistency; violations remain in trace for repair analysis.",
                }
            )
        return True
    except Exception as exc:
        warnings.append(
            {
                "node": "synthesize_reply",
                "message": "planner_direct_reply_rejected",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )
        return False


async def _run_reply_model_pipeline(
    *,
    state: AgentState,
    model_client: ModelClient,
    model_messages: list[dict[str, Any]],
    validated_model_messages: Callable[..., list[dict[str, Any]]],
    debug_message_contents: Callable[[list[dict[str, Any]]], list[str]],
    warnings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    tier = _reply_model_tier(state)
    primary_budget = _model_budget_seconds(model_client, "model_reply_primary_budget_seconds", 30.0)
    recovery_budget = _model_budget_seconds(model_client, "model_reply_recovery_budget_seconds", 15.0)
    started_at = time.monotonic()
    primary_deadline = _capped_deadline(
        started_at + primary_budget,
        model_deadline_monotonic(state, tier=tier),
    )
    model_call: dict[str, Any] = {
        "name": "reply_synthesizer_model",
        "input": {"tier": tier, "required": True, "messages": model_messages},
        "deadline": {
            "primary_budget_seconds": primary_budget,
            "recovery_budget_seconds": recovery_budget,
            "runtime_budget": runtime_budget_snapshot(state, tier=tier),
        },
    }
    primary_error: Exception | None = None

    try:
        payload = await _chat_json_with_deadline(
            model_client,
            model_messages,
            tier=tier,
            deadline_monotonic=primary_deadline,
        )
        model_call["raw_json_output"] = payload
        model_call["usage"] = model_usage_snapshot(model_client)
        try:
            messages = validated_model_messages(payload, state)
            messages = _prepare_structural_messages(messages, state, warnings)
            validate_reply_consistency(messages, state)
            _raise_repairable_reply_quality_issues(messages, state)
        except Exception as validation_exc:
            if not can_start_model_retry(state, tier=tier):
                model_call["retry"] = {
                    "reason": f"{type(validation_exc).__name__}: {validation_exc}",
                    "status": "skipped_insufficient_round_budget",
                    "runtime_budget": runtime_budget_snapshot(state, tier=tier),
                }
                raise
            retry_messages = _reply_retry_messages(model_messages, validation_exc)
            retry_deadline = _capped_deadline(
                time.monotonic() + recovery_budget,
                model_deadline_monotonic(state, tier=tier),
            )
            retry_payload = await _chat_json_with_deadline(
                model_client,
                retry_messages,
                tier=tier,
                deadline_monotonic=retry_deadline,
            )
            model_call["retry"] = {
                "reason": f"{type(validation_exc).__name__}: {validation_exc}",
                "messages": retry_messages,
                "raw_json_output": retry_payload,
                "usage": model_usage_snapshot(model_client),
            }
            messages = validated_model_messages(retry_payload, state)
            messages = _prepare_structural_messages(messages, state, warnings)
            validate_reply_consistency(messages, state)
            _raise_repairable_reply_quality_issues(messages, state)
        model_call["draft_messages"] = debug_message_contents(messages)
        model_call["output"] = {"messages": len(messages)}
        model_call["deadline"]["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
        return messages, model_call, "main_model"
    except Exception as exc:
        primary_error = exc
        model_call["primary_error"] = f"{type(exc).__name__}: {exc}"

    recovery_messages = _reply_recovery_messages(state)
    if not can_start_model_retry(state, tier=tier):
        model_call["recovery"] = {
            "status": "skipped_insufficient_round_budget",
            "runtime_budget": runtime_budget_snapshot(state, tier=tier),
        }
        model_call["deadline"]["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
        raise RuntimeError(f"reply primary failed: {type(primary_error).__name__}: {primary_error}") from primary_error
    recovery_deadline = _capped_deadline(
        time.monotonic() + recovery_budget,
        model_deadline_monotonic(state, tier=tier),
    )
    recovery_call: dict[str, Any] = {
        "tier": "fast",
        "messages": recovery_messages,
        "reason": f"{type(primary_error).__name__}: {primary_error}",
    }
    try:
        recovery_payload = await _chat_json_with_deadline(
            model_client,
            recovery_messages,
            tier="fast",
            deadline_monotonic=recovery_deadline,
        )
        recovery_call["raw_json_output"] = recovery_payload
        recovery_call["usage"] = model_usage_snapshot(model_client)
        messages = validated_model_messages(recovery_payload, state)
        messages = _prepare_structural_messages(messages, state, warnings)
        validate_reply_consistency(messages, state)
        _raise_repairable_reply_quality_issues(messages, state)
    except Exception as recovery_exc:
        recovery_call["error"] = f"{type(recovery_exc).__name__}: {recovery_exc}"
        recovery_call["usage"] = model_usage_snapshot(model_client)
        model_call["recovery"] = recovery_call
        model_call["deadline"]["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
        raise RuntimeError(
            f"reply primary failed: {type(primary_error).__name__}: {primary_error}; "
            f"compact recovery failed: {type(recovery_exc).__name__}: {recovery_exc}"
        ) from recovery_exc

    model_call["recovery"] = recovery_call
    model_call["draft_messages"] = debug_message_contents(messages)
    model_call["output"] = {"messages": len(messages)}
    model_call["deadline"]["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
    return messages, model_call, "compact_recovery_model"


def _raise_repairable_reply_quality_issues(messages: list[dict[str, Any]], state: AgentState) -> None:
    repairable_details = {
        "precision_reply_passive_mainline_closure",
        "precision_reply_missing_mainline_action",
        "manual_transfer_missing_screenshot_registration",
        "nearby_store_claim_without_location_fact",
    }
    for warning in collect_reply_soft_warnings(messages, state):
        detail = str(warning.get("detail") or "")
        if detail in repairable_details:
            raise ValueError(detail)


def _prepare_structural_messages(
    messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prepared = _filter_unsupported_images(messages, state, warnings)
    prepared = append_activity_intro_image(prepared, state, warnings)
    prepared, duplicate_payment_removed = _dedupe_payment_collection_messages(prepared)
    if duplicate_payment_removed:
        warnings.append({"node": "synthesize_reply", "message": "duplicate_payment_collection_removed"})
    prepared = _normalize_payment_amount_text_messages(prepared)
    prepared = _maybe_append_planner_payment_structure(prepared, state)
    prepared, duplicate_payment_removed = _dedupe_payment_collection_messages(prepared)
    if duplicate_payment_removed:
        warnings.append({"node": "synthesize_reply", "message": "duplicate_payment_collection_removed"})
    prepared, planner_store_cards_preserved = _preserve_planner_store_address_actions(prepared, state)
    if planner_store_cards_preserved:
        warnings.append({"node": "synthesize_reply", "message": "planner_store_address_action_preserved"})
    for warning in warnings:
        if isinstance(warning, dict) and warning.get("message") == "activity_intro_image_appended":
            warning.setdefault("node", "synthesize_reply")
    return prepared


def _maybe_append_planner_payment_structure(
    messages: list[dict[str, Any]],
    state: AgentState,
) -> list[dict[str, Any]]:
    if not messages or _messages_have_payment_collection(messages):
        return messages
    if not any(str(item.get("type") or "") == "text" for item in messages if isinstance(item, dict)):
        return messages
    if not _state_requires_payment_collection(state):
        return messages
    if is_hard_health_risk_hold(health_risk_hold(state)) or _state_has_paid_deposit_context(state):
        return messages
    context = payment_collection_context(state=state, messages=[])
    if context.get("over_limit"):
        return messages
    amount = int(context.get("amount") or 10)
    return _renumber(
        [
            *messages,
            {
                "type": "payment_collection",
                "content": payment_collection_content({"amount": amount}, state=state, messages=messages),
            },
        ]
    )


def _reply_recovery_messages(state: AgentState) -> list[dict[str, Any]]:
    payload = _compact_recovery_value(reply_recovery_payload_for_model(state))
    return [
        {"role": "system", "content": REPLY_RECOVERY_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


def _compact_recovery_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:240]
    if isinstance(value, dict):
        return {
            str(key): _compact_recovery_value(item, depth=depth + 1)
            for key, item in list(value.items())[:24]
            if item not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [_compact_recovery_value(item, depth=depth + 1) for item in value[-12:]]
    if isinstance(value, str):
        return value[:600]
    return value


async def _chat_json_with_deadline(
    model_client: ModelClient,
    messages: list[dict[str, Any]],
    *,
    tier: str,
    deadline_monotonic: float,
) -> dict[str, Any]:
    try:
        return await model_client.chat_json(
            messages,
            tier=tier,
            temperature=0.0,
            deadline_monotonic=deadline_monotonic,
        )
    except TypeError as exc:
        if "deadline_monotonic" not in str(exc) and "temperature" not in str(exc):
            raise
        return await model_client.chat_json(messages, tier=tier)


def _model_budget_seconds(model_client: ModelClient, name: str, default: float) -> float:
    settings = getattr(model_client, "settings", None)
    value = getattr(settings, name, default) if settings is not None else default
    try:
        return max(0.1, float(value))
    except (TypeError, ValueError):
        return default


def _capped_deadline(node_deadline: float, round_deadline: float | None) -> float:
    return min(node_deadline, round_deadline) if round_deadline is not None else node_deadline


def _reply_retry_messages(messages: list[dict[str, Any]], exc: Exception) -> list[dict[str, Any]]:
    repair_hint = _reply_repair_hint(str(exc))
    retry_instruction = (
        "上一次输出没有通过 JSON schema 校验。"
        f"错误：{type(exc).__name__}: {exc}。"
        f"{repair_hint}"
        "请只重新输出严格 JSON 对象，顶层必须包含非空 reply_messages 数组；"
        "不要解释错误，不要输出 markdown，不要输出内部分析。"
    )
    return [*messages, {"role": "user", "content": retry_instruction}]


def _reply_model_tier(state: AgentState) -> str:
    if _needs_strong_reply_model(state):
        return "strong"
    return "reply"


def _needs_strong_reply_model(state: AgentState) -> bool:
    handoff = state.get("handoff") if isinstance(state.get("handoff"), dict) else {}
    if handoff.get("needed"):
        return True
    for tool in state.get("required_tools") or []:
        if isinstance(tool, dict) and str(tool.get("name") or "") == "professional_assist":
            return True
    structured = _structured_facts(state)
    professional_assist = structured.get("professional_assist")
    if isinstance(professional_assist, dict) and str(professional_assist.get("status") or ""):
        return True
    risk_hold_state = health_risk_hold(state)
    if is_hard_health_risk_hold(risk_hold_state):
        return True
    for key in ("planner_stage", "conversion_stage", "main_blocker", "sub_rule_id", "customer_type"):
        value = str(state.get(key) or "").lower()
        if any(marker in value for marker in ("complaint", "refund", "payment_exception", "risk", "handoff")):
            return True
    return False


def _neutral_final_fallback_messages() -> list[dict[str, Any]]:
    return [{"type": "text", "order": 1, "content": "我在，继续帮您处理。"}]


def _maybe_append_required_store_address(
    messages: list[dict[str, Any]],
    state: AgentState,
    exc: Exception,
) -> list[dict[str, Any]] | None:
    if "store_address_message_required_when_reply_promises_location_card" not in str(exc):
        return None
    store_id = _single_store_fact_id(state)
    if not store_id:
        return None
    if any(isinstance(item, dict) and str(item.get("type") or "") == "store_address" for item in messages):
        return None
    if _store_address_card_conflicts_with_visible_text(messages, state, store_id):
        return None
    return _renumber([*messages, {"type": "store_address", "content": {"store_id": store_id}}])


def _maybe_build_required_payment_collection_fallback(
    state: AgentState,
    exc: Exception,
    *,
    messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]] | None:
    if "payment_collection_required_when_reply_promises_payment_entry" not in str(exc):
        return None
    source_messages = [item for item in (messages or []) if isinstance(item, dict)]
    if not source_messages or _messages_have_payment_collection(source_messages):
        return None
    if is_hard_health_risk_hold(health_risk_hold(state)):
        return None
    if _state_has_paid_deposit_context(state):
        return None
    if not _state_requires_payment_collection(state):
        return None
    context = payment_collection_context(state=state, messages=[])
    if context.get("over_limit"):
        return None
    amount = int(context.get("amount") or 10)
    return _renumber(
        [
            *source_messages,
            {
                "type": "payment_collection",
                "order": len(source_messages) + 1,
                "content": payment_collection_content({"amount": amount}, state=state, messages=source_messages),
            },
        ]
    )


def _messages_have_payment_collection(messages: list[dict[str, Any]]) -> bool:
    return any(isinstance(item, dict) and str(item.get("type") or "") == "payment_collection" for item in messages)


def _state_requires_payment_collection(state: AgentState) -> bool:
    payment_decision = state.get("payment_decision") if isinstance(state.get("payment_decision"), dict) else {}
    decision_action = str(payment_decision.get("action") or "")
    if decision_action in {"send_now", "resend"}:
        return True
    if decision_action in {"none", "explain", "manual_transfer", "after_paid_next_step", "ask_party_size"}:
        return False
    payment_action = str(state.get("payment_action") or "")
    if payment_action in {"none", "manual_transfer", "offer_resend", "explain_existing", "confirm_next_step"}:
        return False
    if payment_action == "send_now":
        return True
    if str(state.get("payment_state") or "") == "customer_claimed_paid":
        return False
    return False


def _state_has_paid_deposit_context(state: AgentState) -> bool:
    """Return whether order or successful screenshot evidence confirms payment."""
    image = state.get("image_info") if isinstance(state.get("image_info"), dict) else {}
    if image.get("image_type") == "payment_proof" and image.get("payment_result") == "success":
        return True
    current_turn_context = state.get("current_turn_context") if isinstance(state.get("current_turn_context"), dict) else {}
    return str(current_turn_context.get("deposit_state") or "") == "deposit_paid"


def _ensure_required_handoff_notice(messages: list[dict[str, Any]], state: AgentState) -> tuple[list[dict[str, Any]], bool]:
    if not messages or _messages_have_handoff_notice(messages) or not _state_requests_handoff_notice(state):
        return messages, False
    reason = _handoff_notice_reason(state)
    return (
        _renumber(
            [
                *messages,
                {
                    "type": "human_handoff_notice",
                    "order": len(messages) + 1,
                    "content": {"handoff_reason": reason},
                },
            ]
        ),
        True,
    )


def _suppress_stale_handoff_notice(messages: list[dict[str, Any]], state: AgentState) -> tuple[list[dict[str, Any]], bool]:
    if _state_has_current_handoff_notice_signal(state):
        return messages, False
    if not messages or not _is_stale_handoff_context(state):
        return messages, False
    changed = False
    filtered: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") in {"human_handoff", "human_handoff_notice"}:
            changed = True
            continue
        if str(item.get("type") or "") == "text" and _is_stale_handoff_status_text(_message_text(item.get("content"))):
            changed = True
            continue
        filtered.append(item)
    if not filtered:
        return messages, False
    return _renumber(filtered), changed


def _is_stale_handoff_context(state: AgentState) -> bool:
    if explicit_professional_assist_reason(state):
        return False
    if is_hard_health_risk_hold(health_risk_hold(state)):
        return False
    current = str(state.get("normalized_content") or state.get("content") or "")
    if _contains_any(current, ("说了三遍", "说了很多遍", "一直问", "还问", "烦死了", "很烦", "不会回答", "强烈不满")):
        return False
    return True


def _is_stale_handoff_status_text(text: str) -> bool:
    compact = "".join(str(text or "").split())
    if not compact:
        return False
    stale_markers = (
        "健康评估正在",
        "健康评估未闭环",
        "专业团队核验",
        "加急处理",
        "结果出来后",
        "内部关注",
    )
    return any(marker in compact for marker in stale_markers)


def _message_text(content: Any) -> str:
    if isinstance(content, dict):
        for key in ("text", "handoff_reason", "reason", "url", "store_id", "amount"):
            value = content.get(key)
            if value not in (None, ""):
                return str(value)
        return ""
    return str(content or "")


def _messages_have_handoff_notice(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(item, dict) and str(item.get("type") or "") in {"human_handoff", "human_handoff_notice"}
        for item in messages
    )


def _state_requests_handoff_notice(state: AgentState) -> bool:
    handoff = state.get("handoff") if isinstance(state.get("handoff"), dict) else {}
    if bool(handoff.get("needed")):
        return True
    return _state_has_current_handoff_notice_signal(state)


def _state_has_current_handoff_notice_signal(state: AgentState) -> bool:
    required_tools = state.get("required_tools") if isinstance(state.get("required_tools"), list) else []
    if any(isinstance(item, dict) and str(item.get("name") or "") == "professional_assist" for item in required_tools):
        return True
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    assist = tool_results.get("professional_assist") if isinstance(tool_results.get("professional_assist"), dict) else {}
    if str(assist.get("status") or "") == "requested":
        return True
    structured = _structured_facts(state)
    assist_fact = structured.get("professional_assist") if isinstance(structured.get("professional_assist"), dict) else {}
    return str(assist_fact.get("status") or "") == "requested"


def _handoff_notice_reason(state: AgentState) -> str:
    risk_hold = health_risk_hold(state)
    if is_hard_health_risk_hold(risk_hold):
        reason = str(risk_hold.get("reason") or "").strip()
        if reason:
            return reason[:180]
    candidates: list[str] = []
    structured = _structured_facts(state)
    assist_fact = structured.get("professional_assist") if isinstance(structured.get("professional_assist"), dict) else {}
    candidates.append(str(assist_fact.get("reason") or ""))
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    assist = tool_results.get("professional_assist") if isinstance(tool_results.get("professional_assist"), dict) else {}
    candidates.append(str(assist.get("reason") or ""))
    handoff = state.get("handoff") if isinstance(state.get("handoff"), dict) else {}
    candidates.append(str(handoff.get("reason") or ""))
    for item in state.get("required_tools") or []:
        if isinstance(item, dict) and str(item.get("name") or "") == "professional_assist":
            candidates.append(str(item.get("reason") or item.get("purpose") or ""))
    for value in candidates:
        reason = " ".join(value.split())
        if reason:
            return reason[:180]
    return "高风险或人工诉求，需要内部关注"


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _structured_facts(state: AgentState) -> dict[str, Any]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    return structured if isinstance(structured, dict) else {}


def _single_store_fact_id(state: AgentState) -> str:
    structured = _structured_facts(state)
    store_facts = [item for item in structured.get("store_facts") or [] if isinstance(item, dict)]
    ids = list(
        dict.fromkeys(
            str(item.get("store_id") or item.get("id") or "").strip()
            for item in store_facts
            if str(item.get("store_id") or item.get("id") or "").strip()
        )
    )
    return ids[0] if len(ids) == 1 else ""


def _store_address_card_conflicts_with_visible_text(
    messages: list[dict[str, Any]],
    state: AgentState,
    store_id: str,
) -> bool:
    text = _combined_visible_text(messages)
    if not text:
        return False
    target = _store_record_for_id(state, store_id)
    if not target:
        return False
    target_names = _store_record_names(target)
    target_tokens = {_compact_text(item) for item in [*target_names, *_store_record_regions(target)] if _compact_text(item)}
    for record in _known_store_records(state):
        if str(record.get("store_id") or record.get("id") or "").strip() == store_id:
            continue
        for name in _store_record_names(record):
            if name and name in text and name not in target_names:
                return True
        other_region_hit = any(region_mentioned_in_text(region, text) for region in _store_record_regions(record))
        if other_region_hit and target_tokens and not any(token and token in _compact_text(text) for token in target_tokens):
            return True
    for name in KNOWN_STORE_NAMES:
        store_name = str(name or "").strip()
        if store_name and store_name in text and store_name not in target_names:
            return True
    return False


def _combined_visible_text(messages: list[dict[str, Any]]) -> str:
    return " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )


def _store_record_for_id(state: AgentState, store_id: str) -> dict[str, Any]:
    for record in _known_store_records(state):
        if str(record.get("store_id") or record.get("id") or "").strip() == store_id:
            return record
    return {}


def _known_store_records(state: AgentState) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    structured = _structured_facts(state)
    for key in ("store_facts", "appointment_facts"):
        for item in structured.get(key) or []:
            if isinstance(item, dict):
                records.append(item)
    recommended = structured.get("recommended_store")
    if isinstance(recommended, dict):
        records.append(recommended)
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    for item in knowledge.get("stores") or []:
        if isinstance(item, dict):
            records.append(item)
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    if basic:
        records.append(
            {
                "store_id": basic.get("preferred_store_id") or basic.get("store_id"),
                "store_name": basic.get("preferred_store_name") or basic.get("store_name"),
                "city": basic.get("city") or basic.get("current_city"),
                "district": basic.get("district") or basic.get("area_or_landmark") or basic.get("region"),
            }
        )
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in records:
        store_id = str(item.get("store_id") or item.get("id") or "").strip()
        store_name = str(item.get("store_name") or item.get("name") or "").strip()
        key = (store_id, store_name)
        if not (store_id or store_name) or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _store_record_names(record: dict[str, Any]) -> list[str]:
    values = [str(record.get(key) or "").strip() for key in ("store_name", "name")]
    return [item for item in dict.fromkeys(values) if item]


def _store_record_regions(record: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("province", "city", "district", "area", "region", "address_region"):
        value = str(record.get(key) or "").strip()
        if value:
            values.append(value)
    address = str(record.get("address") or record.get("store_address") or "").strip()
    if address:
        values.extend(_known_region_tokens_in_text(address))
    return [item for item in dict.fromkeys(values) if len(_compact_text(item)) >= 2]


def _known_region_tokens_in_text(text: str) -> list[str]:
    tokens: list[str] = []
    for suffix in ("省", "市", "区", "县", "镇", "乡", "旗", "州", "盟", "新区"):
        for part in str(text or "").replace("，", " ").replace(",", " ").split():
            if suffix in part:
                index = part.find(suffix)
                token = part[: index + len(suffix)]
                if token:
                    tokens.append(token)
    return tokens


def _compact_text(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def _reply_repair_hint(error: str) -> str:
    if "precision_reply_passive_mainline_closure" in error:
        return "精准支线问题已经回答到点，但收尾不能等待客户许可。请删除“如果您想/如果您愿意/我可以继续/要不要/是否需要”等表达，直接落一个主线动作：问城市或区域、主动接活动、发案例、推进预约金或登记。没有真实图片或门店卡事实时，直接问城市/区或接活动，不要承诺稍后发。"
    if "precision_reply_missing_mainline_action" in error:
        return "精准支线问题不能只答疑后停住。请保留当前问题的正面回答，再补一条明确主线动作句：问城市或区域、主动接活动名额、发案例、推进预约金或登记到店时间。动作句要具体、像微信销售，不要写“继续处理/安排下一步/如果您想”。"
    if "precision_reply_weak_one_session_confidence" in error:
        return "客户问一次效果时，先给正向信心：大多数客户一次能看到明显改善方向。不要用“不是完全没变化”这类弱安慰；再说明具体程度看斑点深浅和时间，到店会做原相机对比，最后接一个主线动作。"
    if "health_online_symptom_question_not_allowed" in error:
        return "不要在线追问客户症状、出现频率或用药情况。健康、过敏或孕期只正面承接并引导到店专业检测；严重不适收原门店、项目和时间。"
    if "health_specific_care_advice_not_allowed" in error:
        return "不要给热敷、冷敷、去角质、酸类、停用护肤品等具体护理清单。严重不适只说停止继续刺激、联系原门店；明显紧急时及时线下就医。"
    if "pregnancy_deferral_claim_not_allowed" in error:
        return "不要直接判定只能等孕期结束、产后或哺乳期结束再来。只说明需要到店专业检测评估，确认适合后再安排。"
    if "payment_collection_blocked_by_health_risk_hold" in error:
        return "客户近期有健康/过敏高风险，未到店检测确认适配前不要输出 payment_collection；只确认检测、门店或时间。"
    if "payment_collection_blocked_by_payment_action" in error:
        return "planner 的 payment_action 表示本轮不直接发预约金入口时，不要输出 payment_collection，也不要在 text 里说马上发入口；改成自然承接、询问是否需要重发或推进下一步。"
    if "payment_collection_blocked_by_precision_qa_boundary" in error:
        return "当前是精准问答边界：不支持项目不能发预约金卡；手脸/两个部位问题不能把部位当同行人数发卡。先把当前边界答清，再自然回到淡斑主线，不要承诺本轮发入口。"
    if "payment_collection_requires_activity_intro" in error:
        return "客户还没有看到完整活动报价/预约金规则时，不要输出 payment_collection，也不要说入口或卡片已发，不要写“付好截图发我”。先用自然话术补活动价268、每位10元预约金到店抵扣、未做或不满意可退，再用“您确认按这个活动参加的话，我马上给您发小程序收款卡”这类封闭式动作承接。"
    if "payment_collection_required" in error:
        return "如果 payment_action=send_now、文本承诺发送预约金入口或 next_step=send_deposit，必须同时输出 payment_collection；否则删除发入口承诺并调整回复节奏。"
    if "payment_collection_amount_text_mismatch" in error:
        return "预约金卡片金额必须和文本一致；同行按每位10元锁活动名额，2位说一共20元，3位说一共30元，4位说一共40元。"
    if "offer_total_tail_amount_conflict" in error:
        return "活动总价是268元。10元预约金计入总价并到店抵扣，客户实际做时再付剩余258元；不能把258说成最终总价、全部费用或一共只付258元。"
    if "payment_participant_count_confirm_required" in error:
        return "客户同行人数超过4位时不要发送 payment_collection；改成 text 确认一共几位到店，或说明多人同行先由门店承接确认。"
    if "human_handoff_notice" in error:
        return "需要内部关注时，先用客户可见 text 正面回答和引导到店检测或核对事实，再追加 human_handoff_notice；text 不要说转人工、转同事、专业同事、稍等一下哈。"
    if "ambiguous_deposit_refund_wording" in error or "legacy_deposit_refund_policy" in error:
        return "预约金口径统一为“每位10元锁活动名额，到店抵扣；未做或不满意可退，实际按付款记录核对”。不要承诺自动退款、即时到账、具体退款金额或处理时效。"
    if "case_context_must_not_use_activity_intro_image" in error:
        return "本轮客户在问效果或案例，且已有 case_facts 案例图片事实。必须回答效果顾虑，并且如输出 image，只能使用 case_facts 里的 image_url；不要输出活动宣传图。"
    if "case_image_required_for_effect_turn" in error:
        return "本轮客户在问效果或案例，且已有 case_facts 案例图片事实。必须先用 text 肯定效果方向，再追加 1 条 case_facts.image_url 的 image。"
    if "effect_reply_confidence_order_required" in error:
        return "效果疑问要先肯定对应需求可以做、大多数客户改善反馈不错，再补到店检测更准确；不要第一句就说因人而异、不保证或具体看个人情况。"
    if "effect_absolute_safety_claim" in error:
        return "效果和安全顾虑可以积极承接，允许‘一般不会反黑’这类非绝对信心表达，但不要说绝对不会、保证不会、100%不会、一定有效、保证效果或包效果；同时要给多数客户反馈正常/改善不错的信心，再说到店检测评估、按皮肤状态操作、适合再安排。"
    if "reply_too_similar" in error:
        return "客户在重复追问同类问题，请换一个角度回答，不要复用上一轮核心话术。"
    if "two_text_required" in error:
        return "这条回复同时包含回答和下一步推进，请改成两条短 text：第一条只回答问题，第二条只轻推一个动作。"
    if "parking_fact_required" in error:
        return "没有停车工具事实时，不要说有停车场或可以停车，只能说需要核对或询问门店/区域。"
    if "business_hours_fact_required" in error:
        return "没有营业时间工具事实时，不要输出具体营业时间。"
    if "store_address_fact_required" in error:
        return "没有门店详情事实时，不要输出具体地址。"
    if "unsupported_store_address_message" in error:
        return "store_address 卡片的 store_id 必须来自本轮门店工具事实或请求里明确确认的门店 ID；没有匹配门店事实时，不要输出 store_address，只能用文字说明暂时没查到并继续确认城市、区域或门店。"
    if "complete_store_listing_cards_required" in error:
        return "本轮门店工具已经返回 purpose=existence 的完整 1 至 3 家真实门店。请保留自然说明，并为 tool_facts.store_facts 中每个 store_id 各输出一条 store_address；不能只用编号或文字列出门店来代替卡片。"
    if "recommended_store_card_required" in error:
        return "本轮 distance_calculate 已产生唯一 recommended_store。请基于该事实自然承接，并追加一条 content.store_id 等于 recommended_store.store_id 的 store_address；不要只在文字里承诺稍后发卡。"
    if "store_address_message_required_when_reply_promises_location_card" in error:
        return "你已经在文本里承诺发地址、位置或让客户点开导航；如果本轮有门店事实，必须追加对应 store_address 卡片。若不想发卡，就删除“我发您/点开导航/位置卡”等承诺。"
    if "distance_value_not_customer_visible" in error:
        return "distance_calculate 只用于内部排序门店。客户可见回复只说优先哪家或哪家更近一些，不要输出几公里、几分钟、车程或步行时长。"
    if "distance_fact_required" in error:
        return "没有 distance_calculate 排序事实时，不要输出最近、离您最近、较近、就近等距离排序表达。只回答门店名、地址、停车或营业时间等已有门店事实，再问客户哪个区域/哪家更方便。"
    if "nearby_store_claim_without_location_fact" in error:
        return "没有客户定位、门店工具或距离排序事实时，不要说“附近门店/离您近”。请改成“我给您看下门店/对下城市或区域”，不要编距离感。"
    if "manual_transfer_missing_screenshot_registration" in error:
        return "客户明确选择转账时，不要发 payment_collection。必须说清楚“转好截图发我，我给您登记/备注”，然后只补一个必要主线字段。"
    if "available_time_fact_required" in error:
        return "available_time 工具失败、超时或没有返回可用 slots 时，不要说有空、可以约、有时间或有名额；只能说明暂时没查到实时档期，并继续确认门店/时间或让门店核对。如果本轮是效果/案例图场景且已有 case_facts，请删除所有旧历史里的今天/明天/几点、几位、预约金、锁名额表达，改成“多数可以看改善 + 发送 case_facts.image_url + 到店专业检测更准”。"
    if "appointment_confirmation_fact_required" in error:
        return "available_time 只表示目标时段目前可选，不代表已经留位、改约或安排成功。普通预约可问“这个时间方便吗”；已有旧预约的改约场景只输出一条：“这个时间目前可以，您确认要改到这个时间吗？”。删除其他“继续核对/先按这个时间/帮您改过去/帮您留/锁定/安排/记上/预约成功”表达。"
    if "too_many_appointment_time_options" in error:
        return "档期回复最多只能给 1 个推荐时间和 1 个备选时间。请基于 recommended_slot 和 backup_slots 重写，不要列完整时间表。"
    if "unfinished_appointment_lookup_promise" in error:
        return "没有真实 available_time 档期事实时，不要说“查档期/核对档期/看档期/可约时间”。如果本轮已有门店事实，只回答门店位置并引导客户选择区域或门店；如果缺门店或具体时间，只问客户补一个关键字段。"
    if "unfinished_tool_promise_after_tool_execution" in error:
        return "本轮工具已经执行完，不能再说“马上查、帮您查一下、帮您找案例、稍后给您”。请直接基于已有事实回答；如果事实不足，只问客户补一个关键字段，或说明当前没有可发事实。"
    return ""


def _filter_unsupported_images(
    messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[Any],
) -> list[dict[str, Any]]:
    allowed_urls = _case_image_urls(state)
    filtered: list[dict[str, Any]] = []
    removed_urls: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "image":
            filtered.append(item)
            continue
        url = _message_url(item.get("content"))
        if url and url in allowed_urls:
            filtered.append(item)
        else:
            removed_urls.append(url or "")
    if removed_urls:
        warnings.append(
            {
                "node": "synthesize_reply",
                "message": "unsupported_image_removed",
                "detail": {"removed_urls": removed_urls},
            }
        )
    return _renumber(filtered)


def _case_image_urls(state: AgentState) -> set[str]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    urls: set[str] = set()
    for item in structured.get("case_facts") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("image_url") or "").strip()
        if url:
            urls.add(url)
    activity_url = activity_intro_image_url(state)
    if activity_url:
        urls.add(activity_url)
    return urls


def _message_url(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("url") or content.get("image_url") or "").strip()
    return str(content or "").strip()


def _renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(messages, start=1):
        result.append({**item, "order": index})
    return result


def _normalize_planner_reply_messages(value: Any, *, state: AgentState | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    messages: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        message_type = str(item.get("type") or "text").strip()
        content = item.get("content")
        if message_type == "text":
            if isinstance(content, dict):
                text = str(content.get("text") or "").strip()
            else:
                text = str(content or item.get("text") or "").strip()
            if text:
                messages.append({"type": "text", "order": int(item.get("order") or index), "content": {"text": text}})
            continue
        if message_type == "payment_collection":
            messages.append(
                {
                    "type": "payment_collection",
                    "order": int(item.get("order") or index),
                    "content": payment_collection_content(content, state=state, messages=messages),
                }
            )
            continue
        if message_type in {"human_handoff", "human_handoff_notice"}:
            reason = str(content.get("handoff_reason") if isinstance(content, dict) else content or "").strip()
            if reason:
                messages.append({"type": "human_handoff_notice", "order": int(item.get("order") or index), "content": {"handoff_reason": reason}})
            continue
        if message_type == "store_address":
            store_id = str(content.get("store_id") if isinstance(content, dict) else content or "").strip()
            if store_id:
                messages.append({"type": "store_address", "order": int(item.get("order") or index), "content": {"store_id": store_id}})
    messages, _ = _dedupe_payment_collection_messages(messages)
    return _normalize_payment_amount_text_messages(messages)


def _dedupe_payment_collection_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    output: list[dict[str, Any]] = []
    seen_payment = False
    changed = False
    for item in messages:
        if not isinstance(item, dict):
            changed = True
            continue
        if str(item.get("type") or "") == "payment_collection":
            if seen_payment:
                changed = True
                continue
            seen_payment = True
        output.append(item)
    return _renumber(output), changed


def _normalize_payment_amount_text_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    amount = _first_payment_collection_amount(messages)
    if amount <= 10:
        return _renumber(messages)
    output: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "text":
            output.append(item)
            continue
        content = item.get("content")
        if isinstance(content, dict):
            text = normalize_payment_amount_text(str(content.get("text") or ""), amount)
            output.append({**item, "content": {**content, "text": text}})
        else:
            output.append({**item, "content": normalize_payment_amount_text(str(content or ""), amount)})
    return _renumber(output)


def _preserve_planner_store_address_actions(
    messages: list[dict[str, Any]],
    state: AgentState,
) -> tuple[list[dict[str, Any]], bool]:
    """Keep Planner-approved, fact-validated store cards from being lost by Reply."""
    if str(state.get("planner_decision") or "") != "direct_reply":
        return messages, False
    planner_messages = _normalize_planner_reply_messages(state.get("planner_reply_messages"), state=state)
    planned_cards = [item for item in planner_messages if str(item.get("type") or "") == "store_address"]
    if not planned_cards:
        return messages, False

    existing_ids = {
        _store_id_from_message(item)
        for item in messages
        if str(item.get("type") or "") == "store_address"
    }
    additions = [item for item in planned_cards if _store_id_from_message(item) and _store_id_from_message(item) not in existing_ids]
    if not additions:
        return messages, False

    candidate = _renumber([*messages, *additions])
    try:
        validate_reply_consistency(candidate, state)
    except Exception:
        return messages, False
    return candidate, True


def _store_id_from_message(message: dict[str, Any]) -> str:
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    return str(content.get("store_id") or "").strip()


def _first_payment_collection_amount(messages: list[dict[str, Any]]) -> int:
    for item in messages:
        if not isinstance(item, dict) or str(item.get("type") or "") != "payment_collection":
            continue
        content = item.get("content")
        if not isinstance(content, dict):
            continue
        try:
            amount = int(float(str(content.get("amount") or "").strip()))
        except (TypeError, ValueError):
            return 10
        return amount
    return 10


def _schedule_profile_event_background(
    schedule_background_task: Callable[[AgentState], Any] | None,
    state: AgentState,
) -> None:
    if not schedule_background_task:
        return
    try:
        schedule_background_task(state)
    except RuntimeError:
        return
