from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.graph.nodes.sent_message_summary import sent_message_summary_for_model
from app.services.run_observability_summary import build_run_observability, trace_wall_duration_ms


_INTENT_LABELS = {
    "fact_inquiry": "咨询事实",
    "blocker_expression": "表达卡点",
    "transaction_progress": "推进成交",
    "information_submission": "提交信息",
    "defer": "暂缓",
    "explicit_exit": "明确退出",
    "normal_exchange": "普通交流",
}
_EMOTION_LABELS = {
    "enthusiastic": "热情",
    "curious": "好奇",
    "neutral": "中性",
    "hesitant": "犹豫",
    "cold": "冷淡",
    "defensive": "防备",
    "impatient": "不耐烦",
    "angry": "愤怒",
}
_TASK_LABELS = {
    "answer_current_question": "回答当前问题",
    "resolve_blocker": "处理当前卡点",
    "transaction_progression": "执行成交动作",
    "closing_progression": "推进逼单策略",
    "normal_conversation": "普通交流",
    "risk": "风险处理",
    "human_takeover": "人工接管",
    "hard_stop": "停止营销",
    "transaction_terminal": "交易完成承接",
}
_CLOSING_LABELS = {
    "none": "未进入",
    "enter": "进入逼单",
    "advance": "推进逼单",
    "pause": "暂停逼单",
    "fallback": "降级承接",
    "complete": "结束逼单",
}


def build_v3_run_observability(state: dict[str, Any]) -> dict[str, Any]:
    """Project V3 runtime state into a stable, human-facing audit summary.

    This module only joins structured facts and model-owned audit fields. It
    never infers customer intent from visible reply text.
    """

    context = _dict(state.get("request_context"))
    interface_version = _text(
        context.get("interface_version") or context.get("api_version")
    ).lower()
    if interface_version != "v3":
        return {}

    route = _dict(state.get("semantic_route"))
    retrieval_goal = _dict(route.get("current_intent"))
    checkpoint = _dict(route.get("checkpoint"))
    sequence_match = _dict(route.get("sequence_match"))
    store_query = _dict(route.get("store_query"))
    recall = _dict(state.get("sales_recall"))
    selector = _dict(recall.get("selector"))
    knowledge_use = _dict(state.get("reply_knowledge_use"))
    content_metrics = _dict(state.get("content_selection_metrics"))
    message_refs = _message_ref_map(state)
    conversation = _conversation_view(state)
    store_summary = _store_summary(state, store_query=store_query)
    compact_model_usage = _compact_model_usage(state.get("trace"))

    matched_sequences = _matched_sequences(
        recall,
        sequence_match=sequence_match,
        adopted=knowledge_use,
    )
    script_candidates = _script_candidates(
        recall,
        adopted_script_ids={
            _text(item)
            for item in knowledge_use.get("selected_script_ids") or []
            if _text(item)
        },
        delivered_content_ids={
            _text(item)
            for item in content_metrics.get("delivered_ids") or []
            if _text(item)
        },
    )
    errors = _dict_list(state.get("errors"))[:8]
    warnings = _dict_list(state.get("warnings"))[:8]
    fallback_source = _text(state.get("fallback_source"))

    return {
        "schema_version": "v3_run_observability_v1",
        "overview": {
            "interface_version": "v3",
            "reply_chain_mode": _text(context.get("reply_chain_mode")),
            "status": "failed" if errors else "completed",
            "fallback_used": bool(fallback_source),
            "fallback_source": fallback_source,
            "knowledge_matched": bool(matched_sequences or script_candidates),
            "knowledge_adopted": bool(
                knowledge_use.get("sequence_id")
                or knowledge_use.get("selected_script_ids")
            ),
            "store_called": bool(store_summary.get("called")),
        },
        "customer_input": {
            "content": _text(state.get("content")),
            "message_type": _text(context.get("msgtype") or context.get("message_type") or "text"),
            "conversation_count": len(conversation),
            "conversation": conversation,
        },
        "checkpoint_decision": {
            "retrieval_goal": {
                "summary": _text(retrieval_goal.get("summary")),
                "evidence": [
                    {"ref": ref, "quote": message_refs.get(ref, "")}
                    for ref in _string_list(retrieval_goal.get("evidence_refs"))
                ],
            },
            "classification_status": _text(route.get("classification_status")),
            "primary": {
                "type_id": _integer(checkpoint.get("primary_type_id")),
                "code": _text(checkpoint.get("primary_code")),
                "name": _text(checkpoint.get("primary_type_name")),
                "tag_id": _integer(checkpoint.get("primary_tag_id")),
                "tag_name": _text(checkpoint.get("primary_tag_name")),
            },
            "secondary": {
                "type_id": _integer(checkpoint.get("secondary_type_id")),
                "code": _text(checkpoint.get("secondary_code")),
                "name": _text(checkpoint.get("secondary_type_name")),
                "tag_id": _integer(checkpoint.get("secondary_tag_id")),
                "tag_name": _text(checkpoint.get("secondary_tag_name")),
            },
            "evidence": [
                {
                    "ref": ref,
                    "quote": message_refs.get(ref, ""),
                }
                for ref in _string_list(checkpoint.get("evidence_refs"))
            ],
            "reason": _text(checkpoint.get("reason")),
        },
        "knowledge_match": {
            "execution": {
                "router_invoked": bool(route),
                "router_status": _text(route.get("status") or ("completed" if route else "not_run")),
                "router_phase": _text(route.get("phase")),
                "sequence_index_count": len(recall.get("sequence_candidates") or []),
                "knowledge_status": _text(recall.get("status") or ("completed" if recall else "not_run")),
                "script_lookup_invoked": bool(recall.get("script_query_results")),
                "script_lookup_count": len(recall.get("script_query_results") or []),
                "selector_invoked": bool(selector),
            },
            "sequence_reason": _text(sequence_match.get("reason")),
            "selector": {
                "status": _text(selector.get("status")),
                "reason": _text(selector.get("reason")),
                "selected_groups": _dict_list(selector.get("selected_groups")),
                "excluded_groups": _dict_list(selector.get("excluded_groups")),
            },
            "matched_sequences": matched_sequences,
            "excluded_sequences": [
                {
                    "sequence_id": sequence_id,
                    "reason": _text(
                        _dict(sequence_match.get("exclusion_reasons")).get(sequence_id)
                    ),
                }
                for sequence_id in _string_list(
                    sequence_match.get("excluded_sequence_ids")
                )
            ],
            "script_query_count": len(recall.get("script_query_results") or []),
            "script_candidate_count": int(
                recall.get("candidate_count") or len(script_candidates)
            ),
            "script_candidates": script_candidates,
            "adopted": {
                "sequence_id": _text(knowledge_use.get("sequence_id")),
                "sequence_name": _text(knowledge_use.get("sequence_name")),
                "step_id": _text(knowledge_use.get("step_id")),
                "checkpoint_code": _text(knowledge_use.get("checkpoint_code")),
                "action_code": _text(knowledge_use.get("action_code")),
                "script_ids": _string_list(
                    knowledge_use.get("selected_script_ids")
                ),
                "reason": _text(knowledge_use.get("reason")),
            },
            "delivered_content_ids": _string_list(
                content_metrics.get("delivered_ids")
            ),
        },
        "store_workflow": store_summary,
        "reply_result": {
            "messages": _dict_list(state.get("reply_messages")),
            "source": _text(state.get("reply_source")),
            "action": _text(state.get("reply_action") or "none"),
            "action_reason": _text(state.get("reply_action_reason")),
            "sales_judgment": _dict(state.get("reply_sales_judgment")),
            "selected_content_ids": _string_list(
                state.get("selected_content_ids")
            ),
            "content_decisions": _dict_list(
                state.get("reply_content_decisions")
            ),
        },
        "delivery": _initial_delivery_summary(state),
        "model_usage": compact_model_usage,
        "strategy_callback": _dict(state.get("strategy_data_callback")),
        "timing": _timing_summary(state.get("trace")),
        "failures": {
            "errors": errors,
            "warnings": warnings,
            "recovery_attempts": _dict_list(state.get("recovery_attempts"))[:8],
        },
    }


