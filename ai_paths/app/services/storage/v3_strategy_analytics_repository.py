from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.services.storage.serialization import dumps, loads_dict, utc_now_iso


_SUCCESS_DELIVERY_STATUS = "send_succeeded"
_PAID_STATES = {"paid", "waiting_schedule", "scheduled", "visited", "finished", "evaluated"}
_SCHEDULED_STATES = {"scheduled", "visited", "finished", "evaluated"}
_VISITED_STATES = {"visited", "finished", "evaluated"}
_FINISHED_STATES = {"finished", "evaluated"}


class V3StrategyAnalyticsRepositoryMixin:
    def record_v3_strategy_usage(
        self,
        *,
        conversation_id: str,
        final_state: dict[str, Any],
    ) -> dict[str, Any]:
        request_context = _dict(final_state.get("request_context"))
        interface_version = str(
            request_context.get("interface_version")
            or request_context.get("api_version")
            or ""
        ).strip().lower()
        if interface_version != "v3":
            return {"status": "skipped", "reason": "not_v3"}
        if bool(final_state.get("test_isolated") or request_context.get("test_isolated")):
            return {"status": "skipped", "reason": "test_isolated"}

        request_id = _text(final_state.get("request_id"))
        if not request_id:
            return {"status": "skipped", "reason": "missing_request_id"}

        event = _usage_event_from_state(
            conversation_id=conversation_id,
            final_state=final_state,
            now=utc_now_iso(),
        )
        with self.store.connect() as conn:
            existing = conn.execute(
                "SELECT id, created_at FROM v3_strategy_usage_events WHERE request_id=?",
                (request_id,),
            ).fetchone()
            dispatch = conn.execute(
                """
                SELECT id, status, confirmed_at, accepted_at, error_message, error_code
                FROM message_dispatches
                WHERE source_request_id=?
                ORDER BY created_at ASC LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            if dispatch is not None and not event["dispatch_id"]:
                event["dispatch_id"] = _text(dispatch["id"])
                event["delivery_status"] = _text(dispatch["status"])
                event["delivered_at"] = _text(dispatch["confirmed_at"] or dispatch["accepted_at"])
                event["failed_reason"] = _text(dispatch["error_message"] or dispatch["error_code"])
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO v3_strategy_usage_events
                        (id, request_id, conversation_id, customer_id, corp_id, wechat,
                         external_userid, user_id, sales_contact_key, occurred_at,
                         checkpoint_type_id, checkpoint_code, checkpoint_name,
                         checkpoint_tag_id, checkpoint_tag_name, friction_status,
                         sequence_id, sequence_name, sequence_step_id, action_code, action_name,
                         script_id, script_code, script_name, script_match_scope,
                         matched_count, candidate_count, sequence_candidate_count,
                         script_candidate_count, adopted, dispatch_id, delivery_status,
                         delivered_at, failed_reason, reply_source, reply_action,
                         intent_code, closing_strategy_code, emotion_before, emotion_after,
                         selector_status, fallback_used, payload_json, order_state_before_json,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _usage_insert_values(event),
                )
                event_id = event["id"]
                created = True
            else:
                event_id = _text(existing["id"])
                event["id"] = event_id
                conn.execute(
                    """
                    UPDATE v3_strategy_usage_events
                    SET conversation_id=?, customer_id=?, corp_id=?, wechat=?,
                        external_userid=?, user_id=?, sales_contact_key=?, occurred_at=?,
                        checkpoint_type_id=?, checkpoint_code=?, checkpoint_name=?,
                        checkpoint_tag_id=?, checkpoint_tag_name=?, friction_status=?,
                        sequence_id=?, sequence_name=?, sequence_step_id=?,
                        action_code=?, action_name=?, script_id=?, script_code=?,
                        script_name=?, script_match_scope=?, matched_count=?,
                        candidate_count=?, sequence_candidate_count=?, script_candidate_count=?,
                        adopted=?, dispatch_id=?, delivery_status=?, delivered_at=?,
                        failed_reason=?, reply_source=?, reply_action=?, intent_code=?,
                        closing_strategy_code=?, emotion_before=?, emotion_after=?,
                        selector_status=?, fallback_used=?, payload_json=?,
                        order_state_before_json=?, updated_at=?
                    WHERE request_id=?
                    """,
                    _usage_update_values(event),
                )
                created = False
        return {"status": "recorded", "id": event_id, "created": created}

    def link_v3_strategy_usage_dispatch(self, *, request_id: str, dispatch_id: str) -> dict[str, Any]:
        clean_request_id = _text(request_id)
        clean_dispatch_id = _text(dispatch_id)
        if not clean_request_id or not clean_dispatch_id:
            return {"updated": 0}
        with self.store.connect() as conn:
            dispatch = conn.execute(
                "SELECT status, confirmed_at, accepted_at, error_message, error_code FROM message_dispatches WHERE id=?",
                (clean_dispatch_id,),
            ).fetchone()
            status = _text(dispatch["status"]) if dispatch else ""
            delivered_at = _text(dispatch["confirmed_at"] or dispatch["accepted_at"]) if dispatch else ""
            failed_reason = _text(dispatch["error_message"] or dispatch["error_code"]) if dispatch else ""
            result = conn.execute(
                """
                UPDATE v3_strategy_usage_events
                SET dispatch_id=?, delivery_status=COALESCE(NULLIF(?, ''), delivery_status),
                    delivered_at=COALESCE(NULLIF(?, ''), delivered_at),
                    failed_reason=COALESCE(NULLIF(?, ''), failed_reason),
                    updated_at=?
                WHERE request_id=?
                """,
                (clean_dispatch_id, status, delivered_at, failed_reason, utc_now_iso(), clean_request_id),
            )
        return {"updated": int(result.rowcount or 0)}

    def update_v3_strategy_usage_delivery(
        self,
        *,
        dispatch_id: str,
        delivery_status: str,
        delivered_at: str = "",
        failed_reason: str = "",
    ) -> dict[str, Any]:
        clean_dispatch_id = _text(dispatch_id)
        if not clean_dispatch_id:
            return {"updated": 0}
        now = utc_now_iso()
        with self.store.connect() as conn:
            result = conn.execute(
                """
                UPDATE v3_strategy_usage_events
                SET delivery_status=?, delivered_at=COALESCE(NULLIF(?, ''), delivered_at),
                    failed_reason=COALESCE(NULLIF(?, ''), failed_reason), updated_at=?
                WHERE dispatch_id=?
                """,
                (_text(delivery_status), _text(delivered_at), _text(failed_reason), now, clean_dispatch_id),
            )
        return {"updated": int(result.rowcount or 0)}

    def refresh_v3_strategy_outcomes(self, *, limit: int = 100) -> dict[str, Any]:
        capped_limit = max(1, min(int(limit or 100), 500))
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT u.*
                FROM v3_strategy_usage_events u
                LEFT JOIN v3_strategy_outcome_events o ON o.usage_event_id=u.id
                WHERE o.usage_event_id IS NULL
                   OR o.updated_at < u.updated_at
                   OR o.updated_at < ?
                ORDER BY u.occurred_at DESC
                LIMIT ?
                """,
                ((_utc_now() - timedelta(hours=1)).isoformat(), capped_limit),
            ).fetchall()
            updated = 0
            for row in rows:
                event = dict(row)
                outcome = _outcome_for_usage(conn, event)
                conn.execute(
                    """
                    INSERT INTO v3_strategy_outcome_events
                        (usage_event_id, customer_replied_1h, customer_replied_6h,
                         customer_replied_24h, customer_replied_72h,
                         first_reply_after_at, first_reply_after_msgid,
                         order_state_before, order_state_after_24h,
                         order_state_after_72h, order_state_after_7d,
                         paid_after_24h, paid_after_72h, paid_after_7d,
                         scheduled_after_7d, visited_after_14d, finished_after_30d,
                         attribution_source, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(usage_event_id) DO UPDATE SET
                        customer_replied_1h=excluded.customer_replied_1h,
                        customer_replied_6h=excluded.customer_replied_6h,
                        customer_replied_24h=excluded.customer_replied_24h,
                        customer_replied_72h=excluded.customer_replied_72h,
                        first_reply_after_at=excluded.first_reply_after_at,
                        first_reply_after_msgid=excluded.first_reply_after_msgid,
                        order_state_before=excluded.order_state_before,
                        order_state_after_24h=excluded.order_state_after_24h,
                        order_state_after_72h=excluded.order_state_after_72h,
                        order_state_after_7d=excluded.order_state_after_7d,
                        paid_after_24h=excluded.paid_after_24h,
                        paid_after_72h=excluded.paid_after_72h,
                        paid_after_7d=excluded.paid_after_7d,
                        scheduled_after_7d=excluded.scheduled_after_7d,
                        visited_after_14d=excluded.visited_after_14d,
                        finished_after_30d=excluded.finished_after_30d,
                        attribution_source=excluded.attribution_source,
                        payload_json=excluded.payload_json,
                        updated_at=excluded.updated_at
                    """,
                    _outcome_values(outcome),
                )
                updated += 1
        return {"scanned": len(rows), "updated": updated}

    def v3_strategy_analytics_summary(self, **filters: Any) -> dict[str, Any]:
        where_sql, params = _analytics_filters(filters)
        with self.store.connect() as conn:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS usage_count,
                    SUM(CASE WHEN u.adopted=1 THEN 1 ELSE 0 END) AS adopted_count,
                    SUM(CASE WHEN u.dispatch_id<>'' THEN 1 ELSE 0 END) AS dispatch_count,
                    SUM(CASE WHEN u.delivery_status=? THEN 1 ELSE 0 END) AS delivery_success_count,
                    SUM(CASE WHEN COALESCE(o.customer_replied_24h, 0)=1 THEN 1 ELSE 0 END) AS replied_24h_count,
                    SUM(CASE WHEN COALESCE(o.paid_after_72h, 0)=1 THEN 1 ELSE 0 END) AS paid_72h_count,
                    SUM(CASE WHEN COALESCE(o.scheduled_after_7d, 0)=1 THEN 1 ELSE 0 END) AS scheduled_7d_count,
                    SUM(CASE WHEN u.selector_status IN ('empty','error') THEN 1 ELSE 0 END) AS selector_empty_or_error_count,
                    SUM(CASE WHEN u.fallback_used=1 THEN 1 ELSE 0 END) AS taxonomy_fallback_count
                FROM v3_strategy_usage_events u
                LEFT JOIN v3_strategy_outcome_events o ON o.usage_event_id=u.id
                {where_sql}
                """,
                (_SUCCESS_DELIVERY_STATUS, *params),
            ).fetchone()
        value = _analytics_counts(dict(row) if row else {})
        return {"filters": _clean_filters(filters), **value}

    def v3_strategy_analytics_by_dimension(self, *, dimension: str, **filters: Any) -> dict[str, Any]:
        specs = {
            "checkpoint": (
                "u.checkpoint_code, u.checkpoint_name, u.checkpoint_tag_id, u.checkpoint_tag_name",
                "u.checkpoint_code, u.checkpoint_name, u.checkpoint_tag_id, u.checkpoint_tag_name",
            ),
            "sequence": ("u.sequence_id, u.sequence_name", "u.sequence_id, u.sequence_name"),
            "script": ("u.script_id, u.script_code, u.script_name", "u.script_id, u.script_code, u.script_name"),
        }
        if dimension not in specs:
            raise ValueError(f"unsupported analytics dimension: {dimension}")
        select_keys, group_keys = specs[dimension]
        where_sql, params = _analytics_filters(filters)
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    {select_keys},
                    COUNT(*) AS usage_count,
                    SUM(CASE WHEN u.adopted=1 THEN 1 ELSE 0 END) AS adopted_count,
                    SUM(CASE WHEN u.dispatch_id<>'' THEN 1 ELSE 0 END) AS dispatch_count,
                    SUM(CASE WHEN u.delivery_status=? THEN 1 ELSE 0 END) AS delivery_success_count,
                    SUM(CASE WHEN COALESCE(o.customer_replied_24h, 0)=1 THEN 1 ELSE 0 END) AS replied_24h_count,
                    SUM(CASE WHEN COALESCE(o.paid_after_72h, 0)=1 THEN 1 ELSE 0 END) AS paid_72h_count,
                    SUM(CASE WHEN COALESCE(o.scheduled_after_7d, 0)=1 THEN 1 ELSE 0 END) AS scheduled_7d_count,
                    SUM(CASE WHEN u.selector_status IN ('empty','error') THEN 1 ELSE 0 END) AS selector_empty_or_error_count,
                    SUM(CASE WHEN u.fallback_used=1 THEN 1 ELSE 0 END) AS taxonomy_fallback_count
                FROM v3_strategy_usage_events u
                LEFT JOIN v3_strategy_outcome_events o ON o.usage_event_id=u.id
                {where_sql}
                GROUP BY {group_keys}
                ORDER BY usage_count DESC
                LIMIT ?
                """,
                (_SUCCESS_DELIVERY_STATUS, *params, _limit(filters.get("limit"), default=50, maximum=200)),
            ).fetchall()
        return {
            "dimension": dimension,
            "filters": _clean_filters(filters),
            "items": [_analytics_counts(dict(row)) for row in rows],
        }

    def v3_strategy_analytics_failures(self, **filters: Any) -> dict[str, Any]:
        where_sql, params = _analytics_filters(filters)
        failure_clause = (
            "(u.selector_status IN ('empty','error') OR u.adopted=0 "
            "OR (u.dispatch_id<>'' AND u.delivery_status NOT IN ('', ?, 'platform_accepted', 'created', 'sending')))"
        )
        prefix = "WHERE" if not where_sql else f"{where_sql} AND"
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT u.id, u.request_id, u.conversation_id, u.customer_id, u.corp_id,
                       u.wechat, u.external_userid, u.occurred_at,
                       u.checkpoint_code, u.checkpoint_name, u.checkpoint_tag_id, u.checkpoint_tag_name,
                       u.sequence_id, u.sequence_name, u.sequence_step_id,
                       u.action_code, u.action_name, u.script_id, u.script_code, u.script_name,
                       u.script_match_scope, u.selector_status, u.fallback_used,
                       u.adopted, u.dispatch_id, u.delivery_status, u.failed_reason,
                       u.reply_source, u.reply_action
                FROM v3_strategy_usage_events u
                {prefix} {failure_clause}
                ORDER BY u.occurred_at DESC
                LIMIT ?
                """,
                (*params, _SUCCESS_DELIVERY_STATUS, _limit(filters.get("limit"), default=50, maximum=200)),
            ).fetchall()
        return {"filters": _clean_filters(filters), "items": [_decode_usage_row(dict(row)) for row in rows]}


def _usage_event_from_state(*, conversation_id: str, final_state: dict[str, Any], now: str) -> dict[str, Any]:
    request_context = _dict(final_state.get("request_context"))
    route = _dict(final_state.get("semantic_route"))
    checkpoint = _dict(route.get("checkpoint"))
    sequence_match = _dict(route.get("sequence_match"))
    recall = _dict(final_state.get("sales_recall"))
    selector = _dict(recall.get("selector"))
    knowledge_use = _dict(final_state.get("reply_knowledge_use"))
    script = _selected_script(recall, knowledge_use)
    sequence = _selected_sequence(recall, sequence_match, knowledge_use)
    async_final = _dict(final_state.get("async_final_reply"))
    reply_control = _dict(final_state.get("reply_control"))
    control_async = _dict(reply_control.get("async_final"))
    order_state_before = _order_state_snapshot(final_state)
    sequence_candidate_count = len(_list(recall.get("sequence_candidates")))
    script_candidate_count = int(recall.get("candidate_count") or len(_list(recall.get("candidates"))))
    selected_script_ids = _string_list(knowledge_use.get("selected_script_ids"))
    fallback_used = _fallback_used(final_state, recall)
    payload = {
        "schema_version": "v3_strategy_usage_event_v1",
        "classification_status": _text(route.get("classification_status")),
        "support_level": _text(recall.get("support_level")),
        "selector_reason": _text(selector.get("reason")),
        "selected_script_ids": selected_script_ids,
        "script_query_sources": _query_sources(recall),
        "message_count": len(_list(final_state.get("reply_messages"))),
        "future_dimensions": {
            "intent_code": _intent_code(final_state),
            "closing_strategy_code": _closing_strategy_code(final_state),
            "emotion_before": _emotion_value(final_state, "before"),
            "emotion_after": _emotion_value(final_state, "after"),
        },
    }
    adopted = bool(knowledge_use.get("sequence_id") or selected_script_ids)
    return {
        "id": str(uuid4()),
        "request_id": _text(final_state.get("request_id")),
        "conversation_id": _text(conversation_id),
        "customer_id": _text(final_state.get("customer_id")),
        "corp_id": _text(final_state.get("corp_id")),
        "wechat": _text(final_state.get("wechat")),
        "external_userid": _text(final_state.get("external_userid")),
        "user_id": _text(final_state.get("user_id")),
        "sales_contact_key": _text(final_state.get("sales_contact_key")) or _sales_contact_key(final_state),
        "occurred_at": now,
        "checkpoint_type_id": _int(checkpoint.get("primary_type_id")),
        "checkpoint_code": _text(knowledge_use.get("checkpoint_code") or checkpoint.get("primary_code")),
        "checkpoint_name": _text(checkpoint.get("primary_type_name")),
        "checkpoint_tag_id": _int(checkpoint.get("primary_tag_id")),
        "checkpoint_tag_name": _text(checkpoint.get("primary_tag_name")),
        "friction_status": _text(
            route.get("friction_status")
            or checkpoint.get("friction_status")
            or route.get("classification_status")
        ),
        "sequence_id": _text(knowledge_use.get("sequence_id") or sequence.get("sequence_id")),
        "sequence_name": _text(knowledge_use.get("sequence_name") or sequence.get("sequence_name")),
        "sequence_step_id": _text(knowledge_use.get("step_id")),
        "action_code": _text(knowledge_use.get("action_code") or script.get("action_code")),
        "action_name": _text(script.get("action_name")),
        "script_id": _text(script.get("script_id") or script.get("id")),
        "script_code": _text(script.get("source_id") or script.get("script_code")),
        "script_name": _text(script.get("script_name")),
        "script_match_scope": _text(script.get("retrieval_match_scope") or script.get("match_scope")),
        "matched_count": sequence_candidate_count + script_candidate_count,
        "candidate_count": script_candidate_count,
        "sequence_candidate_count": sequence_candidate_count,
        "script_candidate_count": script_candidate_count,
        "adopted": 1 if adopted else 0,
        "dispatch_id": _text(async_final.get("dispatch_id") or control_async.get("dispatch_id")),
        "delivery_status": _text(async_final.get("status") or control_async.get("status")),
        "delivered_at": "",
        "failed_reason": _text(async_final.get("error") or control_async.get("error")),
        "reply_source": _text(final_state.get("reply_source")),
        "reply_action": _text(final_state.get("reply_action")),
        "intent_code": _intent_code(final_state),
        "closing_strategy_code": _closing_strategy_code(final_state),
        "emotion_before": _emotion_value(final_state, "before"),
        "emotion_after": _emotion_value(final_state, "after"),
        "selector_status": _text(selector.get("status")),
        "fallback_used": 1 if fallback_used else 0,
        "payload_json": dumps(payload),
        "order_state_before_json": dumps(order_state_before),
        "created_at": now,
        "updated_at": now,
    }


def _usage_insert_values(event: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(event[key] for key in (
        "id", "request_id", "conversation_id", "customer_id", "corp_id", "wechat",
        "external_userid", "user_id", "sales_contact_key", "occurred_at",
        "checkpoint_type_id", "checkpoint_code", "checkpoint_name", "checkpoint_tag_id",
        "checkpoint_tag_name", "friction_status", "sequence_id", "sequence_name",
        "sequence_step_id", "action_code", "action_name", "script_id", "script_code",
        "script_name", "script_match_scope", "matched_count", "candidate_count",
        "sequence_candidate_count", "script_candidate_count", "adopted", "dispatch_id",
        "delivery_status", "delivered_at", "failed_reason", "reply_source",
        "reply_action", "intent_code", "closing_strategy_code", "emotion_before",
        "emotion_after", "selector_status", "fallback_used", "payload_json",
        "order_state_before_json", "created_at", "updated_at",
    ))


def _usage_update_values(event: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(event[key] for key in (
        "conversation_id", "customer_id", "corp_id", "wechat", "external_userid",
        "user_id", "sales_contact_key", "occurred_at", "checkpoint_type_id",
        "checkpoint_code", "checkpoint_name", "checkpoint_tag_id", "checkpoint_tag_name",
        "friction_status", "sequence_id", "sequence_name", "sequence_step_id",
        "action_code", "action_name", "script_id", "script_code", "script_name",
        "script_match_scope", "matched_count", "candidate_count",
        "sequence_candidate_count", "script_candidate_count", "adopted",
        "dispatch_id", "delivery_status", "delivered_at", "failed_reason",
        "reply_source", "reply_action", "intent_code", "closing_strategy_code",
        "emotion_before", "emotion_after", "selector_status", "fallback_used",
        "payload_json", "order_state_before_json", "updated_at", "request_id",
    ))


def _selected_sequence(recall: dict[str, Any], sequence_match: dict[str, Any], knowledge_use: dict[str, Any]) -> dict[str, Any]:
    selected = _text(knowledge_use.get("sequence_id"))
    if not selected:
        selected_ids = _string_list(sequence_match.get("sequence_ids"))
        selected = selected_ids[0] if selected_ids else ""
    for item in _list(recall.get("sequence_candidates")):
        if isinstance(item, dict) and _text(item.get("sequence_id")) == selected:
            return item
    return {}


def _selected_script(recall: dict[str, Any], knowledge_use: dict[str, Any]) -> dict[str, Any]:
    selected_ids = set(_string_list(knowledge_use.get("selected_script_ids")))
    if not selected_ids:
        return {}
    for item in _list(recall.get("candidates")):
        if not isinstance(item, dict):
            continue
        aliases = {
            _text(item.get("script_id") or item.get("id")),
            _text(item.get("source_id") or item.get("script_code")),
            _text(item.get("id")),
            _text(item.get("script_code")),
        }
        if aliases & selected_ids:
            return item
    return {}


def _query_sources(recall: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "query_source": _text(item.get("query_source")),
            "match_scope": _text(item.get("match_scope")),
            "fallback_used": bool(item.get("fallback_used")),
            "total": _int(item.get("total")),
        }
        for item in _list(recall.get("script_query_results"))
        if isinstance(item, dict)
    ][:20]


def _fallback_used(final_state: dict[str, Any], recall: dict[str, Any]) -> bool:
    if _text(final_state.get("fallback_source")):
        return True
    selector = _dict(recall.get("selector"))
    if bool(selector.get("structure_retry_used")):
        return True
    for item in _list(recall.get("script_query_results")):
        if not isinstance(item, dict):
            continue
        if bool(item.get("fallback_used")):
            return True
        if _text(item.get("query_source")) == "taxonomy_action_coverage_fallback":
            return True
        if _text(item.get("match_scope")).startswith("taxonomy_"):
            return True
    for item in _list(recall.get("candidates")):
        if isinstance(item, dict) and _text(item.get("retrieval_match_scope")).startswith("taxonomy_"):
            return True
    return False


def _outcome_for_usage(conn: Any, event: dict[str, Any]) -> dict[str, Any]:
    usage_id = _text(event.get("id"))
    occurred_at = _text(event.get("occurred_at"))
    occurred_dt = _parse_dt(occurred_at) or _utc_now()
    first_reply = conn.execute(
        """
        SELECT id, created_at FROM messages
        WHERE conversation_id=? AND role='user' AND created_at>?
        ORDER BY created_at ASC LIMIT 1
        """,
        (_text(event.get("conversation_id")), occurred_at),
    ).fetchone()
    first_reply_at = _text(first_reply["created_at"]) if first_reply else ""
    first_reply_msgid = _text(first_reply["id"]) if first_reply else ""
    first_reply_dt = _parse_dt(first_reply_at) if first_reply_at else None
    order_before = loads_dict(_text(event.get("order_state_before_json"))).get("order_state", "")
    order_windows = _local_order_windows(conn, event, occurred_at=occurred_at)
    now = utc_now_iso()
    return {
        "usage_event_id": usage_id,
        "customer_replied_1h": _within(first_reply_dt, occurred_dt, hours=1),
        "customer_replied_6h": _within(first_reply_dt, occurred_dt, hours=6),
        "customer_replied_24h": _within(first_reply_dt, occurred_dt, hours=24),
        "customer_replied_72h": _within(first_reply_dt, occurred_dt, hours=72),
        "first_reply_after_at": first_reply_at,
        "first_reply_after_msgid": first_reply_msgid,
        "order_state_before": _text(order_before),
        "order_state_after_24h": order_windows["24h"],
        "order_state_after_72h": order_windows["72h"],
        "order_state_after_7d": order_windows["7d"],
        "paid_after_24h": 1 if order_windows["24h"] in _PAID_STATES else 0,
        "paid_after_72h": 1 if order_windows["72h"] in _PAID_STATES else 0,
        "paid_after_7d": 1 if order_windows["7d"] in _PAID_STATES else 0,
        "scheduled_after_7d": 1 if order_windows["7d"] in _SCHEDULED_STATES else 0,
        "visited_after_14d": 1 if order_windows["14d"] in _VISITED_STATES else 0,
        "finished_after_30d": 1 if order_windows["30d"] in _FINISHED_STATES else 0,
        "attribution_source": "local_messages_and_run_snapshots",
        "payload_json": dumps({"windows": order_windows}),
        "created_at": now,
        "updated_at": now,
    }


def _local_order_windows(conn: Any, event: dict[str, Any], *, occurred_at: str) -> dict[str, str]:
    windows = {"24h": "", "72h": "", "7d": "", "14d": "", "30d": ""}
    if not _text(event.get("conversation_id")):
        return windows
    rows = conn.execute(
        """
        SELECT output_snapshot, created_at FROM runs
        WHERE conversation_id=? AND created_at>?
        ORDER BY created_at ASC
        """,
        (_text(event.get("conversation_id")), occurred_at),
    ).fetchall()
    occurred_dt = _parse_dt(occurred_at) or _utc_now()
    limits = {
        "24h": occurred_dt + timedelta(hours=24),
        "72h": occurred_dt + timedelta(hours=72),
        "7d": occurred_dt + timedelta(days=7),
        "14d": occurred_dt + timedelta(days=14),
        "30d": occurred_dt + timedelta(days=30),
    }
    for row in rows:
        row_dt = _parse_dt(_text(row["created_at"]))
        if row_dt is None:
            continue
        state = _order_state_snapshot(loads_dict(row["output_snapshot"]))
        order_state = _text(state.get("order_state"))
        if not order_state:
            continue
        for key, limit in limits.items():
            if row_dt <= limit:
                windows[key] = order_state
    return windows


def _outcome_values(outcome: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(outcome[key] for key in (
        "usage_event_id", "customer_replied_1h", "customer_replied_6h",
        "customer_replied_24h", "customer_replied_72h", "first_reply_after_at",
        "first_reply_after_msgid", "order_state_before", "order_state_after_24h",
        "order_state_after_72h", "order_state_after_7d", "paid_after_24h",
        "paid_after_72h", "paid_after_7d", "scheduled_after_7d",
        "visited_after_14d", "finished_after_30d", "attribution_source",
        "payload_json", "created_at", "updated_at",
    ))


def _analytics_filters(filters: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []
    mapping = {
        "corp_id": "u.corp_id",
        "wechat": "u.wechat",
        "checkpoint_code": "u.checkpoint_code",
        "sequence_id": "u.sequence_id",
        "script_id": "u.script_id",
        "action_code": "u.action_code",
    }
    if _text(filters.get("started_from")):
        clauses.append("u.occurred_at>=?")
        params.append(_text(filters.get("started_from")))
    if _text(filters.get("started_to")):
        clauses.append("u.occurred_at<=?")
        params.append(_text(filters.get("started_to")))
    for key, column in mapping.items():
        value = _text(filters.get(key))
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    if filters.get("fallback_used") is not None:
        clauses.append("u.fallback_used=?")
        params.append(1 if bool(filters.get("fallback_used")) else 0)
    return ("WHERE " + " AND ".join(clauses) if clauses else "", tuple(params))


def _analytics_counts(row: dict[str, Any]) -> dict[str, Any]:
    usage_count = _int(row.get("usage_count"))
    adopted_count = _int(row.get("adopted_count"))
    dispatch_count = _int(row.get("dispatch_count"))
    delivery_success_count = _int(row.get("delivery_success_count"))
    return {
        **{key: value for key, value in row.items() if key not in {
            "usage_count", "adopted_count", "dispatch_count", "delivery_success_count",
            "replied_24h_count", "paid_72h_count", "scheduled_7d_count",
            "selector_empty_or_error_count", "taxonomy_fallback_count",
        }},
        "usage_count": usage_count,
        "adopted_count": adopted_count,
        "adoption_rate": _rate(adopted_count, usage_count),
        "dispatch_count": dispatch_count,
        "delivery_success_count": delivery_success_count,
        "delivery_success_rate": _rate(delivery_success_count, dispatch_count),
        "customer_replied_24h_count": _int(row.get("replied_24h_count")),
        "customer_replied_24h_rate": _rate(_int(row.get("replied_24h_count")), usage_count),
        "paid_72h_count": _int(row.get("paid_72h_count")),
        "paid_72h_rate": _rate(_int(row.get("paid_72h_count")), usage_count),
        "scheduled_7d_count": _int(row.get("scheduled_7d_count")),
        "scheduled_7d_rate": _rate(_int(row.get("scheduled_7d_count")), usage_count),
        "selector_empty_or_error_count": _int(row.get("selector_empty_or_error_count")),
        "taxonomy_fallback_count": _int(row.get("taxonomy_fallback_count")),
    }


def _decode_usage_row(row: dict[str, Any]) -> dict[str, Any]:
    row["adopted"] = bool(row.get("adopted"))
    row["fallback_used"] = bool(row.get("fallback_used"))
    return row


def _order_state_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    stored_snapshot = _dict(state.get("order_state_snapshot"))
    if stored_snapshot:
        return {
            "order_state": _text(stored_snapshot.get("order_state")),
            "deposit_state": _text(stored_snapshot.get("deposit_state")),
            "fee_paid": stored_snapshot.get("fee_paid", ""),
            "source": "run_order_state_snapshot",
        }
    customer_context = _dict(state.get("customer_context"))
    basic_info = _dict(customer_context.get("basic_info"))
    tool_results = _dict(state.get("tool_results"))
    order_context = _dict(tool_results.get("customer_order_context"))
    if "data" in order_context and isinstance(order_context.get("data"), dict):
        order_context = _dict(order_context.get("data"))
    order_state = _text(
        state.get("order_state")
        or basic_info.get("order_state")
        or order_context.get("order_state")
        or order_context.get("status_text")
        or order_context.get("status")
    )
    deposit_state = _text(
        state.get("deposit_state")
        or basic_info.get("deposit_state")
        or order_context.get("deposit_state")
    )
    return {
        "order_state": order_state,
        "deposit_state": deposit_state,
        "fee_paid": order_context.get("fee_paid", basic_info.get("fee_paid", "")),
        "source": "state_customer_context_or_order_tool",
    }


def _intent_code(state: dict[str, Any]) -> str:
    intent = _dict(state.get("realtime_intent"))
    return _text(intent.get("code") or intent.get("intent_code") or intent.get("intent") or intent.get("name"))


def _closing_strategy_code(state: dict[str, Any]) -> str:
    closing = _dict(state.get("closing_decision"))
    shadow = _dict(state.get("closing_sequence_shadow"))
    return _text(
        closing.get("strategy_code")
        or closing.get("closing_strategy_code")
        or closing.get("code")
        or shadow.get("strategy_code")
    )


def _emotion_value(state: dict[str, Any], key: str) -> str:
    emotion = _dict(state.get("emotion_decision"))
    if key == "before":
        return _text(emotion.get("before") or emotion.get("emotion_before") or emotion.get("previous_emotion"))
    return _text(emotion.get("after") or emotion.get("emotion_after") or emotion.get("current_emotion") or emotion.get("emotion"))


def _sales_contact_key(state: dict[str, Any]) -> str:
    return "|".join(
        _text(state.get(key))
        for key in ("corp_id", "wechat", "external_userid", "customer_id")
    )


def _clean_filters(filters: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in filters.items():
        if key == "limit":
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        output[key] = value
    return output


def _within(value: datetime | None, start: datetime, *, hours: int) -> int:
    if value is None:
        return 0
    return 1 if start < value <= start + timedelta(hours=hours) else 0


def _parse_dt(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(value or default), maximum))
    except (TypeError, ValueError):
        return default


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    return [_text(item) for item in _list(value) if _text(item)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
