from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from app.services.customer_order_context import order_status_text
from app.services.storage.serialization import dumps, loads_dict, loads_list, utc_now_iso


_SUCCESS_DELIVERY_STATUS = "send_succeeded"
_PAID_STATES = {"paid", "waiting_schedule", "scheduled", "visited", "finished", "evaluated"}
_SCHEDULED_STATES = {"scheduled", "visited", "finished", "evaluated"}
_VISITED_STATES = {"visited", "finished", "evaluated"}
_FINISHED_STATES = {"finished", "evaluated"}
_ORDER_WINDOW_POLL_TOLERANCE_SECONDS = 2 * 60 * 60
_USAGE_COLUMNS = (
    "id", "request_id", "conversation_id", "customer_id", "corp_id", "wechat",
    "external_userid", "user_id", "sales_contact_key", "occurred_at",
    "checkpoint_type_id", "checkpoint_code", "checkpoint_name", "checkpoint_tag_id",
    "checkpoint_tag_name", "friction_status", "sequence_id", "sequence_name",
    "sequence_step_id", "action_code", "action_name", "script_id", "script_code",
    "script_name", "script_match_scope", "matched_count", "candidate_count",
    "sequence_candidate_count", "script_candidate_count", "adopted", "dispatch_id",
    "delivery_status", "delivered_at", "failed_reason", "reply_source", "reply_action",
    "intent_code", "closing_strategy_code", "emotion_before", "emotion_after",
    "policy_version", "decision_status", "intent_confidence", "intent_secondary_json",
    "emotion_confidence", "emotion_pressure", "emotion_flow_action", "closing_action",
    "closing_node_key", "closing_trigger", "closing_customer_state", "closing_pressure",
    "cardpoint_category_key", "cardpoint_state", "decision_reasons_json",
    "decision_evidence_refs_json", "selector_status", "fallback_used", "payload_json",
    "order_state_before_json", "customer_turn_eligible", "created_at", "updated_at",
)
_USAGE_UPDATE_COLUMNS = tuple(
    column for column in _USAGE_COLUMNS if column not in {"id", "request_id", "created_at"}
)


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
                previous = _previous_usage_for_event(conn, event)
                conn.execute(
                    f"INSERT INTO v3_strategy_usage_events ({', '.join(_USAGE_COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in _USAGE_COLUMNS)})",
                    _usage_insert_values(event),
                )
                event_id = event["id"]
                created = True
                if event["customer_turn_eligible"] and previous is not None:
                    _link_previous_usage(conn, previous=previous, current=event)
            else:
                event_id = _text(existing["id"])
                event["id"] = event_id
                conn.execute(
                    "UPDATE v3_strategy_usage_events SET "
                    + ", ".join(
                        (
                            "emotion_after=COALESCE(NULLIF(?, ''), emotion_after)"
                            if column == "emotion_after"
                            else f"{column}=?"
                        )
                        for column in _USAGE_UPDATE_COLUMNS
                    )
                    + " WHERE request_id=?",
                    _usage_update_values(event),
                )
                created = False
        return {"status": "recorded", "id": event_id, "created": created}

    def latest_v3_strategy_state(
        self,
        sales_contact_key: str,
        exclude_request_id: str = "",
        *,
        corp_id: str = "",
        wechat: str = "",
        external_userid: str = "",
        customer_id: str = "",
    ) -> dict[str, Any]:
        boundary = _validated_contact_boundary(
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            customer_id=customer_id,
        )
        if boundary is None:
            return {}
        corp_id, wechat, external_userid, customer_id = boundary
        clauses = [
            "u.sales_contact_key=?", "u.corp_id=?", "u.wechat=?",
            "u.customer_turn_eligible=1", "u.decision_status IN ('ok', 'degraded')",
        ]
        params: list[Any] = [sales_contact_key, corp_id, wechat]
        identity_clauses: list[str] = []
        if external_userid:
            identity_clauses.append("u.external_userid=?")
            params.append(external_userid)
        if customer_id:
            identity_clauses.append("u.customer_id=?")
            params.append(customer_id)
        clauses.append(f"({' OR '.join(identity_clauses)})")
        if _text(exclude_request_id):
            clauses.append("u.request_id<>?")
            params.append(_text(exclude_request_id))
        with self.store.connect() as conn:
            row = conn.execute(
                f"""
                SELECT u.request_id, u.occurred_at, u.intent_code, u.emotion_before,
                       u.closing_strategy_code, u.closing_action, u.closing_node_key,
                       u.checkpoint_code, u.cardpoint_category_key, u.cardpoint_state,
                       u.delivery_status, u.delivered_at, u.decision_status,
                       o.customer_replied_1h, o.customer_replied_6h,
                       o.customer_replied_24h, o.customer_replied_72h,
                       o.first_reply_after_at, o.order_state_before,
                       o.order_state_after_24h, o.order_state_after_72h,
                       o.order_state_after_7d
                FROM v3_strategy_usage_events u
                LEFT JOIN v3_strategy_outcome_events o ON o.usage_event_id=u.id
                WHERE {' AND '.join(clauses)}
                ORDER BY u.occurred_at DESC, u.created_at DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        if row is None:
            return {}
        value = dict(row)
        order_before = _text(value.get("order_state_before"))
        order_after = next(
            (
                _text(value.get(key))
                for key in ("order_state_after_7d", "order_state_after_72h", "order_state_after_24h")
                if _text(value.get(key))
            ),
            order_before,
        )
        return {
            "previous_intent": _text(value.get("intent_code")),
            "previous_emotion": _text(value.get("emotion_before")),
            "closing_sequence_key": _text(value.get("closing_strategy_code")),
            "closing_node_key": _text(value.get("closing_node_key")),
            "active_cardpoint": (
                _text(value.get("cardpoint_category_key"))
                if _text(value.get("cardpoint_state")) in {"active", "repeated"}
                else ""
            ),
            "delivered": bool(
                _text(value.get("delivered_at"))
                or _text(value.get("delivery_status")) in {_SUCCESS_DELIVERY_STATUS, "platform_accepted"}
            ),
            "customer_replied": bool(
                _text(value.get("first_reply_after_at"))
                or any(_int(value.get(key)) for key in (
                    "customer_replied_1h", "customer_replied_6h",
                    "customer_replied_24h", "customer_replied_72h",
                ))
            ),
            "order_changed": bool(order_before and order_after and order_before != order_after),
            "request_id": _text(value.get("request_id")),
            "occurred_at": _text(value.get("occurred_at")),
            "decision_status": _text(value.get("decision_status")),
        }

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

    def refresh_v3_strategy_outcomes(
        self,
        *,
        limit: int = 100,
        order_snapshot_provider: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        order_provider_max_concurrency: int = 4,
    ) -> dict[str, Any]:
        capped_limit = max(1, min(int(limit or 100), 500))
        events: list[dict[str, Any]] = []
        provider_events: list[dict[str, Any]] = []
        with self.store.connect() as conn:
            now = _utc_now()
            rows = conn.execute(
                """
                SELECT u.*
                FROM v3_strategy_usage_events u
                LEFT JOIN v3_strategy_outcome_events o ON o.usage_event_id=u.id
                WHERE u.customer_turn_eligible=1 AND (
                   o.usage_event_id IS NULL
                   OR o.updated_at < u.updated_at
                   OR (
                        u.delivered_at<>''
                        AND
                        COALESCE(NULLIF(u.delivered_at, ''), u.occurred_at) >= ?
                        AND COALESCE(NULLIF(o.order_last_refreshed_at, ''), '') < ?
                        AND (
                            (COALESCE(NULLIF(u.delivered_at, ''), u.occurred_at) <= ? AND COALESCE(o.order_state_after_24h, '')='')
                            OR (COALESCE(NULLIF(u.delivered_at, ''), u.occurred_at) <= ? AND COALESCE(o.order_state_after_72h, '')='')
                            OR (COALESCE(NULLIF(u.delivered_at, ''), u.occurred_at) <= ? AND COALESCE(o.order_state_after_7d, '')='')
                            OR (COALESCE(NULLIF(u.delivered_at, ''), u.occurred_at) <= ? AND COALESCE(o.order_state_after_14d, '')='')
                            OR (COALESCE(NULLIF(u.delivered_at, ''), u.occurred_at) <= ? AND COALESCE(o.order_state_after_30d, '')='')
                        )
                   ))
                ORDER BY
                    CASE WHEN o.usage_event_id IS NULL THEN 0 ELSE 1 END,
                    COALESCE(NULLIF(o.order_last_refreshed_at, ''), NULLIF(o.updated_at, ''), u.occurred_at) ASC,
                    u.occurred_at ASC
                LIMIT ?
                """,
                (
                    (now - timedelta(days=31)).isoformat(),
                    (now - timedelta(hours=1)).isoformat(),
                    (now - timedelta(hours=24)).isoformat(),
                    (now - timedelta(hours=72)).isoformat(),
                    (now - timedelta(days=7)).isoformat(),
                    (now - timedelta(days=14)).isoformat(),
                    (now - timedelta(days=30)).isoformat(),
                    capped_limit,
                ),
            ).fetchall()
            updated = 0
            for row in rows:
                event = dict(row)
                events.append(event)
                outcome = _outcome_for_usage(conn, event)
                if _order_provider_due(event, outcome=outcome, now=now):
                    provider_events.append(event)
                conn.execute(
                    """
                    INSERT INTO v3_strategy_outcome_events
                        (usage_event_id, customer_replied_1h, customer_replied_6h,
                         customer_replied_24h, customer_replied_72h,
                         first_reply_after_at, first_reply_after_msgid,
                         order_state_before, order_state_after_24h,
                         order_state_after_72h, order_state_after_7d,
                         order_state_after_14d, order_state_after_30d,
                         paid_after_24h, paid_after_72h, paid_after_7d,
                         scheduled_after_7d, visited_after_14d, finished_after_30d,
                         attribution_source, next_usage_event_id, next_intent_code,
                         next_emotion_code, emotion_transition, attribution_anchor_source,
                         order_source, order_query_status, order_query_error,
                         order_last_refreshed_at, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(usage_event_id) DO UPDATE SET
                        customer_replied_1h=excluded.customer_replied_1h,
                        customer_replied_6h=excluded.customer_replied_6h,
                        customer_replied_24h=excluded.customer_replied_24h,
                        customer_replied_72h=excluded.customer_replied_72h,
                        first_reply_after_at=excluded.first_reply_after_at,
                        first_reply_after_msgid=excluded.first_reply_after_msgid,
                        order_state_before=excluded.order_state_before,
                        order_state_after_24h=CASE WHEN attribution_anchor_source='unknown' AND excluded.attribution_anchor_source<>'unknown' THEN excluded.order_state_after_24h ELSE COALESCE(NULLIF(excluded.order_state_after_24h, ''), order_state_after_24h) END,
                        order_state_after_72h=CASE WHEN attribution_anchor_source='unknown' AND excluded.attribution_anchor_source<>'unknown' THEN excluded.order_state_after_72h ELSE COALESCE(NULLIF(excluded.order_state_after_72h, ''), order_state_after_72h) END,
                        order_state_after_7d=CASE WHEN attribution_anchor_source='unknown' AND excluded.attribution_anchor_source<>'unknown' THEN excluded.order_state_after_7d ELSE COALESCE(NULLIF(excluded.order_state_after_7d, ''), order_state_after_7d) END,
                        order_state_after_14d=CASE WHEN attribution_anchor_source='unknown' AND excluded.attribution_anchor_source<>'unknown' THEN excluded.order_state_after_14d ELSE COALESCE(NULLIF(excluded.order_state_after_14d, ''), order_state_after_14d) END,
                        order_state_after_30d=CASE WHEN attribution_anchor_source='unknown' AND excluded.attribution_anchor_source<>'unknown' THEN excluded.order_state_after_30d ELSE COALESCE(NULLIF(excluded.order_state_after_30d, ''), order_state_after_30d) END,
                        paid_after_24h=CASE WHEN attribution_anchor_source='unknown' AND excluded.attribution_anchor_source<>'unknown' THEN excluded.paid_after_24h WHEN paid_after_24h=1 OR excluded.paid_after_24h=1 THEN 1 ELSE 0 END,
                        paid_after_72h=CASE WHEN attribution_anchor_source='unknown' AND excluded.attribution_anchor_source<>'unknown' THEN excluded.paid_after_72h WHEN paid_after_72h=1 OR excluded.paid_after_72h=1 THEN 1 ELSE 0 END,
                        paid_after_7d=CASE WHEN attribution_anchor_source='unknown' AND excluded.attribution_anchor_source<>'unknown' THEN excluded.paid_after_7d WHEN paid_after_7d=1 OR excluded.paid_after_7d=1 THEN 1 ELSE 0 END,
                        scheduled_after_7d=CASE WHEN attribution_anchor_source='unknown' AND excluded.attribution_anchor_source<>'unknown' THEN excluded.scheduled_after_7d WHEN scheduled_after_7d=1 OR excluded.scheduled_after_7d=1 THEN 1 ELSE 0 END,
                        visited_after_14d=CASE WHEN attribution_anchor_source='unknown' AND excluded.attribution_anchor_source<>'unknown' THEN excluded.visited_after_14d WHEN visited_after_14d=1 OR excluded.visited_after_14d=1 THEN 1 ELSE 0 END,
                        finished_after_30d=CASE WHEN attribution_anchor_source='unknown' AND excluded.attribution_anchor_source<>'unknown' THEN excluded.finished_after_30d WHEN finished_after_30d=1 OR excluded.finished_after_30d=1 THEN 1 ELSE 0 END,
                        attribution_source=excluded.attribution_source,
                        attribution_anchor_source=excluded.attribution_anchor_source,
                        payload_json=excluded.payload_json,
                        updated_at=excluded.updated_at
                    """,
                    _outcome_values(outcome),
                )
                updated += 1
        provider_calls = 0
        provider_errors = 0
        if order_snapshot_provider is not None:
            snapshots = _collect_order_snapshots(
                provider_events,
                order_snapshot_provider,
                max_concurrency=order_provider_max_concurrency,
            )
            for event, snapshot in snapshots:
                provider_calls += 1
                if (
                    _text(snapshot.get("status")).lower() in {"error", "failed", "unavailable"}
                    or _text(snapshot.get("error"))
                ):
                    provider_errors += 1
                with self.store.connect() as conn:
                    _apply_order_snapshot(conn, event=event, snapshot=snapshot)
        return {
            "scanned": len(events),
            "updated": updated,
            "order_provider_calls": provider_calls,
            "order_provider_errors": provider_errors,
        }


    def v3_strategy_analytics_summary(self, **filters: Any) -> dict[str, Any]:
        where_sql, params = _analytics_filters(filters)
        order_72h_cutoff = (_utc_now() - timedelta(hours=72)).isoformat()
        order_7d_cutoff = (_utc_now() - timedelta(days=7)).isoformat()
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
                    SUM(CASE WHEN u.decision_status IN ('ok', 'degraded') THEN 1 ELSE 0 END) AS decision_coverage_count,
                    SUM(CASE WHEN u.policy_version<>'' AND u.decision_status NOT IN ('not_enabled', 'system_guard', 'skipped') THEN 1 ELSE 0 END) AS decision_eligible_count,
                    SUM(CASE WHEN u.decision_status='degraded' THEN 1 ELSE 0 END) AS decision_degraded_count,
                    SUM(CASE WHEN COALESCE(o.attribution_anchor_source, 'unknown')<>'unknown' THEN 1 ELSE 0 END) AS delivered_attribution_count,
                    SUM(CASE WHEN COALESCE(o.attribution_anchor_source, 'unknown')='unknown' THEN 1 ELSE 0 END) AS delivery_unknown_count,
                    SUM(CASE WHEN o.order_query_status IN ('ok', 'success', 'backfill_current_only', 'insufficient_baseline') THEN 1 ELSE 0 END) AS order_query_success_count,
                    SUM(CASE WHEN o.order_query_status<>'' THEN 1 ELSE 0 END) AS order_query_attempt_count,
                    SUM(CASE WHEN COALESCE(o.order_source, '')<>'' AND (
                                  COALESCE(o.order_state_after_24h, '')<>'' OR
                                  COALESCE(o.order_state_after_72h, '')<>'' OR
                                  COALESCE(o.order_state_after_7d, '')<>'' OR
                                  COALESCE(o.order_state_after_14d, '')<>'' OR
                                  COALESCE(o.order_state_after_30d, '')<>''
                             ) THEN 1 ELSE 0 END) AS order_attribution_complete_count,
                    SUM(CASE WHEN COALESCE(o.attribution_anchor_source, 'unknown')<>'unknown'
                              AND COALESCE(u.delivered_at, '')<=?
                              AND COALESCE(o.order_state_before, '') NOT IN ('paid','waiting_schedule','scheduled','visited','finished','evaluated')
                              AND COALESCE(o.order_state_after_72h, '')<>'' THEN 1 ELSE 0 END) AS order_outcome_eligible_count,
                    SUM(CASE WHEN COALESCE(o.attribution_anchor_source, 'unknown')<>'unknown'
                              AND COALESCE(u.delivered_at, '')<=?
                              AND COALESCE(o.order_state_before, '') NOT IN ('scheduled','visited','finished','evaluated')
                              AND COALESCE(o.order_state_after_7d, '')<>'' THEN 1 ELSE 0 END) AS order_7d_eligible_count,
                    SUM(CASE WHEN COALESCE(o.attribution_anchor_source, 'unknown')<>'unknown'
                              AND COALESCE(u.delivered_at, '')<=?
                              AND COALESCE(o.order_state_before, '') NOT IN ('paid','waiting_schedule','scheduled','visited','finished','evaluated')
                              AND COALESCE(o.order_state_after_72h, '')='' THEN 1 ELSE 0 END) AS order_outcome_unknown_count,
                    SUM(CASE WHEN COALESCE(o.attribution_anchor_source, 'unknown')<>'unknown'
                              AND COALESCE(u.delivered_at, '')>? THEN 1 ELSE 0 END) AS order_outcome_not_due_count,
                    SUM(CASE WHEN o.order_query_status='backfill_current_only' THEN 1 ELSE 0 END) AS order_backfill_current_only_count,
                    SUM(CASE WHEN u.decision_reasons_json LIKE '%"explicit_exit_requires_complete"%'
                                  OR u.decision_reasons_json LIKE '%"explicit_exit_same_turn_sales_conflict"%'
                             THEN 1 ELSE 0 END) AS hard_stop_wrong_advance_count,
                    SUM(CASE WHEN u.decision_reasons_json LIKE '%"new_blocker_requires_pause"%'
                                  OR u.decision_reasons_json LIKE '%"active_cardpoint_requires_pause"%'
                                  OR u.decision_reasons_json LIKE '%"active_cardpoint_same_turn_sales_conflict"%'
                             THEN 1 ELSE 0 END) AS new_blocker_not_paused_count,
                    SUM(CASE WHEN u.selector_status IN ('empty','error') THEN 1 ELSE 0 END) AS selector_empty_or_error_count,
                    SUM(CASE WHEN u.fallback_used=1 THEN 1 ELSE 0 END) AS taxonomy_fallback_count
                FROM v3_strategy_usage_events u
                LEFT JOIN v3_strategy_outcome_events o ON o.usage_event_id=u.id
                {where_sql}
                """,
                (
                    _SUCCESS_DELIVERY_STATUS, order_72h_cutoff, order_7d_cutoff,
                    order_72h_cutoff, order_72h_cutoff, *params,
                ),
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
            "intent": ("u.intent_code", "u.intent_code"),
            "emotion": ("u.emotion_before AS emotion_code", "u.emotion_before"),
            "closing": (
                "u.closing_strategy_code AS closing_sequence_key, u.closing_action, u.closing_node_key",
                "u.closing_strategy_code, u.closing_action, u.closing_node_key",
            ),
            "transitions": (
                "u.intent_code, o.next_intent_code, u.emotion_before AS emotion_code, "
                "o.next_emotion_code, o.emotion_transition",
                "u.intent_code, o.next_intent_code, u.emotion_before, "
                "o.next_emotion_code, o.emotion_transition",
            ),
        }
        if dimension not in specs:
            raise ValueError(f"unsupported analytics dimension: {dimension}")
        select_keys, group_keys = specs[dimension]
        where_sql, params = _analytics_filters(filters)
        if dimension == "transitions":
            transition_clause = (
                "COALESCE(o.next_usage_event_id, '')<>'' AND "
                "(COALESCE(o.next_intent_code, '')<>'' OR COALESCE(o.next_emotion_code, '')<>'')"
            )
            where_sql = (
                f"{where_sql} AND {transition_clause}"
                if where_sql
                else f"WHERE {transition_clause}"
            )
        order_72h_cutoff = (_utc_now() - timedelta(hours=72)).isoformat()
        order_7d_cutoff = (_utc_now() - timedelta(days=7)).isoformat()
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
                    SUM(CASE WHEN u.decision_status IN ('ok', 'degraded') THEN 1 ELSE 0 END) AS decision_coverage_count,
                    SUM(CASE WHEN u.policy_version<>'' AND u.decision_status NOT IN ('not_enabled', 'system_guard', 'skipped') THEN 1 ELSE 0 END) AS decision_eligible_count,
                    SUM(CASE WHEN u.decision_status='degraded' THEN 1 ELSE 0 END) AS decision_degraded_count,
                    SUM(CASE WHEN COALESCE(o.attribution_anchor_source, 'unknown')<>'unknown' THEN 1 ELSE 0 END) AS delivered_attribution_count,
                    SUM(CASE WHEN COALESCE(o.attribution_anchor_source, 'unknown')='unknown' THEN 1 ELSE 0 END) AS delivery_unknown_count,
                    SUM(CASE WHEN o.order_query_status IN ('ok', 'success', 'backfill_current_only', 'insufficient_baseline') THEN 1 ELSE 0 END) AS order_query_success_count,
                    SUM(CASE WHEN o.order_query_status<>'' THEN 1 ELSE 0 END) AS order_query_attempt_count,
                    SUM(CASE WHEN COALESCE(o.order_source, '')<>'' AND (
                                  COALESCE(o.order_state_after_24h, '')<>'' OR
                                  COALESCE(o.order_state_after_72h, '')<>'' OR
                                  COALESCE(o.order_state_after_7d, '')<>'' OR
                                  COALESCE(o.order_state_after_14d, '')<>'' OR
                                  COALESCE(o.order_state_after_30d, '')<>''
                             ) THEN 1 ELSE 0 END) AS order_attribution_complete_count,
                    SUM(CASE WHEN COALESCE(o.attribution_anchor_source, 'unknown')<>'unknown'
                              AND COALESCE(u.delivered_at, '')<=?
                              AND COALESCE(o.order_state_before, '') NOT IN ('paid','waiting_schedule','scheduled','visited','finished','evaluated')
                              AND COALESCE(o.order_state_after_72h, '')<>'' THEN 1 ELSE 0 END) AS order_outcome_eligible_count,
                    SUM(CASE WHEN COALESCE(o.attribution_anchor_source, 'unknown')<>'unknown'
                              AND COALESCE(u.delivered_at, '')<=?
                              AND COALESCE(o.order_state_before, '') NOT IN ('scheduled','visited','finished','evaluated')
                              AND COALESCE(o.order_state_after_7d, '')<>'' THEN 1 ELSE 0 END) AS order_7d_eligible_count,
                    SUM(CASE WHEN COALESCE(o.attribution_anchor_source, 'unknown')<>'unknown'
                              AND COALESCE(u.delivered_at, '')<=?
                              AND COALESCE(o.order_state_before, '') NOT IN ('paid','waiting_schedule','scheduled','visited','finished','evaluated')
                              AND COALESCE(o.order_state_after_72h, '')='' THEN 1 ELSE 0 END) AS order_outcome_unknown_count,
                    SUM(CASE WHEN COALESCE(o.attribution_anchor_source, 'unknown')<>'unknown'
                              AND COALESCE(u.delivered_at, '')>? THEN 1 ELSE 0 END) AS order_outcome_not_due_count,
                    SUM(CASE WHEN o.order_query_status='backfill_current_only' THEN 1 ELSE 0 END) AS order_backfill_current_only_count,
                    SUM(CASE WHEN u.decision_reasons_json LIKE '%"explicit_exit_requires_complete"%'
                                  OR u.decision_reasons_json LIKE '%"explicit_exit_same_turn_sales_conflict"%'
                             THEN 1 ELSE 0 END) AS hard_stop_wrong_advance_count,
                    SUM(CASE WHEN u.decision_reasons_json LIKE '%"new_blocker_requires_pause"%'
                                  OR u.decision_reasons_json LIKE '%"active_cardpoint_requires_pause"%'
                                  OR u.decision_reasons_json LIKE '%"active_cardpoint_same_turn_sales_conflict"%'
                             THEN 1 ELSE 0 END) AS new_blocker_not_paused_count,
                    SUM(CASE WHEN u.selector_status IN ('empty','error') THEN 1 ELSE 0 END) AS selector_empty_or_error_count,
                    SUM(CASE WHEN u.fallback_used=1 THEN 1 ELSE 0 END) AS taxonomy_fallback_count
                FROM v3_strategy_usage_events u
                LEFT JOIN v3_strategy_outcome_events o ON o.usage_event_id=u.id
                {where_sql}
                GROUP BY {group_keys}
                ORDER BY usage_count DESC
                LIMIT ?
                """,
                (
                    _SUCCESS_DELIVERY_STATUS, order_72h_cutoff, order_7d_cutoff,
                    order_72h_cutoff, order_72h_cutoff, *params,
                    _limit(filters.get("limit"), default=50, maximum=200),
                ),
            ).fetchall()
        return {
            "dimension": dimension,
            "filters": _clean_filters(filters),
            "items": [_analytics_counts(dict(row)) for row in rows],
        }

    def v3_strategy_analytics_failures(self, **filters: Any) -> dict[str, Any]:
        where_sql, params = _analytics_filters(filters)
        failure_clause = (
            "(u.selector_status IN ('empty','error') OR u.decision_status='degraded' "
            "OR (u.adopted=0 AND u.checkpoint_code<>'' AND u.candidate_count>0 "
            "    AND u.intent_code<>'explicit_exit' AND u.decision_status<>'system_guard' "
            "    AND u.selector_status NOT IN ('deferred','not_needed')) "
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
                       u.reply_source, u.reply_action, u.policy_version, u.decision_status,
                       u.intent_code, u.intent_confidence, u.emotion_before,
                       u.emotion_confidence, u.emotion_pressure, u.emotion_flow_action,
                       u.closing_strategy_code, u.closing_action, u.closing_node_key,
                       u.closing_trigger, u.closing_customer_state, u.closing_pressure,
                       u.cardpoint_category_key, u.cardpoint_state, u.decision_reasons_json
                FROM v3_strategy_usage_events u
                {prefix} {failure_clause}
                ORDER BY u.occurred_at DESC
                LIMIT ?
                """,
                (*params, _SUCCESS_DELIVERY_STATUS, _limit(filters.get("limit"), default=50, maximum=200)),
            ).fetchall()
        return {"filters": _clean_filters(filters), "items": [_decode_usage_row(dict(row)) for row in rows]}