def enrich_v3_run_observability(
    output_snapshot: dict[str, Any],
    *,
    dispatch: dict[str, Any] | None = None,
) -> None:
    observability = _dict(output_snapshot.get("observability_v3"))
    if not observability:
        return
    observability["strategy_callback"] = _dict(
        output_snapshot.get("strategy_data_callback")
    )
    if dispatch:
        observability["delivery"] = {
            "mode": "async_callback",
            "status": _text(dispatch.get("status")),
            "callback_expected": True,
            "callback_reason": "最终回复由异步发送链路交付，平台逐条回执已更新到本记录。",
            "dispatch_id": _text(dispatch.get("id")),
            "expected_count": _integer(dispatch.get("expected_count")),
            "succeeded_count": _integer(dispatch.get("succeeded_count")),
            "failed_count": _integer(dispatch.get("failed_count")),
            "platform_request_id": _text(dispatch.get("platform_request_id")),
            "error_code": _text(dispatch.get("error_code")),
            "error_message": _text(dispatch.get("error_message")),
            "messages": _dict_list(dispatch.get("reply_messages")),
            "items": [
                {
                    "message_index": _integer(item.get("message_index")),
                    "message_type": _text(item.get("message_type")),
                    "status": _text(item.get("status")),
                    "platform_message_id": _text(item.get("platform_message_id")),
                    "error_code": _text(item.get("error_code")),
                    "error_message": _text(item.get("error_message")),
                }
                for item in _dict_list(dispatch.get("items"))
            ],
        }
    output_snapshot["observability_v3"] = observability


