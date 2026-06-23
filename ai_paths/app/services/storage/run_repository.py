from __future__ import annotations

from typing import Any

from app.graph.planner.runtime_plan import planner_task_views
from app.graph.planner.runtime_plan import planner_public_route
from app.services.storage.serialization import (
    decode_run,
    decode_trace,
    dumps,
    tags_from_state,
    utc_now_iso,
)
from app.services.trace_logger import compact


class RunRepositoryMixin:
    def save_run(self, *, conversation_id: str, final_state: dict[str, Any], token_usage: dict[str, Any]) -> None:
        request_id = str(final_state.get("request_id") or "")
        trace = final_state.get("trace") or []
        duration_ms = sum(int(item.get("duration_ms") or 0) for item in trace if isinstance(item, dict))
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
        output_snapshot = {
            "reply_messages": final_state.get("reply_messages", []),
            "planner_route": planner_public_route(final_state),
            "planner_source": final_state.get("planner_source", ""),
            "policy_id": final_state.get("policy_id", ""),
            "policy_family_id": final_state.get("policy_family_id", ""),
            "exact_policy_id": final_state.get("exact_policy_id", ""),
            "policy_match_level": final_state.get("policy_match_level", ""),
            "policy_version": final_state.get("policy_version", ""),
            "reply_source": final_state.get("reply_source", ""),
            "postprocess_changed": bool(final_state.get("postprocess_changed")),
            "postprocess_reasons": final_state.get("postprocess_reasons", []),
            "warnings": final_state.get("warnings", []),
            "primary_task": final_state.get("primary_task", {}),
            "secondary_tasks": final_state.get("secondary_tasks", []),
            "handoff": final_state.get("handoff", {}),
            "profile_update": final_state.get("profile_update", {}),
            "event_updates": final_state.get("event_updates", []),
        }
        with self.store.connect() as conn:
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
                    dumps(compact(output_snapshot)),
                    dumps(planner_task_views(final_state)),
                    dumps(tags_from_state(final_state)),
                    duration_ms,
                    dumps(token_usage),
                    error,
                    utc_now_iso(),
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
        return [decode_run(dict(row)) for row in rows]

    def get_run(self, request_id: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            run = conn.execute("SELECT * FROM runs WHERE request_id=?", (request_id,)).fetchone()
            traces = conn.execute(
                "SELECT * FROM node_traces WHERE request_id=? ORDER BY created_at ASC",
                (request_id,),
            ).fetchall()
        return {
            "run": decode_run(dict(run)) if run else {},
            "node_traces": [decode_trace(dict(row)) for row in traces],
        }
