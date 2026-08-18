from __future__ import annotations

import asyncio
import html
import time
from contextlib import suppress
from typing import Any
from uuid import uuid4

from app.chat_request_context import (
    build_request_context,
    conversation_id_from_request,
    conversation_title,
    is_isolated_v2_test_request,
    is_platform_recalled_message,
)
from app.chat_runtime_helpers import failed_state_from_exception, safe_repository_call
from app.chat_runtime_metrics import collect_model_usage, collect_tool_calls
from app.config import Settings
from app.graph.nodes.activity_intro_image import activity_intro_image_url
from app.graph.planner.runtime_plan import planner_public_route
from app.graph.state import AgentState
from app.schemas import ChatRequest, ChatResponse, ReplyMessage
from app.services.customer_payment_state import payment_fact_from_image
from app.services.customer_scope import customer_scope_from_state
from app.services.memory_store import CustomerMemoryStore
from app.services.outreach_send_client import OutreachSendClient
from app.services.platform_reply_coordinator import PlatformReplyCoordinator, PlatformReplyRecord
from app.services.runtime_budget import build_runtime_budget, graph_deadline_monotonic, runtime_budget_snapshot
from app.services.sop_execution_service import SopExecutionService, is_platform_auto_opening_message
from app.services.storage import AppRepository
from app.services.store_fact_integrity import store_fact_is_valid
from app.services.trace_logger import TraceLogger, compact, utc_now_iso