def enrich_admin_observability_v3(
    view: dict[str, Any],
    detail: dict[str, Any],
    *,
    trace_retention_days: int = 14,
) -> dict[str, Any]:
    """Add a product-readable V3 decision and workflow view to run observability."""

    run = _dict(detail.get("run"))
    output = _dict(run.get("output_snapshot"))
    stored = _dict(output.get("observability_v3"))
    usage = _dict(detail.get("strategy_usage_event"))
    nodes = _dict_list(view.get("nodes"))
    intent = _dict(output.get("realtime_intent"))
    emotion = _dict(output.get("emotion_decision"))
    closing = _dict(output.get("closing_decision"))
    cardpoint = _dict(output.get("cardpoint_decision"))
    primary_task = _dict(output.get("primary_task"))
    reference_map = _stored_reference_map(stored)
    compact_model_usage = _dict_list(stored.get("model_usage"))
    token_usage = _dict(run.get("token_usage"))

    intent_code = _text(intent.get("type") or usage.get("intent_code"))
    emotion_code = _text(emotion.get("label") or usage.get("emotion_before"))
    closing_action = _text(closing.get("action") or usage.get("closing_action") or "none")
    knowledge = _dict(stored.get("knowledge_match"))
    checkpoint = _dict(stored.get("checkpoint_decision"))
    trace_status = _trace_availability(run, nodes, trace_retention_days=trace_retention_days)

    view["contract_version"] = "run_observability_v2"
    summary = _dict(view.get("summary"))
    if compact_model_usage:
        summary["model_names"] = list(
            dict.fromkeys(
                _text(item.get("model"))
                for item in compact_model_usage
                if _text(item.get("model"))
            )
        )
        summary["model_call_count"] = len(compact_model_usage)
        summary["model_retry_count"] = sum(
            max(0, _integer(item.get("attempts")) - 1) for item in compact_model_usage
        )
        summary["model_fallback_count"] = sum(
            1 for item in compact_model_usage if bool(item.get("fallback_used"))
        )
    if not _integer(summary.get("total_tokens")):
        summary["total_tokens"] = _integer(token_usage.get("total_tokens"))
    view["summary"] = summary
    view["decision_summary"] = {
        "available": bool(intent or emotion or closing or usage),
        "decision_status": _text(output.get("decision_status") or usage.get("decision_status")),
        "decision_reasons": _string_list(output.get("decision_reasons") or usage.get("decision_reasons")),
        "primary_task": {
            "code": _text(primary_task.get("type")),
            "name": _TASK_LABELS.get(_text(primary_task.get("type")), _text(primary_task.get("type"))),
            "goal": _text(primary_task.get("goal")),
            "basis": _string_list(primary_task.get("basis")),
        },
        "intent": {
            "code": intent_code,
            "name": _INTENT_LABELS.get(intent_code, intent_code),
            "confidence": _text(intent.get("confidence") or usage.get("intent_confidence")),
            "secondary_codes": _string_list(intent.get("secondary_types") or usage.get("intent_secondary"))[:3],
            "secondary_names": [
                _INTENT_LABELS.get(item, item)
                for item in _string_list(intent.get("secondary_types") or usage.get("intent_secondary"))[:3]
            ],
            "evidence": _decision_evidence(intent, reference_map),
            "basis": _string_list(intent.get("basis")),
        },
        "emotion": {
            "code": emotion_code,
            "name": _EMOTION_LABELS.get(emotion_code, emotion_code),
            "confidence": _text(emotion.get("confidence") or usage.get("emotion_confidence")),
            "pressure": _text(emotion.get("pressure") or usage.get("emotion_pressure")),
            "flow_action": _text(emotion.get("flow_action") or usage.get("emotion_flow_action")),
            "evidence": _decision_evidence(emotion, reference_map),
            "basis": _string_list(emotion.get("basis")),
        },
        "closing": {
            "action": closing_action,
            "action_name": _CLOSING_LABELS.get(closing_action, closing_action),
            "customer_state": _text(closing.get("customer_state") or usage.get("closing_customer_state")),
            "pressure": _text(closing.get("pressure") or usage.get("closing_pressure")),
            "trigger": _text(closing.get("trigger") or usage.get("closing_trigger")),
            "rule_id": _text(usage.get("closing_primary_rule_id") or _first(closing.get("rule_ids"))),
            "rule_name": _text(closing.get("primary_rule_name") or usage.get("closing_primary_rule_name")),
            "sequence_key": _text(closing.get("sequence_key") or usage.get("closing_strategy_code")),
            "sequence_name": _text(closing.get("sequence_name") or usage.get("closing_sequence_name")),
            "node_key": _text(closing.get("node_key") or usage.get("closing_node_key")),
            "node_name": _text(closing.get("node_name") or usage.get("closing_node_name")),
            "rule_match_status": _text(closing.get("rule_match_status") or usage.get("closing_rule_match_status")),
            "constraint_status": _text(closing.get("constraint_status") or usage.get("closing_constraint_status")),
            "constraint_reasons": _string_list(
                closing.get("constraint_reasons") or usage.get("closing_constraint_reasons")
            ),
            "evidence": _decision_evidence(closing, reference_map),
            "basis": _string_list(closing.get("basis")),
        },
    }
    view["checkpoint_summary"] = {
        "router": checkpoint,
        "final": {
            "available": bool(cardpoint),
            "category_key": _text(cardpoint.get("category_key") or usage.get("cardpoint_category_key")),
            "scenario": _text(cardpoint.get("scenario_query")),
            "state": _text(cardpoint.get("state") or usage.get("cardpoint_state")),
            "confidence": _text(cardpoint.get("confidence")),
            "tactic_tags": _string_list(cardpoint.get("tactic_tags")),
            "evidence": _decision_evidence(cardpoint, reference_map),
            "basis": _string_list(cardpoint.get("basis")),
        },
    }
    view["knowledge_match"] = {
        **knowledge,
        "available": bool(knowledge),
        "adoption_explanation": _knowledge_adoption_explanation(knowledge, checkpoint),
    }
    view["store_workflow"] = _dict(stored.get("store_workflow"))
    view["workflow_nodes"] = _workflow_nodes(
        run=run,
        nodes=nodes,
        stored=stored,
        decision_status=_text(view["decision_summary"].get("decision_status")),
        trace_status=trace_status,
        delivery=_dict(view.get("delivery")),
    )
    view["data_availability"] = {
        "business_summary": "available" if stored or view["decision_summary"]["available"] else "not_recorded",
        "strategy_usage_event": "available" if usage else "not_recorded",
        "node_traces": trace_status,
        "raw_detail": "available" if nodes else trace_status,
        "trace_retention_days": max(1, int(trace_retention_days or 14)),
        "snapshot_compacted": True,
        "notice": "节点输入输出来自现有留存，已脱敏且可能截断；历史缺失不代表业务结果为零。",
    }
    return view


