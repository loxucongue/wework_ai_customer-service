from __future__ import annotations

import asyncio
import base64
import html
import json
import time
import zlib
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

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
from app.services.customer_scope import build_customer_scope, customer_scope_from_state
from app.services.follow_knowledge_metadata import adopted_follow_knowledge_metadata
from app.services.memory_store import CustomerMemoryStore
from app.services.outreach_send_client import OutreachSendClient
from app.services.outreach_service import OutreachService
from app.services.outreach_system_client import OutreachSystemClient
from app.services.platform_reply_coordinator import PlatformReplyCoordinator, PlatformReplyRecord
from app.services.runtime_budget import build_runtime_budget, graph_deadline_monotonic, runtime_budget_snapshot
from app.services.ai_sales_policy_service import AiSalesPolicyService
from app.services.sales_strategy_service import SalesStrategyService
from app.services.v3_sop_execution_service import SopExecutionService, is_platform_auto_opening_message
from app.services.service_rule_data_service import ServiceRuleDataService
from app.services.storage import AppRepository
from app.services.store_fact_integrity import store_fact_is_valid
from app.services.trace_logger import TraceLogger, audit_snapshot, compact, utc_now_iso
from app.services.v3_reply_recovery import (
    GENERATION_STATUS_FALLBACK_PENDING,
    GENERATION_STATUS_GENERATING,
    stable_v3_reply_messages,
    v3_generation_lease_seconds,
    v3_generation_key,
    v3_response_id,
)


RUNTIME_SAFE_FALLBACK_TEXT = "您稍等一下"


