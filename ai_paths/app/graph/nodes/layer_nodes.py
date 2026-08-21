from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from app.graph.nodes.common import (
    looks_bad_text,
    looks_suspected_short_mojibake,
    model_usage_snapshot,
    repair_mojibake_text,
)
from app.graph.nodes.conversation_history_fetch import ConversationFetcher, fetch_platform_conversation_history
from app.graph.nodes.image_info import build_vision_prompt, fallback_image_info, validated_image_info
from app.graph.nodes.location_card import append_location_card_to_content
from app.graph.state import AgentState
from app.services.coze_client import CozeClient
from app.services.customer_context import CustomerContextService
from app.services.customer_store_knowledge import CustomerStoreKnowledgeService
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger

UNKNOWN_TRANSFER_MESSAGE_PLACEHOLDERS = {
    "【未知消息类型】",
    "[未知消息类型]",
    "未知消息类型",
}
BACKGROUND_STORE_CONTEXT_BUDGET_SECONDS = 5.0
BACKGROUND_EXTERNAL_TIMEOUT_SECONDS = 8.0


def create_input_normalization_layer(
    *,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
) -> Callable[[AgentState], Any]:
    async def input_normalization_layer(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(state, "layer_1_input_normalization", {"content": state.get("content"), "file_image": state.get("file_image")}) as span:
            normalized = (state.get("content") or "").strip()
            if not normalized and state.get("file_image"):
                normalized = "[图片]"
            normalized, encoding_repair = repair_mojibake_text(normalized)
            request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
            normalized, location_card = append_location_card_to_content(normalized, request_context)
            errors = list(state.get("errors", []))
            if looks_bad_text(normalized):
                errors.append({"node": "layer_1_input_normalization", "message": "输入疑似乱码，已保留原文但后续会降低置信度"})
            input_quality_flags: list[str] = []
            if encoding_repair.get("applied") if isinstance(encoding_repair, dict) else False:
                input_quality_flags.append("encoding_repaired")
            if looks_bad_text(normalized):
                input_quality_flags.append("suspected_mojibake")
            if looks_suspected_short_mojibake(normalized):
                input_quality_flags.append("suspected_short_mojibake")
            temp_state = dict(state)
            temp_state["normalized_content"] = normalized
            platform_transfer_info = _platform_unknown_transfer_image_info(
                normalized,
                msgtype=str(request_context.get("msgtype") or ""),
            )
            if platform_transfer_info is not None:
                normalized = "客户发送了转账消息"
                image_info, model_calls = platform_transfer_info, []
            else:
                image_task = asyncio.create_task(_understand_image(temp_state, model_client))
                image_info, model_calls = await image_task
            if model_calls:
                span["entry"]["tool_calls"] = model_calls
            output = {
                "normalized_content": normalized,
                "location_card": location_card,
                "image_info": image_info,
                "errors": errors,
                "encoding_repair": encoding_repair,
                "input_quality_flags": input_quality_flags,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return input_normalization_layer


def _platform_unknown_transfer_image_info(
    content: str,
    *,
    msgtype: str = "",
) -> dict[str, Any] | None:
    """Normalize the platform's structured unknown message as a transfer fact."""
    compact = "".join(str(content or "").split())
    if str(msgtype or "").strip().lower() != "unknown" and compact not in UNKNOWN_TRANSFER_MESSAGE_PLACEHOLDERS:
        return None
    return {
        "has_image": False,
        "image_desc": "平台未知消息类型占位符，业务约定为客户转账消息。",
        "image_type": "payment_proof",
        "image_intent": "general_image",
        "body_part": "无",
        "visible_concerns": [],
        "risk_signals": [],
        "extracted_text": ["【未知消息类型】"],
        "text_clues": ["客户转账消息"],
        "payment_result": "success",
        "payment_amount": None,
        "payment_order_no": "",
        "confidence": 0.9,
        "source": "platform.unknown_message_transfer",
    }


async def _understand_image(
    state: dict[str, Any],
    model_client: ModelClient | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    image_urls = _image_urls_from_state(state)
    has_image = bool(image_urls)
    if not has_image or not model_client or not model_client.available:
        return fallback_image_info(has_image=has_image), []

    prompt = build_vision_prompt(state)

    async def analyze(image_url: str, index: int) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        model_call: dict[str, Any] = {
            "name": "vision_model",
            "input": {
                "tier": "vision",
                "image_index": index,
                "image_url": image_url,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
            },
        }
        try:
            payload = await model_client.vision_json(
                prompt=prompt,
                image_url=image_url,
                tier="vision",
                temperature=0.0,
            )
            image_info = validated_image_info(payload, has_image=True)
            model_call["raw_json_output"] = payload
            model_call["output"] = {
                "image_type": image_info.get("image_type"),
                "confidence": image_info.get("confidence"),
            }
            model_call["usage"] = model_usage_snapshot(model_client)
            return image_info, model_call
        except Exception as exc:
            model_call["error"] = f"{type(exc).__name__}: {exc}"
            return None, model_call

    analyzed = await asyncio.gather(*(analyze(url, index) for index, url in enumerate(image_urls, start=1)))
    infos = [info for info, _call in analyzed if isinstance(info, dict)]
    model_calls = [call for _info, call in analyzed]
    if not infos:
        fallback = fallback_image_info(has_image=True)
        fallback.update({"image_count": len(image_urls), "analyzed_image_count": 0})
        return fallback, model_calls
    return _merge_image_infos(infos, image_count=len(image_urls)), model_calls


def _image_urls_from_state(state: dict[str, Any]) -> list[str]:
    values = state.get("image_urls") if isinstance(state.get("image_urls"), list) else []
    values = [*values, str(state.get("file_image") or "")]
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        url = str(value or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        output.append(url)
    return output[-3:]


def _merge_image_infos(infos: list[dict[str, Any]], *, image_count: int) -> dict[str, Any]:
    if len(infos) == 1:
        return {
            **infos[0],
            "image_count": image_count,
            "analyzed_image_count": 1,
            "images": [infos[0]],
        }

    payment_info = next(
        (
            info
            for info in infos
            if str(info.get("image_type") or "") == "payment_proof"
            and str(info.get("payment_result") or "") in {"success", "pending", "failed"}
        ),
        None,
    )
    image_types = _dedupe_image_values(infos, "image_type")
    image_intents = _dedupe_image_values(infos, "image_intent")
    confidence_values = [float(info.get("confidence") or 0) for info in infos]
    merged: dict[str, Any] = {
        "has_image": True,
        "image_desc": "；".join(
            f"图片{index}：{str(info.get('image_desc') or '').strip()}"
            for index, info in enumerate(infos, start=1)
            if str(info.get("image_desc") or "").strip()
        ),
        "image_type": (
            "payment_proof"
            if payment_info
            else image_types[0]
            if len(image_types) == 1
            else "unclear"
        ),
        "image_intent": (
            str(payment_info.get("image_intent") or "general_image")
            if payment_info
            else image_intents[0]
            if len(image_intents) == 1
            else "general_image"
        ),
        "body_part": "、".join(_dedupe_image_values(infos, "body_part")),
        "visible_concerns": _dedupe_image_list_values(infos, "visible_concerns"),
        "risk_signals": _dedupe_image_list_values(infos, "risk_signals"),
        "extracted_text": _dedupe_image_list_values(infos, "extracted_text"),
        "text_clues": _dedupe_image_list_values(infos, "text_clues"),
        "payment_result": str((payment_info or {}).get("payment_result") or "unclear"),
        "payment_amount": (payment_info or {}).get("payment_amount"),
        "payment_order_no": str((payment_info or {}).get("payment_order_no") or ""),
        "confidence": round(sum(confidence_values) / len(confidence_values), 3),
        "image_count": image_count,
        "analyzed_image_count": len(infos),
        "images": infos,
    }
    return merged


def _dedupe_image_values(infos: list[dict[str, Any]], key: str) -> list[str]:
    return _dedupe_text_values(str(info.get(key) or "") for info in infos)


def _dedupe_image_list_values(infos: list[dict[str, Any]], key: str) -> list[str]:
    return _dedupe_text_values(
        str(value or "")
        for info in infos
        for value in (info.get(key) if isinstance(info.get(key), list) else [])
    )


def _dedupe_text_values(values: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def create_background_context_layer(
    *,
    trace_logger: TraceLogger,
    memory_store: CustomerMemoryStore | None,
    customer_context_service: CustomerContextService | None,
    customer_store_knowledge_service: CustomerStoreKnowledgeService | None,
    coze_client: CozeClient | None = None,
    conversation_fetcher: ConversationFetcher | None = None,
    follow_sequence_fetcher: Callable[[], Any] | None = None,
) -> Callable[[AgentState], Any]:
    async def background_context_layer(state: AgentState) -> dict[str, Any]:
        request_context = request_context_from_state(state)
        with trace_logger.node(
            state,
            "layer_2_background_context",
            {"customer_id": state.get("customer_id"), "user_id": state.get("user_id"), "wechat": state.get("wechat")},
        ) as span:
            substeps: list[dict[str, Any]] = []
            sequence_task = asyncio.create_task(
                _timed_async_call(
                    "follow_sequence_index",
                    follow_sequence_fetcher,
                    disabled_result={
                        "status": "disabled",
                        "reason": "follow_sequence_fetcher_unavailable",
                        "total": 0,
                        "items": [],
                    },
                )
            )
            memory_task = asyncio.to_thread(_timed_call, "memory_load", _load_memory, memory_store, state)
            identity_task = asyncio.to_thread(_timed_call, "get_customer_info", _load_customer_identity, customer_context_service, state, request_context)
            memory_result, identity_result = await asyncio.gather(
                memory_task,
                _await_timed_background_task(
                    identity_task,
                    name="get_customer_info",
                    timeout_seconds=BACKGROUND_EXTERNAL_TIMEOUT_SECONDS,
                    timeout_result={
                        "request_context": {},
                        "identity_context": {},
                        "error": f"timeout_after_{BACKGROUND_EXTERNAL_TIMEOUT_SECONDS:g}s",
                    },
                ),
            )
            memory = memory_result["result"]
            identity = identity_result["result"]
            substeps.extend([_without_result(memory_result), _without_result(identity_result)])

            identity_context = identity.get("request_context") if isinstance(identity, dict) else {}
            scoped_request_context = {**request_context, **identity_context} if isinstance(identity_context, dict) else request_context
            saved_memory = memory.get("saved_memory") if isinstance(memory, dict) else {}
            conversation_task = asyncio.create_task(
                _timed_conversation_fetch(
                    state,
                    conversation_fetcher,
                    request_context=scoped_request_context,
                )
            )
            store_context_started_at = time.monotonic()
            store_context_deadline = store_context_started_at + BACKGROUND_STORE_CONTEXT_BUDGET_SECONDS
            customer_task = asyncio.to_thread(
                _timed_call,
                "order_index",
                _load_customer_context_with_identity,
                customer_context_service,
                state,
                saved_memory,
                request_context,
                identity,
            )
            store_task = asyncio.to_thread(
                _timed_call,
                "store_index",
                _load_customer_stores,
                customer_store_knowledge_service,
                scoped_request_context,
                {},
                identity,
            )
            customer_result_timed, conversation_result_timed, store_result_timed, sequence_result_timed = await asyncio.gather(
                _await_timed_background_task(
                    customer_task,
                    name="order_index",
                    timeout_seconds=BACKGROUND_EXTERNAL_TIMEOUT_SECONDS,
                    timeout_result={
                        "customer_context": {},
                        "customer_context_error": f"timeout_after_{BACKGROUND_EXTERNAL_TIMEOUT_SECONDS:g}s",
                    },
                ),
                _await_timed_background_task(
                    conversation_task,
                    name="conversation_fetch",
                    timeout_seconds=BACKGROUND_EXTERNAL_TIMEOUT_SECONDS,
                    timeout_result={
                        "conversation_history": list(state.get("conversation_history") or []),
                        "conversation_turns": list(state.get("conversation_turns") or []),
                        "conversation_fetch": {
                            "status": "timeout",
                            "limit": 50,
                            "used_message_count": len(state.get("conversation_history") or []),
                            "error": f"timeout_after_{BACKGROUND_EXTERNAL_TIMEOUT_SECONDS:g}s",
                        },
                    },
                ),
                _await_timed_background_task(
                    store_task,
                    name="store_index",
                    timeout_seconds=max(0.05, store_context_deadline - time.monotonic()),
                    timeout_result={
                        "source": "customer_store_knowledge_timeout",
                        "stores": [],
                        "appointment_extra_stores": [],
                        "error": f"timeout_after_{BACKGROUND_STORE_CONTEXT_BUDGET_SECONDS:g}s",
                    },
                ),
                _await_timed_background_task(
                    sequence_task,
                    name="follow_sequence_index",
                    timeout_seconds=BACKGROUND_EXTERNAL_TIMEOUT_SECONDS,
                    timeout_result={
                        "status": "error",
                        "reason": f"timeout_after_{BACKGROUND_EXTERNAL_TIMEOUT_SECONDS:g}s",
                        "total": 0,
                        "items": [],
                        "error": f"timeout_after_{BACKGROUND_EXTERNAL_TIMEOUT_SECONDS:g}s",
                    },
                ),
            )
            customer_result = customer_result_timed["result"]
            customer_store_knowledge = store_result_timed["result"]
            conversation_result = conversation_result_timed["result"] if isinstance(conversation_result_timed.get("result"), dict) else {}
            conversation_history = conversation_result.get("conversation_history")
            if not isinstance(conversation_history, list):
                conversation_history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
            conversation_turns = conversation_result.get("conversation_turns")
            if not isinstance(conversation_turns, list):
                conversation_turns = state.get("conversation_turns") if isinstance(state.get("conversation_turns"), list) else []
            substeps.extend(
                [
                    _without_result(customer_result_timed),
                    _without_result(store_result_timed),
                    _without_result(conversation_result_timed),
                    _without_result(sequence_result_timed),
                ]
            )
            customer_context = customer_result.get("customer_context", {})
            store_context_skipped_steps: list[str] = []
            store_index_error = str(store_result_timed.get("error") or "")
            store_index_timed_out = "timeout_after_" in store_index_error
            store_context_remaining = max(0.0, store_context_deadline - time.monotonic())
            if store_index_timed_out:
                store_context_skipped_steps.append("store_snapshot_hydrate:index_timeout")
                extra_result = _skipped_background_result(
                    "store_snapshot_hydrate",
                    customer_store_knowledge,
                    reason="store_index_timeout",
                )
            elif store_context_remaining <= 0.05:
                store_context_skipped_steps.append("store_snapshot_hydrate:budget_exhausted")
                extra_result = _skipped_background_result(
                    "store_snapshot_hydrate",
                    customer_store_knowledge,
                    reason="shared_store_budget_exhausted",
                )
            else:
                extra_task = asyncio.to_thread(
                    _timed_call,
                    "store_snapshot_hydrate",
                    _enrich_customer_stores,
                    customer_store_knowledge_service,
                    customer_store_knowledge,
                    scoped_request_context,
                    customer_context,
                )
                extra_result = await _await_timed_background_task(
                    extra_task,
                    name="store_snapshot_hydrate",
                    timeout_seconds=store_context_remaining,
                    timeout_result={
                        **customer_store_knowledge,
                        "error": f"timeout_after_{store_context_remaining:g}s",
                        "snapshot_refresh_error": f"timeout_after_{store_context_remaining:g}s",
                    },
                )
            customer_store_knowledge = extra_result["result"]
            substeps.append(_without_result(extra_result))
            store_context_status = _store_context_status(
                customer_store_knowledge_service=customer_store_knowledge_service,
                store_index_result=store_result_timed,
                hydrate_result=extra_result,
            )
            store_context_elapsed_ms = int((time.monotonic() - store_context_started_at) * 1000)
            span["entry"]["tool_calls"] = [
                *[
                    {
                        "name": f"background_{item.get('name')}",
                        "input": {"cache_hit": item.get("cache_hit", False)},
                        "output": _substep_tool_output(item),
                        "error": item.get("error"),
                    }
                    for item in substeps
                ],
            ]
            output = {
                **memory,
                **customer_result,
                "customer_store_knowledge": customer_store_knowledge,
                "conversation_history": conversation_history,
                "conversation_turns": conversation_turns,
                "conversation_fetch": conversation_result.get("conversation_fetch", {}),
                "follow_sequence_index": sequence_result_timed.get("result") or {},
                "background_substeps": substeps,
                "store_context_status": store_context_status,
                "store_context_elapsed_ms": store_context_elapsed_ms,
                "store_context_skipped_steps": store_context_skipped_steps,
                "background_fact_views": _background_fact_views(
                    identity=identity,
                    customer_result=customer_result,
                    store_knowledge=customer_store_knowledge,
                    conversation_result=conversation_result,
                ),
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = _background_output_snapshot(output)
            return output

    return background_context_layer


def _background_fact_views(
    *,
    identity: Any,
    customer_result: Any,
    store_knowledge: Any,
    conversation_result: Any,
) -> dict[str, Any]:
    identity_dict = identity if isinstance(identity, dict) else {}
    customer_dict = customer_result if isinstance(customer_result, dict) else {}
    store_dict = store_knowledge if isinstance(store_knowledge, dict) else {}
    conversation_dict = conversation_result if isinstance(conversation_result, dict) else {}
    customer_context = customer_dict.get("customer_context") if isinstance(customer_dict.get("customer_context"), dict) else {}
    stores = store_dict.get("stores") if isinstance(store_dict.get("stores"), list) else []
    fetch = conversation_dict.get("conversation_fetch") if isinstance(conversation_dict.get("conversation_fetch"), dict) else {}
    return {
        "history_facts": {
            "status": fetch.get("status", "fallback"),
            "used_message_count": fetch.get("used_message_count", 0),
            "source": "platform_conversation_or_request_fallback",
        },
        "transaction_facts": {
            "order_count": len(customer_context.get("orders") or []) if isinstance(customer_context.get("orders"), list) else 0,
            "source": customer_context.get("source", "platform_order_index"),
            "missing": customer_dict.get("customer_context_error", ""),
        },
        "store_scope_facts": {
            "store_count": len(stores),
            "source": store_dict.get("source", ""),
            "missing": store_dict.get("error", ""),
        },
        "identity_facts": {
            "resolved": bool(identity_dict.get("request_context") or identity_dict.get("identity_context")),
            "missing": identity_dict.get("error", ""),
        },
        "fact_conflicts": [],
    }


def _timed_call(name: str, func: Callable[..., Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = func(*args, **kwargs)
        return {
            "name": name,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "result": result,
            "cache_hit": _cache_hit_from_result(result),
            "error": _error_from_result(result),
        }
    except Exception as exc:
        return {
            "name": name,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "result": {},
            "cache_hit": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _timed_async_call(
    name: str,
    func: Callable[[], Any] | None,
    *,
    disabled_result: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    if func is None:
        return {
            "name": name,
            "duration_ms": 0,
            "result": dict(disabled_result),
            "cache_hit": False,
            "error": "",
        }
    try:
        result = await func()
        return {
            "name": name,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "result": result if isinstance(result, dict) else {},
            "cache_hit": _cache_hit_from_result(result),
            "error": _error_from_result(result),
        }
    except Exception as exc:
        return {
            "name": name,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "result": {},
            "cache_hit": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _await_timed_background_task(
    task: asyncio.Task[dict[str, Any]],
    *,
    name: str,
    timeout_seconds: float,
    timeout_result: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
    except TimeoutError:
        return {
            "name": name,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "result": dict(timeout_result),
            "cache_hit": False,
            "error": str(timeout_result.get("error") or f"timeout_after_{timeout_seconds:g}s"),
        }


def _without_result(item: dict[str, Any]) -> dict[str, Any]:
    output = {
        "name": item.get("name", ""),
        "duration_ms": item.get("duration_ms", 0),
        "cache_hit": item.get("cache_hit", False),
        "error": item.get("error"),
    }
    summary = item.get("summary")
    if isinstance(summary, dict):
        output.update(summary)
    return output


def _substep_tool_output(item: dict[str, Any]) -> dict[str, Any]:
    output = {"duration_ms": item.get("duration_ms", 0)}
    for key in ("status", "reason", "missing", "message_count", "used_message_count", "limit"):
        if key in item:
            output[key] = item.get(key)
    return output


async def _timed_conversation_fetch(
    state: AgentState,
    conversation_fetcher: ConversationFetcher | None,
    *,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        conversation_history, conversation_fetch = await fetch_platform_conversation_history(
            state,
            conversation_fetcher,
            limit=50,
            fallback_limit=50,
            request_context=request_context,
        )
        return {
            "name": "conversation_fetch",
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "result": {
                "conversation_history": conversation_history,
                "conversation_turns": list(conversation_fetch.get("recent_turns") or []),
                "conversation_fetch": conversation_fetch,
            },
            "summary": conversation_fetch,
            "cache_hit": False,
            "error": conversation_fetch.get("error", "") if isinstance(conversation_fetch, dict) else "",
        }
    except Exception as exc:
        fallback = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
        summary = {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "used_message_count": len(fallback[-50:]),
            "limit": 50,
        }
        return {
            "name": "conversation_fetch",
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "result": {
                "conversation_history": fallback[-50:],
                "conversation_turns": list(state.get("conversation_turns") or []),
                "conversation_fetch": summary,
            },
            "summary": summary,
            "cache_hit": False,
            "error": summary["error"],
        }


def _cache_hit_from_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if "cache_hit" in result:
        return bool(result.get("cache_hit"))
    cache = result.get("cache")
    if isinstance(cache, dict):
        return any(bool(value) for value in cache.values())
    return False


def _error_from_result(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    return str(result.get("error") or result.get("customer_context_error") or result.get("orders_error") or "")


def _load_memory(memory_store: CustomerMemoryStore | None, state: AgentState) -> dict[str, Any]:
    if state.get("test_isolated"):
        return {
            "customer_profile": {},
            "customer_basic_info": {},
            "history_events": [],
            "lifecycle_stage": "",
            "saved_memory": {},
            "memory_isolated": True,
        }
    sales_contact_key = str(state.get("sales_contact_key") or "").strip()
    memory = memory_store.load(sales_contact_key) if memory_store and sales_contact_key else {}
    return {
        "customer_profile": memory.get("portrait", {}) if isinstance(memory, dict) else {},
        "customer_basic_info": memory.get("basic_info", {}) if isinstance(memory, dict) else {},
        "history_events": memory.get("history_events", []) if isinstance(memory, dict) else [],
        "lifecycle_stage": memory.get("lifecycle_stage", "") if isinstance(memory, dict) else "",
        "saved_memory": memory if isinstance(memory, dict) else {},
        "memory_scope_status": "scoped" if sales_contact_key else "skipped_missing_wechat_scope",
    }


def _load_customer_context(
    customer_context_service: CustomerContextService | None,
    state: AgentState,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    error = None
    if customer_context_service:
        try:
            context = customer_context_service.load(
                customer_id=str(state.get("customer_id") or "unknown"),
                memory=state.get("saved_memory") or {},
                request_context=request_context,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    return {
        "customer_context": context,
        "appointment_cache": context.get("appointment", {}) if isinstance(context, dict) else {},
        "customer_context_error": error,
    }


def _load_customer_identity(
    customer_context_service: CustomerContextService | None,
    state: AgentState,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    if not customer_context_service:
        return {"platform_customer_id": str(state.get("customer_id") or "unknown"), "request_context": request_context}
    return customer_context_service.load_identity(
        customer_id=str(state.get("customer_id") or "unknown"),
        request_context=request_context,
    )


def _load_customer_context_with_identity(
    customer_context_service: CustomerContextService | None,
    state: AgentState,
    saved_memory: dict[str, Any],
    request_context: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    error = None
    if customer_context_service:
        try:
            context = customer_context_service.load_with_identity(
                customer_id=str(state.get("customer_id") or "unknown"),
                memory=saved_memory if isinstance(saved_memory, dict) else {},
                request_context=request_context,
                identity=identity,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    return {
        "customer_context": context,
        "appointment_cache": context.get("appointment", {}) if isinstance(context, dict) else {},
        "customer_context_error": error,
    }


def _load_customer_stores(
    customer_store_knowledge_service: CustomerStoreKnowledgeService | None,
    request_context: dict[str, Any],
    customer_context: dict[str, Any],
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not customer_store_knowledge_service:
        return {"source": "service_unavailable", "stores": [], "appointment_extra_stores": []}
    try:
        return customer_store_knowledge_service.load(request_context=request_context, customer_context=customer_context, identity=identity)
    except Exception as exc:
        return {"source": "customer_store_knowledge_error", "stores": [], "appointment_extra_stores": [], "error": f"{type(exc).__name__}: {exc}"}


def _enrich_customer_stores(
    customer_store_knowledge_service: CustomerStoreKnowledgeService | None,
    customer_store_knowledge: dict[str, Any],
    request_context: dict[str, Any],
    customer_context: dict[str, Any],
) -> dict[str, Any]:
    if not customer_store_knowledge_service or not hasattr(customer_store_knowledge_service, "with_appointment_extra_stores"):
        return customer_store_knowledge
    return customer_store_knowledge_service.with_appointment_extra_stores(
        customer_store_knowledge=customer_store_knowledge,
        request_context=request_context,
        customer_context=customer_context,
    )


def _background_output_snapshot(output: dict[str, Any]) -> dict[str, Any]:
    customer_context = output.get("customer_context") if isinstance(output.get("customer_context"), dict) else {}
    store_knowledge = output.get("customer_store_knowledge") if isinstance(output.get("customer_store_knowledge"), dict) else {}
    return {
        "customer_profile": output.get("customer_profile", {}),
        "customer_basic_info": output.get("customer_basic_info", {}),
        "history_events_count": len(output.get("history_events") or []),
        "lifecycle_stage": output.get("lifecycle_stage", ""),
        "customer_context": {
            "customer_id": customer_context.get("customer_id"),
            "platform_customer_id": customer_context.get("platform_customer_id"),
            "customer_add_wechat_id": customer_context.get("customer_add_wechat_id"),
            "source": customer_context.get("source"),
            "appointment": customer_context.get("appointment"),
            "orders_count": len(customer_context.get("orders") or []),
            "cache": customer_context.get("cache", {}),
            "orders_error": customer_context.get("orders_error", ""),
            "customer_info_error": customer_context.get("customer_info_error", ""),
        },
        "appointment_cache": output.get("appointment_cache", {}),
        "customer_context_error": output.get("customer_context_error"),
        "customer_store_knowledge": {
            "source": store_knowledge.get("source"),
            "customer_id": store_knowledge.get("customer_id"),
            "customer_add_wechat_id": store_knowledge.get("customer_add_wechat_id"),
            "store_count": store_knowledge.get("store_count", 0),
            "missing_snapshot_store_ids": store_knowledge.get("missing_snapshot_store_ids", []),
            "snapshot_generated_at": store_knowledge.get("snapshot_generated_at"),
            "snapshot_source": store_knowledge.get("snapshot_source"),
            "snapshot_refresh_error": store_knowledge.get("snapshot_refresh_error", ""),
            "appointment_extra_store_count": len(store_knowledge.get("appointment_extra_stores") or []),
            "cache": store_knowledge.get("cache", {}),
            "error": store_knowledge.get("error", ""),
        },
        "background_substeps": output.get("background_substeps", []),
        "store_context_status": output.get("store_context_status", ""),
        "store_context_elapsed_ms": output.get("store_context_elapsed_ms", 0),
        "store_context_skipped_steps": output.get("store_context_skipped_steps", []),
        "conversation_fetch": output.get("conversation_fetch", {}),
        "conversation_history_count": len(output.get("conversation_history") or []),
    }


def _skipped_background_result(name: str, result: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "duration_ms": 0,
        "result": dict(result),
        "cache_hit": _cache_hit_from_result(result),
        "error": "",
        "summary": {"status": "skipped", "reason": reason},
    }


def _store_context_status(
    *,
    customer_store_knowledge_service: CustomerStoreKnowledgeService | None,
    store_index_result: dict[str, Any],
    hydrate_result: dict[str, Any],
) -> str:
    if customer_store_knowledge_service is None:
        return "unavailable"
    errors = " ".join(
        str(item.get("error") or "")
        for item in (store_index_result, hydrate_result)
        if isinstance(item, dict)
    )
    if "timeout_after_" in errors:
        return "partial_timeout"
    store_result = hydrate_result.get("result") if isinstance(hydrate_result, dict) else {}
    if errors and not (isinstance(store_result, dict) and (store_result.get("stores") or store_result.get("appointment_extra_stores"))):
        return "unavailable"
    return "complete"


def request_context_from_state(state: AgentState) -> dict[str, Any]:
    context = dict(state.get("request_context") or {})
    fields = {
        "user_id": state.get("user_id"),
        "corp_id": state.get("corp_id"),
        "wechat": state.get("wechat"),
        "external_userid": state.get("external_userid"),
        "customer_id": state.get("customer_id"),
        "customer_add_wechat_id": state.get("customer_add_wechat_id"),
        "confirmed_store_id": state.get("confirmed_store_id"),
        "confirmed_store_name": state.get("confirmed_store_name"),
        "store_id": state.get("store_id"),
        "store_name": state.get("store_name"),
        "appointment_id": state.get("appointment_id"),
        "appointment_time": state.get("appointment_time"),
    }
    for key, value in fields.items():
        if value not in (None, ""):
            context[key] = value
    return context