def compact_admin_run_detail(run: dict[str, Any]) -> dict[str, Any]:
    """Return the fields required by the default log UI without raw history/debug payloads."""

    input_snapshot = _dict(run.get("input_snapshot"))
    request_context = _dict(input_snapshot.get("request_context"))
    output_snapshot = _dict(run.get("output_snapshot"))
    return {
        key: value
        for key, value in {
            **run,
            "input_snapshot": {
                "content": _text(input_snapshot.get("content")),
                "customer_id": _text(input_snapshot.get("customer_id")),
                "corp_id": _text(input_snapshot.get("corp_id")),
                "wechat": _text(input_snapshot.get("wechat")),
                "external_userid": _text(input_snapshot.get("external_userid")),
                "request_context": {
                    key: request_context.get(key)
                    for key in (
                        "interface_version",
                        "api_version",
                        "reply_chain_mode",
                        "msgtype",
                        "message_type",
                    )
                    if request_context.get(key) not in (None, "")
                },
            },
            "output_snapshot": {
                key: output_snapshot.get(key)
                for key in (
                    "reply_messages",
                    "interface_version",
                    "reply_chain_mode",
                    "runtime_status",
                    "runtime_phase",
                    "runtime_started_at",
                    "runtime_finished_at",
                )
                if output_snapshot.get(key) not in (None, "")
            },
        }.items()
        if key not in {"raw_log", "node_traces", "strategy_usage_event"}
    }


def _stored_reference_map(stored: dict[str, Any]) -> dict[str, str]:
    customer_input = _dict(stored.get("customer_input"))
    output = {"current_message": _text(customer_input.get("content"))}
    for item in _dict_list(customer_input.get("conversation")):
        ref = _text(item.get("message_ref"))
        if ref:
            output[ref] = _text(item.get("content"))
    return output


def _decision_evidence(value: dict[str, Any], reference_map: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"ref": ref, "quote": reference_map.get(ref, "")}
        for ref in _string_list(value.get("evidence_refs"))
    ]


