from __future__ import annotations

import asyncio
import html
import time
from contextlib import suppress
from typing import Any
from uuid import uuid4

from app.chat_request_context import build_request_context, conversation_id_from_request, conversation_title
from app.chat_runtime_helpers import failed_state_from_exception, safe_repository_call
from app.chat_runtime_metrics import collect_model_usage, collect_tool_calls
from app.config import Settings
from app.graph.nodes.activity_intro_image import activity_intro_image_url, append_activity_intro_image
from app.graph.nodes.conversation_history_fetch import (
    conversation_fetch_params,
    newer_conversation_activity_after_trigger,
)
from app.graph.nodes.reply_delivery_manifest import build_sop_delivery_manifest
from app.graph.planner.runtime_plan import planner_public_route
from app.graph.state import AgentState
from app.schemas import ChatRequest, ChatResponse, ReplyMessage
from app.services.customer_scope import customer_scope_from_state
from app.services.memory_store import CustomerMemoryStore
from app.services.outreach_send_client import OutreachSendClient
from app.services.platform_reply_coordinator import PlatformReplyCoordinator, PlatformReplyRecord
from app.services.reply_governance import reply_governance_flags
from app.services.runtime_budget import build_runtime_budget, graph_deadline_monotonic, runtime_budget_snapshot
from app.services.sop_execution_service import SopExecutionService
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
        planner_graph: Any | None = None,
        finalize_graph: Any | None = None,
        outreach_send_client: OutreachSendClient | None = None,
        memory_store: CustomerMemoryStore | None = None,
        platform_reply_coordinator: PlatformReplyCoordinator | None = None,
        sop_execution_service: SopExecutionService | None = None,
        profile_event_extractor: Any | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._full_graph = full_graph
        self._planner_graph = planner_graph or full_graph
        self._finalize_graph = finalize_graph
        self._trace_logger = trace_logger
        self._repository = repository
        self._outreach_send_client = outreach_send_client
        self._memory_store = memory_store
        self._platform_reply_coordinator = platform_reply_coordinator
        self._sop_execution_service = sop_execution_service
        self._profile_event_extractor = profile_event_extractor
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
        request_context["memory_persist_allowed"] = True
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

        self._update_run_progress(request_id, "sop_gate")
        sop_gate = await self._evaluate_sop_gate(effective_request, request_id, effective_context)
        initial_state["sop_gate"] = sop_gate
        initial_state["sop_gate_decision"] = {
            "route": str(sop_gate.get("route") or sop_gate.get("mode") or ""),
            "coverage": str(sop_gate.get("coverage") or ""),
            "reason": str(sop_gate.get("reason") or ""),
            "scene_decision": dict(sop_gate.get("scene_decision") or {})
            if isinstance(sop_gate.get("scene_decision"), dict)
            else {},
            "task": dict(sop_gate.get("active_task") or {})
            if isinstance(sop_gate.get("active_task"), dict)
            else {},
            "priority_question_id": str(sop_gate.get("priority_question_id") or ""),
            "selected_scene_id": str(
                sop_gate.get("selected_scene_id") or sop_gate.get("priority_question_id") or ""
            ),
            "resume_stage": str(sop_gate.get("resume_stage") or ""),
            "sop_pack_id": str(sop_gate.get("sop_pack_id") or ""),
            "sop_message_types": [
                str(message.get("type") or "")
                for message in (sop_gate.get("reply_messages") or [])
                if isinstance(message, dict) and str(message.get("type") or "")
            ],
            "sop_image_count": sum(
                1
                for message in (sop_gate.get("reply_messages") or [])
                if isinstance(message, dict) and str(message.get("type") or "") == "image"
            ),
            "source": "chat_sop_gate_model",
            "safety_decision": dict(sop_gate.get("safety_decision") or {})
            if isinstance(sop_gate.get("safety_decision"), dict)
            else {},
            "scene_decision": dict(sop_gate.get("scene_decision") or {})
            if isinstance(sop_gate.get("scene_decision"), dict)
            else {},
        }
        initial_state["sop_progress_evidence"] = dict(sop_gate.get("sop_progress_evidence") or {})
        initial_state["sop_gate_candidate_messages"] = [
            dict(message)
            for message in (sop_gate.get("reply_messages") or [])
            if isinstance(message, dict)
        ]
        initial_state["sop_delivery_manifest"] = build_sop_delivery_manifest(sop_gate)
        _append_sop_gate_trace(initial_state, sop_gate)
        if _sop_gate_terminal_no_reply(sop_gate):
            terminal_state = dict(initial_state)
            terminal_state["reply_messages"] = []
            terminal_state["sync_reply_messages"] = []
            terminal_state["reply_source"] = str(sop_gate.get("mode") or "sop_gate_no_reply")
            terminal_state["planner_decision"] = "no_reply"
            terminal_state["planner_stage"] = "SOP_GATE"
            terminal_state["planner_sub_rule_id"] = str(sop_gate.get("reason") or "")
            terminal_state["async_final_reply"] = {
                "scheduled": False,
                "status": "not_required",
                "reason": str(sop_gate.get("reason") or ""),
            }
            _set_sync_return(terminal_state, "empty", [])
            if self._platform_reply_coordinator:
                await self._platform_reply_coordinator.complete(control_record)
            return self._persist_and_build_response(
                request=request,
                request_id=request_id,
                conversation_id=conversation_id,
                final_state=terminal_state,
                allow_empty_reply=True,
            )

        if _sop_gate_direct_reply(sop_gate):
            direct_state = self._sop_reply_state(initial_state, sop_gate)
            direct_state = await self._apply_platform_freshness_guard(
                request=effective_request,
                state=direct_state,
                control_record=control_record,
            )
            response = self._persist_and_build_response(
                request=request,
                request_id=request_id,
                conversation_id=conversation_id,
                final_state=direct_state,
                allow_empty_reply=False,
            )
            if self._platform_reply_coordinator:
                await self._platform_reply_coordinator.complete(control_record)
            return response

        try:
            planner_state = await self._run_planner_graph_with_preemption(initial_state, control_record)
        except Exception as exc:
            if self._platform_reply_coordinator:
                await self._platform_reply_coordinator.complete(control_record)
            planner_state = self._handle_graph_exception(initial_state, exc)
        _preserve_reply_control(planner_state, initial_state)
        if (
            control_record
            and self._platform_reply_coordinator
            and await self._platform_reply_coordinator.is_superseded(control_record)
        ):
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
        _set_sync_return(planner_state, _sync_return_type(planner_state), sync_messages)
        if should_finalize:
            final_state = await self._run_finalize_sync(
                request=request,
                conversation_id=conversation_id,
                planner_state=planner_state,
                control_record=control_record,
            )
            return self._persist_and_build_response(
                request=request,
                request_id=request_id,
                conversation_id=conversation_id,
                final_state=final_state,
                allow_empty_reply=False,
            )
        planner_state["async_final_reply"] = {
            "scheduled": False,
            "status": "not_required",
        }
        _set_async_final_control(planner_state, planner_state["async_final_reply"])
        planner_state = await self._apply_platform_freshness_guard(
            request=request,
            state=planner_state,
            control_record=control_record,
        )
        response = self._persist_and_build_response(
            request=request,
            request_id=request_id,
            conversation_id=conversation_id,
            final_state=planner_state,
            allow_empty_reply=False,
        )
        if self._platform_reply_coordinator:
            await self._platform_reply_coordinator.complete(control_record)
        if sync_messages:
            self._schedule_background_profile_update(
                conversation_id=conversation_id,
                state=planner_state,
                background_tasks=background_tasks,
                reason="planner_sync_reply",
            )
        return response

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

    async def _run_planner_graph_with_preemption(
        self,
        initial_state: AgentState,
        control_record: PlatformReplyRecord | None,
    ) -> AgentState:
        if not control_record:
            return await self._invoke_graph_with_budget(self._planner_graph, initial_state, phase="planner")
        graph_task = asyncio.create_task(
            self._invoke_graph_with_budget(self._planner_graph, initial_state, phase="planner")
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

    async def _run_finalize_sync(
        self,
        *,
        request: ChatRequest,
        conversation_id: str,
        planner_state: AgentState,
        control_record: PlatformReplyRecord | None = None,
    ) -> AgentState:
        if not self._finalize_graph:
            planner_state["async_final_reply"] = {"scheduled": False, "status": "skipped", "reason": "finalize_graph_missing"}
            _set_async_final_control(planner_state, planner_state["async_final_reply"])
            if self._platform_reply_coordinator:
                await self._platform_reply_coordinator.complete(control_record)
            return planner_state

        final_state: AgentState = dict(planner_state)
        final_state["trace"] = list(planner_state.get("trace") or [])
        final_state["errors"] = list(planner_state.get("errors") or [])
        try:
            if self._platform_reply_coordinator and await self._platform_reply_coordinator.is_superseded(control_record):
                state = self._superseded_state(planner_state, control_record) if control_record else final_state
                if self._platform_reply_coordinator:
                    await self._platform_reply_coordinator.complete(control_record)
                return state
            final_state = await self._invoke_graph_with_budget(self._finalize_graph, final_state, phase="reply")
            _preserve_reply_control(final_state, planner_state)
            messages = final_state.get("reply_messages") if isinstance(final_state.get("reply_messages"), list) else []
            if self._platform_reply_coordinator and await self._platform_reply_coordinator.is_superseded(control_record):
                state = self._superseded_state(planner_state, control_record) if control_record else final_state
                if self._platform_reply_coordinator:
                    await self._platform_reply_coordinator.complete(control_record)
                return state
            final_state = await self._apply_platform_freshness_guard(
                request=request,
                state=final_state,
                control_record=control_record,
            )
            if _final_state_superseded(final_state):
                return final_state
            messages = final_state.get("reply_messages") if isinstance(final_state.get("reply_messages"), list) else []
            if not messages and not bool(final_state.get("reply_blocked")):
                messages = _deterministic_final_fallback_messages(final_state)
                final_state["reply_messages"] = messages
                final_state["reply_source"] = "deterministic_sync_empty_reply_fallback"
                final_state.setdefault("warnings", []).append(
                    {"node": "sync_final_reply", "message": "empty_final_reply_recovered_before_return"}
                )
            result = {
                "scheduled": False,
                "status": "blocked" if final_state.get("reply_blocked") else "completed_sync",
                "reason": "reply_contract_blocked" if final_state.get("reply_blocked") else "platform_sync_final_reply",
                "reply_messages": messages,
            }
            final_state["async_final_reply"] = result
            _set_sync_return(final_state, "final_reply", messages)
            _set_async_final_control(final_state, result)
            _append_sync_final_trace(final_state, result)
            return final_state
        except Exception as exc:
            final_state.setdefault("errors", []).append(
                {"node": "sync_final_reply", "message": "sync_final_reply_failed", "detail": f"{type(exc).__name__}: {exc}"}
            )
            recovered = self._handle_graph_exception(final_state, exc)
            _preserve_reply_control(recovered, final_state)
            result = {
                "scheduled": False,
                "status": "error_recovered_sync",
                "error": f"{type(exc).__name__}: {exc}",
                "reply_messages": recovered.get("reply_messages", []),
            }
            recovered["async_final_reply"] = result
            _set_sync_return(recovered, "final_reply", recovered.get("reply_messages", []))
            _set_async_final_control(recovered, result)
            _append_sync_final_trace(recovered, result)
            return recovered
        finally:
            if self._platform_reply_coordinator:
                await self._platform_reply_coordinator.complete(control_record)

    async def _apply_platform_freshness_guard(
        self,
        *,
        request: ChatRequest,
        state: AgentState,
        control_record: PlatformReplyRecord | None,
    ) -> AgentState:
        """Drop a completed reply when the platform conversation already moved on."""

        if not control_record or not self._outreach_send_client:
            return state
        if self._platform_reply_coordinator and not await self._platform_reply_coordinator.is_latest(control_record):
            return self._superseded_state(state, control_record)

        params = conversation_fetch_params(
            state,
            request_context=state.get("request_context") if isinstance(state.get("request_context"), dict) else {},
            limit=50,
        )
        started_at = time.perf_counter()
        try:
            result = await self._outreach_send_client.fetch_conversation(**params)
        except Exception as exc:
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        messages = result.get("messages") if isinstance(result, dict) and isinstance(result.get("messages"), list) else []
        comparison = newer_conversation_activity_after_trigger(
            messages,
            trigger_message_id=control_record.message_id,
            trigger_events=control_record.merged_input_events,
        )
        fetch_status = str(result.get("status") or "failed") if isinstance(result, dict) else "failed"
        freshness = {
            "status": "checked" if fetch_status == "ok" and comparison.get("status") != "unavailable" else "unavailable",
            "fetch_status": fetch_status,
            "trigger_message_id": control_record.message_id,
            "newer_customer_message": bool(comparison.get("newer_customer_message")),
            "newer_assistant_message": bool(comparison.get("newer_assistant_message")),
            "newer_message_refs": list(comparison.get("newer_customer_message_refs") or []),
            "newer_assistant_message_refs": list(comparison.get("newer_assistant_message_refs") or []),
            "reason": str(comparison.get("reason") or result.get("reason") or result.get("error") or "")[:500],
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
        }
        state["reply_freshness_check"] = freshness
        _append_platform_freshness_trace(state, freshness)
        if fetch_status == "ok" and (
            comparison.get("newer_customer_message") or comparison.get("newer_assistant_message")
        ):
            stale_state = self._superseded_state(state, control_record)
            stale_state["trace"] = list(state.get("trace") or [])
            stale_state["errors"] = list(state.get("errors") or [])
            stale_state["warnings"] = list(state.get("warnings") or [])
            stale_state["reply_freshness_check"] = {
                **freshness,
                "status": "superseded",
                "reason": (
                    "newer_customer_message_detected_after_trigger"
                    if comparison.get("newer_customer_message")
                    else "newer_assistant_message_detected_after_trigger"
                ),
            }
            stale_state["reply_source"] = "platform_superseded"
            return stale_state
        if freshness["status"] == "unavailable":
            state.setdefault("warnings", []).append(
                {
                    "node": "platform_reply_freshness",
                    "message": "freshness_check_unavailable",
                    "detail": freshness,
                }
            )
        return state

    async def _run_sop_ai_reply_sync(
        self,
        *,
        request: ChatRequest,
        conversation_id: str,
        initial_state: AgentState,
        sop_state: AgentState,
        control_record: PlatformReplyRecord | None = None,
    ) -> AgentState:
        async_state: AgentState = dict(initial_state)
        async_state["trace"] = list(initial_state.get("trace") or [])
        async_state["errors"] = list(initial_state.get("errors") or [])
        async_context = dict(async_state.get("request_context") if isinstance(async_state.get("request_context"), dict) else {})
        async_context["skip_sop_gate"] = True
        async_context["async_origin"] = "sop_gate_ai_reply"
        async_context["sync_final_mode"] = True
        async_state["request_context"] = async_context
        sop_messages = sop_state.get("reply_messages") if isinstance(sop_state.get("reply_messages"), list) else []
        try:
            if self._platform_reply_coordinator and await self._platform_reply_coordinator.is_superseded(control_record):
                state = self._superseded_state(initial_state, control_record) if control_record else async_state
                if self._platform_reply_coordinator:
                    await self._platform_reply_coordinator.complete(control_record)
                return state
            final_state = await self._invoke_graph_with_budget(self._full_graph, async_state, phase="full")
            _preserve_reply_control(final_state, initial_state)
            ai_messages = final_state.get("reply_messages") if isinstance(final_state.get("reply_messages"), list) else []
            if self._platform_reply_coordinator and await self._platform_reply_coordinator.is_superseded(control_record):
                state = self._superseded_state(initial_state, control_record) if control_record else final_state
                if self._platform_reply_coordinator:
                    await self._platform_reply_coordinator.complete(control_record)
                return state
            ai_reply_usable = _ai_reply_usable_before_sop(final_state, ai_messages)
            authorized_sop_messages = _payment_authorized_reply_messages(
                sop_messages,
                payment_decision=final_state.get("payment_decision"),
            )
            if ai_reply_usable:
                messages = _merge_ai_then_sop_reply_messages(
                    ai_messages,
                    authorized_sop_messages,
                    payment_decision=final_state.get("payment_decision"),
                )
                _confirm_deferred_chat_sop_task(
                    self._sop_execution_service,
                    sop_state,
                    request_id=str(final_state.get("request_id") or initial_state.get("request_id") or ""),
                    reply_messages=authorized_sop_messages,
                )
                result_reason = "ai_reply_then_sop_returned_with_response"
            elif authorized_sop_messages:
                messages = list(authorized_sop_messages)
                final_state["reply_source"] = "sop_gate_sync_sop_fallback_after_ai_unavailable"
                _confirm_deferred_chat_sop_task(
                    self._sop_execution_service,
                    sop_state,
                    request_id=str(final_state.get("request_id") or initial_state.get("request_id") or ""),
                    reply_messages=authorized_sop_messages,
                )
                final_state.setdefault("warnings", []).append(
                    {"node": "sop_gate_sync_ai_reply", "message": "sop_sent_after_ai_reply_unavailable"}
                )
                result_reason = "sop_returned_after_ai_reply_unavailable"
            else:
                messages = _deterministic_final_fallback_messages(final_state)
                final_state["reply_source"] = "deterministic_sop_sync_empty_ai_reply_fallback"
                _fail_deferred_chat_sop_task(
                    self._sop_execution_service,
                    sop_state,
                    error="ai_reply_unavailable_before_sop_send",
                )
                final_state.setdefault("warnings", []).append(
                    {"node": "sop_gate_sync_ai_reply", "message": "sop_withheld_because_ai_reply_unavailable"}
                )
                result_reason = "sop_withheld_after_empty_ai_reply"
            final_state["reply_messages"] = messages
            result = {
                "scheduled": False,
                "status": "completed_sync",
                "reason": result_reason,
                "reply_messages": messages,
            }
            final_state["async_final_reply"] = result
            _set_sync_return(final_state, "sop_reply_with_ai", messages)
            _set_async_final_control(final_state, result)
            _append_sync_final_trace(final_state, result)
            return final_state
        except Exception as exc:
            async_state.setdefault("errors", []).append(
                {"node": "sop_gate_sync_ai_reply", "message": "sop_gate_sync_ai_failed", "detail": f"{type(exc).__name__}: {exc}"}
            )
            fallback_messages = _deterministic_final_fallback_messages(async_state)
            async_state["reply_source"] = "deterministic_sop_sync_exception_fallback"
            _fail_deferred_chat_sop_task(
                self._sop_execution_service,
                sop_state,
                error=f"{type(exc).__name__}: {exc}",
            )
            result_reason = "sop_withheld_after_ai_exception"
            async_state["reply_messages"] = fallback_messages
            result = {
                "scheduled": False,
                "status": "error_recovered_sync",
                "reason": result_reason,
                "error": f"{type(exc).__name__}: {exc}",
                "reply_messages": fallback_messages,
            }
            async_state["async_final_reply"] = result
            _set_sync_return(async_state, "sop_reply_with_ai", fallback_messages)
            _set_async_final_control(async_state, result)
            _append_sync_final_trace(async_state, result)
            return async_state
        finally:
            if self._platform_reply_coordinator:
                await self._platform_reply_coordinator.complete(control_record)

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
                if self._platform_reply_coordinator and await self._platform_reply_coordinator.is_superseded(control_record):
                    skipped = _async_superseded_result()
                    final_state["async_final_reply"] = skipped
                    _set_async_final_control(final_state, skipped)
                    _append_async_send_trace(final_state, skipped)
                    self._save_state(conversation_id, final_state)
                    return
                final_state = await self._invoke_graph_with_budget(self._finalize_graph, final_state, phase="reply")
                _preserve_reply_control(final_state, planner_state)
                messages = final_state.get("reply_messages") if isinstance(final_state.get("reply_messages"), list) else []
                if self._platform_reply_coordinator and await self._platform_reply_coordinator.is_superseded(control_record):
                    skipped = {**_async_superseded_result(), "reply_messages": messages}
                    final_state["async_final_reply"] = skipped
                    _set_async_final_control(final_state, skipped)
                    _append_async_send_trace(final_state, skipped)
                    self._save_state(conversation_id, final_state)
                    return
                if not messages:
                    messages = _deterministic_final_fallback_messages(final_state)
                    final_state["reply_messages"] = messages
                    final_state["reply_source"] = "deterministic_async_empty_reply_fallback"
                    final_state.setdefault("warnings", []).append(
                        {"node": "async_final_reply", "message": "empty_final_reply_recovered_before_send"}
                    )
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
                    if _memory_persistence_allowed(final_state):
                        _record_sent_case_images(
                            self._memory_store,
                            final_state,
                            customer_id=str(final_state.get("sales_contact_key") or ""),
                            reply_messages=messages,
                        )
                        _record_activity_intro_image(
                            self._memory_store,
                            final_state,
                            customer_id=str(final_state.get("sales_contact_key") or ""),
                            reply_messages=messages,
                            send_mode="async",
                        )
                        _record_visible_store_facts(
                            self._memory_store,
                            final_state,
                            customer_id=str(final_state.get("sales_contact_key") or ""),
                            reply_messages=messages,
                        )
                self._save_state(conversation_id, final_state)
            except Exception as exc:
                error = {"scheduled": True, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
                final_state.setdefault("errors", []).append({"node": "async_final_reply", "message": "async_final_reply_failed", "detail": error["error"]})
                await self._recover_async_exception_and_send(
                    request=request,
                    conversation_id=conversation_id,
                    state=final_state,
                    original_error=error,
                )
                self._save_state(conversation_id, final_state)
            finally:
                if self._platform_reply_coordinator:
                    await self._platform_reply_coordinator.complete(control_record)

        if background_tasks is not None:
            background_tasks.add_task(runner)
        else:
            asyncio.create_task(runner())

    def _schedule_async_full_ai_and_send(
        self,
        *,
        request: ChatRequest,
        conversation_id: str,
        initial_state: AgentState,
        control_record: PlatformReplyRecord | None = None,
        background_tasks: Any | None = None,
    ) -> None:
        async def runner() -> None:
            async_state: AgentState = dict(initial_state)
            async_state["trace"] = list(initial_state.get("trace") or [])
            async_state["errors"] = list(initial_state.get("errors") or [])
            async_context = dict(async_state.get("request_context") if isinstance(async_state.get("request_context"), dict) else {})
            async_context["skip_sop_gate"] = True
            async_context["async_origin"] = "sop_gate_ai_reply"
            async_state["request_context"] = async_context
            try:
                if self._platform_reply_coordinator and await self._platform_reply_coordinator.is_superseded(control_record):
                    skipped = _async_superseded_result()
                    async_state["async_final_reply"] = skipped
                    _set_async_final_control(async_state, skipped)
                    _append_async_send_trace(async_state, skipped)
                    self._save_state(conversation_id, async_state)
                    return
                final_state = await self._invoke_graph_with_budget(self._full_graph, async_state, phase="full")
                _preserve_reply_control(final_state, initial_state)
                messages = final_state.get("reply_messages") if isinstance(final_state.get("reply_messages"), list) else []
                if self._platform_reply_coordinator and await self._platform_reply_coordinator.is_superseded(control_record):
                    skipped = {**_async_superseded_result(), "reply_messages": messages}
                    final_state["async_final_reply"] = skipped
                    _set_async_final_control(final_state, skipped)
                    _append_async_send_trace(final_state, skipped)
                    self._save_state(conversation_id, final_state)
                    return
                if not messages:
                    messages = _deterministic_final_fallback_messages(final_state)
                    final_state["reply_messages"] = messages
                    final_state["reply_source"] = "deterministic_async_empty_reply_fallback"
                    final_state.setdefault("warnings", []).append(
                        {"node": "sop_gate_async_ai_reply", "message": "empty_full_ai_reply_recovered_before_send"}
                    )
                send_result = await self._send_async_reply(request, final_state, messages)
                send_result["reply_messages"] = messages
                final_state["async_final_reply"] = send_result
                _set_async_final_control(final_state, send_result)
                _append_async_send_trace(final_state, send_result)
                if send_result.get("status") == "sent" and not bool(final_state.get("test_isolated")):
                    safe_repository_call(
                        self._repository.add_assistant_message,
                        conversation_id=conversation_id,
                        request_id=f"{final_state.get('request_id')}:sop_async",
                        reply_messages=messages,
                    )
                    if _memory_persistence_allowed(final_state):
                        _record_sent_case_images(
                            self._memory_store,
                            final_state,
                            customer_id=str(final_state.get("sales_contact_key") or ""),
                            reply_messages=messages,
                        )
                        _record_activity_intro_image(
                            self._memory_store,
                            final_state,
                            customer_id=str(final_state.get("sales_contact_key") or ""),
                            reply_messages=messages,
                            send_mode="async",
                        )
                        _record_visible_store_facts(
                            self._memory_store,
                            final_state,
                            customer_id=str(final_state.get("sales_contact_key") or ""),
                            reply_messages=messages,
                        )
                self._save_state(conversation_id, final_state)
            except Exception as exc:
                error = {"scheduled": True, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
                async_state.setdefault("errors", []).append({"node": "sop_gate_async_ai_reply", "message": "sop_gate_async_ai_failed", "detail": error["error"]})
                await self._recover_async_exception_and_send(
                    request=request,
                    conversation_id=conversation_id,
                    state=async_state,
                    original_error=error,
                )
                self._save_state(conversation_id, async_state)
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

    async def _recover_async_exception_and_send(
        self,
        *,
        request: ChatRequest,
        conversation_id: str,
        state: AgentState,
        original_error: dict[str, Any],
    ) -> None:
        messages = _deterministic_final_fallback_messages(state)
        state["reply_messages"] = messages
        state["reply_source"] = "deterministic_async_exception_fallback"
        try:
            send_result = await self._send_async_reply(request, state, messages)
            send_result["reply_messages"] = messages
            send_result["recovered_from"] = original_error.get("error", "")
        except Exception as send_exc:
            send_result = {
                **original_error,
                "scheduled": True,
                "status": "error",
                "send_error": f"{type(send_exc).__name__}: {send_exc}",
                "reply_messages": messages,
            }
        state["async_final_reply"] = send_result
        _set_async_final_control(state, send_result)
        _append_async_send_trace(state, send_result)
        if send_result.get("status") == "sent" and not bool(state.get("test_isolated")):
            safe_repository_call(
                self._repository.add_assistant_message,
                conversation_id=conversation_id,
                request_id=f"{state.get('request_id')}:async_fallback",
                reply_messages=messages,
            )

    def _schedule_background_profile_update(
        self,
        *,
        conversation_id: str,
        state: AgentState,
        background_tasks: Any | None = None,
        reason: str,
    ) -> None:
        if not self._profile_event_extractor:
            return
        if bool(state.get("test_isolated")) or not _memory_persistence_allowed(state):
            return

        async def runner() -> None:
            profile_state: AgentState = dict(state)
            profile_state["trace"] = list(state.get("trace") or [])
            profile_state.setdefault("background_profile_update", {})["scheduled_reason"] = reason
            try:
                output = await self._profile_event_extractor(profile_state)
                if isinstance(output, dict):
                    profile_state.update(
                        {
                            "profile_update": output.get("profile_update", {}),
                            "event_updates": output.get("event_updates", []),
                            "saved_memory": output.get("saved_memory", {}),
                            "memory_error": output.get("memory_error"),
                            "background_profile_update": {
                                "scheduled_reason": reason,
                                "status": "completed",
                                "profile_update": output.get("profile_update", {}),
                                "event_updates": output.get("event_updates", []),
                                "saved_memory": output.get("saved_memory", {}),
                                "memory_error": output.get("memory_error"),
                            },
                        }
                    )
                self._save_state(conversation_id, profile_state)
            except Exception as exc:
                profile_state["background_profile_update"] = {
                    "scheduled_reason": reason,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                profile_state.setdefault("errors", []).append(
                    {
                        "node": "background_profile_update",
                        "message": "background_profile_update_failed",
                        "detail": profile_state["background_profile_update"]["error"],
                    }
                )
                self._save_state(conversation_id, profile_state)

        if background_tasks is not None:
            background_tasks.add_task(runner)
        else:
            asyncio.create_task(runner())

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
            "reply_governance": reply_governance_flags(self._settings),
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
        gate = final_state.get("sop_gate") if isinstance(final_state.get("sop_gate"), dict) else {}
        gate_task = gate.get("task") if isinstance(gate.get("task"), dict) else {}
        if str(gate_task.get("status") or "") == "pending":
            reply_source = str(final_state.get("reply_source") or "")
            authorized_manifest = (
                final_state.get("authorized_sop_delivery_manifest")
                if isinstance(final_state.get("authorized_sop_delivery_manifest"), dict)
                else {}
            )
            manifest_fallback_sent = (
                reply_source == "deterministic_authorized_sop_manifest_fallback"
                and raw_reply_messages
                and authorized_manifest.get("active")
            )
            if (
                bool(final_state.get("reply_blocked"))
                or (final_state.get("errors") and not manifest_fallback_sent)
                or (reply_source.startswith("deterministic_") and not manifest_fallback_sent)
            ):
                _fail_deferred_chat_sop_task(
                    self._sop_execution_service,
                    final_state,
                    error=str(final_state.get("recovery_reason") or reply_source or "unified_reply_chain_failed"),
                )
            elif manifest_fallback_sent or (raw_reply_messages and authorized_manifest.get("active")):
                _confirm_deferred_chat_sop_task(
                    self._sop_execution_service,
                    final_state,
                    request_id=request_id,
                    reply_messages=[item for item in raw_reply_messages if isinstance(item, dict)],
                )
            else:
                delivery_decision = (
                    authorized_manifest.get("delivery_decision")
                    if isinstance(authorized_manifest.get("delivery_decision"), dict)
                    else {}
                )
                _fail_deferred_chat_sop_task(
                    self._sop_execution_service,
                    final_state,
                    error=(
                        "planner_"
                        + str(delivery_decision.get("action") or authorized_manifest.get("reason") or "sop_not_delivered")
                    ),
                )
        if bool(final_state.get("reply_blocked")):
            allow_empty_reply = True
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
        reply_messages = [ReplyMessage(**message) for message in raw_reply_messages]
        if reply_messages and not bool(final_state.get("test_isolated")):
            safe_repository_call(
                self._repository.add_assistant_message,
                conversation_id=conversation_id,
                request_id=request_id,
                reply_messages=[message.model_dump() for message in reply_messages],
            )
            if _memory_persistence_allowed(final_state):
                _record_sent_case_images(
                    self._memory_store,
                    final_state,
                    customer_id=str(final_state.get("sales_contact_key") or ""),
                    reply_messages=[message.model_dump() for message in reply_messages],
                )
                _record_activity_intro_image(
                    self._memory_store,
                    final_state,
                    customer_id=str(final_state.get("sales_contact_key") or ""),
                    reply_messages=[message.model_dump() for message in reply_messages],
                    send_mode="sync",
                )
                _record_visible_store_facts(
                    self._memory_store,
                    final_state,
                    customer_id=str(final_state.get("sales_contact_key") or ""),
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
                "fallback_source": final_state.get("fallback_source", ""),
                "postprocess_changed": bool(final_state.get("postprocess_changed")),
                "postprocess_reasons": final_state.get("postprocess_reasons", []),
                "async_final_reply": final_state.get("async_final_reply", {}),
                "reply_control": final_state.get("reply_control", {}),
                "sop_gate": final_state.get("sop_gate", {}),
                "reply_governance": final_state.get("reply_governance", {}),
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


def _planner_sync_reply_messages(state: AgentState) -> list[dict[str, Any]]:
    messages = state.get("planner_reply_messages") if isinstance(state.get("planner_reply_messages"), list) else []
    decision = str(state.get("planner_decision") or "")
    if decision == "need_tools":
        return []
    if decision == "direct_reply":
        violations = state.get("tool_policy_violations") if isinstance(state.get("tool_policy_violations"), list) else []
        if violations:
            _append_platform_sync_trace(
                state,
                {
                    "message": "planner_direct_reply_rejected",
                    "detail": {
                        "reason": "tool_policy_violations",
                        "violations": violations[:5],
                    },
                },
            )
            return []
        warnings: list[Any] = []
        output = append_activity_intro_image([item for item in messages if isinstance(item, dict)], state, warnings)
        if warnings:
            _append_platform_sync_trace(state, warnings[0] if isinstance(warnings[0], dict) else {})
        return output
    return []


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


def _platform_reply_source(state: AgentState) -> str:
    decision = str(state.get("planner_decision") or "").strip()
    if not state.get("reply_messages"):
        return "planner_no_reply" if decision == "no_reply" else "planner_empty_reply"
    if decision == "no_reply":
        return "planner_no_reply"
    if decision == "need_tools":
        return "planner_transition_reply"
    return "planner_direct_reply"


def _sop_gate_terminal_no_reply(sop_gate: dict[str, Any]) -> bool:
    return (
        str(sop_gate.get("mode") or "")
        in {"ignored_platform_auto_message", "safety_stop_no_reply", "safety_stop_contact"}
        and not sop_gate.get("send_sop")
        and not sop_gate.get("need_ai_reply")
    )


def _sop_gate_direct_reply(sop_gate: dict[str, Any]) -> bool:
    return (
        str(sop_gate.get("mode") or "") == "platform_auto_opening_sop"
        and str(sop_gate.get("delivery_mode") or "") == "configured_passthrough"
        and bool(sop_gate.get("send_sop"))
        and not bool(sop_gate.get("need_ai_reply"))
        and isinstance(sop_gate.get("reply_messages"), list)
        and bool(sop_gate.get("reply_messages"))
    )


def _sync_return_type(state: AgentState) -> str:
    source = str(state.get("reply_source") or "")
    if source == "planner_transition_reply":
        return "transition_reply"
    if source == "planner_direct_reply":
        return "direct_reply"
    return "empty" if not state.get("reply_messages") else "direct_reply"


def _append_platform_sync_trace(state: AgentState, warning: dict[str, Any]) -> None:
    started = time.perf_counter()
    entry = {
        "node": "platform_sync_reply",
        "started_at": utc_now_iso(),
        "input_snapshot": compact(
            {
                "planner_decision": state.get("planner_decision", ""),
                "planner_sub_rule_id": state.get("planner_sub_rule_id", ""),
            }
        ),
        "tool_calls": [],
        "error": "",
        "output_snapshot": compact(
            {
                "message": warning.get("message", ""),
                "detail": warning.get("detail", {}),
            }
        ),
    }
    entry["finished_at"] = utc_now_iso()
    entry["duration_ms"] = int((time.perf_counter() - started) * 1000)
    state.setdefault("trace", []).append(entry)


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


def _memory_persistence_allowed(state: AgentState) -> bool:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    return bool(request_context.get("memory_persist_allowed")) and bool(str(state.get("sales_contact_key") or "").strip())


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
    planner_decision = str(state.get("planner_decision") or "").strip()
    if planner_decision == "direct_reply":
        # Planner owns the decision; Reply owns all ordinary customer-visible text.
        # Keeping one finalization path prevents planner drafts from bypassing schema
        # and factual consistency checks.
        return True
    if planner_decision == "need_tools":
        # A malformed plan can request tools without producing an executable call.
        # Finalize it through Reply with the recorded violation instead of returning
        # an empty response when Planner repair exhausts its budget.
        return True
    return False


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


def _append_sync_final_trace(state: AgentState, result: dict[str, Any]) -> None:
    started = time.perf_counter()
    entry = {
        "node": "sync_final_reply_return",
        "started_at": utc_now_iso(),
        "input_snapshot": compact(
            {
                "reply_messages": len(state.get("reply_messages") or []),
                "request_id": state.get("request_id", ""),
            }
        ),
        "tool_calls": [],
        "error": result.get("error"),
        "output_snapshot": compact(result),
    }
    entry["finished_at"] = utc_now_iso()
    entry["duration_ms"] = int((time.perf_counter() - started) * 1000)
    state.setdefault("trace", []).append(entry)


def _append_platform_freshness_trace(state: AgentState, result: dict[str, Any]) -> None:
    entry = {
        "node": "platform_reply_freshness",
        "started_at": utc_now_iso(),
        "input_snapshot": compact(
            {
                "request_id": state.get("request_id", ""),
                "trigger_message_id": result.get("trigger_message_id", ""),
            }
        ),
        "tool_calls": [{"name": "ai_outreach_conversation", "output": compact(result)}],
        "error": result.get("reason") if result.get("status") == "unavailable" else None,
        "output_snapshot": compact(result),
    }
    entry["finished_at"] = utc_now_iso()
    entry["duration_ms"] = int(result.get("duration_ms") or 0)
    state.setdefault("trace", []).append(entry)


def _merge_reply_message_groups(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for group in groups:
        for message in group:
            if not isinstance(message, dict):
                continue
            copied = dict(message)
            copied["order"] = len(messages) + 1
            messages.append(copied)
    return messages


def _merge_ai_then_sop_reply_messages(
    ai_messages: list[dict[str, Any]],
    sop_messages: list[dict[str, Any]],
    *,
    payment_decision: Any = None,
) -> list[dict[str, Any]]:
    ai_messages = _payment_authorized_reply_messages(ai_messages, payment_decision=payment_decision)
    sop_messages = _payment_authorized_reply_messages(sop_messages, payment_decision=payment_decision)
    if not any(_message_type(message) == "text" and _message_text(message) for message in ai_messages):
        return _merge_reply_message_groups(ai_messages, sop_messages)

    sop_structural = [
        message
        for message in sop_messages
        if isinstance(message, dict) and _message_type(message) in {"image", "video", "store_address", "payment_collection", "human_handoff_notice"}
    ]
    if not sop_structural:
        return _merge_reply_message_groups(ai_messages, sop_messages)

    ai_text_count = sum(1 for message in ai_messages if _message_type(message) == "text" and _message_text(message))
    bridge_sop_text = _first_text_message(sop_messages) if ai_text_count <= 1 else None
    trailing_ai_text: dict[str, Any] | None = None
    ai_prefix = list(ai_messages)
    if ai_text_count > 1 and ai_prefix and _message_type(ai_prefix[-1]) == "text" and _message_text(ai_prefix[-1]):
        trailing_ai_text = ai_prefix.pop()

    # The final customer-visible turn has one transaction decision. Prefer the
    # AI card, which was produced from the latest context, over a static SOP
    # card and never expose conflicting amounts in the same turn.
    ai_has_payment = any(_message_type(message) == "payment_collection" for message in ai_messages)
    payment_kept = False
    merged: list[dict[str, Any]] = []
    seen = set()
    for message in [*ai_prefix, *([bridge_sop_text] if bridge_sop_text else []), *sop_structural[:3], *([trailing_ai_text] if trailing_ai_text else [])]:
        if not isinstance(message, dict):
            continue
        if _message_type(message) == "payment_collection":
            is_ai_message = message in ai_messages
            if payment_kept or (ai_has_payment and not is_ai_message):
                continue
            payment_kept = True
        identity = _message_identity(message)
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        copied = dict(message)
        copied["order"] = len(merged) + 1
        merged.append(copied)
    return merged


def _payment_authorized_reply_messages(
    messages: list[dict[str, Any]],
    *,
    payment_decision: Any,
) -> list[dict[str, Any]]:
    # Keep the helper's legacy behavior for isolated callers that do not pass a
    # planner decision. Runtime callers always pass the structured authority.
    if payment_decision is None:
        return list(messages)
    decision = payment_decision if isinstance(payment_decision, dict) else {}
    if str(decision.get("action") or "").strip() in {"send_now", "resend"}:
        return list(messages)
    return [
        message
        for message in messages
        if isinstance(message, dict) and _message_type(message) != "payment_collection"
    ]


def _message_type(message: dict[str, Any]) -> str:
    return str(message.get("type") or "").strip().lower()


def _first_text_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in messages:
        if isinstance(message, dict) and _message_type(message) == "text" and _message_text(message):
            return message
    return None


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or "").strip()
    return str(content or "").strip()


def _message_identity(message: dict[str, Any]) -> tuple[str, str]:
    msg_type = _message_type(message)
    content = message.get("content")
    if isinstance(content, dict):
        value = str(content.get("url") or content.get("store_id") or content.get("amount") or content.get("text") or "").strip()
    else:
        value = str(content or "").strip()
    return (msg_type, value)


def _ai_reply_usable_before_sop(state: AgentState, messages: list[dict[str, Any]]) -> bool:
    if not messages:
        return False
    source = str(state.get("reply_source") or "").strip().lower()
    return not (source.startswith("deterministic_") or "fallback" in source)


def _confirm_deferred_chat_sop_task(
    service: Any,
    sop_state: AgentState,
    *,
    request_id: str,
    reply_messages: list[dict[str, Any]],
) -> None:
    if service is None or not hasattr(service, "confirm_chat_gate_task_sent"):
        return
    gate = sop_state.get("sop_gate") if isinstance(sop_state.get("sop_gate"), dict) else {}
    task = gate.get("task") if isinstance(gate.get("task"), dict) else {}
    if str(task.get("status") or "") != "pending":
        return
    gate["task"] = service.confirm_chat_gate_task_sent(
        task,
        request_id=request_id,
        reply_messages=reply_messages,
    )


def _fail_deferred_chat_sop_task(service: Any, sop_state: AgentState, *, error: str) -> None:
    if service is None or not hasattr(service, "fail_chat_gate_task"):
        return
    gate = sop_state.get("sop_gate") if isinstance(sop_state.get("sop_gate"), dict) else {}
    task = gate.get("task") if isinstance(gate.get("task"), dict) else {}
    if str(task.get("status") or "") != "pending":
        return
    gate["task"] = service.fail_chat_gate_task(task, error=error)


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
        )
        record.update(saved)
    except Exception as exc:
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    state["activity_intro_image_send_record"] = record
    _append_activity_intro_image_trace(state, record)


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
    records: list[dict[str, Any]] = []
    missing_store_ids: list[str] = []
    for store_id in store_address_ids:
        store = _store_by_id(state, store_id)
        if store and store_fact_is_valid(store):
            records.append({"event_type": "store_address_sent", "store": store})
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
