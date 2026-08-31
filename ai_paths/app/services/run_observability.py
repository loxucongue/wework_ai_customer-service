from __future__ import annotations

from typing import Any

from app.services.run_observability_legacy import build_run_observability, trace_wall_duration_ms


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
