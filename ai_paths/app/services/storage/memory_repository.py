from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from app.services.storage.serialization import dumps, loads_dict, utc_now_iso


def _load_memory_row(conn: Any, customer_id: str) -> Any:
    return conn.execute(
        """
        SELECT customer_id, portrait, basic_info, lifecycle_stage,
               last_customer_message_at, last_staff_message_at, last_ai_reply_at,
               last_manual_takeover_at, last_outreach_at, outreach_status,
               outreach_plan_id, updated_at
        FROM customer_memory
        WHERE customer_id=?
        """,
        (customer_id,),
    ).fetchone()


def _load_memory_events(conn: Any, customer_id: str) -> list[Any]:
    return list(
        conn.execute(
            """
            SELECT id, event_type, stage, summary, facts, impact, confidence, created_at
            FROM history_events
            WHERE customer_id=?
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (customer_id,),
        ).fetchall()
    )


def _decode_memory(row: Any, events: list[Any]) -> dict[str, Any]:
    return {
        "customer_id": row["customer_id"],
        "portrait": loads_dict(row["portrait"]),
        "basic_info": loads_dict(row["basic_info"]),
        "lifecycle_stage": row["lifecycle_stage"] or "",
        "last_customer_message_at": row["last_customer_message_at"] or "",
        "last_staff_message_at": row["last_staff_message_at"] or "",
        "last_ai_reply_at": row["last_ai_reply_at"] or "",
        "last_manual_takeover_at": row["last_manual_takeover_at"] or "",
        "last_outreach_at": row["last_outreach_at"] or "",
        "outreach_status": row["outreach_status"] or "none",
        "outreach_plan_id": row["outreach_plan_id"] or "",
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
            for item in reversed(events)
        ],
    }


def _sop_scope_predicates(
    *,
    customer_id: str,
    external_userid: str,
    corp_id: str,
    wechat: str,
) -> tuple[list[str], list[Any]]:
    clean_wechat = str(wechat or "").strip()
    if not clean_wechat:
        return [], []
    clauses = ["status='sent'"]
    params: list[Any] = []
    if str(external_userid or "").strip():
        clauses.append("external_userid=?")
        params.append(str(external_userid).strip())
    elif str(customer_id or "").strip():
        clauses.append("customer_id=?")
        params.append(str(customer_id).strip())
    else:
        return [], []
    if str(corp_id or "").strip():
        clauses.append("corp_id=?")
        params.append(str(corp_id).strip())
    clauses.append("LOWER(wechat)=LOWER(?)")
    params.append(clean_wechat)
    return clauses, params


def _append_unique(output: list[str], value: Any, *, allow_merge: bool = False) -> None:
    clean = str(value or "").strip()
    if clean and (allow_merge or not clean.startswith("merge:")) and clean not in output:
        output.append(clean)


def _decode_sop_delivery_rows(rows: list[Any]) -> tuple[list[str], list[str]]:
    pack_ids: list[str] = []
    categories: list[str] = []
    for row in rows:
        pack_id = str(row["sop_pack_id"] or "").strip()
        category = str(row["sop_category"] or "").strip()
        payload = loads_dict(row["send_payload_json"])
        if pack_id:
            _append_unique(pack_ids, pack_id)
            for selected_id in payload.get("selected_sop_pack_ids") or []:
                _append_unique(pack_ids, selected_id, allow_merge=True)
        if category:
            _append_unique(categories, category)
            for selected_category in payload.get("selected_sop_categories") or []:
                _append_unique(categories, selected_category, allow_merge=True)
    return pack_ids, categories


class MemoryRepositoryMixin:
    def has_stop_contact(self, customer_id: str) -> bool:
        clean_customer_id = str(customer_id or "").strip()
        if not clean_customer_id:
            return False
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM history_events
                WHERE customer_id=? AND event_type='stop_contact_confirmed'
                LIMIT 1
                """,
                (clean_customer_id,),
            ).fetchone()
        return row is not None

    def load_memory(self, customer_id: str) -> dict[str, Any] | None:
        with self.store.connect() as conn:
            row = _load_memory_row(conn, customer_id)
            if not row:
                return None
            events = _load_memory_events(conn, customer_id)
        return _decode_memory(row, events)

    def load_reply_customer_snapshot(
        self,
        *,
        sales_contact_key: str,
        customer_id: str,
        external_userid: str,
        corp_id: str = "",
        wechat: str = "",
    ) -> dict[str, Any]:
        """Load memory and scoped SOP delivery facts with one DB connection.

        The memory key already contains the complete sales-contact boundary.
        SOP rows retain their existing explicit boundary predicates; no result is
        shared across receptionist WeChat accounts or external customers.
        """

        started = time.perf_counter()
        acquire_started = time.perf_counter()
        memory_row = None
        memory_events: list[Any] = []
        sop_rows: list[Any] = []
        statement_count = 0
        with self.store.connect() as conn:
            connection_acquire_ms = max(0, int((time.perf_counter() - acquire_started) * 1000))
            memory_started = time.perf_counter()
            if str(sales_contact_key or "").strip():
                memory_row = _load_memory_row(conn, sales_contact_key)
                statement_count += 1
                if memory_row:
                    memory_events = list(_load_memory_events(conn, sales_contact_key))
                    statement_count += 1
            memory_query_ms = max(0, int((time.perf_counter() - memory_started) * 1000))

            sop_started = time.perf_counter()
            clauses, params = _sop_scope_predicates(
                customer_id=customer_id,
                external_userid=external_userid,
                corp_id=corp_id,
                wechat=wechat,
            )
            if clauses:
                sop_rows = list(
                    conn.execute(
                        f"""
                        SELECT sop_pack_id, sop_category, send_payload_json
                        FROM sop_send_tasks
                        WHERE {' AND '.join(clauses)}
                          AND (sop_pack_id<>'' OR sop_category<>'')
                        """,
                        params,
                    ).fetchall()
                )
                statement_count += 1
            sop_query_ms = max(0, int((time.perf_counter() - sop_started) * 1000))

        completed_pack_ids, completed_categories = _decode_sop_delivery_rows(sop_rows)
        return {
            "memory": _decode_memory(memory_row, memory_events) if memory_row else None,
            "completed_pack_ids": completed_pack_ids,
            "completed_categories": completed_categories,
            "storage_timing": {
                "connection_acquire_ms": connection_acquire_ms,
                "memory_query_ms": memory_query_ms,
                "sop_query_ms": sop_query_ms,
                "total_ms": max(0, int((time.perf_counter() - started) * 1000)),
                "connection_count": 1,
                "statement_count": statement_count,
            },
        }

    def load_memories(self, customer_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(str(item or "").strip() for item in customer_ids if str(item or "").strip()))
        if not ids:
            return {}
        memory_rows: list[Any] = []
        event_rows: list[Any] = []
        with self.store.connect() as conn:
            for offset in range(0, len(ids), 500):
                chunk = ids[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                memory_rows.extend(
                    conn.execute(
                        f"""
                        SELECT customer_id, portrait, basic_info, lifecycle_stage,
                               last_customer_message_at, last_staff_message_at, last_ai_reply_at,
                               last_manual_takeover_at, last_outreach_at, outreach_status,
                               outreach_plan_id, updated_at
                        FROM customer_memory
                        WHERE customer_id IN ({placeholders})
                        """,
                        chunk,
                    ).fetchall()
                )
                event_rows.extend(
                    conn.execute(
                        f"""
                        SELECT id, customer_id, event_type, stage, summary, facts,
                               impact, confidence, created_at
                        FROM history_events
                        WHERE customer_id IN ({placeholders})
                        ORDER BY customer_id, created_at DESC
                        """,
                        chunk,
                    ).fetchall()
                )
        events_by_customer: dict[str, list[dict[str, Any]]] = {}
        for item in event_rows:
            customer_id = str(item["customer_id"] or "")
            events = events_by_customer.setdefault(customer_id, [])
            if len(events) < 100:
                events.append(
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
                )
        return {
            str(row["customer_id"]): {
                "customer_id": row["customer_id"],
                "portrait": loads_dict(row["portrait"]),
                "basic_info": loads_dict(row["basic_info"]),
                "lifecycle_stage": row["lifecycle_stage"] or "",
                "last_customer_message_at": row["last_customer_message_at"] or "",
                "last_staff_message_at": row["last_staff_message_at"] or "",
                "last_ai_reply_at": row["last_ai_reply_at"] or "",
                "last_manual_takeover_at": row["last_manual_takeover_at"] or "",
                "last_outreach_at": row["last_outreach_at"] or "",
                "outreach_status": row["outreach_status"] or "none",
                "outreach_plan_id": row["outreach_plan_id"] or "",
                "updated_at": row["updated_at"],
                "history_events": list(
                    reversed(events_by_customer.get(str(row["customer_id"]), []))
                ),
            }
            for row in memory_rows
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
            event_rows = []
            for event in memory.get("history_events") or []:
                if not isinstance(event, dict):
                    continue
                event_rows.append(
                    (
                        str(event.get("event_id") or event.get("id") or uuid4()),
                        customer_id,
                        str(event.get("event_type") or ""),
                        str(event.get("stage") or ""),
                        str(event.get("summary") or ""),
                        dumps(event.get("facts") or {}),
                        str(event.get("impact") or ""),
                        float(event.get("confidence") or 0),
                        str(event.get("event_time") or event.get("created_at") or now),
                    )
                )
            if event_rows:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO history_events
                        (id, customer_id, event_type, stage, summary, facts, impact, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    event_rows,
                )