class ChatRuntime:
    def __init__(
        self,
        *,
        full_graph: Any,
        trace_logger: TraceLogger,
        repository: AppRepository,
        commit_graph: Any | None = None,
        outreach_send_client: OutreachSendClient | None = None,
        memory_store: CustomerMemoryStore | None = None,
        platform_reply_coordinator: PlatformReplyCoordinator | None = None,
        sop_execution_service: SopExecutionService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._full_graph = full_graph
        self._commit_graph = commit_graph
        self._trace_logger = trace_logger
        self._repository = repository
        self._outreach_send_client = outreach_send_client
        self._memory_store = memory_store
        self._platform_reply_coordinator = platform_reply_coordinator
        self._sop_execution_service = sop_execution_service
        self._settings = settings
        self._platform_request_tasks: dict[str, asyncio.Task[ChatResponse]] = {}
        self._platform_request_results: dict[str, tuple[float, ChatResponse]] = {}
        self._platform_request_tasks_lock = asyncio.Lock()

    async def run_chat(self, request: ChatRequest) -> ChatResponse:
        request_id = str(uuid4())
        request_context = build_request_context(request)
        request_context["memory_persist_allowed"] = False
        conversation_id = self._prepare_conversation(request, request_id, request_context)
        self._start_run_tracking(
            request=request,
            request_id=request_id,
            conversation_id=conversation_id,
            request_context=request_context,
        )
        initial_state = self._initial_state(request, request_id, request_context)

        try:
            final_state = await self._invoke_graph_with_budget(self._full_graph, initial_state, phase="full")
        except Exception as exc:
            final_state = self._handle_graph_exception(initial_state, exc)

        return self._persist_and_build_response(
            request=request,
            request_id=request_id,
            conversation_id=conversation_id,
            final_state=final_state,
            allow_empty_reply=False,
        )

    async def run_platform_reply(self, request: ChatRequest, background_tasks: Any | None = None) -> ChatResponse:
        request_context = build_request_context(request)
        request_identity = _platform_request_identity(request, request_context)
        if not request_identity:
            return await self._run_platform_reply_once(request, background_tasks)

        async with self._platform_request_tasks_lock:
            cutoff = time.monotonic() - 900.0
            self._platform_request_results = {
                key: value
                for key, value in self._platform_request_results.items()
                if value[0] >= cutoff
            }
            cached = self._platform_request_results.get(request_identity)
            if cached:
                return cached[1]
            task = self._platform_request_tasks.get(request_identity)
            if task is None:
                task = asyncio.create_task(self._run_platform_reply_once(request, background_tasks))
                self._platform_request_tasks[request_identity] = task
        try:
            response = await asyncio.shield(task)
            async with self._platform_request_tasks_lock:
                self._platform_request_results[request_identity] = (time.monotonic(), response)
            return response
        finally:
            if task.done():
                async with self._platform_request_tasks_lock:
                    if self._platform_request_tasks.get(request_identity) is task:
                        self._platform_request_tasks.pop(request_identity, None)

    async def _run_platform_reply_once(
        self,
        request: ChatRequest,
        background_tasks: Any | None = None,
    ) -> ChatResponse:
        request_id = str(uuid4())
        request_context = build_request_context(request)
        request_context["test_isolated"] = is_isolated_v2_test_request(request, request_context)
        request_context["memory_persist_allowed"] = not request_context["test_isolated"]
        conversation_id = self._prepare_conversation(request, request_id, request_context)
        self._start_run_tracking(
            request=request,
            request_id=request_id,
            conversation_id=conversation_id,
            request_context=request_context,
        )
        decision = (
            await self._platform_reply_coordinator.begin(request, request_id=request_id, request_context=request_context)
            if self._platform_reply_coordinator
            else None
        )
        if decision and not decision.should_run_graph:
            state = self._initial_state(request, request_id, request_context)
            state["reply_messages"] = []
            state["reply_source"] = (
                "platform_superseded"
                if decision.mode == "input_batch_superseded"
                else "platform_filtered"
            )
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

        # A recalled customer message is a platform protocol event, not a
        # customer utterance. It must not consume model capacity or produce a
        # customer-visible reply.
        if is_platform_recalled_message(effective_request.content):
            initial_state["reply_messages"] = []
            initial_state["reply_source"] = "platform_recalled_message"
            initial_state.setdefault("trace", []).append(
                {
                    "node": "platform_protocol_filter",
                    "decision": "no_reply",
                    "reason": "customer_message_recalled",
                }
            )
            _set_sync_return(initial_state, "empty", [])
            if self._platform_reply_coordinator:
                await self._platform_reply_coordinator.complete(control_record)
            return self._persist_and_build_response(
                request=request,
                request_id=request_id,
                conversation_id=conversation_id,
                final_state=initial_state,
                allow_empty_reply=True,
            )

        # WeCom's automatic opening remains a protocol-level special case.
        # Every ordinary customer message enters the same parallel evidence
        # graph; Gate no longer commits tasks before Reply has validated the
        # final customer-visible response.
        if is_platform_auto_opening_message(effective_request.content):
            sop_gate = await self._evaluate_sop_gate(effective_request, request_id, effective_context)
            initial_state["sop_gate"] = sop_gate
            _append_sop_gate_trace(initial_state, sop_gate)
            if sop_gate.get("send_sop"):
                final_state = self._sop_reply_state(initial_state, sop_gate)
                if self._platform_reply_coordinator:
                    await self._platform_reply_coordinator.complete(control_record)
                return self._persist_and_build_response(
                    request=request,
                    request_id=request_id,
                    conversation_id=conversation_id,
                    final_state=final_state,
                    allow_empty_reply=True,
                )
            if _sop_gate_terminal_no_reply(sop_gate):
                terminal_state = dict(initial_state)
                terminal_state["reply_messages"] = []
                terminal_state["reply_source"] = str(sop_gate.get("mode") or "sop_gate_no_reply")
                if self._platform_reply_coordinator:
                    await self._platform_reply_coordinator.complete(control_record)
                return self._persist_and_build_response(
                    request=request,
                    request_id=request_id,
                    conversation_id=conversation_id,
                    final_state=terminal_state,
                    allow_empty_reply=True,
                )

        try:
            final_state = await self._run_graph_with_preemption(
                self._full_graph,
                initial_state,
                control_record,
                phase="full",
            )
        except Exception as exc:
            final_state = self._handle_graph_exception(initial_state, exc)
        _preserve_reply_control(final_state, initial_state)
        if (
            control_record
            and self._platform_reply_coordinator
            and await self._platform_reply_coordinator.is_superseded(control_record)
        ):
            final_state = self._superseded_state(initial_state, control_record)
            await self._platform_reply_coordinator.complete(control_record)
            return self._persist_and_build_response(
                request=request,
                request_id=request_id,
                conversation_id=conversation_id,
                final_state=final_state,
                allow_empty_reply=True,
            )

        final_state = await self._commit_after_reply_validation(final_state)

        final_state["sync_reply_messages"] = list(final_state.get("reply_messages") or [])
        final_state.setdefault("async_final_reply", {"scheduled": False, "status": "not_required"})
        _set_sync_return(final_state, _sync_return_type(final_state), final_state["sync_reply_messages"])
        response = self._persist_and_build_response(
            request=request,
            request_id=request_id,
            conversation_id=conversation_id,
            final_state=final_state,
            allow_empty_reply=False,
        )
        if self._platform_reply_coordinator:
            await self._platform_reply_coordinator.complete(control_record)
        return response

    async def _commit_after_reply_validation(self, state: AgentState) -> AgentState:
        if self._commit_graph is None or not state.get("reply_messages"):
            return state
        commit_state: AgentState = dict(state)
        commit_state["trace"] = list(state.get("trace") or [])
        commit_state["errors"] = list(state.get("errors") or [])
        try:
            return await self._invoke_graph_with_budget(self._commit_graph, commit_state, phase="commit")
        except Exception as exc:
            commit_state.setdefault("errors", []).append(
                {
                    "node": "commit_coordinator",
                    "message": "deferred_commit_failed_after_valid_reply",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            commit_state["commit_result"] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
            return commit_state

    async def _evaluate_sop_gate(
        self,
        request: ChatRequest,
        request_id: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._sop_execution_service:
            return {"mode": "skipped", "send_sop": False, "reason": "sop_execution_service_missing"}
        return await self._sop_execution_service.evaluate_chat_gate(
            request,
            request_id=request_id,
            request_context=request_context,
        )

    @staticmethod
    def _sop_reply_state(initial_state: AgentState, sop_gate: dict[str, Any]) -> AgentState:
        state: AgentState = dict(initial_state)
        messages = sop_gate.get("reply_messages") if isinstance(sop_gate.get("reply_messages"), list) else []
        state["reply_messages"] = messages
        state["sync_reply_messages"] = messages
        state["reply_source"] = "sop_gate"
        state["planner_decision"] = "direct_reply"
        state["planner_stage"] = "SOP"
        state["planner_sub_rule_id"] = str(sop_gate.get("sop_pack_id") or "")
        state["async_final_reply"] = {
            "scheduled": bool(sop_gate.get("need_ai_reply")),
            "status": "scheduled" if sop_gate.get("need_ai_reply") else "not_required",
            "reason": "sop_gate_requested_ai_reply" if sop_gate.get("need_ai_reply") else "",
        }
        _set_sync_return(state, "sop_reply", messages)
        return state

    async def _run_graph_with_preemption(
        self,
        graph: Any,
        initial_state: AgentState,
        control_record: PlatformReplyRecord | None,
        *,
        phase: str,
    ) -> AgentState:
        if not control_record:
            return await self._invoke_graph_with_budget(graph, initial_state, phase=phase)
        graph_task = asyncio.create_task(
            self._invoke_graph_with_budget(graph, initial_state, phase=phase)
        )
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
        if (
            bool(request_context.get("memory_persist_allowed"))
            and not bool(request_context.get("test_isolated"))
            and str(request.wechat or "").strip()
        ):
            cancel_outreach = getattr(self._repository, "cancel_outreach_for_customer_reply", None)
            if callable(cancel_outreach):
                safe_repository_call(
                    cancel_outreach,
                    customer_id=str(request.customer_id or ""),
                    corp_id=str(request.corp_id or ""),
                    wechat=str(request.wechat or ""),
                    external_userid=str(request.external_userid or ""),
                    request_id=request_id,
                )
        return conversation_id

    async def _invoke_graph_with_budget(
        self,
        graph: Any,
        state: AgentState,
        *,
        phase: str,
    ) -> AgentState:
        self._update_run_progress(str(state.get("request_id") or ""), phase)
        deadline = graph_deadline_monotonic(
            state,
            phase=phase,
            strong_reply=_has_structured_professional_assist(state),
        )
        if deadline is None:
            return await graph.ainvoke(state)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"{phase} graph round deadline exhausted")
        try:
            return await asyncio.wait_for(graph.ainvoke(state), timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"{phase} graph round deadline exhausted after {remaining:.1f}s") from exc

    def _start_run_tracking(
        self,
        *,
        request: ChatRequest,
        request_id: str,
        conversation_id: str,
        request_context: dict[str, Any],
    ) -> None:
        start_run = getattr(self._repository, "start_run", None)
        if not callable(start_run):
            return
        safe_repository_call(
            start_run,
            request_id=request_id,
            conversation_id=conversation_id,
            customer_id=str(request.customer_id or ""),
            input_snapshot=_run_tracking_input_snapshot(request, request_context),
            interface_version=str(request_context.get("interface_version") or "v1"),
        )

    def _update_run_progress(self, request_id: str, phase: str) -> None:
        if not request_id:
            return
        update_run_progress = getattr(self._repository, "update_run_progress", None)
        if callable(update_run_progress):
            safe_repository_call(update_run_progress, request_id=request_id, phase=phase)

    def _initial_state(self, request: ChatRequest, request_id: str, request_context: dict[str, Any]) -> AgentState:
        test_isolated = bool(request_context.get("test_isolated"))
        state: AgentState = {
            "request_id": request_id,
            "customer_id": request.customer_id,
            "corp_id": request.corp_id,
            "content": request.content,
            "conversation_history": request.conversation_history,
            "file_image": request.file_image,
            "image_urls": _image_urls_from_request(request, request_context),
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
            "memory_persist_allowed": bool(request_context.get("memory_persist_allowed")),
            "runtime_budget": build_runtime_budget(self._settings),
            "trace": [],
            "errors": [],
        }
        scope = customer_scope_from_state(state)
        state["sales_contact_key"] = scope.sales_contact_key
        state["global_customer_key"] = scope.global_customer_key
        state["customer_scope"] = scope.as_dict()
        return state

    def _handle_graph_exception(self, initial_state: AgentState, exc: Exception) -> AgentState:
        failed_state = failed_state_from_exception(initial_state, exc)
        failed_state["reply_messages"] = _deterministic_final_fallback_messages(failed_state)
        failed_state["reply_source"] = "deterministic_runtime_exception_fallback"
        return failed_state

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
        if _final_state_superseded(final_state):
            final_state["reply_messages"] = []
            final_state["sync_reply_messages"] = []
            final_state["reply_source"] = "platform_superseded"
            final_state["async_final_reply"] = {"scheduled": False, "status": "superseded"}
            _set_sync_return(final_state, "empty", [])
            allow_empty_reply = True
        raw_reply_messages = final_state.get("reply_messages") or []
        if not raw_reply_messages and not allow_empty_reply:
            final_state.setdefault("errors", []).append(
                {
                    "stage": "final_reply",
                    "error": "Final reply model failed or produced no customer-facing reply.",
                }
            )
            raw_reply_messages = _deterministic_final_fallback_messages(final_state)
            final_state["reply_messages"] = raw_reply_messages
            final_state["reply_source"] = "deterministic_empty_reply_fallback"
            _set_sync_return(final_state, "final_reply", raw_reply_messages)
        reply_messages = [ReplyMessage(**message) for message in raw_reply_messages]
        reply_message_dicts = [message.model_dump() for message in reply_messages]
        if reply_messages and not bool(final_state.get("test_isolated")):
            safe_repository_call(
                self._repository.add_assistant_message,
                conversation_id=conversation_id,
                request_id=request_id,
                reply_messages=reply_message_dicts,
            )
            if _memory_persistence_allowed(final_state):
                _record_authoritative_payment_fact(
                    self._memory_store,
                    final_state,
                    customer_id=str(final_state.get("sales_contact_key") or ""),
                )
                _record_sent_case_images(
                    self._memory_store,
                    final_state,
                    customer_id=str(final_state.get("sales_contact_key") or ""),
                    reply_messages=reply_message_dicts,
                )
                _record_activity_intro_image(
                    self._memory_store,
                    final_state,
                    customer_id=str(final_state.get("sales_contact_key") or ""),
                    reply_messages=reply_message_dicts,
                    send_mode="sync",
                )
                _record_visible_store_facts(
                    self._memory_store,
                    final_state,
                    customer_id=str(final_state.get("sales_contact_key") or ""),
                    reply_messages=reply_message_dicts,
                )
                try:
                    _record_v2_reply_model_observation(
                        self._memory_store,
                        final_state,
                        customer_id=str(final_state.get("sales_contact_key") or ""),
                    )
                except Exception as exc:
                    final_state.setdefault("warnings", []).append(
                        {
                            "node": "v2_reply_model_observation",
                            "message": "observation_persistence_failed",
                            "detail": f"{type(exc).__name__}: {exc}",
                        }
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
                "store_fact_memory_record": final_state.get("store_fact_memory_record", {}),
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
                "reply_action": final_state.get("reply_action", "none"),
                "reply_action_reason": final_state.get("reply_action_reason", ""),
                "reply_sales_judgment": final_state.get("reply_sales_judgment", {}),
                "reply_deposit_evidence": final_state.get("reply_deposit_evidence", {}),
                "selected_content_ids": final_state.get("selected_content_ids", []),
                "reply_content_decisions": final_state.get("reply_content_decisions", []),
                "content_selection_metrics": final_state.get("content_selection_metrics", {}),
                "parallel_branch_metrics": final_state.get("parallel_branch_metrics", {}),
                "fallback_source": final_state.get("fallback_source", ""),
                "postprocess_changed": bool(final_state.get("postprocess_changed")),
                "postprocess_reasons": final_state.get("postprocess_reasons", []),
                "async_final_reply": final_state.get("async_final_reply", {}),
                "reply_control": final_state.get("reply_control", {}),
                "sop_gate": final_state.get("sop_gate", {}),
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


def _image_urls_from_request(request: ChatRequest, request_context: dict[str, Any]) -> list[str]:
    merged = request_context.get("merged_image_urls")
    values = list(merged) if isinstance(merged, list) else []
    values.append(str(request.file_image or ""))
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        url = str(value or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        output.append(url)
    return output[-3:]


def _run_tracking_input_snapshot(request: ChatRequest, request_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": request.content,
        "customer_id": request.customer_id,
        "corp_id": request.corp_id,
        "conversation_history": request.conversation_history,
        "file_image": bool(request.file_image),
        "user_id": request.user_id,
        "wechat": request.wechat,
        "external_userid": request.external_userid,
        "request_context": request_context,
    }


def _deterministic_final_fallback_messages(state: AgentState) -> list[dict[str, Any]]:
    state["fallback_source"] = str(state.get("fallback_source") or "deterministic_runtime_fallback")
    state["fallback_failure_node"] = str(
        (state.get("errors") or [{}])[-1].get("node")
        if isinstance((state.get("errors") or [{}])[-1], dict)
        else "runtime"
    )
    state["fallback_retry_count"] = len(state.get("recovery_attempts") or [])
    state["fallback_violation"] = str(state.get("recovery_reason") or "")[:500]
    state["fallback_remaining_budget"] = runtime_budget_snapshot(state, tier="reply")
    return [{"type": "text", "order": 1, "content": {"text": "您稍等一下"}}]


def _final_state_superseded(state: AgentState) -> bool:
    control = state.get("reply_control") if isinstance(state.get("reply_control"), dict) else {}
    if str(control.get("mode") or "") == "superseded":
        return True
    async_final = state.get("async_final_reply") if isinstance(state.get("async_final_reply"), dict) else {}
    if str(async_final.get("status") or "") == "superseded":
        return True
    if str(state.get("reply_source") or "") == "platform_superseded":
        return True
    return False


def _has_structured_professional_assist(state: AgentState) -> bool:
    handoff = state.get("handoff") if isinstance(state.get("handoff"), dict) else {}
    if handoff.get("needed"):
        return True
    reply_strategy = state.get("reply_strategy") if isinstance(state.get("reply_strategy"), dict) else {}
    risk_hold = reply_strategy.get("risk_hold")
    if isinstance(risk_hold, dict) and (
        str(risk_hold.get("risk_hold") or "") == "health_check_required"
        or str(risk_hold.get("severity") or "") == "hard"
    ):
        return True
    for key in ("required_tools", "planner_tool_calls"):
        tools = state.get(key) if isinstance(state.get(key), list) else []
        if any(isinstance(item, dict) and str(item.get("name") or "") == "professional_assist" for item in tools):
            return True
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope, dict) else {}
    professional_assist = structured.get("professional_assist") if isinstance(structured, dict) else {}
    return isinstance(professional_assist, dict) and professional_assist.get("status") == "requested"


def _sop_gate_terminal_no_reply(sop_gate: dict[str, Any]) -> bool:
    return (
        str(sop_gate.get("mode") or "") == "ignored_platform_auto_message"
        and not sop_gate.get("send_sop")
        and not sop_gate.get("need_ai_reply")
    )


def _sync_return_type(state: AgentState) -> str:
    control = state.get("reply_control") if isinstance(state.get("reply_control"), dict) else {}
    sync_return = control.get("sync_return") if isinstance(control.get("sync_return"), dict) else {}
    if sync_return.get("type") == "final_reply":
        return "final_reply"
    return "empty" if not state.get("reply_messages") else "direct_reply"


def _append_sop_gate_trace(state: AgentState, result: dict[str, Any]) -> None:
    started = time.perf_counter()
    entry = {
        "node": "sop_gate",
        "started_at": utc_now_iso(),
        "input_snapshot": compact(
            {
                "content": state.get("content", ""),
                "customer_id": state.get("customer_id", ""),
                "external_userid": state.get("external_userid", ""),
                "skip_sop_gate": (state.get("request_context") or {}).get("skip_sop_gate")
                if isinstance(state.get("request_context"), dict)
                else False,
            }
        ),
        "tool_calls": [],
        "error": result.get("error", ""),
        "output_snapshot": compact(
            {
                "mode": result.get("mode", ""),
                "send_sop": result.get("send_sop", False),
                "sop_pack_id": result.get("sop_pack_id", ""),
                "need_ai_reply": result.get("need_ai_reply", False),
                "unfinished_count": result.get("unfinished_count", 0),
                "reason": result.get("reason", ""),
                "model_usage": result.get("model_usage", {}),
                "task": result.get("task", {}),
            }
        ),
    }
    entry["finished_at"] = utc_now_iso()
    entry["duration_ms"] = int(result.get("duration_ms") or ((time.perf_counter() - started) * 1000))
    state.setdefault("trace", []).append(entry)


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


def _memory_persistence_allowed(state: AgentState) -> bool:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    return bool(request_context.get("memory_persist_allowed")) and bool(str(state.get("sales_contact_key") or "").strip())


def _record_authoritative_payment_fact(
    memory_store: CustomerMemoryStore | None,
    state: AgentState,
    *,
    customer_id: str,
) -> None:
    """Record current structured payment evidence without interpreting customer text."""
    image_info = state.get("image_info") if isinstance(state.get("image_info"), dict) else {}
    fact = payment_fact_from_image(image_info)
    record: dict[str, Any] = {
        "status": "skipped",
        "deposit_state": str(fact.get("deposit_state") or ""),
        "source": str(fact.get("source") or ""),
    }
    if not memory_store:
        record["reason"] = "memory_store_unavailable"
    elif not customer_id:
        record["reason"] = "missing_sales_contact_key"
    elif not fact.get("deposit_state"):
        record["reason"] = "no_current_authoritative_payment_fact"
    else:
        try:
            saved = memory_store.record_authoritative_payment_fact(
                customer_id,
                deposit_state=str(fact.get("deposit_state") or ""),
                source=str(fact.get("source") or ""),
                request_id=str(state.get("request_id") or ""),
                amount=fact.get("amount"),
                order_id=str(fact.get("order_id") or ""),
                order_no=str(fact.get("order_no") or ""),
                interface_version=_interface_version_from_state(state),
            )
            record.update(saved)
        except Exception as exc:
            record["status"] = "error"
            record["error"] = f"{type(exc).__name__}: {exc}"
    state["authoritative_payment_memory_record"] = record
    state.setdefault("trace", []).append(
        {
            "node": "authoritative_payment_memory_record",
            "started_at": utc_now_iso(),
            "finished_at": utc_now_iso(),
            "duration_ms": 0,
            "input_snapshot": {
                "deposit_state": record.get("deposit_state"),
                "source": record.get("source"),
            },
            "output_snapshot": {
                "status": record.get("status"),
                "reason": record.get("reason"),
                "event_id": record.get("event_id"),
                "error": record.get("error"),
            },
        }
    )


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    with suppress(asyncio.CancelledError, Exception):
        task.result()


def _platform_request_identity(request: ChatRequest, request_context: dict[str, Any]) -> str:
    msgid = str(request_context.get("msgid") or "").strip()
    if not msgid:
        return ""
    corp_id = str(request_context.get("corp_id") or request.corp_id or "").strip()
    wechat = str(request_context.get("wechat") or request.wechat or "").strip()
    external_userid = str(
        request_context.get("external_userid") or request.external_userid or ""
    ).strip()
    if not (corp_id and wechat and external_userid):
        return ""
    return f"{corp_id}:wechat:{wechat}:external:{external_userid}:msgid:{msgid}"


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
    if not record.get("document_ids") and not record.get("image_urls"):
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
            interface_version=_interface_version_from_state(state),
        )
        record.update(saved)
    except Exception as exc:
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    state["case_image_send_record"] = record
    _append_case_image_trace(state, record)


def _case_image_send_record(state: AgentState, reply_messages: list[dict[str, Any]]) -> dict[str, Any]:
    case_by_url = _case_documents_by_image_url(state)
    effect_asset_urls = _selected_effect_asset_image_urls(state)
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
        elif _normalize_url(image_url) in effect_asset_urls:
            matched_urls.append(image_url)
        else:
            unmatched_urls.append(image_url)
    return {
        "image_message_count": len(image_urls),
        "document_ids": matched_ids,
        "image_urls": matched_urls,
        "unmatched_image_urls": unmatched_urls,
        "candidate_document_ids": sorted(set(case_by_url.values())),
        "selected_effect_asset_ids": sorted(
            {
                asset_id
                for asset_id in effect_asset_urls.values()
                if str(asset_id or "").strip()
            }
        ),
    }


def _selected_effect_asset_image_urls(state: AgentState) -> dict[str, str]:
    selected_ids = {
        str(item).strip()
        for item in state.get("selected_content_ids") or state.get("reply_selected_content_ids") or []
        if str(item or "").strip()
    }
    if not selected_ids:
        return {}
    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    output: dict[str, str] = {}
    for candidate in joined.get("content_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content_id = str(candidate.get("content_id") or candidate.get("id") or "").strip()
        if content_id not in selected_ids:
            continue
        if str(candidate.get("asset_role") or "").strip() != "effect_evidence":
            continue
        messages = candidate.get("messages")
        if not isinstance(messages, list):
            messages = candidate.get("reply_messages") if isinstance(candidate.get("reply_messages"), list) else []
        for message in messages:
            if not isinstance(message, dict):
                continue
            image_url = _message_image_url(message)
            if image_url:
                output[_normalize_url(image_url)] = content_id
    return output


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


def _record_activity_intro_image(
    memory_store: CustomerMemoryStore | None,
    state: AgentState,
    *,
    customer_id: str,
    reply_messages: list[dict[str, Any]],
    send_mode: str,
) -> None:
    record = _activity_intro_image_record_plan(state, reply_messages, send_mode=send_mode)
    if not memory_store:
        record["status"] = "skipped"
        record["reason"] = "memory_store_unavailable"
        state["activity_intro_image_send_record"] = record
        _append_activity_intro_image_trace(state, record)
        return
    if not record.get("image_url"):
        record["status"] = "skipped"
        record["reason"] = record.get("reason") or "no_activity_intro_image"
        state["activity_intro_image_send_record"] = record
        _append_activity_intro_image_trace(state, record)
        return
    try:
        saved = memory_store.record_activity_intro_image_sent(
            customer_id,
            image_url=str(record["image_url"]),
            request_id=str(state.get("request_id") or ""),
            send_mode=send_mode,
            interface_version=_interface_version_from_state(state),
        )
        record.update(saved)
    except Exception as exc:
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    state["activity_intro_image_send_record"] = record
    _append_activity_intro_image_trace(state, record)


def _record_v2_reply_model_observation(
    memory_store: CustomerMemoryStore | None,
    state: AgentState,
    *,
    customer_id: str,
) -> None:
    """Append Reply's own short observation after a successful visible reply."""

    if not memory_store or not state.get("evidence_join") or not customer_id:
        return
    judgment = (
        state.get("reply_sales_judgment")
        if isinstance(state.get("reply_sales_judgment"), dict)
        else {}
    )
    memory_store.record_v2_reply_model_observation(
        customer_id,
        request_id=str(state.get("request_id") or ""),
        primary_objective=str(judgment.get("primary_objective") or ""),
        customer_friction_observation=str(
            judgment.get("customer_friction_observation") or ""
        ),
        interface_version=str(
            (state.get("request_context") if isinstance(state.get("request_context"), dict) else {}).get(
                "interface_version"
            )
            or "v2"
        ),
    )


def _activity_intro_image_record_plan(
    state: AgentState,
    reply_messages: list[dict[str, Any]],
    *,
    send_mode: str,
) -> dict[str, Any]:
    target_url = activity_intro_image_url(state)
    image_urls = [_message_image_url(message) for message in reply_messages if isinstance(message, dict)]
    image_urls = [url for url in image_urls if url]
    matched = ""
    target = _normalize_url(target_url)
    if target:
        for image_url in image_urls:
            if _normalize_url(image_url) == target:
                matched = image_url
                break
    return {
        "image_url": matched,
        "activity_intro_image_url": target_url,
        "image_message_count": len(image_urls),
        "send_mode": send_mode,
    }


def _append_activity_intro_image_trace(state: AgentState, result: dict[str, Any]) -> None:
    started = time.perf_counter()
    entry = {
        "node": "activity_intro_image_send_record",
        "started_at": utc_now_iso(),
        "input_snapshot": compact(
            {
                "image_url": result.get("image_url", ""),
                "send_mode": result.get("send_mode", ""),
            }
        ),
        "tool_calls": [{"name": "record_activity_intro_image_sent", "output": compact(result)}],
        "error": result.get("error"),
        "output_snapshot": compact(result),
    }
    entry["finished_at"] = utc_now_iso()
    entry["duration_ms"] = int((time.perf_counter() - started) * 1000)
    state.setdefault("trace", []).append(entry)


def _record_visible_store_facts(
    memory_store: CustomerMemoryStore | None,
    state: AgentState,
    *,
    customer_id: str,
    reply_messages: list[dict[str, Any]],
) -> None:
    record = _store_fact_record_plan(state, reply_messages)
    if not memory_store:
        record["status"] = "skipped"
        record["reason"] = "memory_store_unavailable"
        state["store_fact_memory_record"] = record
        _append_store_fact_trace(state, record)
        return
    if not record.get("records"):
        record["status"] = "skipped"
        record["reason"] = record.get("reason") or "no_clear_store_fact"
        state["store_fact_memory_record"] = record
        _append_store_fact_trace(state, record)
        return
    saved_records: list[dict[str, Any]] = []
    try:
        for item in record["records"]:
            if not isinstance(item, dict):
                continue
            saved = memory_store.record_store_fact(
                customer_id,
                store=item.get("store") if isinstance(item.get("store"), dict) else {},
                event_type=str(item.get("event_type") or ""),
                request_id=str(state.get("request_id") or ""),
                interface_version=_interface_version_from_state(state),
                store_search_evidence=(
                    item.get("store_search_evidence")
                    if isinstance(item.get("store_search_evidence"), dict)
                    else None
                ),
            )
            saved_records.append(saved)
        record["status"] = "recorded" if any(item.get("status") == "recorded" for item in saved_records) else "skipped"
        record["saved_records"] = saved_records
    except Exception as exc:
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    state["store_fact_memory_record"] = record
    _append_store_fact_trace(state, record)


def _store_fact_record_plan(state: AgentState, reply_messages: list[dict[str, Any]]) -> dict[str, Any]:
    store_address_ids = _store_address_message_ids(reply_messages)
    store_search_evidence = _store_search_evidence_from_state(state)
    records: list[dict[str, Any]] = []
    missing_store_ids: list[str] = []
    for store_id in store_address_ids:
        store = _store_by_id(state, store_id)
        if store and store_fact_is_valid(store):
            records.append(
                {
                    "event_type": "store_address_sent",
                    "store": store,
                    "store_search_evidence": store_search_evidence,
                }
            )
        else:
            missing_store_ids.append(store_id)
    if records:
        return {
            "records": records,
            "store_address_message_ids": store_address_ids,
            "missing_store_ids": missing_store_ids,
        }

    matched_store = _clear_matched_store_from_tool_facts(state)
    if matched_store and store_fact_is_valid(matched_store):
        return {
            "records": [{"event_type": "store_matched", "store": matched_store}],
            "store_address_message_ids": store_address_ids,
            "missing_store_ids": missing_store_ids,
        }
    return {
        "records": [],
        "store_address_message_ids": store_address_ids,
        "missing_store_ids": missing_store_ids,
    }


def _store_search_evidence_from_state(state: AgentState) -> dict[str, Any]:
    structured = _structured_facts_from_state(state)
    resolution = (
        structured.get("store_resolution_fact")
        if isinstance(structured.get("store_resolution_fact"), dict)
        else {}
    )
    return {
        key: resolution.get(key)
        for key in (
            "raw_place",
            "normalized_query",
            "location_evidence",
            "resolved_admin_level",
            "province",
            "city",
            "district",
            "township",
            "candidate_search_complete",
            "distance_ranking_available",
            "distance_ranking_complete",
            "ranked_candidate_count",
            "unranked_candidate_count",
            "visible_candidate_count",
            "recommended_store_id",
            "delivery_store_ids",
            "ranking_method",
            "customer_claim_level",
        )
        if resolution.get(key) not in (None, "", [], {})
    }


def _store_address_message_ids(reply_messages: list[dict[str, Any]]) -> list[str]:
    store_ids: list[str] = []
    for message in reply_messages:
        if not isinstance(message, dict) or str(message.get("type") or "") != "store_address":
            continue
        content = message.get("content")
        store_id = str(content.get("store_id") if isinstance(content, dict) else content or "").strip()
        if store_id and store_id not in store_ids:
            store_ids.append(store_id)
    return store_ids


def _clear_matched_store_from_tool_facts(state: AgentState) -> dict[str, Any]:
    structured = _structured_facts_from_state(state)
    recommended = structured.get("recommended_store") if isinstance(structured, dict) else {}
    recommended_id = str(recommended.get("id") or recommended.get("store_id") or "").strip() if isinstance(recommended, dict) else ""
    if recommended_id:
        hydrated = _store_by_id(state, recommended_id)
        return hydrated or _normalize_store_record(recommended)

    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    lookup = tool_results.get("customer_store_lookup") if isinstance(tool_results.get("customer_store_lookup"), dict) else {}
    if not lookup:
        return {}
    candidates = lookup.get("candidate_stores") if isinstance(lookup.get("candidate_stores"), list) else []
    stores = lookup.get("stores") if isinstance(lookup.get("stores"), list) else []
    source = candidates or stores
    if len(source) != 1 or not isinstance(source[0], dict):
        return {}
    return _normalize_store_record(source[0])


def _store_by_id(state: AgentState, store_id: str) -> dict[str, Any]:
    target = str(store_id or "").strip()
    if not target:
        return {}
    for store in _iter_store_records(state):
        normalized = _normalize_store_record(store)
        if str(normalized.get("store_id") or "") == target:
            return normalized
    return {}


def _iter_store_records(state: AgentState) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    for key in ("customer_store_lookup", "distance_calculate"):
        value = tool_results.get(key)
        if not isinstance(value, dict):
            continue
        for list_key in ("stores", "candidate_stores", "ranked_stores"):
            items = value.get(list_key) if isinstance(value.get(list_key), list) else []
            records.extend(item for item in items if isinstance(item, dict))

    structured = _structured_facts_from_state(state)
    if isinstance(structured, dict):
        recommended = structured.get("recommended_store")
        if isinstance(recommended, dict):
            records.append(recommended)
        store_facts = structured.get("store_facts") if isinstance(structured.get("store_facts"), list) else []
        records.extend(item for item in store_facts if isinstance(item, dict))

    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    for list_key in ("stores", "appointment_extra_stores"):
        items = knowledge.get(list_key) if isinstance(knowledge.get(list_key), list) else []
        records.extend(item for item in items if isinstance(item, dict))
    return records


def _structured_facts_from_state(state: AgentState) -> dict[str, Any]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    return structured


def _normalize_store_record(store: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(store, dict):
        return {}
    return {
        "store_id": str(store.get("store_id") or store.get("id") or "").strip(),
        "store_name": str(store.get("store_name") or store.get("name") or "").strip(),
        "province": str(store.get("province") or "").strip(),
        "city": str(store.get("city") or "").strip(),
        "district": str(store.get("district") or "").strip(),
        "store_address": str(store.get("store_address") or store.get("address") or "").strip(),
        "business_hours": str(store.get("business_hours") or "").strip(),
        "parking": str(store.get("parking") or store.get("parking_name") or store.get("parking_address") or "").strip(),
        "parking_name": str(store.get("parking_name") or "").strip(),
        "parking_address": str(store.get("parking_address") or "").strip(),
        "map_url": str(store.get("map_url") or "").strip(),
        "store_fact_integrity": str(store.get("store_fact_integrity") or "valid").strip(),
        "store_fact_integrity_violations": list(store.get("store_fact_integrity_violations") or []),
        "store_fact_integrity_warnings": list(store.get("store_fact_integrity_warnings") or []),
    }


def _append_store_fact_trace(state: AgentState, result: dict[str, Any]) -> None:
    started = time.perf_counter()
    entry = {
        "node": "store_fact_memory_record",
        "started_at": utc_now_iso(),
        "input_snapshot": compact(
            {
                "store_address_message_ids": result.get("store_address_message_ids", []),
                "record_count": len(result.get("records") or []),
            }
        ),
        "tool_calls": [{"name": "record_store_fact", "output": compact(result)}],
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


def _interface_version_from_state(state: AgentState) -> str:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    version = str(request_context.get("interface_version") or "v1").strip().lower()
    return version if version in {"v1", "v2", "v3"} else "v1"
