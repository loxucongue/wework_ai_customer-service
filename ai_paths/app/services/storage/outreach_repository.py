from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.services.customer_scope import build_customer_scope
from app.services.first_day_outreach_log import (
    build_first_day_run_business_summary,
    build_first_day_run_observability,
    redact_first_day_log_value,
)
from app.services.storage.serialization import dumps, loads_dict, loads_list, utc_now_iso
from app.services.storage.store_base import scalar


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _silent_minutes(value: str | None) -> int:
    parsed = _parse_iso(value)
    if not parsed:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds() // 60))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _latest_iso_value(*values: Any) -> str:
    parsed_values = [
        (parsed, _string(value))
        for value in values
        if _string(value) and (parsed := _parse_iso(_string(value))) is not None
    ]
    if not parsed_values:
        return ""
    return max(parsed_values, key=lambda item: item[0])[1]


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


_AUTOMATIC_OUTREACH_SOURCE_TYPES = {
    "first_day",
    "followup_strategy",
    "closing_sequence",
    "auto_approved",
}
_OUTREACH_NO_PLAN_EVENT_TYPES = {
    "plan_rejected",
    "plan_skipped_customer_deleted",
    "plan_skipped_customer_relation_unavailable",
}
_OUTREACH_PENDING_TASK_STATUSES = {"pending"}
_OUTREACH_PROCESSING_TASK_STATUSES = {"checking", "sending"}
_OUTREACH_CONSUMED_TASK_STATUSES = {
    "skipped",
    "cancelled",
    "completed_without_send",
    "shadow_no_send",
    "shadowed",
}
_OUTREACH_FAILED_TASK_STATUSES = {"failed", "check_failed", "partial_failed"}


def _outreach_log_window(started_from: str, started_to: str) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    parsed_start = _parse_iso(started_from)
    parsed_end = _parse_iso(started_to)
    if started_from and parsed_start is None:
        raise ValueError("started_from must be an ISO-8601 datetime")
    if started_to and parsed_end is None:
        raise ValueError("started_to must be an ISO-8601 datetime")
    start = parsed_start or now - timedelta(days=30)
    end = parsed_end or now
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if start >= end:
        raise ValueError("started_from must be earlier than started_to")
    if end - start > timedelta(days=90):
        raise ValueError("outreach customer log range cannot exceed 90 days")
    return start.isoformat(), end.isoformat()


def _outreach_source_type(plan: dict[str, Any]) -> str:
    sop_plan_id = _string(plan.get("sop_plan_id")).lower()
    if sop_plan_id == "first_day_opened_silence":
        return "first_day"
    if sop_plan_id.startswith("followup_strategy:"):
        return "followup_strategy"
    if sop_plan_id.startswith("closing_sequence:"):
        return "closing_sequence"
    snapshot = plan.get("source_snapshot") if isinstance(plan.get("source_snapshot"), dict) else {}
    plan_type = _string(snapshot.get("plan_type"))
    if plan_type in {"followup_strategy", "closing_sequence"}:
        return plan_type
    trigger_context = snapshot.get("trigger_context") if isinstance(snapshot.get("trigger_context"), dict) else {}
    if _string(trigger_context.get("trigger_type")) == "first_day_opened_silence":
        return "first_day"
    if _string(trigger_context.get("activation_policy")) == "auto_approved":
        return "auto_approved"
    return ""


def _outreach_event_source_type(payload: dict[str, Any]) -> str:
    trigger_context = payload.get("trigger_context") if isinstance(payload.get("trigger_context"), dict) else {}
    if _string(trigger_context.get("trigger_type")) == "first_day_opened_silence":
        return "first_day"
    if _string(trigger_context.get("activation_policy")) == "auto_approved":
        return "auto_approved"
    return ""


def _outreach_contact_identity(row: dict[str, Any]) -> dict[str, str]:
    corp_id = _string(row.get("corp_id"))
    wechat = _string(row.get("wechat"))
    external_userid = _string(row.get("external_userid"))
    customer_id = _string(row.get("customer_id"))
    identity_value = external_userid or customer_id
    if not corp_id or not wechat or not identity_value:
        return {}
    identity = {
        "corp_id": corp_id,
        "wechat": wechat,
        "external_userid": external_userid,
        "customer_id": customer_id,
    }
    snapshot = row.get("source_snapshot") if isinstance(row.get("source_snapshot"), dict) else {}
    if not snapshot and isinstance(row.get("input_snapshot"), dict):
        snapshot = row["input_snapshot"]
    trigger_context = (
        snapshot.get("trigger_context")
        if isinstance(snapshot.get("trigger_context"), dict)
        else {}
    )
    optional_fields = {
        "user_id": _string(row.get("user_id"))
        or _string(snapshot.get("user_id"))
        or _string(trigger_context.get("user_id")),
        "customer_add_wechat_id": _string(snapshot.get("customer_add_wechat_id"))
        or _string(trigger_context.get("customer_add_wechat_id")),
        "conversation_id": _string(snapshot.get("conversation_id"))
        or _string(trigger_context.get("conversation_id")),
        "customer_name": _string(snapshot.get("platform_customer_name"))
        or _string(trigger_context.get("platform_customer_name")),
    }
    identity.update({key: value for key, value in optional_fields.items() if value})
    return identity


