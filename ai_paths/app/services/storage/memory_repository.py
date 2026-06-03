from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.services.storage.serialization import dumps, loads_dict, utc_now_iso


class MemoryRepositoryMixin:
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
