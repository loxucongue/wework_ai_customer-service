from __future__ import annotations

from typing import Any

from app.services.customer_scope import build_customer_scope
from app.services.storage.store_base import scalar


class CustomerRecordAdminRepositoryMixin:
    def resolve_customer_account_scope(
        self,
        customer_id: str,
        *,
        wechat: str,
        corp_id: str = "",
        external_userid: str = "",
    ) -> dict[str, Any]:
        customer = _clean(customer_id)
        account = _clean(wechat)
        requested_corp = _clean(corp_id)
        requested_external = _clean(external_userid)
        if not customer or not account:
            return {
                "status": "missing_required_scope",
                "customer_id": customer,
                "wechat": account,
                "missing": [name for name, value in (("customer_id", customer), ("wechat", account)) if not value],
            }

        rows: list[dict[str, str]] = []
        with self.store.connect() as conn:
            for table in ("conversations", "sop_send_tasks", "outreach_plans"):
                clauses = ["(customer_id=? OR external_userid=?)", "wechat=?"]
                params: list[Any] = [customer, customer, account]
                if requested_corp:
                    clauses.append("corp_id=?")
                    params.append(requested_corp)
                if requested_external:
                    clauses.append("external_userid=?")
                    params.append(requested_external)
                found = conn.execute(
                    f"""
                    SELECT customer_id, external_userid, corp_id, wechat
                    FROM {table}
                    WHERE {' AND '.join(clauses)}
                    ORDER BY updated_at DESC
                    LIMIT 20
                    """,
                    params,
                ).fetchall()
                rows.extend({key: _clean(row[key]) for key in ("customer_id", "external_userid", "corp_id", "wechat")} for row in found)

        external_values = {row["external_userid"] for row in rows if row["external_userid"]}
        corp_values = {row["corp_id"] for row in rows if row["corp_id"]}
        if requested_external:
            external_values = {requested_external}
        if requested_corp:
            corp_values = {requested_corp}
        if len(external_values) > 1 or len(corp_values) > 1:
            return {
                "status": "ambiguous_scope",
                "customer_id": customer,
                "wechat": account,
                "corp_candidates": sorted(corp_values),
                "external_userid_candidates": sorted(external_values),
            }

        resolved_external = next(iter(external_values), "")
        resolved_corp = next(iter(corp_values), "")
        matched_customer = next((row["customer_id"] for row in rows if row["customer_id"]), customer)
        can_use_customer_fallback = bool(rows) and not resolved_external
        scope = build_customer_scope(
            corp_id=resolved_corp,
            wechat=account,
            external_userid=resolved_external,
            customer_id=matched_customer if can_use_customer_fallback else "",
        )
        return {
            "status": "resolved" if scope.persistence_allowed else "records_only",
            "customer_id": customer,
            "resolved_customer_id": matched_customer,
            "external_userid": resolved_external,
            "corp_id": resolved_corp,
            "wechat": account,
            "sales_contact_key": scope.sales_contact_key,
            "memory_scope_available": scope.persistence_allowed,
            "matched_identity_rows": len(rows),
            "missing": list(scope.missing),
        }

    def inspect_customer_records(
        self,
        customer_id: str,
        *,
        wechat: str,
        corp_id: str = "",
        external_userid: str = "",
    ) -> dict[str, Any]:
        scope = self.resolve_customer_account_scope(
            customer_id,
            wechat=wechat,
            corp_id=corp_id,
            external_userid=external_userid,
        )
        customer = _clean(customer_id)
        account = _clean(wechat)
        resolved_corp = _clean(scope.get("corp_id"))
        sales_key = _clean(scope.get("sales_contact_key"))
        if not customer or not account or scope.get("status") == "ambiguous_scope":
            return {"customer_id": customer, "wechat": account, "scope": scope, "counts": {}, "sop_summary": [], "latest_events": []}

        account_sql, account_params = _account_match(customer, account, resolved_corp)
        with self.store.connect() as conn:
            memory_count = _count(conn, "SELECT COUNT(*) FROM customer_memory WHERE customer_id=?", (sales_key,)) if sales_key else 0
            history_count = _count(conn, "SELECT COUNT(*) FROM history_events WHERE customer_id=?", (sales_key,)) if sales_key else 0
            sop_count = _count(conn, f"SELECT COUNT(*) FROM sop_send_tasks WHERE {account_sql}", account_params)
            conversation_count = _count(conn, f"SELECT COUNT(*) FROM conversations WHERE {account_sql}", account_params)
            message_count = _count(
                conn,
                f"SELECT COUNT(*) FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE {account_sql})",
                account_params,
            )
            run_count = _count(
                conn,
                f"SELECT COUNT(*) FROM runs WHERE conversation_id IN (SELECT id FROM conversations WHERE {account_sql})",
                account_params,
            )
            node_trace_count = _count(
                conn,
                f"""
                SELECT COUNT(*) FROM node_traces
                WHERE request_id IN (
                    SELECT request_id FROM runs
                    WHERE conversation_id IN (SELECT id FROM conversations WHERE {account_sql})
                )
                """,
                account_params,
            )
            outreach_plan_count = _count(conn, f"SELECT COUNT(*) FROM outreach_plans WHERE {account_sql}", account_params)
            outreach_task_count = _count(
                conn,
                f"SELECT COUNT(*) FROM outreach_tasks WHERE plan_id IN (SELECT id FROM outreach_plans WHERE {account_sql})",
                account_params,
            )
            outreach_event_count = _count(
                conn,
                f"SELECT COUNT(*) FROM outreach_events WHERE plan_id IN (SELECT id FROM outreach_plans WHERE {account_sql})",
                account_params,
            )
            sop_rows = conn.execute(
                f"""
                SELECT sop_pack_id, sop_pack_name, sop_category, trigger_source, status,
                       COUNT(*) AS count, MAX(created_at) AS latest_at
                FROM sop_send_tasks
                WHERE {account_sql}
                GROUP BY sop_pack_id, sop_pack_name, sop_category, trigger_source, status
                ORDER BY latest_at DESC
                LIMIT 30
                """,
                account_params,
            ).fetchall()
            latest_events = (
                conn.execute(
                    """
                    SELECT id, event_type, stage, summary, created_at
                    FROM history_events
                    WHERE customer_id=?
                    ORDER BY created_at DESC
                    LIMIT 20
                    """,
                    (sales_key,),
                ).fetchall()
                if sales_key
                else []
            )

        return {
            "customer_id": customer,
            "wechat": account,
            "scope": scope,
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
        wechat: str,
        corp_id: str = "",
        external_userid: str = "",
        clear_memory: bool = True,
        clear_sop: bool = True,
        clear_conversations: bool = False,
        clear_outreach: bool = False,
    ) -> dict[str, Any]:
        scope = self.resolve_customer_account_scope(
            customer_id,
            wechat=wechat,
            corp_id=corp_id,
            external_userid=external_userid,
        )
        customer = _clean(customer_id)
        account = _clean(wechat)
        if not customer or not account:
            return {"status": "rejected", "customer_id": customer, "wechat": account, "scope": scope, "deleted": {}}
        if scope.get("status") == "ambiguous_scope":
            return {"status": "ambiguous_scope", "customer_id": customer, "wechat": account, "scope": scope, "deleted": {}}

        account_sql, account_params = _account_match(customer, account, _clean(scope.get("corp_id")))
        sales_key = _clean(scope.get("sales_contact_key"))
        deleted: dict[str, int] = {}
        with self.store.connect() as conn:
            if clear_conversations:
                request_rows = conn.execute(
                    f"""
                    SELECT request_id FROM runs
                    WHERE conversation_id IN (SELECT id FROM conversations WHERE {account_sql})
                    """,
                    account_params,
                ).fetchall()
                request_ids = [(str(row["request_id"]),) for row in request_rows if str(row["request_id"] or "")]
                if request_ids:
                    deleted["node_traces"] = _delete_many(conn, "DELETE FROM node_traces WHERE request_id=?", request_ids)
                deleted["runs"] = _execute_delete(
                    conn,
                    f"DELETE FROM runs WHERE conversation_id IN (SELECT id FROM conversations WHERE {account_sql})",
                    account_params,
                )
                deleted["messages"] = _execute_delete(
                    conn,
                    f"DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE {account_sql})",
                    account_params,
                )
                deleted["conversations"] = _execute_delete(conn, f"DELETE FROM conversations WHERE {account_sql}", account_params)

            if clear_outreach:
                plan_rows = conn.execute(f"SELECT id FROM outreach_plans WHERE {account_sql}", account_params).fetchall()
                plan_ids = [(str(row["id"]),) for row in plan_rows if str(row["id"] or "")]
                if plan_ids:
                    deleted["outreach_events"] = _delete_many(conn, "DELETE FROM outreach_events WHERE plan_id=?", plan_ids)
                    deleted["outreach_tasks"] = _delete_many(conn, "DELETE FROM outreach_tasks WHERE plan_id=?", plan_ids)
                    deleted["outreach_plans"] = _delete_many(conn, "DELETE FROM outreach_plans WHERE id=?", plan_ids)
                else:
                    deleted.update({"outreach_events": 0, "outreach_tasks": 0, "outreach_plans": 0})

            if clear_sop:
                deleted["sop_send_tasks"] = _execute_delete(
                    conn,
                    f"DELETE FROM sop_send_tasks WHERE {account_sql}",
                    account_params,
                )

            if clear_memory and sales_key:
                deleted["history_events"] = _execute_delete(conn, "DELETE FROM history_events WHERE customer_id=?", (sales_key,))
                deleted["customer_memory"] = _execute_delete(conn, "DELETE FROM customer_memory WHERE customer_id=?", (sales_key,))
            elif clear_memory:
                deleted.update({"history_events": 0, "customer_memory": 0})

        return {
            "status": "ok",
            "customer_id": customer,
            "wechat": account,
            "scope": scope,
            "deleted": deleted,
        }


def _account_match(customer: str, wechat: str, corp_id: str) -> tuple[str, tuple[Any, ...]]:
    clauses = ["(customer_id=? OR external_userid=?)", "wechat=?"]
    params: list[Any] = [customer, customer, wechat]
    if corp_id:
        clauses.append("corp_id=?")
        params.append(corp_id)
    return " AND ".join(clauses), tuple(params)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _count(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(scalar(row))


def _execute_delete(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    result = conn.execute(sql, params)
    return int(result.rowcount or 0)


def _delete_many(conn: Any, sql: str, params: list[tuple[Any, ...]]) -> int:
    result = conn.executemany(sql, params)
    return int(result.rowcount or 0)
