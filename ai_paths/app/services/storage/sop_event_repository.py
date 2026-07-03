from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.services.storage.serialization import dumps, loads_dict, loads_list, utc_now_iso


class SopEventRepositoryMixin:
    def create_sop_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("event_id is required")
        now = utc_now_iso()
        log_id = str(uuid4())
        with self.store.connect() as conn:
            result = conn.execute(
                """
                INSERT OR IGNORE INTO sop_events
                    (id, event_id, event_type, source, request_reply, upstream_created_at,
                     raw_payload_json, status, received_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?)
                """,
                (
                    log_id,
                    event_id,
                    str(payload.get("event_type") or ""),
                    str(payload.get("source") or ""),
                    1 if payload.get("request_reply") else 0,
                    str(payload.get("created_at") or ""),
                    dumps(payload),
                    now,
                    now,
                ),
            )
            created = result.rowcount > 0
        event = self.get_sop_event(event_id)
        event["created"] = created
        return event

    def get_sop_event(self, event_id: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sop_events WHERE event_id=? OR id=?",
                (event_id, event_id),
            ).fetchone()
        return self._decode_sop_event(dict(row)) if row else {}

    def list_sop_events(
        self,
        *,
        limit: int = 50,
        event_type: str = "",
        status: str = "",
        customer_id: str = "",
        external_userid: str = "",
        has_error: str = "",
        include_chat_gate: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_chat_gate:
            clauses.append("e.event_type<>'chat_gate'")
        if event_type:
            clauses.append("e.event_type=?")
            params.append(event_type)
        if status:
            clauses.append("e.status=?")
            params.append(status)
        if customer_id:
            clauses.append(
                "EXISTS (SELECT 1 FROM sop_send_tasks t WHERE t.event_id=e.event_id AND t.customer_id=?)"
            )
            params.append(customer_id)
        if external_userid:
            clauses.append(
                "EXISTS (SELECT 1 FROM sop_send_tasks t WHERE t.event_id=e.event_id AND t.external_userid=?)"
            )
            params.append(external_userid)
        task_error_sql = _sop_task_error_sql("t")
        if has_error.lower() in {"true", "1", "yes"}:
            clauses.append(
                "(e.error<>'' OR e.status LIKE '%error%' OR e.status LIKE '%failed%' OR EXISTS "
                f"(SELECT 1 FROM sop_send_tasks t WHERE t.event_id=e.event_id AND {task_error_sql}))"
            )
        elif has_error.lower() in {"false", "0", "no"}:
            clauses.append(
                "(e.error='' AND e.status NOT LIKE '%error%' AND e.status NOT LIKE '%failed%' AND NOT EXISTS "
                f"(SELECT 1 FROM sop_send_tasks t WHERE t.event_id=e.event_id AND {task_error_sql}))"
            )
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        safe_limit = max(1, min(int(limit or 50), 200))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.*,
                       COUNT(t.id) AS task_count,
                       SUM(CASE WHEN t.status='sent' THEN 1 ELSE 0 END) AS sent_count,
                       SUM(CASE WHEN {task_error_sql} THEN 1 ELSE 0 END) AS failed_count,
                       SUM(CASE WHEN t.status LIKE 'skipped%' THEN 1 ELSE 0 END) AS skipped_count
                FROM sop_events e
                LEFT JOIN sop_send_tasks t ON t.event_id=e.event_id
                {where_sql}
                GROUP BY e.event_id
                ORDER BY e.received_at DESC
                LIMIT ?
                """,
                [*params, safe_limit],
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = self._decode_sop_event(dict(row))
            item["task_count"] = int(item.pop("task_count", 0) or 0)
            item["sent_count"] = int(item.pop("sent_count", 0) or 0)
            item["failed_count"] = int(item.pop("failed_count", 0) or 0)
            item["skipped_count"] = int(item.pop("skipped_count", 0) or 0)
            item["raw_payload_summary"] = _sop_payload_summary(item.get("raw_payload"))
            item.pop("raw_payload", None)
            items.append(item)
        return items

    def update_sop_event_status(self, event_id: str, *, status: str, error: str = "") -> dict[str, Any]:
        now = utc_now_iso()
        with self.store.connect() as conn:
            conn.execute(
                """
                UPDATE sop_events
                SET status=?, error=?, updated_at=?
                WHERE event_id=?
                """,
                (status, error, now, event_id),
            )
        return self.get_sop_event(event_id)

    def create_sop_send_task(
        self,
        *,
        event_id: str,
        idempotency_key: str,
        customer_id: str,
        external_userid: str,
        corp_id: str,
        user_id: str,
        wechat: str,
        sop_pack_id: str,
        sop_pack_name: str,
        sop_category: str = "",
        trigger_source: str = "",
        reply_messages: list[dict[str, Any]],
        status: str = "pending",
        error: str = "",
        send_once_key: str = "",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        task_id = str(uuid4())
        send_once_key = str(send_once_key or "").strip()
        with self.store.connect() as conn:
            result = conn.execute(
                """
                INSERT OR IGNORE INTO sop_send_tasks
                    (id, event_id, idempotency_key, send_once_key, customer_id, external_userid, corp_id,
                     user_id, wechat, sop_pack_id, sop_pack_name, sop_category, trigger_source, reply_messages_json,
                     status, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    event_id,
                    idempotency_key,
                    send_once_key,
                    customer_id,
                    external_userid,
                    corp_id,
                    user_id,
                    wechat,
                    sop_pack_id,
                    sop_pack_name,
                    sop_category,
                    trigger_source,
                    dumps(reply_messages),
                    status,
                    error,
                    now,
                    now,
                ),
            )
            created = result.rowcount > 0
            row = conn.execute(
                "SELECT * FROM sop_send_tasks WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            duplicate_of_task_id = ""
            if row is None and send_once_key:
                duplicate = conn.execute(
                    """
                    SELECT *
                    FROM sop_send_tasks
                    WHERE send_once_key=? AND status IN ('pending','sent')
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (send_once_key,),
                ).fetchone()
                if duplicate is not None:
                    duplicate_of_task_id = str(duplicate["id"] or "")
                    result = conn.execute(
                        """
                        INSERT OR IGNORE INTO sop_send_tasks
                            (id, event_id, idempotency_key, send_once_key, customer_id, external_userid, corp_id,
                             user_id, wechat, sop_pack_id, sop_pack_name, sop_category, trigger_source, reply_messages_json,
                             status, error, created_at, updated_at)
                        VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'skipped_send_once_duplicate', ?, ?, ?)
                        """,
                        (
                            task_id,
                            event_id,
                            idempotency_key,
                            customer_id,
                            external_userid,
                            corp_id,
                            user_id,
                            wechat,
                            sop_pack_id,
                            sop_pack_name,
                            sop_category,
                            trigger_source,
                            dumps(reply_messages),
                            f"duplicate_sop_pack_task:{duplicate_of_task_id}",
                            now,
                            now,
                        ),
                    )
                    created = result.rowcount > 0
                    row = conn.execute(
                        "SELECT * FROM sop_send_tasks WHERE idempotency_key=?",
                        (idempotency_key,),
                    ).fetchone()
        task = self._decode_sop_send_task(dict(row)) if row else {}
        task["created"] = created
        if duplicate_of_task_id:
            task["dedupe_reason"] = "send_once_key"
            task["duplicate_of_task_id"] = duplicate_of_task_id
        return task

    def update_sop_send_task(
        self,
        task_id: str,
        *,
        status: str,
        send_payload: dict[str, Any] | None = None,
        send_response: dict[str, Any] | None = None,
        error: str = "",
        sent_at: str = "",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self.store.connect() as conn:
            conn.execute(
                """
                UPDATE sop_send_tasks
                SET status=?, send_payload_json=?, send_response_json=?, error=?,
                    sent_at=COALESCE(NULLIF(?, ''), sent_at), updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    dumps(send_payload or {}),
                    dumps(send_response or {}),
                    error,
                    sent_at,
                    now,
                    task_id,
                ),
            )
            row = conn.execute("SELECT * FROM sop_send_tasks WHERE id=?", (task_id,)).fetchone()
        return self._decode_sop_send_task(dict(row)) if row else {}

    def list_sop_send_tasks_for_event(self, event_id: str) -> list[dict[str, Any]]:
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM sop_send_tasks
                WHERE event_id=?
                ORDER BY created_at ASC
                """,
                (event_id,),
            ).fetchall()
        return [self._decode_sop_send_task(dict(row)) for row in rows]

    def get_sop_event_detail(self, event_id: str) -> dict[str, Any]:
        event = self.get_sop_event(event_id)
        if not event:
            return {}
        event["raw_payload_summary"] = _sop_payload_summary(event.get("raw_payload"))
        canonical_event_id = str(event.get("event_id") or event_id)
        return {
            "event": event,
            "tasks": self.list_sop_send_tasks_for_event(canonical_event_id),
        }

    def find_sop_event_identity(
        self,
        *,
        customer_id: str = "",
        external_userid: str = "",
        wechat: str = "",
    ) -> dict[str, str]:
        external_key = str(external_userid or customer_id or "").strip()
        customer_key = str(customer_id or external_userid or "").strip()
        wechat_key = str(wechat or "").strip()
        with self.store.connect() as conn:
            if external_key or customer_key:
                row = conn.execute(
                    """
                    SELECT customer_id, external_userid, corp_id, user_id, wechat, source, updated_at FROM (
                        SELECT customer_id, external_userid, corp_id, user_id, wechat, 'conversations' AS source, updated_at
                        FROM conversations
                        WHERE corp_id<>'' AND user_id<>'' AND wechat<>''
                          AND (LOWER(external_userid)=LOWER(?) OR LOWER(customer_id)=LOWER(?))
                        UNION ALL
                        SELECT customer_id, external_userid, corp_id, user_id, wechat, 'sop_send_tasks' AS source, updated_at
                        FROM sop_send_tasks
                        WHERE corp_id<>'' AND user_id<>'' AND wechat<>''
                          AND (LOWER(external_userid)=LOWER(?) OR LOWER(customer_id)=LOWER(?))
                        UNION ALL
                        SELECT customer_id, external_userid, corp_id, user_id, wechat, 'outreach_plans' AS source, updated_at
                        FROM outreach_plans
                        WHERE corp_id<>'' AND user_id<>'' AND wechat<>''
                          AND (LOWER(external_userid)=LOWER(?) OR LOWER(customer_id)=LOWER(?))
                    )
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (external_key, customer_key, external_key, customer_key, external_key, customer_key),
                ).fetchone()
                if row:
                    return _identity_row(dict(row))
            if wechat_key:
                row = conn.execute(
                    """
                    SELECT customer_id, external_userid, corp_id, user_id, wechat, source, updated_at FROM (
                        SELECT customer_id, external_userid, corp_id, user_id, wechat, 'conversations' AS source, updated_at
                        FROM conversations
                        WHERE corp_id<>'' AND user_id<>'' AND wechat<>'' AND LOWER(wechat)=LOWER(?)
                        UNION ALL
                        SELECT customer_id, external_userid, corp_id, user_id, wechat, 'sop_send_tasks' AS source, updated_at
                        FROM sop_send_tasks
                        WHERE corp_id<>'' AND user_id<>'' AND wechat<>'' AND LOWER(wechat)=LOWER(?)
                        UNION ALL
                        SELECT customer_id, external_userid, corp_id, user_id, wechat, 'outreach_plans' AS source, updated_at
                        FROM outreach_plans
                        WHERE corp_id<>'' AND user_id<>'' AND wechat<>'' AND LOWER(wechat)=LOWER(?)
                    )
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (wechat_key, wechat_key, wechat_key),
                ).fetchone()
                if row:
                    return _identity_row(dict(row))
        return {}

    def has_sent_sop_pack_for_customer(self, *, customer_id: str, external_userid: str, sop_pack_id: str) -> bool:
        if not sop_pack_id:
            return False
        clauses = ["sop_pack_id=?", "status='sent'"]
        params: list[Any] = [sop_pack_id]
        if external_userid:
            clauses.append("external_userid=?")
            params.append(external_userid)
        elif customer_id:
            clauses.append("customer_id=?")
            params.append(customer_id)
        else:
            return False
        with self.store.connect() as conn:
            row = conn.execute(
                f"SELECT id FROM sop_send_tasks WHERE {' AND '.join(clauses)} LIMIT 1",
                params,
            ).fetchone()
        return row is not None

    def list_sent_sop_pack_ids_for_customer(
        self,
        *,
        customer_id: str,
        external_userid: str,
        sent_before: str = "",
    ) -> list[str]:
        clauses = ["status='sent'", "sop_pack_id<>''"]
        params: list[Any] = []
        if external_userid:
            clauses.append("external_userid=?")
            params.append(external_userid)
        elif customer_id:
            clauses.append("customer_id=?")
            params.append(customer_id)
        else:
            return []
        if sent_before:
            clauses.append("COALESCE(NULLIF(sent_at, ''), updated_at)<=?")
            params.append(sent_before)
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT sop_pack_id
                FROM sop_send_tasks
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall()
        return [str(row["sop_pack_id"]) for row in rows if str(row["sop_pack_id"] or "").strip()]

    def list_sent_sop_categories_for_customer(
        self,
        *,
        customer_id: str,
        external_userid: str,
        sent_before: str = "",
    ) -> list[str]:
        clauses = ["status='sent'", "sop_category<>''"]
        params: list[Any] = []
        if external_userid:
            clauses.append("external_userid=?")
            params.append(external_userid)
        elif customer_id:
            clauses.append("customer_id=?")
            params.append(customer_id)
        else:
            return []
        if sent_before:
            clauses.append("COALESCE(NULLIF(sent_at, ''), updated_at)<=?")
            params.append(sent_before)
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT sop_category
                FROM sop_send_tasks
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall()
        return [str(row["sop_category"]) for row in rows if str(row["sop_category"] or "").strip()]

    @staticmethod
    def _decode_sop_event(row: dict[str, Any]) -> dict[str, Any]:
        if not str(row.get("id") or "").strip():
            row["id"] = str(row.get("event_id") or "")
        row["request_reply"] = bool(row.get("request_reply"))
        row["raw_payload"] = loads_dict(row.get("raw_payload_json"))
        row.pop("raw_payload_json", None)
        return row

    @staticmethod
    def _decode_sop_send_task(row: dict[str, Any]) -> dict[str, Any]:
        row["reply_messages"] = loads_list(row.get("reply_messages_json"))
        row["send_payload"] = loads_dict(row.get("send_payload_json"))
        row["send_response"] = loads_dict(row.get("send_response_json"))
        row.pop("reply_messages_json", None)
        row.pop("send_payload_json", None)
        row.pop("send_response_json", None)
        return row


def _sop_payload_summary(payload: Any) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    sop = data.get("sop") if isinstance(data.get("sop"), dict) else {}
    customers = data.get("customers") if isinstance(data.get("customers"), list) else []
    first_customer = customers[0] if customers and isinstance(customers[0], dict) else {}
    conversation = first_customer.get("conversation") if isinstance(first_customer.get("conversation"), dict) else {}
    customer = first_customer.get("customer") if isinstance(first_customer.get("customer"), dict) else {}
    return {
        "event_type": str(data.get("event_type") or ""),
        "event_id": str(data.get("event_id") or ""),
        "delay_minutes": sop.get("delay_minutes") or sop.get("scheduled_delay_minutes") or "",
        "day_stage": str(sop.get("day_stage") or ""),
        "customer_state": str(sop.get("customer_state") or ""),
        "stage_tag": str(sop.get("stage_tag") or ""),
        "platform_task_id": str(sop.get("platform_task_id") or ""),
        "customer_count": len(customers),
        "first_external_userid": str(customer.get("external_userid") or conversation.get("external_userid") or ""),
        "first_wechat": str(conversation.get("wework_user_id") or ""),
    }


def _identity_row(row: dict[str, Any]) -> dict[str, str]:
    return {
        "customer_id": str(row.get("customer_id") or "").strip(),
        "external_userid": str(row.get("external_userid") or "").strip(),
        "corp_id": str(row.get("corp_id") or "").strip(),
        "user_id": str(row.get("user_id") or "").strip(),
        "wechat": str(row.get("wechat") or "").strip(),
        "identity_source": str(row.get("source") or "").strip(),
    }


def _sop_task_error_sql(alias: str) -> str:
    return (
        f"({alias}.status LIKE 'failed%' OR "
        f"{alias}.status IN ('skipped_missing_identity','skipped_unsupported_event_type','skipped_model_error'))"
    )
