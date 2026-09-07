from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.first_day_outreach_log import build_first_day_run_business_summary
from app.services.storage.serialization import loads_dict, loads_list


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MAX_RANGE = timedelta(days=31)
_RUN_JSON_FIELDS = {
    "input_snapshot_json": "input_snapshot",
    "workflow_json": "workflow",
    "final_plan_json": "final_plan",
}
_REASON_LABELS = {
    "human_takeover": "人工接待",
    "customer_replied": "客户已回复",
    "order_state_changed": "已预约/已支付",
    "explicit_stop_contact": "明确退订",
    "no_safe_content": "无安全可用内容",
    "send_failed": "发送失败",
    "ai_status_unknown": "AI 状态无法确认",
    "customer_relation_unavailable": "客户关系不可用",
    "customer_deleted": "客户关系已失效",
    "daily_limit": "当日触达上限",
    "duplicate_or_completed": "重复计划或周期已完成",
    "model_rejected": "模型未生成计划",
    "workflow_failed": "计划生成异常",
    "other": "其他原因",
}


class OutreachDashboardRepositoryMixin:
    def outreach_bi_dashboard(
        self,
        *,
        started_from: str = "",
        started_to: str = "",
        corp_id: str = "",
        wechat: str = "",
        queue_limit: int = 50,
    ) -> dict[str, Any]:
        start, end = _dashboard_range(started_from, started_to)
        clauses = ["started_at>=?", "started_at<=?"]
        params: list[Any] = [start.isoformat(), end.isoformat()]
        if corp_id:
            clauses.append("corp_id=?")
            params.append(corp_id)
        if wechat:
            clauses.append("wechat=?")
            params.append(wechat)
        with self.store.connect() as conn:
            run_rows = _dict_rows(conn.execute(
                f"SELECT * FROM first_day_outreach_runs WHERE {' AND '.join(clauses)} "
                "ORDER BY started_at DESC, workflow_run_id DESC",
                params,
            ).fetchall())

        runs = [_decode_run(row) for row in run_rows]
        plan_ids = sorted({_text(row.get("plan_id")) for row in runs if _text(row.get("plan_id"))})
        plans, tasks, events = self._outreach_dashboard_related(plan_ids)
        confirmed_tasks = [row for row in tasks if _is_confirmed_send(row)]
        first_sent_at: dict[str, datetime] = {}
        for task in confirmed_tasks:
            plan_id = _text(task.get("plan_id"))
            sent_at = _parse_time(_text(task.get("sent_at")))
            if plan_id and sent_at:
                first_sent_at[plan_id] = min(first_sent_at.get(plan_id, sent_at), sent_at)

        observation_end = min(datetime.now(UTC), end + timedelta(hours=72))
        message_start = min(first_sent_at.values(), default=start)
        message_rows = self._outreach_dashboard_customer_messages(
            started_from=message_start.isoformat(),
            started_to=observation_end.isoformat(),
            corp_id=corp_id,
            wechat=wechat,
        ) if first_sent_at else []
        messages_by_contact: dict[tuple[str, str, str], list[datetime]] = defaultdict(list)
        for row in message_rows:
            created_at = _parse_time(_text(row.get("created_at")))
            if created_at:
                messages_by_contact[_contact_key(row)].append(created_at)

        reopened_plan_ids: set[str] = set()
        matched_reply_times: list[datetime] = []
        for plan_id, sent_at in first_sent_at.items():
            plan = plans.get(plan_id, {})
            matching = [
                created_at
                for created_at in messages_by_contact.get(_contact_key(plan), [])
                if sent_at < created_at <= sent_at + timedelta(hours=24)
            ]
            if matching:
                reopened_plan_ids.add(plan_id)
                matched_reply_times.extend(matching)

        transaction_plan_ids: set[str] = set()
        for event in events:
            plan_id = _text(event.get("plan_id"))
            if _text(event.get("event_type")) != "task_skipped_order_state_changed":
                continue
            sent_at = first_sent_at.get(plan_id)
            event_at = _parse_time(_text(event.get("created_at")))
            if sent_at and event_at and sent_at < event_at <= sent_at + timedelta(days=7):
                transaction_plan_ids.add(plan_id)

        planned_run_ids = {
            _text(run.get("workflow_run_id")) for run in runs if _text(run.get("plan_id"))
        }
        funnel_counts = [
            ("scanned", "进入扫描", len(runs)),
            ("eligible", "符合条件", sum(_run_is_eligible(run) for run in runs)),
            ("planned", "计划生成", len(planned_run_ids)),
            ("sent", "成功触达", len(first_sent_at)),
            ("reopened", "客户开口", len(reopened_plan_ids)),
            ("converted", "预约/支付进展", len(transaction_plan_ids)),
        ]
        bucket = "hour" if end - start <= timedelta(days=2) else "day"
        return {
            "range": {
                "started_from": start.isoformat(),
                "started_to": end.isoformat(),
                "timezone": "Asia/Shanghai",
                "bucket": bucket,
                "max_days": int(_MAX_RANGE.days),
            },
            "filters": {"corp_id": corp_id, "wechat": wechat},
            "funnel": _build_funnel(funnel_counts),
            "trend": _build_trend(
                start=start,
                end=end,
                bucket=bucket,
                first_sent_at=first_sent_at,
                reopened_plan_ids=reopened_plan_ids,
            ),
            "reason_breakdown": _reason_breakdown(runs, events),
            "queue": _build_queue(
                runs,
                plans=plans,
                tasks=tasks,
                reopened_plan_ids=reopened_plan_ids,
                limit=max(1, min(int(queue_limit or 50), 100)),
            ),
            "queue_total": len(runs),
            "wechat_breakdown": _wechat_breakdown(
                runs,
                sent_plan_ids=set(first_sent_at),
                reopened_plan_ids=reopened_plan_ids,
            ),
            "outcomes": {
                "sent_plans": len(first_sent_at),
                "reopened_24h": len(reopened_plan_ids),
                "reopened_24h_rate": _rate(len(reopened_plan_ids), len(first_sent_at)),
                "transaction_progress_7d": len(transaction_plan_ids),
                "transaction_progress_7d_rate": _rate(len(transaction_plan_ids), len(first_sent_at)),
            },
            "freshness": {
                "latest_run_at": max((_text(row.get("started_at")) for row in runs), default=""),
                "latest_sent_at": max((_text(row.get("sent_at")) for row in confirmed_tasks), default=""),
                "latest_customer_reply_at": max(matched_reply_times).isoformat() if matched_reply_times else "",
            },
            "data_quality": {
                "runs_with_context": sum(bool(run.get("input_snapshot")) for run in runs),
                "runs_total": len(runs),
                "reopen_measurement": "本地客户入站消息发生在首次真实触达后的 24 小时内",
                "transaction_measurement": "触达后 7 天内发送前检查发现预约或支付状态变化",
                "historical_raw_retention_days": 30,
            },
        }

    def _outreach_dashboard_related(
        self,
        plan_ids: list[str],
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        if not plan_ids:
            return {}, [], []
        plans: dict[str, dict[str, Any]] = {}
        tasks: dict[str, dict[str, Any]] = {}
        events: dict[str, dict[str, Any]] = {}
        with self.store.connect() as conn:
            for batch in _chunks(plan_ids, 400):
                placeholders = ",".join("?" for _ in batch)
                for row in _dict_rows(conn.execute(
                    f"SELECT * FROM outreach_plans WHERE id IN ({placeholders})", batch,
                ).fetchall()):
                    row["source_snapshot"] = loads_dict(row.get("source_snapshot"))
                    plans[_text(row.get("id"))] = row
                for row in _dict_rows(conn.execute(
                    f"SELECT * FROM outreach_tasks WHERE plan_id IN ({placeholders}) ORDER BY scheduled_at ASC", batch,
                ).fetchall()):
                    row["content_sources"] = loads_list(row.get("content_sources"))
                    row["reply_messages"] = loads_list(row.get("reply_messages_json"))
                    row.pop("reply_messages_json", None)
                    tasks[_text(row.get("id"))] = row
                for row in _dict_rows(conn.execute(
                    f"SELECT * FROM outreach_events WHERE plan_id IN ({placeholders}) ORDER BY created_at ASC", batch,
                ).fetchall()):
                    row["payload"] = loads_dict(row.get("payload_json"))
                    row.pop("payload_json", None)
                    events[_text(row.get("id"))] = row
        return plans, list(tasks.values()), list(events.values())

    def _outreach_dashboard_customer_messages(
        self,
        *,
        started_from: str,
        started_to: str,
        corp_id: str,
        wechat: str,
    ) -> list[dict[str, Any]]:
        clauses = ["m.role='user'", "m.created_at>=?", "m.created_at<=?"]
        params: list[Any] = [started_from, started_to]
        if corp_id:
            clauses.append("c.corp_id=?")
            params.append(corp_id)
        if wechat:
            clauses.append("c.wechat=?")
            params.append(wechat)
        with self.store.connect() as conn:
            return _dict_rows(conn.execute(
                f"""
                SELECT m.created_at, c.customer_id, c.external_userid, c.corp_id, c.wechat
                FROM messages m INNER JOIN conversations c ON c.id=m.conversation_id
                WHERE {' AND '.join(clauses)} ORDER BY m.created_at ASC
                """,
                params,
            ).fetchall())


def _build_funnel(values: list[tuple[str, str, int]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, (key, label, count) in enumerate(values):
        next_count = values[index + 1][2] if index + 1 < len(values) else None
        result.append({
            "key": key,
            "label": label,
            "count": count,
            "next_count": next_count,
            "next_rate": _rate(next_count, count) if next_count is not None else None,
            "drop_count": max(0, count - next_count) if next_count is not None else None,
            "drop_rate": _rate(max(0, count - next_count), count) if next_count is not None else None,
        })
    return result


def _build_trend(
    *,
    start: datetime,
    end: datetime,
    bucket: str,
    first_sent_at: dict[str, datetime],
    reopened_plan_ids: set[str],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: {"sent": 0, "reopened": 0})
    pattern = "%Y-%m-%dT%H:00:00+08:00" if bucket == "hour" else "%Y-%m-%dT00:00:00+08:00"
    for plan_id, sent_at in first_sent_at.items():
        key = sent_at.astimezone(_SHANGHAI).strftime(pattern)
        grouped[key]["sent"] += 1
        grouped[key]["reopened"] += int(plan_id in reopened_plan_ids)
    cursor = start.astimezone(_SHANGHAI)
    if bucket == "hour":
        cursor = cursor.replace(minute=0, second=0, microsecond=0)
        step = timedelta(hours=1)
    else:
        cursor = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
        step = timedelta(days=1)
    result: list[dict[str, Any]] = []
    end_local = end.astimezone(_SHANGHAI)
    while cursor <= end_local:
        key = cursor.strftime(pattern)
        value = grouped.get(key, {"sent": 0, "reopened": 0})
        result.append({
            "bucket": key,
            "sent": value["sent"],
            "reopened": value["reopened"],
            "reopen_rate": _rate(value["reopened"], value["sent"]),
        })
        cursor += step
    return result


def _build_queue(
    runs: list[dict[str, Any]],
    *,
    plans: dict[str, dict[str, Any]],
    tasks: list[dict[str, Any]],
    reopened_plan_ids: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    by_plan: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        by_plan[_text(task.get("plan_id"))].append(task)
    result: list[dict[str, Any]] = []
    for run in runs[:limit]:
        plan_id = _text(run.get("plan_id"))
        plan_tasks = sorted(by_plan.get(plan_id, []), key=lambda item: int(item.get("step_index") or 0))
        pending = [item for item in plan_tasks if _text(item.get("status")) in {"pending", "checking", "check_failed", "sending"}]
        sent = [item for item in plan_tasks if _is_confirmed_send(item)]
        failed = [item for item in plan_tasks if _text(item.get("status")) == "failed"]
        next_touch = min((_text(item.get("scheduled_at")) for item in pending if _text(item.get("scheduled_at"))), default="")
        if not plan_id:
            phase = "failed" if _text(run.get("status")) == "failed" else "blocked"
        elif failed:
            phase = "failed"
        elif pending and sent:
            phase = "waiting_next_touch"
        elif pending:
            phase = "waiting_first_touch"
        elif sent:
            phase = "reopened" if plan_id in reopened_plan_ids else "completed"
        else:
            phase = "planned"
        summary = build_first_day_run_business_summary(run)
        snapshot = _dict(run.get("input_snapshot"))
        activity = _dict(snapshot.get("conversation_activity"))
        trigger = _dict(snapshot.get("trigger_context"))
        silence_minutes = int(
            activity.get("reply_wait_minutes")
            or trigger.get("reply_wait_minutes")
            or trigger.get("monitor_silent_minutes")
            or 0
        )
        result.append({
            "workflow_run_id": _text(run.get("workflow_run_id")),
            "plan_id": plan_id,
            "customer_id": _text(run.get("customer_id")),
            "external_userid": _text(run.get("external_userid")),
            "corp_id": _text(run.get("corp_id")),
            "wechat": _text(run.get("wechat")),
            "started_at": _text(run.get("started_at")),
            "finished_at": _text(run.get("finished_at")),
            "status": _text(run.get("status")),
            "reason_code": _text(run.get("reason_code")),
            "reason_label": _REASON_LABELS.get(_normalize_reason(_text(run.get("reason_code"))), _text(run.get("reason_code")) or "处理中"),
            "phase": phase,
            "silence_minutes": silence_minutes,
            "last_customer_message": _text(summary.get("last_customer_message")),
            "customer_need": _text(summary.get("customer_need")),
            "first_scene": _text(run.get("first_scene")),
            "second_scene": _text(run.get("second_scene")),
            "plan_goal": _text(plans.get(plan_id, {}).get("plan_goal")),
            "next_touch_at": next_touch,
            "sent_steps": len(sent),
            "task_count": len(plan_tasks),
            "reopened_24h": plan_id in reopened_plan_ids,
        })
    return result


def _reason_breakdown(runs: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()
    for run in runs:
        if _text(run.get("plan_id")) and _text(run.get("status")) not in {"blocked", "cancelled", "failed"}:
            continue
        key = _normalize_reason(_text(run.get("reason_code")))
        if not key:
            continue
        marker = ((_text(run.get("plan_id")) or _text(run.get("workflow_run_id"))), key)
        if marker not in seen:
            counts[key] += 1
            seen.add(marker)
    for event in events:
        key = _normalize_reason(_text(event.get("event_type")))
        if not key or key == "other":
            continue
        marker = (_text(event.get("plan_id")), key)
        if marker not in seen:
            counts[key] += 1
            seen.add(marker)
    return [
        {"key": key, "label": _REASON_LABELS.get(key, key), "count": count}
        for key, count in counts.most_common(12)
    ]


def _wechat_breakdown(
    runs: list[dict[str, Any]],
    *,
    sent_plan_ids: set[str],
    reopened_plan_ids: set[str],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[_text(run.get("wechat")) or "未记录"].append(run)
    result = []
    for wechat, values in grouped.items():
        plan_ids = {_text(item.get("plan_id")) for item in values if _text(item.get("plan_id"))}
        sent = len(plan_ids & sent_plan_ids)
        reopened = len(plan_ids & reopened_plan_ids)
        result.append({
            "wechat": wechat,
            "scanned": len(values),
            "planned": len(plan_ids),
            "sent": sent,
            "reopened": reopened,
            "reopen_rate": _rate(reopened, sent),
        })
    return sorted(result, key=lambda item: (-item["scanned"], item["wechat"]))


def _run_is_eligible(run: dict[str, Any]) -> bool:
    if _text(run.get("plan_id")):
        return True
    workflow = _dict(run.get("workflow"))
    summary = _dict(workflow.get("summary"))
    scene = _dict(summary.get("scene_analysis")) or _dict(workflow.get("scene_analysis"))
    return scene.get("eligible") is True


def _normalize_reason(value: str) -> str:
    key = value.strip().lower()
    if not key:
        return ""
    if any(marker in key for marker in ("human", "manual", "non_ai_mode")):
        return "human_takeover"
    if "customer_replied" in key or key == "not_waiting_for_customer_reply":
        return "customer_replied"
    if any(marker in key for marker in ("order_state", "prepay", "scheduled", "paid")):
        return "order_state_changed"
    if any(marker in key for marker in ("stop_contact", "unsubscribe", "opt_out")):
        return "explicit_stop_contact"
    if any(marker in key for marker in ("no_safe", "message_policy", "payment_card_only", "unanswered_payment")):
        return "no_safe_content"
    if any(marker in key for marker in ("delivery_failed", "task_failed", "send_failed")):
        return "send_failed"
    if any(marker in key for marker in ("ai_mode_unknown", "ai_mode_check_failed", "ai_management")):
        return "ai_status_unknown"
    if "customer_relation_unavailable" in key or "conversation_id_unavailable" in key:
        return "customer_relation_unavailable"
    if "customer_deleted" in key:
        return "customer_deleted"
    if "daily" in key and "limit" in key:
        return "daily_limit"
    if any(marker in key for marker in ("fingerprint", "cycle_completed", "already_logged")):
        return "duplicate_or_completed"
    if "plan_rejected" in key:
        return "model_rejected"
    if any(marker in key for marker in ("workflow", "model_node", "conversation_refresh", "order_context_unavailable")):
        return "workflow_failed"
    return "other"


def _dashboard_range(started_from: str, started_to: str) -> tuple[datetime, datetime]:
    end = _parse_time(started_to) or datetime.now(UTC)
    start = _parse_time(started_from) or end - timedelta(hours=24)
    if start >= end:
        raise ValueError("started_from must be earlier than started_to")
    if end - start > _MAX_RANGE:
        raise ValueError("outreach dashboard range cannot exceed 31 days")
    return start, end


def _decode_run(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    for storage_field, public_field in _RUN_JSON_FIELDS.items():
        output[public_field] = loads_dict(output.get(storage_field))
        output.pop(storage_field, None)
    return output


def _is_confirmed_send(row: dict[str, Any]) -> bool:
    return (
        _text(row.get("status")) == "sent"
        and bool(_text(row.get("sent_at")))
        and bool(_text(row.get("system_msgid")) or _text(row.get("send_status")))
    )


def _contact_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _text(row.get("corp_id")).lower(),
        _text(row.get("wechat")).lower(),
        (_text(row.get("external_userid")) or _text(row.get("customer_id"))).lower(),
    )


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _rate(numerator: int | None, denominator: int) -> float:
    return round(numerator / denominator, 4) if numerator is not None and denominator else 0.0


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [row if isinstance(row, dict) else dict(row) for row in rows]


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]
