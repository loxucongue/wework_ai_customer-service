from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from app.services.storage.serialization import dumps, loads_dict, loads_list, utc_now_iso
from app.services.trace_logger import compact
from app.services.v3_reply_recovery import chat_request_recovery_payload, v3_response_id


class ConversationRepositoryMixin:
    def _prepare_v3_request_in_connection(
        self,
        conn: Any,
        *,
        default_conversation_id: str,
        resolve_existing: bool,
        request: Any,
        request_id: str,
        title: str,
        input_snapshot: dict[str, Any],
        interface_version: str,
        started_at: str,
        http_request_ingress_id: str,
        generation_key: str = "",
        response_id: str = "",
        generation_status: str = "generating",
        recovery_kind: str = "",
        recovery_next_at: str = "",
        recovery_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write V3 ingress facts using a caller-owned transaction."""

        now = str(started_at or utc_now_iso())
        version = str(interface_version or "v3").strip().lower()
        if version not in {"v1", "v2", "v3"}:
            version = "v3"
        corp_id = str(getattr(request, "corp_id", "") or "")
        wechat = str(getattr(request, "wechat", "") or "")
        external_userid = str(getattr(request, "external_userid", "") or "")
        customer_id = str(
            getattr(request, "platform_customer_id", "")
            or getattr(request, "customer_id", "")
            or ""
        )
        clean_generation_key = str(generation_key or "").strip() or None
        clean_response_id = str(response_id or "").strip() or (
            v3_response_id(clean_generation_key) if clean_generation_key else None
        )
        if clean_generation_key:
            existing_generation = conn.execute(
                """
                SELECT request_id, conversation_id, response_id, generation_status
                FROM runs WHERE generation_key=? LIMIT 1
                """,
                (clean_generation_key,),
            ).fetchone()
            if existing_generation is not None:
                same_request = str(existing_generation["request_id"] or "") == str(request_id or "")
                return {
                    "conversation_id": str(existing_generation["conversation_id"] or ""),
                    "request_id": str(existing_generation["request_id"] or ""),
                    "generation_key": clean_generation_key,
                    "response_id": str(existing_generation["response_id"] or ""),
                    "generation_status": str(existing_generation["generation_status"] or ""),
                    "replayed": not same_request,
                    "continue_existing": same_request,
                }
        conversation_id = str(default_conversation_id or "")
        if resolve_existing and corp_id and wechat and (external_userid or customer_id):
            identity_clause = "external_userid=?" if external_userid else "customer_id=?"
            identity_value = external_userid or customer_id
            row = conn.execute(
                f"""
                SELECT id FROM conversations
                WHERE corp_id=? AND LOWER(wechat)=LOWER(?) AND {identity_clause}
                ORDER BY updated_at DESC LIMIT 1
                """,
                (corp_id, wechat, identity_value),
            ).fetchone()
            if row and str(row["id"] or ""):
                conversation_id = str(row["id"])

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
                external_userid,
                corp_id,
                str(getattr(request, "user_id", "") or ""),
                wechat,
                title,
                now,
                now,
            ),
        )
        output_snapshot = {
            "runtime_status": "running",
            "runtime_phase": "request_received",
            "runtime_started_at": now,
            "runtime_updated_at": now,
            "interface_version": version,
            "http_request_ingress_id": str(http_request_ingress_id or ""),
            "http_request_started_at": now,
        }
        durable_recovery_payload = (
            recovery_payload
            if isinstance(recovery_payload, dict) and recovery_payload
            else (chat_request_recovery_payload(request) if clean_generation_key else {})
        )
        if durable_recovery_payload:
            output_snapshot["v3_recovery_payload"] = durable_recovery_payload
        insert_run = conn.execute(
            """
            INSERT OR IGNORE INTO runs
                (request_id, conversation_id, customer_id, generation_key, response_id,
                 generation_status, recovery_kind, recovery_attempts, recovery_next_at,
                 recovery_dispatch_id, recovery_error, input_snapshot, output_snapshot, intents, tags,
                 duration_ms, token_usage, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, '', '', ?, ?, '[]', '[]', 0, '{}', '', ?)
            """,
            (
                request_id,
                conversation_id,
                str(getattr(request, "customer_id", "") or ""),
                clean_generation_key,
                clean_response_id,
                str(generation_status or "generating") if clean_generation_key else "",
                str(recovery_kind or ""),
                str(recovery_next_at or ""),
                dumps(compact(input_snapshot)),
                dumps(output_snapshot),
                now,
            ),
        )
        if clean_generation_key and int(insert_run.rowcount or 0) == 0:
            suffix = " FOR UPDATE" if getattr(self.store, "dialect", "") == "mysql" else ""
            existing_generation = conn.execute(
                """
                SELECT request_id, conversation_id, response_id, generation_status
                FROM runs WHERE generation_key=? LIMIT 1
                """ + suffix,
                (clean_generation_key,),
            ).fetchone()
            if existing_generation is None:
                raise RuntimeError("V3 generation reservation conflicted but could not be reloaded")
            same_request = str(existing_generation["request_id"] or "") == str(request_id or "")
            return {
                "conversation_id": str(existing_generation["conversation_id"] or ""),
                "request_id": str(existing_generation["request_id"] or ""),
                "generation_key": clean_generation_key,
                "response_id": str(existing_generation["response_id"] or ""),
                "generation_status": str(existing_generation["generation_status"] or ""),
                "replayed": not same_request,
                "continue_existing": same_request,
            }
        conn.execute(
            """
            INSERT OR IGNORE INTO messages (id, conversation_id, request_id, role, content, file_image, reply_messages, created_at)
            VALUES (?, ?, ?, 'user', ?, ?, '[]', ?)
            """,
            (
                str(uuid4()),
                conversation_id,
                request_id,
                str(getattr(request, "content", "") or ""),
                str(getattr(request, "file_image", "") or ""),
                now,
            ),
        )
        return {
            "conversation_id": conversation_id,
            "request_id": request_id,
            "generation_key": clean_generation_key or "",
            "response_id": clean_response_id or "",
            "generation_status": str(generation_status or "generating") if clean_generation_key else "",
            "replayed": False,
            "continue_existing": False,
        }

    def prepare_v3_request(
        self,
        *,
        default_conversation_id: str,
        resolve_existing: bool,
        request: Any,
        request_id: str,
        title: str,
        input_snapshot: dict[str, Any],
        interface_version: str,
        started_at: str,
        http_request_ingress_id: str,
        cancel_outreach: bool = False,
        generation_key: str = "",
        response_id: str = "",
        generation_status: str = "generating",
        recovery_kind: str = "",
        recovery_next_at: str = "",
        recovery_payload: dict[str, Any] | None = None,
        include_previous_strategy_state: bool = False,
        sales_contact_key: str = "",
    ) -> dict[str, Any]:
        """Persist the V3 ingress facts in one database transaction.

        Historically the reply path checked the conversation, upserted it,
        inserted the customer message and created the run through four
        independent connections.  Remote MySQL latency made those round trips
        visible to the customer.  This method keeps the exact same durable
        facts while sharing one checkout and one commit.
        """

        operation_started = time.perf_counter()
        statement_count = 0
        with self.store.connect() as raw_conn:
            class _CountedConnection:
                def execute(self, *args: Any, **kwargs: Any) -> Any:
                    nonlocal statement_count
                    statement_count += 1
                    return raw_conn.execute(*args, **kwargs)

                def __getattr__(self, name: str) -> Any:
                    return getattr(raw_conn, name)

            conn = _CountedConnection()
            prepared = self._prepare_v3_request_in_connection(
                conn,
                default_conversation_id=default_conversation_id,
                resolve_existing=resolve_existing,
                request=request,
                request_id=request_id,
                title=title,
                input_snapshot=input_snapshot,
                interface_version=interface_version,
                started_at=started_at,
                http_request_ingress_id=http_request_ingress_id,
                generation_key=generation_key,
                response_id=response_id,
                generation_status=generation_status,
                recovery_kind=recovery_kind,
                recovery_next_at=recovery_next_at,
                recovery_payload=recovery_payload,
            )
            cancellation = {"cancelled_plans": 0, "skipped_tasks": 0}
            if (
                not bool(prepared.get("replayed"))
                and not bool(prepared.get("continue_existing"))
                and cancel_outreach
                and str(getattr(request, "wechat", "") or "").strip()
            ):
                cancellation = self._cancel_outreach_for_customer_reply_in_connection(
                    conn,
                    customer_id=str(getattr(request, "customer_id", "") or ""),
                    corp_id=str(getattr(request, "corp_id", "") or ""),
                    wechat=str(getattr(request, "wechat", "") or ""),
                    external_userid=str(getattr(request, "external_userid", "") or ""),
                    request_id=request_id,
                )
            previous_strategy_state: dict[str, Any] = {}
            load_previous = getattr(self, "_latest_v3_strategy_state_in_connection", None)
            if (
                include_previous_strategy_state
                and not bool(prepared.get("replayed"))
                and not bool(prepared.get("continue_existing"))
                and callable(load_previous)
            ):
                previous_strategy_state = load_previous(
                    conn,
                    sales_contact_key=sales_contact_key,
                    exclude_request_id=request_id,
                    corp_id=str(getattr(request, "corp_id", "") or ""),
                    wechat=str(getattr(request, "wechat", "") or ""),
                    external_userid=str(getattr(request, "external_userid", "") or ""),
                    customer_id=str(getattr(request, "customer_id", "") or ""),
                )
        return {
            **prepared,
            "duration_ms": max(0, int((time.perf_counter() - operation_started) * 1000)),
            "connection_count": 1,
            "statement_count": statement_count,
            "outreach_cancel": cancellation,
            "previous_strategy_state": previous_strategy_state,
        }

    def find_conversation_id_for_identity(
        self,
        *,
        corp_id: str,
        wechat: str,
        external_userid: str,
        customer_id: str,
    ) -> str:
        if not str(corp_id or "").strip() or not str(wechat or "").strip():
            return ""
        clauses = ["corp_id=?", "LOWER(wechat)=LOWER(?)"]
        params: list[Any] = [corp_id, wechat]
        if external_userid:
            clauses.append("external_userid=?")
            params.append(external_userid)
        elif customer_id:
            clauses.append("customer_id=?")
            params.append(customer_id)
        else:
            return ""
        with self.store.connect() as conn:
            row = conn.execute(
                f"SELECT id FROM conversations WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT 1",
                tuple(params),
            ).fetchone()
        return str(row["id"] or "") if row else ""

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
        observe_identity = getattr(self, "observe_customer_identity", None)
        if callable(observe_identity):
            observe_identity(
                corp_id=str(getattr(request, "corp_id", "") or ""),
                wechat=str(getattr(request, "wechat", "") or ""),
                external_userid=str(getattr(request, "external_userid", "") or ""),
                customer_id=str(getattr(request, "platform_customer_id", "") or getattr(request, "customer_id", "") or ""),
                user_id=str(getattr(request, "user_id", "") or ""),
                customer_add_wechat_id=str(getattr(request, "customer_add_wechat_id", "") or ""),
                source="v3_request",
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

    def list_recent_messages_for_sales_contact(
        self,
        *,
        customer_id: str,
        external_userid: str,
        corp_id: str = "",
        wechat: str = "",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        if not str(wechat or "").strip():
            return []
        clauses = ["LOWER(c.wechat)=LOWER(?)"]
        params: list[Any] = [wechat]
        if corp_id:
            clauses.append("c.corp_id=?")
            params.append(corp_id)
        if external_userid:
            clauses.append("c.external_userid=?")
            params.append(external_userid)
        elif customer_id:
            clauses.append("c.customer_id=?")
            params.append(customer_id)
        else:
            return []
        capped_limit = max(1, min(int(limit or 30), 50))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT m.role, m.content, m.reply_messages, m.created_at
                FROM messages m
                JOIN conversations c ON c.id=m.conversation_id
                WHERE {' AND '.join(clauses)}
                ORDER BY m.created_at DESC
                LIMIT ?
                """,
                (*params, capped_limit),
            ).fetchall()
        output = [{**dict(row), "reply_messages": loads_list(row["reply_messages"])} for row in rows]
        output.reverse()
        return output

    def clear_customer_conversations(self, customer_id: str) -> int:
        customer = str(customer_id or "").strip()
        if not customer:
            return 0
        with self.store.connect() as conn:
            rows = conn.execute("SELECT id FROM conversations WHERE customer_id=?", (customer,)).fetchall()
            conversation_ids = [str(row["id"] or "") for row in rows if str(row["id"] or "")]
            if not conversation_ids:
                return 0
            conn.executemany("DELETE FROM conversations WHERE id=?", [(conversation_id,) for conversation_id in conversation_ids])
        return len(conversation_ids)