def _collect_order_snapshots(
    events: list[dict[str, Any]],
    provider: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    max_concurrency: int,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Query different sales contacts concurrently, preserving per-contact order."""

    groups: dict[str, list[dict[str, Any]]] = {}
    event_by_id = {_text(item.get("id")): item for item in events}
    for event in events:
        provider_input = _order_provider_input(event)
        key = "|".join(
            _text(provider_input.get(field))
            for field in ("corp_id", "wechat", "external_userid", "customer_id")
        )
        groups.setdefault(key, []).append(provider_input)

    def run_group(group: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        results: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for provider_input in group:
            event = event_by_id[_text(provider_input.get("usage_event_id"))]
            try:
                snapshot = _dict(provider(provider_input))
            except Exception as exc:
                snapshot = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}"[:1000],
                }
            results.append((event, snapshot))
        return results

    output: list[tuple[dict[str, Any], dict[str, Any]]] = []
    workers = max(1, min(int(max_concurrency or 1), len(groups) or 1, 16))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="v3-order-outcome") as pool:
        futures = [pool.submit(run_group, group) for group in groups.values()]
        for future in as_completed(futures):
            output.extend(future.result())
    return output


def _order_provider_due(
    event: dict[str, Any],
    *,
    outcome: dict[str, Any],
    now: datetime,
) -> bool:
    if _text(outcome.get("attribution_anchor_source")) == "unknown":
        return False
    anchor = _parse_dt(_text(event.get("delivered_at")))
    return bool(anchor and now >= anchor + timedelta(hours=24))


def _usage_event_from_state(*, conversation_id: str, final_state: dict[str, Any], now: str) -> dict[str, Any]:
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
    intent = _decision_part(final_state, "realtime_intent")
    emotion = _decision_part(final_state, "emotion_decision")
    closing = _decision_part(final_state, "closing_decision")
    cardpoint = _decision_part(final_state, "cardpoint_decision")
    payload = {
        "schema_version": "v3_strategy_usage_event_v2",
        "classification_status": _text(route.get("classification_status")),
        "support_level": _text(recall.get("support_level")),
        "selector_reason": _text(selector.get("reason")),
        "selected_script_ids": selected_script_ids,
        "script_query_sources": _query_sources(recall),
        "message_count": len(_list(final_state.get("reply_messages"))),
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
        "emotion_before": _emotion_value(final_state),
        "emotion_after": "",
        "policy_version": _policy_version(final_state),
        "decision_status": _decision_status(final_state),
        "intent_confidence": _text(intent.get("confidence")),
        "intent_secondary_json": dumps(_string_list(intent.get("secondary_types"))[:3]),
        "emotion_confidence": _text(emotion.get("confidence")),
        "emotion_pressure": _text(emotion.get("pressure")),
        "emotion_flow_action": _text(emotion.get("flow_action")),
        "closing_action": _text(closing.get("action")),
        "closing_node_key": _text(closing.get("node_key")),
        "closing_trigger": _text(closing.get("trigger")),
        "closing_customer_state": _text(closing.get("customer_state")),
        "closing_pressure": _text(closing.get("pressure")),
        "cardpoint_category_key": _text(cardpoint.get("category_key")),
        "cardpoint_state": _text(cardpoint.get("state")),
        "decision_reasons_json": dumps(_string_list(final_state.get("decision_reasons"))[:20]),
        "decision_evidence_refs_json": dumps(_decision_evidence_refs(final_state)),
        "selector_status": _text(selector.get("status")),
        "fallback_used": 1 if fallback_used else 0,
        "payload_json": dumps(payload),
        "order_state_before_json": dumps(order_state_before),
        "customer_turn_eligible": 1 if _customer_turn_eligible(final_state) else 0,
        "created_at": now,
        "updated_at": now,
    }


def _usage_insert_values(event: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(event[key] for key in _USAGE_COLUMNS)


def _usage_update_values(event: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(event[key] for key in _USAGE_UPDATE_COLUMNS) + (event["request_id"],)


def _previous_usage_for_event(conn: Any, event: dict[str, Any]) -> Any | None:
    boundary = _validated_contact_boundary(
        corp_id=_text(event.get("corp_id")),
        wechat=_text(event.get("wechat")),
        external_userid=_text(event.get("external_userid")),
        customer_id=_text(event.get("customer_id")),
    )
    if boundary is None:
        return None
    corp_id, wechat, external_userid, customer_id = boundary
    identity_clauses: list[str] = []
    identity_params: list[str] = []
    if external_userid:
        identity_clauses.append("external_userid=?")
        identity_params.append(external_userid)
    if customer_id:
        identity_clauses.append("customer_id=?")
        identity_params.append(customer_id)
    return conn.execute(
        f"""
        SELECT u.id, u.request_id, u.occurred_at, u.delivered_at, u.delivery_status,
               u.intent_code, u.emotion_before
        FROM v3_strategy_usage_events u
        LEFT JOIN v3_strategy_outcome_events o ON o.usage_event_id=u.id
        WHERE u.sales_contact_key=? AND u.corp_id=? AND u.wechat=?
          AND ({' OR '.join(f'u.{clause}' for clause in identity_clauses)}) AND u.request_id<>?
          AND u.customer_turn_eligible=1 AND u.decision_status IN ('ok', 'degraded')
          AND COALESCE(o.next_usage_event_id, '')=''
        ORDER BY u.occurred_at DESC, u.created_at DESC
        LIMIT 1
        """,
        (
            _text(event.get("sales_contact_key")), corp_id, wechat,
            *identity_params, _text(event.get("request_id")),
        ),
    ).fetchone()


def _link_previous_usage(conn: Any, *, previous: Any, current: dict[str, Any]) -> None:
    previous_row = dict(previous)
    previous_id = _text(previous_row.get("id"))
    if not previous_id:
        return
    now = _text(current.get("occurred_at")) or utc_now_iso()
    current_emotion = _text(current.get("emotion_before"))
    previous_emotion = _text(previous_row.get("emotion_before"))
    transition = (
        f"{previous_emotion}->{current_emotion}"
        if previous_emotion and current_emotion
        else ""
    )
    conn.execute(
        """
        UPDATE v3_strategy_usage_events
        SET emotion_after=COALESCE(NULLIF(?, ''), emotion_after), updated_at=?
        WHERE id=?
        """,
        (current_emotion, now, previous_id),
    )
    anchor_source = _anchor_source_from_usage(previous_row)
    anchor_at = _text(previous_row.get("delivered_at")) if anchor_source != "unknown" else ""
    start = _parse_dt(anchor_at) if anchor_at else None
    replied = _parse_dt(now)
    replied_1h = _within(replied, start, hours=1) if start is not None else 0
    replied_6h = _within(replied, start, hours=6) if start is not None else 0
    replied_24h = _within(replied, start, hours=24) if start is not None else 0
    replied_72h = _within(replied, start, hours=72) if start is not None else 0
    conn.execute(
        """
        INSERT INTO v3_strategy_outcome_events
            (usage_event_id, customer_replied_1h, customer_replied_6h,
             customer_replied_24h, customer_replied_72h, first_reply_after_at,
             next_usage_event_id, next_intent_code, next_emotion_code,
             emotion_transition, attribution_anchor_source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(usage_event_id) DO UPDATE SET
            customer_replied_1h=CASE WHEN excluded.attribution_anchor_source<>'unknown' THEN excluded.customer_replied_1h ELSE customer_replied_1h END,
            customer_replied_6h=CASE WHEN excluded.attribution_anchor_source<>'unknown' THEN excluded.customer_replied_6h ELSE customer_replied_6h END,
            customer_replied_24h=CASE WHEN excluded.attribution_anchor_source<>'unknown' THEN excluded.customer_replied_24h ELSE customer_replied_24h END,
            customer_replied_72h=CASE WHEN excluded.attribution_anchor_source<>'unknown' THEN excluded.customer_replied_72h ELSE customer_replied_72h END,
            first_reply_after_at=CASE WHEN excluded.attribution_anchor_source<>'unknown' THEN excluded.first_reply_after_at ELSE first_reply_after_at END,
            next_usage_event_id=excluded.next_usage_event_id,
            next_intent_code=excluded.next_intent_code,
            next_emotion_code=excluded.next_emotion_code,
            emotion_transition=excluded.emotion_transition,
            attribution_anchor_source=CASE
                WHEN attribution_anchor_source='unknown' THEN excluded.attribution_anchor_source
                ELSE attribution_anchor_source END,
            updated_at=excluded.updated_at
        """,
        (
            previous_id, replied_1h, replied_6h, replied_24h, replied_72h, now,
            _text(current.get("id")), _text(current.get("intent_code")), current_emotion,
            transition, anchor_source, now, now,
        ),
    )


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
    anchor_at, anchor_source = _attribution_anchor(conn, event)
    anchor_for_window = anchor_at if anchor_source != "unknown" else ""
    anchor_dt = _parse_dt(anchor_for_window) if anchor_for_window else None
    first_reply = (
        conn.execute(
            """
            SELECT m.id, m.created_at FROM messages m
            INNER JOIN v3_strategy_usage_events next_u ON next_u.request_id=m.request_id
            WHERE m.conversation_id=? AND m.role='user' AND m.created_at>?
              AND next_u.customer_turn_eligible=1
            ORDER BY m.created_at ASC LIMIT 1
            """,
            (_text(event.get("conversation_id")), anchor_for_window),
        ).fetchone()
        if anchor_dt is not None
        else None
    )
    first_reply_at = _text(first_reply["created_at"]) if first_reply else ""
    first_reply_msgid = _text(first_reply["id"]) if first_reply else ""
    first_reply_dt = _parse_dt(first_reply_at) if first_reply_at else None
    order_before = loads_dict(_text(event.get("order_state_before_json"))).get("order_state", "")
    order_windows = (
        _local_order_windows(conn, event, occurred_at=anchor_for_window)
        if anchor_dt is not None
        else {"24h": "", "72h": "", "7d": "", "14d": "", "30d": ""}
    )
    now = utc_now_iso()
    return {
        "usage_event_id": usage_id,
        "customer_replied_1h": _within(first_reply_dt, anchor_dt, hours=1) if anchor_dt else 0,
        "customer_replied_6h": _within(first_reply_dt, anchor_dt, hours=6) if anchor_dt else 0,
        "customer_replied_24h": _within(first_reply_dt, anchor_dt, hours=24) if anchor_dt else 0,
        "customer_replied_72h": _within(first_reply_dt, anchor_dt, hours=72) if anchor_dt else 0,
        "first_reply_after_at": first_reply_at,
        "first_reply_after_msgid": first_reply_msgid,
        "order_state_before": _text(order_before),
        "order_state_after_24h": order_windows["24h"],
        "order_state_after_72h": order_windows["72h"],
        "order_state_after_7d": order_windows["7d"],
        "order_state_after_14d": order_windows["14d"],
        "order_state_after_30d": order_windows["30d"],
        "paid_after_24h": _entered_state(order_before, order_windows["24h"], _PAID_STATES),
        "paid_after_72h": _entered_state(order_before, order_windows["72h"], _PAID_STATES),
        "paid_after_7d": _entered_state(order_before, order_windows["7d"], _PAID_STATES),
        "scheduled_after_7d": _entered_state(order_before, order_windows["7d"], _SCHEDULED_STATES),
        "visited_after_14d": _entered_state(order_before, order_windows["14d"], _VISITED_STATES),
        "finished_after_30d": _entered_state(order_before, order_windows["30d"], _FINISHED_STATES),
        "attribution_source": "local_messages_and_run_snapshots",
        "next_usage_event_id": "",
        "next_intent_code": "",
        "next_emotion_code": "",
        "emotion_transition": "",
        "attribution_anchor_source": anchor_source,
        "order_source": "",
        "order_query_status": "",
        "order_query_error": "",
        "order_last_refreshed_at": "",
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
        SELECT r.output_snapshot, r.created_at FROM runs r
        INNER JOIN v3_strategy_usage_events next_u ON next_u.request_id=r.request_id
        WHERE r.conversation_id=? AND r.created_at>?
          AND next_u.customer_turn_eligible=1
        ORDER BY r.created_at ASC
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
        "order_state_after_72h", "order_state_after_7d", "order_state_after_14d",
        "order_state_after_30d", "paid_after_24h",
        "paid_after_72h", "paid_after_7d", "scheduled_after_7d",
        "visited_after_14d", "finished_after_30d", "attribution_source",
        "next_usage_event_id", "next_intent_code", "next_emotion_code",
        "emotion_transition", "attribution_anchor_source", "order_source",
        "order_query_status", "order_query_error", "order_last_refreshed_at",
        "payload_json", "created_at", "updated_at",
    ))


def _attribution_anchor(conn: Any, event: dict[str, Any]) -> tuple[str, str]:
    dispatch_id = _text(event.get("dispatch_id"))
    if dispatch_id:
        dispatch = conn.execute(
            "SELECT confirmed_at, accepted_at FROM message_dispatches WHERE id=?",
            (dispatch_id,),
        ).fetchone()
        if dispatch is not None:
            confirmed_at = _text(dispatch["confirmed_at"])
            accepted_at = _text(dispatch["accepted_at"])
            if confirmed_at:
                return confirmed_at, "delivered_at"
            if accepted_at:
                return accepted_at, "platform_accepted_at"
    delivered_at = _text(event.get("delivered_at"))
    delivery_status = _text(event.get("delivery_status"))
    if delivered_at and delivery_status == _SUCCESS_DELIVERY_STATUS:
        return delivered_at, "delivered_at"
    if delivered_at and delivery_status == "platform_accepted":
        return delivered_at, "platform_accepted_at"
    return "", "unknown"


def _anchor_source_from_usage(event: dict[str, Any]) -> str:
    delivered_at = _text(event.get("delivered_at"))
    status = _text(event.get("delivery_status"))
    if delivered_at and status == _SUCCESS_DELIVERY_STATUS:
        return "delivered_at"
    if delivered_at and status == "platform_accepted":
        return "platform_accepted_at"
    return "unknown"


def _order_provider_input(event: dict[str, Any]) -> dict[str, Any]:
    order_before = loads_dict(_text(event.get("order_state_before_json")))
    return {
        "usage_event_id": _text(event.get("id")),
        "request_id": _text(event.get("request_id")),
        "corp_id": _text(event.get("corp_id")),
        "wechat": _text(event.get("wechat")),
        "external_userid": _text(event.get("external_userid")),
        "customer_id": _text(event.get("customer_id")),
        "user_id": _text(event.get("user_id")),
        "sales_contact_key": _text(event.get("sales_contact_key")),
        "occurred_at": _text(event.get("occurred_at")),
        "delivered_at": _text(event.get("delivered_at")),
        "order_state_before": _text(order_before.get("order_state")),
        "order_id_before": _text(order_before.get("order_id")),
    }


def _apply_order_snapshot(conn: Any, *, event: dict[str, Any], snapshot: dict[str, Any]) -> None:
    status = _text(snapshot.get("status")).lower() or "unknown"
    source = _text(snapshot.get("source"))
    error = _text(snapshot.get("error"))[:1000]
    now = utc_now_iso()
    usable = status in {"ok", "success"} and not error
    windows = _provider_windows(snapshot) if usable else {}
    current_state = _text(snapshot.get("order_state")) if usable else ""
    order_before = loads_dict(_text(event.get("order_state_before_json")))
    baseline_state = _text(order_before.get("order_state"))
    anchor_known = _anchor_source_from_usage(event) != "unknown"
    occurred_dt = _parse_dt(_text(event.get("delivered_at"))) if anchor_known else None
    now_dt = _utc_now()
    expired = {
        "24h": bool(occurred_dt and now_dt >= occurred_dt + timedelta(hours=24)),
        "72h": bool(occurred_dt and now_dt >= occurred_dt + timedelta(hours=72)),
        "7d": bool(occurred_dt and now_dt >= occurred_dt + timedelta(days=7)),
        "14d": bool(occurred_dt and now_dt >= occurred_dt + timedelta(days=14)),
        "30d": bool(occurred_dt and now_dt >= occurred_dt + timedelta(days=30)),
    }
    values = {
        key: _text(windows.get(key)) if expired[key] else ""
        for key in expired
    }
    window_hours = {"24h": 24, "72h": 72, "7d": 168, "14d": 336, "30d": 720}
    poll_delay_seconds: dict[str, int] = {}
    if occurred_dt is not None:
        for key, hours in window_hours.items():
            delay = int((now_dt - (occurred_dt + timedelta(hours=hours))).total_seconds())
            if values[key]:
                poll_delay_seconds[key] = max(0, delay)
            elif current_state and 0 <= delay <= _ORDER_WINDOW_POLL_TOLERANCE_SECONDS:
                values[key] = current_state
                poll_delay_seconds[key] = delay
    current_only = bool(usable and current_state and not any(values.values()))
    effective_status = "backfill_current_only" if current_only else status
    existing = conn.execute(
        "SELECT payload_json FROM v3_strategy_outcome_events WHERE usage_event_id=?",
        (_text(event.get("id")),),
    ).fetchone()
    payload = loads_dict(_text(existing["payload_json"])) if existing is not None else {}
    observations = _list(payload.get("order_observations"))
    observations.append(
        {
            "observed_at": now,
            "status": effective_status,
            "order_state": current_state,
            "source": source,
            "selection_mode": _text(snapshot.get("selection_mode")),
        }
    )
    payload["order_observations"] = observations[-40:]
    payload["order_attribution"] = {
        "mode": (
            "explicit_windows"
            if any(values.values())
            else "backfill_current_only"
            if current_only
            else "query_error"
            if error or status in {"error", "failed", "unavailable"}
            else "no_eligible_window"
        ),
        "window_definitions_hours": window_hours,
        "available_windows": [key for key, value in values.items() if value],
        "poll_delay_seconds": poll_delay_seconds,
        "poll_tolerance_seconds": _ORDER_WINDOW_POLL_TOLERANCE_SECONDS,
        "refresh_age_seconds": (
            max(0, int((now_dt - occurred_dt).total_seconds()))
            if occurred_dt is not None else None
        ),
        "baseline_state": baseline_state,
        "selection_mode": _text(snapshot.get("selection_mode")),
    }
    conn.execute(
        """
        UPDATE v3_strategy_outcome_events
        SET order_state_after_24h=COALESCE(NULLIF(?, ''), order_state_after_24h),
            order_state_after_72h=COALESCE(NULLIF(?, ''), order_state_after_72h),
            order_state_after_7d=COALESCE(NULLIF(?, ''), order_state_after_7d),
            order_state_after_14d=COALESCE(NULLIF(?, ''), order_state_after_14d),
            order_state_after_30d=COALESCE(NULLIF(?, ''), order_state_after_30d),
            paid_after_24h=CASE WHEN paid_after_24h=1 OR ?=1 THEN 1 ELSE 0 END,
            paid_after_72h=CASE WHEN paid_after_72h=1 OR ?=1 THEN 1 ELSE 0 END,
            paid_after_7d=CASE WHEN paid_after_7d=1 OR ?=1 THEN 1 ELSE 0 END,
            scheduled_after_7d=CASE WHEN scheduled_after_7d=1 OR ?=1 THEN 1 ELSE 0 END,
            visited_after_14d=CASE WHEN visited_after_14d=1 OR ?=1 THEN 1 ELSE 0 END,
            finished_after_30d=CASE WHEN finished_after_30d=1 OR ?=1 THEN 1 ELSE 0 END,
            order_source=COALESCE(NULLIF(?, ''), order_source),
            order_query_status=?, order_query_error=?, order_last_refreshed_at=?,
            attribution_source=CASE
                WHEN ?<>'' THEN 'platform_order_snapshot'
                ELSE attribution_source END,
            payload_json=?,
            updated_at=?
        WHERE usage_event_id=?
        """,
        (
            values["24h"], values["72h"], values["7d"], values["14d"], values["30d"],
            _entered_state(baseline_state, values["24h"], _PAID_STATES),
            _entered_state(baseline_state, values["72h"], _PAID_STATES),
            _entered_state(baseline_state, values["7d"], _PAID_STATES),
            _entered_state(baseline_state, values["7d"], _SCHEDULED_STATES),
            _entered_state(baseline_state, values["14d"], _VISITED_STATES),
            _entered_state(baseline_state, values["30d"], _FINISHED_STATES),
            source, effective_status, error, now, source if any(values.values()) else "",
            dumps(payload), now,
            _text(event.get("id")),
        ),
    )


def _provider_windows(snapshot: dict[str, Any]) -> dict[str, str]:
    nested = _dict(snapshot.get("windows"))
    aliases = {
        "24h": "order_state_after_24h",
        "72h": "order_state_after_72h",
        "7d": "order_state_after_7d",
        "14d": "order_state_after_14d",
        "30d": "order_state_after_30d",
    }
    return {
        key: _text(nested.get(key) or snapshot.get(alias))
        for key, alias in aliases.items()
    }


def _analytics_filters(filters: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = ["u.customer_turn_eligible=1"]
    params: list[Any] = []
    mapping = {
        "corp_id": "u.corp_id",
        "wechat": "u.wechat",
        "checkpoint_code": "u.checkpoint_code",
        "sequence_id": "u.sequence_id",
        "script_id": "u.script_id",
        "action_code": "u.action_code",
        "intent_code": "u.intent_code",
        "emotion_code": "u.emotion_before",
        "closing_sequence_key": "u.closing_strategy_code",
        "closing_action": "u.closing_action",
        "decision_status": "u.decision_status",
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


def _customer_turn_eligible(state: dict[str, Any]) -> bool:
    """Exclude platform protocol events without interpreting sales semantics."""

    reply_source = _text(state.get("reply_source")).lower()
    return reply_source not in {
        "ignored_platform_auto_message",
        "platform_recalled_message",
        "platform_superseded",
        "platform_filtered",
    }


def _analytics_counts(row: dict[str, Any]) -> dict[str, Any]:
    usage_count = _int(row.get("usage_count"))
    adopted_count = _int(row.get("adopted_count"))
    dispatch_count = _int(row.get("dispatch_count"))
    delivery_success_count = _int(row.get("delivery_success_count"))
    delivered_attribution_count = _int(row.get("delivered_attribution_count"))
    decision_coverage_count = _int(row.get("decision_coverage_count"))
    decision_eligible_count = _int(row.get("decision_eligible_count"))
    order_query_success_count = _int(row.get("order_query_success_count"))
    order_query_attempt_count = _int(row.get("order_query_attempt_count"))
    return {
        **{key: value for key, value in row.items() if key not in {
            "usage_count", "adopted_count", "dispatch_count", "delivery_success_count",
            "replied_24h_count", "paid_72h_count", "scheduled_7d_count",
            "decision_coverage_count", "decision_eligible_count", "decision_degraded_count",
            "delivered_attribution_count", "delivery_unknown_count",
            "order_query_success_count", "order_query_attempt_count",
            "order_attribution_complete_count",
            "order_outcome_eligible_count", "order_7d_eligible_count",
            "order_outcome_unknown_count", "order_outcome_not_due_count",
            "order_backfill_current_only_count",
            "hard_stop_wrong_advance_count", "new_blocker_not_paused_count",
            "selector_empty_or_error_count", "taxonomy_fallback_count",
        }},
        "usage_count": usage_count,
        "adopted_count": adopted_count,
        "adoption_rate": _rate(adopted_count, usage_count),
        "dispatch_count": dispatch_count,
        "delivery_success_count": delivery_success_count,
        "delivery_success_rate": _rate(delivery_success_count, dispatch_count),
        "customer_replied_24h_count": _int(row.get("replied_24h_count")),
        "customer_replied_24h_rate": _rate(_int(row.get("replied_24h_count")), delivered_attribution_count),
        "paid_72h_count": _int(row.get("paid_72h_count")),
        "paid_72h_rate": _rate(_int(row.get("paid_72h_count")), _int(row.get("order_outcome_eligible_count"))),
        "scheduled_7d_count": _int(row.get("scheduled_7d_count")),
        "scheduled_7d_rate": _rate(_int(row.get("scheduled_7d_count")), _int(row.get("order_7d_eligible_count"))),
        "decision_coverage_count": decision_coverage_count,
        "decision_eligible_count": decision_eligible_count,
        "decision_coverage_rate": _rate(decision_coverage_count, decision_eligible_count),
        "decision_degraded_count": _int(row.get("decision_degraded_count")),
        "decision_degraded_rate": _rate(_int(row.get("decision_degraded_count")), decision_coverage_count),
        "delivered_attribution_count": delivered_attribution_count,
        "delivery_unknown_count": _int(row.get("delivery_unknown_count")),
        "delivery_unknown_rate": _rate(_int(row.get("delivery_unknown_count")), usage_count),
        "order_query_success_count": order_query_success_count,
        "order_query_attempt_count": order_query_attempt_count,
        "order_query_success_rate": _rate(order_query_success_count, order_query_attempt_count),
        "order_attribution_complete_count": _int(row.get("order_attribution_complete_count")),
        "order_attribution_complete_rate": _rate(
            _int(row.get("order_attribution_complete_count")),
            order_query_success_count,
        ),
        "order_outcome_eligible_count": _int(row.get("order_outcome_eligible_count")),
        "order_7d_eligible_count": _int(row.get("order_7d_eligible_count")),
        "order_outcome_unknown_count": _int(row.get("order_outcome_unknown_count")),
        "order_outcome_not_due_count": _int(row.get("order_outcome_not_due_count")),
        "order_backfill_current_only_count": _int(row.get("order_backfill_current_only_count")),
        "hard_stop_wrong_advance_count": _int(row.get("hard_stop_wrong_advance_count")),
        "new_blocker_not_paused_count": _int(row.get("new_blocker_not_paused_count")),
        "selector_empty_or_error_count": _int(row.get("selector_empty_or_error_count")),
        "taxonomy_fallback_count": _int(row.get("taxonomy_fallback_count")),
    }


def _decode_usage_row(row: dict[str, Any]) -> dict[str, Any]:
    row["adopted"] = bool(row.get("adopted"))
    row["fallback_used"] = bool(row.get("fallback_used"))
    if "decision_reasons_json" in row:
        row["decision_reasons"] = loads_list(_text(row.pop("decision_reasons_json")))
    return row


def _order_state_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    stored_snapshot = _dict(state.get("order_state_snapshot"))
    if stored_snapshot:
        return {
            "order_state": _normalized_order_state(stored_snapshot.get("order_state")),
            "order_id": _text(stored_snapshot.get("order_id")),
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
    order_state = _normalized_order_state(
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
        "order_id": _text(order_context.get("order_id") or order_context.get("id")),
        "deposit_state": deposit_state,
        "fee_paid": order_context.get("fee_paid", basic_info.get("fee_paid", "")),
        "source": "state_customer_context_or_order_tool",
    }


def _intent_code(state: dict[str, Any]) -> str:
    return _text(_decision_part(state, "realtime_intent").get("type"))


def _closing_strategy_code(state: dict[str, Any]) -> str:
    closing = _decision_part(state, "closing_decision")
    shadow = _dict(state.get("closing_sequence_shadow"))
    return _text(
        closing.get("sequence_key")
        or shadow.get("strategy_code")
    )


def _emotion_value(state: dict[str, Any]) -> str:
    return _text(_decision_part(state, "emotion_decision").get("label"))


def _decision_part(state: dict[str, Any], key: str) -> dict[str, Any]:
    value = state.get(key)
    if isinstance(value, dict):
        return value
    return _dict(_dict(state.get("policy_decision")).get(key))


def _policy_version(state: dict[str, Any]) -> str:
    return _text(_dict(state.get("ai_sales_policy")).get("policy_version") or state.get("policy_version"))


def _decision_status(state: dict[str, Any]) -> str:
    explicit = _text(state.get("decision_status") or state.get("policy_decision_status"))
    if explicit:
        return explicit
    takeover = _dict(state.get("takeover_guard")) or _dict(
        _dict(state.get("request_context")).get("takeover_guard")
    )
    if (
        _text(state.get("reply_source")) == "human_takeover_guard"
        or _text(takeover.get("decision")).lower() == "return_empty"
        or _text(takeover.get("mode")).lower() in {"human", "manual"}
    ):
        return "system_guard"
    policy = _dict(state.get("ai_sales_policy"))
    if _text(policy.get("runtime_mode")).lower() in {"", "off"}:
        return "not_enabled"
    required = (
        _decision_part(state, "realtime_intent"),
        _decision_part(state, "emotion_decision"),
        _decision_part(state, "closing_decision"),
    )
    return "ok" if all(required) else "degraded"


def _decision_evidence_refs(state: dict[str, Any]) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    for key in ("primary_task", "realtime_intent", "emotion_decision", "closing_decision"):
        value = _decision_part(state, key)
        refs = _string_list(value.get("evidence_refs"))[:8]
        if refs:
            output[key] = refs
    return output


def _sales_contact_key(state: dict[str, Any]) -> str:
    return "|".join(
        _text(state.get(key))
        for key in ("corp_id", "wechat", "external_userid", "customer_id")
    )


def _validated_contact_boundary(
    *,
    corp_id: str,
    wechat: str,
    external_userid: str,
    customer_id: str,
) -> tuple[str, str, str, str] | None:
    corp_id = _text(corp_id)
    wechat = _text(wechat)
    external_userid = _text(external_userid)
    customer_id = _text(customer_id)
    if not corp_id or not wechat or not (external_userid or customer_id):
        return None
    return corp_id, wechat, external_userid, customer_id


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


def _entered_state(before: str, after: str, target_states: set[str]) -> int:
    return 1 if _text(after) in target_states and _text(before) not in target_states else 0


def _normalized_order_state(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("order_state") or value.get("status_text") or value.get("status")
    return _text(order_status_text(value))


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
