from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.services.storage.serialization import dumps, loads_dict, loads_list, utc_now_iso


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


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _outreach_sent_count_72h_blocked(value: Any) -> bool:
    try:
        return int(value or 0) >= 3
    except (TypeError, ValueError):
        return False


def _recent_outreach_24h_blocked(value: str | None) -> bool:
    parsed = _parse_iso(value)
    if not parsed:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - parsed < timedelta(hours=24)


def _candidate_has_effective_intent(item: dict[str, Any]) -> bool:
    portrait = item.get("portrait") if isinstance(item.get("portrait"), dict) else {}
    basic_info = item.get("basic_info") if isinstance(item.get("basic_info"), dict) else {}
    projects = portrait.get("projects") if isinstance(portrait.get("projects"), list) else []
    signals = [
        item.get("last_customer_message"),
        item.get("latest_event_summary"),
        item.get("lifecycle_stage"),
        portrait.get("summary"),
        portrait.get("main_objection"),
        portrait.get("next_sales_strategy"),
        basic_info.get("preferred_store_name"),
        basic_info.get("city"),
        *projects,
    ]
    return any(_string(value) for value in signals)


def _candidate_suppressed_by_risk(item: dict[str, Any]) -> bool:
    if _string(item.get("last_manual_takeover_at")):
        return True
    portrait = item.get("portrait") if isinstance(item.get("portrait"), dict) else {}
    basic_info = item.get("basic_info") if isinstance(item.get("basic_info"), dict) else {}
    combined = " ".join(
        _string(value)
        for value in (
            item.get("lifecycle_stage"),
            item.get("last_customer_message"),
            item.get("latest_event_summary"),
            portrait.get("summary"),
            portrait.get("main_objection"),
            portrait.get("decision_stage"),
            basic_info.get("deposit_state"),
            basic_info.get("appointment_status"),
        )
    )
    blocked_keywords = (
        "投诉",
        "退款",
        "售后纠纷",
        "严重不满",
        "人工接管",
        "拉黑",
        "别联系",
        "不要联系",
        "不需要了",
        "不考虑了",
        "已付款",
        "已支付",
        "预约成功",
        "已到店",
    )
    return any(keyword in combined for keyword in blocked_keywords)


