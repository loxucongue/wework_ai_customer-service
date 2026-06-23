from __future__ import annotations

import asyncio
import html
import time
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

from app.chat_request_context import build_request_context, conversation_id_from_request, conversation_title
from app.chat_runtime_helpers import failed_state_from_exception, safe_repository_call
from app.chat_runtime_metrics import collect_model_usage, collect_tool_calls
from app.graph.planner.runtime_plan import planner_public_route
from app.graph.state import AgentState
from app.schemas import ChatRequest, ChatResponse, ReplyMessage
from app.services.memory_store import CustomerMemoryStore
from app.services.outreach_send_client import OutreachSendClient
from app.services.storage import AppRepository
from app.services.trace_logger import TraceLogger, compact, utc_now_iso


class ChatRuntime:
    def __init__(
        self,
        *,
        full_graph: Any,
        trace_logger: TraceLogger,
        repository: AppRepository,
        planner_graph: Any | None = None,
        finalize_graph: Any | None = None,
        outreach_send_client: OutreachSendClient | None = None,
        memory_store: CustomerMemoryStore | None = None,
    ) -> None:
        self._full_graph = full_graph
        self._planner_graph = planner_graph or full_graph
        self._finalize_graph = finalize_graph
        self._trace_logger = trace_logger
        self._repository = repository
        self._outreach_send_client = outreach_send_client
        self._memory_store = memory_store

    async def run_chat(self, request: ChatRequest) -> ChatResponse:
        request_id = str(uuid4())
        request_context = build_request_context(request)
        conversation_id = self._prepare_conversation(request, request_id, request_context)
        initial_state = self._initial_state(request, request_id, request_context)

        try:
            final_state: AgentState = await self._full_graph.ainvoke(initial_state)
        except Exception as exc:
            self._handle_graph_exception(initial_state, conversation_id, exc)

        return self._persist_and_build_response(
            request=request,
            request_id=request_id,
            conversation_id=conversation_id,
            final_state=final_state,
            allow_empty_reply=False,
        )

    async def run_platform_reply(self, request: ChatRequest, background_tasks: Any | None = None) -> ChatResponse:
        request_id = str(uuid4())
        request_context = build_request_context(request)
        conversation_id = self._prepare_conversation(request, request_id, request_context)
        initial_state = self._initial_state(request, request_id, request_context)

        try:
            planner_state: AgentState = await self._planner_graph.ainvoke(initial_state)
        except Exception as exc:
            self._handle_graph_exception(initial_state, conversation_id, exc)

        sync_messages = _planner_sync_reply_messages(planner_state)
        planner_state["reply_messages"] = sync_messages
        planner_state["sync_reply_messages"] = sync_messages
        planner_state["reply_source"] = _platform_reply_source(planner_state)
        should_finalize = _should_run_async_finalize(planner_state)
        planner_state["async_final_reply"] = {
            "scheduled": should_finalize,
            "status": "scheduled" if should_finalize else "not_required",
        }

        response = self._persist_and_build_response(
            request=request,
            request_id=request_id,
            conversation_id=conversation_id,
            final_state=planner_state,
            allow_empty_reply=True,
        )
        if should_finalize:
            self._schedule_async_finalize_and_send(
                request=request,
                conversation_id=conversation_id,
                planner_state=planner_state,
                background_tasks=background_tasks,
            )
        return response

    def _schedule_async_finalize_and_send(
        self,
        *,
        request: ChatRequest,
        conversation_id: str,
        planner_state: AgentState,
        background_tasks: Any | None = None,
    ) -> None:
        if not self._finalize_graph:
            planner_state["async_final_reply"] = {"scheduled": False, "status": "skipped", "reason": "finalize_graph_missing"}
            self._save_state(conversation_id, planner_state)
            return

        async def runner() -> None:
            final_state = dict(planner_state)
            final_state["trace"] = list(planner_state.get("trace") or [])
            final_state["errors"] = list(planner_state.get("errors") or [])
            try:
                final_state = await self._finalize_graph.ainvoke(final_state)
                messages = final_state.get("reply_messages") if isinstance(final_state.get("reply_messages"), list) else []
                if not messages:
                    final_state["async_final_reply"] = {
                        "scheduled": True,
                        "status": "skipped",
                        "reason": "empty_final_reply_messages",
                    }
                    _append_async_send_trace(final_state, final_state["async_final_reply"])
                    self._save_state(conversation_id, final_state)
                    return
                send_result = await self._send_async_reply(request, final_state, messages)
                final_state["async_final_reply"] = send_result
                _append_async_send_trace(final_state, send_result)
                if send_result.get("status") == "sent":
                    safe_repository_call(
                        self._repository.add_assistant_message,
                        conversation_id=conversation_id,
                        request_id=f"{final_state.get('request_id')}:async",
                        reply_messages=messages,
                    )
                    _record_sent_case_images(
                        self._memory_store,
                        final_state,
                        customer_id=str(request.customer_id or ""),
                        reply_messages=messages,
                    )
                self._save_state(conversation_id, final_state)
            except Exception as exc:
                error = {"scheduled": True, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
                final_state["async_final_reply"] = error
                final_state.setdefault("errors", []).append({"node": "async_final_reply", "message": "async_final_reply_failed", "detail": error["error"]})
                _append_async_send_trace(final_state, error)
                self._save_state(conversation_id, final_state)

        if background_tasks is not None:
            background_tasks.add_task(runner)
        else:
            asyncio.create_task(runner())

    async def _send_async_reply(self, request: ChatRequest, final_state: AgentState, messages: list[dict[str, Any]]) -> dict[str, Any]:
        if not self._outreach_send_client:
            return {"scheduled": True, "status": "skipped", "reason": "outreach_send_client_missing"}
        result = await self._outreach_send_client.send_reply_messages(
            request_id=str(final_state.get("request_id") or ""),
            request_context=final_state.get("request_context") if isinstance(final_state.get("request_context"), dict) else {},
            fallback_customer_id=request.customer_id,
            fallback_corp_id=request.corp_id,
            fallback_user_id=request.user_id,
            fallback_wechat=request.wechat,
            fallback_external_userid=request.external_userid,
            reply_messages=messages,
        )
        result["scheduled"] = True
        return result

    def _prepare_conversation(self, request: ChatRequest, request_id: str, request_context: dict[str, Any]) -> str:
        conversation_id = conversation_id_from_request(request, request_context)
        safe_repository_call(
            self._repository.upsert_conversation,
            conversation_id=conversation_id,
            request=request,
            title=conversation_title(request.content),
        )
        safe_repository_call(
            self._repository.add_user_message,
            conversation_id=conversation_id,
            request_id=request_id,
            content=request.content,
            file_image=request.file_image,
        )
        return conversation_id

    @staticmethod
    def _initial_state(request: ChatRequest, request_id: str, request_context: dict[str, Any]) -> AgentState:
        return {
            "request_id": request_id,
            "customer_id": request.customer_id,
            "corp_id": request.corp_id,
            "content": request.content,
            "conversation_history": request.conversation_history,
            "file_image": request.file_image,
            "user_id": request.user_id,
            "wechat": request.wechat,
            "external_userid": request.external_userid,
            "customer_add_wechat_id": request.customer_add_wechat_id,
            "confirmed_store_id": request.confirmed_store_id,
            "confirmed_store_name": request.confirmed_store_name,
            "store_id": request.store_id,
            "store_name": request.store_name,
            "appointment_id": request.appointment_id,
            "appointment_time": request.appointment_time,
            "request_context": request_context,
            "trace": [],
            "errors": [],
        }

    def _handle_graph_exception(self, initial_state: AgentState, conversation_id: str, exc: Exception) -> None:
        failed_state = failed_state_from_exception(initial_state, exc)
        self._trace_logger.write_run(failed_state)
        safe_repository_call(
            self._repository.save_run,
            conversation_id=conversation_id,
            final_state=failed_state,
            token_usage=collect_model_usage(failed_state.get("trace", []))["summary"],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI customer service run failed before producing a reply.",
        ) from exc

    def _persist_and_build_response(
        self,
        *,
        request: ChatRequest,
        request_id: str,
        conversation_id: str,
        final_state: AgentState,
        allow_empty_reply: bool,
    ) -> ChatResponse:
        route_result = planner_public_route(final_state)
        model_usage = collect_model_usage(final_state.get("trace", []))
        raw_reply_messages = final_state.get("reply_messages") or []
        planner_no_reply = str(final_state.get("planner_decision") or "") == "no_reply" or str(final_state.get("reply_source") or "") == "planner_no_reply"
        if not raw_reply_messages and not (allow_empty_reply or planner_no_reply):
            final_state.setdefault("errors", []).append(
                {
                    "stage": "final_reply",
                    "error": "Final reply model failed or produced no customer-facing reply.",
                }
            )
            self._trace_logger.write_run(final_state)
            safe_repository_call(
                self._repository.save_run,
                conversation_id=conversation_id,
                final_state=final_state,
                token_usage=model_usage["summary"],
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Final reply model failed or produced no customer-facing reply.",
            )
        reply_messages = [ReplyMessage(**message) for message in raw_reply_messages]
        if reply_messages:
            safe_repository_call(
                self._repository.add_assistant_message,
                conversation_id=conversation_id,
                request_id=request_id,
                reply_messages=[message.model_dump() for message in reply_messages],
            )
            _record_sent_case_images(
                self._memory_store,
                final_state,
                customer_id=str(request.customer_id or ""),
                reply_messages=[message.model_dump() for message in reply_messages],
            )
        log_path = self._trace_logger.write_run(final_state)
        safe_repository_call(
            self._repository.save_run,
            conversation_id=conversation_id,
            final_state=final_state,
            token_usage=model_usage["summary"],
        )

        return ChatResponse(
            request_id=request_id,
            reply_messages=reply_messages,
            scene=str(route_result.get("scene", "")),
            intent=str(route_result.get("intent", "")),
            subflow=str(route_result.get("subflow", "")),
            trace_url=str(log_path),
            meta={
                "tool_result_keys": list((final_state.get("tool_results") or {}).keys()),
                "profile_update": final_state.get("profile_update", {}),
                "event_updates": final_state.get("event_updates", []),
                "image_info": final_state.get("image_info", {}),
                "memory_error": final_state.get("memory_error"),
                "customer_context": final_state.get("customer_context", {}),
                "customer_context_error": final_state.get("customer_context_error"),
                "customer_store_knowledge": _customer_store_knowledge_meta(final_state.get("customer_store_knowledge")),
                "sales_talk_reference": _sales_talk_reference_meta(final_state.get("sales_talk_reference")),
                "case_image_send_record": final_state.get("case_image_send_record", {}),
                "model_usage": model_usage["calls"],
                "token_usage": model_usage["summary"],
                "tool_calls": collect_tool_calls(final_state.get("trace", [])),
                "planner_source": final_state.get("planner_source", ""),
                "planner_decision": final_state.get("planner_decision", ""),
                "planner_stage": final_state.get("planner_stage", ""),
                "planner_sub_rule_id": final_state.get("planner_sub_rule_id", ""),
                "policy_id": final_state.get("policy_id", ""),
                "policy_family_id": final_state.get("policy_family_id", ""),
                "exact_policy_id": final_state.get("exact_policy_id", ""),
                "policy_match_level": final_state.get("policy_match_level", ""),
                "policy_version": final_state.get("policy_version", ""),
                "reply_source": final_state.get("reply_source", ""),
                "postprocess_changed": bool(final_state.get("postprocess_changed")),
                "postprocess_reasons": final_state.get("postprocess_reasons", []),
                "async_final_reply": final_state.get("async_final_reply", {}),
                "conversation_id": conversation_id,
            },
        )

    def _save_state(self, conversation_id: str, state: AgentState) -> None:
        self._trace_logger.write_run(state)
        safe_repository_call(
            self._repository.save_run,
            conversation_id=conversation_id,
            final_state=state,
            token_usage=collect_model_usage(state.get("trace", []))["summary"],
        )


def _planner_sync_reply_messages(state: AgentState) -> list[dict[str, Any]]:
    messages = state.get("planner_reply_messages") if isinstance(state.get("planner_reply_messages"), list) else []
    if str(state.get("planner_decision") or "") in {"direct_reply", "need_tools"}:
        return [item for item in messages if isinstance(item, dict)]
    return []


def _platform_reply_source(state: AgentState) -> str:
    decision = str(state.get("planner_decision") or "").strip()
    if not _planner_sync_reply_messages(state):
        return "planner_no_reply" if decision == "no_reply" else "planner_empty_reply"
    if decision == "no_reply":
        return "planner_no_reply"
    if decision == "need_tools":
        return "planner_transition_reply"
    return "planner_direct_reply"


def _should_run_async_finalize(state: AgentState) -> bool:
    if str(state.get("planner_decision") or "").strip() != "need_tools":
        return False
    tools = state.get("planner_tool_calls") if isinstance(state.get("planner_tool_calls"), list) else []
    return any(isinstance(tool, dict) and str(tool.get("name") or "").strip() not in {"", "no_tool"} for tool in tools)


def _append_async_send_trace(state: AgentState, result: dict[str, Any]) -> None:
    started = time.perf_counter()
    entry = {
        "node": "async_final_reply_send",
        "started_at": utc_now_iso(),
        "input_snapshot": compact(
            {
                "reply_messages": len(state.get("reply_messages") or []),
                "request_id": state.get("request_id", ""),
            }
        ),
        "tool_calls": [{"name": "ai_outreach_send", "output": compact(result)}],
        "error": result.get("error"),
        "output_snapshot": compact(result),
    }
    entry["finished_at"] = utc_now_iso()
    entry["duration_ms"] = int((time.perf_counter() - started) * 1000)
    state.setdefault("trace", []).append(entry)


def _record_sent_case_images(
    memory_store: CustomerMemoryStore | None,
    state: AgentState,
    *,
    customer_id: str,
    reply_messages: list[dict[str, Any]],
) -> None:
    record = _case_image_send_record(state, reply_messages)
    if not memory_store:
        record["status"] = "skipped"
        record["reason"] = "memory_store_unavailable"
        state["case_image_send_record"] = record
        _append_case_image_trace(state, record)
        return
    if not record.get("document_ids"):
        record["status"] = "skipped"
        record["reason"] = record.get("reason") or "no_case_images_matched"
        state["case_image_send_record"] = record
        _append_case_image_trace(state, record)
        return
    try:
        saved = memory_store.record_case_images_sent(
            customer_id,
            document_ids=record["document_ids"],
            image_urls=record["image_urls"],
            request_id=str(state.get("request_id") or ""),
        )
        record.update(saved)
    except Exception as exc:
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    state["case_image_send_record"] = record
    _append_case_image_trace(state, record)


def _case_image_send_record(state: AgentState, reply_messages: list[dict[str, Any]]) -> dict[str, Any]:
    case_by_url = _case_documents_by_image_url(state)
    image_urls = [_message_image_url(message) for message in reply_messages if isinstance(message, dict)]
    image_urls = [url for url in image_urls if url]
    matched_ids: list[str] = []
    matched_urls: list[str] = []
    unmatched_urls: list[str] = []
    for image_url in image_urls:
        doc_id = case_by_url.get(_normalize_url(image_url), "")
        if doc_id:
            if doc_id not in matched_ids:
                matched_ids.append(doc_id)
            matched_urls.append(image_url)
        else:
            unmatched_urls.append(image_url)
    return {
        "image_message_count": len(image_urls),
        "document_ids": matched_ids,
        "image_urls": matched_urls,
        "unmatched_image_urls": unmatched_urls,
        "candidate_document_ids": sorted(set(case_by_url.values())),
    }


def _case_documents_by_image_url(state: AgentState) -> dict[str, str]:
    structured = ((state.get("fact_envelope") or {}).get("structured_facts") or {})
    case_facts = structured.get("case_facts") if isinstance(structured, dict) else []
    mapping: dict[str, str] = {}
    for fact in case_facts if isinstance(case_facts, list) else []:
        if not isinstance(fact, dict):
            continue
        image_url = str(fact.get("image_url") or "").strip()
        document_id = str(fact.get("document_id") or fact.get("documentId") or "").strip()
        if image_url and document_id:
            mapping[_normalize_url(image_url)] = document_id
    return mapping


def _message_image_url(message: dict[str, Any]) -> str:
    if str(message.get("type") or "") != "image":
        return ""
    content = message.get("content")
    if isinstance(content, dict):
        for key in ("url", "text"):
            value = str(content.get(key) or "").strip()
            if value:
                return value
        return ""
    return str(content or "").strip()


def _normalize_url(value: str) -> str:
    return html.unescape(str(value or "").strip())


def _append_case_image_trace(state: AgentState, result: dict[str, Any]) -> None:
    started = time.perf_counter()
    entry = {
        "node": "case_image_send_record",
        "started_at": utc_now_iso(),
        "input_snapshot": compact(
            {
                "image_message_count": result.get("image_message_count", 0),
                "candidate_document_ids": result.get("candidate_document_ids", []),
            }
        ),
        "tool_calls": [{"name": "record_case_images_sent", "output": compact(result)}],
        "error": result.get("error"),
        "output_snapshot": compact(result),
    }
    entry["finished_at"] = utc_now_iso()
    entry["duration_ms"] = int((time.perf_counter() - started) * 1000)
    state.setdefault("trace", []).append(entry)


def _customer_store_knowledge_meta(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    stores = value.get("stores") if isinstance(value.get("stores"), list) else []
    extras = value.get("appointment_extra_stores") if isinstance(value.get("appointment_extra_stores"), list) else []
    return {
        "store_count": len(stores),
        "appointment_extra_store_count": len(extras),
        "source": value.get("source", ""),
        "error": value.get("error", ""),
    }


def _sales_talk_reference_meta(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    items = value.get("items") if isinstance(value.get("items"), list) else []
    return {
        "source": value.get("source", ""),
        "query": value.get("query", ""),
        "item_count": len(items),
        "error": value.get("error", ""),
    }
