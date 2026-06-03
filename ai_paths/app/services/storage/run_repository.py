from __future__ import annotations

from typing import Any

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
            "file_image": bool(final_state.get("file_image")),
        }
        output_snapshot = {
            "reply_messages": final_state.get("reply_messages", []),
            "route_result": final_state.get("route_result", {}),
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
                    dumps(final_state.get("intents") or []),
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
