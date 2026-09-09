from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.services.storage.serialization import dumps, loads_dict, loads_list, utc_now_iso


_WORK_TYPE_STORE_FACT_FOLLOWUP = "store_fact_followup"
_TERMINAL_STATUSES = {"resolved", "dismissed"}


class InternalWorkItemRepositoryMixin:
    def upsert_store_fact_followup_work_item(
        self,
        *,
        followup: dict[str, Any] | None,
        request_id: str,
        conversation_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str,
        customer_id: str,
    ) -> dict[str, Any]:
        raw = followup if isinstance(followup, dict) else {}
        task = raw.get("internal_followup") if isinstance(raw.get("internal_followup"), dict) else {}
        if (
            str(task.get("task_type") or "").strip() != _WORK_TYPE_STORE_FACT_FOLLOWUP
            or str(task.get("status") or "").strip() != "pending"
        ):
            return {"status": "skipped", "reason": "no_pending_store_fact_followup"}

        store_id = str(task.get("store_id") or "").strip()
        detail_kind = str(task.get("detail_kind") or "").strip()
        missing_keys = sorted(
            {
                str(item or "").strip()
                for item in task.get("missing_fact_keys") or []
                if str(item or "").strip()
            }
        )
        clean_request_id = str(request_id or "").strip()
        if not store_id or not detail_kind or not missing_keys or not clean_request_id:
            return {"status": "skipped", "reason": "incomplete_store_fact_followup"}

        key_source = "\x1f".join((store_id, detail_kind, ",".join(missing_keys)))
        checksum = sha256(key_source.encode("utf-8")).hexdigest()
        idempotency_key = f"store_fact_followup:{checksum}"
        item_id = str(uuid5(NAMESPACE_URL, f"ai-paths:{idempotency_key}"))
        now = utc_now_iso()
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM internal_work_items WHERE idempotency_key=? LIMIT 1",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                payload = {
                    "schema_version": "internal_work_item_v1",
                    "source_request_ids": [clean_request_id],
                    "customer_send_authorized": False,
                }
                conn.execute(
                    """
                    INSERT INTO internal_work_items (
                        id, idempotency_key, work_type, status, store_id, detail_kind,
                        missing_fact_keys_json, source_request_id, conversation_id,
                        corp_id, wechat, external_userid, customer_id, occurrence_count,
                        first_seen_at, last_seen_at, resolved_at, resolved_by,
                        resolution_note, payload_json, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        item_id,
                        idempotency_key,
                        _WORK_TYPE_STORE_FACT_FOLLOWUP,
                        "pending",
                        store_id,
                        detail_kind,
                        dumps(missing_keys),
                        clean_request_id,
                        str(conversation_id or "").strip(),
                        str(corp_id or "").strip(),
                        str(wechat or "").strip(),
                        str(external_userid or "").strip(),
                        str(customer_id or "").strip(),
                        1,
                        now,
                        now,
                        "",
                        "",
                        "",
                        dumps(payload),
                        now,
                        now,
                    ),
                )
                created = True
                occurrence_count = 1
                status = "pending"
            else:
                current = dict(row)
                payload = loads_dict(current.get("payload_json"))
                seen = [str(item) for item in payload.get("source_request_ids") or [] if str(item)]
                is_new_occurrence = clean_request_id not in seen
                if is_new_occurrence:
                    seen.append(clean_request_id)
                    payload["source_request_ids"] = seen[-200:]
                occurrence_count = int(current.get("occurrence_count") or 0) + int(
                    is_new_occurrence
                )
                status = str(current.get("status") or "pending")
                should_reopen = is_new_occurrence and status == "resolved"
                if should_reopen:
                    status = "pending"
                conn.execute(
                    """
                    UPDATE internal_work_items
                    SET source_request_id=?, conversation_id=?, corp_id=?, wechat=?,
                        external_userid=?, customer_id=?, occurrence_count=?,
                        status=?, last_seen_at=?, resolved_at=?, resolved_by=?,
                        resolution_note=?, payload_json=?, updated_at=?
                    WHERE idempotency_key=?
                    """,
                    (
                        clean_request_id,
                        str(conversation_id or "").strip(),
                        str(corp_id or "").strip(),
                        str(wechat or "").strip(),
                        str(external_userid or "").strip(),
                        str(customer_id or "").strip(),
                        occurrence_count,
                        status,
                        now,
                        "" if should_reopen else str(current.get("resolved_at") or ""),
                        "" if should_reopen else str(current.get("resolved_by") or ""),
                        "" if should_reopen else str(current.get("resolution_note") or ""),
                        dumps(payload),
                        now,
                        idempotency_key,
                    ),
                )
                created = False
        return {
            "status": status,
            "id": item_id,
            "created": created,
            "occurrence_count": occurrence_count,
        }

    def list_internal_work_items(
        self,
        *,
        work_type: str = "",
        status: str = "",
        store_id: str = "",
        detail_kind: str = "",
        started_from: str = "",
        started_to: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        clean_status = str(status or "").strip()
        if clean_status and clean_status not in {"pending", *_TERMINAL_STATUSES}:
            raise ValueError("status must be pending, resolved or dismissed")
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("work_type", work_type),
            ("status", clean_status),
            ("store_id", store_id),
            ("detail_kind", detail_kind),
        ):
            clean = str(value or "").strip()
            if clean:
                clauses.append(f"{column}=?")
                params.append(clean)
        if str(started_from or "").strip():
            clauses.append("last_seen_at>=?")
            params.append(str(started_from).strip())
        if str(started_to or "").strip():
            clauses.append("last_seen_at<=?")
            params.append(str(started_to).strip())
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        safe_limit = max(1, min(int(limit or 100), 500))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM internal_work_items
                {where}
                ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,
                         last_seen_at DESC, id DESC
                LIMIT ?
                """,
                (*params, safe_limit),
            ).fetchall()
        items = [_decode_work_item(dict(row)) for row in rows]
        return {"total": len(items), "items": items}

    def update_internal_work_item(
        self,
        *,
        item_id: str,
        status: str,
        resolved_by: str = "",
        resolution_note: str = "",
    ) -> dict[str, Any]:
        clean_id = str(item_id or "").strip()
        clean_status = str(status or "").strip()
        if not clean_id:
            raise ValueError("item_id is required")
        if clean_status not in _TERMINAL_STATUSES:
            raise ValueError("status must be resolved or dismissed")
        now = utc_now_iso()
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM internal_work_items WHERE id=? LIMIT 1",
                (clean_id,),
            ).fetchone()
            if row is None:
                return {"status": "not_found", "item": {}}
            current = dict(row)
            current_status = str(current.get("status") or "pending")
            if current_status not in {"pending", clean_status}:
                raise ValueError(
                    f"work item is already {current_status}; it cannot become {clean_status}"
                )
            conn.execute(
                """
                UPDATE internal_work_items
                SET status=?, resolved_at=?, resolved_by=?, resolution_note=?, updated_at=?
                WHERE id=?
                """,
                (
                    clean_status,
                    now if current_status == "pending" else str(current.get("resolved_at") or now),
                    str(resolved_by or "").strip()[:191],
                    str(resolution_note or "").strip()[:2000],
                    now,
                    clean_id,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM internal_work_items WHERE id=? LIMIT 1",
                (clean_id,),
            ).fetchone()
        return {
            "status": "updated" if current_status == "pending" else "unchanged",
            "item": _decode_work_item(dict(updated)) if updated is not None else {},
        }


def _decode_work_item(row: dict[str, Any]) -> dict[str, Any]:
    row["missing_fact_keys"] = loads_list(row.pop("missing_fact_keys_json", "[]"))
    payload = loads_dict(row.pop("payload_json", "{}"))
    # Source request history is an internal idempotency aid; the admin response
    # exposes only the latest request and aggregate count.
    payload.pop("source_request_ids", None)
    row["payload"] = payload
    row["occurrence_count"] = int(row.get("occurrence_count") or 0)
    return row
