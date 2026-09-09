from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid5
import zlib

from app.graph.planner.runtime_plan import planner_task_views
from app.graph.planner.runtime_plan import planner_public_route
from app.services.storage.serialization import (
    decode_run,
    decode_trace,
    dumps,
    loads_dict,
    loads_list,
    tags_from_state,
    utc_now_iso,
)
from app.services.trace_logger import compact
from app.services.run_observability import (
    build_v3_run_observability,
    enrich_v3_run_observability,
)
from app.services.storage.v3_strategy_analytics_repository import _usage_event_from_state
from app.services.v3_reply_recovery import (
    GENERATION_STATUS_COMPLETED,
    GENERATION_STATUS_FALLBACK_PENDING,
    decode_v3_recovery_payload,
    encode_v3_recovery_payload,
    stable_v3_reply_messages,
    v3_response_id,
)


class RunRepositoryMixin:
    def save_v3_terminal_no_reply(
        self,
        *,
        default_conversation_id: str,
        resolve_existing: bool,
        request: Any,
        request_id: str,
        title: str,
        input_snapshot: dict[str, Any],
        request_context: dict[str, Any],
        final_state: dict[str, Any],
        reply_messages: list[dict[str, Any]],
        token_usage: dict[str, Any],
        deferred_payload: dict[str, Any],
        response_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist terminal V3 paths and enqueue durable audit work.

        Human takeover, filtering, supersession and status-failure fallback do
        not need a sales graph.  Keeping their ingress, outreach cancellation,
        minimal BI row and finalization job in one transaction avoids multiple
        remote MySQL checkouts while preserving every required durable fact.
        """

        if not request_id:
            raise ValueError("request_id is required")
        operation_started = time.perf_counter()
        now = utc_now_iso()
        started_at = str(request_context.get("http_request_started_at") or now)
        statement_count = 0

        class _CountedConnection:
            def __init__(self, raw: Any) -> None:
                self.raw = raw

            def execute(self, *args: Any, **kwargs: Any) -> Any:
                nonlocal statement_count
                statement_count += 1
                return self.raw.execute(*args, **kwargs)

            def __getattr__(self, name: str) -> Any:
                return getattr(self.raw, name)

        with self.store.connect() as raw_conn:
            conn = _CountedConnection(raw_conn)
            generation_key = str(
                final_state.get("generation_key") or request_context.get("generation_key") or ""
            ).strip()
            response_id = str(
                final_state.get("response_id") or request_context.get("response_id") or ""
            ).strip()
            requested_generation_status = str(
                final_state.get("generation_status") or request_context.get("generation_status") or ""
            ).strip()
            generation_status = (
                requested_generation_status
                if requested_generation_status == GENERATION_STATUS_FALLBACK_PENDING
                else GENERATION_STATUS_COMPLETED
            )
            prepared = self._prepare_v3_request_in_connection(
                conn,
                default_conversation_id=default_conversation_id,
                resolve_existing=resolve_existing,
                request=request,
                request_id=request_id,
                title=title,
                input_snapshot=input_snapshot,
                interface_version=str(request_context.get("interface_version") or "v3"),
                started_at=started_at,
                http_request_ingress_id=str(request_context.get("http_request_ingress_id") or ""),
                generation_key=generation_key,
                response_id=response_id,
                generation_status=generation_status,
                recovery_kind=str(final_state.get("recovery_kind") or ""),
                recovery_next_at=str(final_state.get("recovery_next_at") or ""),
            )
            if bool(prepared.get("replayed")):
                return {
                    **prepared,
                    "duration_ms": max(0, int((time.perf_counter() - operation_started) * 1000)),
                    "connection_count": 1,
                    "statement_count": statement_count,
                    "outreach_cancel": {"cancelled_plans": 0, "skipped_tasks": 0},
                    "usage_event": {"status": "skipped", "reason": "generation_replayed"},
                }
            conversation_id = str(prepared.get("conversation_id") or "")
            response_id = str(prepared.get("response_id") or response_id)
            if response_id:
                reply_messages[:] = stable_v3_reply_messages(
                    reply_messages,
                    response_id=response_id,
                )
                final_state["reply_messages"] = reply_messages
            cancellation = {"cancelled_plans": 0, "skipped_tasks": 0}
            if (
                bool(request_context.get("memory_persist_allowed"))
                and not bool(request_context.get("test_isolated"))
                and str(getattr(request, "wechat", "") or "").strip()
            ):
                cancellation = self._cancel_outreach_for_customer_reply_in_connection(
                    conn,
                    customer_id=str(getattr(request, "customer_id", "") or ""),
                    corp_id=str(getattr(request, "corp_id", "") or ""),
                    wechat=str(getattr(request, "wechat", "") or ""),
                    external_userid=str(getattr(request, "external_userid", "") or ""),
                    request_id=request_id,
                    sales_contact_key=str(final_state.get("sales_contact_key") or ""),
                )

            final_state["decision_status"] = str(final_state.get("decision_status") or "system_guard")
            final_state["outreach_cancel"] = cancellation
            final_state["deferred_identity_observation"] = True
            final_state["service_rule_data_allow_empty_reply"] = not bool(reply_messages)
            output_snapshot = {
                "runtime_status": "completed",
                "runtime_phase": "reply_return_ready",
                "runtime_started_at": started_at,
                "runtime_updated_at": now,
                "runtime_processing_finished_at": now,
                "interface_version": str(request_context.get("interface_version") or "v3"),
                "http_request_ingress_id": str(request_context.get("http_request_ingress_id") or ""),
                "http_request_started_at": started_at,
                "reply_messages": reply_messages,
                "reply_source": str(final_state.get("reply_source") or ""),
                "decision_status": str(final_state.get("decision_status") or "system_guard"),
                "takeover_guard": final_state.get("takeover_guard", {}),
                "outreach_cancel": cancellation,
                "performance": {
                    "phases": final_state.get("v3_phase_timings")
                    if isinstance(final_state.get("v3_phase_timings"), dict)
                    else {},
                    "database": {
                        "connection_count": 1,
                        "statement_count_before_terminal_update": statement_count,
                        "elapsed_before_terminal_update_ms": max(
                            0,
                            int((time.perf_counter() - operation_started) * 1000),
                        ),
                    },
                },
                "post_reply_finalization": {
                    "status": "pending",
                    "attempts": 0,
                    "next_retry_at": "",
                    "enqueued_at": now,
                    "updated_at": now,
                    "last_error": "",
                },
                "post_reply_payload": deferred_payload,
            }
            if generation_key:
                output_snapshot["v3_response_snapshot"] = encode_v3_recovery_payload(
                    _response_snapshot_from_state(
                        request_id=request_id,
                        response_id=response_id,
                        reply_messages=reply_messages,
                        final_state=final_state,
                        response_snapshot=response_snapshot,
                    )
                )
                if generation_status == GENERATION_STATUS_FALLBACK_PENDING:
                    existing_run = conn.execute(
                        "SELECT output_snapshot FROM runs WHERE request_id=?",
                        (request_id,),
                    ).fetchone()
                    existing_output = loads_dict(existing_run["output_snapshot"]) if existing_run else {}
                    if existing_output.get("v3_recovery_payload"):
                        output_snapshot["v3_recovery_payload"] = existing_output["v3_recovery_payload"]
            duration_ms = _elapsed_ms(started_at, now)
            conn.execute(
                """
                UPDATE runs
                SET conversation_id=?, response_id=COALESCE(response_id, ?),
                    generation_status=?, recovery_kind=?, recovery_next_at=?,
                    output_snapshot=?, duration_ms=?, token_usage=?, error=?
                WHERE request_id=?
                """,
                (
                    conversation_id,
                    response_id or None,
                    generation_status if generation_key else "",
                    str(final_state.get("recovery_kind") or ""),
                    str(final_state.get("recovery_next_at") or ""),
                    dumps(output_snapshot),
                    duration_ms,
                    dumps(token_usage),
                    dumps(final_state.get("errors") or []) if final_state.get("errors") else "",
                    request_id,
                ),
            )
            if reply_messages:
                content = "\n".join(
                    str(item.get("content") or "")
                    for item in reply_messages
                    if isinstance(item, dict)
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO messages
                        (id, conversation_id, request_id, role, content, file_image, reply_messages, created_at)
                    VALUES (?, ?, ?, 'assistant', ?, '', ?, ?)
                    """,
                    (
                        str(uuid5(NAMESPACE_URL, f"v3-assistant:{request_id}")),
                        conversation_id,
                        request_id,
                        content,
                        dumps(reply_messages),
                        now,
                    ),
                )
            event = _usage_event_from_state(
                conversation_id=conversation_id,
                final_state=final_state,
                now=now,
            )
            usage_result = self._record_v3_strategy_usage_in_connection(conn, event=event)

        return {
            **prepared,
            "conversation_id": conversation_id,
            "duration_ms": max(0, int((time.perf_counter() - operation_started) * 1000)),
            "connection_count": 1,
            "statement_count": statement_count,
            "outreach_cancel": cancellation,
            "usage_event": usage_result,
            "generation_status": generation_status if generation_key else "",
        }

    def save_v3_reply_core(
        self,
        *,
        conversation_id: str,
        final_state: dict[str, Any],
        reply_messages: list[dict[str, Any]],
        token_usage: dict[str, Any],
        deferred_payload: dict[str, Any],
        response_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist the customer-visible reply and a durable finalization job.

        This is the only persistence required before returning the V3 response.
        The worker later expands traces, BI, shadow plans and callbacks from the
        payload.  Keeping both writes in one transaction prevents a visible
        reply from losing its recoverable audit job.
        """

        request_id = str(final_state.get("request_id") or "")
        if not request_id:
            raise ValueError("request_id is required")
        operation_started = time.perf_counter()
        now = utc_now_iso()
        statement_count = 0
        with self.store.connect() as conn:
            def execute(*args: Any, **kwargs: Any) -> Any:
                nonlocal statement_count
                statement_count += 1
                return conn.execute(*args, **kwargs)

            existing = execute(
                """
                SELECT output_snapshot, created_at, generation_key, response_id,
                       generation_status, recovery_kind
                FROM runs WHERE request_id=?
                """,
                (request_id,),
            ).fetchone()
            output_snapshot = loads_dict(existing["output_snapshot"]) if existing else {}
            request_context = (
                final_state.get("request_context")
                if isinstance(final_state.get("request_context"), dict)
                else {}
            )
            generation_key = str(
                (existing["generation_key"] if existing else "")
                or final_state.get("generation_key")
                or request_context.get("generation_key")
                or ""
            ).strip()
            response_id = str(
                (existing["response_id"] if existing else "")
                or final_state.get("response_id")
                or request_context.get("response_id")
                or ""
            ).strip() or v3_response_id(generation_key)
            requested_generation_status = str(
                final_state.get("generation_status")
                or request_context.get("generation_status")
                or ""
            ).strip()
            saved_generation_status = (
                GENERATION_STATUS_FALLBACK_PENDING
                if requested_generation_status == GENERATION_STATUS_FALLBACK_PENDING
                else GENERATION_STATUS_COMPLETED
            )
            if response_id:
                reply_messages[:] = stable_v3_reply_messages(
                    reply_messages,
                    response_id=response_id,
                )
                final_state["reply_messages"] = reply_messages
            started_at = str(
                output_snapshot.get("runtime_started_at")
                or (existing["created_at"] if existing else "")
                or now
            )
            output_snapshot.update(
                {
                    "runtime_status": "completed",
                    "runtime_phase": "reply_return_ready",
                    "runtime_started_at": started_at,
                    "runtime_updated_at": now,
                    "runtime_processing_finished_at": now,
                    "reply_messages": reply_messages,
                    "reply_source": str(final_state.get("reply_source") or ""),
                    "decision_status": str(final_state.get("decision_status") or ""),
                    "realtime_intent": final_state.get("realtime_intent", {}),
                    "emotion_decision": final_state.get("emotion_decision", {}),
                    "closing_decision": final_state.get("closing_decision", {}),
                    "cardpoint_decision": final_state.get("cardpoint_decision", {}),
                    "post_reply_finalization": {
                        "status": "pending",
                        "attempts": 0,
                        "next_retry_at": "",
                        "enqueued_at": now,
                        "updated_at": now,
                        "last_error": "",
                    },
                    # This temporary payload is removed after finalization and is
                    # stripped from all admin API responses while it is pending.
                    "post_reply_payload": deferred_payload,
                    "performance": {
                        "phases": final_state.get("v3_phase_timings")
                        if isinstance(final_state.get("v3_phase_timings"), dict)
                        else {},
                        "database": {
                            "connection_count": 1,
                            "statement_count": 2 + (1 if reply_messages else 0),
                            "elapsed_before_final_write_ms": max(
                                0,
                                int((time.perf_counter() - operation_started) * 1000),
                            ),
                        },
                    },
                }
            )
            if generation_key:
                if saved_generation_status != GENERATION_STATUS_FALLBACK_PENDING:
                    output_snapshot.pop("v3_recovery_payload", None)
                output_snapshot["v3_response_snapshot"] = encode_v3_recovery_payload(
                    _response_snapshot_from_state(
                        request_id=request_id,
                        response_id=response_id,
                        reply_messages=reply_messages,
                        final_state=final_state,
                        response_snapshot=response_snapshot,
                    )
                )
            duration_ms = _elapsed_ms(started_at, now)
            if existing:
                execute(
                    """
                    UPDATE runs
                    SET response_id=COALESCE(response_id, ?), generation_status=?,
                        recovery_kind=?, recovery_next_at=?, recovery_error='', output_snapshot=?,
                        duration_ms=?, token_usage=?, error=?
                    WHERE request_id=?
                    """,
                    (
                        response_id or None,
                        saved_generation_status if generation_key else "",
                        str(final_state.get("recovery_kind") or ""),
                        (
                            str(final_state.get("recovery_next_at") or "")
                            if saved_generation_status == GENERATION_STATUS_FALLBACK_PENDING
                            else ""
                        ),
                        dumps(output_snapshot),
                        duration_ms,
                        dumps(token_usage),
                        dumps(final_state.get("errors") or []) if final_state.get("errors") else "",
                        request_id,
                    ),
                )
            else:
                execute(
                    """
                    INSERT INTO runs
                        (request_id, conversation_id, customer_id, generation_key, response_id,
                         generation_status, recovery_kind, recovery_next_at,
                         input_snapshot, output_snapshot, intents, tags,
                         duration_ms, token_usage, error, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        conversation_id,
                        str(final_state.get("customer_id") or ""),
                        generation_key or None,
                        response_id or None,
                        saved_generation_status if generation_key else "",
                        str(final_state.get("recovery_kind") or ""),
                        (
                            str(final_state.get("recovery_next_at") or "")
                            if saved_generation_status == GENERATION_STATUS_FALLBACK_PENDING
                            else ""
                        ),
                        dumps({}),
                        dumps(output_snapshot),
                        dumps([]),
                        dumps([]),
                        duration_ms,
                        dumps(token_usage),
                        dumps(final_state.get("errors") or []) if final_state.get("errors") else "",
                        started_at,
                    ),
                )
            if reply_messages:
                content = "\n".join(
                    str(item.get("content") or "")
                    for item in reply_messages
                    if isinstance(item, dict)
                )
                execute(
                    """
                    INSERT OR IGNORE INTO messages
                        (id, conversation_id, request_id, role, content, file_image, reply_messages, created_at)
                    VALUES (?, ?, ?, 'assistant', ?, '', ?, ?)
                    """,
                    (
                        str(uuid5(NAMESPACE_URL, f"v3-assistant:{request_id}")),
                        conversation_id,
                        request_id,
                        content,
                        dumps(reply_messages),
                        now,
                    ),
                )
        return {
            "duration_ms": max(0, int((time.perf_counter() - operation_started) * 1000)),
            "connection_count": 1,
            "statement_count": statement_count,
            "generation_key": generation_key,
            "response_id": response_id,
            "generation_status": saved_generation_status if generation_key else "",
        }
    def claim_v3_reply_finalizations(self, *, limit: int = 10) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        stale_before = (now - timedelta(minutes=5)).isoformat()
        recent_cutoff = (now - timedelta(days=7)).isoformat()
        claimed: list[dict[str, Any]] = []
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT request_id, conversation_id, output_snapshot, token_usage
                FROM runs
                WHERE created_at >= ?
                  AND output_snapshot LIKE '%"post_reply_finalization"%'
                  AND (
                       output_snapshot LIKE '%"status": "pending"%'
                    OR output_snapshot LIKE '%"status":"pending"%'
                    OR output_snapshot LIKE '%"status": "error"%'
                    OR output_snapshot LIKE '%"status":"error"%'
                    OR output_snapshot LIKE '%"status": "processing"%'
                    OR output_snapshot LIKE '%"status":"processing"%'
                  )
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (recent_cutoff, max(1, min(int(limit or 10), 100))),
            ).fetchall()
            for row in rows:
                output = loads_dict(row["output_snapshot"])
                job = output.get("post_reply_finalization")
                payload = _decode_post_reply_payload(output.get("post_reply_payload"))
                if not isinstance(job, dict) or not isinstance(payload, dict):
                    continue
                status = str(job.get("status") or "")
                updated_at = str(job.get("updated_at") or "")
                if status == "processing" and updated_at and updated_at > stale_before:
                    continue
                if status not in {"pending", "error", "processing"}:
                    continue
                next_retry_at = str(job.get("next_retry_at") or "")
                if next_retry_at and next_retry_at > now.isoformat():
                    continue
                job["status"] = "processing"
                job["updated_at"] = now.isoformat()
                output["post_reply_finalization"] = job
                conn.execute(
                    "UPDATE runs SET output_snapshot=? WHERE request_id=?",
                    (dumps(output), str(row["request_id"] or "")),
                )
                claimed.append(
                    {
                        "request_id": str(row["request_id"] or ""),
                        "conversation_id": str(row["conversation_id"] or ""),
                        "final_state": payload,
                        "token_usage": loads_dict(row["token_usage"]),
                    }
                )
        return claimed

    def finish_v3_reply_finalization(
        self,
        *,
        request_id: str,
        error: str = "",
    ) -> None:
        self.finish_v3_reply_finalizations(
            [{"request_id": request_id, "error": error}],
        )

    def finish_v3_reply_finalizations(
        self,
        results: list[dict[str, Any]],
    ) -> None:
        """Finish a claimed batch with one checkout and one commit."""

        if not results:
            return
        now = datetime.now(timezone.utc)
        with self.store.connect() as conn:
            for result in results:
                request_id = str(result.get("request_id") or "")
                if not request_id:
                    continue
                error = str(result.get("error") or "")
                row = conn.execute(
                    "SELECT output_snapshot FROM runs WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if not row:
                    continue
                output = loads_dict(row["output_snapshot"])
                job = output.get("post_reply_finalization")
                if not isinstance(job, dict):
                    job = {}
                attempts = int(job.get("attempts") or 0) + (1 if error else 0)
                if error:
                    retry_seconds = min(300, 2 ** min(attempts, 8))
                    job.update(
                        {
                            "status": "failed" if attempts >= 6 else "error",
                            "attempts": attempts,
                            "next_retry_at": (now + timedelta(seconds=retry_seconds)).isoformat(),
                            "updated_at": now.isoformat(),
                            "last_error": error[:1000],
                        }
                    )
                else:
                    enqueued_at = str(job.get("enqueued_at") or "")
                    job.update(
                        {
                            "status": "completed",
                            "attempts": attempts,
                            "next_retry_at": "",
                            "updated_at": now.isoformat(),
                            "finished_at": now.isoformat(),
                            "duration_ms": (
                                _elapsed_ms(enqueued_at, now.isoformat()) if enqueued_at else 0
                            ),
                            "last_error": "",
                        }
                    )
                    output.pop("post_reply_payload", None)
                    output["runtime_phase"] = "completed"
                output["post_reply_finalization"] = job
                conn.execute(
                    "UPDATE runs SET output_snapshot=? WHERE request_id=?",
                    (dumps(output), request_id),
                )

    def save_platform_protocol_run(
        self,
        *,
        request_id: str,
        conversation_id: str,
        customer_id: str,
        external_userid: str,
        corp_id: str,
        user_id: str,
        wechat: str,
        content: str,
        request_context: dict[str, Any],
        protocol_event: dict[str, str],
        token_usage: dict[str, Any],
    ) -> None:
        """Persist a protocol-only request with one database checkout.

        Protocol messages never enter the customer conversation or sales graph.
        Keeping their conversation shell, run and single trace row in one
        transaction avoids turning a no-op request into several RDS round trips.
        """

        started_at = str(request_context.get("http_request_started_at") or utc_now_iso())
        finished_at = utc_now_iso()
        ingress_id = str(request_context.get("http_request_ingress_id") or "")
        interface_version = str(
            request_context.get("interface_version")
            or request_context.get("api_version")
            or "v3"
        ).strip().lower()
        if interface_version not in {"v1", "v2", "v3"}:
            interface_version = "v3"
        reason = str(protocol_event.get("reason") or "platform_protocol_ignored")
        message_type = str(protocol_event.get("message_type") or "platform_protocol")
        reply_source = str(protocol_event.get("reply_source") or "platform_protocol_ignored")
        input_snapshot = {
            "content": content,
            "customer_id": customer_id,
            "corp_id": corp_id,
            "user_id": user_id,
            "wechat": wechat,
            "external_userid": external_userid,
            "request_context": request_context,
        }
        output_snapshot = {
            "runtime_status": "completed",
            "runtime_phase": "completed",
            "runtime_started_at": started_at,
            "runtime_updated_at": finished_at,
            "runtime_finished_at": finished_at,
            "interface_version": interface_version,
            "reply_chain_mode": str(request_context.get("reply_chain_mode") or ""),
            "v3_sidecar": bool(request_context.get("v3_sidecar")),
            "http_request_ingress_id": ingress_id,
            "http_request_started_at": started_at,
            "http_response_reply_messages": [],
            "reply_messages": [],
            "reply_source": reply_source,
            "decision_status": "skipped",
            "decision_reasons": [reason],
            "platform_protocol_event": dict(protocol_event),
        }
        duration_ms = _elapsed_ms(started_at, finished_at)
        trace_id = f"{request_id}_0"
        with self.store.connect() as conn:
            # Protocol events must not update the customer's chat state.  The
            # shell row only satisfies the run FK when this is the first event
            # observed for an identity.
            conn.execute(
                """
                INSERT OR IGNORE INTO conversations
                    (id, customer_id, external_userid, corp_id, user_id, wechat, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    customer_id,
                    external_userid,
                    corp_id,
                    user_id,
                    wechat,
                    "",
                    started_at,
                    started_at,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO runs
                    (request_id, conversation_id, customer_id, input_snapshot, output_snapshot, intents, tags,
                     duration_ms, token_usage, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    conversation_id,
                    customer_id,
                    dumps(compact(input_snapshot)),
                    dumps(_compact_run_output(output_snapshot)),
                    "[]",
                    "[]",
                    duration_ms,
                    dumps(token_usage),
                    "",
                    started_at,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO node_traces
                    (id, request_id, node_name, input_snapshot, output_snapshot, tool_calls, duration_ms, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    request_id,
                    "platform_protocol_filter",
                    dumps({"message_type": message_type}),
                    dumps({"decision": "no_reply", "reason": reason}),
                    "[]",
                    0,
                    "",
                    finished_at,
                ),
            )

    def start_run(
        self,
        *,
        request_id: str,
        conversation_id: str,
        customer_id: str,
        input_snapshot: dict[str, Any],
        interface_version: str = "v1",
        started_at: str = "",
        http_request_ingress_id: str = "",
    ) -> None:
        """Persist the request before model execution so it is visible live."""

        started_at = str(started_at or utc_now_iso())
        version = str(interface_version or "v1").strip().lower()
        if version not in {"v1", "v2", "v3"}:
            version = "v1"
        output_snapshot = {
            "runtime_status": "running",
            "runtime_phase": "request_received",
            "runtime_started_at": started_at,
            "runtime_updated_at": started_at,
            "interface_version": version,
            "http_request_ingress_id": str(http_request_ingress_id or ""),
            "http_request_started_at": started_at,
        }
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO runs
                    (request_id, conversation_id, customer_id, input_snapshot, output_snapshot, intents, tags,
                     duration_ms, token_usage, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    conversation_id,
                    customer_id,
                    dumps(compact(input_snapshot)),
                    dumps(output_snapshot),
                    "[]",
                    "[]",
                    0,
                    "{}",
                    "",
                    started_at,
                ),
            )

    def update_run_progress(self, *, request_id: str, phase: str) -> None:
        """Update only observability metadata; never alter business state."""

        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT output_snapshot FROM runs WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if not row:
                return
            output_snapshot = loads_dict(row["output_snapshot"])
            if str(output_snapshot.get("runtime_status") or "") not in {"", "running"}:
                return
            output_snapshot["runtime_status"] = "running"
            output_snapshot["runtime_phase"] = str(phase or "running")
            output_snapshot["runtime_updated_at"] = utc_now_iso()
            conn.execute(
                "UPDATE runs SET output_snapshot=? WHERE request_id=?",
                (dumps(_compact_run_output(output_snapshot)), request_id),
            )

    def save_run(self, *, conversation_id: str, final_state: dict[str, Any], token_usage: dict[str, Any]) -> None:
        request_id = str(final_state.get("request_id") or "")
        trace = final_state.get("trace") or []
        trace_duration_ms = sum(int(item.get("duration_ms") or 0) for item in trace if isinstance(item, dict))
        errors = final_state.get("errors") or []
        error = dumps(errors) if errors else ""
        input_snapshot = {
            "content": final_state.get("content", ""),
            "customer_id": final_state.get("customer_id", ""),
            "corp_id": final_state.get("corp_id", ""),
            "conversation_history": final_state.get("conversation_history", []),
            "file_image": bool(final_state.get("file_image")),
            "user_id": final_state.get("user_id"),
            "wechat": final_state.get("wechat"),
            "external_userid": final_state.get("external_userid"),
            "customer_add_wechat_id": final_state.get("customer_add_wechat_id"),
            "confirmed_store_id": final_state.get("confirmed_store_id"),
            "confirmed_store_name": final_state.get("confirmed_store_name"),
            "store_id": final_state.get("store_id"),
            "store_name": final_state.get("store_name"),
            "appointment_id": final_state.get("appointment_id"),
            "appointment_time": final_state.get("appointment_time"),
            "request_context": final_state.get("request_context", {}),
        }
        request_context = (
            final_state.get("request_context")
            if isinstance(final_state.get("request_context"), dict)
            else {}
        )
        interface_version = str(
            request_context.get("interface_version")
            or request_context.get("api_version")
            or "v1"
        ).strip().lower()
        if interface_version not in {"v1", "v2", "v3"}:
            interface_version = "v1"
        output_snapshot = {
            "reply_messages": final_state.get("reply_messages", []),
            "interface_version": interface_version,
            "reply_chain_mode": str(request_context.get("reply_chain_mode") or ""),
            "v3_sidecar": bool(request_context.get("v3_sidecar")),
            "strategy_data_callback": final_state.get("strategy_data_callback", {}),
            "planner_route": planner_public_route(final_state),
            "planner_source": final_state.get("planner_source", ""),
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
            "reply_control": final_state.get("reply_control", {}),
            "async_final_reply": final_state.get("async_final_reply", {}),
            "performance": {
                "phases": final_state.get("v3_phase_timings", {}),
            },
            "postprocess_changed": bool(final_state.get("postprocess_changed")),
            "postprocess_reasons": final_state.get("postprocess_reasons", []),
            "warnings": final_state.get("warnings", []),
            "decision_status": final_state.get("decision_status", ""),
            "decision_reasons": final_state.get("decision_reasons", []),
            "primary_task": final_state.get("primary_task", {}),
            "secondary_tasks": final_state.get("secondary_tasks", []),
            "realtime_intent": final_state.get("realtime_intent", {}),
            "emotion_decision": final_state.get("emotion_decision", {}),
            "closing_decision": final_state.get("closing_decision", {}),
            "cardpoint_decision": final_state.get("cardpoint_decision", {}),
            "cardpoint_candidates": final_state.get("cardpoint_candidates", []),
            "followup_strategy_candidates": final_state.get("followup_strategy_candidates", []),
            "sales_strategy_retrieval_audit": final_state.get("sales_strategy_retrieval_audit", {}),
            "closing_sequence_shadow": final_state.get("closing_sequence_shadow", {}),
            "order_state_snapshot": _compact_order_state_snapshot(final_state),
            "ai_sales_policy": {
                "schema_version": (final_state.get("ai_sales_policy") or {}).get("schema_version", ""),
                "policy_version": (final_state.get("ai_sales_policy") or {}).get("policy_version", ""),
                "checksum": (final_state.get("ai_sales_policy") or {}).get("checksum", ""),
                "runtime_mode": (final_state.get("ai_sales_policy") or {}).get("runtime_mode", ""),
                "runtime_health": (final_state.get("ai_sales_policy") or {}).get("runtime_health", {}),
            },
            "sales_strategy_catalog": {
                "schema_version": (final_state.get("sales_strategy_catalog") or {}).get("schema_version", ""),
                "catalog_version": (final_state.get("sales_strategy_catalog") or {}).get("catalog_version", ""),
                "checksum": (final_state.get("sales_strategy_catalog") or {}).get("checksum", ""),
                "runtime_mode": (final_state.get("sales_strategy_catalog") or {}).get("runtime_mode", ""),
                "runtime_health": (final_state.get("sales_strategy_catalog") or {}).get("runtime_health", {}),
            },
            "handoff": final_state.get("handoff", {}),
            "profile_update": final_state.get("profile_update", {}),
            "event_updates": final_state.get("event_updates", []),
            "observability_v3": build_v3_run_observability(final_state),
        }
        with self.store.connect() as conn:
            existing = conn.execute(
                """
                SELECT output_snapshot, created_at, generation_key, response_id,
                       generation_status, recovery_kind, recovery_attempts,
                       recovery_next_at, recovery_dispatch_id, recovery_error
                FROM runs WHERE request_id=?
                """,
                (request_id,),
            ).fetchone()
            existing_output: dict[str, Any] = {}
            started_at = ""
            if existing:
                existing_output = loads_dict(existing["output_snapshot"])
                # ``start_run`` owns the HTTP ingress identity.  The final graph
                # snapshot must not erase it; otherwise the response middleware
                # cannot attach the real end-to-end duration to the run.
                for key in (
                    "http_request_ingress_id",
                    "http_request_started_at",
                    "http_response_finished_at",
                    "http_duration_ms",
                    "http_response_body",
                    "http_response_reply_messages",
                    "runtime_finished_at",
                    "runtime_processing_finished_at",
                    "post_reply_finalization",
                    "post_reply_payload",
                    "v3_recovery_payload",
                    "v3_response_snapshot",
                    "performance",
                ):
                    if key in existing_output and key not in output_snapshot:
                        output_snapshot[key] = existing_output[key]
                existing_performance = (
                    existing_output.get("performance")
                    if isinstance(existing_output.get("performance"), dict)
                    else {}
                )
                current_performance = (
                    output_snapshot.get("performance")
                    if isinstance(output_snapshot.get("performance"), dict)
                    else {}
                )
                output_snapshot["performance"] = {
                    **existing_performance,
                    **current_performance,
                    "phases": current_performance.get("phases")
                    or existing_performance.get("phases")
                    or {},
                }
                started_at = str(existing_output.get("runtime_started_at") or existing["created_at"] or "")
            finished_at = utc_now_iso()
            processing_finished_at = str(
                existing_output.get("runtime_processing_finished_at")
                or output_snapshot.get("runtime_processing_finished_at")
                or ""
            )
            duration_ms = (
                int(existing_output.get("http_duration_ms") or 0)
                or (
                    _elapsed_ms(started_at, processing_finished_at)
                    if started_at and processing_finished_at
                    else (_elapsed_ms(started_at, finished_at) if started_at else trace_duration_ms)
                )
            )
            customer_finished_at = str(
                existing_output.get("http_response_finished_at")
                or existing_output.get("runtime_finished_at")
                or finished_at
            )
            output_snapshot = {
                "runtime_status": "completed_with_errors" if errors else "completed",
                "runtime_phase": "completed",
                "runtime_started_at": started_at or finished_at,
                "runtime_updated_at": finished_at,
                "runtime_finished_at": customer_finished_at,
                **output_snapshot,
                "interface_version": str(
                    existing_output.get("interface_version") or interface_version
                ),
            }
            conn.execute(
                """
                INSERT OR REPLACE INTO runs
                    (request_id, conversation_id, customer_id, generation_key, response_id,
                     generation_status, recovery_kind, recovery_attempts, recovery_next_at,
                     recovery_dispatch_id, recovery_error, input_snapshot, output_snapshot, intents, tags,
                     duration_ms, token_usage, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    conversation_id,
                    str(final_state.get("customer_id") or ""),
                    (existing["generation_key"] if existing else None),
                    (existing["response_id"] if existing else None),
                    str(existing["generation_status"] or "") if existing else "",
                    str(existing["recovery_kind"] or "") if existing else "",
                    int(existing["recovery_attempts"] or 0) if existing else 0,
                    str(existing["recovery_next_at"] or "") if existing else "",
                    str(existing["recovery_dispatch_id"] or "") if existing else "",
                    str(existing["recovery_error"] or "") if existing else "",
                    dumps(compact(input_snapshot)),
                    dumps(_compact_run_output(output_snapshot)),
                    dumps(planner_task_views(final_state)),
                    dumps(tags_from_state(final_state)),
                    duration_ms,
                    dumps(token_usage),
                    error,
                    started_at or finished_at,
                ),
            )
            trace_rows = [
                (
                    f"{request_id}_{index}",
                    request_id,
                    str(entry.get("node") or ""),
                    dumps(entry.get("input_snapshot") or {}),
                    dumps(entry.get("output_snapshot") or {}),
                    dumps(entry.get("tool_calls") or []),
                    int(entry.get("duration_ms") or 0),
                    str(entry.get("error") or ""),
                    str(entry.get("started_at") or finished_at),
                )
                for index, entry in enumerate(trace)
                if isinstance(entry, dict)
            ]
            if trace_rows:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO node_traces
                        (id, request_id, node_name, input_snapshot, output_snapshot, tool_calls, duration_ms, error, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    trace_rows,
                )

    def update_run_http_response(self, *, request_id: str, response_body: dict[str, Any]) -> None:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT output_snapshot, generation_key FROM runs WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if not row:
                return
            output_snapshot = loads_dict(row["output_snapshot"])
            output_snapshot["http_response_body"] = response_body
            output_snapshot["http_response_reply_messages"] = _reply_messages_from_http_response(response_body)
            if str(row["generation_key"] or ""):
                stored_response = decode_v3_recovery_payload(output_snapshot.get("v3_response_snapshot"))
                if "chat_response" not in stored_response and "http_response" not in stored_response:
                    stored_response = {"chat_response": stored_response}
                stored_response["http_response"] = response_body
                output_snapshot["v3_response_snapshot"] = encode_v3_recovery_payload(stored_response)
            conn.execute(
                "UPDATE runs SET output_snapshot=? WHERE request_id=?",
                (dumps(_compact_run_output(output_snapshot)), request_id),
            )

    def finalize_run_http_timing(
        self,
        *,
        request_id: str,
        ingress_id: str,
        started_at: str,
        finished_at: str,
        duration_ms: int,
        response_body: dict[str, Any] | None = None,
    ) -> bool:
        """Finalize timing only for the ingress that created this run.

        Platform retries may reuse an earlier result and request_id. The ingress
        identity check prevents a cached retry from replacing the original run's
        lifecycle duration with the retry's much shorter HTTP duration.
        """

        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT output_snapshot, duration_ms, generation_key FROM runs WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if not row:
                return False
            output_snapshot = loads_dict(row["output_snapshot"])
            stored_ingress_id = str(output_snapshot.get("http_request_ingress_id") or "")
            if not ingress_id or stored_ingress_id != str(ingress_id):
                return False
            processing_finished_at = str(
                output_snapshot.get("runtime_processing_finished_at")
                or output_snapshot.get("runtime_finished_at")
                or ""
            )
            if processing_finished_at:
                output_snapshot["runtime_processing_finished_at"] = processing_finished_at
            effective_duration_ms = max(int(row["duration_ms"] or 0), max(0, int(duration_ms or 0)))
            output_snapshot.update(
                {
                    "http_request_started_at": str(started_at or output_snapshot.get("http_request_started_at") or ""),
                    "http_response_finished_at": str(finished_at or ""),
                    "http_duration_ms": effective_duration_ms,
                    "runtime_finished_at": str(finished_at or processing_finished_at),
                    "runtime_updated_at": str(finished_at or output_snapshot.get("runtime_updated_at") or ""),
                }
            )
            if isinstance(response_body, dict):
                output_snapshot["http_response_body"] = response_body
                output_snapshot["http_response_reply_messages"] = _reply_messages_from_http_response(response_body)
                if str(row["generation_key"] or ""):
                    stored_response = decode_v3_recovery_payload(
                        output_snapshot.get("v3_response_snapshot")
                    )
                    if "chat_response" not in stored_response and "http_response" not in stored_response:
                        stored_response = {"chat_response": stored_response}
                    stored_response["http_response"] = response_body
                    output_snapshot["v3_response_snapshot"] = encode_v3_recovery_payload(
                        stored_response
                    )
            conn.execute(
                "UPDATE runs SET output_snapshot=?, duration_ms=? WHERE request_id=?",
                (dumps(_compact_run_output(output_snapshot)), effective_duration_ms, request_id),
            )
        return True

    def list_runs(
        self,
        *,
        limit: int = 50,
        customer_id: str = "",
        conversation_id: str = "",
        has_error: bool | None = None,
        started_from: str = "",
        started_to: str = "",
        wechat: str = "",
        run_status: str = "",
        intent_code: str = "",
        emotion_code: str = "",
        checkpoint_code: str = "",
        decision_status: str = "",
        sequence_matched: bool | None = None,
        sequence_adopted: bool | None = None,
        script_adopted: bool | None = None,
        node_failed: bool | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if customer_id:
            clauses.append("r.customer_id=?")
            params.append(customer_id)
        if conversation_id:
            clauses.append("r.conversation_id=?")
            params.append(conversation_id)
        if has_error is True:
            clauses.append("r.error<>''")
        elif has_error is False:
            clauses.append("r.error=''")
        if started_from:
            clauses.append("r.created_at>=?")
            params.append(started_from)
        if started_to:
            clauses.append("r.created_at<=?")
            params.append(started_to)
        if wechat:
            clauses.append("c.wechat=?")
            params.append(wechat)
        if intent_code:
            clauses.append("u.intent_code=?")
            params.append(intent_code)
        if emotion_code:
            clauses.append("u.emotion_before=?")
            params.append(emotion_code)
        if checkpoint_code:
            clauses.append("u.checkpoint_code=?")
            params.append(checkpoint_code)
        if decision_status:
            clauses.append("u.decision_status=?")
            params.append(decision_status)
        if sequence_matched is not None:
            clauses.append("u.id IS NOT NULL AND u.sequence_candidate_count" + (">0" if sequence_matched else "=0"))
        if sequence_adopted is not None:
            clauses.append("u.id IS NOT NULL AND u.sequence_id" + ("<>''" if sequence_adopted else "=''"))
        if script_adopted is not None:
            clauses.append("u.id IS NOT NULL AND u.script_id" + ("<>''" if script_adopted else "=''"))
        if node_failed is not None:
            existence = "EXISTS" if node_failed else "NOT EXISTS"
            clauses.append(
                f"{existence} (SELECT 1 FROM node_traces nt WHERE nt.request_id=r.request_id AND nt.error<>'')"
            )
        normalized_status = str(run_status or "").strip().lower()
        if normalized_status == "failed":
            clauses.append("r.error<>''")
        elif normalized_status == "degraded":
            clauses.append("u.decision_status='degraded'")
        elif normalized_status == "fallback":
            clauses.append("u.fallback_used=1")
        elif normalized_status == "delivery_failed":
            clauses.append("u.delivery_status IN ('send_failed','partial_failed','delivery_failed')")
        elif normalized_status == "success":
            clauses.extend(
                [
                    "r.error=''",
                    "COALESCE(u.decision_status,'')<>'degraded'",
                    "COALESCE(u.fallback_used,0)=0",
                    "COALESCE(u.delivery_status,'') NOT IN ('send_failed','partial_failed','delivery_failed')",
                ]
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 200)))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT r.request_id, r.conversation_id, r.customer_id,
                       r.generation_key, r.response_id, r.generation_status,
                       r.recovery_kind, r.recovery_attempts, r.recovery_next_at,
                       r.recovery_dispatch_id, r.recovery_error,
                       r.input_snapshot, r.output_snapshot,
                       r.intents, r.tags, r.duration_ms, r.token_usage, r.error, r.created_at,
                       COALESCE(c.wechat, '') AS contact_wechat,
                       u.intent_code AS usage_intent_code,
                       u.emotion_before AS usage_emotion_code,
                       u.checkpoint_code AS usage_checkpoint_code,
                       u.checkpoint_name AS usage_checkpoint_name,
                       u.sequence_candidate_count AS usage_sequence_candidate_count,
                       u.sequence_id AS usage_sequence_id,
                       u.script_id AS usage_script_id,
                       u.decision_status AS usage_decision_status,
                       u.fallback_used AS usage_fallback_used,
                       u.delivery_status AS usage_delivery_status
                FROM runs r
                LEFT JOIN conversations c ON c.id=r.conversation_id
                LEFT JOIN v3_strategy_usage_events u ON u.request_id=r.request_id
                {where}
                ORDER BY r.created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_run_list_view(decode_run(dict(row))) for row in rows]

    def get_run(self, request_id: str, *, include_debug: bool = True) -> dict[str, Any]:
        dispatch_id = ""
        usage_event: dict[str, Any] = {}
        with self.store.connect() as conn:
            run = conn.execute("SELECT * FROM runs WHERE request_id=?", (request_id,)).fetchone()
            traces = (
                conn.execute(
                    "SELECT * FROM node_traces WHERE request_id=? ORDER BY created_at ASC",
                    (request_id,),
                ).fetchall()
                if include_debug
                else conn.execute(
                    """
                    SELECT id, request_id, node_name, '{}' AS input_snapshot,
                           '{}' AS output_snapshot, '[]' AS tool_calls,
                           duration_ms, error, created_at
                    FROM node_traces WHERE request_id=? ORDER BY created_at ASC
                    """,
                    (request_id,),
                ).fetchall()
            )
            decoded_run = decode_run(dict(run)) if run else {}
            output_snapshot = (
                decoded_run.get("output_snapshot")
                if isinstance(decoded_run.get("output_snapshot"), dict)
                else {}
            )
            output_snapshot.pop("post_reply_payload", None)
            output_snapshot.pop("v3_recovery_payload", None)
            callback = (
                output_snapshot.get("strategy_data_callback")
                if isinstance(output_snapshot.get("strategy_data_callback"), dict)
                else {}
            )
            outbox_id = str(callback.get("outbox_id") or "").strip()
            if outbox_id and include_debug:
                callback_row = conn.execute(
                    """
                    SELECT status, retry_count, error, payload_json, response_json,
                           created_at, updated_at, sent_at
                    FROM strategy_data_outbox WHERE id=?
                    """,
                    (outbox_id,),
                ).fetchone()
                if callback_row:
                    request_payload = loads_dict(callback_row["payload_json"])
                    response = loads_dict(callback_row["response_json"])
                    output_snapshot["strategy_data_callback"] = {
                        **callback,
                        "status": str(callback_row["status"] or ""),
                        "retry_count": int(callback_row["retry_count"] or 0),
                        "error": str(callback_row["error"] or ""),
                        "created_at": str(callback_row["created_at"] or ""),
                        "updated_at": str(callback_row["updated_at"] or ""),
                        "sent_at": str(callback_row["sent_at"] or ""),
                        "request_payload": request_payload,
                        "response": response,
                        "response_code": response.get("code"),
                        "response_message": str(response.get("message") or ""),
                    }
            dispatch_row = conn.execute(
                """
                SELECT id FROM message_dispatches
                WHERE source_request_id=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            dispatch_id = str(dispatch_row["id"] or "") if dispatch_row else ""
            usage_row = conn.execute(
                """
                SELECT request_id, intent_code, intent_confidence, intent_secondary_json,
                       emotion_before, emotion_confidence, emotion_pressure, emotion_flow_action,
                       checkpoint_type_id, checkpoint_code, checkpoint_name, checkpoint_tag_id,
                       checkpoint_tag_name, friction_status, sequence_id, sequence_name,
                       sequence_step_id, script_id, script_code, script_name, action_code, action_name,
                       sequence_candidate_count, script_candidate_count, adopted, selector_status,
                       retrieval_mode, fallback_used, decision_status, decision_reasons_json,
                       cardpoint_category_key, cardpoint_state,
                       closing_action, closing_strategy_code, closing_node_key, closing_trigger,
                       closing_customer_state, closing_pressure, closing_primary_rule_id,
                       closing_primary_rule_name, closing_sequence_name, closing_node_name,
                       closing_rule_match_status, closing_constraint_status,
                       closing_constraint_reasons_json, delivery_status, delivered_at, failed_reason
                FROM v3_strategy_usage_events WHERE request_id=? LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            if usage_row:
                usage_event = _decode_usage_event(dict(usage_row))
        dispatch = self.get_message_dispatch(dispatch_id) if dispatch_id else {}
        enrich_v3_run_observability(output_snapshot, dispatch=dispatch)
        summary_source = {
            **decoded_run,
            "contact_wechat": str(_dict_value(decoded_run, "input_snapshot").get("wechat") or ""),
            "usage_intent_code": usage_event.get("intent_code") if usage_event else None,
            "usage_emotion_code": usage_event.get("emotion_before") if usage_event else None,
            "usage_checkpoint_code": usage_event.get("checkpoint_code") if usage_event else None,
            "usage_checkpoint_name": usage_event.get("checkpoint_name") if usage_event else None,
            "usage_sequence_candidate_count": usage_event.get("sequence_candidate_count") if usage_event else None,
            "usage_sequence_id": usage_event.get("sequence_id") if usage_event else None,
            "usage_script_id": usage_event.get("script_id") if usage_event else None,
            "usage_decision_status": usage_event.get("decision_status") if usage_event else None,
            "usage_fallback_used": usage_event.get("fallback_used") if usage_event else None,
            "usage_delivery_status": usage_event.get("delivery_status") if usage_event else None,
        }
        decoded_run["business_summary"] = _business_summary_for_run(summary_source)
        return {
            "run": decoded_run,
            "node_traces": [decode_trace(dict(row)) for row in traces],
            "strategy_usage_event": usage_event,
        }

    def get_run_node_trace(self, *, request_id: str, node_id: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM node_traces WHERE request_id=? AND id=? LIMIT 1",
                (request_id, node_id),
            ).fetchone()
        return decode_trace(dict(row)) if row else {}

    def prune_runtime_history(self, *, trace_days: int, run_days: int) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        trace_before = (now - timedelta(days=max(1, trace_days))).isoformat()
        run_before = (now - timedelta(days=max(1, run_days))).isoformat()
        with self.store.connect() as conn:
            traces = conn.execute(
                "DELETE FROM node_traces WHERE created_at<?",
                (trace_before,),
            )
            runs = conn.execute(
                "DELETE FROM runs WHERE created_at<?",
                (run_before,),
            )
        return {
            "node_traces": int(traces.rowcount or 0),
            "runs": int(runs.rowcount or 0),
        }


def _compact_run_output(output_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep stable business observability outside generic trace truncation."""

    stored = compact(output_snapshot)
    if not isinstance(stored, dict):
        stored = {}
    # Generic compaction bounds dictionaries by insertion order.  HTTP timing
    # fields are appended after the business snapshot and would otherwise be
    # silently dropped once that snapshot grows beyond the generic key limit.
    for key in (
        "http_request_ingress_id",
        "http_request_started_at",
        "http_response_finished_at",
        "http_duration_ms",
        "http_response_body",
        "http_response_reply_messages",
        "runtime_processing_finished_at",
        "post_reply_finalization",
        "post_reply_payload",
        "v3_recovery_payload",
        "v3_response_snapshot",
        "performance",
    ):
        if key in output_snapshot:
            stored[key] = (
                output_snapshot[key]
                if key in {"post_reply_payload", "v3_recovery_payload", "v3_response_snapshot"}
                else compact(output_snapshot[key])
            )
    observability = output_snapshot.get("observability_v3")
    if isinstance(observability, dict) and observability:
        # The projection is already bounded and scrubbed by its builder. Do not
        # run it through generic trace compaction, which truncates lists to 8
        # entries and would hide the complete visible conversation.
        stored["observability_v3"] = observability
    return stored


def _response_snapshot_from_state(
    *,
    request_id: str,
    response_id: str,
    reply_messages: list[dict[str, Any]],
    final_state: dict[str, Any],
    response_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(response_snapshot, dict) and response_snapshot:
        snapshot = dict(response_snapshot)
    else:
        route = planner_public_route(final_state)
        response_meta = (
            final_state.get("response_meta")
            if isinstance(final_state.get("response_meta"), dict)
            else {}
        )
        snapshot = {
            "request_id": request_id,
            "response_id": response_id,
            "replayed": False,
            "reply_messages": reply_messages,
            "scene": str(route.get("scene") or ""),
            "intent": str(route.get("intent") or ""),
            "subflow": str(route.get("subflow") or ""),
            "trace_url": final_state.get("trace_url") or None,
            "meta": response_meta,
        }
    snapshot["request_id"] = request_id
    snapshot["response_id"] = response_id
    snapshot["replayed"] = False
    snapshot["reply_messages"] = reply_messages
    return snapshot


def _decode_post_reply_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if str(value.get("encoding") or "") != "zlib+base64+json":
        return value
    encoded = str(value.get("data") or "")
    if not encoded:
        return {}
    try:
        raw = zlib.decompress(base64.b64decode(encoded, validate=True)).decode("utf-8")
    except (ValueError, OSError, UnicodeDecodeError):
        return {}
    return loads_dict(raw)


def _compact_order_state_snapshot(final_state: dict[str, Any]) -> dict[str, Any]:
    customer_context = (
        final_state.get("customer_context")
        if isinstance(final_state.get("customer_context"), dict)
        else {}
    )
    basic_info = (
        customer_context.get("basic_info")
        if isinstance(customer_context.get("basic_info"), dict)
        else {}
    )
    tool_results = (
        final_state.get("tool_results")
        if isinstance(final_state.get("tool_results"), dict)
        else {}
    )
    order_context = (
        tool_results.get("customer_order_context")
        if isinstance(tool_results.get("customer_order_context"), dict)
        else {}
    )
    if isinstance(order_context.get("data"), dict):
        order_context = order_context["data"]
    return {
        "order_state": str(
            final_state.get("order_state")
            or basic_info.get("order_state")
            or order_context.get("order_state")
            or order_context.get("status_text")
            or order_context.get("status")
            or ""
        ).strip(),
        "deposit_state": str(
            final_state.get("deposit_state")
            or basic_info.get("deposit_state")
            or order_context.get("deposit_state")
            or ""
        ).strip(),
        "fee_paid": order_context.get("fee_paid", basic_info.get("fee_paid", "")),
    }


def _run_list_view(run: dict[str, Any]) -> dict[str, Any]:
    """Keep list responses small; full business detail is loaded per run."""

    run = dict(run)
    run["business_summary"] = _business_summary_for_run(run)
    output = run.get("output_snapshot")
    if isinstance(output, dict):
        output = dict(output)
        output.pop("observability_v3", None)
        output.pop("post_reply_payload", None)
        output.pop("v3_recovery_payload", None)
        run["output_snapshot"] = output
    for key in list(run):
        if key.startswith("usage_") or key == "contact_wechat":
            run.pop(key, None)
    return run


def _business_summary_for_run(run: dict[str, Any]) -> dict[str, Any]:
    output = run.get("output_snapshot") if isinstance(run.get("output_snapshot"), dict) else {}
    observability = output.get("observability_v3") if isinstance(output.get("observability_v3"), dict) else {}
    checkpoint = observability.get("checkpoint_decision") if isinstance(observability.get("checkpoint_decision"), dict) else {}
    primary_checkpoint = checkpoint.get("primary") if isinstance(checkpoint.get("primary"), dict) else {}
    knowledge = observability.get("knowledge_match") if isinstance(observability.get("knowledge_match"), dict) else {}
    adopted = knowledge.get("adopted") if isinstance(knowledge.get("adopted"), dict) else {}
    intent = output.get("realtime_intent") if isinstance(output.get("realtime_intent"), dict) else {}
    emotion = output.get("emotion_decision") if isinstance(output.get("emotion_decision"), dict) else {}
    input_snapshot = run.get("input_snapshot") if isinstance(run.get("input_snapshot"), dict) else {}
    usage_present = any(run.get(key) is not None for key in ("usage_intent_code", "usage_decision_status"))
    sequence_candidates = knowledge.get("matched_sequences") if isinstance(knowledge.get("matched_sequences"), list) else []
    reply_source = str(output.get("reply_source") or "")
    if reply_source == "platform_superseded":
        response_kind = "superseded"
    elif reply_source in {
        "ignored_platform_auto_message",
        "platform_recalled_message",
    } or reply_source.startswith("platform_protocol"):
        response_kind = "protocol_filtered"
    elif reply_source == "human_takeover_guard":
        response_kind = "human_takeover"
    elif "fallback" in reply_source or output.get("fallback_source"):
        response_kind = "failure_fallback"
    else:
        response_kind = "business_reply"
    return {
        "wechat": str(run.get("contact_wechat") or input_snapshot.get("wechat") or ""),
        "intent_code": str(run.get("usage_intent_code") or intent.get("type") or ""),
        "emotion_code": str(run.get("usage_emotion_code") or emotion.get("label") or ""),
        "checkpoint_code": str(run.get("usage_checkpoint_code") or primary_checkpoint.get("code") or ""),
        "checkpoint_name": str(run.get("usage_checkpoint_name") or primary_checkpoint.get("name") or ""),
        "sequence_matched": (
            bool(int(run.get("usage_sequence_candidate_count") or 0))
            if usage_present
            else bool(sequence_candidates)
        ),
        "sequence_adopted": bool(run.get("usage_sequence_id") or adopted.get("sequence_id")),
        "script_adopted": bool(run.get("usage_script_id") or adopted.get("script_ids")),
        "decision_status": str(run.get("usage_decision_status") or output.get("decision_status") or ""),
        "fallback_used": bool(
            run.get("usage_fallback_used")
            or (
                observability.get("overview", {}).get("fallback_used")
                if isinstance(observability.get("overview"), dict)
                else False
            )
        ),
        "delivery_status": str(run.get("usage_delivery_status") or ""),
        "usage_event_recorded": usage_present,
        "response_kind": response_kind,
        "post_reply_finalization": output.get("post_reply_finalization", {}),
    }


def _decode_usage_event(row: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "intent_secondary_json",
        "decision_reasons_json",
        "closing_constraint_reasons_json",
    ):
        row[key.removesuffix("_json")] = loads_list(row.pop(key, None))
    row["adopted"] = bool(row.get("adopted"))
    row["fallback_used"] = bool(row.get("fallback_used"))
    return row


def _dict_value(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    return item if isinstance(item, dict) else {}


def _reply_messages_from_http_response(response_body: dict[str, Any]) -> list[Any]:
    data = response_body.get("data") if isinstance(response_body.get("data"), dict) else {}
    messages = data.get("reply_messages") if isinstance(data.get("reply_messages"), list) else None
    if messages is not None:
        return messages
    messages = response_body.get("reply_messages") if isinstance(response_body.get("reply_messages"), list) else []
    return messages


def _elapsed_ms(started_at: str, finished_at: str) -> int:
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        return max(0, int((finished - started).total_seconds() * 1000))
    except (TypeError, ValueError):
        return 0
