from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo

from app.services.storage.serialization import loads_dict


_SHANGHAI = ZoneInfo("Asia/Shanghai")


class OperationsDashboardRepositoryMixin:
    def operations_dashboard(
        self,
        *,
        started_from: str = "",
        started_to: str = "",
        corp_id: str = "",
        wechat: str = "",
    ) -> dict[str, Any]:
        start, end = _dashboard_range(started_from, started_to)
        start_iso = start.isoformat()
        end_iso = end.isoformat()
        run_clauses = ["created_at>=?", "created_at<=?"]
        trace_clauses = ["r.created_at>=?", "r.created_at<=?"]
        run_params: list[Any] = [start_iso, end_iso]
        trace_params: list[Any] = [start_iso, end_iso]
        if corp_id:
            run_clauses.append(f"{self.store.json_text('input_snapshot', '$.corp_id')}=?")
            trace_clauses.append(f"{self.store.json_text('r.input_snapshot', '$.corp_id')}=?")
            run_params.append(corp_id)
            trace_params.append(corp_id)
        if wechat:
            run_clauses.append(f"{self.store.json_text('input_snapshot', '$.wechat')}=?")
            trace_clauses.append(f"{self.store.json_text('r.input_snapshot', '$.wechat')}=?")
            run_params.append(wechat)
            trace_params.append(wechat)

        contact_clauses = ["started_at>=?", "started_at<=?"]
        contact_params: list[Any] = [start_iso, end_iso]
        if corp_id:
            contact_clauses.append("corp_id=?")
            contact_params.append(corp_id)
        if wechat:
            contact_clauses.append("wechat=?")
            contact_params.append(wechat)

        with self.store.connect() as conn:
            run_rows = _dict_rows(conn.execute(
                f"SELECT request_id, duration_ms, error, created_at FROM runs WHERE {' AND '.join(run_clauses)}",
                run_params,
            ).fetchall())
            trace_rows = _dict_rows(conn.execute(
                f"""
                SELECT n.node_name, n.duration_ms, n.error, n.created_at
                FROM node_traces n
                INNER JOIN runs r ON r.request_id=n.request_id
                WHERE {' AND '.join(trace_clauses)}
                """,
                trace_params,
            ).fetchall())

            sop_clauses = ["e.event_type='platform_sop_task'", "e.received_at>=?", "e.received_at<=?"]
            sop_params: list[Any] = [start_iso, end_iso]
            if corp_id:
                sop_clauses.append("t.corp_id=?")
                sop_params.append(corp_id)
            if wechat:
                sop_clauses.append("t.wechat=?")
                sop_params.append(wechat)
            sop_rows = _dict_rows(conn.execute(
                f"""
                SELECT e.event_id, e.status AS event_status, e.error AS event_error,
                       e.retry_count, e.received_at, e.updated_at,
                       t.id AS task_id, t.status AS task_status, t.error AS task_error,
                       t.send_payload_json, t.created_at AS task_created_at, t.sent_at
                FROM sop_events e
                LEFT JOIN sop_send_tasks t ON t.event_id=e.event_id
                WHERE {' AND '.join(sop_clauses)}
                """,
                sop_params,
            ).fetchall())

            outreach_rows = _dict_rows(conn.execute(
                f"""
                SELECT workflow_run_id, plan_id, status, reason_code, final_decision,
                       model_attempt_count, retry_count, duration_ms, started_at, finished_at,
                       error_type, error_message
                FROM first_day_outreach_runs
                WHERE {' AND '.join(contact_clauses)}
                """,
                contact_params,
            ).fetchall())
            new_contact_rows = _dict_rows(conn.execute(
                f"""
                SELECT customer_id, external_userid, corp_id, wechat
                FROM conversations
                WHERE {' AND '.join(contact_clauses).replace('started_at', 'created_at')}
                """,
                contact_params,
            ).fetchall())
            opened_contact_rows = _dict_rows(conn.execute(
                f"""
                SELECT c.customer_id, c.external_userid, c.corp_id, c.wechat
                FROM messages m
                JOIN conversations c ON c.id=m.conversation_id
                WHERE m.role='user' AND {' AND '.join(contact_clauses).replace('started_at', 'm.created_at')}
                """,
                contact_params,
            ).fetchall())
            plan_ids = [str(row.get("plan_id") or "") for row in outreach_rows if row.get("plan_id")]
            task_rows: list[dict[str, Any]] = []
            if plan_ids:
                placeholders = ",".join("?" for _ in plan_ids)
                task_rows = _dict_rows(conn.execute(
                    f"SELECT plan_id, step_index, status, sent_at, error_message FROM outreach_tasks WHERE plan_id IN ({placeholders})",
                    plan_ids,
                ).fetchall())

        bucket = "hour" if end - start <= timedelta(days=2) else "day"
        return {
            "range": {
                "started_from": start_iso,
                "started_to": end_iso,
                "bucket": bucket,
                "timezone": "Asia/Shanghai",
            },
            "filters": {"corp_id": corp_id, "wechat": wechat},
            "ai_reply": _ai_reply_metrics(run_rows, trace_rows, bucket),
            "contacts": _contact_metrics(new_contact_rows, opened_contact_rows),
            "platform_sop": _platform_sop_metrics(sop_rows, bucket),
            "first_day_outreach": _first_day_metrics(outreach_rows, task_rows, bucket),
            "freshness": {
                "latest_ai_reply_at": _latest(run_rows, "created_at"),
                "latest_platform_sop_at": _latest(sop_rows, "received_at"),
                "latest_first_day_outreach_at": _latest(outreach_rows, "started_at"),
            },
        }


