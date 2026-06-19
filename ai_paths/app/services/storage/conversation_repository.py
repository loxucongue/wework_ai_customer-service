from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.services.storage.serialization import dumps, loads_dict, loads_list, utc_now_iso


class ConversationRepositoryMixin:
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
