from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta, timezone
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo

from app.services.storage.serialization import loads_dict


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SOP_UNFINISHED_STATUSES = {
    "accepted", "pending", "platform_received", "platform_queued", "platform_claiming",
    "platform_judging", "platform_processing", "platform_processing_retry",
    "platform_delivery_pending", "platform_send_uncertain", "platform_complete_pending",
    "platform_batch_send_retry", "platform_batch_consume_pending",
    "platform_failure_rule_data_pending", "sending", "processing_retry",
}


class OperationsDashboardRepositoryMixin:
    def platform_sop_dashboard(
        self,
        *,
        started_from: str = "",
        started_to: str = "",
        corp_id: str = "",
        wechat: str = "",
    ) -> dict[str, Any]:
        start, end = _dashboard_range(started_from, started_to)
        clauses = ["e.event_type='platform_sop_task'", "e.received_at>=?", "e.received_at<=?"]
        params: list[Any] = [start.isoformat(), end.isoformat()]
        if corp_id:
            clauses.append("t.corp_id=?")
            params.append(corp_id)
        if wechat:
            clauses.append("t.wechat=?")
            params.append(wechat)
        with self.store.connect() as conn:
            rows = _dict_rows(conn.execute(
                f"""
                SELECT e.event_id, e.status AS event_status, e.error AS event_error,
                       e.retry_count, e.received_at, e.updated_at AS event_updated_at,
                       e.raw_payload_json,
                       t.id AS task_id, t.customer_id, t.external_userid, t.corp_id,
                       t.user_id, t.wechat, t.status AS task_status, t.error AS task_error,
                       t.send_payload_json, t.send_response_json,
                       t.reply_messages_json, t.created_at AS task_created_at,
                       t.updated_at AS task_updated_at, t.sent_at
                FROM sop_events e
                LEFT JOIN sop_send_tasks t ON t.event_id=e.event_id
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall())
        bucket = "hour" if end - start <= timedelta(days=2) else "day"
        return {
            "range": {
                "started_from": start.isoformat(),
                "started_to": end.isoformat(),
                "bucket": bucket,
                "timezone": "Asia/Shanghai",
            },
            "filters": {"corp_id": corp_id, "wechat": wechat},
            "platform_sop": _platform_sop_metrics(rows, bucket),
            "freshness": {"latest_platform_sop_at": _latest(rows, "received_at")},
        }

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
        production_run = f"LOWER(COALESCE({self.store.json_text('input_snapshot', '$.request_context.test_isolated')}, 'false')) NOT IN ('true','1')"
        production_trace = f"LOWER(COALESCE({self.store.json_text('r.input_snapshot', '$.request_context.test_isolated')}, 'false')) NOT IN ('true','1')"
        run_clauses.append(production_run)
        trace_clauses.append(production_trace)
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
                       e.retry_count, e.received_at, e.updated_at AS event_updated_at,
                       e.raw_payload_json,
                       t.id AS task_id, t.customer_id, t.external_userid, t.corp_id,
                       t.user_id, t.wechat, t.status AS task_status, t.error AS task_error,
                       t.send_payload_json, t.send_response_json,
                       t.reply_messages_json, t.created_at AS task_created_at,
                       t.updated_at AS task_updated_at, t.sent_at
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
            relation_table = self.store.source_table("customer_member_relations")
            archive_messages_table = self.store.source_table("archive_messages")
            local_start = start.astimezone(_SHANGHAI).strftime("%Y-%m-%d %H:%M:%S.%f")
            local_end = end.astimezone(_SHANGHAI).strftime("%Y-%m-%d %H:%M:%S.%f")
            relation_clauses = ["r.member_friend_added_at>=?", "r.member_friend_added_at<=?"]
            relation_params: list[Any] = [local_start, local_end]
            if corp_id:
                relation_clauses.append("r.enterprise_id=?")
                relation_params.append(corp_id)
            if wechat:
                relation_clauses.append("r.wework_user_id=?")
                relation_params.append(wechat)
            new_contact_rows = _dict_rows(conn.execute(
                f"""
                SELECT DISTINCT r.enterprise_id AS corp_id, r.wework_user_id AS wechat,
                       r.external_userid
                FROM {relation_table} r
                WHERE {' AND '.join(relation_clauses)}
                """,
                relation_params,
            ).fetchall())
            opened_contact_rows = _dict_rows(conn.execute(
                f"""
                SELECT DISTINCT r.enterprise_id AS corp_id, r.wework_user_id AS wechat,
                       r.external_userid
                FROM {relation_table} r
                INNER JOIN {archive_messages_table} m ON m.conversation_id=r.conversation_id
                WHERE {' AND '.join(relation_clauses)}
                  AND m.direction='incoming'
                  AND m.timestamp>=r.member_friend_added_at
                  AND m.timestamp>=? AND m.timestamp<=?
                  AND TRIM(COALESCE(m.content,''))!='我已经添加了你，现在我们可以开始聊天了。'
                """,
                [*relation_params, local_start, local_end],
            ).fetchall())
            plan_ids = [str(row.get("plan_id") or "") for row in outreach_rows if row.get("plan_id")]
            task_rows: list[dict[str, Any]] = []
            if plan_ids:
                placeholders = ",".join("?" for _ in plan_ids)
                task_rows = _dict_rows(conn.execute(
                    f"SELECT id, plan_id, step_index, status, sent_at, send_status, system_msgid, error_message FROM outreach_tasks WHERE plan_id IN ({placeholders})",
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
    new_keys = _contact_keys(new_rows)
    return {
        "new_contacts": len(new_keys),
        "opened_contacts": len(_contact_keys(opened_rows) & new_keys),
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
    sent_rows = [row for row in task_values if _is_confirmed_sop_send(row)]
    sent = len(sent_rows)
    no_send = sum(status == "completed_without_send" for status in statuses.elements())
    failed_event_ids = {
        str(row.get("event_id") or "")
        for row in rows
        if str(row.get("event_status") or "") == "platform_failed"
        or str(row.get("task_status") or "") == "failed"
        or bool(str(row.get("event_error") or row.get("task_error") or "").strip())
    }
    failed = len(failed_event_ids)
    unfinished = sum(
        str(row.get("event_status") or "") in _SOP_UNFINISHED_STATUSES
        or str(row.get("task_status") or "") in _SOP_UNFINISHED_STATUSES
        for row in events.values()
    )
    reasons = Counter()
    for row in task_values:
        error = str(row.get("task_error") or row.get("event_error") or "")
        if str(row.get("task_status") or "") not in {"completed_without_send", "failed"} and not error.strip():
            continue
        payload = loads_dict(row.get("send_payload_json"))
        platform_task = loads_dict(row.get("raw_payload_json")).get("platform_task")
        platform_task = platform_task if isinstance(platform_task, dict) else {}
        reasons[_sop_reason(payload, error, platform_task) or "reason_unrecorded"] += 1
    dispatch_latencies = []
    process_latencies = []
    queue_latencies = []
    consume_latencies = []
    message_count = 0
    for row in sent_rows:
        start = _parse_time(str(row.get("task_created_at") or ""))
        finish = _parse_time(str(row.get("sent_at") or ""))
        if start and finish and finish >= start:
            dispatch_latencies.append(int((finish - start).total_seconds() * 1000))
        response = loads_dict(row.get("send_response_json"))
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        system_msgids = data.get("system_msgids") if isinstance(data.get("system_msgids"), list) else []
        message_count += len(system_msgids) or (1 if data.get("system_msgid") else 0)
    for row in task_values:
        created = _parse_time(str(row.get("task_created_at") or ""))
        updated = _parse_time(str(row.get("task_updated_at") or ""))
        if created and updated and updated >= created:
            process_latencies.append(int((updated - created).total_seconds() * 1000))
        platform_task = loads_dict(row.get("raw_payload_json")).get("platform_task")
        platform_task = platform_task if isinstance(platform_task, dict) else {}
        scheduled = _parse_time(
            str(platform_task.get("scheduledAt") or platform_task.get("scheduled_at") or ""),
            default_tz=_SHANGHAI,
        )
        if created and scheduled and created >= scheduled:
            queue_latencies.append(int((created - scheduled).total_seconds() * 1000))
        payload = loads_dict(row.get("send_payload_json"))
        attempts = payload.get("consume_results") if isinstance(payload.get("consume_results"), list) else []
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            requested = _parse_time(str(attempt.get("requested_at") or ""))
            completed = _parse_time(str(attempt.get("completed_at") or ""))
            if requested and completed and completed >= requested:
                consume_latencies.append(int((completed - requested).total_seconds() * 1000))
    terminal_total = sent + no_send + failed
    terminal_events = sum(
        str(row.get("event_status") or "") in {"platform_completed", "platform_failed"}
        for row in events.values()
    )
    return {
        "events": len(events),
        "tasks": len(task_values),
        "customers": len(_contact_keys(task_values)),
        "messages_sent": message_count,
        "sent": sent,
        "no_send": no_send,
        "failed": failed,
        "unfinished": unfinished,
        "retry_count": sum(int(row.get("retry_count") or 0) for row in events.values()),
        "send_rate": _rate(sent, terminal_total),
        "terminal_rate": _rate(terminal_events, len(events)),
        "avg_dispatch_ms": _average(dispatch_latencies) if dispatch_latencies else None,
        "latency": {
            "queue": _latency_summary(queue_latencies),
            "process": _latency_summary(process_latencies),
            "dispatch": _latency_summary(dispatch_latencies),
            "consume_request": _latency_summary(consume_latencies),
        },
        "status_breakdown": _counter_items(statuses),
        "reason_breakdown": _counter_items(reasons),
        "wechat_breakdown": _platform_sop_wechat_breakdown(task_values),
        "trend": _platform_sop_trend(list(events.values()), bucket),
    }


def _platform_sop_wechat_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("wechat") or "未记录")].append(row)
    result = []
    for wechat, values in grouped.items():
        result.append({
            "wechat": wechat,
            "tasks": len(values),
            "customers": len(_contact_keys(values)),
            "sent": sum(_is_confirmed_sop_send(row) for row in values),
            "no_send": sum(str(row.get("task_status") or "") == "completed_without_send" for row in values),
            "failed": sum(
                str(row.get("task_status") or "") == "failed"
                or bool(str(row.get("event_error") or row.get("task_error") or "").strip())
                for row in values
            ),
        })
    return sorted(result, key=lambda item: (-item["tasks"], item["wechat"]))


def _platform_sop_trend(rows: list[dict[str, Any]], bucket: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        parsed = _parse_time(str(row.get("received_at") or ""))
        if not parsed:
            continue
        local = parsed.astimezone(_SHANGHAI)
        key = local.strftime("%Y-%m-%dT%H:00:00+08:00" if bucket == "hour" else "%Y-%m-%dT00:00:00+08:00")
        grouped[key].append(row)
    result = []
    for key in sorted(grouped):
        values = grouped[key]
        sent = sum(_is_confirmed_sop_send(row) for row in values)
        no_send = sum(str(row.get("task_status") or "") == "completed_without_send" for row in values)
        failed = sum(
            str(row.get("event_status") or "") == "platform_failed"
            or str(row.get("task_status") or "") == "failed"
            or bool(str(row.get("event_error") or row.get("task_error") or "").strip())
            for row in values
        )
        result.append({
            "bucket": key,
            "total": len(values),
            "sent": sent,
            "no_send": no_send,
            "failed": failed,
            "unfinished": sum(
                str(row.get("event_status") or "") in _SOP_UNFINISHED_STATUSES
                or str(row.get("task_status") or "") in _SOP_UNFINISHED_STATUSES
                for row in values
            ),
        })
    return result


def _latency_summary(values: list[int]) -> dict[str, Any]:
    clean = [max(0, int(value)) for value in values]
    return {
        "count": len(clean),
        "avg_ms": _average(clean) if clean else None,
        "p50_ms": _percentile(clean, 50) if clean else None,
        "p90_ms": _percentile(clean, 90) if clean else None,
        "max_ms": max(clean) if clean else None,
    }


def _is_confirmed_sop_send(row: dict[str, Any]) -> bool:
    if str(row.get("task_status") or "") != "sent" or not str(row.get("sent_at") or "").strip():
        return False
    response = loads_dict(row.get("send_response_json"))
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    return (
        str(data.get("send_status") or "").lower() == "accepted"
        and bool(data.get("system_msgid") or data.get("system_msgids"))
    )


def _sop_reason(payload: dict[str, Any], error: str, platform_task: dict[str, Any] | None = None) -> str:
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    consume_results = payload.get("consume_results") if isinstance(payload.get("consume_results"), list) else []
    consume_remarks = [
        item.get("remark")
        for item in reversed(consume_results)
        if isinstance(item, dict) and str(item.get("remark") or "").strip()
    ]
    platform_task = platform_task or {}
    candidates = [
        payload.get("reason_code"), payload.get("reason"), payload.get("terminal_reason"),
        *consume_remarks, platform_task.get("remark"), platform_task.get("pauseReason"),
        decision.get("reason_code"), decision.get("reason"), decision.get("decision_source"),
    ]
    evaluations = decision.get("evaluations") if isinstance(decision.get("evaluations"), list) else []
    for evaluation in evaluations:
        if isinstance(evaluation, dict):
            candidates.extend((evaluation.get("reason_code"), evaluation.get("reason")))
    candidates.append(error)
    reason = next((str(value).strip() for value in candidates if str(value or "").strip()), "")
    lowered = reason.lower()
    if "human_takeover" in lowered or "人工接待" in reason or "人工接管" in reason:
        return "human_takeover"
    if "customer_relation_deleted" in lowered or ("客户关系" in reason and ("失效" in reason or "删除" in reason)):
        return "customer_relation_deleted"
    if "expired" in lowered or ("超过" in reason and "分钟" in reason):
        return "sop_task_expired"
    return reason


def _first_day_metrics(
    rows: list[dict[str, Any]],
    task_rows: list[dict[str, Any]],
    bucket: str,
) -> dict[str, Any]:
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    reasons = Counter(str(row.get("reason_code") or "") for row in rows if row.get("reason_code"))
    first_sent = sum(int(row.get("step_index") or 0) == 1 and _is_confirmed_outreach_send(row) for row in task_rows)
    second_sent = sum(int(row.get("step_index") or 0) == 2 and _is_confirmed_outreach_send(row) for row in task_rows)
    second_cancelled = sum(
        int(row.get("step_index") or 0) == 2
        and row.get("status") in {"cancelled", "skipped"}
        and "customer" in str(row.get("error_message") or "").lower()
        for row in task_rows
    )
    durations = [int(row.get("duration_ms") or 0) for row in rows if int(row.get("duration_ms") or 0) > 0]
    failed = sum(str(row.get("status") or "").lower() == "failed" for row in rows)
    trend_rows = [
        {**row, "failed_marker": "failed" if str(row.get("status") or "").lower() == "failed" else ""}
        for row in rows
    ]
    return {
        "triggers": len(rows),
        "plans_created": len({str(row.get("plan_id")).strip() for row in rows if str(row.get("plan_id") or "").strip()}),
        "blocked": sum(str(row.get("status") or "").lower() == "blocked" for row in rows),
        "failed": failed,
        "first_sent": first_sent,
        "second_sent": second_sent,
        "second_cancelled_customer_reply": second_cancelled,
        "model_attempts": sum(int(row.get("model_attempt_count") or 0) for row in rows),
        "retry_count": sum(int(row.get("retry_count") or 0) > 0 for row in rows),
        "retry_attempts": sum(int(row.get("retry_count") or 0) for row in rows),
        "avg_ms": _average(durations) if durations else 0,
        "p90_ms": _percentile(durations, 90) if durations else 0,
        "status_breakdown": _counter_items(statuses),
        "reason_breakdown": _counter_items(reasons),
        "trend": _trend(trend_rows, bucket, time_field="started_at", failure_field="failed_marker", duration_field="duration_ms"),
    }


def _is_confirmed_outreach_send(row: dict[str, Any]) -> bool:
    return (
        str(row.get("status") or "") == "sent"
        and bool(str(row.get("sent_at") or "").strip())
        and bool(str(row.get("system_msgid") or "").strip() or str(row.get("send_status") or "").strip())
    )


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
        durations = [
            int(row.get(duration_field) or 0)
            for row in values
            if duration_field and int(row.get(duration_field) or 0) > 0
        ]
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


def _parse_time(value: str, *, default_tz: ZoneInfo | timezone = UTC) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=default_tz).astimezone(UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


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