class ChatRuntime:
    def __init__(
        self,
        *,
        full_graph: Any,
        trace_logger: TraceLogger,
        repository: AppRepository,
        commit_graph: Any | None = None,
        outreach_send_client: OutreachSendClient | None = None,
        outreach_system_client: OutreachSystemClient | None = None,
        memory_store: CustomerMemoryStore | None = None,
        platform_reply_coordinator: PlatformReplyCoordinator | None = None,
        sop_execution_service: SopExecutionService | None = None,
        service_rule_data_service: ServiceRuleDataService | None = None,
        ai_sales_policy_service: AiSalesPolicyService | None = None,
        sales_strategy_service: SalesStrategyService | None = None,
        outreach_service: OutreachService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._full_graph = full_graph
        self._commit_graph = commit_graph
        self._trace_logger = trace_logger
        self._repository = repository
        self._outreach_send_client = outreach_send_client
        self._outreach_system_client = outreach_system_client
        self._memory_store = memory_store
        self._platform_reply_coordinator = platform_reply_coordinator
        self._sop_execution_service = sop_execution_service
        self._service_rule_data_service = service_rule_data_service
        self._ai_sales_policy_service = ai_sales_policy_service
        self._sales_strategy_service = sales_strategy_service
        self._outreach_service = outreach_service
        self._settings = settings
        self._platform_request_tasks: dict[str, asyncio.Task[ChatResponse]] = {}
        self._platform_request_results: dict[str, tuple[float, ChatResponse]] = {}
        self._platform_request_tasks_lock = asyncio.Lock()

    @staticmethod
    def is_platform_protocol_message(request: ChatRequest) -> bool:
        return _platform_protocol_event(request.content) is not None

    async def run_v3_takeover_guard(
        self,
        request: ChatRequest,
        *,
        request_id: str = "",
    ) -> ChatResponse | None:
        """Stop V3 before model/tool work when the platform is in human mode."""

        request_context = build_request_context(request)
        if str(request_context.get("interface_version") or "").lower() != "v3":
            return None
        if is_isolated_v2_test_request(request, request_context):
            return None
        if not self._outreach_system_client or not self._outreach_system_client.available:
            return self._build_takeover_block_response(
                request,
                request_context,
                reason="outreach_system_not_configured",
                request_id=request_id,
            )

        guard_started = time.perf_counter()
        try:
            timeout_seconds = max(
                0.5,
                float(getattr(self._settings, "v3_takeover_timeout_seconds", 3.0) or 3.0),
            )
            status = await asyncio.wait_for(
                self._outreach_system_client.conversation_status(
                    corp_id=str(request.corp_id or ""),
                    customer_id=str(request.platform_customer_id or request.customer_id or ""),
                    external_userid=str(request.external_userid or ""),
                    user_id=str(request.user_id or ""),
                    wechat=str(request.wechat or ""),
                    ai_profile_id=str(request_context.get("ai_profile_id") or ""),
                    plan_id=str(request_context.get("plan_id") or ""),
                ),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            _record_v3_phase(request_context, "takeover_guard", guard_started)
            request.request_context = request_context
            return await asyncio.to_thread(
                self._build_takeover_block_response,
                request,
                request_context,
                reason="status_query_failed",
                error=f"{type(exc).__name__}: {exc}"[:500],
                request_id=request_id,
            )
        _record_v3_phase(request_context, "takeover_guard", guard_started)

        data = status.get("data") if isinstance(status.get("data"), dict) else {}
        takeover = data.get("takeover") if isinstance(data.get("takeover"), dict) else {}
        human_mode = bool(takeover.get("is_human")) or str(takeover.get("mode") or "").lower() == "human"
        request_context["takeover_guard"] = {
            "checked": True,
            "decision": "return_empty" if human_mode else "continue_ai",
            "mode": str(takeover.get("mode") or ""),
            "handoff_status": str(takeover.get("handoff_status") or ""),
            "reason_code": str(takeover.get("reason_code") or ""),
            "reason": str(takeover.get("reason") or ""),
        }
        request.request_context = request_context
        if not human_mode:
            return None

        request_id = str(request_id or uuid4())
        request_context["test_isolated"] = False
        request_context["memory_persist_allowed"] = True
        state = self._initial_state(request, request_id, request_context)
        _copy_generation_context_to_state(state, request_context)
        state["reply_messages"] = []
        state["reply_source"] = "human_takeover_guard"
        state["takeover_guard"] = dict(request_context["takeover_guard"])
        state.setdefault("trace", []).append(
            {
                "node": "human_takeover_guard",
                "decision": "no_reply",
                "reason": "platform_human_takeover_active",
                "takeover": dict(request_context["takeover_guard"]),
                "duration_ms": int(
                    ((request_context.get("v3_phase_timings") or {}).get("takeover_guard") or {}).get(
                        "duration_ms"
                    )
                    or 0
                ),
            }
        )
        _set_sync_return(state, "empty", [])
        return await asyncio.to_thread(
            self._persist_terminal_response,
            request=request,
            request_id=request_id,
            final_state=state,
        )

    def _build_takeover_block_response(
        self,
        request: ChatRequest,
        request_context: dict[str, Any],
        *,
        reason: str,
        error: str = "",
        request_id: str = "",
    ) -> ChatResponse:
        guard = {
            "checked": False,
            "decision": "return_empty",
            "reason": reason,
        }
        if error:
            guard["error"] = error
        request_context["takeover_guard"] = guard
        request.request_context = request_context
        request_id = str(request_id or uuid4())
        request_context["test_isolated"] = False
        request_context["memory_persist_allowed"] = True
        state = self._initial_state(request, request_id, request_context)
        _copy_generation_context_to_state(state, request_context)
        state["reply_messages"] = _deterministic_final_fallback_messages(state)
        state["reply_source"] = "takeover_status_unavailable_fallback"
        self._mark_v3_recovery_pending(
            state,
            recovery_kind="takeover_status_unavailable",
            error=error or reason,
        )
        state["takeover_guard"] = dict(guard)
        state.setdefault("trace", []).append(
            {
                "node": "human_takeover_guard",
                "decision": "neutral_retry_reply",
                "reason": reason,
                "takeover": dict(guard),
            }
        )
        _set_sync_return(state, "final_reply", state["reply_messages"])
        return self._persist_terminal_response(
            request=request,
            request_id=request_id,
            final_state=state,
        )

    async def run_chat(self, request: ChatRequest) -> ChatResponse:
        request_context = build_request_context(request)
        protocol_event = _platform_protocol_event(request.content)
        if protocol_event is not None:
            return self._persist_platform_protocol_event(
                request=request,
                request_context=request_context,
                protocol_event=protocol_event,
            )

        request_id = str(uuid4())
        request_context["memory_persist_allowed"] = False
        conversation_id = self._prepare_conversation(request, request_id, request_context)
        self._start_run_tracking(
            request=request,
            request_id=request_id,
            conversation_id=conversation_id,
            request_context=request_context,
        )
        initial_state = self._initial_state(request, request_id, request_context)
        initial_state["previous_policy_state"] = await asyncio.to_thread(
            self._load_previous_policy_state,
            initial_state,
            request_id,
        )

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
                return _replayed_chat_response(cached[1])
            task = self._platform_request_tasks.get(request_identity)
            joined_existing = task is not None
            if task is None:
                task = asyncio.create_task(self._run_platform_reply_once(request, background_tasks))
                self._platform_request_tasks[request_identity] = task
        try:
            response = await asyncio.shield(task)
            async with self._platform_request_tasks_lock:
                self._platform_request_results[request_identity] = (time.monotonic(), response)
            return _replayed_chat_response(response) if joined_existing else response
        finally:
            if task.done():
                async with self._platform_request_tasks_lock:
                    if self._platform_request_tasks.get(request_identity) is task:
                        self._platform_request_tasks.pop(request_identity, None)

    async def run_platform_protocol_reply(
        self,
        request: ChatRequest,
        background_tasks: Any | None = None,
    ) -> ChatResponse:
        """Return a protocol no-op before its lightweight audit is committed."""

        request_context = build_request_context(request)
        protocol_event = _platform_protocol_event(request.content)
        if protocol_event is None:
            return await self.run_platform_reply(request, background_tasks)
        if background_tasks is None:
            return self._persist_platform_protocol_event(
                request=request,
                request_context=request_context,
                protocol_event=protocol_event,
            )
        background_tasks.add_task(
            self._persist_platform_protocol_event,
            request=request,
            request_context=request_context,
            protocol_event=protocol_event,
        )
        return self._build_platform_protocol_response(
            request=request,
            request_context=request_context,
            protocol_event=protocol_event,
        )

    async def _run_platform_reply_once(
        self,
        request: ChatRequest,
        background_tasks: Any | None = None,
    ) -> ChatResponse:
        request_context = build_request_context(request)
        protocol_event = _platform_protocol_event(request.content)
        if protocol_event is not None:
            return self._persist_platform_protocol_event(
                request=request,
                request_context=request_context,
                protocol_event=protocol_event,
            )

        request_id = str(uuid4())
        request_context["test_isolated"] = is_isolated_v2_test_request(request, request_context)
        request_context["memory_persist_allowed"] = not request_context["test_isolated"]
        generation_key = ""
        response_id = ""
        if not request_context["test_isolated"]:
            generation_key = v3_generation_key(
                corp_id=str(request.corp_id or ""),
                wechat=str(request.wechat or ""),
                external_userid=str(request.external_userid or ""),
                msgid=str(request_context.get("msgid") or ""),
            )
            response_id = v3_response_id(generation_key)
        if generation_key:
            generation_lease_token = f"generation_lease:{request_id}"
            generation_lease_until = (
                datetime.now(timezone.utc)
                + timedelta(seconds=v3_generation_lease_seconds(self._settings))
            ).isoformat()
            request_context.update(
                {
                    "generation_key": generation_key,
                    "response_id": response_id,
                    "generation_status": GENERATION_STATUS_GENERATING,
                    "generation_lease_token": generation_lease_token,
                    "recovery_kind": generation_lease_token,
                    "recovery_next_at": generation_lease_until,
                }
            )
            request.request_context = request_context

        scope = build_customer_scope(
            corp_id=request.corp_id,
            wechat=request.wechat,
            external_userid=request.external_userid,
            customer_id=request.platform_customer_id or request.customer_id,
            customer_add_wechat_id=request.customer_add_wechat_id,
            user_id=request.user_id,
        )

        ingress_started = time.perf_counter()
        ingress_result = await asyncio.to_thread(
            self._prepare_and_start_request,
            request=request,
            request_id=request_id,
            request_context=request_context,
            generation_key=generation_key,
            response_id=response_id,
            sales_contact_key=scope.sales_contact_key,
        )
        if bool(ingress_result.get("replayed")):
            replay_response, reclaimed_generation = await self._await_persisted_generation(
                generation_key=str(ingress_result.get("generation_key") or generation_key),
                fallback_request_id=str(ingress_result.get("request_id") or request_id),
                fallback_response_id=str(ingress_result.get("response_id") or response_id),
                claimant_request_id=request_id,
            )
            if replay_response is not None:
                return replay_response

            # The original process died after reserving this platform message
            # but before making a durable response available.  The expired
            # lease was atomically transferred to this HTTP request, so resume
            # the ordinary synchronous path against the original run.  This is
            # generation liveness, not the optional out-of-band reply recovery
            # feature; no proactive dispatch is created here.
            request_id = str(reclaimed_generation.get("request_id") or request_id)
            generation_key = str(reclaimed_generation.get("generation_key") or generation_key)
            response_id = str(reclaimed_generation.get("response_id") or response_id)
            generation_lease_token = str(
                reclaimed_generation.get("generation_lease_token") or ""
            )
            request_context.update(
                {
                    "generation_key": generation_key,
                    "response_id": response_id,
                    "generation_status": GENERATION_STATUS_GENERATING,
                    "generation_lease_token": generation_lease_token,
                    "recovery_kind": generation_lease_token,
                    "recovery_next_at": str(
                        reclaimed_generation.get("recovery_next_at") or ""
                    ),
                    "generation_http_reclaimed": True,
                }
            )
            request.request_context = request_context
            ingress_result = {
                **ingress_result,
                **reclaimed_generation,
                "replayed": False,
                "continue_existing": True,
                "generation_http_reclaimed": True,
            }
        conversation_id = str(ingress_result.get("conversation_id") or "")
        _record_v3_phase(
            request_context,
            "request_ingress_persistence",
            ingress_started,
            metadata={
                "repository_ms": int(ingress_result.get("duration_ms") or 0),
                "connection_count": int(ingress_result.get("connection_count") or 0),
                "statement_count": int(ingress_result.get("statement_count") or 0),
                "outreach_cancel": ingress_result.get("outreach_cancel", {}),
            },
        )

        # Reserve the durable generation before any remote status/model work.
        # Platform retries and another Reply process now converge on this run.
        takeover_response = await self.run_v3_takeover_guard(request, request_id=request_id)
        if takeover_response is not None:
            return takeover_response
        request_context = build_request_context(request)

        decision = (
            await self._platform_reply_coordinator.begin(
                request,
                request_id=request_id,
                request_context=request_context,
            )
            if self._platform_reply_coordinator
            else None
        )
        if decision and not decision.should_run_graph:
            state = self._initial_state(request, request_id, request_context)
            _copy_generation_context_to_state(state, request_context)
            state["reply_messages"] = []
            state["reply_source"] = (
                "platform_superseded"
                if decision.mode == "input_batch_superseded"
                else "platform_filtered"
            )
            state["reply_control"] = self._platform_reply_coordinator.control_for_decision(decision)
            _set_sync_return(state, "empty", [])
            return await asyncio.to_thread(
                self._persist_terminal_response,
                request=request,
                request_id=request_id,
                final_state=state,
            )

        effective_request = request
        effective_context = request_context
        control_record: PlatformReplyRecord | None = None
        if decision:
            control_record = decision.record
            effective_context = {
                **request_context,
                **decision.effective_request_context,
            }
            effective_request = request.model_copy(
                update={
                    "content": decision.effective_content,
                    "request_context": effective_context,
                }
            )
        initial_state = self._initial_state(effective_request, request_id, effective_context)
        _copy_generation_context_to_state(initial_state, effective_context)
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
            return await asyncio.to_thread(
                self._persist_and_build_response,
                request=request,
                request_id=request_id,
                conversation_id=conversation_id,
                final_state=initial_state,
                allow_empty_reply=True,
            )

        # WeCom sends this fixed sentence when a friend is added. It is a
        # platform protocol event, not a customer question.
        if is_platform_auto_opening_message(effective_request.content):
            initial_state["reply_messages"] = []
            initial_state["reply_source"] = "ignored_platform_auto_message"
            initial_state.setdefault("trace", []).append(
                {
                    "node": "platform_protocol_filter",
                    "decision": "no_reply",
                    "reason": "platform_auto_opening_ignored",
                }
            )
            _set_sync_return(initial_state, "empty", [])
            if self._platform_reply_coordinator:
                await self._platform_reply_coordinator.complete(control_record)
            return await asyncio.to_thread(
                self._persist_and_build_response,
                request=request,
                request_id=request_id,
                conversation_id=conversation_id,
                final_state=initial_state,
                allow_empty_reply=True,
            )

        previous_state = ingress_result.get("previous_strategy_state")
        if isinstance(previous_state, dict):
            initial_state["previous_policy_state"] = previous_state
            _record_v3_phase(
                effective_context,
                "previous_policy_state",
                time.perf_counter(),
                metadata={"source": "request_ingress_transaction"},
            )
        else:
            previous_state_started = time.perf_counter()
            initial_state["previous_policy_state"] = await asyncio.to_thread(
                self._load_previous_policy_state,
                initial_state,
                request_id,
            )
            _record_v3_phase(effective_context, "previous_policy_state", previous_state_started)

        graph_started = time.perf_counter()
        try:
            final_state = await self._run_graph_with_preemption(
                self._full_graph,
                initial_state,
                control_record,
                phase="full",
            )
        except Exception as exc:
            final_state = self._handle_graph_exception(initial_state, exc)
        _record_v3_phase(effective_context, "full_graph", graph_started)
        _preserve_reply_control(final_state, initial_state)
        if (
            control_record
            and self._platform_reply_coordinator
            and await self._platform_reply_coordinator.is_superseded(control_record)
        ):
            final_state = self._superseded_state(initial_state, control_record)
            await self._platform_reply_coordinator.complete(control_record)
            return await asyncio.to_thread(
                self._persist_and_build_response,
                request=request,
                request_id=request_id,
                conversation_id=conversation_id,
                final_state=final_state,
                allow_empty_reply=True,
            )

        commit_started = time.perf_counter()
        final_state = await self._commit_after_reply_validation(final_state)
        _record_v3_phase(effective_context, "commit_graph", commit_started)
        final_state["v3_phase_timings"] = dict(effective_context.get("v3_phase_timings") or {})

        final_state["ingress_side_effects"] = {
            "status": "completed",
            "identity": {"status": "deferred_to_finalization_worker"},
            "outreach_cancel": ingress_result.get("outreach_cancel", {}),
        }

        final_state["sync_reply_messages"] = list(final_state.get("reply_messages") or [])
        final_state.setdefault("async_final_reply", {"scheduled": False, "status": "not_required"})
        _set_sync_return(final_state, _sync_return_type(final_state), final_state["sync_reply_messages"])
        response = await asyncio.to_thread(
            self._persist_and_build_response,
            request=request,
            request_id=request_id,
            conversation_id=conversation_id,
            final_state=final_state,
            allow_empty_reply=False,
        )
        if self._platform_reply_coordinator:
            await self._platform_reply_coordinator.complete(control_record)
        return response

    def _persist_platform_protocol_event(
        self,
        *,
        request: ChatRequest,
        request_context: dict[str, Any],
        protocol_event: dict[str, str],
    ) -> ChatResponse:
        request_id = _platform_protocol_request_id(request, request_context)
        request_context["test_isolated"] = is_isolated_v2_test_request(request, request_context)
        request_context["memory_persist_allowed"] = False
        request_context["platform_protocol_event"] = dict(protocol_event)
        request.request_context = request_context
        conversation_id = conversation_id_from_request(request, request_context)
        model_usage = collect_model_usage([])
        save_protocol_run = getattr(self._repository, "save_platform_protocol_run", None)
        if callable(save_protocol_run):
            safe_repository_call(
                save_protocol_run,
                request_id=request_id,
                conversation_id=conversation_id,
                customer_id=str(request.customer_id or ""),
                external_userid=str(request.external_userid or ""),
                corp_id=str(request.corp_id or ""),
                user_id=str(request.user_id or ""),
                wechat=str(request.wechat or ""),
                content=str(request.content or ""),
                request_context=request_context,
                protocol_event=protocol_event,
                token_usage=model_usage["summary"],
            )
        else:
            # Compatibility for narrow repository stubs. Production storage
            # implements the one-transaction method above.
            state = self._initial_state(request, request_id, request_context)
            state["reply_messages"] = []
            state["reply_source"] = protocol_event["reply_source"]
            state["decision_status"] = "skipped"
            state["decision_reasons"] = [protocol_event["reason"]]
            safe_repository_call(
                self._repository.save_run,
                conversation_id=conversation_id,
                final_state=state,
                token_usage=model_usage["summary"],
            )
        return self._build_platform_protocol_response(
            request=request,
            request_context=request_context,
            protocol_event=protocol_event,
        )

    @staticmethod
    def _build_platform_protocol_response(
        *,
        request: ChatRequest,
        request_context: dict[str, Any],
        protocol_event: dict[str, str],
    ) -> ChatResponse:
        request_id = _platform_protocol_request_id(request, request_context)
        conversation_id = conversation_id_from_request(request, request_context)
        model_usage = collect_model_usage([])
        return ChatResponse(
            request_id=request_id,
            reply_messages=[],
            trace_url="",
            meta={
                "model_usage": [],
                "token_usage": model_usage["summary"],
                "tool_calls": [],
                "reply_source": protocol_event["reply_source"],
                "reply_control": {},
                "conversation_id": conversation_id,
                "platform_protocol_event": dict(protocol_event),
            },
        )

    async def _commit_after_reply_validation(self, state: AgentState) -> AgentState:
        if self._commit_graph is None or not state.get("reply_messages"):
            return state
        if bool(state.get("test_isolated")):
            isolated_state: AgentState = dict(state)
            isolated_state["trace"] = list(state.get("trace") or [])
            isolated_state["errors"] = list(state.get("errors") or [])
            isolated_state["commit_result"] = {
                "status": "skipped",
                "reason": "test_isolated",
            }
            isolated_state["trace"].append(
                {
                    "node": "commit_coordinator",
                    "status": "skipped",
                    "reason": "test_isolated",
                }
            )
            return isolated_state
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
        if not (request_context.get("conversation_id") or request_context.get("session_id")):
            find_existing = getattr(self._repository, "find_conversation_id_for_identity", None)
            if callable(find_existing):
                existing = safe_repository_call(
                    find_existing,
                    corp_id=str(request.corp_id or ""),
                    wechat=str(request.wechat or ""),
                    external_userid=str(request.external_userid or ""),
                    customer_id=str(request.platform_customer_id or request.customer_id or ""),
                )
                if existing:
                    conversation_id = str(existing)
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

    def _prepare_and_start_request(
        self,
        *,
        request: ChatRequest,
        request_id: str,
        request_context: dict[str, Any],
        generation_key: str = "",
        response_id: str = "",
        sales_contact_key: str = "",
    ) -> dict[str, Any]:
        conversation_id = conversation_id_from_request(request, request_context)
        prepare = getattr(self._repository, "prepare_v3_request", None)
        if callable(prepare):
            return prepare(
                default_conversation_id=conversation_id,
                resolve_existing=not bool(
                    request_context.get("conversation_id") or request_context.get("session_id")
                ),
                request=request,
                request_id=request_id,
                title=conversation_title(request.content),
                input_snapshot=_run_tracking_input_snapshot(request, request_context),
                interface_version=str(request_context.get("interface_version") or "v3"),
                started_at=str(request_context.get("http_request_started_at") or ""),
                http_request_ingress_id=str(request_context.get("http_request_ingress_id") or ""),
                cancel_outreach=bool(request_context.get("memory_persist_allowed"))
                and not bool(request_context.get("test_isolated")),
                generation_key=generation_key,
                response_id=response_id,
                generation_status=str(
                    request_context.get("generation_status") or GENERATION_STATUS_GENERATING
                ),
                recovery_kind=str(request_context.get("recovery_kind") or ""),
                recovery_next_at=str(request_context.get("recovery_next_at") or ""),
                include_previous_strategy_state=bool(sales_contact_key),
                sales_contact_key=sales_contact_key,
            )
        conversation_id = self._prepare_conversation(request, request_id, request_context)
        self._start_run_tracking(
            request=request,
            request_id=request_id,
            conversation_id=conversation_id,
            request_context=request_context,
        )
        return {"conversation_id": conversation_id, "duration_ms": 0, "connection_count": 0}

    async def _await_persisted_generation(
        self,
        *,
        generation_key: str,
        fallback_request_id: str,
        fallback_response_id: str,
        claimant_request_id: str = "",
    ) -> tuple[ChatResponse | None, dict[str, Any]]:
        """Replay an existing result or safely reclaim an expired HTTP lease.

        Reclaiming a dead synchronous owner is deliberately independent from
        ``V3_REPLY_RECOVERY_ENABLED``.  That flag controls later out-of-band
        generation and sending; it must not turn a crashed primary request into
        a permanent empty replay for the same platform message.
        """

        get_result = getattr(self._repository, "get_v3_generation_result", None)
        if not generation_key or not callable(get_result):
            return _generation_wait_fallback(fallback_request_id, fallback_response_id), {}
        wait_seconds = min(
            12.0,
            max(
                2.0,
                float(getattr(self._settings, "v3_reply_reserve_seconds", 10.0) or 10.0),
            ),
        )
        deadline = time.monotonic() + wait_seconds
        latest: dict[str, Any] = {}
        while True:
            latest = await asyncio.to_thread(get_result, generation_key=generation_key)
            if bool(latest.get("ready")):
                return _chat_response_from_generation(latest), {}
            if str(latest.get("generation_status") or "") not in {
                "",
                GENERATION_STATUS_GENERATING,
            }:
                break
            if _explicit_generation_lease_expired(latest):
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.15)

        # Only an explicit expired primary lease can be transferred.  The CAS
        # also requires the row to remain generating and dispatch-free.  If the
        # original owner finished or a recovery worker won the race, this HTTP
        # request falls back to replay/wait and never creates a second result.
        if (
            claimant_request_id
            and str(latest.get("generation_status") or "") == GENERATION_STATUS_GENERATING
            and _explicit_generation_lease_expired(latest)
        ):
            try:
                reclaimed = await asyncio.to_thread(
                    self._claim_expired_generation_for_http_retry,
                    generation_key=generation_key,
                    claimant_request_id=claimant_request_id,
                    latest=latest,
                )
            except Exception:
                reclaimed = {}
            if bool(reclaimed.get("claimed")):
                return None, reclaimed
            latest = await asyncio.to_thread(get_result, generation_key=generation_key)
            if bool(latest.get("ready")):
                return _chat_response_from_generation(latest), {}

        return (
            _generation_wait_fallback(
                str(latest.get("request_id") or fallback_request_id),
                str(latest.get("response_id") or fallback_response_id),
                generation_status=str(
                    latest.get("generation_status") or GENERATION_STATUS_GENERATING
                ),
            ),
            {},
        )

    def _claim_expired_generation_for_http_retry(
        self,
        *,
        generation_key: str,
        claimant_request_id: str,
        latest: dict[str, Any],
    ) -> dict[str, Any]:
        """CAS one dead primary lease to the current HTTP request.

        This intentionally performs no recovery scheduling and no customer
        send.  The caller continues through the same Router/Reply/persistence
        path as the original request and stores the result under the original
        durable run and response IDs.
        """

        current_token = str(latest.get("recovery_kind") or "").strip()
        current_lease_until = str(latest.get("recovery_next_at") or "").strip()
        if (
            not generation_key
            or not claimant_request_id
            or not current_token.startswith("generation_lease:")
            or not current_lease_until
            or not _explicit_generation_lease_expired(latest)
            or str(latest.get("recovery_dispatch_id") or "").strip()
        ):
            return {"claimed": False, "reason": "generation_not_safely_reclaimable"}

        store = getattr(self._repository, "store", None)
        connect = getattr(store, "connect", None)
        if not callable(connect):
            return {"claimed": False, "reason": "repository_store_unavailable"}

        lease_token = f"generation_lease:{claimant_request_id}"[:64]
        lease_until = (
            datetime.now(timezone.utc)
            + timedelta(seconds=v3_generation_lease_seconds(self._settings))
        ).isoformat()
        with connect() as conn:
            updated = conn.execute(
                """
                UPDATE runs
                SET recovery_kind=?, recovery_next_at=?, recovery_error=''
                WHERE generation_key=? AND generation_status=?
                  AND COALESCE(recovery_dispatch_id, '')=''
                  AND COALESCE(recovery_kind, '')=?
                  AND COALESCE(recovery_next_at, '')=?
                """,
                (
                    lease_token,
                    lease_until,
                    generation_key,
                    GENERATION_STATUS_GENERATING,
                    current_token,
                    current_lease_until,
                ),
            )
            if not int(updated.rowcount or 0):
                return {"claimed": False, "reason": "generation_claim_race_lost"}
            row = conn.execute(
                """
                SELECT request_id, conversation_id, generation_key, response_id,
                       generation_status, recovery_dispatch_id
                FROM runs WHERE generation_key=? LIMIT 1
                """,
                (generation_key,),
            ).fetchone()
        if row is None:
            return {"claimed": False, "reason": "generation_missing_after_claim"}
        stored = dict(row)
        return {
            "claimed": True,
            "request_id": str(stored.get("request_id") or ""),
            "conversation_id": str(stored.get("conversation_id") or ""),
            "generation_key": str(stored.get("generation_key") or generation_key),
            "response_id": str(
                stored.get("response_id") or latest.get("response_id") or ""
            ),
            "generation_status": GENERATION_STATUS_GENERATING,
            "generation_lease_token": lease_token,
            "recovery_kind": lease_token,
            "recovery_next_at": lease_until,
            "recovery_dispatch_id": "",
        }

    async def run_v3_recovery_graph(self, request: ChatRequest, *, request_id: str) -> ChatResponse:
        """Regenerate one failed turn without ingress, commit, memory, BI or customer send."""

        request_context = build_request_context(request)
        request_context.update(
            {
                "interface_version": "v3",
                "test_isolated": False,
                "memory_persist_allowed": False,
                "v3_recovery_execution": True,
            }
        )
        recovery_request = request.model_copy(update={"request_context": request_context})
        state = self._initial_state(recovery_request, request_id, request_context)
        state["deferred_identity_observation"] = False
        state["runtime_budget"] = build_runtime_budget(self._settings)
        try:
            final_state = await self._invoke_graph_with_budget(self._full_graph, state, phase="full")
        except Exception as exc:
            final_state = failed_state_from_exception(state, exc)
            final_state["reply_messages"] = []
            final_state["reply_source"] = "v3_recovery_generation_failed"
        raw_messages = [
            item for item in final_state.get("reply_messages") or [] if isinstance(item, dict)
        ]
        if not raw_messages or _only_runtime_fallback_text(raw_messages):
            raw_messages = []
        route_result = planner_public_route(final_state)
        return ChatResponse(
            request_id=request_id,
            reply_messages=[ReplyMessage(**item) for item in raw_messages],
            scene=str(route_result.get("scene") or ""),
            intent=str(route_result.get("intent") or ""),
            subflow=str(route_result.get("subflow") or ""),
            trace_url="",
            meta={
                "reply_source": str(final_state.get("reply_source") or ""),
                "decision_status": str(final_state.get("decision_status") or ""),
                "reply_sales_judgment": (
                    final_state.get("reply_sales_judgment")
                    if isinstance(final_state.get("reply_sales_judgment"), dict)
                    else {}
                ),
                "v3_recovery_execution": True,
            },
        )

    def _load_previous_policy_state(self, state: AgentState, request_id: str) -> dict[str, Any]:
        scope = customer_scope_from_state(state)
        latest_policy_state = getattr(self._repository, "latest_v3_strategy_state", None)
        if not callable(latest_policy_state) or not scope.persistence_allowed or bool(state.get("test_isolated")):
            return {}
        try:
            return latest_policy_state(
                scope.sales_contact_key,
                exclude_request_id=request_id,
                corp_id=scope.corp_id,
                wechat=scope.wechat,
                external_userid=scope.external_userid,
                customer_id=scope.customer_id,
            )
        except Exception as exc:
            state.setdefault("warnings", []).append(
                {
                    "stage": "previous_policy_state",
                    "warning": "Previous policy state unavailable; current turn will be decided independently.",
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                }
            )
            return {}

    async def _invoke_graph_with_budget(
        self,
        graph: Any,
        state: AgentState,
        *,
        phase: str,
    ) -> AgentState:
        deadline = graph_deadline_monotonic(
            state,
            phase=phase,
            # The graph wrapper must leave room for a Router-selected fact
            # tool. Ordinary turns still keep their shorter internal deadline;
            # fact_actions promotes it only after a structured tool plan exists.
            strong_reply=(phase == "full") or _has_structured_professional_assist(state),
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
            interface_version=str(request_context.get("interface_version") or "v3"),
            started_at=str(request_context.get("http_request_started_at") or ""),
            http_request_ingress_id=str(request_context.get("http_request_ingress_id") or ""),
        )

    def _initial_state(self, request: ChatRequest, request_id: str, request_context: dict[str, Any]) -> AgentState:
        test_isolated = bool(request_context.get("test_isolated"))
        state: AgentState = {
            "request_id": request_id,
            "customer_id": request.customer_id,
            "platform_customer_id": request.platform_customer_id or request.customer_id,
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
            "v3_phase_timings": dict(request_context.get("v3_phase_timings") or {}),
            "test_isolated": test_isolated,
            "memory_persist_allowed": bool(request_context.get("memory_persist_allowed")),
            "runtime_budget": build_runtime_budget(self._settings),
            "trace": [],
            "errors": [],
        }
        _copy_generation_context_to_state(state, request_context)
        scope = customer_scope_from_state(state)
        state["sales_contact_key"] = scope.sales_contact_key
        state["global_customer_key"] = scope.global_customer_key
        state["customer_scope"] = scope.as_dict()
        state["previous_policy_state"] = {}
        state["deferred_identity_observation"] = bool(
            request_context.get("memory_persist_allowed")
            and not request_context.get("test_isolated")
        )
        if self._ai_sales_policy_service is not None:
            try:
                state["ai_sales_policy"] = self._ai_sales_policy_service.runtime_snapshot()
            except ValueError as exc:
                state["ai_sales_policy"] = {
                    "runtime_mode": "off",
                    "runtime_health": {"status": "unavailable", "last_error": str(exc)},
                }
                state["warnings"] = [
                    {
                        "stage": "ai_sales_policy",
                        "warning": "AI sales policy unavailable; policy extension disabled for this turn.",
                    }
                ]
        return state

    def _handle_graph_exception(self, initial_state: AgentState, exc: Exception) -> AgentState:
        failed_state = failed_state_from_exception(initial_state, exc)
        failed_state["reply_messages"] = _deterministic_final_fallback_messages(failed_state)
        failed_state["reply_source"] = "deterministic_runtime_exception_fallback"
        self._mark_v3_recovery_pending(
            failed_state,
            recovery_kind="runtime_exception",
            error=f"{type(exc).__name__}: {exc}",
        )
        return failed_state

    def _mark_v3_recovery_pending(
        self,
        state: AgentState,
        *,
        recovery_kind: str,
        error: str = "",
    ) -> None:
        if not bool(getattr(self._settings, "v3_reply_recovery_enabled", False)):
            return
        if not str(state.get("generation_key") or "").strip():
            return
        if bool(state.get("test_isolated")):
            return
        if str(state.get("generation_status") or "") == GENERATION_STATUS_FALLBACK_PENDING:
            return
        next_at = datetime.now(timezone.utc) + timedelta(seconds=15)
        state["generation_status"] = GENERATION_STATUS_FALLBACK_PENDING
        state["recovery_kind"] = str(recovery_kind or "runtime_fallback")[:64]
        state["recovery_next_at"] = next_at.isoformat()
        if error:
            state["recovery_error"] = str(error)[:4000]
        context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
        context.update(
            {
                "generation_status": GENERATION_STATUS_FALLBACK_PENDING,
                "recovery_kind": state["recovery_kind"],
                "recovery_next_at": state["recovery_next_at"],
            }
        )
        state["request_context"] = context

    def _persist_terminal_response(
        self,
        *,
        request: ChatRequest,
        request_id: str,
        final_state: AgentState,
    ) -> ChatResponse:
        """Persist a no-graph terminal result with one database checkout."""

        raw_reply_messages = [
            item for item in final_state.get("reply_messages") or [] if isinstance(item, dict)
        ]
        final_state["decision_status"] = str(final_state.get("decision_status") or "system_guard")
        final_state["deferred_identity_observation"] = True
        final_state["service_rule_data_allow_empty_reply"] = not bool(raw_reply_messages)
        persist = getattr(self._repository, "save_v3_terminal_no_reply", None)
        if not callable(persist):
            raise RuntimeError("repository does not support atomic V3 terminal persistence")
        persistence_started = time.perf_counter()
        result = persist(
            default_conversation_id=conversation_id_from_request(request, final_state.get("request_context") or {}),
            resolve_existing=not bool(
                (final_state.get("request_context") or {}).get("conversation_id")
                or (final_state.get("request_context") or {}).get("session_id")
            ),
            request=request,
            request_id=request_id,
            title=conversation_title(request.content),
            input_snapshot=_run_tracking_input_snapshot(
                request,
                final_state.get("request_context") or {},
            ),
            request_context=final_state.get("request_context") or {},
            final_state=final_state,
            reply_messages=raw_reply_messages,
            token_usage=collect_model_usage(final_state.get("trace", []))["summary"],
            deferred_payload=_deferred_state_payload(final_state),
        )
        if bool(result.get("generation_owner_lost")):
            return self._response_after_generation_owner_loss(
                request_id=request_id,
                response_id=str(final_state.get("response_id") or ""),
                generation_key=str(final_state.get("generation_key") or ""),
                generation_status=str(result.get("generation_status") or ""),
            )
        _record_v3_phase(
            final_state,
            "terminal_persistence",
            persistence_started,
            metadata={
                "repository_ms": int(result.get("duration_ms") or 0),
                "connection_count": int(result.get("connection_count") or 0),
                "statement_count": int(result.get("statement_count") or 0),
            },
        )
        final_state["persistence_metrics"] = {
            "terminal": {
                "duration_ms": int(result.get("duration_ms") or 0),
                "connection_count": int(result.get("connection_count") or 0),
                "statement_count": int(result.get("statement_count") or 0),
            }
        }
        final_state["post_reply_finalization"] = {"status": "pending", "mode": "durable_worker"}
        return self._persist_and_build_response(
            request=request,
            request_id=request_id,
            conversation_id=str(result.get("conversation_id") or ""),
            final_state=final_state,
            allow_empty_reply=not bool(raw_reply_messages),
            persistence_completed=True,
        )

    def _persist_and_build_response(
        self,
        *,
        request: ChatRequest,
        request_id: str,
        conversation_id: str,
        final_state: AgentState,
        allow_empty_reply: bool,
        persistence_completed: bool = False,
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
            self._mark_v3_recovery_pending(
                final_state,
                recovery_kind="empty_reply",
                error="final reply was empty",
            )
            _set_sync_return(final_state, "final_reply", raw_reply_messages)
        elif (
            not allow_empty_reply
            and _only_runtime_fallback_text(
                [item for item in raw_reply_messages if isinstance(item, dict)]
            )
        ):
            self._mark_v3_recovery_pending(
                final_state,
                recovery_kind="reply_pipeline_failure",
                error=str(final_state.get("recovery_reason") or "reply pipeline fallback"),
            )
        response_id = str(
            final_state.get("response_id")
            or (
                (final_state.get("request_context") or {}).get("response_id")
                if isinstance(final_state.get("request_context"), dict)
                else ""
            )
            or ""
        )
        if response_id:
            raw_reply_messages = stable_v3_reply_messages(
                [item for item in raw_reply_messages if isinstance(item, dict)],
                response_id=response_id,
            )
            final_state["reply_messages"] = raw_reply_messages
        follow_knowledge_callback = adopted_follow_knowledge_metadata(final_state)
        if follow_knowledge_callback:
            final_state["follow_knowledge_callback"] = follow_knowledge_callback
        else:
            final_state.pop("follow_knowledge_callback", None)
        reply_messages = [ReplyMessage(**message) for message in raw_reply_messages]
        reply_message_dicts = [message.model_dump() for message in reply_messages]
        # Persist the exact public response metadata before returning.  A later
        # retry can be handled by another process, so rebuilding metadata from
        # the compact run state would otherwise lose follow knowledge and other
        # workflow-compatible fields even though the original HTTP response was
        # complete.
        response_meta = _chat_response_meta(
            final_state,
            model_usage=model_usage,
            conversation_id=conversation_id,
            response_id=response_id,
            replayed=False,
        )
        response_snapshot = {
            "request_id": request_id,
            "response_id": response_id,
            "replayed": False,
            "reply_messages": reply_message_dicts,
            "scene": str(route_result.get("scene", "")),
            "intent": str(route_result.get("intent", "")),
            "subflow": str(route_result.get("subflow", "")),
            "trace_url": None,
            "meta": response_meta,
        }
        if not persistence_completed and (
            not bool(final_state.get("test_isolated"))
            and _memory_persistence_allowed(final_state)
        ):
            _record_stop_contact_fact(
                self._memory_store,
                final_state,
                customer_id=str(final_state.get("sales_contact_key") or ""),
            )
        deferred_finalization = False
        log_path: Any = ""
        if not persistence_completed and not bool(final_state.get("test_isolated")):
            save_reply_core = getattr(self._repository, "save_v3_reply_core", None)
            if callable(save_reply_core):
                try:
                    core_result = save_reply_core(
                        conversation_id=conversation_id,
                        final_state=final_state,
                        reply_messages=reply_message_dicts,
                        token_usage=model_usage["summary"],
                        deferred_payload=_deferred_state_payload(final_state),
                        response_snapshot=response_snapshot,
                    )
                    deferred_finalization = True
                    final_state["post_reply_finalization"] = {
                        "status": "pending",
                        "mode": "durable_worker",
                    }
                    final_state["persistence_metrics"] = {"reply_core": core_result or {}}
                    if bool((core_result or {}).get("generation_owner_lost")):
                        return self._response_after_generation_owner_loss(
                            request_id=request_id,
                            response_id=response_id,
                            generation_key=str(final_state.get("generation_key") or ""),
                            generation_status=str(
                                (core_result or {}).get("generation_status") or ""
                            ),
                        )
                except Exception as exc:
                    final_state.setdefault("warnings", []).append(
                        {
                            "node": "post_reply_finalization",
                            "message": "durable_finalization_enqueue_failed",
                            "detail": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    # A generation-key response must never escape without its
                    # durable idempotent result.  Otherwise a platform retry
                    # can run the same message again while this untracked reply
                    # has already reached the customer.
                    if str(final_state.get("generation_key") or "").strip() or final_state.get("material_identity_governed"):
                        return self._response_after_generation_owner_loss(
                            request_id=request_id,
                            response_id=response_id,
                            generation_key=str(final_state.get("generation_key") or ""),
                            generation_status=GENERATION_STATUS_GENERATING,
                        )
            if not deferred_finalization and reply_messages:
                if _memory_persistence_allowed(final_state):
                    self._record_reply_memory(
                        final_state=final_state,
                        reply_messages=reply_message_dicts,
                    )
                safe_repository_call(
                    self._repository.add_assistant_message,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    reply_messages=reply_message_dicts,
                )
                if self._service_rule_data_service:
                    try:
                        final_state["strategy_data_callback"] = (
                            self._service_rule_data_service.enqueue_customer_open(final_state)
                        )
                    except Exception as exc:
                        final_state["strategy_data_callback"] = {
                            "status": "error",
                            "reason": f"{type(exc).__name__}: {exc}"[:500],
                        }
                        final_state.setdefault("warnings", []).append(
                            {
                                "node": "strategy_data_callback",
                                "message": "strategy_data_callback_enqueue_failed",
                                "detail": f"{type(exc).__name__}: {exc}",
                            }
                        )
        elif not persistence_completed and reply_messages:
            final_state["case_image_send_record"] = {
                "status": "skipped",
                "reason": "test_isolated",
                "image_message_count": len(
                    [message for message in reply_messages if message.type == "image"]
                ),
            }
        if not persistence_completed and not deferred_finalization and self._outreach_service is not None:
            try:
                final_state["closing_sequence_shadow"] = self._outreach_service.record_closing_sequence_shadow(
                    final_state
                )
            except Exception as exc:
                final_state["closing_sequence_shadow"] = {
                    "created": False,
                    "reason": "shadow_audit_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                final_state.setdefault("warnings", []).append(
                    {
                        "stage": "closing_sequence_shadow",
                        "warning": "Closing sequence shadow audit failed; no delayed customer message was sent.",
                    }
                )
        if not persistence_completed and not deferred_finalization:
            log_path = self._trace_logger.write_run(final_state)
            safe_repository_call(
                self._repository.save_run,
                conversation_id=conversation_id,
                final_state=final_state,
                token_usage=model_usage["summary"],
            )
            try:
                final_state["v3_strategy_usage_event"] = self._repository.record_v3_strategy_usage(
                    conversation_id=conversation_id,
                    final_state=final_state,
                )
            except Exception as exc:
                final_state.setdefault("warnings", []).append(
                    {
                        "node": "v3_strategy_analytics",
                        "message": "usage_event_persistence_failed",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )

        response_meta = _chat_response_meta(
            final_state,
            model_usage=model_usage,
            conversation_id=conversation_id,
            response_id=response_id,
            replayed=False,
        )
        return ChatResponse(
            request_id=request_id,
            response_id=response_id,
            replayed=False,
            reply_messages=reply_messages,
            scene=str(route_result.get("scene", "")),
            intent=str(route_result.get("intent", "")),
            subflow=str(route_result.get("subflow", "")),
            trace_url=str(log_path),
            meta=response_meta,
        )

    def _response_after_generation_owner_loss(
        self,
        *,
        request_id: str,
        response_id: str,
        generation_key: str,
        generation_status: str,
    ) -> ChatResponse:
        """Suppress a late owner's payload after its durable lease was reclaimed."""

        get_result = getattr(self._repository, "get_v3_generation_result", None)
        if callable(get_result) and generation_key:
            try:
                latest = get_result(generation_key=generation_key)
                if bool(latest.get("ready")):
                    return _chat_response_from_generation(latest)
            except Exception:
                pass
        return _generation_wait_fallback(
            request_id,
            response_id,
            generation_status=generation_status or GENERATION_STATUS_FALLBACK_PENDING,
        )

    def _record_reply_memory(
        self,
        *,
        final_state: AgentState,
        reply_messages: list[dict[str, Any]],
    ) -> None:
        record_reply_memory(
            self._memory_store,
            final_state=final_state,
            reply_messages=reply_messages,
        )

    def _save_state(self, conversation_id: str, state: AgentState) -> None:
        self._trace_logger.write_run(state)
        safe_repository_call(
            self._repository.save_run,
            conversation_id=conversation_id,
            final_state=state,
            token_usage=collect_model_usage(state.get("trace", []))["summary"],
        )


def record_reply_memory(
    memory_store: CustomerMemoryStore | None,
    *,
    final_state: AgentState,
    reply_messages: list[dict[str, Any]],
) -> None:
    customer_id = str(final_state.get("sales_contact_key") or "")
    if memory_store is None or not customer_id:
        return
    with memory_store.write_batch(customer_id):
        _record_authoritative_payment_fact(memory_store, final_state, customer_id=customer_id)
        _record_sent_case_images(
            memory_store,
            final_state,
            customer_id=customer_id,
            reply_messages=reply_messages,
        )
        _record_activity_intro_image(
            memory_store,
            final_state,
            customer_id=customer_id,
            reply_messages=reply_messages,
            send_mode="sync",
        )
        _record_visible_store_facts(
            memory_store,
            final_state,
            customer_id=customer_id,
            reply_messages=reply_messages,
        )
        _record_mainline_stage_delivery(
            memory_store,
            final_state,
            customer_id=customer_id,
            reply_messages=reply_messages,
        )
        try:
            _record_reply_model_observation(memory_store, final_state, customer_id=customer_id)
        except Exception as exc:
            final_state.setdefault("warnings", []).append(
                {
                    "node": "reply_model_observation",
                    "message": "observation_persistence_failed",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
        try:
            _record_follow_knowledge_match(memory_store, final_state, customer_id=customer_id)
            _record_follow_knowledge_usage(memory_store, final_state, customer_id=customer_id)
        except Exception as exc:
            final_state.setdefault("warnings", []).append(
                {
                    "node": "follow_knowledge_usage",
                    "message": "knowledge_usage_persistence_failed",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
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
        "customer_add_wechat_id": request.customer_add_wechat_id,
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
    return [{"type": "text", "order": 1, "content": RUNTIME_SAFE_FALLBACK_TEXT}]


def _copy_generation_context_to_state(
    state: AgentState,
    request_context: dict[str, Any],
) -> None:
    for key in ("generation_key", "response_id", "generation_status"):
        value = request_context.get(key)
        if value not in (None, ""):
            state[key] = value
    # ``recovery_kind`` and ``recovery_next_at`` carry the active owner lease
    # while generation is running.  They are storage coordination details, not
    # a customer-visible recovery decision.  A real fallback copies them when
    # ``_mark_v3_recovery_pending`` changes the status.
    if str(request_context.get("generation_status") or "") != GENERATION_STATUS_GENERATING:
        for key in ("recovery_kind", "recovery_next_at"):
            value = request_context.get(key)
            if value not in (None, ""):
                state[key] = value


def _chat_response_meta(
    final_state: AgentState,
    *,
    model_usage: dict[str, Any],
    conversation_id: str,
    response_id: str,
    replayed: bool,
) -> dict[str, Any]:
    """Build the public ChatResponse metadata from one completed state.

    This projection is used both for the live response and the durable replay
    snapshot.  Keeping one builder prevents cross-process retries from losing
    workflow metadata such as adopted follow sequences or scripts.
    """

    return {
        "tool_result_keys": list((final_state.get("tool_results") or {}).keys()),
        "profile_update": final_state.get("profile_update", {}),
        "event_updates": final_state.get("event_updates", []),
        "image_info": final_state.get("image_info", {}),
        "memory_error": final_state.get("memory_error"),
        "customer_context": final_state.get("customer_context", {}),
        "customer_context_error": final_state.get("customer_context_error"),
        "customer_store_knowledge": _customer_store_knowledge_meta(
            final_state.get("customer_store_knowledge")
        ),
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
        "reply_knowledge_use": final_state.get("reply_knowledge_use", {}),
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
        "strategy_data_callback": final_state.get("strategy_data_callback", {}),
        "follow_knowledge_callback": final_state.get("follow_knowledge_callback", {}),
        "post_reply_finalization": final_state.get("post_reply_finalization", {}),
        "persistence_metrics": final_state.get("persistence_metrics", {}),
        "conversation_id": conversation_id,
        "response_id": response_id,
        "replayed": replayed,
        "generation_status": str(final_state.get("generation_status") or ""),
        "recovery_kind": str(final_state.get("recovery_kind") or ""),
        "recovery_next_at": str(final_state.get("recovery_next_at") or ""),
    }


def _replayed_chat_response(response: ChatResponse) -> ChatResponse:
    meta = dict(response.meta or {})
    meta["replayed"] = True
    return response.model_copy(update={"replayed": True, "meta": meta}, deep=True)


def _chat_response_from_generation(result: dict[str, Any]) -> ChatResponse:
    snapshot = result.get("response") if isinstance(result.get("response"), dict) else {}
    messages = snapshot.get("reply_messages") if isinstance(snapshot.get("reply_messages"), list) else []
    meta = snapshot.get("meta") if isinstance(snapshot.get("meta"), dict) else {}
    meta = {
        **meta,
        "replayed": True,
        "generation_status": str(result.get("generation_status") or ""),
        "recovery_kind": str(result.get("recovery_kind") or ""),
        "recovery_attempts": int(result.get("recovery_attempts") or 0),
        "recovery_next_at": str(result.get("recovery_next_at") or ""),
        "recovery_dispatch_id": str(result.get("recovery_dispatch_id") or ""),
    }
    return ChatResponse(
        request_id=str(snapshot.get("request_id") or result.get("request_id") or ""),
        response_id=str(snapshot.get("response_id") or result.get("response_id") or ""),
        replayed=True,
        reply_messages=[ReplyMessage(**item) for item in messages if isinstance(item, dict)],
        scene=str(snapshot.get("scene") or ""),
        intent=str(snapshot.get("intent") or ""),
        subflow=str(snapshot.get("subflow") or ""),
        trace_url=snapshot.get("trace_url") or None,
        meta=meta,
    )


def _generation_wait_fallback(
    request_id: str,
    response_id: str,
    *,
    generation_status: str = GENERATION_STATUS_GENERATING,
) -> ChatResponse:
    # Another process still owns this generation.  Returning a customer-visible
    # fallback here would create a second message while the owner may shortly
    # return the real reply.  The workflow-compatible response deliberately has
    # no customer payload; a later retry replays the durable owner result.
    return ChatResponse(
        request_id=request_id,
        response_id=response_id,
        replayed=True,
        reply_messages=[],
        trace_url="",
        meta={
            "reply_source": "generation_in_progress",
            "response_kind": "generation_in_progress",
            "replayed": True,
            "generation_status": generation_status or GENERATION_STATUS_GENERATING,
        },
    )


def _explicit_generation_lease_expired(
    generation: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """Return true only for a parseable, explicitly expired primary lease."""

    raw = str(generation.get("recovery_next_at") or "").strip()
    token = str(generation.get("recovery_kind") or "").strip()
    if not raw or not token.startswith("generation_lease:"):
        return False
    try:
        lease_until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if lease_until.tzinfo is None:
        lease_until = lease_until.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return lease_until.astimezone(timezone.utc) <= current.astimezone(timezone.utc)


def _only_runtime_fallback_text(messages: list[dict[str, Any]]) -> bool:
    visible = [item for item in messages if isinstance(item, dict)]
    return bool(visible) and all(
        _message_type(item) == "text" and _message_text(item) == RUNTIME_SAFE_FALLBACK_TEXT
        for item in visible
    )


def _deferred_state_payload(state: AgentState) -> dict[str, Any]:
    """Return a JSON-safe, credential-scrubbed copy for durable finalization."""

    secret_fragments = ("token", "authorization", "api_key", "apikey", "password", "secret")

    def scrub(value: Any, key: str = "") -> Any:
        normalized_key = key.lower()
        if any(fragment in normalized_key for fragment in secret_fragments):
            return "[redacted]"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if value.startswith("data:image/") and ";base64," in value:
                return f"[base64 image omitted: {len(value)} chars]"
            return value[:100000]
        if isinstance(value, dict):
            return {str(item_key): scrub(item_value, str(item_key)) for item_key, item_value in value.items()}
        if isinstance(value, (list, tuple)):
            return [scrub(item) for item in value]
        return str(value)

    scrubbed = scrub(state)
    if not isinstance(scrubbed, dict):
        return {}
    # A serialization round trip proves the job can survive a process restart.
    raw = json.dumps(scrubbed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(raw, level=6)
    return {
        "encoding": "zlib+base64+json",
        "uncompressed_bytes": len(raw),
        "data": base64.b64encode(compressed).decode("ascii"),
    }


def _merge_reply_message_groups(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for message in group:
            if not isinstance(message, dict):
                continue
            identity = _message_identity(message)
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            copied = dict(message)
            copied["order"] = len(merged) + 1
            merged.append(copied)
    return merged


def _merge_ai_then_sop_reply_messages(
    ai_messages: list[dict[str, Any]],
    sop_messages: list[dict[str, Any]],
    *,
    payment_decision: Any = None,
) -> list[dict[str, Any]]:
    """Compatibility merger with the main payment-card safety contract."""
    ai_messages = _payment_authorized_reply_messages(ai_messages, payment_decision=payment_decision)
    sop_messages = _payment_authorized_reply_messages(sop_messages, payment_decision=payment_decision)
    if not any(_message_type(message) == "text" and _message_text(message) for message in ai_messages):
        return _merge_reply_message_groups(ai_messages, sop_messages)

    sop_structural = [
        message
        for message in sop_messages
        if isinstance(message, dict)
        and _message_type(message) in {"image", "video", "store_address", "payment_collection", "human_handoff_notice"}
    ]
    if not sop_structural:
        return _merge_reply_message_groups(ai_messages, sop_messages)

    ai_text_count = sum(1 for message in ai_messages if _message_type(message) == "text" and _message_text(message))
    bridge_sop_text = _first_text_message(sop_messages) if ai_text_count <= 1 else None
    trailing_ai_text: dict[str, Any] | None = None
    ai_prefix = list(ai_messages)
    if ai_text_count > 1 and ai_prefix and _message_type(ai_prefix[-1]) == "text" and _message_text(ai_prefix[-1]):
        trailing_ai_text = ai_prefix.pop()

    ai_has_payment = any(_message_type(message) == "payment_collection" for message in ai_messages)
    payment_kept = False
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    candidates = [
        *ai_prefix,
        *([bridge_sop_text] if bridge_sop_text else []),
        *sop_structural[:3],
        *([trailing_ai_text] if trailing_ai_text else []),
    ]
    for message in candidates:
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
        value = str(
            content.get("url")
            or content.get("store_id")
            or content.get("amount")
            or content.get("text")
            or ""
        ).strip()
    else:
        value = str(content or "").strip()
    return (msg_type, value)


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


def _record_stop_contact_fact(
    memory_store: CustomerMemoryStore | None,
    state: AgentState,
    *,
    customer_id: str,
) -> None:
    """Persist Reply's explicit-exit decision without blocking the customer response."""

    policy_decision = (
        state.get("policy_decision")
        if isinstance(state.get("policy_decision"), dict)
        else {}
    )
    realtime_intent = (
        state.get("realtime_intent")
        if isinstance(state.get("realtime_intent"), dict)
        else policy_decision.get("realtime_intent")
        if isinstance(policy_decision.get("realtime_intent"), dict)
        else {}
    )
    record: dict[str, Any] = {"status": "skipped", "reason": "not_explicit_exit"}
    if str(realtime_intent.get("type") or "").strip() != "explicit_exit":
        state["stop_contact_memory_record"] = record
        return
    if not memory_store:
        record["reason"] = "memory_store_unavailable"
    elif not customer_id:
        record["reason"] = "missing_sales_contact_key"
    else:
        evidence_refs = [
            str(item).strip()
            for item in realtime_intent.get("evidence_refs") or []
            if str(item or "").strip()
        ]
        if not evidence_refs:
            record["reason"] = "missing_valid_customer_evidence"
        else:
            try:
                record.update(
                    memory_store.record_stop_contact(
                        customer_id,
                        request_id=str(state.get("request_id") or ""),
                        evidence_refs=evidence_refs,
                        reason="explicit_exit",
                    )
                )
            except Exception as exc:
                record = {
                    "status": "error",
                    "reason": "persistence_failed",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
    state["stop_contact_memory_record"] = record
    if record.get("status") != "recorded":
        state.setdefault("warnings", []).append(
            {
                "stage": "stop_contact_memory",
                "warning": "Explicit stop-contact was detected but could not be persisted.",
                "detail": str(record.get("error") or record.get("reason") or "")[:500],
            }
        )


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


def _platform_protocol_request_id(request: ChatRequest, request_context: dict[str, Any]) -> str:
    request_identity = _platform_request_identity(request, request_context)
    if request_identity:
        return str(uuid5(NAMESPACE_URL, f"ai-paths:platform-protocol:{request_identity}"))
    return str(uuid4())


def _platform_protocol_event(content: str) -> dict[str, str] | None:
    if is_platform_recalled_message(content):
        return {
            "message_type": "customer_message_recalled",
            "reply_source": "platform_recalled_message",
            "reason": "customer_message_recalled",
        }
    if is_platform_auto_opening_message(content):
        return {
            "message_type": "platform_auto_opening",
            "reply_source": "ignored_platform_auto_message",
            "reason": "platform_auto_opening_ignored",
        }
    return None


def _record_sent_case_images(
    memory_store: CustomerMemoryStore | None,
    state: AgentState,
    *,
    customer_id: str,
    reply_messages: list[dict[str, Any]],
) -> None:
    record = _case_image_send_record(state, reply_messages)
    if state.get("material_identity_governed"):
        record["status"] = "response_committed" if record.get("image_urls") else "skipped"
        record["reason"] = "platform_send_and_delivery_unconfirmed"
        state["case_image_send_record"] = record
        _append_case_image_trace(state, record)
        return
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
            asset_roles=(record.get("asset_roles") if "asset_roles" in record else None),
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
    selected_asset_urls = _selected_content_asset_image_urls(state)
    image_urls = [_message_image_url(message) for message in reply_messages if isinstance(message, dict)]
    image_urls = [url for url in image_urls if url]
    matched_ids: list[str] = []
    matched_urls: list[str] = []
    unmatched_urls: list[str] = []
    matched_content_ids: list[str] = []
    matched_script_ids: list[str] = []
    matched_script_codes: list[str] = []
    matched_asset_roles: list[str] = []
    for image_url in image_urls:
        normalized_url = _normalize_url(image_url)
        doc_id = case_by_url.get(normalized_url, "")
        selected_asset = selected_asset_urls.get(normalized_url) or {}
        if doc_id:
            if doc_id not in matched_ids:
                matched_ids.append(doc_id)
            # An explicitly selected content asset owns the semantic role for
            # this URL. A sales-reference image can share a generic case URL
            # without falsely advancing the effect-evidence stage.
            if not selected_asset and "effect_evidence" not in matched_asset_roles:
                matched_asset_roles.append("effect_evidence")
        for record_id in selected_asset.get("record_ids") or []:
            if record_id not in matched_ids:
                matched_ids.append(record_id)
        for target, key in (
            (matched_content_ids, "content_ids"),
            (matched_script_ids, "script_ids"),
            (matched_script_codes, "script_codes"),
        ):
            for value in selected_asset.get(key) or []:
                if value not in target:
                    target.append(value)
        for role in selected_asset.get("asset_roles") or []:
            if role and role not in matched_asset_roles:
                matched_asset_roles.append(role)
        if doc_id or selected_asset:
            matched_urls.append(image_url)
        else:
            unmatched_urls.append(image_url)
    return {
        "image_message_count": len(image_urls),
        "document_ids": matched_ids,
        "image_urls": matched_urls,
        "unmatched_image_urls": unmatched_urls,
        "candidate_document_ids": sorted(set(case_by_url.values())),
        "matched_content_ids": matched_content_ids,
        "matched_script_ids": matched_script_ids,
        "matched_script_codes": matched_script_codes,
        "asset_roles": matched_asset_roles,
        "selected_effect_asset_ids": sorted(
            {
                content_id
                for asset in selected_asset_urls.values()
                if "effect_evidence" in (asset.get("asset_roles") or [])
                for content_id in asset.get("content_ids") or []
            }
        ),
        "selected_sales_reference_ids": sorted(
            {
                content_id
                for asset in selected_asset_urls.values()
                if "sales_reference" in (asset.get("asset_roles") or [])
                for content_id in asset.get("content_ids") or []
            }
        ),
    }


def _selected_content_asset_image_urls(state: AgentState) -> dict[str, dict[str, list[str]]]:
    """Index selected image candidates that may be durably recorded after delivery.

    Follow-script media uses ``asset_role=sales_reference`` rather than
    ``effect_evidence``.  Both roles are explicit, already-approved content
    candidates; intersecting them with the final reply images prevents an
    unadopted candidate from being recorded as delivered.
    """

    raw_selected_ids = state.get("selected_content_ids")
    if not isinstance(raw_selected_ids, list):
        raw_selected_ids = (
            state.get("reply_selected_content_ids") if isinstance(state.get("reply_selected_content_ids"), list) else []
        )
    selected_ids = {str(item).strip() for item in raw_selected_ids if str(item or "").strip()}
    if not selected_ids:
        return {}
    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    output: dict[str, dict[str, list[str]]] = {}
    for candidate in joined.get("content_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content_id = str(candidate.get("content_id") or candidate.get("id") or "").strip()
        if content_id not in selected_ids:
            continue
        asset_role = str(candidate.get("asset_role") or "").strip()
        if asset_role not in {"effect_evidence", "sales_reference"}:
            continue
        script_id = str(candidate.get("source_script_id") or "").strip()
        script_code = str(candidate.get("source_script_code") or "").strip()
        record_ids = list(dict.fromkeys(value for value in (content_id, script_id, script_code) if value))
        messages = candidate.get("messages")
        if not isinstance(messages, list):
            messages = candidate.get("reply_messages") if isinstance(candidate.get("reply_messages"), list) else []
        for message in messages:
            if not isinstance(message, dict):
                continue
            image_url = _message_image_url(message)
            if image_url:
                entry = output.setdefault(
                    _normalize_url(image_url),
                    {
                        "content_ids": [],
                        "script_ids": [],
                        "script_codes": [],
                        "asset_roles": [],
                        "record_ids": [],
                    },
                )
                for target_key, values in (
                    ("content_ids", [content_id]),
                    ("script_ids", [script_id]),
                    ("script_codes", [script_code]),
                    ("asset_roles", [asset_role]),
                    ("record_ids", record_ids),
                ):
                    for value in values:
                        if value and value not in entry[target_key]:
                            entry[target_key].append(value)
    return output


def _selected_effect_asset_image_urls(state: AgentState) -> dict[str, str]:
    """Backward-compatible view retained for callers/tests of the old helper."""

    return {
        image_url: str((asset.get("content_ids") or [""])[0])
        for image_url, asset in _selected_content_asset_image_urls(state).items()
        if "effect_evidence" in (asset.get("asset_roles") or [])
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
        "node": "case_image_response_commit" if state.get("material_identity_governed") else "case_image_send_record",
        "started_at": utc_now_iso(),
        "input_snapshot": compact(
            {
                "image_message_count": result.get("image_message_count", 0),
                "candidate_document_ids": result.get("candidate_document_ids", []),
            }
        ),
        "tool_calls": [{"name": "record_material_response_commit" if state.get("material_identity_governed")
                        else "record_case_images_sent", "output": compact(result)}],
        "error": result.get("error"),
        "output_snapshot": compact(result),
    }
    entry["finished_at"] = utc_now_iso()
    entry["duration_ms"] = int((time.perf_counter() - started) * 1000)
    state.setdefault("trace", []).append(audit_snapshot(entry))


def _record_activity_intro_image(
    memory_store: CustomerMemoryStore | None,
    state: AgentState,
    *,
    customer_id: str,
    reply_messages: list[dict[str, Any]],
    send_mode: str,
) -> None:
    record = _activity_intro_image_record_plan(state, reply_messages, send_mode=send_mode)
    if state.get("material_identity_governed"):
        record["status"] = "response_committed" if record.get("image_url") else "skipped"
        record["reason"] = "platform_send_and_delivery_unconfirmed"
        state["activity_intro_image_send_record"] = record
        _append_activity_intro_image_trace(state, record)
        return
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


def _record_reply_model_observation(
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
    memory_store.record_reply_model_observation(
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
            or "v3"
        ),
    )


def _record_mainline_stage_delivery(
    memory_store: CustomerMemoryStore | None,
    state: AgentState,
    *,
    customer_id: str,
    reply_messages: list[dict[str, Any]],
) -> None:
    """Persist a model-declared stage only after its visible reply exists.

    This does not infer sales meaning from keywords.  V3 Reply is the sole
    semantic decision-maker; code records only its normalized action together
    with the fact that a customer-visible text response was produced.  Effect,
    store, appointment and payment stages continue to require their dedicated
    structured delivery or authoritative transaction facts.
    """

    if not memory_store or not state.get("evidence_join") or not customer_id:
        return
    if not any(
        isinstance(item, dict)
        and str(item.get("type") or "").strip() == "text"
        and str(item.get("content") or "").strip()
        for item in reply_messages
    ):
        return
    judgment = (
        state.get("reply_sales_judgment")
        if isinstance(state.get("reply_sales_judgment"), dict)
        else {}
    )
    next_action = (
        judgment.get("next_sales_action")
        if isinstance(judgment.get("next_sales_action"), dict)
        else {}
    )
    action_type = str(next_action.get("type") or "").strip()
    target_stage = str(next_action.get("target_stage") or "").strip()
    stage_by_action = {"explain_activity": "activity_offer"}
    stage = stage_by_action.get(action_type, "")
    if not stage or target_stage != stage:
        return
    memory_store.record_sales_stage_delivered(
        customer_id,
        stage=stage,
        action_type=action_type,
        request_id=str(state.get("request_id") or ""),
        interface_version=_interface_version_from_state(state),
    )


def _record_follow_knowledge_usage(
    memory_store: CustomerMemoryStore | None,
    state: AgentState,
    *,
    customer_id: str,
) -> None:
    """Append validated Reply knowledge provenance after a visible response."""

    if not memory_store or not customer_id:
        return
    knowledge_use = (
        state.get("reply_knowledge_use")
        if isinstance(state.get("reply_knowledge_use"), dict)
        else {}
    )
    if not knowledge_use:
        return
    memory_store.record_follow_knowledge_usage(
        customer_id,
        request_id=str(state.get("request_id") or ""),
        knowledge_use=knowledge_use,
        interface_version=_interface_version_from_state(state),
    )


def _record_follow_knowledge_match(
    memory_store: CustomerMemoryStore | None,
    state: AgentState,
    *,
    customer_id: str,
) -> None:
    if not memory_store or not customer_id:
        return
    semantic_route = state.get("semantic_route") if isinstance(state.get("semantic_route"), dict) else {}
    if not semantic_route:
        return
    memory_store.record_follow_knowledge_match(
        customer_id,
        request_id=str(state.get("request_id") or ""),
        semantic_route=semantic_route,
        interface_version=_interface_version_from_state(state),
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
    if state.get("material_identity_governed"):
        # URL/catalog overlap is not evidence of activity-role delivery.
        bindings = state.get("material_identity_bindings") or {}
        image_urls = [url for url in image_urls if
                      (bindings.get(f"image:{_normalize_url(url)}") or {}).get("asset_role") == "activity_offer"]
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
        "node": "activity_image_response_commit" if state.get("material_identity_governed") else "activity_intro_image_send_record",
        "started_at": utc_now_iso(),
        "input_snapshot": compact(
            {
                "image_url": result.get("image_url", ""),
                "send_mode": result.get("send_mode", ""),
            }
        ),
        "tool_calls": [{"name": "record_material_response_commit" if state.get("material_identity_governed")
                        else "record_activity_intro_image_sent", "output": compact(result)}],
        "error": result.get("error"),
        "output_snapshot": compact(result),
    }
    entry["finished_at"] = utc_now_iso()
    entry["duration_ms"] = int((time.perf_counter() - started) * 1000)
    state.setdefault("trace", []).append(audit_snapshot(entry))


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
    destination = (
        resolution.get("destination_resolution")
        if isinstance(resolution.get("destination_resolution"), dict)
        else {}
    )
    evidence = {
        key: resolution.get(key)
        for key in (
            "raw_place",
            "normalized_query",
            "destination_fingerprint",
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
            "coverage_status",
            "scope_match_level",
            "recommendation_final_for_destination",
            "clarification_would_change_result",
        )
        if key in resolution
        and (
            isinstance(resolution.get(key), bool)
            or resolution.get(key) not in (None, "", [], {})
        )
    }
    for key in ("request_kind", "destination_precision"):
        if destination.get(key) not in (None, "", [], {}):
            evidence[key] = destination.get(key)
    return evidence


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
    version = str(request_context.get("interface_version") or "v3").strip().lower()
    return version if version in {"v1", "v2", "v3"} else "v1"


def _record_v3_phase(
    request_context: dict[str, Any],
    name: str,
    started: float,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record compact customer-path timings without changing decisions."""

    timings = request_context.setdefault("v3_phase_timings", {})
    if not isinstance(timings, dict):
        timings = {}
        request_context["v3_phase_timings"] = timings
    item = {
        "duration_ms": max(0, int((time.perf_counter() - started) * 1000)),
    }
    if metadata:
        item.update(compact(metadata, max_chars=1200))
    timings[str(name or "unknown")] = item
