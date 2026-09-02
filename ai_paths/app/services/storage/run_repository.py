from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.graph.planner.runtime_plan import planner_task_views
from app.graph.planner.runtime_plan import planner_public_route
from app.services.storage.serialization import (
    decode_run,
    decode_trace,
    dumps,
    loads_dict,
    tags_from_state,
    utc_now_iso,
)
from app.services.trace_logger import compact
from app.services.run_observability import (
    build_v3_run_observability,
    enrich_v3_run_observability,
)


class RunRepositoryMixin:
    def start_run(
        self,
        *,
        request_id: str,
        conversation_id: str,
        customer_id: str,
        input_snapshot: dict[str, Any],
        interface_version: str = "v1",
    ) -> None:
        """Persist the request before model execution so it is visible live."""

        started_at = utc_now_iso()
        version = str(interface_version or "v1").strip().lower()
        if version not in {"v1", "v2", "v3"}:
            version = "v1"
        output_snapshot = {
            "runtime_status": "running",
            "runtime_phase": "request_received",
            "runtime_started_at": started_at,
            "runtime_updated_at": started_at,
            "interface_version": version,
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
            "postprocess_changed": bool(final_state.get("postprocess_changed")),
            "postprocess_reasons": final_state.get("postprocess_reasons", []),
            "warnings": final_state.get("warnings", []),
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
                "SELECT output_snapshot, created_at FROM runs WHERE request_id=?",
                (request_id,),
            ).fetchone()
            existing_output: dict[str, Any] = {}
            started_at = ""
            if existing:
                existing_output = loads_dict(existing["output_snapshot"])
                for key in ("http_response_body", "http_response_reply_messages"):
                    if key in existing_output and key not in output_snapshot:
                        output_snapshot[key] = existing_output[key]
                started_at = str(existing_output.get("runtime_started_at") or existing["created_at"] or "")
            finished_at = utc_now_iso()
            duration_ms = _elapsed_ms(started_at, finished_at) if started_at else trace_duration_ms
            output_snapshot = {
                "runtime_status": "completed_with_errors" if errors else "completed",
                "runtime_phase": "completed",
                "runtime_started_at": started_at or finished_at,
                "runtime_updated_at": finished_at,
                "runtime_finished_at": finished_at,
                **output_snapshot,
                "interface_version": str(
                    existing_output.get("interface_version") or interface_version
                ),
            }
            conn.execute(
                """
                INSERT OR REPLACE INTO runs
                    (request_id, conversation_id, customer_id, input_snapshot, output_snapshot, intents, tags,
                     duration_ms, token_usage, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    conversation_id,
                    str(final_state.get("customer_id") or ""),
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
            for index, entry in enumerate(trace):
                if not isinstance(entry, dict):
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO node_traces
                        (id, request_id, node_name, input_snapshot, output_snapshot, tool_calls, duration_ms, error, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{request_id}_{index}",
                        request_id,
                        str(entry.get("node") or ""),
                        dumps(entry.get("input_snapshot") or {}),
                        dumps(entry.get("output_snapshot") or {}),
                        dumps(entry.get("tool_calls") or []),
                        int(entry.get("duration_ms") or 0),
                        str(entry.get("error") or ""),
                        str(entry.get("started_at") or utc_now_iso()),
                    ),
                )

    def update_run_http_response(self, *, request_id: str, response_body: dict[str, Any]) -> None:
        with self.store.connect() as conn:
            row = conn.execute("SELECT output_snapshot FROM runs WHERE request_id=?", (request_id,)).fetchone()
            if not row:
                return
            output_snapshot = loads_dict(row["output_snapshot"])
            output_snapshot["http_response_body"] = response_body
            output_snapshot["http_response_reply_messages"] = _reply_messages_from_http_response(response_body)
            conn.execute(
                "UPDATE runs SET output_snapshot=? WHERE request_id=?",
                (dumps(_compact_run_output(output_snapshot)), request_id),
            )

    def list_runs(
        self,
        *,
        limit: int = 50,
        customer_id: str = "",
        conversation_id: str = "",
        has_error: bool | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if customer_id:
            clauses.append("customer_id=?")
            params.append(customer_id)
        if conversation_id:
            clauses.append("conversation_id=?")
            params.append(conversation_id)
        if has_error is True:
            clauses.append("error<>''")
        elif has_error is False:
            clauses.append("error=''")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 200)))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT request_id, conversation_id, customer_id, input_snapshot, output_snapshot,
                       intents, tags, duration_ms, token_usage, error, created_at
                FROM runs
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_run_list_view(decode_run(dict(row))) for row in rows]

    def get_run(self, request_id: str, *, include_debug: bool = True) -> dict[str, Any]:
        dispatch_id = ""
        with self.store.connect() as conn:
            run = conn.execute("SELECT * FROM runs WHERE request_id=?", (request_id,)).fetchone()
            traces = (
                conn.execute(
                    "SELECT * FROM node_traces WHERE request_id=? ORDER BY created_at ASC",
                    (request_id,),
                ).fetchall()
                if include_debug
                else []
            )
            decoded_run = decode_run(dict(run)) if run else {}
            output_snapshot = (
                decoded_run.get("output_snapshot")
                if isinstance(decoded_run.get("output_snapshot"), dict)
                else {}
            )
            callback = (
                output_snapshot.get("strategy_data_callback")
                if isinstance(output_snapshot.get("strategy_data_callback"), dict)
                else {}
            )
            outbox_id = str(callback.get("outbox_id") or "").strip()
            if outbox_id:
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
        dispatch = self.get_message_dispatch(dispatch_id) if dispatch_id else {}
        enrich_v3_run_observability(output_snapshot, dispatch=dispatch)
        return {
            "run": decoded_run,
            "node_traces": [decode_trace(dict(row)) for row in traces],
        }

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
    observability = output_snapshot.get("observability_v3")
    if isinstance(observability, dict) and observability:
        # The projection is already bounded and scrubbed by its builder. Do not
        # run it through generic trace compaction, which truncates lists to 8
        # entries and would hide the complete visible conversation.
        stored["observability_v3"] = observability
    return stored


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

    output = run.get("output_snapshot")
    if not isinstance(output, dict) or "observability_v3" not in output:
        return run
    run = dict(run)
    output = dict(output)
    output.pop("observability_v3", None)
    run["output_snapshot"] = output
    return run


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