def _knowledge_adoption_explanation(
    knowledge: dict[str, Any], checkpoint: dict[str, Any]
) -> dict[str, str]:
    if not knowledge:
        return {"sequence": "historical_not_recorded", "script": "historical_not_recorded"}
    adopted = _dict(knowledge.get("adopted"))
    execution = _dict(knowledge.get("execution"))
    selector = _dict(knowledge.get("selector"))
    primary = _dict(checkpoint.get("primary"))
    matched_sequences = _dict_list(knowledge.get("matched_sequences"))
    scripts = _dict_list(knowledge.get("script_candidates"))
    knowledge_status = _text(execution.get("knowledge_status")).lower()
    selector_status = _text(selector.get("status")).lower()

    if adopted.get("sequence_id"):
        sequence_reason = "adopted"
    elif matched_sequences:
        sequence_reason = "reply_not_adopted"
    elif not primary.get("code") and _text(checkpoint.get("classification_status")) in {"", "none"}:
        sequence_reason = "no_checkpoint"
    elif any(token in knowledge_status for token in ("error", "unavailable", "stale")):
        sequence_reason = "directory_unavailable"
    else:
        sequence_reason = "no_sequence_candidate"

    if adopted.get("script_ids"):
        script_reason = "adopted"
    elif scripts:
        script_reason = "reply_not_adopted"
    elif selector_status in {"empty", "error"}:
        script_reason = f"selector_{selector_status}"
    elif not execution.get("script_lookup_invoked"):
        script_reason = "script_lookup_not_run"
    elif any(token in knowledge_status for token in ("error", "unavailable", "stale")):
        script_reason = "directory_unavailable"
    else:
        script_reason = "no_script_candidate"
    return {"sequence": sequence_reason, "script": script_reason}


