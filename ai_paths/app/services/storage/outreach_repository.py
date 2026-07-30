from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.services.customer_scope import build_customer_scope
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
        result_limit = max(1, min(limit, 200))
        query_limit = 5000 if keyword.strip() else max(result_limit, min(result_limit * 5, 1000))
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
        items: list[dict[str, Any]] = []
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
            memory = self.load_memory(scope.sales_contact_key) if scope.persistence_allowed else {}
            memory = memory if isinstance(memory, dict) else {}
            item["portrait"] = memory.get("portrait") if isinstance(memory.get("portrait"), dict) else {}
            item["basic_info"] = memory.get("basic_info") if isinstance(memory.get("basic_info"), dict) else {}
            item["lifecycle_stage"] = str(memory.get("lifecycle_stage") or "")
            item["last_customer_message_at"] = str(
                memory.get("last_customer_message_at")
                or item.get("conversation_last_customer_at")
                or item.get("updated_at")
                or ""
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
            if len(items) >= result_limit:
                break
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
            external = _string(row["external_userid"]).lower() or _string(row["customer_id"]).lower()
            return (
                _string(row["corp_id"]).lower(),
                _string(row["wechat"]).lower(),
                external,
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
    ) -> dict[str, Any]:
        now = utc_now_iso()
        plan_id = str(uuid4())
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
                conn.execute(
                    """
                    INSERT INTO outreach_tasks
                        (id, plan_id, customer_id, step_index, scheduled_at, status, intent, message_goal,
                         content_sources, reply_messages_json, before_send_check, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
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
        self.add_outreach_event(
            plan_id=plan_id,
            task_id="",
            customer_id=customer_id,
            event_type="plan_created",
            event_summary="AI generated outreach plan",
            payload=source_snapshot,
        )
        scope = build_customer_scope(
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            customer_id=customer_id,
        )
        if scope.persistence_allowed:
            self.update_customer_outreach_state(scope.sales_contact_key, outreach_status="draft", outreach_plan_id=plan_id)
        return self.get_outreach_plan(plan_id)

    def list_outreach_sop_plans(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM outreach_sop_plans
                WHERE status!='deleted'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 300)),),
            ).fetchall()
        return [self._decode_outreach_sop_plan(dict(row)) for row in rows]

    def get_outreach_sop_plan(self, sop_plan_id: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM outreach_sop_plans WHERE id=?", (sop_plan_id,)).fetchone()
        return self._decode_outreach_sop_plan(dict(row)) if row else {}

    def create_outreach_sop_plan(
        self,
        *,
        name: str,
        description: str = "",
        filters: dict[str, Any] | None = None,
        status: str = "draft",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        sop_plan_id = str(uuid4())
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO outreach_sop_plans
                    (id, name, description, status, filters_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sop_plan_id, name, description, status or "draft", dumps(filters or {}), now, now),
            )
        return self.get_outreach_sop_plan(sop_plan_id)

    def update_outreach_sop_plan(
        self,
        sop_plan_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        filters: dict[str, Any] | None = None,
        status: str | None = None,
        last_run_summary: dict[str, Any] | None = None,
        touch_last_run: bool = False,
    ) -> dict[str, Any]:
        current = self.get_outreach_sop_plan(sop_plan_id)
        if not current:
            return {}
        now = utc_now_iso()
        with self.store.connect() as conn:
            conn.execute(
                """
                UPDATE outreach_sop_plans
                SET name=?,
                    description=?,
                    filters_json=?,
                    status=?,
                    last_run_at=CASE WHEN ? THEN ? ELSE last_run_at END,
                    last_run_summary_json=CASE WHEN ? THEN ? ELSE last_run_summary_json END,
                    updated_at=?
                WHERE id=?
                """,
                (
                    name if name is not None else current.get("name", ""),
                    description if description is not None else current.get("description", ""),
                    dumps(filters if filters is not None else current.get("filters", {})),
                    status if status is not None else current.get("status", "draft"),
                    1 if touch_last_run else 0,
                    now,
                    1 if last_run_summary is not None else 0,
                    dumps(last_run_summary or {}),
                    now,
                    sop_plan_id,
                ),
            )
        return self.get_outreach_sop_plan(sop_plan_id)

    def delete_outreach_sop_plan(self, sop_plan_id: str) -> bool:
        with self.store.connect() as conn:
            result = conn.execute("DELETE FROM outreach_sop_plans WHERE id=?", (sop_plan_id,))
        return result.rowcount > 0

    def outreach_sop_stats(self, sop_plan_id: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            plan_rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM outreach_plans
                WHERE sop_plan_id=?
                GROUP BY status
                """,
                (sop_plan_id,),
            ).fetchall()
            task_rows = conn.execute(
                """
                SELECT t.status, COUNT(*) AS count
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE p.sop_plan_id=?
                GROUP BY t.status
                """,
                (sop_plan_id,),
            ).fetchall()
        plan_counts = {str(row["status"]): int(row["count"]) for row in plan_rows}
        task_counts = {str(row["status"]): int(row["count"]) for row in task_rows}
        return {
            "plans": plan_counts,
            "tasks": task_counts,
            "total_plans": sum(plan_counts.values()),
            "total_tasks": sum(task_counts.values()),
            "sent_tasks": task_counts.get("sent", 0),
            "pending_tasks": task_counts.get("pending", 0),
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
                self.update_customer_outreach_state(
                    scope.sales_contact_key,
                    outreach_status=status,
                    outreach_plan_id=plan_id if status not in {"cancelled", "completed"} else "",
                )
        return self.get_outreach_plan(plan_id)

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
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM outreach_plans
                WHERE corp_id=?
                  AND lower(wechat)=lower(?)
                  AND (lower(external_userid)=lower(?) OR customer_id=?)
                  AND status IN ('draft', 'active', 'waiting', 'paused')
                """,
                (corp_id, wechat, external_userid or customer_id, customer_id),
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
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT sop_pack_id, sop_pack_name, sop_category, trigger_source,
                       reply_messages_json, sent_at
                FROM sop_send_tasks
                WHERE status='sent'
                  AND corp_id=?
                  AND lower(wechat)=lower(?)
                  AND (lower(external_userid)=lower(?) OR customer_id=?)
                  AND sent_at>=?
                ORDER BY sent_at DESC
                LIMIT 20
                """,
                (corp_id, wechat, external_userid or customer_id, customer_id, since),
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
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE t.status='sent'
                  AND t.sent_at>=? AND t.sent_at<?
                  AND p.corp_id=?
                  AND lower(p.wechat)=lower(?)
                  AND (lower(p.external_userid)=lower(?) OR p.customer_id=?)
                """,
                (start, end, corp_id, wechat, external_userid or customer_id, customer_id),
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
                    dumps(payload or {}),
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
        with self.store.connect() as conn:
            conversation = conn.execute(
                """
                SELECT id FROM conversations
                WHERE (customer_id=? OR external_userid=?) AND corp_id=? AND wechat=?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (customer_id, external_userid or customer_id, corp_id, wechat),
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

    def _decode_outreach_sop_plan(self, row: dict[str, Any]) -> dict[str, Any]:
        row["filters"] = loads_dict(row.get("filters_json"))
        row["last_run_summary"] = loads_dict(row.get("last_run_summary_json"))
        row.pop("filters_json", None)
        row.pop("last_run_summary_json", None)
        row["stats"] = self.outreach_sop_stats(str(row.get("id") or ""))
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
