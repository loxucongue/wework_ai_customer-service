from __future__ import annotations

from typing import Any


class CustomerRecordAdminRepositoryMixin:
    def inspect_customer_records(self, customer_id: str) -> dict[str, Any]:
        customer = _clean_customer_id(customer_id)
        if not customer:
            return {"customer_id": "", "counts": {}, "sop_summary": [], "latest_events": []}

        with self.store.connect() as conn:
            memory_count = _count(conn, "SELECT COUNT(*) FROM customer_memory WHERE customer_id=?", (customer,))
            history_count = _count(conn, "SELECT COUNT(*) FROM history_events WHERE customer_id=?", (customer,))
            sop_count = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM sop_send_tasks
                WHERE customer_id=? OR external_userid=?
                """,
                (customer, customer),
            )
            conversation_count = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM conversations
                WHERE customer_id=? OR external_userid=?
                """,
                (customer, customer),
            )
            message_count = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM messages
                WHERE conversation_id IN (
                    SELECT id FROM conversations WHERE customer_id=? OR external_userid=?
                )
                """,
                (customer, customer),
            )
            run_count = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM runs
                WHERE customer_id=?
                   OR conversation_id IN (
                       SELECT id FROM conversations WHERE customer_id=? OR external_userid=?
                   )
                """,
                (customer, customer, customer),
            )
            node_trace_count = _count(
                conn,
                """
                SELECT COUNT(*)
                FROM node_traces
                WHERE request_id IN (
                    SELECT request_id FROM runs
                    WHERE customer_id=?
                       OR conversation_id IN (
                           SELECT id FROM conversations WHERE customer_id=? OR external_userid=?
                       )
                )
                """,
                (customer, customer, customer),
            )
            outreach_plan_count = _count(conn, "SELECT COUNT(*) FROM outreach_plans WHERE customer_id=?", (customer,))
            outreach_task_count = _count(conn, "SELECT COUNT(*) FROM outreach_tasks WHERE customer_id=?", (customer,))
            outreach_event_count = _count(conn, "SELECT COUNT(*) FROM outreach_events WHERE customer_id=?", (customer,))
            sop_rows = conn.execute(
                """
                SELECT sop_pack_id, sop_pack_name, sop_category, trigger_source, status,
                       COUNT(*) AS count, MAX(created_at) AS latest_at
                FROM sop_send_tasks
                WHERE customer_id=? OR external_userid=?
                GROUP BY sop_pack_id, sop_pack_name, sop_category, trigger_source, status
                ORDER BY latest_at DESC
                LIMIT 30
                """,
                (customer, customer),
            ).fetchall()
            latest_events = conn.execute(
                """
                SELECT id, event_type, stage, summary, created_at
                FROM history_events
                WHERE customer_id=?
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (customer,),
            ).fetchall()

        return {
            "customer_id": customer,
            "counts": {
                "customer_memory": memory_count,
                "history_events": history_count,
                "sop_send_tasks": sop_count,
                "conversations": conversation_count,
                "messages": message_count,
                "runs": run_count,
                "node_traces": node_trace_count,
                "outreach_plans": outreach_plan_count,
                "outreach_tasks": outreach_task_count,
                "outreach_events": outreach_event_count,
            },
            "sop_summary": [dict(row) for row in sop_rows],
            "latest_events": [dict(row) for row in latest_events],
        }

    def clear_customer_records(
        self,
        customer_id: str,
        *,
        clear_memory: bool = True,
        clear_sop: bool = True,
        clear_conversations: bool = False,
        clear_outreach: bool = False,
    ) -> dict[str, Any]:
        customer = _clean_customer_id(customer_id)
        if not customer:
            return {"customer_id": "", "deleted": {}}

        deleted: dict[str, int] = {}
        with self.store.connect() as conn:
            if clear_conversations:
                request_rows = conn.execute(
                    """
                    SELECT request_id
                    FROM runs
                    WHERE customer_id=?
                       OR conversation_id IN (
                           SELECT id FROM conversations WHERE customer_id=? OR external_userid=?
                       )
                    """,
                    (customer, customer, customer),
                ).fetchall()
                request_ids = [(str(row["request_id"]),) for row in request_rows if str(row["request_id"] or "")]
                if request_ids:
                    deleted["node_traces"] = _delete_many(conn, "DELETE FROM node_traces WHERE request_id=?", request_ids)
                deleted["runs"] = _execute_delete(
                    conn,
                    """
                    DELETE FROM runs
                    WHERE customer_id=?
                       OR conversation_id IN (
                           SELECT id FROM conversations WHERE customer_id=? OR external_userid=?
                       )
                    """,
                    (customer, customer, customer),
                )
                deleted["messages"] = _execute_delete(
                    conn,
                    """
                    DELETE FROM messages
                    WHERE conversation_id IN (
                        SELECT id FROM conversations WHERE customer_id=? OR external_userid=?
                    )
                    """,
                    (customer, customer),
                )
                deleted["conversations"] = _execute_delete(
                    conn,
                    "DELETE FROM conversations WHERE customer_id=? OR external_userid=?",
                    (customer, customer),
                )

            if clear_outreach:
                deleted["outreach_events"] = _execute_delete(
                    conn,
                    "DELETE FROM outreach_events WHERE customer_id=?",
                    (customer,),
                )
                deleted["outreach_tasks"] = _execute_delete(
                    conn,
                    "DELETE FROM outreach_tasks WHERE customer_id=?",
                    (customer,),
                )
                deleted["outreach_plans"] = _execute_delete(
                    conn,
                    "DELETE FROM outreach_plans WHERE customer_id=?",
                    (customer,),
                )

            if clear_sop:
                deleted["sop_send_tasks"] = _execute_delete(
                    conn,
                    "DELETE FROM sop_send_tasks WHERE customer_id=? OR external_userid=?",
                    (customer, customer),
                )

            if clear_memory:
                deleted["history_events"] = _execute_delete(
                    conn,
                    "DELETE FROM history_events WHERE customer_id=?",
                    (customer,),
                )
                deleted["customer_memory"] = _execute_delete(
                    conn,
                    "DELETE FROM customer_memory WHERE customer_id=?",
                    (customer,),
                )

        return {
            "status": "ok",
            "customer_id": customer,
            "deleted": deleted,
        }


def _clean_customer_id(customer_id: str) -> str:
    return str(customer_id or "").strip()


def _count(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] if row else 0)


def _execute_delete(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    result = conn.execute(sql, params)
    return int(result.rowcount or 0)


def _delete_many(conn: Any, sql: str, params: list[tuple[Any, ...]]) -> int:
    before = conn.total_changes
    conn.executemany(sql, params)
    return int(conn.total_changes - before)
