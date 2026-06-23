from __future__ import annotations

import asyncio
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
            memory_task = asyncio.to_thread(_load_memory, memory_store, state)
            customer_task = asyncio.to_thread(_load_customer_context, customer_context_service, state, request_context)
            sales_talk_task = asyncio.create_task(_load_sales_talk_reference(coze_client, state))
            memory, customer_result, sales_talk_reference = await asyncio.gather(memory_task, customer_task, sales_talk_task)
            customer_context = customer_result.get("customer_context", {})
            store_task = asyncio.to_thread(
                _load_customer_stores,
                customer_store_knowledge_service,
                request_context,
                customer_context,
            )
            customer_store_knowledge = await store_task
            span["entry"]["tool_calls"] = [
                {
                    "name": "coze_kb_search",
                    "input": {"kb_name": "sales_talk_qa", "query": sales_talk_reference.get("query", "")},
                    "output": {"items": len(sales_talk_reference.get("items") or [])},
                    "error": sales_talk_reference.get("error"),
                }
            ]
            output = {
                **memory,
                **customer_result,
                "customer_store_knowledge": customer_store_knowledge,
                "sales_talk_reference": sales_talk_reference,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return background_context_layer


async def _load_sales_talk_reference(coze_client: CozeClient | None, state: AgentState) -> dict[str, Any]:
    query = _sales_talk_query_from_state(state)
    if not coze_client:
        return {"source": "service_unavailable", "query": query, "items": []}
    try:
        result = await coze_client.search_kb("sales_talk_qa", query)
        items = [
            {
                "document_id": item.document_id,
                "content": item.content[:500],
            }
            for item in result.items[:3]
        ]
        return {
            "source": "sales_talk_qa",
            "query": query,
            "items": items,
            "item_count": len(items),
        }
    except Exception as exc:
        return {
            "source": "sales_talk_qa",
            "query": query,
            "items": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def _sales_talk_query_from_state(state: AgentState) -> str:
    parts: list[str] = []
    content = str(state.get("normalized_content") or state.get("content") or "").strip()
    if content:
        parts.append(content[:120])
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    for item in history[-3:]:
        text = str(item or "").strip()
        if text:
            parts.append(text[:120])
    query = " ".join(parts).strip()
    return query or "当前客户咨询承接话术"


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


def _load_customer_stores(
    customer_store_knowledge_service: CustomerStoreKnowledgeService | None,
    request_context: dict[str, Any],
    customer_context: dict[str, Any],
) -> dict[str, Any]:
    if not customer_store_knowledge_service:
        return {"source": "service_unavailable", "stores": [], "appointment_extra_stores": []}
    try:
        return customer_store_knowledge_service.load(request_context=request_context, customer_context=customer_context)
    except Exception as exc:
        return {"source": "customer_store_knowledge_error", "stores": [], "appointment_extra_stores": [], "error": f"{type(exc).__name__}: {exc}"}


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
