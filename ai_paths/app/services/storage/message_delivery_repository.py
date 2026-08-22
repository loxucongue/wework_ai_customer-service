from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from app.services.storage.serialization import dumps, loads_dict, loads_list, utc_now_iso


_SUCCESS = "send_succeeded"
_FAILED = "send_failed"
_TERMINAL = {_SUCCESS, _FAILED, "partial_failed"}


class MessageDeliveryRepositoryMixin:
    def create_message_dispatch(
        self,
        *,
        dispatch_id: str,
        idempotency_key: str,
        source_channel: str,
        source_kind: str,
        source_request_id: str,
        source_task_id: str,
        conversation_id: str,
        identity: dict[str, Any],
        plan_id: str,
        task_id: str,
        reply_messages: list[dict[str, Any]],
        source_context: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self.store.connect() as conn:
            result = conn.execute(
                """
                INSERT OR IGNORE INTO message_dispatches
                    (id, idempotency_key, source_channel, source_kind, source_request_id,
                     source_task_id, conversation_id, corp_id, customer_id, external_userid,
                     user_id, wechat, plan_id, task_id, reply_messages_json,
                     source_context_json, status, expected_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)
                """,
                (
                    dispatch_id,
                    idempotency_key,
                    source_channel,
                    source_kind,
                    source_request_id,
                    source_task_id,
                    conversation_id,
                    str(identity.get("corp_id") or ""),
                    str(identity.get("customer_id") or ""),
                    str(identity.get("external_userid") or ""),
                    str(identity.get("user_id") or ""),
                    str(identity.get("wechat") or ""),
                    plan_id,
                    task_id,
                    dumps(reply_messages),
                    dumps(source_context),
                    len(reply_messages),
                    now,
                    now,
                ),
            )
            created = bool(result.rowcount)
            row = conn.execute(
                "SELECT * FROM message_dispatches WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("message dispatch was not created")
            stored_id = str(row["id"] or "")
            if created:
                for index, message in enumerate(reply_messages):
                    client_message_id = _client_message_id(stored_id, index)
                    conn.execute(
                        """
                        INSERT INTO message_dispatch_items
                            (id, dispatch_id, client_message_id, message_index, message_type,
                             payload_hash, status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'created', ?, ?)
                        """,
                        (
                            str(uuid4()),
                            stored_id,
                            client_message_id,
                            index,
                            str(message.get("type") or "") if isinstance(message, dict) else "",
                            _payload_hash(message),
                            now,
                            now,
                        ),
                    )
        dispatch = self.get_message_dispatch(stored_id)
        dispatch["created"] = created
        return dispatch

    def get_message_dispatch(self, dispatch_id: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM message_dispatches WHERE id=?",
                (str(dispatch_id or ""),),
            ).fetchone()
            items = conn.execute(
                """
                SELECT * FROM message_dispatch_items
                WHERE dispatch_id=? ORDER BY message_index ASC
                """,
                (str(dispatch_id or ""),),
            ).fetchall()
        return _decode_dispatch(row, items)

    def update_message_dispatch_submission(
        self,
        dispatch_id: str,
        *,
        status: str,
        platform_request_id: str = "",
        system_msgid: str = "",
        error_code: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        accepted_at = now if status == "platform_accepted" else ""
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT status FROM message_dispatches WHERE id=?",
                (dispatch_id,),
            ).fetchone()
            if row is None:
                return {}
            current = str(row["status"] or "")
            if current not in _TERMINAL:
                conn.execute(
                    """
                    UPDATE message_dispatches
                    SET status=?, platform_request_id=COALESCE(NULLIF(?, ''), platform_request_id),
                        system_msgid=COALESCE(NULLIF(?, ''), system_msgid),
                        error_code=?, error_message=?, submitted_at=COALESCE(NULLIF(submitted_at, ''), ?),
                        accepted_at=COALESCE(NULLIF(accepted_at, ''), ?), updated_at=?
                    WHERE id=?
                    """,
                    (
                        status,
                        platform_request_id,
                        system_msgid,
                        error_code,
                        error_message,
                        now,
                        accepted_at,
                        now,
                        dispatch_id,
                    ),
                )
        return self.get_message_dispatch(dispatch_id)

    def apply_message_delivery_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or "").strip()
        dispatch_id = str(payload.get("dispatch_id") or "").strip()
        callback_status = str(payload.get("status") or "").strip()
        now = utc_now_iso()
        with self.store.connect() as conn:
            dispatch_row = conn.execute(
                "SELECT * FROM message_dispatches WHERE id=?",
                (dispatch_id,),
            ).fetchone()
            if dispatch_row is None:
                return {"found": False, "duplicate": False, "dispatch_id": dispatch_id}
            existing_event = conn.execute(
                "SELECT event_id FROM message_delivery_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            duplicate = existing_event is not None
            if not duplicate:
                conn.execute(
                    """
                    INSERT INTO message_delivery_events
                        (event_id, dispatch_id, status, raw_payload_json, occurred_at, received_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        dispatch_id,
                        callback_status,
                        dumps(payload),
                        str(payload.get("occurred_at") or ""),
                        now,
                    ),
                )
                self._apply_delivery_items(conn, dispatch_id=dispatch_id, payload=payload, now=now)
                summary = _delivery_summary(conn, dispatch_id)
                aggregate = _aggregate_delivery_status(
                    expected=int(dispatch_row["expected_count"] or 0),
                    succeeded=summary["succeeded"],
                    failed=summary["failed"],
                    callback_status=callback_status,
                )
                current = str(dispatch_row["status"] or "")
                if current in _TERMINAL and aggregate != current:
                    raise ValueError(f"terminal delivery status conflict: {current} -> {aggregate}")
                conn.execute(
                    """
                    UPDATE message_dispatches
                    SET status=?, succeeded_count=?, failed_count=?,
                        platform_request_id=COALESCE(NULLIF(?, ''), platform_request_id),
                        system_msgid=COALESCE(NULLIF(?, ''), system_msgid),
                        error_code=?, error_message=?, last_callback_at=?,
                        confirmed_at=CASE WHEN ? IN ('send_succeeded','send_failed','partial_failed')
                                          THEN COALESCE(NULLIF(confirmed_at, ''), ?) ELSE confirmed_at END,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        aggregate,
                        summary["succeeded"],
                        summary["failed"],
                        str(payload.get("platform_request_id") or ""),
                        str(payload.get("system_msgid") or ""),
                        str(payload.get("error_code") or ""),
                        str(payload.get("error_message") or ""),
                        now,
                        aggregate,
                        now,
                        now,
                        dispatch_id,
                    ),
                )
        dispatch = self.get_message_dispatch(dispatch_id)
        return {"found": True, "duplicate": duplicate, "dispatch": dispatch}

    def _apply_delivery_items(self, conn: Any, *, dispatch_id: str, payload: dict[str, Any], now: str) -> None:
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        if not items:
            status = str(payload.get("status") or "")
            if status in {_SUCCESS, _FAILED}:
                conn.execute(
                    """
                    UPDATE message_dispatch_items
                    SET status=?, error_code=?, error_message=?, sent_at=?, updated_at=?
                    WHERE dispatch_id=?
                    """,
                    (
                        status,
                        str(payload.get("error_code") or ""),
                        str(payload.get("error_message") or ""),
                        str(payload.get("occurred_at") or "") if status == _SUCCESS else "",
                        now,
                        dispatch_id,
                    ),
                )
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            client_message_id = str(item.get("client_message_id") or "").strip()
            if not client_message_id:
                raise ValueError("delivery item client_message_id is required")
            row = conn.execute(
                """
                SELECT id, status FROM message_dispatch_items
                WHERE dispatch_id=? AND client_message_id=?
                """,
                (dispatch_id, client_message_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown client_message_id: {client_message_id}")
            status = str(item.get("status") or "").strip()
            current = str(row["status"] or "")
            if current in {_SUCCESS, _FAILED} and status != current:
                raise ValueError(f"terminal item status conflict: {client_message_id}")
            conn.execute(
                """
                UPDATE message_dispatch_items
                SET status=?, platform_message_id=COALESCE(NULLIF(?, ''), platform_message_id),
                    error_code=?, error_message=?, sent_at=COALESCE(NULLIF(?, ''), sent_at), updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    str(item.get("platform_message_id") or ""),
                    str(item.get("error_code") or ""),
                    str(item.get("error_message") or ""),
                    str(item.get("sent_at") or ""),
                    now,
                    str(row["id"] or ""),
                ),
            )

    def mark_message_dispatch_finalized(self, dispatch_id: str) -> dict[str, Any]:
        now = utc_now_iso()
        with self.store.connect() as conn:
            conn.execute(
                """
                UPDATE message_dispatches SET finalized_at=COALESCE(NULLIF(finalized_at, ''), ?), updated_at=?
                WHERE id=?
                """,
                (now, now, dispatch_id),
            )
        return self.get_message_dispatch(dispatch_id)


def _client_message_id(dispatch_id: str, index: int) -> str:
    return f"{dispatch_id}:{index + 1}"


def _payload_hash(message: Any) -> str:
    return hashlib.sha256(dumps(message if isinstance(message, dict) else {}).encode("utf-8")).hexdigest()


def _decode_dispatch(row: Any, items: Any) -> dict[str, Any]:
    if row is None:
        return {}
    value = dict(row)
    value["reply_messages"] = loads_list(value.pop("reply_messages_json", "[]"))
    value["source_context"] = loads_dict(value.pop("source_context_json", "{}"))
    value["items"] = [dict(item) for item in items or []]
    return value


def _delivery_summary(conn: Any, dispatch_id: str) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS count FROM message_dispatch_items
        WHERE dispatch_id=? GROUP BY status
        """,
        (dispatch_id,),
    ).fetchall()
    counts = {str(row["status"] or ""): int(row["count"] or 0) for row in rows}
    return {"succeeded": counts.get(_SUCCESS, 0), "failed": counts.get(_FAILED, 0)}


def _aggregate_delivery_status(*, expected: int, succeeded: int, failed: int, callback_status: str) -> str:
    if expected > 0 and succeeded == expected:
        return _SUCCESS
    if expected > 0 and failed == expected:
        return _FAILED
    if succeeded and failed:
        return "partial_failed"
    if callback_status in {_SUCCESS, _FAILED, "partial_failed", "sending"}:
        return callback_status
    return "platform_accepted"
