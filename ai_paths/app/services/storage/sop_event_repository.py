from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
                SET status=?, error=?, next_retry_at='', updated_at=?
                WHERE event_id=?
                """,
                (status, error, now, event_id),
            )
        return self.get_sop_event(event_id)

    def list_platform_sop_task_records(
        self,
        *,
        limit: int = 100,
        task_id: str = "",
        customer_id: str = "",
    ) -> list[dict[str, Any]]:
        clauses = ["e.event_type='platform_sop_task'"]
        params: list[Any] = []
        if task_id:
            clauses.append("e.event_id=?")
            params.append(f"platform_sop_task:{str(task_id).strip()}")
        if customer_id:
            clauses.append("t.customer_id=?")
            params.append(str(customer_id).strip())
        safe_limit = max(1, min(int(limit or 100), 500))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    e.event_id,
                    e.status AS event_status,
                    e.error AS event_error,
                    e.received_at,
                    e.updated_at AS event_updated_at,
                    e.raw_payload_json,
                    t.id AS local_task_id,
                    t.customer_id,
                    t.external_userid,
                    t.corp_id,
                    t.user_id,
                    t.wechat,
                    t.sop_pack_name,
                    t.status AS task_status,
                    t.reply_messages_json,
                    t.send_payload_json,
                    t.send_response_json,
                    t.error AS task_error,
                    t.created_at AS task_created_at,
                    t.updated_at AS task_updated_at,
                    t.sent_at
                FROM sop_events e
                LEFT JOIN sop_send_tasks t ON t.event_id=e.event_id
                WHERE {' AND '.join(clauses)}
                ORDER BY e.received_at DESC
                LIMIT ?
                """,
                [*params, safe_limit],
            ).fetchall()
        records: list[dict[str, Any]] = []
        for raw_row in rows:
            row = dict(raw_row)
            raw_payload = loads_dict(row.pop("raw_payload_json", "{}"))
            row["platform_task"] = (
                raw_payload.get("platform_task")
                if isinstance(raw_payload.get("platform_task"), dict)
                else {}
            )
            row["reply_messages"] = loads_list(row.pop("reply_messages_json", "[]"))
            row["send_payload"] = loads_dict(row.pop("send_payload_json", "{}"))
            row["send_response"] = loads_dict(row.pop("send_response_json", "{}"))
            records.append(row)
        return records

    def list_recent_platform_sop_wechats(self, *, days: int = 2) -> list[dict[str, Any]]:
        safe_days = max(1, min(int(days or 2), 30))
        beijing = timezone(timedelta(hours=8))
        local_start = datetime.now(beijing).replace(hour=0, minute=0, second=0, microsecond=0)
        since = (local_start - timedelta(days=safe_days - 1)).astimezone(timezone.utc).isoformat()
        nested = self.store.json_text("e.raw_payload_json", "$.platform_task.user_wechat")
        root = self.store.json_text("e.raw_payload_json", "$.user_wechat")
        expression = f"COALESCE(NULLIF({nested}, ''), NULLIF({root}, ''))"
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    {expression} AS user_wechat,
                    COUNT(*) AS task_count,
                    MIN(e.received_at) AS first_seen_at,
                    MAX(e.received_at) AS last_seen_at
                FROM sop_events e
                WHERE e.event_type='platform_sop_task'
                  AND e.received_at>=?
                  AND {expression} IS NOT NULL
                GROUP BY {expression}
                ORDER BY task_count DESC, user_wechat ASC
                """,
                (since,),
            ).fetchall()
        return [dict(row) for row in rows if str(dict(row).get("user_wechat") or "").strip()]

    def schedule_sop_event_model_retry(
        self,
        event_id: str,
        *,
        error: str,
        max_attempts: int,
        base_delay_seconds: float,
        max_delay_seconds: float,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT retry_count FROM sop_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if row is None:
                return {}
            retry_count = int(row["retry_count"] or 0) + 1
            if retry_count > max(0, int(max_attempts)):
                status = "retry_exhausted_model"
                next_retry_at = ""
                event_error = error
            else:
                status = "retry_pending_model"
                event_error = ""
                delay = min(
                    max(0.0, float(max_delay_seconds)),
                    max(0.0, float(base_delay_seconds)) * (2 ** max(0, retry_count - 1)),
                )
                next_retry_at = (now + timedelta(seconds=delay)).isoformat()
            conn.execute(
                """
                UPDATE sop_events
                SET status=?, error=?, retry_count=?, next_retry_at=?, last_retry_error=?, updated_at=?
                WHERE event_id=?
                """,
                (status, event_error, retry_count, next_retry_at, error, now.isoformat(), event_id),
            )
        return self.get_sop_event(event_id)

    def claim_due_sop_event_model_retries(self, *, limit: int = 5) -> list[dict[str, Any]]:
        now = utc_now_iso()
        safe_limit = max(1, min(int(limit or 5), 50))
        claimed: list[dict[str, Any]] = []
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id FROM sop_events
                WHERE status='retry_pending_model' AND next_retry_at<>'' AND next_retry_at<=?
                ORDER BY next_retry_at ASC
                LIMIT ?
                """,
                (now, safe_limit),
            ).fetchall()
            for row in rows:
                event_id = str(row["event_id"] or "")
                updated = conn.execute(
                    """
                    UPDATE sop_events SET status='retry_processing_model', updated_at=?
                    WHERE event_id=? AND status='retry_pending_model'
                    """,
                    (now, event_id),
                )
                if updated.rowcount:
                    claimed.append({"event_id": event_id})
        return claimed

    def recover_interrupted_sop_event_model_retries(self) -> int:
        now = utc_now_iso()
        with self.store.connect() as conn:
            result = conn.execute(
                """
                UPDATE sop_events
                SET status='retry_pending_model', next_retry_at=?, updated_at=?
                WHERE status='retry_processing_model'
                """,
                (now, now),
            )
        return int(result.rowcount or 0)

    def resolve_sop_event_model_retry_tasks(self, event_id: str) -> int:
        now = utc_now_iso()
        with self.store.connect() as conn:
            result = conn.execute(
                """
                UPDATE sop_send_tasks
                SET status='model_retry_resolved', error='', updated_at=?
                WHERE event_id=? AND status='failed_model_error'
                """,
                (now, event_id),
            )
        return int(result.rowcount or 0)

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
            current = conn.execute(
                "SELECT send_payload_json, send_response_json FROM sop_send_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            existing_payload = loads_dict(current["send_payload_json"]) if current else {}
            existing_response = loads_dict(current["send_response_json"]) if current else {}
            conn.execute(
                """
                UPDATE sop_send_tasks
                SET status=?, send_payload_json=?, send_response_json=?, error=?,
                    sent_at=COALESCE(NULLIF(?, ''), sent_at), updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    dumps(send_payload if send_payload is not None else existing_payload),
                    dumps(send_response if send_response is not None else existing_response),
                    error,
                    sent_at,
                    now,
                    task_id,
                ),
            )
            row = conn.execute("SELECT * FROM sop_send_tasks WHERE id=?", (task_id,)).fetchone()
        return self._decode_sop_send_task(dict(row)) if row else {}

    def get_sop_send_task(self, task_id: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sop_send_tasks WHERE id=?",
                (str(task_id or "").strip(),),
            ).fetchone()
        return self._decode_sop_send_task(dict(row)) if row else {}

    def list_quiet_hour_backlog_tasks(
        self,
        *,
        start_at: str,
        end_at: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 500), 2000))
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.*,
                    e.event_type,
                    e.raw_payload_json
                FROM sop_send_tasks t
                JOIN sop_events e ON e.event_id=t.event_id
                WHERE (
                        (t.status='skipped_quiet_hours_inactive'
                         AND e.event_type IN ('sop_friend_added_schedule_batch','sop_friend_added_immediate'))
                     OR (t.status='completed_without_send' AND e.event_type='platform_sop_task')
                  )
                  AND t.created_at>=?
                  AND t.created_at<?
                ORDER BY t.created_at ASC
                LIMIT ?
                """,
                (start_at, end_at, safe_limit),
            ).fetchall()
        tasks: list[dict[str, Any]] = []
        for raw_row in rows:
            row = dict(raw_row)
            raw_payload = loads_dict(row.pop("raw_payload_json", "{}"))
            task = self._decode_sop_send_task(row)
            task["raw_event_payload"] = raw_payload
            task["event_type"] = str(row.get("event_type") or raw_payload.get("event_type") or "")
            if task["event_type"] == "platform_sop_task":
                platform_task = raw_payload.get("platform_task") if isinstance(raw_payload.get("platform_task"), dict) else {}
                trigger = str(
                    platform_task.get("triggerEvent")
                    or platform_task.get("trigger_event")
                    or platform_task.get("eventType")
                    or platform_task.get("event_type")
                    or ""
                ).strip().lower()
                send_payload = task.get("send_payload") if isinstance(task.get("send_payload"), dict) else {}
                decision = send_payload.get("decision") if isinstance(send_payload.get("decision"), dict) else {}
                reason = str(decision.get("reason") or "").strip()
                if trigger != "add_wecom" or reason not in {
                    "quiet_hours_unknown_activity",
                    "quiet_hours_customer_pending_reply",
                    "quiet_hours_first_add_inactive",
                }:
                    continue
            marker = task.get("send_payload") if isinstance(task.get("send_payload"), dict) else {}
            backlog_marker = marker.get("backlog_marker") if isinstance(marker.get("backlog_marker"), dict) else {}
            if backlog_marker and backlog_marker.get("pending") is False:
                continue
            tasks.append(task)
        return tasks

    def list_quiet_backlog_fusion_tasks(
        self,
        *,
        start_at: str,
        end_at: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 500), 2000))
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.*, e.received_at AS event_received_at, e.status AS event_status,
                       e.raw_payload_json AS raw_payload_json
                FROM sop_send_tasks t
                JOIN sop_events e ON e.event_id=t.event_id
                WHERE e.event_type='sop_quiet_backlog_fusion'
                  AND t.created_at>=?
                  AND t.created_at<?
                ORDER BY t.created_at DESC
                LIMIT ?
                """,
                (start_at, end_at, safe_limit),
            ).fetchall()
        tasks: list[dict[str, Any]] = []
        for raw_row in rows:
            row = dict(raw_row)
            event_received_at = str(row.pop("event_received_at", "") or "")
            event_status = str(row.pop("event_status", "") or "")
            raw_payload = loads_dict(row.pop("raw_payload_json", "{}"))
            task = self._decode_sop_send_task(row)
            task["event_received_at"] = event_received_at
            task["event_status"] = event_status
            task["raw_event_payload"] = raw_payload
            tasks.append(task)
        return tasks

    def list_sop_send_tasks_by_ids(self, task_ids: list[str]) -> list[dict[str, Any]]:
        normalized = list(dict.fromkeys(str(task_id or "").strip() for task_id in task_ids if str(task_id or "").strip()))
        if not normalized:
            return []
        normalized = normalized[:2000]
        placeholders = ",".join("?" for _ in normalized)
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT t.*, e.event_type AS source_event_type, e.raw_payload_json AS source_raw_payload_json
                FROM sop_send_tasks t
                LEFT JOIN sop_events e ON e.event_id=t.event_id
                WHERE t.id IN ({placeholders})
                ORDER BY t.created_at ASC
                """,
                normalized,
            ).fetchall()
        tasks: list[dict[str, Any]] = []
        for raw_row in rows:
            row = dict(raw_row)
            source_event_type = str(row.pop("source_event_type", "") or "")
            source_raw_payload = loads_dict(row.pop("source_raw_payload_json", "{}"))
            task = self._decode_sop_send_task(row)
            task["source_event_type"] = source_event_type
            task["raw_event_payload"] = source_raw_payload
            tasks.append(task)
        return tasks

    def get_sop_send_task_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sop_send_tasks WHERE idempotency_key=?",
                (str(idempotency_key or "").strip(),),
            ).fetchone()
        return self._decode_sop_send_task(dict(row)) if row else {}

    def find_sop_send_task_delivery_duplicate(
        self,
        send_once_key: str,
        *,
        exclude_idempotency_key: str = "",
    ) -> dict[str, Any]:
        clean_key = str(send_once_key or "").strip()
        if not clean_key:
            return {}
        params: list[Any] = [clean_key]
        exclude_sql = ""
        if exclude_idempotency_key:
            exclude_sql = "AND idempotency_key<>?"
            params.append(str(exclude_idempotency_key or "").strip())
        with self.store.connect() as conn:
            row = conn.execute(
                f"""
                SELECT *
                FROM sop_send_tasks
                WHERE send_once_key=?
                  {exclude_sql}
                  AND (
                    status IN ('sent', 'sending')
                    OR error='active_send_timeout_unknown_result'
                  )
                ORDER BY created_at ASC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return self._decode_sop_send_task(dict(row)) if row else {}

    def list_sop_events_by_statuses(
        self,
        statuses: list[str],
        *,
        limit: int = 10,
        event_type: str = "",
    ) -> list[dict[str, Any]]:
        clean_statuses = [str(item or "").strip() for item in statuses if str(item or "").strip()]
        if not clean_statuses:
            return []
        placeholders = ",".join("?" for _ in clean_statuses)
        clauses = [f"status IN ({placeholders})"]
        params: list[Any] = list(clean_statuses)
        if event_type:
            clauses.append("event_type=?")
            params.append(str(event_type))
        params.append(max(1, min(int(limit or 10), 100)))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM sop_events
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._decode_sop_event(dict(row)) for row in rows]

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

    def list_recent_sop_send_tasks_for_customer(
        self,
        *,
        customer_id: str,
        external_userid: str,
        corp_id: str = "",
        wechat: str = "",
        before: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not str(wechat or "").strip():
            return []
        clauses = ["trigger_source IN ('sop_event','platform_auto_opening')"]
        params: list[Any] = []
        if external_userid:
            clauses.append("external_userid=?")
            params.append(external_userid)
        elif customer_id:
            clauses.append("customer_id=?")
            params.append(customer_id)
        else:
            return []
        if corp_id:
            clauses.append("corp_id=?")
            params.append(corp_id)
        clauses.append("LOWER(wechat)=LOWER(?)")
        params.append(wechat)
        if before:
            clauses.append("created_at<=?")
            params.append(before)
        safe_limit = max(1, min(int(limit or 100), 500))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM sop_send_tasks
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [*params, safe_limit],
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
                          AND (?='' OR LOWER(wechat)=LOWER(?))
                        UNION ALL
                        SELECT customer_id, external_userid, corp_id, user_id, wechat, 'sop_send_tasks' AS source, updated_at
                        FROM sop_send_tasks
                        WHERE corp_id<>'' AND user_id<>'' AND wechat<>''
                          AND (LOWER(external_userid)=LOWER(?) OR LOWER(customer_id)=LOWER(?))
                          AND (?='' OR LOWER(wechat)=LOWER(?))
                        UNION ALL
                        SELECT customer_id, external_userid, corp_id, user_id, wechat, 'outreach_plans' AS source, updated_at
                        FROM outreach_plans
                        WHERE corp_id<>'' AND user_id<>'' AND wechat<>''
                          AND (LOWER(external_userid)=LOWER(?) OR LOWER(customer_id)=LOWER(?))
                          AND (?='' OR LOWER(wechat)=LOWER(?))
                    ) AS identities
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (
                        external_key, customer_key, wechat_key, wechat_key,
                        external_key, customer_key, wechat_key, wechat_key,
                        external_key, customer_key, wechat_key, wechat_key,
                    ),
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
                    ) AS identities
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (wechat_key, wechat_key, wechat_key),
                ).fetchone()
                if row:
                    return _identity_row(dict(row))
        return {}

    def has_sent_sop_pack_for_customer(
        self,
        *,
        customer_id: str,
        external_userid: str,
        sop_pack_id: str,
        corp_id: str = "",
        wechat: str = "",
    ) -> bool:
        if not sop_pack_id or not str(wechat or "").strip():
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
        clauses.append("LOWER(wechat)=LOWER(?)")
        params.append(wechat)
        if corp_id:
            clauses.append("corp_id=?")
            params.append(corp_id)
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
        corp_id: str = "",
        wechat: str = "",
        sent_before: str = "",
    ) -> list[str]:
        if not str(wechat or "").strip():
            return []
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
        if corp_id:
            clauses.append("corp_id=?")
            params.append(corp_id)
        if wechat:
            clauses.append("LOWER(wechat)=LOWER(?)")
            params.append(wechat)
        if sent_before:
            clauses.append("COALESCE(NULLIF(sent_at, ''), updated_at)<=?")
            params.append(sent_before)
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT sop_pack_id, send_payload_json
                FROM sop_send_tasks
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall()
        output: list[str] = []
        for row in rows:
            pack_id = str(row["sop_pack_id"] or "").strip()
            if pack_id and not pack_id.startswith("merge:") and pack_id not in output:
                output.append(pack_id)
            send_payload = loads_dict(row["send_payload_json"])
            for selected_id in send_payload.get("selected_sop_pack_ids") or []:
                selected_id = str(selected_id or "").strip()
                if selected_id and selected_id not in output:
                    output.append(selected_id)
        return output

    def list_sent_sop_categories_for_customer(
        self,
        *,
        customer_id: str,
        external_userid: str,
        corp_id: str = "",
        wechat: str = "",
        sent_before: str = "",
    ) -> list[str]:
        if not str(wechat or "").strip():
            return []
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
        if corp_id:
            clauses.append("corp_id=?")
            params.append(corp_id)
        if wechat:
            clauses.append("LOWER(wechat)=LOWER(?)")
            params.append(wechat)
        if sent_before:
            clauses.append("COALESCE(NULLIF(sent_at, ''), updated_at)<=?")
            params.append(sent_before)
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT sop_category, send_payload_json
                FROM sop_send_tasks
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall()
        output: list[str] = []
        for row in rows:
            category = str(row["sop_category"] or "").strip()
            if category and not category.startswith("merge:") and category not in output:
                output.append(category)
            send_payload = loads_dict(row["send_payload_json"])
            for selected_category in send_payload.get("selected_sop_categories") or []:
                selected_category = str(selected_category or "").strip()
                if selected_category and selected_category not in output:
                    output.append(selected_category)
        return output

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
