from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.services.storage.sqlite_store import SQLiteStore
from app.services.trace_logger import compact


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def loads_dict(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def loads_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


class AppRepository:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def upsert_conversation(self, *, conversation_id: str, request: Any, title: str) -> None:
        now = utc_now_iso()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id, customer_id, external_userid, corp_id, user_id, wechat, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    customer_id=excluded.customer_id,
                    external_userid=excluded.external_userid,
                    corp_id=excluded.corp_id,
                    user_id=excluded.user_id,
                    wechat=excluded.wechat,
                    title=CASE WHEN conversations.title='' THEN excluded.title ELSE conversations.title END,
                    updated_at=excluded.updated_at
                """,
                (
                    conversation_id,
                    str(getattr(request, "customer_id", "") or ""),
                    str(getattr(request, "external_userid", "") or ""),
                    str(getattr(request, "corp_id", "") or ""),
                    str(getattr(request, "user_id", "") or ""),
                    str(getattr(request, "wechat", "") or ""),
                    title,
                    now,
                    now,
                ),
            )

    def add_user_message(self, *, conversation_id: str, request_id: str, content: str, file_image: str | None) -> None:
        self._add_message(
            conversation_id=conversation_id,
            request_id=request_id,
            role="user",
            content=content,
            file_image=file_image or "",
            reply_messages=[],
        )

    def add_assistant_message(self, *, conversation_id: str, request_id: str, reply_messages: list[dict[str, Any]]) -> None:
        content = "\n".join(str(item.get("content", "")) for item in reply_messages if isinstance(item, dict))
        self._add_message(
            conversation_id=conversation_id,
            request_id=request_id,
            role="assistant",
            content=content,
            file_image="",
            reply_messages=reply_messages,
        )

    def _add_message(
        self,
        *,
        conversation_id: str,
        request_id: str,
        role: str,
        content: str,
        file_image: str,
        reply_messages: list[dict[str, Any]],
    ) -> None:
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (id, conversation_id, request_id, role, content, file_image, reply_messages, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), conversation_id, request_id, role, content, file_image, dumps(reply_messages), utc_now_iso()),
            )

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
                    dumps(_tags_from_state(final_state)),
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

    def load_memory(self, customer_id: str) -> dict[str, Any] | None:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT customer_id, portrait, basic_info, lifecycle_stage, updated_at FROM customer_memory WHERE customer_id=?",
                (customer_id,),
            ).fetchone()
            if not row:
                return None
            events = conn.execute(
                """
                SELECT id, event_type, stage, summary, facts, impact, confidence, created_at
                FROM history_events
                WHERE customer_id=?
                ORDER BY created_at ASC
                LIMIT 100
                """,
                (customer_id,),
            ).fetchall()
        return {
            "customer_id": row["customer_id"],
            "portrait": loads_dict(row["portrait"]),
            "basic_info": loads_dict(row["basic_info"]),
            "lifecycle_stage": row["lifecycle_stage"] or "",
            "updated_at": row["updated_at"],
            "history_events": [
                {
                    "event_id": item["id"],
                    "event_type": item["event_type"],
                    "stage": item["stage"],
                    "summary": item["summary"],
                    "facts": loads_dict(item["facts"]),
                    "impact": item["impact"],
                    "confidence": item["confidence"],
                    "event_time": item["created_at"],
                }
                for item in events
            ],
        }

    def clear_memory(self, customer_id: str) -> None:
        with self.store.connect() as conn:
            conn.execute("DELETE FROM history_events WHERE customer_id=?", (customer_id,))
            conn.execute("DELETE FROM customer_memory WHERE customer_id=?", (customer_id,))

    def save_memory(self, customer_id: str, memory: dict[str, Any]) -> None:
        now = utc_now_iso()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO customer_memory (customer_id, portrait, basic_info, lifecycle_stage, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(customer_id) DO UPDATE SET
                    portrait=excluded.portrait,
                    basic_info=excluded.basic_info,
                    lifecycle_stage=excluded.lifecycle_stage,
                    updated_at=excluded.updated_at
                """,
                (
                    customer_id,
                    dumps(memory.get("portrait") or {}),
                    dumps(memory.get("basic_info") or {}),
                    str(memory.get("lifecycle_stage") or ""),
                    now,
                ),
            )
            for event in memory.get("history_events") or []:
                if not isinstance(event, dict):
                    continue
                event_id = str(event.get("event_id") or event.get("id") or uuid4())
                conn.execute(
                    """
                    INSERT OR IGNORE INTO history_events
                        (id, customer_id, event_type, stage, summary, facts, impact, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        customer_id,
                        str(event.get("event_type") or ""),
                        str(event.get("stage") or ""),
                        str(event.get("summary") or ""),
                        dumps(event.get("facts") or {}),
                        str(event.get("impact") or ""),
                        float(event.get("confidence") or 0),
                        str(event.get("event_time") or event.get("created_at") or now),
                    ),
                )

    def list_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.*,
                       (SELECT content FROM messages m WHERE m.conversation_id=c.id ORDER BY created_at DESC LIMIT 1) AS last_message
                FROM conversations c
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            conversation = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            messages = conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC",
                (conversation_id,),
            ).fetchall()
            runs = conn.execute(
                "SELECT request_id, intents, tags, duration_ms, token_usage, error, created_at FROM runs WHERE conversation_id=? ORDER BY created_at ASC",
                (conversation_id,),
            ).fetchall()
        return {
            "conversation": dict(conversation) if conversation else {},
            "messages": [{**dict(row), "reply_messages": loads_list(row["reply_messages"])} for row in messages],
            "runs": [
                {
                    **dict(row),
                    "intents": loads_list(row["intents"]),
                    "tags": loads_list(row["tags"]),
                    "token_usage": loads_dict(row["token_usage"]),
                }
                for row in runs
            ],
        }

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
        return [_decode_run(dict(row)) for row in rows]

    def get_run(self, request_id: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            run = conn.execute("SELECT * FROM runs WHERE request_id=?", (request_id,)).fetchone()
            traces = conn.execute(
                "SELECT * FROM node_traces WHERE request_id=? ORDER BY created_at ASC",
                (request_id,),
            ).fetchall()
        return {
            "run": _decode_run(dict(run)) if run else {},
            "node_traces": [_decode_trace(dict(row)) for row in traces],
        }


def _decode_run(row: dict[str, Any]) -> dict[str, Any]:
    for key in ["input_snapshot", "output_snapshot"]:
        row[key] = loads_dict(row.get(key))
    for key in ["intents", "tags"]:
        row[key] = loads_list(row.get(key))
    row["token_usage"] = loads_dict(row.get("token_usage"))
    return row


def _decode_trace(row: dict[str, Any]) -> dict[str, Any]:
    for key in ["input_snapshot", "output_snapshot"]:
        row[key] = loads_dict(row.get(key))
    row["tool_calls"] = loads_list(row.get("tool_calls"))
    return row


def _tags_from_state(state: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for item in state.get("intents") or []:
        if isinstance(item, dict) and item.get("intent"):
            tags.append(str(item["intent"]))
    route = state.get("route_result") or {}
    if route.get("subflow"):
        tags.append(str(route["subflow"]))
    if state.get("image_info", {}).get("has_image"):
        tags.append("has_image")
    return list(dict.fromkeys(tags))