def _contact_metrics(new_rows: list[dict[str, Any]], opened_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "new_contacts": len(_contact_keys(new_rows)),
        "opened_contacts": len(_contact_keys(opened_rows)),
    }


def _contact_keys(rows: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    output: set[tuple[str, str, str]] = set()
    for row in rows:
        corp = str(row.get("corp_id") or "").strip().lower()
        wechat = str(row.get("wechat") or "").strip().lower()
        external = str(row.get("external_userid") or row.get("customer_id") or "").strip().lower()
        customer = str(row.get("customer_id") or row.get("external_userid") or "").strip().lower()
        identity = external or customer
        if identity:
            output.add((corp, wechat, identity))
    return output


def _dashboard_range(started_from: str, started_to: str) -> tuple[datetime, datetime]:
    end = _parse_time(started_to) or datetime.now(UTC)
    start = _parse_time(started_from) or end - timedelta(hours=24)
    if start >= end:
        raise ValueError("started_from must be earlier than started_to")
    if end - start > timedelta(days=90):
        raise ValueError("dashboard range cannot exceed 90 days")
    return start, end


def _ai_reply_metrics(runs: list[dict[str, Any]], traces: list[dict[str, Any]], bucket: str) -> dict[str, Any]:
    durations = [int(row.get("duration_ms") or 0) for row in runs]
    failed = [row for row in runs if str(row.get("error") or "").strip()]
    timeouts = [row for row in failed if _is_timeout(row.get("error"))]
    trend = _trend(
        runs,
        bucket,
        time_field="created_at",
        failure_field="error",
        duration_field="duration_ms",
    )
    nodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in traces:
        nodes[str(row.get("node_name") or "unknown")].append(row)
    node_breakdown = []
    for name, rows in nodes.items():
        node_durations = [int(row.get("duration_ms") or 0) for row in rows]
        node_breakdown.append(
            {
                "node": name,
                "calls": len(rows),
                "failed": sum(bool(str(row.get("error") or "").strip()) for row in rows),
                "timeout": sum(_is_timeout(row.get("error")) for row in rows),
                "avg_ms": _average(node_durations),
                "p90_ms": _percentile(node_durations, 90),
            }
        )
    node_breakdown.sort(key=lambda item: (-item["calls"], item["node"]))
    return {
        "calls": len(runs),
        "success": len(runs) - len(failed),
        "failed": len(failed),
        "timeout": len(timeouts),
        "success_rate": _rate(len(runs) - len(failed), len(runs)),
        "avg_ms": _average(durations),
        "p50_ms": _percentile(durations, 50),
        "p90_ms": _percentile(durations, 90),
        "p95_ms": _percentile(durations, 95),
        "trend": trend,
        "node_breakdown": node_breakdown[:20],
    }


def _platform_sop_metrics(rows: list[dict[str, Any]], bucket: str) -> dict[str, Any]:
    events: dict[str, dict[str, Any]] = {}
    tasks: dict[str, dict[str, Any]] = {}
    for row in rows:
        events[str(row.get("event_id") or "")] = row
        if row.get("task_id"):
            tasks[str(row["task_id"])] = row
    task_values = list(tasks.values())
    statuses = Counter(str(row.get("task_status") or "no_task") for row in task_values)
    sent = sum(status in {"sent", "shadow_send"} for status in statuses.elements())
    no_send = sum(status in {"completed_without_send", "shadow_no_send"} for status in statuses.elements())
    failed = sum(
        bool(str(row.get("task_error") or "").strip())
        or "fail" in str(row.get("task_status") or "").lower()
        or "error" in str(row.get("task_status") or "").lower()
        for row in task_values
    )
    reasons = Counter()
    for row in task_values:
        payload = loads_dict(row.get("send_payload_json"))
        decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else payload
        reason = str(decision.get("reason_code") or decision.get("reason") or "").strip()
        if reason:
            reasons[reason] += 1
    latencies = []
    for row in task_values:
        start = _parse_time(str(row.get("task_created_at") or ""))
        finish = _parse_time(str(row.get("sent_at") or ""))
        if start and finish and finish >= start:
            latencies.append(int((finish - start).total_seconds() * 1000))
    return {
        "events": len(events),
        "tasks": len(task_values),
        "sent": sent,
        "no_send": no_send,
        "failed": failed,
        "retry_count": sum(int(row.get("retry_count") or 0) for row in events.values()),
        "send_rate": _rate(sent, len(task_values)),
        "avg_dispatch_ms": _average(latencies) if latencies else None,
        "status_breakdown": _counter_items(statuses),
        "reason_breakdown": _counter_items(reasons),
        "trend": _trend(list(events.values()), bucket, time_field="received_at", failure_field="event_error"),
    }


def _first_day_metrics(
    rows: list[dict[str, Any]],
    task_rows: list[dict[str, Any]],
    bucket: str,
) -> dict[str, Any]:
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    reasons = Counter(str(row.get("reason_code") or "") for row in rows if row.get("reason_code"))
    first_sent = sum(int(row.get("step_index") or 0) == 1 and row.get("status") == "sent" for row in task_rows)
    second_sent = sum(int(row.get("step_index") or 0) == 2 and row.get("status") == "sent" for row in task_rows)
    second_cancelled = sum(
        int(row.get("step_index") or 0) == 2
        and row.get("status") in {"cancelled", "skipped"}
        and "customer" in str(row.get("error_message") or "").lower()
        for row in task_rows
    )
    durations = [int(row.get("duration_ms") or 0) for row in rows]
    failed = sum(
        bool(str(row.get("error_message") or "").strip())
        or str(row.get("status") or "").lower() == "failed"
        for row in rows
    )
    return {
        "triggers": len(rows),
        "plans_created": sum(bool(str(row.get("plan_id") or "").strip()) for row in rows),
        "blocked": sum(str(row.get("status") or "").lower() == "blocked" for row in rows),
        "failed": failed,
        "first_sent": first_sent,
        "second_sent": second_sent,
        "second_cancelled_customer_reply": second_cancelled,
        "model_attempts": sum(int(row.get("model_attempt_count") or 0) for row in rows),
        "retry_count": sum(int(row.get("retry_count") or 0) for row in rows),
        "avg_ms": _average(durations),
        "p90_ms": _percentile(durations, 90),
        "status_breakdown": _counter_items(statuses),
        "reason_breakdown": _counter_items(reasons),
        "trend": _trend(rows, bucket, time_field="started_at", failure_field="error_message", duration_field="duration_ms"),
    }


def _trend(
    rows: list[dict[str, Any]],
    bucket: str,
    *,
    time_field: str,
    failure_field: str = "",
    duration_field: str = "",
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        parsed = _parse_time(str(row.get(time_field) or ""))
        if not parsed:
            continue
        local = parsed.astimezone(_SHANGHAI)
        key = local.strftime("%Y-%m-%dT%H:00:00+08:00" if bucket == "hour" else "%Y-%m-%dT00:00:00+08:00")
        grouped[key].append(row)
    result = []
    for key in sorted(grouped):
        values = grouped[key]
        durations = [int(row.get(duration_field) or 0) for row in values] if duration_field else []
        result.append(
            {
                "bucket": key,
                "total": len(values),
                "failed": sum(bool(str(row.get(failure_field) or "").strip()) for row in values) if failure_field else 0,
                "timeout": sum(_is_timeout(row.get(failure_field)) for row in values) if failure_field else 0,
                "avg_ms": _average(durations) if durations else None,
            }
        )
    return result


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _is_timeout(value: Any) -> bool:
    text = str(value or "").lower()
    return any(marker in text for marker in ("timeout", "timed out", "超时"))


def _average(values: list[int]) -> int:
    return round(sum(values) / len(values)) if values else 0


def _percentile(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return int(ordered[max(0, ceil(len(ordered) * percentile / 100) - 1)])


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _counter_items(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common() if key]


def _latest(rows: list[dict[str, Any]], field: str) -> str:
    values = [str(row.get(field) or "") for row in rows if str(row.get(field) or "")]
    return max(values, default="")


def _dict_rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [row if isinstance(row, dict) else dict(row) for row in rows]
