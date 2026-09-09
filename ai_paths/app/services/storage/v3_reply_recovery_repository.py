from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.services.storage.serialization import dumps, loads_dict, loads_list, utc_now_iso
from app.services.v3_reply_recovery import (
    GENERATION_STATUS_FALLBACK_PENDING,
    GENERATION_STATUS_MANUAL_REVIEW,
    GENERATION_STATUS_RECOVERED,
    GENERATION_STATUS_RECOVERY_CLAIMED,
    GENERATION_STATUS_RECOVERY_FAILED,
    decode_v3_recovery_payload,
    encode_v3_recovery_payload,
    rebuildable_chat_request_snapshot,
    stable_v3_reply_messages,
)


_EMPTY_REPLY_SOURCES = {
    "human_takeover_guard",
    "ignored_platform_auto_message",
    "platform_recalled_message",
    "platform_superseded",
    "platform_filtered",
}


class V3ReplyRecoveryRepositoryMixin:
    def has_newer_customer_message_for_v3_recovery(self, request_id: str) -> bool:
        return bool(self.newer_customer_message_for_v3_recovery(request_id).get("has_newer"))

    def newer_customer_message_for_v3_recovery(self, request_id: str) -> dict[str, Any]:
        """Read the strict sales-contact boundary before a recovery send.

        Missing boundary data blocks recovery conservatively.  A message on the
        same external contact but another receiving WeChat is intentionally not
        considered a reply to this run.
        """

        clean_request_id = str(request_id or "").strip()
        if not clean_request_id:
            return {"has_newer": True, "latest_at": "", "reason": "missing_request_id"}
        with self.store.connect() as conn:
            run = conn.execute(
                """
                SELECT r.created_at, c.corp_id, c.wechat, c.external_userid
                FROM runs r
                JOIN conversations c ON c.id=r.conversation_id
                WHERE r.request_id=? LIMIT 1
                """,
                (clean_request_id,),
            ).fetchone()
            if run is None:
                return {"has_newer": True, "latest_at": "", "reason": "run_not_found"}
            corp_id = str(run["corp_id"] or "").strip()
            wechat = str(run["wechat"] or "").strip()
            external_userid = str(run["external_userid"] or "").strip()
            if not corp_id or not wechat or not external_userid:
                return {"has_newer": True, "latest_at": "", "reason": "incomplete_identity_boundary"}
            row = conn.execute(
                """
                SELECT MAX(m.created_at) AS latest_at
                FROM messages m
                JOIN conversations c ON c.id=m.conversation_id
                WHERE c.corp_id=? AND LOWER(c.wechat)=LOWER(?) AND c.external_userid=?
                  AND m.role='user' AND m.request_id<>? AND m.created_at>?
                """,
                (
                    corp_id,
                    wechat,
                    external_userid,
                    clean_request_id,
                    str(run["created_at"] or ""),
                ),
            ).fetchone()
        latest_at = str(row["latest_at"] or "") if row is not None else ""
        return {"has_newer": bool(latest_at), "latest_at": latest_at, "reason": ""}

    def get_v3_generation_result(
        self,
        *,
        generation_key: str = "",
        response_id: str = "",
        request_id: str = "",
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if str(generation_key or "").strip():
            clauses.append("generation_key=?")
            params.append(str(generation_key).strip())
        elif str(response_id or "").strip():
            clauses.append("response_id=?")
            params.append(str(response_id).strip())
        elif str(request_id or "").strip():
            clauses.append("request_id=?")
            params.append(str(request_id).strip())
        else:
            return {"found": False, "ready": False, "replayed": False}
        with self.store.connect() as conn:
            row = conn.execute(
                f"SELECT * FROM runs WHERE {clauses[0]} LIMIT 1",
                tuple(params),
            ).fetchone()
        return _generation_result(dict(row)) if row is not None else {
            "found": False,
            "ready": False,
            "replayed": False,
        }

    def load_v3_generation_response(self, generation_key: str) -> dict[str, Any]:
        result = self.get_v3_generation_result(generation_key=generation_key)
        response = result.get("response") if isinstance(result.get("response"), dict) else {}
        return response if result.get("ready") else {}

    def schedule_v3_fallback_recovery(
        self,
        *,
        request_id: str,
        recovery_kind: str,
        next_retry_at: str,
        error: str = "",
        recovery_payload: dict[str, Any] | None = None,
        response_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_request_id = str(request_id or "").strip()
        clean_next_retry_at = str(next_retry_at or "").strip()
        if not clean_request_id or not clean_next_retry_at:
            raise ValueError("request_id and next_retry_at are required")
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT output_snapshot, generation_key FROM runs WHERE request_id=? LIMIT 1",
                (clean_request_id,),
            ).fetchone()
            if row is None or not str(row["generation_key"] or ""):
                return {"scheduled": False, "reason": "generation_not_found"}
            output = loads_dict(row["output_snapshot"])
            if isinstance(recovery_payload, dict) and recovery_payload:
                output["v3_recovery_payload"] = recovery_payload
            if isinstance(response_snapshot, dict) and response_snapshot:
                output["v3_response_snapshot"] = encode_v3_recovery_payload(response_snapshot)
            updated = conn.execute(
                """
                UPDATE runs
                SET generation_status=?, recovery_kind=?, recovery_next_at=?,
                    recovery_error=?, output_snapshot=?
                WHERE request_id=? AND generation_status NOT IN (?, ?)
                """,
                (
                    GENERATION_STATUS_FALLBACK_PENDING,
                    str(recovery_kind or "runtime_fallback")[:64],
                    clean_next_retry_at,
                    str(error or "")[:4000],
                    dumps(output),
                    clean_request_id,
                    GENERATION_STATUS_RECOVERED,
                    GENERATION_STATUS_RECOVERY_FAILED,
                ),
            )
        return {
            "scheduled": bool(updated.rowcount),
            "request_id": clean_request_id,
            "status": GENERATION_STATUS_FALLBACK_PENDING if int(updated.rowcount or 0) else "",
        }

    def claim_v3_fallback_recoveries(
        self,
        *,
        limit: int = 10,
        max_attempts: int = 2,
        lease_seconds: int = 120,
        now: str = "",
    ) -> list[dict[str, Any]]:
        now_value = str(now or utc_now_iso())
        parsed_now = _parse_datetime(now_value) or datetime.now(timezone.utc)
        lease_until = (parsed_now + timedelta(seconds=max(30, int(lease_seconds)))).isoformat()
        claimed: list[dict[str, Any]] = []
        with self.store.connect() as conn:
            # A claimed row is a lease, not a permanent state.  If a worker
            # dies before it creates a dispatch, another worker may reclaim it
            # until the attempt budget is exhausted.  Once a dispatch exists,
            # however, its submission may already have reached the platform;
            # expiry must escalate for reconciliation rather than blind-send.
            expired = conn.execute(
                """
                SELECT request_id, recovery_attempts, recovery_dispatch_id
                FROM runs
                WHERE generation_status=?
                  AND recovery_next_at<>'' AND recovery_next_at<=?
                """,
                (GENERATION_STATUS_RECOVERY_CLAIMED, now_value),
            ).fetchall()
            for raw in expired:
                row = dict(raw)
                attempts = int(row["recovery_attempts"] or 0)
                dispatch_id = str(row["recovery_dispatch_id"] or "").strip()
                if not dispatch_id and attempts < max(1, int(max_attempts)):
                    continue
                reason = (
                    "recovery_delivery_confirmation_timeout"
                    if dispatch_id
                    else "recovery_claim_lease_exhausted"
                )
                updated = conn.execute(
                    """
                    UPDATE runs
                    SET generation_status=?, recovery_kind='manual_review',
                        recovery_next_at='', recovery_error=?
                    WHERE request_id=? AND generation_status=?
                      AND recovery_attempts=? AND recovery_next_at<=?
                    """,
                    (
                        GENERATION_STATUS_MANUAL_REVIEW,
                        reason,
                        str(row["request_id"] or ""),
                        GENERATION_STATUS_RECOVERY_CLAIMED,
                        attempts,
                        now_value,
                    ),
                )
                if int(updated.rowcount or 0):
                    _clear_recovery_payload(conn, str(row["request_id"] or ""))

            rows = conn.execute(
                """
                SELECT request_id, generation_status, recovery_attempts, recovery_next_at
                FROM runs
                WHERE (generation_status=? OR (generation_status=? AND recovery_dispatch_id=''))
                  AND recovery_attempts<?
                  AND (recovery_next_at='' OR recovery_next_at<=?)
                ORDER BY recovery_next_at ASC, created_at ASC
                LIMIT ?
                """,
                (
                    GENERATION_STATUS_FALLBACK_PENDING,
                    GENERATION_STATUS_RECOVERY_CLAIMED,
                    max(1, int(max_attempts)),
                    now_value,
                    max(1, min(int(limit or 10), 100)),
                ),
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                updated = conn.execute(
                    """
                    UPDATE runs
                    SET generation_status=?, recovery_attempts=recovery_attempts+1,
                        recovery_next_at=?
                    WHERE request_id=? AND generation_status=? AND recovery_attempts=?
                      AND recovery_next_at=? AND recovery_dispatch_id=''
                    """,
                    (
                        GENERATION_STATUS_RECOVERY_CLAIMED,
                        lease_until,
                        str(row["request_id"] or ""),
                        str(row["generation_status"] or ""),
                        int(row["recovery_attempts"] or 0),
                        str(row["recovery_next_at"] or ""),
                    ),
                )
                if not int(updated.rowcount or 0):
                    continue
                stored = conn.execute(
                    "SELECT * FROM runs WHERE request_id=? LIMIT 1",
                    (str(row["request_id"] or ""),),
                ).fetchone()
                if stored is not None:
                    claimed.append(_generation_result(dict(stored), include_recovery_payload=True))
        return claimed

    def retry_v3_fallback_recovery(
        self,
        *,
        request_id: str,
        next_retry_at: str,
        error: str,
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        clean_request_id = str(request_id or "").strip()
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT recovery_attempts FROM runs WHERE request_id=? AND generation_status=?",
                (clean_request_id, GENERATION_STATUS_RECOVERY_CLAIMED),
            ).fetchone()
            if row is None:
                return {"updated": 0, "status": "not_claimed"}
            exhausted = int(row["recovery_attempts"] or 0) >= max(1, int(max_attempts))
            status = GENERATION_STATUS_MANUAL_REVIEW if exhausted else GENERATION_STATUS_FALLBACK_PENDING
            conn.execute(
                """
                UPDATE runs SET generation_status=?, recovery_next_at=?, recovery_error=?
                WHERE request_id=? AND generation_status=?
                """,
                (
                    status,
                    "" if exhausted else str(next_retry_at or ""),
                    str(error or "")[:4000],
                    clean_request_id,
                    GENERATION_STATUS_RECOVERY_CLAIMED,
                ),
            )
            if exhausted:
                conn.execute(
                    "UPDATE runs SET recovery_kind='manual_review' WHERE request_id=?",
                    (clean_request_id,),
                )
                _clear_recovery_payload(conn, clean_request_id)
        return {"updated": 1, "status": status, "exhausted": exhausted}

    def stage_v3_fallback_recovery_delivery(
        self,
        *,
        request_id: str,
        reply_messages: list[dict[str, Any]],
        dispatch_id: str,
        response_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist the generated answer while waiting for a delivery receipt.

        Platform acceptance is not delivery.  This method deliberately keeps
        the run in ``recovery_claimed``; only a ``send_succeeded`` callback may
        transition it to ``recovered``.
        """

        clean_request_id = str(request_id or "").strip()
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT generation_status, response_id, recovery_kind,
                       recovery_dispatch_id, output_snapshot
                FROM runs WHERE request_id=? AND generation_status IN (?, ?, ?)
                LIMIT 1
                """,
                (
                    clean_request_id,
                    GENERATION_STATUS_RECOVERY_CLAIMED,
                    GENERATION_STATUS_FALLBACK_PENDING,
                    GENERATION_STATUS_RECOVERED,
                ),
            ).fetchone()
            if row is None:
                return {"updated": 0, "status": "not_recoverable"}
            current_status = str(row["generation_status"] or "")
            current_dispatch_id = str(row["recovery_dispatch_id"] or "").strip()
            clean_dispatch_id = str(dispatch_id or "").strip()
            if not clean_dispatch_id:
                return {"updated": 0, "status": "missing_dispatch_id"}
            if current_dispatch_id and current_dispatch_id != clean_dispatch_id:
                return {"updated": 0, "status": "dispatch_mismatch"}
            if current_status == GENERATION_STATUS_RECOVERED:
                return {
                    "updated": 0,
                    "status": GENERATION_STATUS_RECOVERED,
                    "duplicate": True,
                }
            recovery_kind = str(row["recovery_kind"] or "recovery")
            stable_messages = stable_v3_reply_messages(
                reply_messages,
                response_id=str(row["response_id"] or ""),
                recovery_kind=recovery_kind,
            )
            output = loads_dict(row["output_snapshot"])
            output["recovery_reply_messages"] = stable_messages
            output["runtime_status"] = "processing"
            output["runtime_phase"] = "recovery_delivery_pending"
            output["runtime_updated_at"] = utc_now_iso()
            snapshot = dict(response_snapshot) if isinstance(response_snapshot, dict) else {}
            if snapshot:
                snapshot["reply_messages"] = stable_messages
                snapshot["response_id"] = str(row["response_id"] or "")
                snapshot["replayed"] = False
                # Recovery is an out-of-band delivery with its own idempotency
                # key. Keep the original HTTP snapshot immutable so a later
                # retry of the platform msgid cannot send the recovery twice.
                output["v3_recovery_response_snapshot"] = encode_v3_recovery_payload(snapshot)
            updated = conn.execute(
                """
                UPDATE runs
                SET generation_status=?, recovery_dispatch_id=?,
                    recovery_error='awaiting_send_succeeded_callback', output_snapshot=?
                WHERE request_id=? AND generation_status IN (?, ?)
                  AND (recovery_dispatch_id='' OR recovery_dispatch_id=?)
                """,
                (
                    GENERATION_STATUS_RECOVERY_CLAIMED,
                    clean_dispatch_id,
                    dumps(output),
                    clean_request_id,
                    GENERATION_STATUS_RECOVERY_CLAIMED,
                    GENERATION_STATUS_FALLBACK_PENDING,
                    clean_dispatch_id,
                ),
            )
            if not int(updated.rowcount or 0):
                return {"updated": 0, "status": "not_recoverable"}
        return {
            "updated": int(updated.rowcount or 0),
            "status": "delivery_pending",
            "reply_messages": stable_messages,
        }

    def complete_v3_fallback_recovery(
        self,
        *,
        request_id: str,
        reply_messages: list[dict[str, Any]],
        dispatch_id: str,
        response_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compatibility entry point; acceptance now only stages delivery."""

        return self.stage_v3_fallback_recovery_delivery(
            request_id=request_id,
            reply_messages=reply_messages,
            dispatch_id=dispatch_id,
            response_snapshot=response_snapshot,
        )

    def finalize_v3_recovery_delivery(
        self,
        *,
        request_id: str,
        dispatch_id: str,
        delivery_status: str,
        error: str = "",
    ) -> dict[str, Any]:
        """CAS the terminal receipt onto the recovery run that created it.

        A callback can race the worker before it stages the accepted dispatch.
        The durable dispatch itself is therefore authoritative evidence for
        the generated messages.  Only ``send_succeeded`` reaches recovered.
        """

        clean_request_id = str(request_id or "").strip()
        clean_dispatch_id = str(dispatch_id or "").strip()
        clean_status = str(delivery_status or "").strip().lower()
        if not clean_request_id or not clean_dispatch_id:
            raise ValueError("request_id and dispatch_id are required")
        if clean_status not in {"send_succeeded", "send_failed", "partial_failed"}:
            raise ValueError(f"unsupported recovery delivery status: {clean_status or '<empty>'}")

        active_states = {
            GENERATION_STATUS_FALLBACK_PENDING,
            GENERATION_STATUS_RECOVERY_CLAIMED,
            GENERATION_STATUS_RECOVERED,
        }
        allowed_states = active_states | {GENERATION_STATUS_MANUAL_REVIEW}
        now = utc_now_iso()
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT generation_status, recovery_dispatch_id, recovery_error,
                       response_id, recovery_kind, conversation_id, output_snapshot
                FROM runs WHERE request_id=? LIMIT 1
                """,
                (clean_request_id,),
            ).fetchone()
            if row is None:
                return {"found": False, "updated": 0, "status": "not_found"}

            current_status = str(row["generation_status"] or "")
            current_dispatch_id = str(row["recovery_dispatch_id"] or "").strip()
            if current_dispatch_id and current_dispatch_id != clean_dispatch_id:
                return {
                    "found": True,
                    "updated": 0,
                    "status": "dispatch_mismatch",
                    "reason": "recovery dispatch does not match the run",
                }
            if current_status not in allowed_states:
                return {
                    "found": True,
                    "updated": 0,
                    "status": "state_conflict",
                    "reason": f"recovery run is not finalizable from {current_status or '<empty>'}",
                }

            output = loads_dict(row["output_snapshot"])
            output["v3_recovery_delivery"] = {
                "dispatch_id": clean_dispatch_id,
                "status": clean_status,
                "error": str(error or "")[:2000],
                "finalized_at": now,
            }
            if clean_status == "send_succeeded":
                stable_messages = _recovery_messages_for_dispatch(
                    conn,
                    output=output,
                    dispatch_id=clean_dispatch_id,
                    response_id=str(row["response_id"] or ""),
                    recovery_kind=str(row["recovery_kind"] or "recovery"),
                )
                if not stable_messages:
                    return {
                        "found": True,
                        "updated": 0,
                        "status": "state_conflict",
                        "reason": "recovery delivery has no persisted reply messages",
                    }
                output["recovery_reply_messages"] = stable_messages
                output["runtime_status"] = "completed"
                output["runtime_phase"] = "recovery_completed"
                output["runtime_updated_at"] = now
                output.pop("v3_recovery_payload", None)
                updated = conn.execute(
                    """
                    UPDATE runs
                    SET generation_status=?, recovery_next_at='',
                        recovery_dispatch_id=COALESCE(NULLIF(recovery_dispatch_id, ''), ?),
                        recovery_error='', output_snapshot=?
                    WHERE request_id=? AND generation_status=?
                      AND (recovery_dispatch_id='' OR recovery_dispatch_id=?)
                    """,
                    (
                        GENERATION_STATUS_RECOVERED,
                        clean_dispatch_id,
                        dumps(output),
                        clean_request_id,
                        current_status,
                        clean_dispatch_id,
                    ),
                )
                status = GENERATION_STATUS_RECOVERED
                if int(updated.rowcount or 0):
                    _insert_recovered_assistant_message(
                        conn,
                        request_id=clean_request_id,
                        conversation_id=str(row["conversation_id"] or ""),
                        dispatch_id=clean_dispatch_id,
                        reply_messages=stable_messages,
                        created_at=now,
                    )
            elif current_status in active_states:
                output.pop("v3_recovery_payload", None)
                reason = f"recovery_dispatch_{clean_status}"
                if str(error or "").strip():
                    reason += ":" + str(error).strip()
                updated = conn.execute(
                    """
                    UPDATE runs
                    SET generation_status=?, recovery_kind='manual_review',
                        recovery_next_at='', recovery_dispatch_id=?, recovery_error=?,
                        output_snapshot=?
                    WHERE request_id=? AND generation_status=?
                      AND (recovery_dispatch_id='' OR recovery_dispatch_id=?)
                    """,
                    (
                        GENERATION_STATUS_MANUAL_REVIEW,
                        clean_dispatch_id,
                        reason[:4000],
                        dumps(output),
                        clean_request_id,
                        current_status,
                        clean_dispatch_id,
                    ),
                )
                status = GENERATION_STATUS_MANUAL_REVIEW
            else:
                # A repeated terminal-failure callback for an already escalated
                # run is a successful idempotent reconciliation.
                updated = conn.execute(
                    """
                    UPDATE runs
                    SET recovery_dispatch_id=COALESCE(NULLIF(recovery_dispatch_id, ''), ?),
                        output_snapshot=?
                    WHERE request_id=? AND generation_status=?
                      AND (recovery_dispatch_id='' OR recovery_dispatch_id=?)
                    """,
                    (
                        clean_dispatch_id,
                        dumps(output),
                        clean_request_id,
                        GENERATION_STATUS_MANUAL_REVIEW,
                        clean_dispatch_id,
                    ),
                )
                status = GENERATION_STATUS_MANUAL_REVIEW

            if not int(updated.rowcount or 0):
                latest = conn.execute(
                    """
                    SELECT generation_status, recovery_dispatch_id
                    FROM runs WHERE request_id=? LIMIT 1
                    """,
                    (clean_request_id,),
                ).fetchone()
                latest_status = str(latest["generation_status"] or "") if latest else ""
                latest_dispatch_id = str(latest["recovery_dispatch_id"] or "") if latest else ""
                if latest_status == status and latest_dispatch_id == clean_dispatch_id:
                    return {
                        "found": True,
                        "updated": 0,
                        "status": status,
                        "duplicate": True,
                    }
                return {
                    "found": True,
                    "updated": 0,
                    "status": "state_conflict",
                    "reason": "recovery run changed during delivery finalization",
                }
        return {
            "found": True,
            "updated": int(updated.rowcount or 0),
            "status": status,
            "duplicate": False,
        }

    def cancel_v3_fallback_recovery(self, *, request_id: str, reason: str) -> dict[str, Any]:
        clean_request_id = str(request_id or "").strip()
        with self.store.connect() as conn:
            updated = conn.execute(
                """
                UPDATE runs SET generation_status=?, recovery_next_at='', recovery_error=?
                WHERE request_id=? AND generation_status IN (?, ?)
                """,
                (
                    GENERATION_STATUS_RECOVERY_FAILED,
                    str(reason or "cancelled")[:4000],
                    clean_request_id,
                    GENERATION_STATUS_FALLBACK_PENDING,
                    GENERATION_STATUS_RECOVERY_CLAIMED,
                ),
            )
            if int(updated.rowcount or 0):
                _clear_recovery_payload(conn, clean_request_id)
        return {"updated": int(updated.rowcount or 0), "status": GENERATION_STATUS_RECOVERY_FAILED}

    def mark_v3_recovery_manual_review(self, *, request_id: str, reason: str) -> dict[str, Any]:
        clean_request_id = str(request_id or "").strip()
        if not clean_request_id:
            raise ValueError("request_id is required")
        with self.store.connect() as conn:
            updated = conn.execute(
                """
                UPDATE runs
                SET generation_status=?, recovery_kind='manual_review',
                    recovery_next_at='', recovery_error=?
                WHERE request_id=? AND generation_status IN (?, ?)
                """,
                (
                    GENERATION_STATUS_MANUAL_REVIEW,
                    str(reason or "recovery_exhausted")[:4000],
                    clean_request_id,
                    GENERATION_STATUS_FALLBACK_PENDING,
                    GENERATION_STATUS_RECOVERY_CLAIMED,
                ),
            )
            if int(updated.rowcount or 0):
                _clear_recovery_payload(conn, clean_request_id)
        return {"updated": int(updated.rowcount or 0), "status": GENERATION_STATUS_MANUAL_REVIEW}


def _generation_result(
    row: dict[str, Any],
    *,
    include_recovery_payload: bool = False,
) -> dict[str, Any]:
    output = loads_dict(row.get("output_snapshot"))
    input_snapshot = loads_dict(row.get("input_snapshot"))
    stored_response = decode_v3_recovery_payload(output.get("v3_response_snapshot"))
    response_snapshot = (
        dict(stored_response.get("chat_response"))
        if isinstance(stored_response.get("chat_response"), dict)
        else dict(stored_response)
    )
    http_response = (
        dict(stored_response.get("http_response"))
        if isinstance(stored_response.get("http_response"), dict)
        else {}
    )
    response_id = str(row.get("response_id") or "")
    status = str(row.get("generation_status") or "")
    # The primary HTTP result and the later recovery dispatch are separate
    # channels. Never replay recovery_reply_messages for the original msgid:
    # the worker has already sent them through message_dispatches.
    messages = (
        response_snapshot.get("reply_messages")
        if isinstance(response_snapshot.get("reply_messages"), list)
        else output.get("reply_messages")
        if isinstance(output.get("reply_messages"), list)
        else []
    )
    if response_id:
        messages = stable_v3_reply_messages(
            messages,
            response_id=response_id,
            recovery_kind="primary",
        )
    if not response_snapshot:
        response_snapshot = {
            "request_id": str(row.get("request_id") or ""),
            "response_id": response_id,
            "replayed": False,
            "reply_messages": messages,
            "scene": str(output.get("scene") or ""),
            "intent": str(output.get("intent") or ""),
            "subflow": str(output.get("subflow") or ""),
            "trace_url": output.get("trace_url") or None,
            "meta": output.get("response_meta") if isinstance(output.get("response_meta"), dict) else {},
        }
    response_snapshot = dict(response_snapshot)
    response_snapshot["request_id"] = str(row.get("request_id") or response_snapshot.get("request_id") or "")
    response_snapshot["response_id"] = response_id
    response_snapshot["reply_messages"] = messages
    response_snapshot["replayed"] = True
    meta = response_snapshot.get("meta") if isinstance(response_snapshot.get("meta"), dict) else {}
    response_snapshot["meta"] = {**meta, "replayed": True}
    if http_response:
        http_response["replayed"] = True
        http_data = http_response.get("data")
        if isinstance(http_data, dict):
            http_response["data"] = {**http_data, "replayed": True}
    reply_source = str(output.get("reply_source") or meta.get("reply_source") or "")
    ready = bool(messages) or reply_source in _EMPTY_REPLY_SOURCES
    result = {
        "found": True,
        "ready": ready and status != "generating",
        "replayed": True,
        "request_id": str(row.get("request_id") or ""),
        "conversation_id": str(row.get("conversation_id") or ""),
        "generation_key": str(row.get("generation_key") or ""),
        "response_id": response_id,
        "generation_status": status,
        "recovery_kind": str(row.get("recovery_kind") or ""),
        "recovery_attempts": int(row.get("recovery_attempts") or 0),
        "recovery_next_at": str(row.get("recovery_next_at") or ""),
        "recovery_dispatch_id": str(row.get("recovery_dispatch_id") or ""),
        "recovery_error": str(row.get("recovery_error") or ""),
        "response": response_snapshot,
        "http_response": http_response,
        "recovery_reply_messages": (
            list(output.get("recovery_reply_messages") or [])
            if isinstance(output.get("recovery_reply_messages"), list)
            else []
        ),
        "request": {},
    }
    if include_recovery_payload:
        recovery_request = decode_v3_recovery_payload(output.get("v3_recovery_payload"))
        if recovery_request:
            result["request"] = recovery_request
        else:
            result["request"] = rebuildable_chat_request_snapshot(input_snapshot)
    return result


def _clear_recovery_payload(conn: Any, request_id: str) -> None:
    row = conn.execute(
        "SELECT output_snapshot FROM runs WHERE request_id=? LIMIT 1",
        (request_id,),
    ).fetchone()
    if row is None:
        return
    output = loads_dict(row["output_snapshot"])
    output.pop("v3_recovery_payload", None)
    conn.execute(
        "UPDATE runs SET output_snapshot=? WHERE request_id=?",
        (dumps(output), request_id),
    )


def _recovery_messages_for_dispatch(
    conn: Any,
    *,
    output: dict[str, Any],
    dispatch_id: str,
    response_id: str,
    recovery_kind: str,
) -> list[dict[str, Any]]:
    messages = (
        list(output.get("recovery_reply_messages") or [])
        if isinstance(output.get("recovery_reply_messages"), list)
        else []
    )
    if not messages:
        dispatch = conn.execute(
            "SELECT reply_messages_json FROM message_dispatches WHERE id=? LIMIT 1",
            (dispatch_id,),
        ).fetchone()
        if dispatch is not None:
            messages = loads_list(dispatch["reply_messages_json"])
    return stable_v3_reply_messages(
        [item for item in messages if isinstance(item, dict)],
        response_id=response_id,
        recovery_kind=recovery_kind,
    )


def _insert_recovered_assistant_message(
    conn: Any,
    *,
    request_id: str,
    conversation_id: str,
    dispatch_id: str,
    reply_messages: list[dict[str, Any]],
    created_at: str,
) -> None:
    if not conversation_id or not reply_messages:
        return
    content = "\n".join(
        str(item.get("content") or "")
        for item in reply_messages
        if isinstance(item, dict)
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO messages
            (id, conversation_id, request_id, role, content, file_image, reply_messages, created_at)
        VALUES (?, ?, ?, 'assistant', ?, '', ?, ?)
        """,
        (
            str(uuid5(NAMESPACE_URL, f"v3-recovery-assistant:{request_id}:{dispatch_id}")),
            conversation_id,
            request_id,
            content,
            dumps(reply_messages),
            created_at,
        ),
    )


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
