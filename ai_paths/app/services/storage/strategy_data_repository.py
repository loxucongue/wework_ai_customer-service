from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.services.storage.serialization import dumps, loads_dict, utc_now_iso


class StrategyDataRepositoryMixin:
    """Durable outbox for non-blocking strategy-data callbacks."""

    def enqueue_strategy_data_callback(
        self,
        *,
        idempotency_key: str,
        record_kind: str,
        task_id: str,
        sales_contact_key: str,
        customer_id: str,
        interface_version: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now_iso()
        callback_id = str(uuid4())
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO strategy_data_outbox
                    (id, idempotency_key, record_kind, task_id, sales_contact_key,
                     customer_id, interface_version, payload_json, status,
                     retry_count, next_retry_at, response_json, error,
                     created_at, updated_at, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, '{}', '', ?, ?, '')
                """,
                (
                    callback_id,
                    str(idempotency_key or "").strip(),
                    str(record_kind or "").strip(),
                    str(task_id or "").strip(),
                    str(sales_contact_key or "").strip(),
                    str(customer_id or "").strip(),
                    str(interface_version or "").strip(),
                    dumps(payload),
                    now,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM strategy_data_outbox WHERE idempotency_key=?",
                (str(idempotency_key or "").strip(),),
            ).fetchone()
        return self._decode_strategy_data_callback(dict(row)) if row else {}

    def reset_processing_strategy_data_callbacks(self) -> int:
        now = utc_now_iso()
        with self.store.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE strategy_data_outbox
                SET status='retry', next_retry_at=?, updated_at=?
                WHERE status='processing'
                """,
                (now, now),
            )
            return int(cursor.rowcount or 0)

    def claim_due_strategy_data_callbacks(self, *, limit: int = 10) -> list[dict[str, Any]]:
        now = utc_now_iso()
        safe_limit = max(1, min(int(limit or 10), 100))
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM strategy_data_outbox
                WHERE status IN ('pending','retry')
                  AND (next_retry_at='' OR next_retry_at<=?)
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (now, safe_limit),
            ).fetchall()
            claimed: list[dict[str, Any]] = []
            for raw in rows:
                callback_id = str(raw["id"] or "")
                cursor = conn.execute(
                    """
                    UPDATE strategy_data_outbox
                    SET status='processing', updated_at=?
                    WHERE id=? AND status IN ('pending','retry')
                    """,
                    (now, callback_id),
                )
                if int(cursor.rowcount or 0) == 1:
                    claimed.append(dict(raw))
        return [self._decode_strategy_data_callback(item) for item in claimed]

    def complete_strategy_data_callback(
        self,
        callback_id: str,
        *,
        response: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        with self.store.connect() as conn:
            conn.execute(
                """
                UPDATE strategy_data_outbox
                SET status='sent', response_json=?, error='', next_retry_at='',
                    sent_at=?, updated_at=?
                WHERE id=?
                """,
                (dumps(response), now, now, str(callback_id or "").strip()),
            )

    def fail_strategy_data_callback(
        self,
        callback_id: str,
        *,
        error: str,
        max_attempts: int,
        base_delay_seconds: float,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT retry_count FROM strategy_data_outbox WHERE id=?",
                (str(callback_id or "").strip(),),
            ).fetchone()
            retry_count = int(row["retry_count"] or 0) + 1 if row else 1
            exhausted = retry_count >= max(1, int(max_attempts or 1))
            status = "dead" if exhausted else "retry"
            delay = max(1.0, float(base_delay_seconds or 1.0)) * (2 ** max(0, retry_count - 1))
            next_retry_at = "" if exhausted else (now + timedelta(seconds=min(delay, 3600.0))).isoformat()
            conn.execute(
                """
                UPDATE strategy_data_outbox
                SET status=?, retry_count=?, next_retry_at=?, error=?, updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    retry_count,
                    next_retry_at,
                    str(error or "")[:2000],
                    now.isoformat(),
                    str(callback_id or "").strip(),
                ),
            )
        return {"status": status, "retry_count": retry_count, "next_retry_at": next_retry_at}

    def strategy_data_outbox_status(self) -> dict[str, int]:
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM strategy_data_outbox GROUP BY status"
            ).fetchall()
        return {str(row["status"] or "unknown"): int(row["count"] or 0) for row in rows}

    @staticmethod
    def _decode_strategy_data_callback(row: dict[str, Any]) -> dict[str, Any]:
        row["payload"] = loads_dict(row.pop("payload_json", "{}"))
        row["response"] = loads_dict(row.pop("response_json", "{}"))
        return row