def _trace_availability(
    run: dict[str, Any], nodes: list[dict[str, Any]], *, trace_retention_days: int
) -> str:
    if nodes:
        return "available"
    if _text(run.get("runtime_status")) == "running":
        return "pending"
    created_at = _parse_datetime(run.get("created_at"))
    if created_at is not None:
        age_days = (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds() / 86400
        if age_days > max(1, int(trace_retention_days or 14)):
            return "expired"
    return "not_recorded"


def _workflow_nodes(
    *,
    run: dict[str, Any],
    nodes: list[dict[str, Any]],
    stored: dict[str, Any],
    decision_status: str,
    trace_status: str,
    delivery: dict[str, Any],
) -> list[dict[str, Any]]:
    definitions = (
        ("input", "接收与整理消息", "整理当前消息和客户身份", ("layer_1_input_normalization",)),
        ("context", "加载上下文与事实", "加载近聊、客户、订单等权威上下文", ("layer_2_background_context", "authoritative_context")),
        ("router", "Semantic Router", "识别检索目标、卡点和工具需求", ("semantic_evidence",)),
        ("facts", "只读事实查询", "按需查询门店、订单等事实", ("readonly_facts", "semantic_evidence_after_facts")),
        ("knowledge", "序列与话术召回", "整理跟进序列和卡点话术候选", ("material_selection",)),
        ("reply", "V3 Reply 最终决策", "输出最终意图、情绪、逼单和客户回复", ("reply_decision",)),
        ("validation", "事实与安全校验", "校验结构、事实、权限和销售安全边界", ("reply_decision",)),
        ("commit", "提交与记录", "执行合法交易动作并记录运行结果", ("prepare_transaction", "transaction_actions", "commit_result")),
    )
    by_name: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        by_name.setdefault(_text(node.get("node_name")), []).append(node)
    failed_seen = False
    output: list[dict[str, Any]] = []
    for key, label, purpose, names in definitions:
        matches = [item for name in names for item in by_name.get(name, [])]
        if key == "validation" and decision_status:
            status = "warning" if decision_status == "degraded" else "success"
            summary = "策略结构有降级修正" if status == "warning" else "策略结构和安全校验完成"
        elif matches:
            status = _aggregate_node_status(matches)
            summary = "；".join(
                _text(line)
                for item in matches
                for line in (item.get("summary") or [])[:1]
                if _text(line)
            )[:300]
        elif _text(run.get("runtime_status")) == "running":
            status = "pending"
            summary = "请求处理中，完成后提供准确节点轨迹"
        elif trace_status == "expired":
            status = "expired"
            summary = "节点轨迹已超过保留期"
        elif failed_seen:
            status = "not_reached"
            summary = "上游失败，本阶段未到达"
        else:
            status = "not_recorded"
            summary = "本次历史记录未保存该节点轨迹"
        failed_seen = failed_seen or status == "failed"
        output.append(
            {
                "key": key,
                "label": label,
                "purpose": purpose,
                "status": status,
                "summary": summary,
                "node_ids": [_text(item.get("id")) for item in matches if _text(item.get("id"))],
                "node_names": [_text(item.get("node_name")) for item in matches if _text(item.get("node_name"))],
                "duration_ms": sum(_integer(item.get("duration_ms")) for item in matches),
            }
        )
    delivery_status = _text(delivery.get("status") or _dict(stored.get("delivery")).get("status"))
    output.append(
        {
            "key": "delivery",
            "label": "发送与平台回执",
            "purpose": "区分生成、平台接受和确认送达",
            "status": _delivery_workflow_status(delivery_status),
            "summary": delivery_status or "未记录异步发送回执",
            "node_ids": [],
            "node_names": [],
            "duration_ms": 0,
        }
    )
    return output


def _aggregate_node_status(nodes: list[dict[str, Any]]) -> str:
    statuses = {_text(item.get("status")) for item in nodes}
    for status in ("failed", "warning", "pending", "skipped"):
        if status in statuses:
            return status
    return "success"


def _delivery_workflow_status(status: str) -> str:
    if status in {"send_failed", "delivery_failed", "partial_failed"}:
        return "failed"
    if status in {"pending", "platform_accepted", "delivery_pending", "sending"}:
        return "pending"
    if status in {"not_recorded", "", "direct_response_returned"}:
        return "skipped" if status == "not_recorded" else "success"
    return "success"


def _parse_datetime(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else ""


def _compact_model_usage(value: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    def collect(call: Any, *, node_name: str) -> None:
        if not isinstance(call, dict):
            return
        usage = _dict(call.get("usage"))
        model_input = _dict(call.get("input"))
        name = _text(call.get("name"))
        looks_like_model = bool(usage) or "raw_json_output" in call or any(
            token in name.lower()
            for token in ("model", "router", "reply", "vision", "gate")
        )
        if looks_like_model:
            model = _text(
                usage.get("winner_model")
                or usage.get("model")
                or model_input.get("model")
            )
            configured_model = _text(
                usage.get("configured_model") or model_input.get("configured_model")
            )
            output.append(
                {
                    "node": node_name,
                    "name": name or "model_call",
                    "provider": _text(usage.get("provider")),
                    "model": model,
                    "configured_model": configured_model,
                    "total_tokens": _integer(usage.get("total_tokens")),
                    "duration_ms": _integer(
                        usage.get("overall_duration_ms")
                        or usage.get("duration_ms")
                        or call.get("duration_ms")
                    ),
                    "attempts": max(
                        1,
                        _integer(usage.get("attempts") or usage.get("request_attempt") or 1),
                    ),
                    "fallback_used": bool(
                        configured_model and model and configured_model != model
                    ),
                }
            )
        for nested in call.get("nested_calls") or []:
            collect(nested, node_name=node_name)
        for key in ("retry", "recovery"):
            collect(call.get(key), node_name=node_name)

    for trace in value if isinstance(value, list) else []:
        if not isinstance(trace, dict):
            continue
        node_name = _text(trace.get("node") or trace.get("node_name"))
        for call in trace.get("tool_calls") or []:
            collect(call, node_name=node_name)
    return output[:24]


def _matched_sequences(
    recall: dict[str, Any],
    *,
    sequence_match: dict[str, Any],
    adopted: dict[str, Any],
) -> list[dict[str, Any]]:
    selected_ids = _string_list(sequence_match.get("sequence_ids"))
    alternatives = set(_string_list(sequence_match.get("alternative_sequence_ids")))
    adopted_sequence_id = _text(adopted.get("sequence_id"))
    adopted_step_id = _text(adopted.get("step_id"))
    candidates = {
        _text(item.get("sequence_id")): item
        for item in _dict_list(recall.get("sequence_candidates"))
        if _text(item.get("sequence_id"))
    }
    output: list[dict[str, Any]] = []
    for rank, sequence_id in enumerate(selected_ids, start=1):
        item = candidates.get(sequence_id, {})
        steps = [
            {
                "step_id": _text(step.get("step_id")),
                "sort_order": _integer(step.get("sort_order")),
                "action_code": _text(step.get("action_code")),
                "action_name": _text(step.get("action_name")),
                "adopted": (
                    sequence_id == adopted_sequence_id
                    and _text(step.get("step_id")) == adopted_step_id
                ),
            }
            for step in _dict_list(item.get("steps"))
        ]
        output.append(
            {
                "rank": rank,
                "sequence_id": sequence_id,
                "sequence_name": _text(item.get("sequence_name")),
                "checkpoint_code": _text(item.get("checkpoint_code")),
                "checkpoint_name": _text(item.get("checkpoint_name")),
                "alternative": sequence_id in alternatives,
                "adopted": sequence_id == adopted_sequence_id,
                "selection_reason": _text(item.get("selection_reason")),
                "steps": steps,
            }
        )
    return output


def _script_candidates(
    recall: dict[str, Any],
    *,
    adopted_script_ids: set[str],
    delivered_content_ids: set[str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in _dict_list(recall.get("candidates"))[:8]:
        platform_script_id = _text(item.get("script_id") or item.get("id"))
        script_code = _text(item.get("source_id") or item.get("script_code"))
        if not script_code and not platform_script_id:
            continue
        script_aliases = {
            value
            for value in (
                platform_script_id,
                script_code,
                _text(item.get("id")),
                _text(item.get("script_code")),
            )
            if value
        }
        checkpoint_type = _dict(item.get("checkpoint_type"))
        checkpoint_tag = _dict(item.get("checkpoint_tag"))
        paragraph_refs = [
            _text(paragraph.get("source_ref"))
            for paragraph in _dict_list(item.get("paragraphs"))
            if _text(paragraph.get("source_ref"))
        ]
        media = []
        for paragraph in _dict_list(item.get("paragraphs")):
            for message in _dict_list(paragraph.get("messages")):
                message_type = _text(message.get("type"))
                if message_type not in {"image", "video", "card", "miniprogram"}:
                    continue
                media.append(
                    {
                        "type": message_type,
                        "url": _text(message.get("url")),
                        "title": _text(message.get("title")),
                        "remark": _text(message.get("remark")),
                    }
                )
        adopted = bool(script_aliases & adopted_script_ids)
        delivered = adopted and any(
            any(
                content_id == f"follow_script:{alias}"
                or content_id.startswith(f"follow_script:{alias}:p")
                for alias in script_aliases
            )
            for content_id in delivered_content_ids
        )
        output.append(
            {
                "script_id": platform_script_id,
                "script_code": script_code,
                "script_name": _text(item.get("script_name")),
                "checkpoint_type_id": _integer(checkpoint_type.get("id")),
                "checkpoint_type_name": _text(checkpoint_type.get("name")),
                "checkpoint_tag_id": _integer(checkpoint_tag.get("id")),
                "checkpoint_tag_name": _text(checkpoint_tag.get("name")),
                "action_code": _text(item.get("action_code")),
                "action_name": _text(item.get("action_name")),
                "text_preview": _script_text_preview(item),
                "paragraph_refs": paragraph_refs,
                "media": media[:4],
                "adopted": adopted,
                "delivered": delivered,
            }
        )
    return output


def _script_text_preview(item: dict[str, Any]) -> str:
    direct = _text(item.get("reference_text") or item.get("body_text"))
    if direct:
        return direct[:240]
    texts: list[str] = []
    for paragraph in _dict_list(item.get("paragraphs")):
        for message in _dict_list(paragraph.get("messages")):
            if _text(message.get("type")) != "text":
                continue
            content = _text(message.get("content"))
            if content:
                texts.append(content)
    return " ".join(texts)[:240]


def _store_summary(state: dict[str, Any], *, store_query: dict[str, Any]) -> dict[str, Any]:
    fact = _dict(state.get("store_resolution_fact"))
    joined = _dict(state.get("evidence_join"))
    normalized = _dict(joined.get("normalized_tool_facts"))
    structured = _dict(normalized.get("structured_facts"))
    candidate_stores = _dict_list(
        fact.get("candidate_stores")
        or fact.get("recommended_stores")
        or structured.get("store_facts")
    )
    delivery_store_ids = _string_list(fact.get("delivery_store_ids"))
    sent_summary = sent_message_summary_for_model(state)
    latest_delivery = _dict(sent_summary.get("store_address_delivery"))
    latest_recommendation = _dict(sent_summary.get("latest_store_recommendation"))
    recommendation_evidence = _dict(latest_recommendation.get("store_search_evidence"))
    recommendation_final = _historical_store_recommendation_final(recommendation_evidence)
    historical_city = _text(recommendation_evidence.get("city"))
    current_city = _text(fact.get("city"))
    new_city_detected = (
        current_city != historical_city
        if current_city and historical_city
        else None
    )
    if delivery_store_ids:
        by_id = {
            _text(store.get("store_id") or store.get("id")): store
            for store in candidate_stores
            if _text(store.get("store_id") or store.get("id"))
        }
        delivered_stores = [
            by_id[store_id]
            for store_id in delivery_store_ids
            if store_id in by_id
        ]
        if delivered_stores:
            candidate_stores = delivered_stores
    return {
        "called": bool(store_query.get("required") or fact),
        "purpose": _text(store_query.get("purpose")),
        "destination": _text(
            store_query.get("destination_hint")
            or fact.get("raw_place")
            or fact.get("destination_query")
        ),
        "status": _text(fact.get("status")),
        "outcome": _text(fact.get("outcome")),
        "candidate_search_complete": bool(fact.get("candidate_search_complete")),
        "delivery_store_ids": delivery_store_ids,
        "candidate_count": int(
            fact.get("candidate_count") or len(candidate_stores)
        ),
        "latest_delivery": {
            "store_ids": _string_list(latest_delivery.get("latest_batch_store_ids")),
            "last_sent_at": _text(latest_delivery.get("last_sent_at")),
            "request_id": _text(latest_delivery.get("request_id")),
        },
        "latest_recommendation": {
            "query": _text(
                recommendation_evidence.get("normalized_query")
                or recommendation_evidence.get("raw_place")
            ),
            "city": historical_city,
            "district": _text(recommendation_evidence.get("district")),
            "resolved_admin_level": _text(recommendation_evidence.get("resolved_admin_level")),
            "store_ids": _string_list(latest_recommendation.get("latest_batch_store_ids")),
            "candidate_search_complete": recommendation_evidence.get("candidate_search_complete")
            if "candidate_search_complete" in recommendation_evidence
            else None,
            "recommendation_final_for_destination": recommendation_final,
            "clarification_would_change_result": _historical_store_clarification_changes_result(
                recommendation_evidence
            ),
            "ranking_method": _text(recommendation_evidence.get("ranking_method")),
            "distance_ranking_available": recommendation_evidence.get("distance_ranking_available")
            if "distance_ranking_available" in recommendation_evidence
            else None,
            "same_city_refinement_useful": False if recommendation_final else None,
        },
        "new_city_detected": new_city_detected,
        "stores": [
            {
                "store_id": _text(store.get("store_id") or store.get("id")),
                "store_name": _text(store.get("store_name") or store.get("name")),
                "address": _text(store.get("store_address") or store.get("address")),
                "distance_km": store.get("distance_km"),
            }
            for store in candidate_stores[:5]
        ],
        "error": _text(fact.get("error")),
    }


def _historical_store_recommendation_final(evidence: dict[str, Any]) -> bool | None:
    if "recommendation_final_for_destination" in evidence:
        return evidence.get("recommendation_final_for_destination") is True
    if evidence.get("candidate_search_complete") is True and (
        evidence.get("recommended_store_id") or evidence.get("delivery_store_ids")
    ):
        return True
    return None


def _historical_store_clarification_changes_result(evidence: dict[str, Any]) -> bool | None:
    if "clarification_would_change_result" in evidence:
        return evidence.get("clarification_would_change_result") is True
    if _historical_store_recommendation_final(evidence) is True:
        return False
    return None


def _initial_delivery_summary(state: dict[str, Any]) -> dict[str, Any]:
    async_final = _dict(state.get("async_final_reply"))
    control = _dict(state.get("reply_control"))
    control_async = _dict(control.get("async_final"))
    sync_return = _dict(control.get("sync_return"))
    callback_expected = bool(
        async_final.get("scheduled")
        or control_async.get("scheduled")
        or async_final.get("dispatch_id")
        or control_async.get("dispatch_id")
    )
    if callback_expected:
        mode = "async_callback"
        status = _text(
            async_final.get("status")
            or control_async.get("status")
            or "generated"
        )
        callback_reason = "最终回复由异步发送链路交付，等待或已收到平台回执。"
    else:
        mode = "sync_return"
        status = "direct_response_returned" if sync_return or state.get("reply_messages") else "empty"
        callback_reason = "回复已随本次 AI 接口响应同步返回；异步最终回复回调不需要，避免重复发送。"
    return {
        "mode": mode,
        "status": status,
        "callback_expected": callback_expected,
        "callback_reason": callback_reason,
        "dispatch_id": _text(
            async_final.get("dispatch_id") or control_async.get("dispatch_id")
        ),
        "expected_count": len(state.get("reply_messages") or []),
        "succeeded_count": 0,
        "failed_count": 0,
        "messages": _dict_list(state.get("reply_messages")),
        "items": [],
    }


def _timing_summary(trace: Any) -> list[dict[str, Any]]:
    output = []
    for item in _dict_list(trace):
        node = _text(item.get("node"))
        if not node:
            continue
        output.append(
            {
                "stage": _stage_label(node),
                "node": node,
                "duration_ms": _integer(item.get("duration_ms")),
                "status": "error" if item.get("error") else "ok",
            }
        )
    return output


def _stage_label(node: str) -> str:
    lower = node.lower()
    if "semantic" in lower or "knowledge" in lower:
        return "知识路由"
    if "tool" in lower or "store" in lower:
        return "门店与工具"
    if "reply" in lower or "synth" in lower:
        return "最终回复"
    if "commit" in lower:
        return "后台写入"
    if "context" in lower or "preprocess" in lower:
        return "数据准备"
    return "链路处理"


def _message_ref_map(state: dict[str, Any]) -> dict[str, str]:
    output = {"current_message": _text(state.get("content"))}
    shared = _dict(state.get("shared_context"))
    for item in _dict_list(shared.get("conversation")):
        ref = _text(item.get("message_ref"))
        if ref:
            output[ref] = _text(item.get("content"))
    return output


def _conversation_view(state: dict[str, Any]) -> list[dict[str, str]]:
    shared = _dict(state.get("shared_context"))
    output: list[dict[str, str]] = []
    for item in _dict_list(shared.get("conversation"))[-100:]:
        output.append(
            {
                "message_ref": _text(item.get("message_ref")),
                "role": _text(item.get("role") or "unknown"),
                "time": _text(
                    item.get("time")
                    or item.get("timestamp")
                    or item.get("sent_at")
                ),
                "message_type": _text(
                    item.get("message_type") or item.get("type") or "text"
                ),
                "content": _text(item.get("content"))[:1600],
            }
        )
    return output


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    return [_text(item) for item in value or [] if _text(item)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
