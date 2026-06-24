from __future__ import annotations

import asyncio
import html
import time
from contextlib import suppress
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
from app.services.platform_reply_coordinator import PlatformReplyCoordinator, PlatformReplyRecord
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
        platform_reply_coordinator: PlatformReplyCoordinator | None = None,
    ) -> None:
        self._full_graph = full_graph
        self._planner_graph = planner_graph or full_graph
        self._finalize_graph = finalize_graph
        self._trace_logger = trace_logger
        self._repository = repository
        self._outreach_send_client = outreach_send_client
        self._memory_store = memory_store
        self._platform_reply_coordinator = platform_reply_coordinator

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
        decision = (
            await self._platform_reply_coordinator.begin(request, request_id=request_id, request_context=request_context)
            if self._platform_reply_coordinator
            else None
        )
        if decision and not decision.should_run_graph:
            state = self._initial_state(request, request_id, request_context)
            state["reply_messages"] = []
            state["reply_source"] = "platform_filtered"
            state["reply_control"] = self._platform_reply_coordinator.control_for_decision(decision)
            _set_sync_return(state, "empty", [])
            return self._persist_and_build_response(
                request=request,
                request_id=request_id,
                conversation_id=conversation_id,
                final_state=state,
                allow_empty_reply=True,
            )

        effective_request = request
        effective_context = request_context
        control_record: PlatformReplyRecord | None = None
        if decision:
            control_record = decision.record
            effective_context = decision.effective_request_context
            effective_request = request.model_copy(
                update={
                    "content": decision.effective_content,
                    "request_context": effective_context,
                }
            )
        initial_state = self._initial_state(effective_request, request_id, effective_context)
        if decision and self._platform_reply_coordinator:
            initial_state["reply_control"] = self._platform_reply_coordinator.control_for_decision(decision)

        try:
            planner_state = await self._run_planner_graph_with_preemption(initial_state, control_record)
        except Exception as exc:
            if self._platform_reply_coordinator:
                await self._platform_reply_coordinator.complete(control_record)
            self._handle_graph_exception(initial_state, conversation_id, exc)
        _preserve_reply_control(planner_state, initial_state)
        if control_record and self._platform_reply_coordinator and not await self._platform_reply_coordinator.is_latest(control_record):
            planner_state = self._superseded_state(initial_state, control_record)
            return self._persist_and_build_response(
                request=request,
                request_id=request_id,
                conversation_id=conversation_id,
                final_state=planner_state,
                allow_empty_reply=True,
            )

        sync_messages = _planner_sync_reply_messages(planner_state)
        planner_state["reply_messages"] = sync_messages
        planner_state["sync_reply_messages"] = sync_messages
        planner_state["reply_source"] = _platform_reply_source(planner_state)
        should_finalize = _should_run_async_finalize(planner_state)
        planner_state["async_final_reply"] = {
            "scheduled": should_finalize,
            "status": "scheduled" if should_finalize else "not_required",
        }
        _set_sync_return(planner_state, _sync_return_type(planner_state), sync_messages)

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
                control_record=control_record,
                background_tasks=background_tasks,
            )
        elif self._platform_reply_coordinator:
            await self._platform_reply_coordinator.complete(control_record)
        return response

    async def _run_planner_graph_with_preemption(
        self,
        initial_state: AgentState,
        control_record: PlatformReplyRecord | None,
    ) -> AgentState:
        if not control_record:
            return await self._planner_graph.ainvoke(initial_state)
        graph_task = asyncio.create_task(self._planner_graph.ainvoke(initial_state))
        cancel_task = asyncio.create_task(control_record.cancel_event.wait())
        done, pending = await asyncio.wait({graph_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
        if cancel_task in done and control_record.cancel_event.is_set():
            graph_task.cancel()
            graph_task.add_done_callback(_consume_task_result)
            return self._superseded_state(initial_state, control_record)
        cancel_task.cancel()
        with suppress(asyncio.CancelledError):
            await cancel_task
        for task in pending:
            task.cancel()
        return await graph_task

    def _superseded_state(self, initial_state: AgentState, control_record: PlatformReplyRecord) -> AgentState:
        state: AgentState = dict(initial_state)
        state["reply_messages"] = []
        state["reply_source"] = "platform_superseded"
        state["async_final_reply"] = {"scheduled": False, "status": "superseded"}
        if self._platform_reply_coordinator:
            state["reply_control"] = self._platform_reply_coordinator.control_for_superseded(control_record)
        _set_sync_return(state, "empty", [])
        return state

    def _schedule_async_finalize_and_send(
        self,
        *,
        request: ChatRequest,
        conversation_id: str,
        planner_state: AgentState,
        control_record: PlatformReplyRecord | None = None,
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
                if self._platform_reply_coordinator and not await self._platform_reply_coordinator.is_latest(control_record):
                    skipped = _async_superseded_result()
                    final_state["async_final_reply"] = skipped
                    _set_async_final_control(final_state, skipped)
                    _append_async_send_trace(final_state, skipped)
                    self._save_state(conversation_id, final_state)
                    return
                final_state = await self._finalize_graph.ainvoke(final_state)
                _preserve_reply_control(final_state, planner_state)
                messages = final_state.get("reply_messages") if isinstance(final_state.get("reply_messages"), list) else []
                if self._platform_reply_coordinator and not await self._platform_reply_coordinator.is_latest(control_record):
                    skipped = {**_async_superseded_result(), "reply_messages": messages}
                    final_state["async_final_reply"] = skipped
                    _set_async_final_control(final_state, skipped)
                    _append_async_send_trace(final_state, skipped)
                    self._save_state(conversation_id, final_state)
                    return
                if not messages:
                    final_state["async_final_reply"] = {
                        "scheduled": True,
                        "status": "skipped",
                        "reason": "empty_final_reply_messages",
                    }
                    _set_async_final_control(final_state, final_state["async_final_reply"])
                    _append_async_send_trace(final_state, final_state["async_final_reply"])
                    self._save_state(conversation_id, final_state)
                    return
                send_result = await self._send_async_reply(request, final_state, messages)
                send_result["reply_messages"] = messages
                final_state["async_final_reply"] = send_result
                _set_async_final_control(final_state, send_result)
                _append_async_send_trace(final_state, send_result)
                if send_result.get("status") == "sent" and not bool(final_state.get("test_isolated")):
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
                _set_async_final_control(final_state, error)
                final_state.setdefault("errors", []).append({"node": "async_final_reply", "message": "async_final_reply_failed", "detail": error["error"]})
                _append_async_send_trace(final_state, error)
                self._save_state(conversation_id, final_state)
            finally:
                if self._platform_reply_coordinator:
                    await self._platform_reply_coordinator.complete(control_record)

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
        test_isolated = bool(request_context.get("test_isolated"))
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
            "test_isolated": test_isolated,
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
        if reply_messages and not bool(final_state.get("test_isolated")):
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
        elif reply_messages:
            final_state["case_image_send_record"] = {
                "status": "skipped",
                "reason": "test_isolated",
                "image_message_count": len(
                    [message for message in reply_messages if message.type == "image"]
                ),
            }
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
                "case_image_send_record": final_state.get("case_image_send_record", {}),
                "model_usage": model_usage["calls"],
                "token_usage": model_usage["summary"],
                "tool_calls": collect_tool_calls(final_state.get("trace", [])),
                "planner_source": final_state.get("planner_source", ""),
                "planner_decision": final_state.get("planner_decision", ""),
                "planner_stage": final_state.get("planner_stage", ""),
                "planner_sub_rule_id": final_state.get("planner_sub_rule_id", ""),
                "conversion_stage": final_state.get("conversion_stage", ""),
                "customer_type": final_state.get("customer_type", ""),
                "main_blocker": final_state.get("main_blocker", ""),
                "next_step": final_state.get("next_step", ""),
                "policy_id": final_state.get("policy_id", ""),
                "policy_family_id": final_state.get("policy_family_id", ""),
                "exact_policy_id": final_state.get("exact_policy_id", ""),
                "policy_match_level": final_state.get("policy_match_level", ""),
                "policy_version": final_state.get("policy_version", ""),
                "reply_source": final_state.get("reply_source", ""),
                "postprocess_changed": bool(final_state.get("postprocess_changed")),
                "postprocess_reasons": final_state.get("postprocess_reasons", []),
                "async_final_reply": final_state.get("async_final_reply", {}),
                "reply_control": final_state.get("reply_control", {}),
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


def _sync_return_type(state: AgentState) -> str:
    source = str(state.get("reply_source") or "")
    if source == "planner_transition_reply":
        return "transition_reply"
    if source == "planner_direct_reply":
        return "direct_reply"
    return "empty" if not state.get("reply_messages") else "direct_reply"


def _preserve_reply_control(state: AgentState, fallback_state: AgentState) -> None:
    if not isinstance(state.get("reply_control"), dict) and isinstance(fallback_state.get("reply_control"), dict):
        state["reply_control"] = dict(fallback_state["reply_control"])


def _set_sync_return(state: AgentState, return_type: str, reply_messages: list[dict[str, Any]]) -> None:
    control = state.get("reply_control") if isinstance(state.get("reply_control"), dict) else {}
    control["sync_return"] = {
        "type": return_type,
        "reply_messages": reply_messages,
    }
    state["reply_control"] = control


def _set_async_final_control(state: AgentState, result: dict[str, Any]) -> None:
    control = state.get("reply_control") if isinstance(state.get("reply_control"), dict) else {}
    control["async_final"] = {
        "scheduled": bool(result.get("scheduled")),
        "status": str(result.get("status") or ""),
        "reason": result.get("reason", ""),
        "error": result.get("error", ""),
        "reply_messages": result.get("reply_messages", []),
        "send_payload": result.get("send_payload", {}),
        "send_response": result.get("response", {}),
        "payload_message_count": result.get("payload_message_count", 0),
    }
    state["reply_control"] = control


def _async_superseded_result() -> dict[str, Any]:
    return {
        "scheduled": True,
        "status": "superseded",
        "reason": "newer_customer_message_preempted_async_final_reply",
        "reply_messages": [],
    }


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    with suppress(asyncio.CancelledError, Exception):
        task.result()


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

