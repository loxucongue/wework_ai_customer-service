from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from app.graph.nodes.common import looks_bad_text, model_usage_snapshot
from app.graph.nodes.image_info import build_vision_prompt, fallback_image_info, validated_image_info
from app.graph.state import AgentState
from app.services.coze_client import CozeClient
from app.services.customer_context import CustomerContextService
from app.services.customer_store_knowledge import CustomerStoreKnowledgeService
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.trace_logger import TraceLogger


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
            errors = list(state.get("errors", []))
            if looks_bad_text(normalized):
                errors.append({"node": "layer_1_input_normalization", "message": "输入疑似乱码，已保留原文但后续会降低置信度"})
            temp_state = dict(state)
            temp_state["normalized_content"] = normalized
            image_task = asyncio.create_task(_understand_image(temp_state, model_client))
            image_info, model_call = await image_task
            if model_call:
                span["entry"]["tool_calls"] = [model_call]
            output = {"normalized_content": normalized, "image_info": image_info, "errors": errors, "trace": state.get("trace", [])}
            span["output_snapshot"] = output
            return output

    return input_normalization_layer


async def _understand_image(state: dict[str, Any], model_client: ModelClient | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    has_image = bool(state.get("file_image"))
    model_call: dict[str, Any] | None = None
    if has_image and model_client and model_client.available:
        try:
            model_call = {"name": "vision_model", "input": {"tier": "vision"}}
            payload = await model_client.vision_json(
                prompt=build_vision_prompt(state),
                image_url=str(state.get("file_image")),
                tier="vision",
            )
            image_info = validated_image_info(payload, has_image=True)
            model_call["output"] = {
                "image_type": image_info.get("image_type"),
                "confidence": image_info.get("confidence"),
            }
            model_call["usage"] = model_usage_snapshot(model_client)
            return image_info, model_call
        except Exception as exc:
            model_call = model_call or {"name": "vision_model", "input": {"tier": "vision"}}
            model_call["error"] = f"{type(exc).__name__}: {exc}"
    return fallback_image_info(has_image=has_image), model_call


def create_background_context_layer(
    *,
    trace_logger: TraceLogger,
    memory_store: CustomerMemoryStore | None,
    customer_context_service: CustomerContextService | None,
    customer_store_knowledge_service: CustomerStoreKnowledgeService | None,
    coze_client: CozeClient | None = None,
) -> Callable[[AgentState], Any]:
    async def background_context_layer(state: AgentState) -> dict[str, Any]:
        request_context = request_context_from_state(state)
        with trace_logger.node(
            state,
            "layer_2_background_context",
            {"customer_id": state.get("customer_id"), "user_id": state.get("user_id"), "wechat": state.get("wechat")},
        ) as span:
            substeps: list[dict[str, Any]] = []
            memory_task = asyncio.to_thread(_timed_call, "memory_load", _load_memory, memory_store, state)
            identity_task = asyncio.to_thread(_timed_call, "get_customer_info", _load_customer_identity, customer_context_service, state, request_context)
            memory_result, identity_result = await asyncio.gather(memory_task, identity_task)
            memory = memory_result["result"]
            identity = identity_result["result"]
            substeps.extend([_without_result(memory_result), _without_result(identity_result)])

            identity_context = identity.get("request_context") if isinstance(identity, dict) else {}
            scoped_request_context = {**request_context, **identity_context} if isinstance(identity_context, dict) else request_context
            saved_memory = memory.get("saved_memory") if isinstance(memory, dict) else {}
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
            customer_result_timed, store_result_timed = await asyncio.gather(customer_task, store_task)
            customer_result = customer_result_timed["result"]
            customer_store_knowledge = store_result_timed["result"]
            substeps.extend([_without_result(customer_result_timed), _without_result(store_result_timed)])
            customer_context = customer_result.get("customer_context", {})
            extra_result = _timed_call(
                "store_snapshot_hydrate",
                _enrich_customer_stores,
                customer_store_knowledge_service,
                customer_store_knowledge,
                scoped_request_context,
                customer_context,
            )
            customer_store_knowledge = extra_result["result"]
            substeps.append(_without_result(extra_result))
            span["entry"]["tool_calls"] = [
                *[
                    {
                        "name": f"background_{item.get('name')}",
                        "input": {"cache_hit": item.get("cache_hit", False)},
                        "output": {"duration_ms": item.get("duration_ms", 0)},
                        "error": item.get("error"),
                    }
                    for item in substeps
                ],
            ]
            output = {
                **memory,
                **customer_result,
                "customer_store_knowledge": customer_store_knowledge,
                "background_substeps": substeps,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = _background_output_snapshot(output)
            return output

    return background_context_layer


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


def _without_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name", ""),
        "duration_ms": item.get("duration_ms", 0),
        "cache_hit": item.get("cache_hit", False),
        "error": item.get("error"),
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
    memory = memory_store.load(str(state.get("customer_id") or "unknown")) if memory_store else {}
    return {
        "customer_profile": memory.get("portrait", {}) if isinstance(memory, dict) else {},
        "customer_basic_info": memory.get("basic_info", {}) if isinstance(memory, dict) else {},
        "history_events": memory.get("history_events", []) if isinstance(memory, dict) else [],
        "lifecycle_stage": memory.get("lifecycle_stage", "") if isinstance(memory, dict) else "",
        "saved_memory": memory if isinstance(memory, dict) else {},
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
    }


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