def _merged_outreach_log_identity(
    records: list[dict[str, Any]],
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep the newest identity while retaining older, strictly scoped IDs."""

    identities = [
        record.get("identity")
        for record in records
        if isinstance(record.get("identity"), dict) and record.get("identity")
    ]
    merged: dict[str, Any] = {
        key: value
        for key, value in (fallback or {}).items()
        if not str(key).startswith("_") and value
    }
    for identity in identities:
        for key in (
            "corp_id",
            "wechat",
            "external_userid",
            "customer_id",
            "user_id",
            "customer_add_wechat_id",
            "conversation_id",
            "customer_name",
        ):
            value = _string(identity.get(key))
            if value and not _string(merged.get(key)):
                merged[key] = value

    for singular, plural in (
        ("customer_id", "customer_ids"),
        ("user_id", "user_ids"),
        ("customer_add_wechat_id", "customer_add_wechat_ids"),
        ("conversation_id", "conversation_ids"),
    ):
        values: list[str] = []
        for identity in identities:
            value = _string(identity.get(singular))
            if value and value not in values:
                values.append(value)
        fallback_value = _string(merged.get(singular))
        if fallback_value and fallback_value not in values:
            values.append(fallback_value)
        if values:
            merged[plural] = values
    return merged


def _outreach_contact_key(
    identity: dict[str, str],
    *,
    customer_id_hints: list[str] | None = None,
) -> str:
    external_userid = _string(identity.get("external_userid")).lower()
    customer_id = _string(identity.get("customer_id")).lower()
    identity_kind, identity_value = (
        ("external_userid", external_userid)
        if external_userid
        else ("customer_id", customer_id)
    )
    values: list[Any] = [
        _string(identity.get("corp_id")).lower(),
        _string(identity.get("wechat")).lower(),
        identity_kind,
        identity_value,
    ]
    hints = sorted(
        {
            _string(value)
            for value in (customer_id_hints or [])
            if _string(value)
        },
        key=str.lower,
    )
    if identity_kind == "external_userid" and hints:
        values.append(hints)
    raw = json.dumps(values, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_outreach_contact_key(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}
    if (
        not isinstance(decoded, list)
        or len(decoded) not in {4, 5}
        or not all(isinstance(item, str) for item in decoded[:4])
    ):
        return {}
    identity_kind = decoded[2]
    if identity_kind not in {"external_userid", "customer_id"}:
        return {}
    identity = _outreach_contact_identity(
        {
            "corp_id": decoded[0],
            "wechat": decoded[1],
            identity_kind: decoded[3],
        }
    )
    if len(decoded) == 5:
        hints = decoded[4]
        if not isinstance(hints, list) or not all(
            isinstance(item, str) for item in hints
        ):
            return {}
        identity["_customer_id_hints"] = sorted(
            {_string(item) for item in hints if _string(item)},
            key=str.lower,
        )
    return identity


def _outreach_contact_matches(row: dict[str, Any], identity: dict[str, str]) -> bool:
    row_identity = _outreach_contact_identity(row)
    if not row_identity or not identity:
        return False
    if _string(row_identity.get("corp_id")).lower() != _string(identity.get("corp_id")).lower():
        return False
    if _string(row_identity.get("wechat")).lower() != _string(identity.get("wechat")).lower():
        return False
    expected_external = _string(identity.get("external_userid")).lower()
    actual_external = _string(row_identity.get("external_userid")).lower()
    if expected_external:
        return bool(actual_external and actual_external == expected_external)
    if actual_external:
        return False
    expected_customer = _string(identity.get("customer_id")).lower()
    actual_customer = _string(row_identity.get("customer_id")).lower()
    return bool(expected_customer and actual_customer and expected_customer == actual_customer)


def _outreach_contact_scope_sql(
    identity: dict[str, str] | None,
    *,
    table_alias: str = "",
) -> tuple[str, tuple[str, ...]]:
    if not identity:
        return "", ()
    prefix = f"{table_alias}." if table_alias else ""
    corp_id = _string(identity.get("corp_id"))
    wechat = _string(identity.get("wechat"))
    external_userid = _string(identity.get("external_userid"))
    customer_id = _string(identity.get("customer_id"))
    if not corp_id or not wechat or not (external_userid or customer_id):
        return " AND 1=0", ()
    clauses = [
        f"lower({prefix}corp_id)=lower(?)",
        f"lower({prefix}wechat)=lower(?)",
    ]
    params: list[str] = [corp_id, wechat]
    if external_userid:
        clauses.append(f"lower({prefix}external_userid)=lower(?)")
        params.append(external_userid)
    else:
        clauses.extend(
            [
                f"{prefix}external_userid=''",
                f"lower({prefix}customer_id)=lower(?)",
            ]
        )
        params.append(customer_id)
    return " AND " + " AND ".join(clauses), tuple(params)


def _outreach_task_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    next_task: dict[str, Any] = {}
    for task in tasks:
        status = _string(task.get("status")) or "unknown"
        counts[status] = counts.get(status, 0) + 1
        if status in _OUTREACH_PENDING_TASK_STATUSES | _OUTREACH_PROCESSING_TASK_STATUSES:
            candidate = {
                "task_id": _string(task.get("id")),
                "plan_id": _string(task.get("plan_id")),
                "step_index": _int(task.get("step_index"), 0),
                "status": status,
                "scheduled_at": _string(task.get("scheduled_at")),
                "message_goal": _string(task.get("message_goal")),
            }
            if not next_task or (
                candidate["scheduled_at"],
                candidate["step_index"],
                candidate["task_id"],
            ) < (
                next_task.get("scheduled_at", ""),
                next_task.get("step_index", 0),
                next_task.get("task_id", ""),
            ):
                next_task = candidate
    sent = sum(
        1
        for task in tasks
        if _string(task.get("status")) == "sent" and _string(task.get("system_msgid"))
    )
    sent_without_message_id = sum(
        1
        for task in tasks
        if _string(task.get("status")) == "sent" and not _string(task.get("system_msgid"))
    )
    consumed = sum(counts.get(status, 0) for status in _OUTREACH_CONSUMED_TASK_STATUSES)
    failed = sum(counts.get(status, 0) for status in _OUTREACH_FAILED_TASK_STATUSES)
    pending = sum(counts.get(status, 0) for status in _OUTREACH_PENDING_TASK_STATUSES)
    processing = sum(counts.get(status, 0) for status in _OUTREACH_PROCESSING_TASK_STATUSES)
    return {
        "total": len(tasks),
        "handled": max(0, len(tasks) - pending - processing),
        "sent": sent,
        "sent_without_message_id": sent_without_message_id,
        "consumed": consumed,
        "failed": failed,
        "pending": pending,
        "processing": processing,
        "status_counts": counts,
        "next_task": next_task,
    }


def _outreach_log_reason(plan: dict[str, Any], source_type: str) -> str:
    snapshot = plan.get("source_snapshot") if isinstance(plan.get("source_snapshot"), dict) else {}
    trigger_context = snapshot.get("trigger_context") if isinstance(snapshot.get("trigger_context"), dict) else {}
    return (
        _string(trigger_context.get("reason_code"))
        or _string(trigger_context.get("trigger_type"))
        or _string(plan.get("stall_reason"))
        or source_type
    )


def _outreach_record_matches_filters(
    record: dict[str, Any],
    *,
    identity_query: str,
    customer_id: str,
    external_userid: str,
    corp_id: str,
    wechat: str,
    source_type: str,
    plan_status: str,
    task_status: str,
    reason_code: str,
    identity_state: str,
) -> bool:
    identity = record.get("identity") if isinstance(record.get("identity"), dict) else {}
    complete = bool(identity)
    if identity_state == "complete" and not complete:
        return False
    if identity_state == "incomplete" and complete:
        return False
    if identity_query and identity_query not in {
        _string(identity.get("customer_id")),
        _string(identity.get("external_userid")),
    }:
        return False
    if customer_id and _string(identity.get("customer_id")) != customer_id:
        return False
    if external_userid and _string(identity.get("external_userid")) != external_userid:
        return False
    if corp_id and _string(identity.get("corp_id")) != corp_id:
        return False
    if wechat and _string(identity.get("wechat")).lower() != wechat.lower():
        return False
    if source_type and _string(record.get("source_type")) != source_type:
        return False
    if plan_status:
        if plan_status == "no_plan":
            if _string(record.get("record_type")) != "no_plan":
                return False
        elif _string(record.get("record_type")) != "plan" or _string(record.get("status")) != plan_status:
            return False
    if task_status:
        task_counts = record.get("task_summary", {}).get("status_counts", {}) if isinstance(record.get("task_summary"), dict) else {}
        if not isinstance(task_counts, dict) or not int(task_counts.get(task_status, 0) or 0):
            return False
    if reason_code and reason_code.lower() not in _string(record.get("reason_code")).lower():
        return False
    return True


def _strict_identity_match(
    *,
    external_userid: str,
    customer_id: str,
    table_alias: str = "",
) -> tuple[str, tuple[str, ...]]:
    prefix = f"{table_alias}." if table_alias else ""
    if _string(external_userid):
        return f"lower({prefix}external_userid)=lower(?)", (_string(external_userid),)
    if _string(customer_id):
        return f"{prefix}customer_id=?", (_string(customer_id),)
    return "1=0", ()


def _outreach_candidate_matches_keyword(candidate: dict[str, Any], keyword: str) -> bool:
    needle = keyword.strip().lower()
    if not needle:
        return True
    parts = [
        candidate.get("customer_id"),
        candidate.get("external_userid"),
        candidate.get("wechat"),
        candidate.get("platform_customer_name"),
        candidate.get("title"),
        candidate.get("last_customer_message"),
        candidate.get("latest_event_summary"),
        candidate.get("lifecycle_stage"),
    ]
    for field in ("portrait", "basic_info"):
        value = candidate.get(field)
        if isinstance(value, dict):
            parts.extend(str(item) for item in value.values())
    return needle in " ".join(_string(part).lower() for part in parts if part is not None)


def _candidate_identity_matches_keyword(candidate: dict[str, Any], keyword: str) -> bool:
    needle = keyword.strip().lower()
    if not needle:
        return True
    parts = [
        candidate.get("customer_id"),
        candidate.get("external_userid"),
        candidate.get("wechat"),
        candidate.get("platform_customer_name"),
        candidate.get("title"),
        candidate.get("last_customer_message"),
    ]
    return needle in " ".join(_string(part).lower() for part in parts if part is not None)


def _latest_platform_customer_names(conn: Any, rows: list[Any]) -> dict[tuple[str, str], str]:
    external_ids = sorted(
        {
            _string(row["external_userid"]).lower()
            for row in rows
            if _string(row["external_userid"])
        }
    )
    if not external_ids:
        return {}
    names: dict[tuple[str, str], str] = {}
    wanted = set(external_ids)
    event_rows = conn.execute(
        """
        SELECT raw_payload_json
        FROM sop_events
        ORDER BY received_at DESC
        LIMIT 5000
        """
    ).fetchall()
    for event_row in event_rows:
        payload = loads_dict(event_row["raw_payload_json"])
        account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        account_wechat = _string(account.get("wework_user_id")).lower()
        customers = payload.get("customers") if isinstance(payload.get("customers"), list) else []
        for item in customers:
            if not isinstance(item, dict):
                continue
            conversation = item.get("conversation") if isinstance(item.get("conversation"), dict) else {}
            customer = item.get("customer") if isinstance(item.get("customer"), dict) else {}
            external_key = _string(
                conversation.get("external_userid") or customer.get("external_userid")
            ).lower()
            if external_key not in wanted:
                continue
            wechat_key = _string(conversation.get("wework_user_id")).lower() or account_wechat
            customer_name = _string(
                customer.get("name")
                or conversation.get("sender_name")
                or customer.get("remark")
                or conversation.get("sender_remark")
            )
            key = (external_key, wechat_key)
            if key[0] and key[1] and customer_name and key not in names:
                names[key] = customer_name
    return names


class OutreachRepositoryMixin:
    _FIRST_DAY_RUN_JSON_FIELDS = {
        "input_snapshot_json": "input_snapshot",
        "workflow_json": "workflow",
        "final_plan_json": "final_plan",
    }

    def find_open_outreach_plan_by_sop_plan_id(
        self,
        sop_plan_id: str,
        *,
        corp_id: str,
        wechat: str,
        external_userid: str,
        customer_id: str,
    ) -> dict[str, Any]:
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM outreach_plans
                WHERE sop_plan_id=? AND corp_id=? AND wechat=? AND external_userid=? AND customer_id=?
                  AND status IN ('draft','active','waiting','paused')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (sop_plan_id, corp_id, wechat, external_userid, customer_id),
            ).fetchone()
        return self._decode_outreach_plan(dict(row)) if row else {}

    def cancel_open_closing_sequence_plans(
        self,
        *,
        corp_id: str,
        wechat: str,
        external_userid: str,
        customer_id: str,
        reason: str,
    ) -> int:
        now = utc_now_iso()
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM outreach_plans
                WHERE sop_plan_id LIKE 'closing_sequence:%'
                  AND corp_id=? AND wechat=? AND external_userid=? AND customer_id=?
                  AND status IN ('draft','active','waiting','paused')
                """,
                (corp_id, wechat, external_userid, customer_id),
            ).fetchall()
            plan_ids = [str(row["id"]) for row in rows]
            for plan_id in plan_ids:
                conn.execute(
                    "UPDATE outreach_plans SET status='cancelled', updated_at=? WHERE id=?",
                    (now, plan_id),
                )
                conn.execute(
                    """
                    UPDATE outreach_tasks
                    SET status='cancelled', error_message=?, updated_at=?
                    WHERE plan_id=? AND status IN ('pending','checking','check_failed')
                    """,
                    (reason[:500], now, plan_id),
                )
        return len(plan_ids)

    def create_first_day_outreach_run(
        self,
        *,
        customer_id: str,
        corp_id: str,
        user_id: str,
        wechat: str,
        external_userid: str,
        trigger_type: str,
        conversation_fingerprint: str = "",
        input_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workflow_run_id = str(uuid4())
        now = utc_now_iso()
        fingerprint_value = _string(conversation_fingerprint) or None
        try:
            with self.store.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO first_day_outreach_runs
                        (workflow_run_id, corp_id, user_id, wechat, customer_id, external_userid,
                         trigger_type, conversation_fingerprint, status, input_snapshot_json,
                         started_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)
                    """,
                    (
                        workflow_run_id,
                        corp_id,
                        user_id,
                        wechat,
                        customer_id,
                        external_userid,
                        trigger_type,
                        fingerprint_value,
                        dumps(redact_first_day_log_value(input_snapshot or {})),
                        now,
                        now,
                        now,
                    ),
                )
        except Exception:
            existing = self.find_first_day_outreach_run_by_fingerprint(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                conversation_fingerprint=_string(conversation_fingerprint),
            )
            if existing:
                return existing
            raise
        return self.get_first_day_outreach_run(workflow_run_id, include_related=False)

    def find_first_day_outreach_run_by_fingerprint(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str,
        conversation_fingerprint: str,
    ) -> dict[str, Any]:
        if not all((customer_id, corp_id, wechat, external_userid, conversation_fingerprint)):
            return {}
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM first_day_outreach_runs
                WHERE customer_id=? AND corp_id=? AND lower(wechat)=lower(?)
                  AND external_userid=? AND conversation_fingerprint=?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (customer_id, corp_id, wechat, external_userid, conversation_fingerprint),
            ).fetchone()
            if row:
                return self._decode_first_day_outreach_run(dict(row))
            rows = conn.execute(
                """
                SELECT * FROM first_day_outreach_runs
                WHERE customer_id=? AND corp_id=? AND lower(wechat)=lower(?)
                  AND external_userid=? AND conversation_fingerprint IS NULL
                ORDER BY started_at DESC
                LIMIT 20
                """,
                (customer_id, corp_id, wechat, external_userid),
            ).fetchall()
        for row in rows:
            decoded = self._decode_first_day_outreach_run(dict(row))
            snapshot = decoded.get("input_snapshot") if isinstance(decoded.get("input_snapshot"), dict) else {}
            trigger = snapshot.get("trigger_context") if isinstance(snapshot.get("trigger_context"), dict) else {}
            if _string(trigger.get("conversation_fingerprint")) == conversation_fingerprint:
                return decoded
        return {}

    def find_latest_unplanned_first_day_outreach_run_for_customer(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str,
    ) -> dict[str, Any]:
        """Return the scoped run that an interrupted monitor must close.

        This deliberately excludes rows with a materialized plan: the task
        executor owns those rows and monitor recovery must not mutate them.
        """

        if not all((customer_id, corp_id, wechat, external_userid)):
            return {}
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM first_day_outreach_runs
                WHERE customer_id=? AND corp_id=? AND lower(wechat)=lower(?)
                  AND external_userid=?
                  AND trigger_type='first_day_opened_silence'
                  AND status IN ('running', 'created')
                  AND (plan_id IS NULL OR plan_id='')
                ORDER BY updated_at DESC, started_at DESC
                LIMIT 1
                """,
                (customer_id, corp_id, wechat, external_userid),
            ).fetchone()
        return self._decode_first_day_outreach_run(dict(row)) if row else {}

    def list_first_day_outreach_runs_for_monitor(
        self,
        *,
        since: str = "",
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Load recent idempotency rows once for a monitor scan.

        The silence monitor can inspect hundreds of conversations per pass. Looking
        up the same contact fingerprint with a fresh database round trip for every
        row is needlessly expensive on a remote MySQL backend, so the monitor uses
        this bounded snapshot for the cheap preflight check. The authoritative
        fingerprint is still checked again after the platform conversation refresh.
        """

        clauses = ["trigger_type='first_day_opened_silence'", "conversation_fingerprint IS NOT NULL"]
        params: list[Any] = []
        if _string(since):
            clauses.append("started_at>=?")
            params.append(_string(since))
        params.append(max(1, min(int(limit or 5000), 10000)))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM first_day_outreach_runs
                WHERE {' AND '.join(clauses)}
                ORDER BY started_at DESC, workflow_run_id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._decode_first_day_outreach_run(dict(row)) for row in rows]

    def backfill_first_day_outreach_runs(self) -> dict[str, int]:
        """Create observability rows for first-day plans created before run logging existed."""
        created_count = 0
        linked_active_count = 0
        with self.store.connect() as conn:
            existing_plan_ids = {
                _string(row["plan_id"])
                for row in conn.execute(
                    "SELECT plan_id FROM first_day_outreach_runs WHERE plan_id!=''"
                ).fetchall()
            }
            plans = conn.execute(
                """
                SELECT * FROM outreach_plans
                WHERE sop_plan_id='first_day_opened_silence'
                ORDER BY created_at ASC
                """
            ).fetchall()
            for raw_plan in plans:
                plan = dict(raw_plan)
                plan_id = _string(plan.get("id"))
                if not plan_id or plan_id in existing_plan_ids:
                    continue
                source_snapshot = loads_dict(plan.get("source_snapshot"))
                trigger_context = (
                    source_snapshot.get("trigger_context")
                    if isinstance(source_snapshot.get("trigger_context"), dict)
                    else {}
                )
                if _string(trigger_context.get("trigger_type")) != "first_day_opened_silence":
                    continue

                tasks = conn.execute(
                    """
                    SELECT id, status, step_index, sent_at
                    FROM outreach_tasks WHERE plan_id=?
                    ORDER BY step_index ASC, created_at ASC
                    """,
                    (plan_id,),
                ).fetchall()
                task_rows = [dict(row) for row in tasks]
                first_task = task_rows[0] if task_rows else {}
                second_task = task_rows[1] if len(task_rows) > 1 else {}
                customer_reply_cancelled = bool(
                    conn.execute(
                        """
                        SELECT 1 FROM outreach_events
                        WHERE plan_id=? AND event_type='plan_cancelled_customer_replied'
                        LIMIT 1
                        """,
                        (plan_id,),
                    ).fetchone()
                )
                ai_result = (
                    source_snapshot.get("ai_result")
                    if isinstance(source_snapshot.get("ai_result"), dict)
                    else {}
                )
                steps = ai_result.get("steps") if isinstance(ai_result.get("steps"), list) else []
                plan_status = _string(plan.get("status"))
                task_statuses = {_string(task.get("status")) for task in task_rows}
                status = "created"
                reason_code = "legacy_plan_backfilled"
                final_decision = "send_pending"
                finished_at = ""
                if plan_status == "cancelled":
                    status = "cancelled"
                    reason_code = "customer_replied" if customer_reply_cancelled else "plan_cancelled"
                    final_decision = "remaining_tasks_cancelled"
                    finished_at = _string(plan.get("updated_at"))
                elif plan_status in {"failed", "rejected"}:
                    status = "failed"
                    reason_code = "legacy_plan_failed"
                    final_decision = "failed"
                    finished_at = _string(plan.get("updated_at"))
                elif plan_status == "completed":
                    status = "sent"
                    reason_code = "all_tasks_finished"
                    final_decision = "completed"
                    finished_at = _string(plan.get("updated_at"))
                elif "sent" in task_statuses:
                    final_decision = "first_task_sent_second_pending"

                started_at = _string(plan.get("created_at")) or utc_now_iso()
                finished = _parse_iso(finished_at)
                started = _parse_iso(started_at)
                duration_ms = (
                    max(0, round((finished - started).total_seconds() * 1000))
                    if finished and started
                    else 0
                )
                workflow_run_id = str(uuid4())
                workflow = {
                    "summary": source_snapshot.get("first_day_workflow") or {},
                    "backfilled_from_legacy_plan": True,
                }
                conn.execute(
                    """
                    INSERT INTO first_day_outreach_runs
                        (workflow_run_id, plan_id, first_task_id, second_task_id,
                         corp_id, user_id, wechat, customer_id, external_userid,
                         trigger_type, status, reason_code, final_decision,
                         first_scene, second_scene, duration_ms,
                         input_snapshot_json, workflow_json, final_plan_json,
                         started_at, finished_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'first_day_opened_silence',
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workflow_run_id,
                        plan_id,
                        _string(first_task.get("id")),
                        _string(second_task.get("id")),
                        _string(plan.get("corp_id")),
                        _string(plan.get("user_id")),
                        _string(plan.get("wechat")),
                        _string(plan.get("customer_id")),
                        _string(plan.get("external_userid")),
                        status,
                        reason_code,
                        final_decision,
                        _string((steps[0] if steps else {}).get("scene")),
                        _string((steps[1] if len(steps) > 1 else {}).get("scene")),
                        duration_ms,
                        dumps(redact_first_day_log_value(source_snapshot)),
                        dumps(redact_first_day_log_value(workflow)),
                        dumps(redact_first_day_log_value(ai_result)),
                        started_at,
                        finished_at,
                        started_at,
                        _string(plan.get("updated_at")) or started_at,
                    ),
                )
                if plan_status in {"draft", "active", "waiting", "paused"}:
                    source_snapshot["workflow_run_id"] = workflow_run_id
                    conn.execute(
                        "UPDATE outreach_plans SET source_snapshot=? WHERE id=?",
                        (dumps(source_snapshot), plan_id),
                    )
                    linked_active_count += 1
                existing_plan_ids.add(plan_id)
                created_count += 1
        return {
            "scanned_plans": len(plans),
            "created_runs": created_count,
            "linked_active_plans": linked_active_count,
        }

    def update_first_day_outreach_run(self, workflow_run_id: str, **changes: Any) -> dict[str, Any]:
        scalar_fields = {
            "plan_id", "first_task_id", "second_task_id", "status", "reason_code",
            "final_decision", "first_scene", "second_scene", "model_attempt_count",
            "retry_count", "duration_ms", "error_node", "error_type", "error_message",
            "finished_at", "raw_redacted_at", "conversation_fingerprint", "next_retry_at",
        }
        json_fields = set(self._FIRST_DAY_RUN_JSON_FIELDS)
        assignments: list[str] = []
        params: list[Any] = []
        for key, value in changes.items():
            if key in scalar_fields:
                assignments.append(f"{key}=?")
                params.append(
                    redact_first_day_log_value(value)
                    if key in {"error_message"}
                    else value
                )
            elif key in json_fields:
                assignments.append(f"{key}=?")
                params.append(dumps(redact_first_day_log_value(value or {})))
        if not assignments:
            return self.get_first_day_outreach_run(workflow_run_id, include_related=False)
        assignments.append("updated_at=?")
        params.extend((utc_now_iso(), workflow_run_id))
        with self.store.connect() as conn:
            conn.execute(
                f"UPDATE first_day_outreach_runs SET {', '.join(assignments)} WHERE workflow_run_id=?",
                params,
            )
        return self.get_first_day_outreach_run(workflow_run_id, include_related=False)

    def list_first_day_outreach_runs(
        self,
        *,
        limit: int = 50,
        cursor: str = "",
        started_from: str = "",
        started_to: str = "",
        customer_id: str = "",
        external_userid: str = "",
        corp_id: str = "",
        wechat: str = "",
        plan_id: str = "",
        status: str = "",
        reason_code: str = "",
        first_scene: str = "",
        second_scene: str = "",
        failed: bool | None = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        filters = {
            "customer_id": customer_id,
            "external_userid": external_userid,
            "corp_id": corp_id,
            "wechat": wechat,
            "plan_id": plan_id,
            "status": status,
            "reason_code": reason_code,
            "first_scene": first_scene,
            "second_scene": second_scene,
        }
        for field, value in filters.items():
            if value:
                clauses.append(f"{field}=?")
                params.append(value)
        if started_from:
            clauses.append("started_at>=?")
            params.append(started_from)
        if started_to:
            clauses.append("started_at<=?")
            params.append(started_to)
        if failed is True:
            clauses.append("status='failed'")
        elif failed is False:
            clauses.append("status!='failed'")
        cursor_value = self._decode_first_day_cursor(cursor)
        if cursor_value:
            clauses.append("(started_at<? OR (started_at=? AND workflow_run_id<?))")
            params.extend((cursor_value[0], cursor_value[0], cursor_value[1]))
        page_size = max(1, min(int(limit or 50), 200))
        params.append(page_size + 1)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM first_day_outreach_runs
                {where}
                ORDER BY started_at DESC, workflow_run_id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        decoded = [self._decode_first_day_outreach_run(dict(row)) for row in rows]
        has_more = len(decoded) > page_size
        items = decoded[:page_size]
        task_ids = sorted(
            {
                _string(item.get(field))
                for item in items
                for field in ("first_task_id", "second_task_id")
                if _string(item.get(field))
            }
        )
        task_statuses: dict[str, str] = {}
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            with self.store.connect() as conn:
                task_rows = conn.execute(
                    f"SELECT id, status FROM outreach_tasks WHERE id IN ({placeholders})",
                    task_ids,
                ).fetchall()
            task_statuses = {_string(row["id"]): _string(row["status"]) for row in task_rows}
        for item in items:
            item["first_task_status"] = task_statuses.get(_string(item.get("first_task_id")), "")
            item["second_task_status"] = task_statuses.get(_string(item.get("second_task_id")), "")
            item["business_summary"] = build_first_day_run_business_summary(item)
            item.pop("input_snapshot", None)
            item.pop("workflow", None)
            item.pop("final_plan", None)
        next_cursor = ""
        if has_more and items:
            next_cursor = self._encode_first_day_cursor(
                _string(items[-1].get("started_at")),
                _string(items[-1].get("workflow_run_id")),
            )
        return redact_first_day_log_value(
            {"items": items, "next_cursor": next_cursor, "has_more": has_more}
        )

    def get_first_day_outreach_run(
        self,
        workflow_run_id: str,
        *,
        include_related: bool = True,
    ) -> dict[str, Any]:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM first_day_outreach_runs WHERE workflow_run_id=?",
                (workflow_run_id,),
            ).fetchone()
        if not row:
            return {}
        result = self._decode_first_day_outreach_run(dict(row))
        if include_related and _string(result.get("plan_id")):
            related = self.get_outreach_plan(_string(result.get("plan_id")))
            result["tasks"] = related.get("tasks") or []
            result["events"] = related.get("events") or []
        else:
            result["tasks"] = []
            result["events"] = []
        result["observability_view"] = build_first_day_run_observability(result)
        return redact_first_day_log_value(result)

    def _outreach_customer_log_records(
        self,
        *,
        started_from: str,
        started_to: str,
        identity: dict[str, str] | None = None,
        aggregate_no_plan_events: bool = False,
    ) -> list[dict[str, Any]]:
        automatic_snapshot = self.store.json_text(
            "source_snapshot", "$.trigger_context.activation_policy"
        )
        snapshot_plan_type = self.store.json_text("source_snapshot", "$.plan_type")
        snapshot_trigger_type = self.store.json_text(
            "source_snapshot", "$.trigger_context.trigger_type"
        )
        snapshot_reason_code = self.store.json_text(
            "source_snapshot", "$.trigger_context.reason_code"
        )
        snapshot_workflow_run_id = self.store.json_text(
            "source_snapshot", "$.workflow_run_id"
        )
        snapshot_customer_add_wechat_id = self.store.json_text(
            "source_snapshot", "$.customer_add_wechat_id"
        )
        snapshot_trigger_customer_add_wechat_id = self.store.json_text(
            "source_snapshot", "$.trigger_context.customer_add_wechat_id"
        )
        snapshot_conversation_id = self.store.json_text(
            "source_snapshot", "$.conversation_id"
        )
        snapshot_trigger_conversation_id = self.store.json_text(
            "source_snapshot", "$.trigger_context.conversation_id"
        )
        snapshot_platform_customer_name = self.store.json_text(
            "source_snapshot", "$.platform_customer_name"
        )
        snapshot_trigger_platform_customer_name = self.store.json_text(
            "source_snapshot", "$.trigger_context.platform_customer_name"
        )
        snapshot_latest_customer_message_at = self.store.json_text(
            "source_snapshot", "$.conversation_activity.latest_customer_message_at"
        )
        snapshot_fact_last_customer_message_at = self.store.json_text(
            "source_snapshot", "$.customer_fact_snapshot.last_customer_message_at"
        )
        contact_sql, contact_params = _outreach_contact_scope_sql(identity)
        event_identity_json = self.store.json_text("e.payload_json", "$.identity")
        event_trigger_context_json = self.store.json_text(
            "e.payload_json", "$.trigger_context"
        )
        event_trigger_type = self.store.json_text(
            "e.payload_json", "$.trigger_context.trigger_type"
        )
        event_activation_policy = self.store.json_text(
            "e.payload_json", "$.trigger_context.activation_policy"
        )
        event_reason = self.store.json_text("e.payload_json", "$.reason")
        event_workflow_run_id = self.store.json_text(
            "e.payload_json", "$.workflow_run_id"
        )
        event_contact_sql = ""
        event_contact_params: tuple[str, ...] = ()
        if identity:
            event_corp_id = self.store.json_text("e.payload_json", "$.identity.corp_id")
            event_wechat = self.store.json_text("e.payload_json", "$.identity.wechat")
            event_external_userid = self.store.json_text(
                "e.payload_json", "$.identity.external_userid"
            )
            event_customer_id = self.store.json_text(
                "e.payload_json", "$.identity.customer_id"
            )
            expected_corp_id = _string(identity.get("corp_id"))
            expected_wechat = _string(identity.get("wechat"))
            expected_external_userid = _string(identity.get("external_userid"))
            expected_customer_id = _string(identity.get("customer_id"))
            if not expected_corp_id or not expected_wechat or not (
                expected_external_userid or expected_customer_id
            ):
                event_contact_sql = " AND 1=0"
            else:
                event_contact_sql = (
                    f" AND lower({event_corp_id})=lower(?)"
                    f" AND lower({event_wechat})=lower(?)"
                )
                event_contact_values = [expected_corp_id, expected_wechat]
                customer_id_hints = identity.get("_customer_id_hints")
                if isinstance(customer_id_hints, list):
                    customer_id_hints = [
                        _string(value)
                        for value in customer_id_hints
                        if _string(value)
                    ]
                else:
                    customer_id_hints = []
                if customer_id_hints:
                    event_contact_sql += (
                        " AND e.customer_id IN ("
                        + ",".join("?" for _ in customer_id_hints)
                        + ")"
                    )
                    event_contact_values.extend(customer_id_hints)
                if expected_external_userid:
                    event_contact_sql += f" AND lower({event_external_userid})=lower(?)"
                    event_contact_values.append(expected_external_userid)
                else:
                    event_contact_sql += (
                        f" AND COALESCE({event_external_userid},'')=''"
                        f" AND lower({event_customer_id})=lower(?)"
                    )
                    event_contact_values.append(expected_customer_id)
                event_contact_params = tuple(event_contact_values)
        with self.store.connect() as conn:
            plan_rows = conn.execute(
                f"""
                SELECT id, sop_plan_id, customer_id, corp_id, user_id, wechat,
                       external_userid, status, customer_stage, stall_reason,
                       customer_psychology, plan_goal, created_at, updated_at,
                       {snapshot_plan_type} AS snapshot_plan_type,
                       {snapshot_trigger_type} AS snapshot_trigger_type,
                       {automatic_snapshot} AS snapshot_activation_policy,
                       {snapshot_reason_code} AS snapshot_reason_code,
                       {snapshot_workflow_run_id} AS snapshot_workflow_run_id,
                       {snapshot_customer_add_wechat_id} AS snapshot_customer_add_wechat_id,
                       {snapshot_trigger_customer_add_wechat_id} AS snapshot_trigger_customer_add_wechat_id,
                       {snapshot_conversation_id} AS snapshot_conversation_id,
                       {snapshot_trigger_conversation_id} AS snapshot_trigger_conversation_id,
                       {snapshot_platform_customer_name} AS snapshot_platform_customer_name,
                       {snapshot_trigger_platform_customer_name} AS snapshot_trigger_platform_customer_name,
                       {snapshot_latest_customer_message_at} AS snapshot_latest_customer_message_at,
                       {snapshot_fact_last_customer_message_at} AS snapshot_fact_last_customer_message_at
                FROM outreach_plans
                WHERE created_at>=? AND created_at<=?
                  AND (
                    sop_plan_id='first_day_opened_silence'
                    OR sop_plan_id LIKE 'followup_strategy:%'
                    OR sop_plan_id LIKE 'closing_sequence:%'
                    OR {automatic_snapshot}='auto_approved'
                  )
                  {contact_sql}
                ORDER BY created_at DESC, id DESC
                """,
                (started_from, started_to, *contact_params),
            ).fetchall()
            first_day_rows = conn.execute(
                f"""
                SELECT workflow_run_id, plan_id, corp_id, user_id, wechat,
                       customer_id, external_userid, trigger_type, status,
                       reason_code, started_at, finished_at, raw_redacted_at
                FROM first_day_outreach_runs
                WHERE plan_id='' AND started_at>=? AND started_at<=?
                  AND trigger_type='first_day_opened_silence'
                  {contact_sql}
                ORDER BY started_at DESC, workflow_run_id DESC
                """,
                (started_from, started_to, *contact_params),
            ).fetchall()
            placeholders = ",".join("?" for _ in _OUTREACH_NO_PLAN_EVENT_TYPES)
            if aggregate_no_plan_events:
                no_plan_event_rows = conn.execute(
                    f"""
                    SELECT {event_identity_json} AS identity_json,
                           {event_trigger_context_json} AS trigger_context_json,
                           {event_reason} AS reason,
                           e.event_type,
                           COUNT(*) AS record_count,
                           MAX(e.id) AS latest_id,
                           MAX(e.created_at) AS latest_at
                    FROM outreach_events e
                    WHERE e.plan_id='' AND e.created_at>=? AND e.created_at<=?
                      AND e.event_type IN ({placeholders})
                      AND (
                        {event_trigger_type}='first_day_opened_silence'
                        OR {event_activation_policy}='auto_approved'
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM first_day_outreach_runs first_day
                        WHERE first_day.workflow_run_id={event_workflow_run_id}
                          AND first_day.workflow_run_id!=''
                          AND first_day.plan_id=''
                          AND first_day.started_at>=?
                          AND first_day.started_at<=?
                          AND first_day.trigger_type='first_day_opened_silence'
                      )
                      {event_contact_sql}
                    GROUP BY {event_identity_json},
                             {event_trigger_context_json},
                             {event_reason},
                             e.event_type
                    ORDER BY latest_at DESC, latest_id DESC
                    """,
                    (
                        started_from,
                        started_to,
                        *_OUTREACH_NO_PLAN_EVENT_TYPES,
                        started_from,
                        started_to,
                        *event_contact_params,
                    ),
                ).fetchall()
            else:
                no_plan_event_rows = conn.execute(
                    f"""
                    SELECT e.* FROM outreach_events e
                    WHERE e.plan_id='' AND e.created_at>=? AND e.created_at<=?
                      AND e.event_type IN ({placeholders})
                      {event_contact_sql}
                    ORDER BY e.created_at DESC, e.id DESC
                    """,
                    (
                        started_from,
                        started_to,
                        *_OUTREACH_NO_PLAN_EVENT_TYPES,
                        *event_contact_params,
                    ),
                ).fetchall()

            task_rows: list[Any] = []
            plan_ids = [_string(row["id"]) for row in plan_rows if _string(row["id"])]
            for offset in range(0, len(plan_ids), 500):
                chunk = plan_ids[offset : offset + 500]
                if not chunk:
                    continue
                task_rows.extend(
                    conn.execute(
                        f"""
                        SELECT id, plan_id, step_index, scheduled_at, status,
                               message_goal, sent_at, send_status, system_msgid
                        FROM outreach_tasks
                        WHERE plan_id IN ({','.join('?' for _ in chunk)})
                        ORDER BY plan_id, step_index ASC
                        """,
                        chunk,
                    ).fetchall()
                )

        tasks_by_plan: dict[str, list[dict[str, Any]]] = {}
        for task_row in task_rows:
            task = self._decode_outreach_task(dict(task_row))
            tasks_by_plan.setdefault(_string(task.get("plan_id")), []).append(task)

        records: list[dict[str, Any]] = []
        for plan_row in plan_rows:
            raw_plan = dict(plan_row)
            latest_customer_message_at = _string(
                raw_plan.pop("snapshot_latest_customer_message_at", "")
            )
            fact_last_customer_message_at = _string(
                raw_plan.pop("snapshot_fact_last_customer_message_at", "")
            )
            cycle_customer_message_at = (
                latest_customer_message_at or fact_last_customer_message_at
            )
            raw_plan["source_snapshot"] = dumps(
                {
                    "plan_type": _string(raw_plan.pop("snapshot_plan_type", "")),
                    "workflow_run_id": _string(raw_plan.pop("snapshot_workflow_run_id", "")),
                    "customer_add_wechat_id": _string(
                        raw_plan.pop("snapshot_customer_add_wechat_id", "")
                    ),
                    "conversation_id": _string(raw_plan.pop("snapshot_conversation_id", "")),
                    "platform_customer_name": _string(
                        raw_plan.pop("snapshot_platform_customer_name", "")
                    ),
                    "conversation_activity": {
                        "latest_customer_message_at": cycle_customer_message_at,
                    },
                    "trigger_context": {
                        "trigger_type": _string(raw_plan.pop("snapshot_trigger_type", "")),
                        "activation_policy": _string(
                            raw_plan.pop("snapshot_activation_policy", "")
                        ),
                        "reason_code": _string(raw_plan.pop("snapshot_reason_code", "")),
                        "customer_add_wechat_id": _string(
                            raw_plan.pop("snapshot_trigger_customer_add_wechat_id", "")
                        ),
                        "conversation_id": _string(
                            raw_plan.pop("snapshot_trigger_conversation_id", "")
                        ),
                        "platform_customer_name": _string(
                            raw_plan.pop("snapshot_trigger_platform_customer_name", "")
                        ),
                    },
                }
            )
            plan = self._decode_outreach_plan(raw_plan)
            source_type = _outreach_source_type(plan)
            if source_type not in _AUTOMATIC_OUTREACH_SOURCE_TYPES:
                continue
            tasks = tasks_by_plan.get(_string(plan.get("id")), [])
            records.append(
                {
                    "record_id": f"plan:{_string(plan.get('id'))}",
                    "record_type": "plan",
                    "plan_id": _string(plan.get("id")),
                    "workflow_run_id": _string(
                        (plan.get("source_snapshot") or {}).get("workflow_run_id")
                        if isinstance(plan.get("source_snapshot"), dict)
                        else ""
                    ),
                    "identity": _outreach_contact_identity(plan),
                    "source_type": source_type,
                    "status": _string(plan.get("status")),
                    "reason_code": _outreach_log_reason(plan, source_type),
                    "plan_goal": _string(plan.get("plan_goal")),
                    "customer_stage": _string(plan.get("customer_stage")),
                    "customer_psychology": _string(plan.get("customer_psychology")),
                    "cycle_customer_message_at": cycle_customer_message_at,
                    "created_at": _string(plan.get("created_at")),
                    "updated_at": _string(plan.get("updated_at")),
                    "task_summary": _outreach_task_summary(tasks),
                }
            )

        first_day_run_ids: set[str] = set()
        for run_row in first_day_rows:
            run = self._decode_first_day_outreach_run(dict(run_row))
            workflow_run_id = _string(run.get("workflow_run_id"))
            first_day_run_ids.add(workflow_run_id)
            records.append(
                {
                    "record_id": f"run:{workflow_run_id}",
                    "record_type": "no_plan",
                    "plan_id": "",
                    "workflow_run_id": workflow_run_id,
                    "identity": _outreach_contact_identity(run),
                    "source_type": "first_day",
                    "status": _string(run.get("status")) or "blocked",
                    "reason_code": _string(run.get("reason_code")) or "no_plan",
                    "plan_goal": "",
                    "customer_stage": "",
                    "customer_psychology": "",
                    "created_at": _string(run.get("started_at")),
                    "updated_at": _string(run.get("finished_at")) or _string(run.get("started_at")),
                    "task_summary": _outreach_task_summary([]),
                    "raw_redacted_at": _string(run.get("raw_redacted_at")),
                }
            )

        for event_row in no_plan_event_rows:
            if aggregate_no_plan_events:
                aggregated_event = dict(event_row)
                identity_payload = loads_dict(aggregated_event.get("identity_json"))
                trigger_context = loads_dict(
                    aggregated_event.get("trigger_context_json")
                )
                payload = {
                    "identity": identity_payload,
                    "trigger_context": trigger_context,
                    "reason": _string(aggregated_event.get("reason")),
                }
                source_type = _outreach_event_source_type(payload)
                if source_type not in _AUTOMATIC_OUTREACH_SOURCE_TYPES:
                    continue
                records.append(
                    {
                        "record_id": f"event-group:{_string(aggregated_event.get('latest_id'))}",
                        "record_type": "no_plan",
                        "record_count": max(
                            1,
                            _int(aggregated_event.get("record_count"), 1),
                        ),
                        "plan_id": "",
                        "workflow_run_id": "",
                        "identity": _outreach_contact_identity(identity_payload),
                        "source_type": source_type,
                        "status": "blocked",
                        "reason_code": _string(payload.get("reason"))
                        or _string(aggregated_event.get("event_type"))
                        or "no_plan",
                        "plan_goal": "",
                        "customer_stage": "",
                        "customer_psychology": "",
                        "created_at": _string(aggregated_event.get("latest_at")),
                        "updated_at": _string(aggregated_event.get("latest_at")),
                        "task_summary": _outreach_task_summary([]),
                    }
                )
                continue
            event = self._decode_outreach_event(dict(event_row))
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            workflow_run_id = _string(payload.get("workflow_run_id"))
            if workflow_run_id and workflow_run_id in first_day_run_ids:
                continue
            source_type = _outreach_event_source_type(payload)
            if source_type not in _AUTOMATIC_OUTREACH_SOURCE_TYPES:
                continue
            identity_payload = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
            records.append(
                {
                    "record_id": f"event:{_string(event.get('id'))}",
                    "record_type": "no_plan",
                    "plan_id": "",
                    "workflow_run_id": workflow_run_id,
                    "identity": _outreach_contact_identity(identity_payload),
                    "source_type": source_type,
                    "status": "blocked",
                    "reason_code": _string(payload.get("reason")) or _string(event.get("event_type")) or "no_plan",
                    "plan_goal": "",
                    "customer_stage": "",
                    "customer_psychology": "",
                    "created_at": _string(event.get("created_at")),
                    "updated_at": _string(event.get("created_at")),
                    "event_summary": _string(event.get("event_summary")),
                    "task_summary": _outreach_task_summary([]),
                }
            )
        return records

    def list_outreach_customer_logs(
        self,
        *,
        limit: int = 50,
        cursor: str = "",
        started_from: str = "",
        started_to: str = "",
        identity_query: str = "",
        customer_id: str = "",
        external_userid: str = "",
        corp_id: str = "",
        wechat: str = "",
        source_type: str = "",
        plan_status: str = "",
        task_status: str = "",
        reason_code: str = "",
        identity_state: str = "",
    ) -> dict[str, Any]:
        start, end = _outreach_log_window(started_from, started_to)
        filters = {
            "identity_query": identity_query,
            "customer_id": customer_id,
            "external_userid": external_userid,
            "corp_id": corp_id,
            "wechat": wechat,
            "source_type": source_type,
            "plan_status": plan_status,
            "task_status": task_status,
            "reason_code": reason_code,
            "identity_state": identity_state,
        }
        records = [
            record
            for record in self._outreach_customer_log_records(
                started_from=start,
                started_to=end,
                aggregate_no_plan_events=True,
            )
            if _outreach_record_matches_filters(record, **filters)
        ]
        grouped: dict[str, dict[str, Any]] = {}
        unscoped_items: list[dict[str, Any]] = []
        for record in records:
            identity = record.get("identity") if isinstance(record.get("identity"), dict) else {}
            if not identity:
                unscoped_items.append(
                    {
                        "contact_key": _string(record.get("record_id")),
                        "identity_state": "incomplete",
                        "identity": {},
                        "records": [record],
                    }
                )
                continue
            contact_key = _outreach_contact_key(identity)
            group = grouped.setdefault(
                contact_key,
                {
                    "contact_key": contact_key,
                    "identity_state": "complete",
                    "identity": identity,
                    "records": [],
                },
            )
            group["records"].append(record)

        def build_customer_item(group: dict[str, Any]) -> dict[str, Any]:
            customer_records = list(group["records"])
            customer_records.sort(
                key=lambda item: (_string(item.get("created_at")), _string(item.get("record_id"))),
                reverse=True,
            )
            summaries = [item.get("task_summary") for item in customer_records if isinstance(item.get("task_summary"), dict)]
            task_summary = {
                key: sum(_int(summary.get(key)) for summary in summaries)
                for key in ("total", "handled", "sent", "sent_without_message_id", "consumed", "failed", "pending", "processing")
            }
            next_tasks = [
                summary.get("next_task")
                for summary in summaries
                if isinstance(summary.get("next_task"), dict) and summary.get("next_task")
            ]
            next_task = min(
                next_tasks,
                key=lambda item: (
                    _string(item.get("scheduled_at")),
                    _int(item.get("step_index")),
                    _string(item.get("task_id")),
                ),
                default={},
            )
            latest = customer_records[0] if customer_records else {}
            latest_identity = _merged_outreach_log_identity(
                customer_records,
                fallback=group["identity"],
            )
            customer_id_hints: list[str] = []
            for item in customer_records:
                record_identity = (
                    item.get("identity")
                    if isinstance(item.get("identity"), dict)
                    else {}
                )
                customer_id = _string(record_identity.get("customer_id"))
                if customer_id:
                    customer_id_hints.append(customer_id)
            contact_key = _string(group.get("contact_key"))
            if group["identity_state"] == "complete":
                contact_key = _outreach_contact_key(
                    latest_identity,
                    customer_id_hints=customer_id_hints,
                )
            return {
                "contact_key": contact_key,
                "identity_state": group["identity_state"],
                "identity": latest_identity,
                "latest_at": _string(latest.get("created_at")),
                "latest_record": {
                    key: latest.get(key, "")
                    for key in ("record_id", "record_type", "plan_id", "source_type", "status", "reason_code", "plan_goal", "created_at")
                },
                "plan_count": sum(
                    max(1, _int(item.get("record_count"), 1))
                    for item in customer_records
                    if item.get("record_type") == "plan"
                ),
                "no_plan_count": sum(
                    max(1, _int(item.get("record_count"), 1))
                    for item in customer_records
                    if item.get("record_type") == "no_plan"
                ),
                "task_summary": task_summary,
                "next_task": next_task,
            }

        customer_items = [build_customer_item(group) for group in grouped.values()]
        customer_items.extend(build_customer_item(item) for item in unscoped_items)
        customer_items.sort(
            key=lambda item: (_string(item.get("latest_at")), _string(item.get("contact_key"))),
            reverse=True,
        )
        cursor_value = self._decode_first_day_cursor(cursor)
        if cursor_value:
            customer_items = [
                item
                for item in customer_items
                if (
                    _string(item.get("latest_at")),
                    _string(item.get("contact_key")),
                ) < cursor_value
            ]
        page_size = max(1, min(int(limit or 50), 200))
        has_more = len(customer_items) > page_size
        items = customer_items[:page_size]
        next_cursor = ""
        if has_more and items:
            next_cursor = self._encode_first_day_cursor(
                _string(items[-1].get("latest_at")),
                _string(items[-1].get("contact_key")),
            )
        metrics = {
            "customer_count": len(grouped),
            "identity_incomplete_count": len(unscoped_items),
            "plan_count": sum(
                max(1, _int(item.get("record_count"), 1))
                for item in records
                if item.get("record_type") == "plan"
            ),
            "no_plan_count": sum(
                max(1, _int(item.get("record_count"), 1))
                for item in records
                if item.get("record_type") == "no_plan"
            ),
            "task_count": sum(_int(record.get("task_summary", {}).get("total")) for record in records if isinstance(record.get("task_summary"), dict)),
            "sent_count": sum(_int(record.get("task_summary", {}).get("sent")) for record in records if isinstance(record.get("task_summary"), dict)),
            "sent_without_message_id_count": sum(_int(record.get("task_summary", {}).get("sent_without_message_id")) for record in records if isinstance(record.get("task_summary"), dict)),
            "consumed_count": sum(_int(record.get("task_summary", {}).get("consumed")) for record in records if isinstance(record.get("task_summary"), dict)),
            "pending_count": sum(_int(record.get("task_summary", {}).get("pending")) for record in records if isinstance(record.get("task_summary"), dict)),
            "processing_count": sum(_int(record.get("task_summary", {}).get("processing")) for record in records if isinstance(record.get("task_summary"), dict)),
            "failed_count": sum(_int(record.get("task_summary", {}).get("failed")) for record in records if isinstance(record.get("task_summary"), dict)),
        }
        return redact_first_day_log_value(
            {
                "range": {"started_from": start, "started_to": end, "timezone": "Asia/Shanghai"},
                "filters": filters,
                "metrics": metrics,
                "items": items,
                "next_cursor": next_cursor,
                "has_more": has_more,
            }
        )

    def get_outreach_customer_log(
        self,
        contact_key: str,
        *,
        started_from: str = "",
        started_to: str = "",
    ) -> dict[str, Any]:
        identity = _decode_outreach_contact_key(contact_key)
        if not identity:
            return {}
        start, end = _outreach_log_window(started_from, started_to)
        history = [
            record
            for record in self._outreach_customer_log_records(
                started_from=start,
                started_to=end,
                identity=identity,
            )
            if _outreach_contact_matches(
                record.get("identity") if isinstance(record.get("identity"), dict) else {},
                identity,
            )
        ]
        if not history:
            return {}
        history.sort(
            key=lambda item: (_string(item.get("created_at")), _string(item.get("record_id"))),
            reverse=True,
        )
        return redact_first_day_log_value(
            {
                "contact_key": contact_key,
                "identity": _merged_outreach_log_identity(
                    history,
                    fallback={
                    key: value
                    for key, value in identity.items()
                    if not key.startswith("_")
                    },
                ),
                "range": {"started_from": start, "started_to": end, "timezone": "Asia/Shanghai"},
                "history": history,
            }
        )

    def get_outreach_customer_log_plan(
        self,
        contact_key: str,
        plan_id: str,
    ) -> dict[str, Any]:
        identity = _decode_outreach_contact_key(contact_key)
        if not identity or not plan_id:
            return {}
        with self.store.connect() as conn:
            plan_row = conn.execute("SELECT * FROM outreach_plans WHERE id=?", (plan_id,)).fetchone()
            if not plan_row:
                return {}
            task_rows = conn.execute(
                "SELECT * FROM outreach_tasks WHERE plan_id=? ORDER BY step_index ASC", (plan_id,)
            ).fetchall()
            event_rows = conn.execute(
                "SELECT * FROM outreach_events WHERE plan_id=? ORDER BY created_at ASC LIMIT 200", (plan_id,)
            ).fetchall()
        plan = self._decode_outreach_plan(dict(plan_row))
        source_type = _outreach_source_type(plan)
        if source_type not in _AUTOMATIC_OUTREACH_SOURCE_TYPES or not _outreach_contact_matches(plan, identity):
            return {}
        plan_identity = _outreach_contact_identity(plan)
        tasks = [self._decode_outreach_task(dict(row)) for row in task_rows]
        events = [self._decode_outreach_event(dict(row)) for row in event_rows]
        for task in tasks:
            task["actual_send"] = bool(
                _string(task.get("status")) == "sent" and _string(task.get("system_msgid"))
            )
        source_snapshot = plan.pop("source_snapshot", {})
        workflow_run_id = _string(source_snapshot.get("workflow_run_id")) if isinstance(source_snapshot, dict) else ""
        technical: dict[str, Any] = {
            "source_snapshot": source_snapshot,
            "workflow_run_id": workflow_run_id,
        }
        if workflow_run_id:
            first_day_run = self.get_first_day_outreach_run(
                workflow_run_id,
                include_related=False,
            )
            if first_day_run:
                technical["first_day_run"] = first_day_run
        return redact_first_day_log_value(
            {
                "contact_key": contact_key,
                "identity": plan_identity,
                "source_type": source_type,
                "plan": plan,
                "reason_code": _outreach_log_reason({**plan, "source_snapshot": source_snapshot}, source_type),
                "task_summary": _outreach_task_summary(tasks),
                "tasks": tasks,
                "events": events,
                "technical": technical,
            }
        )

    def prune_first_day_outreach_runs(
        self,
        *,
        raw_days: int = 30,
        summary_days: int = 90,
    ) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        raw_cutoff = (now - timedelta(days=max(1, raw_days))).isoformat()
        summary_cutoff = (now - timedelta(days=max(raw_days + 1, summary_days))).isoformat()
        terminal = "('blocked','sent','cancelled','failed','completed')"
        with self.store.connect() as conn:
            old_rows = conn.execute(
                f"""
                SELECT workflow_run_id, plan_id
                FROM first_day_outreach_runs
                WHERE status IN {terminal} AND finished_at!='' AND finished_at<? AND raw_redacted_at=''
                """,
                (raw_cutoff,),
            ).fetchall()
            redacted_runs = 0
            redacted_plans = 0
            redacted_events = 0
            redacted_at = utc_now_iso()
            for row in old_rows:
                cursor = conn.execute(
                    """
                    UPDATE first_day_outreach_runs
                    SET input_snapshot_json='{}', workflow_json='{}', final_plan_json='{}',
                        raw_redacted_at=?, updated_at=?
                    WHERE workflow_run_id=? AND raw_redacted_at=''
                    """,
                    (redacted_at, redacted_at, row["workflow_run_id"]),
                )
                redacted_runs += int(cursor.rowcount or 0)
                plan_id = _string(row["plan_id"])
                if not plan_id:
                    continue
                plan_row = conn.execute(
                    "SELECT source_snapshot FROM outreach_plans WHERE id=?",
                    (plan_id,),
                ).fetchone()
                if plan_row:
                    snapshot = loads_dict(plan_row["source_snapshot"])
                    retained = {
                        "workflow_run_id": _string(snapshot.get("workflow_run_id")),
                        "trigger_context": snapshot.get("trigger_context") or {},
                        "retention_redacted_at": redacted_at,
                    }
                    conn.execute(
                        "UPDATE outreach_plans SET source_snapshot=?, updated_at=? WHERE id=?",
                        (dumps(retained), redacted_at, plan_id),
                    )
                    redacted_plans += 1
                event_cursor = conn.execute(
                    """
                    UPDATE outreach_events
                    SET payload_json='{}'
                    WHERE plan_id=? AND created_at<? AND payload_json!='{}'
                    """,
                    (plan_id, raw_cutoff),
                )
                redacted_events += int(event_cursor.rowcount or 0)
            deleted_cursor = conn.execute(
                f"""
                DELETE FROM first_day_outreach_runs
                WHERE status IN {terminal} AND finished_at!='' AND finished_at<?
                """,
                (summary_cutoff,),
            )
        return {
            "raw_redacted_runs": redacted_runs,
            "raw_redacted_plans": redacted_plans,
            "raw_redacted_events": redacted_events,
            "deleted_runs": int(deleted_cursor.rowcount or 0),
        }

    @classmethod
    def _decode_first_day_outreach_run(cls, row: dict[str, Any]) -> dict[str, Any]:
        for storage_field, output_field in cls._FIRST_DAY_RUN_JSON_FIELDS.items():
            row[output_field] = loads_dict(row.get(storage_field))
            row.pop(storage_field, None)
        return row

    @staticmethod
    def _encode_first_day_cursor(started_at: str, workflow_run_id: str) -> str:
        raw = json.dumps([started_at, workflow_run_id], separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_first_day_cursor(cursor: str) -> tuple[str, str] | None:
        if not cursor:
            return None
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            if isinstance(value, list) and len(value) == 2 and all(isinstance(item, str) for item in value):
                return value[0], value[1]
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        return None

    def touch_customer_message_time(self, memory_key: str, *, field: str, value: str | None = None) -> None:
        if field not in {
            "last_customer_message_at",
            "last_staff_message_at",
            "last_ai_reply_at",
            "last_manual_takeover_at",
            "last_outreach_at",
        }:
            raise ValueError(f"Unsupported customer time field: {field}")
        now = value or utc_now_iso()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO customer_memory (customer_id, portrait, basic_info, lifecycle_stage, updated_at)
                VALUES (?, '{}', '{}', '', ?)
                ON CONFLICT(customer_id) DO UPDATE SET
                    updated_at=excluded.updated_at
                """,
                (memory_key, now),
            )
            conn.execute(
                f"UPDATE customer_memory SET {field}=?, updated_at=? WHERE customer_id=?",
                (now, now, memory_key),
            )

    def update_customer_outreach_state(
        self,
        memory_key: str,
        *,
        outreach_status: str,
        outreach_plan_id: str = "",
        last_outreach_at: str = "",
    ) -> None:
        now = utc_now_iso()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO customer_memory (customer_id, portrait, basic_info, lifecycle_stage, updated_at)
                VALUES (?, '{}', '{}', '', ?)
                ON CONFLICT(customer_id) DO NOTHING
                """,
                (memory_key, now),
            )
            conn.execute(
                """
                UPDATE customer_memory
                SET outreach_status=?, outreach_plan_id=?, last_outreach_at=COALESCE(NULLIF(?, ''), last_outreach_at),
                    updated_at=?
                WHERE customer_id=?
                """,
                (outreach_status, outreach_plan_id, last_outreach_at, now, memory_key),
            )

    def list_outreach_candidates(
        self,
        *,
        limit: int = 50,
        silent_minutes_min: int = 60,
        outreach_status: str = "",
        lifecycle_stage: str = "",
        no_plan_only: bool = False,
        keyword: str = "",
    ) -> list[dict[str, Any]]:
        cutoff_72h = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        result_limit = max(1, min(limit, 2000))
        expands_after_query = bool(
            keyword.strip() or outreach_status or lifecycle_stage or no_plan_only
        )
        if keyword.strip():
            query_limit = 5000
        elif expands_after_query:
            query_limit = max(result_limit, min(result_limit * 10, 5000))
        else:
            query_limit = result_limit
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.customer_id, c.updated_at, c.external_userid, c.corp_id, c.user_id, c.wechat, c.title,
                    (SELECT created_at FROM messages m WHERE m.conversation_id=c.id AND m.role='user' ORDER BY created_at DESC LIMIT 1) AS conversation_last_customer_at,
                    (SELECT created_at FROM messages m WHERE m.conversation_id=c.id AND m.role='assistant' ORDER BY created_at DESC LIMIT 1) AS conversation_last_staff_at,
                    (SELECT content FROM messages m WHERE m.conversation_id=c.id AND m.role='user' ORDER BY created_at DESC LIMIT 1) AS last_customer_message,
                    (
                        SELECT MIN(c3.created_at) FROM conversations c3
                        WHERE c3.customer_id=c.customer_id AND c3.corp_id=c.corp_id
                          AND lower(c3.wechat)=lower(c.wechat) AND c3.external_userid=c.external_userid
                    ) AS sales_contact_started_at,
                    (
                        SELECT COUNT(*) FROM outreach_tasks t
                        JOIN outreach_plans p ON p.id=t.plan_id
                        WHERE p.customer_id=c.customer_id AND p.corp_id=c.corp_id
                          AND lower(p.wechat)=lower(c.wechat) AND p.external_userid=c.external_userid
                          AND t.status='sent' AND t.sent_at>=?
                    ) AS outreach_sent_count_72h
                FROM conversations c
                WHERE c.customer_id IS NOT NULL AND c.customer_id!='' AND c.wechat!=''
                  AND c.updated_at=(
                      SELECT MAX(c2.updated_at) FROM conversations c2
                      WHERE c2.customer_id=c.customer_id AND c2.corp_id=c.corp_id
                        AND lower(c2.wechat)=lower(c.wechat) AND c2.external_userid=c.external_userid
                  )
                ORDER BY c.updated_at DESC
                LIMIT ?
                """,
                (cutoff_72h, query_limit),
            ).fetchall()
            platform_names = _latest_platform_customer_names(conn, list(rows))
        prepared_rows: list[tuple[dict[str, Any], Any]] = []
        for row in rows:
            item = dict(row)
            platform_name_key = (
                _string(item.get("external_userid")).lower(),
                _string(item.get("wechat")).lower(),
            )
            item["platform_customer_name"] = platform_names.get(platform_name_key, "")
            if keyword and not _candidate_identity_matches_keyword(item, keyword):
                continue
            scope = build_customer_scope(
                corp_id=item.get("corp_id"),
                wechat=item.get("wechat"),
                external_userid=item.get("external_userid"),
                customer_id=item.get("customer_id"),
            )
            prepared_rows.append((item, scope))
        memory_loader = getattr(self, "load_memories", None)
        memory_by_contact = (
            memory_loader(
                [scope.sales_contact_key for _, scope in prepared_rows if scope.persistence_allowed]
            )
            if callable(memory_loader)
            else {}
        )
        items: list[dict[str, Any]] = []
        for item, scope in prepared_rows:
            memory = memory_by_contact.get(scope.sales_contact_key, {}) if scope.persistence_allowed else {}
            memory = memory if isinstance(memory, dict) else {}
            item["portrait"] = memory.get("portrait") if isinstance(memory.get("portrait"), dict) else {}
            item["basic_info"] = memory.get("basic_info") if isinstance(memory.get("basic_info"), dict) else {}
            item["lifecycle_stage"] = str(memory.get("lifecycle_stage") or "")
            latest_customer_message_at = _latest_iso_value(
                memory.get("last_customer_message_at"),
                item.get("conversation_last_customer_at"),
            )
            item["last_customer_message_at"] = latest_customer_message_at or _string(
                item.get("updated_at")
            )
            item["last_staff_message_at"] = str(memory.get("last_staff_message_at") or "")
            item["last_ai_reply_at"] = str(memory.get("last_ai_reply_at") or "")
            item["latest_outbound_message_at"] = _latest_iso_value(
                item.get("last_staff_message_at"),
                item.get("last_ai_reply_at"),
                item.get("conversation_last_staff_at"),
            )
            item["last_manual_takeover_at"] = str(memory.get("last_manual_takeover_at") or "")
            item["last_outreach_at"] = str(memory.get("last_outreach_at") or "")
            item["outreach_status"] = str(memory.get("outreach_status") or "none")
            item["outreach_plan_id"] = str(memory.get("outreach_plan_id") or "")
            events = memory.get("history_events") if isinstance(memory.get("history_events"), list) else []
            item["latest_event_summary"] = str((events[-1] if events else {}).get("summary") or "")
            if keyword and not _outreach_candidate_matches_keyword(item, keyword):
                continue
            if outreach_status and item["outreach_status"] != outreach_status:
                continue
            if lifecycle_stage and item["lifecycle_stage"] != lifecycle_stage:
                continue
            if no_plan_only and item["outreach_plan_id"]:
                continue
            item["silent_minutes"] = _silent_minutes(item.get("last_customer_message_at"))
            item["reply_wait_minutes"] = _silent_minutes(item.get("latest_outbound_message_at"))
            latest_customer = _parse_iso(item.get("last_customer_message_at"))
            latest_outbound = _parse_iso(item.get("latest_outbound_message_at"))
            item["awaiting_customer_reply"] = bool(
                latest_customer
                and latest_outbound
                and latest_outbound > latest_customer
            )
            if item["silent_minutes"] >= silent_minutes_min:
                items.append(item)
        items.sort(
            key=lambda item: (
                0 if item.get("awaiting_customer_reply") else 1,
                -int(item.get("reply_wait_minutes") or 0),
                -int(item.get("silent_minutes") or 0),
                _string(item.get("updated_at")),
            )
        )
        return items[:result_limit]

    def list_first_day_sop_contact_candidates(
        self,
        *,
        limit: int = 200,
        since: str = "",
    ) -> list[dict[str, Any]]:
        since_value = since or (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        safe_limit = max(1, min(int(limit or 200), 2000))
        try:
            with self.store.connect() as conn:
                rows = conn.execute(
                    """
                    WITH candidates AS (
                        SELECT customer_id, external_userid, corp_id,
                               MAX(user_id) AS user_id,
                               MAX(wechat) AS wechat,
                               MIN(created_at) AS sales_contact_started_at,
                               MAX(updated_at) AS updated_at,
                               MAX(created_at) AS latest_sop_task_at
                        FROM sop_send_tasks
                        WHERE created_at>=?
                          AND customer_id<>''
                          AND external_userid<>''
                          AND corp_id<>''
                          AND wechat<>''
                          AND (
                            sop_pack_name LIKE '%加微%'
                            OR sop_pack_id LIKE '%add_wecom%'
                            OR trigger_source IN ('third_party_sop_pending', 'platform_auto_opening')
                          )
                        GROUP BY corp_id, lower(wechat), external_userid, customer_id
                    )
                    SELECT COALESCE(
                               (
                                   SELECT c.customer_id
                                   FROM conversations c
                                   WHERE c.corp_id=candidates.corp_id
                                     AND lower(c.wechat)=lower(candidates.wechat)
                                     AND lower(c.external_userid)=lower(candidates.external_userid)
                                     AND c.customer_id<>''
                                     AND lower(c.customer_id)<>lower(candidates.external_userid)
                                   ORDER BY c.updated_at DESC
                                   LIMIT 1
                               ),
                               candidates.customer_id
                           ) AS customer_id,
                           external_userid, corp_id, user_id, wechat,
                           sales_contact_started_at, updated_at, latest_sop_task_at
                    FROM candidates
                    ORDER BY latest_sop_task_at DESC
                    LIMIT ?
                    """,
                    (since_value, safe_limit),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            rows = []
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.update(
                {
                    "title": "",
                    "platform_customer_name": "",
                    "portrait": {},
                    "basic_info": {},
                    "lifecycle_stage": "",
                    "last_customer_message": "",
                    "last_customer_message_at": "",
                    "last_staff_message_at": "",
                    "last_ai_reply_at": "",
                    "latest_outbound_message_at": "",
                    "last_manual_takeover_at": "",
                    "last_outreach_at": "",
                    "outreach_status": "none",
                    "outreach_plan_id": "",
                    "latest_event_summary": "",
                    "silent_minutes": 0,
                    "reply_wait_minutes": 0,
                    "awaiting_customer_reply": False,
                    "candidate_source": "sop_send_tasks",
                }
            )
            items.append(item)
        return items

    def outreach_dashboard_stats(self, *, now: str | None = None) -> dict[str, Any]:
        current = _parse_iso(now) if now else datetime.now(timezone.utc)
        current = current or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        beijing = timezone(timedelta(hours=8))
        current_beijing = current.astimezone(beijing)
        day_start = current_beijing.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        start_utc = day_start.astimezone(timezone.utc).isoformat()
        end_utc = day_end.astimezone(timezone.utc).isoformat()
        now_utc = current.astimezone(timezone.utc).isoformat()
        auto_plan = f"{self.store.json_text('source_snapshot', '$.trigger_context.activation_policy')}='auto_approved'"
        auto_joined_plan = f"{self.store.json_text('p.source_snapshot', '$.trigger_context.activation_policy')}='auto_approved'"

        with self.store.connect() as conn:
            platform_tasks_today = conn.execute(
                """
                SELECT COUNT(*) FROM sop_events
                WHERE event_type='sop_platform_task' AND received_at>=? AND received_at<?
                """,
                (start_utc, end_utc),
            ).fetchone()
            platform_tasks_today = int(scalar(platform_tasks_today))
            plans_today = conn.execute(
                f"SELECT COUNT(*) FROM outreach_plans WHERE {auto_plan} AND created_at>=? AND created_at<?",
                (start_utc, end_utc),
            ).fetchone()
            plans_today = int(scalar(plans_today))
            plan_rows = conn.execute(
                f"""
                SELECT status, COUNT(*) AS count
                FROM outreach_plans
                WHERE {auto_plan}
                GROUP BY status
                """
            ).fetchall()
            task_rows = conn.execute(
                f"""
                SELECT t.status, COUNT(*) AS count
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE {auto_joined_plan}
                GROUP BY t.status
                """
            ).fetchall()
            due_tasks = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE {auto_joined_plan}
                  AND t.status='pending'
                  AND t.scheduled_at<=?
                  AND p.status IN ('active', 'waiting')
                """,
                (now_utc,),
            ).fetchone()
            due_tasks = int(scalar(due_tasks))
            sent_today = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE {auto_joined_plan}
                  AND t.status='sent'
                  AND t.sent_at>=? AND t.sent_at<?
                """,
                (start_utc, end_utc),
            ).fetchone()
            sent_today = int(scalar(sent_today))
            event_rows = conn.execute(
                f"""
                SELECT e.event_type, COUNT(*) AS count
                FROM outreach_events e
                JOIN outreach_plans p ON p.id=e.plan_id
                WHERE {auto_joined_plan}
                  AND e.created_at>=? AND e.created_at<?
                GROUP BY e.event_type
                """,
                (start_utc, end_utc),
            ).fetchall()
            next_due = conn.execute(
                f"""
                SELECT t.scheduled_at, t.customer_id, t.id AS task_id
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE {auto_joined_plan}
                  AND t.status='pending'
                  AND p.status IN ('active', 'waiting')
                ORDER BY t.scheduled_at ASC
                LIMIT 1
                """
            ).fetchone()
            last_sent = conn.execute(
                f"""
                SELECT t.sent_at, t.customer_id, t.id AS task_id
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE {auto_joined_plan} AND t.status='sent'
                ORDER BY t.sent_at DESC
                LIMIT 1
                """
            ).fetchone()
            outcome_stats = self._outreach_outcome_stats(conn, current=current)
        plan_counts = {str(row["status"]): int(row["count"]) for row in plan_rows}
        task_counts = {str(row["status"]): int(row["count"]) for row in task_rows}
        event_counts = {str(row["event_type"]): int(row["count"]) for row in event_rows}
        stopped_today = (
            event_counts.get("task_skipped_customer_replied", 0)
            + event_counts.get("task_skipped_order_state_changed", 0)
        )
        return {
            "generated_at": now_utc,
            "timezone": "Asia/Shanghai",
            "metrics": {
                "platform_tasks_today": platform_tasks_today,
                "personalized_plans_today": plans_today,
                "active_plans": plan_counts.get("active", 0) + plan_counts.get("waiting", 0),
                "pending_tasks": task_counts.get("pending", 0),
                "due_tasks": due_tasks,
                "sent_today": sent_today,
                "stopped_today": stopped_today,
                "retry_today": event_counts.get("before_send_check_failed", 0),
                "failed_today": event_counts.get("task_failed", 0),
            },
            "plan_status_counts": plan_counts,
            "task_status_counts": task_counts,
            "event_counts_today": event_counts,
            "outcomes": outcome_stats,
            "next_due": dict(next_due) if next_due else {},
            "last_sent": dict(last_sent) if last_sent else {},
        }

    def _outreach_outcome_stats(self, conn: Any, *, current: datetime) -> dict[str, Any]:
        window_start = (current - timedelta(days=30)).astimezone(timezone.utc).isoformat()
        auto_plan = f"{self.store.json_text('p.source_snapshot', '$.trigger_context.activation_policy')}='auto_approved'"
        sent_rows = conn.execute(
            f"""
            SELECT t.id, t.plan_id, t.sent_at, t.content_sources,
                   p.customer_id, p.corp_id, p.wechat, p.external_userid
            FROM outreach_tasks t
            JOIN outreach_plans p ON p.id=t.plan_id
            WHERE {auto_plan} AND t.status='sent' AND t.sent_at>=?
            ORDER BY t.sent_at ASC
            """,
            (window_start,),
        ).fetchall()
        message_rows = conn.execute(
            """
            SELECT m.created_at, c.customer_id, c.corp_id, c.wechat, c.external_userid
            FROM messages m
            JOIN conversations c ON c.id=m.conversation_id
            WHERE m.role='user' AND m.created_at>=?
            ORDER BY m.created_at ASC
            """,
            (window_start,),
        ).fetchall()
        run_rows = conn.execute(
            """
            SELECT r.created_at, r.error, r.output_snapshot,
                   c.customer_id, c.corp_id, c.wechat, c.external_userid
            FROM runs r
            JOIN conversations c ON c.id=r.conversation_id
            WHERE r.created_at>=?
            ORDER BY r.created_at ASC
            """,
            (window_start,),
        ).fetchall()
        payment_rows = conn.execute(
            f"""
            SELECT e.created_at, e.payload_json,
                   p.customer_id, p.corp_id, p.wechat, p.external_userid
            FROM outreach_events e
            JOIN outreach_plans p ON p.id=e.plan_id
            WHERE {auto_plan}
              AND e.event_type='task_skipped_order_state_changed'
              AND e.created_at>=?
            """,
            (window_start,),
        ).fetchall()
        safety_rows = conn.execute(
            f"""
            SELECT e.event_type, COUNT(*) AS count
            FROM outreach_events e
            JOIN outreach_plans p ON p.id=e.plan_id
            WHERE {auto_plan}
              AND e.created_at>=?
              AND e.event_type IN ('task_skipped_order_state_changed', 'task_failed')
            GROUP BY e.event_type
            """,
            (window_start,),
        ).fetchall()

        def contact_key(row: Any) -> tuple[str, str, str]:
            external_userid = _string(row["external_userid"]).lower()
            platform_customer_id = _string(row["customer_id"]).lower()
            identity = (
                f"external:{external_userid}"
                if external_userid
                else f"platform_customer:{platform_customer_id}"
                if platform_customer_id
                else ""
            )
            return (
                _string(row["corp_id"]).lower(),
                _string(row["wechat"]).lower(),
                identity,
            )

        customer_messages: dict[tuple[str, str, str], list[datetime]] = {}
        for row in message_rows:
            parsed = _parse_iso(_string(row["created_at"]))
            if parsed:
                customer_messages.setdefault(contact_key(row), []).append(parsed.astimezone(timezone.utc))

        successful_runs: dict[tuple[str, str, str], list[datetime]] = {}
        for row in run_rows:
            if _string(row["error"]):
                continue
            output = loads_dict(row["output_snapshot"])
            messages = output.get("reply_messages")
            if not isinstance(messages, list):
                messages = output.get("http_response_reply_messages")
            parsed = _parse_iso(_string(row["created_at"]))
            if isinstance(messages, list) and messages and parsed:
                successful_runs.setdefault(contact_key(row), []).append(parsed.astimezone(timezone.utc))

        sent_count = len(sent_rows)
        replied_24h = 0
        replied_72h = 0
        ai_resumed_72h = 0
        value_only_count = 0
        asset_count = 0
        asset_reply_count = 0
        repeated_assets = 0
        unique_contacts: set[tuple[str, str, str]] = set()
        seen_assets: set[tuple[tuple[str, str, str], str]] = set()
        plan_angles: dict[str, list[tuple[int, str]]] = {}
        first_sent_by_contact: dict[tuple[str, str, str], datetime] = {}

        for row in sent_rows:
            sent_at = _parse_iso(_string(row["sent_at"]))
            if not sent_at:
                continue
            sent_at = sent_at.astimezone(timezone.utc)
            key = contact_key(row)
            unique_contacts.add(key)
            first_sent_by_contact[key] = min(first_sent_by_contact.get(key, sent_at), sent_at)
            has_reply_24h = any(
                sent_at < item <= sent_at + timedelta(hours=24)
                for item in customer_messages.get(key, [])
            )
            has_reply_72h = any(
                sent_at < item <= sent_at + timedelta(hours=72)
                for item in customer_messages.get(key, [])
            )
            replied_24h += int(has_reply_24h)
            replied_72h += int(has_reply_72h)
            ai_resumed_72h += int(
                any(
                    sent_at < item <= sent_at + timedelta(hours=72)
                    for item in successful_runs.get(key, [])
                )
            )

            metadata: dict[str, Any] = {}
            resolved_asset: dict[str, Any] = {}
            for item in loads_list(row["content_sources"]):
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("outreach_task_metadata"), dict):
                    metadata = item["outreach_task_metadata"]
                if isinstance(item.get("resolved_asset"), dict):
                    resolved_asset = item["resolved_asset"]
            value_only_count += int(_string(metadata.get("content_mode")) == "value_only")
            plan_angles.setdefault(_string(row["plan_id"]), []).append(
                (
                    _int(metadata.get("normalized_delay_minutes"), 0),
                    _string(metadata.get("persuasion_angle")),
                )
            )
            asset_key = _string(resolved_asset.get("document_id") or resolved_asset.get("url"))
            if asset_key:
                asset_count += 1
                asset_reply_count += int(has_reply_72h)
                scoped_asset = (key, asset_key)
                repeated_assets += int(scoped_asset in seen_assets)
                seen_assets.add(scoped_asset)

        repeated_angle_pairs = 0
        angle_pairs = 0
        for items in plan_angles.values():
            ordered = [angle for _, angle in sorted(items) if angle]
            for previous, current_angle in zip(ordered, ordered[1:]):
                angle_pairs += 1
                repeated_angle_pairs += int(previous == current_angle)

        deposit_contacts: set[tuple[str, str, str]] = set()
        for row in payment_rows:
            payload = loads_dict(row["payload_json"])
            paid = _string(payload.get("deposit_state")) in {
                "paid_by_order",
                "paid_by_screenshot",
                "paid",
            } or bool(payload.get("prepay_paid"))
            paid_at = _parse_iso(_string(row["created_at"]))
            key = contact_key(row)
            first_sent = first_sent_by_contact.get(key)
            if paid and paid_at and first_sent and first_sent < paid_at <= first_sent + timedelta(days=7):
                deposit_contacts.add(key)

        safety_counts = {_string(row["event_type"]): int(row["count"]) for row in safety_rows}

        def rate(value: int, denominator: int) -> float:
            return round(value / denominator, 4) if denominator else 0.0

        return {
            "window_days": 30,
            "sent_tasks": sent_count,
            "contact_count": len(unique_contacts),
            "reopened_24h_count": replied_24h,
            "reopened_24h_rate": rate(replied_24h, sent_count),
            "reopened_72h_count": replied_72h,
            "reopened_72h_rate": rate(replied_72h, sent_count),
            "ai_resumed_72h_count": ai_resumed_72h,
            "ai_resumed_72h_rate": rate(ai_resumed_72h, sent_count),
            "deposit_7d_count": len(deposit_contacts),
            "deposit_7d_rate": rate(len(deposit_contacts), len(unique_contacts)),
            "average_touches_per_customer": round(sent_count / len(unique_contacts), 2) if unique_contacts else 0.0,
            "value_only_count": value_only_count,
            "value_only_rate": rate(value_only_count, sent_count),
            "repeated_angle_pairs": repeated_angle_pairs,
            "repeated_angle_rate": rate(repeated_angle_pairs, angle_pairs),
            "asset_send_count": asset_count,
            "asset_repeat_count": repeated_assets,
            "asset_repeat_rate": rate(repeated_assets, asset_count),
            "asset_reply_72h_count": asset_reply_count,
            "asset_reply_72h_rate": rate(asset_reply_count, asset_count),
            "safety_stop_count": safety_counts.get("task_skipped_order_state_changed", 0),
            "failure_count": safety_counts.get("task_failed", 0),
            "complaint_rate": None,
            "complaint_measurement": "unavailable_without_structured_event",
            "effective_reply_measurement": "customer_reply_and_successful_ai_chain_proxy",
        }

    def create_outreach_plan(
        self,
        *,
        customer_id: str,
        corp_id: str,
        user_id: str,
        wechat: str,
        external_userid: str,
        customer_stage: str,
        stall_reason: str,
        customer_psychology: str,
        plan_goal: str,
        source_snapshot: dict[str, Any],
        tasks: list[dict[str, Any]],
        sop_plan_id: str = "",
        workflow_run_id: str = "",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        plan_id = str(uuid4())
        task_ids: list[str] = []
        created_tasks: list[dict[str, Any]] = []
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO outreach_plans
                    (id, sop_plan_id, customer_id, corp_id, user_id, wechat, external_userid, status,
                     customer_stage, stall_reason, customer_psychology, plan_goal,
                     source_snapshot, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    sop_plan_id,
                    customer_id,
                    corp_id,
                    user_id,
                    wechat,
                    external_userid,
                    customer_stage,
                    stall_reason,
                    customer_psychology,
                    plan_goal,
                    dumps(source_snapshot),
                    now,
                    now,
                ),
            )
            for index, task in enumerate(tasks, start=1):
                task_id = str(uuid4())
                task_ids.append(task_id)
                stored_task = {
                    **dict(task),
                    "id": task_id,
                    "plan_id": plan_id,
                    "customer_id": customer_id,
                    "step_index": int(task.get("step_index") or index),
                    "scheduled_at": str(task.get("scheduled_at") or now),
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                }
                created_tasks.append(stored_task)
                conn.execute(
                    """
                    INSERT INTO outreach_tasks
                        (id, plan_id, customer_id, step_index, scheduled_at, status, intent, message_goal,
                         content_sources, reply_messages_json, before_send_check, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        plan_id,
                        customer_id,
                        int(task.get("step_index") or index),
                        str(task.get("scheduled_at") or now),
                        str(task.get("intent") or ""),
                        str(task.get("message_goal") or ""),
                        dumps(task.get("content_sources") or []),
                        dumps(task.get("reply_messages") or []),
                        1 if task.get("before_send_check", True) else 0,
                        now,
                        now,
                    ),
                )
            if workflow_run_id:
                conn.execute(
                    """
                    UPDATE first_day_outreach_runs
                    SET plan_id=?, first_task_id=?, second_task_id=?, updated_at=?
                    WHERE workflow_run_id=?
                    """,
                    (
                        plan_id,
                        task_ids[0] if task_ids else "",
                        task_ids[1] if len(task_ids) > 1 else "",
                        now,
                        workflow_run_id,
                    ),
                )
        try:
            self.add_outreach_event(
                plan_id=plan_id,
                task_id="",
                customer_id=customer_id,
                event_type="plan_created",
                event_summary="AI generated outreach plan",
                payload=source_snapshot,
            )
        except Exception:
            pass
        observe_identity = getattr(self, "observe_customer_identity", None)
        if callable(observe_identity):
            try:
                observe_identity(
                    corp_id=corp_id,
                    wechat=wechat,
                    external_userid=external_userid,
                    customer_id=customer_id,
                    user_id=user_id,
                    customer_add_wechat_id=_string(source_snapshot.get("customer_add_wechat_id")),
                    source="outreach_plan",
                )
            except Exception:
                pass
        scope = build_customer_scope(
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            customer_id=customer_id,
        )
        if scope.persistence_allowed:
            try:
                self.update_customer_outreach_state(scope.sales_contact_key, outreach_status="draft", outreach_plan_id=plan_id)
            except Exception:
                pass
        try:
            return self.get_outreach_plan(plan_id)
        except Exception:
            return {
                "plan": {
                    "id": plan_id,
                    "sop_plan_id": sop_plan_id,
                    "customer_id": customer_id,
                    "corp_id": corp_id,
                    "user_id": user_id,
                    "wechat": wechat,
                    "external_userid": external_userid,
                    "status": "draft",
                    "customer_stage": customer_stage,
                    "stall_reason": stall_reason,
                    "customer_psychology": customer_psychology,
                    "plan_goal": plan_goal,
                    "source_snapshot": source_snapshot,
                    "created_at": now,
                    "updated_at": now,
                },
                "tasks": created_tasks,
                "events": [],
            }

    def get_outreach_plan(self, plan_id: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            plan = conn.execute("SELECT * FROM outreach_plans WHERE id=?", (plan_id,)).fetchone()
            tasks = conn.execute(
                "SELECT * FROM outreach_tasks WHERE plan_id=? ORDER BY step_index ASC",
                (plan_id,),
            ).fetchall()
            events = conn.execute(
                "SELECT * FROM outreach_events WHERE plan_id=? ORDER BY created_at DESC LIMIT 100",
                (plan_id,),
            ).fetchall()
        if not plan:
            return {}
        return {
            "plan": self._decode_outreach_plan(dict(plan)),
            "tasks": [self._decode_outreach_task(dict(row)) for row in tasks],
            "events": [self._decode_outreach_event(dict(row)) for row in events],
        }

    def get_active_outreach_plan_for_customer(
        self,
        customer_id: str,
        *,
        corp_id: str = "",
        wechat: str = "",
        external_userid: str = "",
    ) -> dict[str, Any]:
        if not wechat:
            return {}
        clauses = [
            "customer_id=?",
            "lower(wechat)=lower(?)",
            "status IN ('draft', 'active', 'waiting', 'paused')",
            "EXISTS (SELECT 1 FROM outreach_tasks active_task "
            "WHERE active_task.plan_id=outreach_plans.id "
            "AND active_task.status IN ('pending', 'checking', 'check_failed', 'sending'))",
        ]
        params: list[Any] = [customer_id, wechat]
        if corp_id:
            clauses.append("corp_id=?")
            params.append(corp_id)
        if external_userid:
            clauses.append("external_userid=?")
            params.append(external_userid)
        with self.store.connect() as conn:
            row = conn.execute(
                f"""
                SELECT id FROM outreach_plans
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        return self.get_outreach_plan(row["id"]) if row else {}

    def get_latest_completed_outreach_plan_for_customer(
        self,
        customer_id: str,
        *,
        corp_id: str = "",
        wechat: str = "",
        external_userid: str = "",
    ) -> dict[str, Any]:
        if not wechat:
            return {}
        clauses = [
            "customer_id=?",
            "lower(wechat)=lower(?)",
            "status='completed'",
        ]
        params: list[Any] = [customer_id, wechat]
        if corp_id:
            clauses.append("corp_id=?")
            params.append(corp_id)
        if external_userid:
            clauses.append("external_userid=?")
            params.append(external_userid)
        with self.store.connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM outreach_plans
                WHERE {' AND '.join(clauses)}
                ORDER BY completed_at DESC, created_at DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        return self._decode_outreach_plan(dict(row)) if row else {}

    def outreach_plan_has_remaining_tasks(self, plan_id: str) -> bool:
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM outreach_tasks
                WHERE plan_id=?
                  AND status IN ('pending', 'checking', 'check_failed', 'sending')
                """,
                (plan_id,),
            ).fetchone()
        return int(scalar(row)) > 0

    def has_outreach_evaluation_fingerprint(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str,
        conversation_fingerprint: str,
    ) -> bool:
        if not customer_id or not wechat or not conversation_fingerprint:
            return False
        with self.store.connect() as conn:
            plan_rows = conn.execute(
                """
                SELECT source_snapshot
                FROM outreach_plans
                WHERE customer_id=? AND corp_id=? AND lower(wechat)=lower(?) AND external_userid=?
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (customer_id, corp_id, wechat, external_userid),
            ).fetchall()
            event_rows = conn.execute(
                """
                SELECT payload_json
                FROM outreach_events
                WHERE customer_id=? AND event_type='plan_rejected'
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (customer_id,),
            ).fetchall()
        for row in plan_rows:
            snapshot = loads_dict(row["source_snapshot"])
            trigger = snapshot.get("trigger_context") if isinstance(snapshot.get("trigger_context"), dict) else {}
            if _string(trigger.get("conversation_fingerprint")) == conversation_fingerprint:
                return True
        for row in event_rows:
            payload = loads_dict(row["payload_json"])
            identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
            trigger = payload.get("trigger_context") if isinstance(payload.get("trigger_context"), dict) else {}
            if (
                _string(identity.get("corp_id")) == corp_id
                and _string(identity.get("wechat")) == wechat
                and _string(identity.get("external_userid")) == external_userid
                and _string(trigger.get("conversation_fingerprint")) == conversation_fingerprint
            ):
                return True
        return False

    def count_outreach_plans_for_trigger_between(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str,
        trigger_type: str,
        started_at: str,
        ended_at: str,
    ) -> int:
        if not all(
            (
                customer_id,
                corp_id,
                wechat,
                external_userid,
                trigger_type,
                started_at,
                ended_at,
            )
        ):
            return 0
        trigger_expression = self.store.json_text(
            "source_snapshot",
            "$.trigger_context.trigger_type",
        )
        with self.store.connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM outreach_plans
                WHERE customer_id=?
                  AND corp_id=?
                  AND lower(wechat)=lower(?)
                  AND external_userid=?
                  AND {trigger_expression}=?
                  AND created_at>=?
                  AND created_at<?
                """,
                (
                    customer_id,
                    corp_id,
                    wechat,
                    external_userid,
                    trigger_type,
                    started_at,
                    ended_at,
                ),
            ).fetchone()
        return int(scalar(row) or 0)

    def list_outreach_events(
        self,
        *,
        limit: int = 100,
        customer_id: str = "",
        plan_id: str = "",
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if customer_id:
            clauses.append("customer_id=?")
            params.append(customer_id)
        if plan_id:
            clauses.append("plan_id=?")
            params.append(plan_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 300)))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM outreach_events {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._decode_outreach_event(dict(row)) for row in rows]

    def get_outreach_customer_detail(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str = "",
        event_limit: int = 100,
    ) -> dict[str, Any]:
        scope = build_customer_scope(
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            customer_id=customer_id,
        )
        if not scope.persistence_allowed:
            return {}
        memory = self.load_memory(scope.sales_contact_key) or {
            "customer_id": scope.sales_contact_key,
            "portrait": {},
            "basic_info": {},
            "lifecycle_stage": "",
            "history_events": [],
        }
        clauses = ["p.customer_id=?", "p.wechat=?"]
        params: list[Any] = [customer_id, wechat]
        if corp_id:
            clauses.append("p.corp_id=?")
            params.append(corp_id)
        if external_userid:
            clauses.append("p.external_userid=?")
            params.append(external_userid)
        params.append(max(1, min(event_limit, 300)))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.*
                FROM outreach_events e
                JOIN outreach_plans p ON p.id=e.plan_id
                WHERE {' AND '.join(clauses)}
                ORDER BY e.created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return {
            "customer_id": customer_id,
            "external_userid": external_userid,
            "corp_id": corp_id,
            "wechat": wechat,
            "sales_contact_key": scope.sales_contact_key,
            "portrait": memory.get("portrait") if isinstance(memory.get("portrait"), dict) else {},
            "basic_info": memory.get("basic_info") if isinstance(memory.get("basic_info"), dict) else {},
            "lifecycle_stage": str(memory.get("lifecycle_stage") or ""),
            "profile_updated_at": str(memory.get("updated_at") or ""),
            "history_events": memory.get("history_events") if isinstance(memory.get("history_events"), list) else [],
            "outreach_events": [self._decode_outreach_event(dict(row)) for row in rows],
        }

    def update_outreach_plan_status(self, plan_id: str, status: str) -> dict[str, Any]:
        now = utc_now_iso()
        field = {
            "paused": "paused_at",
            "cancelled": "cancelled_at",
            "completed": "completed_at",
        }.get(status)
        with self.store.connect() as conn:
            if field:
                conn.execute(
                    f"UPDATE outreach_plans SET status=?, {field}=?, updated_at=? WHERE id=?",
                    (status, now, now, plan_id),
                )
            else:
                conn.execute(
                    "UPDATE outreach_plans SET status=?, updated_at=? WHERE id=?",
                    (status, now, plan_id),
                )
            plan = conn.execute(
                "SELECT customer_id, corp_id, wechat, external_userid FROM outreach_plans WHERE id=?",
                (plan_id,),
            ).fetchone()
        if plan:
            scope = build_customer_scope(
                corp_id=plan["corp_id"],
                wechat=plan["wechat"],
                external_userid=plan["external_userid"],
                customer_id=plan["customer_id"],
            )
            if scope.persistence_allowed:
                try:
                    self.update_customer_outreach_state(
                        scope.sales_contact_key,
                        outreach_status=status,
                        outreach_plan_id=plan_id if status not in {"cancelled", "completed"} else "",
                    )
                except Exception:
                    pass
        try:
            return self.get_outreach_plan(plan_id)
        except Exception:
            return {
                "plan": {
                    "id": plan_id,
                    "status": status,
                    "customer_id": str(plan["customer_id"] or "") if plan else "",
                    "corp_id": str(plan["corp_id"] or "") if plan else "",
                    "wechat": str(plan["wechat"] or "") if plan else "",
                    "external_userid": str(plan["external_userid"] or "") if plan else "",
                    "updated_at": now,
                },
                "tasks": [],
                "events": [],
            }

    def skip_remaining_outreach_tasks(
        self,
        plan_id: str,
        *,
        reason: str,
        exclude_task_id: str = "",
    ) -> int:
        now = utc_now_iso()
        query = """
            UPDATE outreach_tasks
            SET status='skipped', error_message=?, updated_at=?
            WHERE plan_id=?
              AND status IN ('pending', 'checking', 'check_failed')
        """
        params: list[Any] = [reason, now, plan_id]
        if exclude_task_id:
            query += " AND id!=?"
            params.append(exclude_task_id)
        with self.store.connect() as conn:
            cursor = conn.execute(query, params)
        return int(cursor.rowcount or 0)

    def cleanup_first_day_task_backlog(
        self,
        *,
        older_than: str,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        trigger_expression = self.store.json_text(
            "p.source_snapshot",
            "$.trigger_context.trigger_type",
        )
        with self.store.connect() as conn:
            broken_rows = conn.execute(
                f"""
                SELECT DISTINCT p.id AS plan_id, p.customer_id
                FROM outreach_plans p
                JOIN outreach_tasks failed ON failed.plan_id=p.id
                JOIN outreach_tasks pending ON pending.plan_id=p.id
                WHERE {trigger_expression}='first_day_opened_silence'
                  AND failed.status IN ('failed','skipped','cancelled')
                  AND pending.status IN ('pending','checking','check_failed')
                  AND pending.step_index>failed.step_index
                """
            ).fetchall()
            expired_rows = conn.execute(
                f"""
                SELECT DISTINCT p.id AS plan_id, p.customer_id
                FROM outreach_plans p
                JOIN outreach_tasks pending ON pending.plan_id=p.id
                WHERE {trigger_expression}='first_day_opened_silence'
                  AND p.created_at<?
                  AND pending.status IN ('pending','checking','check_failed')
                """,
                (older_than,),
            ).fetchall()
        reasons: dict[str, str] = {
            _string(row["plan_id"]): "preceding_first_day_task_not_sent"
            for row in broken_rows
        }
        for row in expired_rows:
            reasons.setdefault(_string(row["plan_id"]), "first_day_task_expired_24h")
        changed_tasks = 0
        if not dry_run:
            for plan_id, reason in reasons.items():
                changed_tasks += self.skip_remaining_outreach_tasks(plan_id, reason=reason)
                self.update_outreach_plan_status(plan_id, "cancelled")
                detail = self.get_outreach_plan(plan_id)
                plan = detail.get("plan") if isinstance(detail.get("plan"), dict) else {}
                source_snapshot = (
                    plan.get("source_snapshot")
                    if isinstance(plan.get("source_snapshot"), dict)
                    else {}
                )
                workflow_run_id = _string(source_snapshot.get("workflow_run_id"))
                if workflow_run_id:
                    self.update_first_day_outreach_run(
                        workflow_run_id,
                        status="cancelled",
                        reason_code=reason,
                        final_decision="cancelled_by_cleanup",
                        finished_at=utc_now_iso(),
                    )
                self.add_outreach_event(
                    plan_id=plan_id,
                    task_id="",
                    customer_id=_string(plan.get("customer_id")),
                    event_type="first_day_backlog_cleanup",
                    event_summary="Cancelled an invalid or expired first-day outreach backlog",
                    payload={"reason": reason, "older_than": older_than},
                )
        return {
            "dry_run": dry_run,
            "broken_plan_count": len(broken_rows),
            "expired_plan_count": len(expired_rows),
            "affected_plan_count": len(reasons),
            "changed_task_count": changed_tasks,
            "plans": [
                {"plan_id": plan_id, "reason": reason}
                for plan_id, reason in sorted(reasons.items())
            ],
        }

    def cancel_outreach_for_customer_reply(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str = "",
        request_id: str = "",
    ) -> dict[str, Any]:
        scope = build_customer_scope(
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            customer_id=customer_id,
        )
        if not scope.persistence_allowed:
            return {"cancelled_plans": 0, "skipped_tasks": 0}

        now = utc_now_iso()
        plan_ids: list[str] = []
        skipped_tasks = 0
        identity_sql, identity_params = _strict_identity_match(
            external_userid=external_userid,
            customer_id=customer_id,
        )
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id
                FROM outreach_plans
                WHERE corp_id=?
                  AND lower(wechat)=lower(?)
                  AND {identity_sql}
                  AND status IN ('draft', 'active', 'waiting', 'paused')
                """,
                (corp_id, wechat, *identity_params),
            ).fetchall()
            plan_ids = [_string(row["id"]) for row in rows if _string(row["id"])]
            for plan_id in plan_ids:
                cursor = conn.execute(
                    """
                    UPDATE outreach_tasks
                    SET status='skipped', error_message='customer_replied', updated_at=?
                    WHERE plan_id=? AND status IN ('pending', 'checking', 'check_failed')
                    """,
                    (now, plan_id),
                )
                skipped_tasks += int(cursor.rowcount or 0)
                conn.execute(
                    """
                    UPDATE outreach_plans
                    SET status='cancelled', cancelled_at=?, updated_at=?
                    WHERE id=?
                    """,
                    (now, now, plan_id),
                )

        for plan_id in plan_ids:
            self.add_outreach_event(
                plan_id=plan_id,
                task_id="",
                customer_id=customer_id,
                event_type="plan_cancelled_customer_replied",
                event_summary="Customer replied; remaining personalized outreach was cancelled",
                payload={"request_id": request_id, "skipped_tasks": skipped_tasks},
            )
            with self.store.connect() as conn:
                run_row = conn.execute(
                    """
                    SELECT workflow_run_id, started_at FROM first_day_outreach_runs
                    WHERE plan_id=? AND status IN ('running','created')
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (plan_id,),
                ).fetchone()
                started_at = _parse_iso(_string(run_row["started_at"])) if run_row else None
                duration_ms = max(
                    0,
                    round((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
                ) if started_at else 0
                conn.execute(
                    """
                    UPDATE first_day_outreach_runs
                    SET status='cancelled', reason_code='customer_replied',
                        final_decision='second_task_cancelled', duration_ms=?, finished_at=?, updated_at=?
                    WHERE plan_id=? AND status IN ('running','created')
                    """,
                    (duration_ms, now, now, plan_id),
                )
        if plan_ids:
            self.update_customer_outreach_state(
                scope.sales_contact_key,
                outreach_status="cancelled",
                outreach_plan_id="",
            )
        return {"cancelled_plans": len(plan_ids), "skipped_tasks": skipped_tasks}

    def recent_sop_delivery(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str = "",
        hours: int = 72,
    ) -> list[dict[str, Any]]:
        if not _string(wechat):
            return []
        since = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))).isoformat()
        identity_sql, identity_params = _strict_identity_match(
            external_userid=external_userid,
            customer_id=customer_id,
        )
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT sop_pack_id, sop_pack_name, sop_category, trigger_source,
                       reply_messages_json, sent_at
                FROM sop_send_tasks
                WHERE status='sent'
                  AND corp_id=?
                  AND lower(wechat)=lower(?)
                  AND {identity_sql}
                  AND sent_at>=?
                ORDER BY sent_at DESC
                LIMIT 20
                """,
                (corp_id, wechat, *identity_params, since),
            ).fetchall()
        return [
            {
                "sop_pack_id": _string(row["sop_pack_id"]),
                "sop_pack_name": _string(row["sop_pack_name"]),
                "sop_category": _string(row["sop_category"]),
                "trigger_source": _string(row["trigger_source"]),
                "sent_at": _string(row["sent_at"]),
                "reply_messages": loads_list(row["reply_messages_json"]),
            }
            for row in rows
        ]

    def recent_outreach_delivery(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str = "",
        hours: int = 24 * 30,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not _string(wechat):
            return []
        since = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))).isoformat()
        identity_sql, identity_params = _strict_identity_match(
            external_userid=external_userid,
            customer_id=customer_id,
            table_alias="p",
        )
        safe_limit = max(1, min(int(limit), 200))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT t.id, t.plan_id, t.step_index, t.sent_at,
                       t.reply_messages_json, t.content_sources
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE t.status='sent'
                  AND t.system_msgid<>''
                  AND t.sent_at>=?
                  AND p.corp_id=?
                  AND lower(p.wechat)=lower(?)
                  AND {identity_sql}
                ORDER BY t.sent_at DESC
                LIMIT ?
                """,
                (since, corp_id, wechat, *identity_params, safe_limit),
            ).fetchall()
            task_ids = [_string(row["id"]) for row in rows if _string(row["id"])]
            plan_ids = sorted({_string(row["plan_id"]) for row in rows if _string(row["plan_id"])})
            event_rows = []
            if task_ids and plan_ids:
                plan_placeholders = ",".join("?" for _ in plan_ids)
                task_placeholders = ",".join("?" for _ in task_ids)
                event_rows = conn.execute(
                    f"""
                    SELECT task_id, payload_json, created_at
                    FROM outreach_events
                    WHERE plan_id IN ({plan_placeholders})
                      AND task_id IN ({task_placeholders})
                      AND event_type='task_follow_script_selected'
                    ORDER BY created_at DESC
                    """,
                    (*plan_ids, *task_ids),
                ).fetchall()
        selections: dict[str, dict[str, Any]] = {}
        for row in event_rows:
            task_id = _string(row["task_id"])
            if task_id and task_id not in selections:
                selections[task_id] = loads_dict(row["payload_json"])
        output: list[dict[str, Any]] = []
        for row in rows:
            decoded = self._decode_outreach_task(dict(row))
            metadata: dict[str, Any] = {}
            for item in decoded.get("content_source_metadata") or []:
                if isinstance(item, dict) and isinstance(item.get("outreach_task_metadata"), dict):
                    metadata = dict(item["outreach_task_metadata"])
                    break
            sequence = metadata.get("follow_sequence") if isinstance(metadata.get("follow_sequence"), dict) else {}
            node = metadata.get("follow_sequence_node") if isinstance(metadata.get("follow_sequence_node"), dict) else {}
            selection = selections.get(_string(row["id"]), {})
            output.append(
                {
                    "task_id": _string(row["id"]),
                    "plan_id": _string(row["plan_id"]),
                    "step_index": _int(row["step_index"]),
                    "sent_at": _string(row["sent_at"]),
                    "reply_messages": decoded.get("reply_messages") or [],
                    "plan_mode": _string(metadata.get("plan_mode")),
                    "source_id": _string(selection.get("selected_mainline_source_id"))
                    or _string(metadata.get("source_id")),
                    "follow_sequence_id": _string(sequence.get("id")),
                    "follow_sequence_checksum": _string(sequence.get("checksum")),
                    "follow_sequence_node_id": _string(node.get("id")),
                    "selected_script_id": _string(selection.get("selected_script_id")),
                    "selected_script_code": _string(selection.get("selected_script_code")),
                    "value_dimension": _string(selection.get("value_dimension")),
                    "new_information": _string(selection.get("new_information")),
                    "conversion_action": _string(selection.get("conversion_action")),
                }
            )
        return output

    def outreach_sent_today_count(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str = "",
        now: str | None = None,
    ) -> int:
        if not _string(wechat):
            return 0
        current = _parse_iso(now) if now else datetime.now(timezone.utc)
        current = current or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        beijing = timezone(timedelta(hours=8))
        local = current.astimezone(beijing)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
        end = (
            local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        ).astimezone(timezone.utc).isoformat()
        identity_sql, identity_params = _strict_identity_match(
            external_userid=external_userid,
            customer_id=customer_id,
            table_alias="p",
        )
        with self.store.connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE t.status='sent'
                  AND t.sent_at>=? AND t.sent_at<?
                  AND p.corp_id=?
                  AND lower(p.wechat)=lower(?)
                  AND {identity_sql}
                """,
                (start, end, corp_id, wechat, *identity_params),
            ).fetchone()
        return int(scalar(row))

    def list_due_outreach_tasks(
        self,
        *,
        limit: int = 20,
        now: str | None = None,
        auto_approved_only: bool = False,
    ) -> list[dict[str, Any]]:
        now_value = now or utc_now_iso()
        auto_clause = (
            f"AND {self.store.json_text('p.source_snapshot', '$.trigger_context.activation_policy')}='auto_approved'"
            if auto_approved_only
            else ""
        )
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT t.*, p.corp_id, p.user_id, p.wechat, p.external_userid, p.status AS plan_status
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE t.status='pending'
                  AND t.scheduled_at<=?
                  AND p.status IN ('active', 'waiting')
                  AND COALESCE(
                      {self.store.json_text('p.source_snapshot', '$.trigger_context.trigger_type')},
                      ''
                  )<>'first_day_opened_silence'
                  {auto_clause}
                  AND NOT EXISTS (
                      SELECT 1
                      FROM outreach_tasks earlier
                      WHERE earlier.plan_id=t.plan_id
                        AND earlier.step_index<t.step_index
                        AND earlier.status IN ('pending', 'checking', 'check_failed')
                  )
                ORDER BY t.scheduled_at ASC
                LIMIT ?
                """,
                (now_value, max(1, min(limit, 100))),
            ).fetchall()
        return [self._decode_outreach_task(dict(row)) for row in rows]

    def list_due_first_day_tasks(
        self,
        *,
        limit: int = 20,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        now_value = now or utc_now_iso()
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT t.*, p.corp_id, p.user_id, p.wechat, p.external_userid,
                       p.status AS plan_status
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE t.status='pending'
                  AND t.scheduled_at<=?
                  AND p.status IN ('active', 'waiting')
                  AND {self.store.json_text('p.source_snapshot', '$.trigger_context.trigger_type')}='first_day_opened_silence'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM outreach_tasks earlier
                      WHERE earlier.plan_id=t.plan_id
                        AND earlier.step_index<t.step_index
                        AND earlier.status<>'sent'
                  )
                  AND (
                      t.step_index=1 OR (
                          SELECT COUNT(*)
                          FROM outreach_tasks earlier
                          WHERE earlier.plan_id=t.plan_id
                            AND earlier.step_index<t.step_index
                            AND earlier.status='sent'
                      )=t.step_index-1
                  )
                ORDER BY t.scheduled_at ASC
                LIMIT ?
                """,
                (now_value, max(1, min(limit, 100))),
            ).fetchall()
        return [self._decode_outreach_task(dict(row)) for row in rows]

    def claim_outreach_task(self, task_id: str) -> bool:
        now = utc_now_iso()
        with self.store.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE outreach_tasks
                SET status='checking', error_message='', updated_at=?
                WHERE id=? AND status='pending'
                """,
                (now, task_id),
            )
        return bool(cursor.rowcount)

    def reschedule_outreach_task(self, task_id: str, *, delay_seconds: int, error_message: str) -> dict[str, Any]:
        scheduled_at = (datetime.now(timezone.utc) + timedelta(seconds=max(1, delay_seconds))).isoformat()
        now = utc_now_iso()
        with self.store.connect() as conn:
            conn.execute(
                """
                UPDATE outreach_tasks
                SET status='pending', scheduled_at=?, error_message=?, updated_at=?
                WHERE id=?
                """,
                (scheduled_at, error_message, now, task_id),
            )
        return self.get_outreach_task(task_id)

    def recover_interrupted_outreach_tasks(self) -> int:
        now = utc_now_iso()
        with self.store.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE outreach_tasks
                SET status='pending', error_message='recovered_after_process_restart', updated_at=?
                WHERE status='checking'
                """,
                (now,),
            )
        return int(cursor.rowcount or 0)

    def get_outreach_task(self, task_id: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT t.*, p.corp_id, p.user_id, p.wechat, p.external_userid, p.status AS plan_status
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE t.id=?
                """,
                (task_id,),
            ).fetchone()
        return self._decode_outreach_task(dict(row)) if row else {}

    def update_outreach_task(
        self,
        task_id: str,
        *,
        status: str,
        reply_messages: list[dict[str, Any]] | None = None,
        sent_at: str = "",
        send_status: str = "",
        system_msgid: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self.store.connect() as conn:
            current = conn.execute("SELECT reply_messages_json FROM outreach_tasks WHERE id=?", (task_id,)).fetchone()
            existing_messages = loads_list(current["reply_messages_json"]) if current else []
            conn.execute(
                """
                UPDATE outreach_tasks
                SET status=?, reply_messages_json=?, sent_at=COALESCE(NULLIF(?, ''), sent_at),
                    send_status=COALESCE(NULLIF(?, ''), send_status),
                    system_msgid=COALESCE(NULLIF(?, ''), system_msgid),
                    error_message=?, updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    dumps(reply_messages if reply_messages is not None else existing_messages),
                    sent_at,
                    send_status,
                    system_msgid,
                    error_message,
                    now,
                    task_id,
                ),
            )
        return self.get_outreach_task(task_id)

    def add_outreach_event(
        self,
        *,
        plan_id: str,
        task_id: str,
        customer_id: str,
        event_type: str,
        event_summary: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_id = str(uuid4())
        with self.store.connect() as conn:
            event_payload = dict(payload or {})
            first_day_event = bool(event_payload.get("workflow_run_id"))
            if plan_id and not event_payload.get("workflow_run_id"):
                run_row = conn.execute(
                    """
                    SELECT workflow_run_id FROM first_day_outreach_runs
                    WHERE plan_id=? ORDER BY started_at DESC LIMIT 1
                    """,
                    (plan_id,),
                ).fetchone()
                if run_row:
                    event_payload["workflow_run_id"] = _string(run_row["workflow_run_id"])
                    first_day_event = True
            conn.execute(
                """
                INSERT INTO outreach_events
                    (id, plan_id, task_id, customer_id, event_type, event_summary, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    plan_id,
                    task_id,
                    customer_id,
                    event_type,
                    event_summary,
                    dumps(redact_first_day_log_value(event_payload) if first_day_event else event_payload),
                    utc_now_iso(),
                ),
            )
        return {"event_id": event_id}

    def recent_customer_context(
        self,
        customer_id: str,
        *,
        corp_id: str,
        wechat: str,
        external_userid: str = "",
    ) -> dict[str, Any]:
        scope = build_customer_scope(
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            customer_id=customer_id,
        )
        memory = self.load_memory(scope.sales_contact_key) if scope.persistence_allowed else None
        memory = memory or {"customer_id": scope.sales_contact_key, "history_events": []}
        identity_sql, identity_params = _strict_identity_match(
            external_userid=external_userid,
            customer_id=customer_id,
        )
        with self.store.connect() as conn:
            conversation = conn.execute(
                f"""
                SELECT id FROM conversations
                WHERE {identity_sql} AND corp_id=? AND lower(wechat)=lower(?)
                ORDER BY updated_at DESC LIMIT 1
                """,
                (*identity_params, corp_id, wechat),
            ).fetchone()
            messages = []
            if conversation:
                messages = conn.execute(
                    """
                    SELECT role, content, reply_messages, created_at
                    FROM messages
                    WHERE conversation_id=?
                    ORDER BY created_at DESC
                    LIMIT 10
                    """,
                    (conversation["id"],),
                ).fetchall()
        return {
            "memory": memory,
            "recent_messages": [
                {
                    "role": row["role"],
                    "content": row["content"],
                    "reply_messages": loads_list(row["reply_messages"]),
                    "created_at": row["created_at"],
                }
                for row in reversed(messages)
            ],
        }

    @staticmethod
    def _decode_outreach_plan(row: dict[str, Any]) -> dict[str, Any]:
        row["source_snapshot"] = loads_dict(row.get("source_snapshot"))
        ai_result = row["source_snapshot"].get("ai_result") if isinstance(row["source_snapshot"], dict) else {}
        if isinstance(ai_result, dict):
            for key in (
                "conversion_stage",
                "customer_type",
                "last_explicit_intent",
                "last_interaction_summary",
                "next_best_action",
                "suppress_reason",
                "core_barrier",
                "emotional_need",
                "plan_arc",
            ):
                row[key] = _string(ai_result.get(key))
            row["customer_stage"] = row.get("customer_stage") or _string(ai_result.get("conversion_stage"))
        return row

    @staticmethod
    def _decode_outreach_task(row: dict[str, Any]) -> dict[str, Any]:
        raw_sources = loads_list(row.get("content_sources"))
        policy_items = [item for item in raw_sources if isinstance(item, dict)]
        row["content_sources"] = [_string(item) for item in raw_sources if not isinstance(item, dict) and _string(item)]
        row["content_source_metadata"] = policy_items
        row["should_send_payment_collection"] = any(
            bool(item.get("should_send_payment_collection")) for item in policy_items
        )
        row["reply_messages"] = loads_list(row.get("reply_messages_json"))
        row.pop("reply_messages_json", None)
        row["before_send_check"] = bool(row.get("before_send_check"))
        return row

    @staticmethod
    def _decode_outreach_event(row: dict[str, Any]) -> dict[str, Any]:
        row["payload"] = loads_dict(row.get("payload_json"))
        row.pop("payload_json", None)
        return row