class OutreachRepositoryMixin:
    def touch_customer_message_time(self, customer_id: str, *, field: str, value: str | None = None) -> None:
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
                (customer_id, now),
            )
            conn.execute(
                f"UPDATE customer_memory SET {field}=?, updated_at=? WHERE customer_id=?",
                (now, now, customer_id),
            )

    def update_customer_outreach_state(
        self,
        customer_id: str,
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
                (customer_id, now),
            )
            conn.execute(
                """
                UPDATE customer_memory
                SET outreach_status=?, outreach_plan_id=?, last_outreach_at=COALESCE(NULLIF(?, ''), last_outreach_at),
                    updated_at=?
                WHERE customer_id=?
                """,
                (outreach_status, outreach_plan_id, last_outreach_at, now, customer_id),
            )

    def list_outreach_candidates(
        self,
        *,
        limit: int = 50,
        silent_minutes_min: int = 60,
        outreach_status: str = "",
        lifecycle_stage: str = "",
        no_plan_only: bool = False,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        clauses = ["1=1"]
        if outreach_status:
            clauses.append("COALESCE(cm.outreach_status, 'none')=?")
            params.append(outreach_status)
        if lifecycle_stage:
            clauses.append("cm.lifecycle_stage=?")
            params.append(lifecycle_stage)
        if no_plan_only:
            clauses.append("(cm.outreach_plan_id='' OR cm.outreach_plan_id IS NULL)")
        cutoff_72h = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        params.append(max(1, min(limit, 200)))
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    cm.customer_id,
                    cm.portrait,
                    cm.basic_info,
                    cm.lifecycle_stage,
                    cm.last_customer_message_at,
                    cm.last_staff_message_at,
                    cm.last_ai_reply_at,
                    cm.last_manual_takeover_at,
                    cm.last_outreach_at,
                    COALESCE(cm.outreach_status, 'none') AS outreach_status,
                    COALESCE(cm.outreach_plan_id, '') AS outreach_plan_id,
                    cm.updated_at,
                    c.external_userid,
                    c.corp_id,
                    c.user_id,
                    c.wechat,
                    c.title,
                    (SELECT content FROM messages m WHERE m.conversation_id=c.id AND m.role='user' ORDER BY created_at DESC LIMIT 1) AS last_customer_message,
                    (SELECT summary FROM history_events e WHERE e.customer_id=cm.customer_id ORDER BY created_at DESC LIMIT 1) AS latest_event_summary,
                    (SELECT COUNT(*) FROM outreach_tasks t WHERE t.customer_id=cm.customer_id AND t.status='sent' AND t.sent_at>=?) AS outreach_sent_count_72h
                FROM customer_memory cm
                LEFT JOIN conversations c ON c.customer_id=cm.customer_id
                    AND c.updated_at=(SELECT MAX(c2.updated_at) FROM conversations c2 WHERE c2.customer_id=cm.customer_id)
                WHERE {' AND '.join(clauses)}
                ORDER BY cm.last_customer_message_at DESC, cm.updated_at DESC
                LIMIT ?
                """,
                [cutoff_72h, *params],
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["portrait"] = loads_dict(item.get("portrait"))
            item["basic_info"] = loads_dict(item.get("basic_info"))
            item["silent_minutes"] = _silent_minutes(item.get("last_customer_message_at"))
            if _recent_outreach_24h_blocked(item.get("last_outreach_at")):
                continue
            if _outreach_sent_count_72h_blocked(item.get("outreach_sent_count_72h")):
                continue
            if _candidate_suppressed_by_risk(item):
                continue
            if not _candidate_has_effective_intent(item):
                continue
            if item["silent_minutes"] >= silent_minutes_min:
                items.append(item)
        return items

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
    ) -> dict[str, Any]:
        now = utc_now_iso()
        plan_id = str(uuid4())
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO outreach_plans
                    (id, customer_id, corp_id, user_id, wechat, external_userid, status,
                     customer_stage, stall_reason, customer_psychology, plan_goal,
                     source_snapshot, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
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
        self.update_customer_outreach_state(customer_id, outreach_status="draft", outreach_plan_id=plan_id)
        return self.get_outreach_plan(plan_id)

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

    def get_active_outreach_plan_for_customer(self, customer_id: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM outreach_plans
                WHERE customer_id=? AND status IN ('draft', 'active', 'waiting', 'paused')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (customer_id,),
            ).fetchone()
        return self.get_outreach_plan(row["id"]) if row else {}

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
            plan = conn.execute("SELECT customer_id FROM outreach_plans WHERE id=?", (plan_id,)).fetchone()
        if plan:
            self.update_customer_outreach_state(
                str(plan["customer_id"]),
                outreach_status=status,
                outreach_plan_id=plan_id if status not in {"cancelled", "completed"} else "",
            )
        return self.get_outreach_plan(plan_id)

    def list_due_outreach_tasks(self, *, limit: int = 20, now: str | None = None) -> list[dict[str, Any]]:
        now_value = now or utc_now_iso()
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.*, p.corp_id, p.user_id, p.wechat, p.external_userid, p.status AS plan_status
                FROM outreach_tasks t
                JOIN outreach_plans p ON p.id=t.plan_id
                WHERE t.status='pending'
                  AND t.scheduled_at<=?
                  AND p.status IN ('active', 'waiting')
                ORDER BY t.scheduled_at ASC
                LIMIT ?
                """,
                (now_value, max(1, min(limit, 100))),
            ).fetchall()
        return [self._decode_outreach_task(dict(row)) for row in rows]

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

    def recent_customer_context(self, customer_id: str) -> dict[str, Any]:
        memory = self.load_memory(customer_id) or {"customer_id": customer_id, "history_events": []}
        with self.store.connect() as conn:
            conversation = conn.execute(
                "SELECT id FROM conversations WHERE customer_id=? ORDER BY updated_at DESC LIMIT 1",
                (customer_id,),
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
            ):
                row[key] = _string(ai_result.get(key))
            row["customer_stage"] = row.get("customer_stage") or _string(ai_result.get("conversion_stage"))
        return row

    @staticmethod
    def _decode_outreach_task(row: dict[str, Any]) -> dict[str, Any]:
        raw_sources = loads_list(row.get("content_sources"))
        policy_items = [item for item in raw_sources if isinstance(item, dict)]
        row["content_sources"] = [_string(item) for item in raw_sources if not isinstance(item, dict) and _string(item)]
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
