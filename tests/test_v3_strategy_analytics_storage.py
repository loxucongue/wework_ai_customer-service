from __future__ import annotations

import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.config import Settings
from app.services.customer_scope import build_customer_scope
from app.services.message_delivery import MessageDeliveryService
from app.services.memory_store import CustomerMemoryStore
from app.services.outreach.planning import PlanGenerator
from app.services.storage.mysql_schema import EXPECTED_COLUMNS
from app.services.storage.repositories import AppRepository
from app.services.storage.sqlite_store import SQLiteStore


USAGE_ADDITIONS = {
    "policy_version", "decision_status", "intent_confidence",
    "intent_secondary_json", "emotion_confidence", "emotion_pressure",
    "emotion_flow_action", "closing_action", "closing_node_key",
    "closing_trigger", "closing_customer_state", "closing_pressure",
    "cardpoint_category_key", "cardpoint_state", "decision_reasons_json",
    "decision_evidence_refs_json", "customer_turn_eligible",
    "closing_rule_ids_json", "closing_primary_rule_id", "closing_sequence_source_id", "closing_node_source_id",
    "closing_action_type_id", "closing_action_type_name", "closing_script_type_id",
    "closing_script_type_name", "closing_catalog_checksum", "closing_catalog_status",
    "closing_rule_match_status", "closing_constraint_status",
    "closing_constraint_reasons_json",
}
OUTCOME_ADDITIONS = {
    "next_usage_event_id", "next_intent_code", "next_emotion_code",
    "emotion_transition", "attribution_anchor_source", "order_source",
    "order_query_status", "order_query_error", "order_last_refreshed_at",
    "order_state_after_14d", "order_state_after_30d",
}


def _repository(tmp_path: Path) -> AppRepository:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "analytics.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    return AppRepository(store)


def _state(
    request_id: str,
    *,
    wechat: str = "sales-a",
    intent: str = "fact_question",
    emotion: str = "curious",
    closing_action: str = "none",
    closing_sequence: str = "none",
    closing_node: str = "",
    customer_id: str = "customer-1",
    external_userid: str = "external-1",
) -> dict[str, Any]:
    scope = build_customer_scope(
        corp_id="corp-1",
        wechat=wechat,
        external_userid=external_userid,
        customer_id=customer_id,
    )
    return {
        "request_context": {"interface_version": "v3"},
        "request_id": request_id,
        "customer_id": customer_id,
        "corp_id": "corp-1",
        "wechat": wechat,
        "external_userid": external_userid,
        "user_id": "staff-1",
        "sales_contact_key": scope.sales_contact_key,
        "ai_sales_policy": {"runtime_mode": "active", "policy_version": "2026-09-03.1"},
        "realtime_intent": {
            "type": intent,
            "secondary_types": ["general_chat"],
            "confidence": "high",
            "evidence_refs": ["message:current"],
            "basis": ["this must never be persisted"],
        },
        "emotion_decision": {
            "label": emotion,
            "confidence": "medium",
            "pressure": "low",
            "flow_action": "lower_pressure",
            "evidence_refs": ["message:current"],
            "basis": ["this must never be persisted"],
        },
        "closing_decision": {
            "action": closing_action,
            "sequence_key": closing_sequence,
            "node_key": closing_node,
            "trigger": "positive_progress",
            "customer_state": "engaged",
            "pressure": "low",
            "evidence_refs": ["order:verified"],
            "basis": ["this must never be persisted"],
        },
        "semantic_route": {"checkpoint": {"primary_code": "price"}},
        "reply_messages": [{"type": "text", "content": "not persisted"}],
    }


def _row(repository: AppRepository, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    with repository.store.connect() as conn:
        value = conn.execute(sql, params).fetchone()
    assert value is not None
    return dict(value)


def test_usage_mapping_is_structured_idempotent_and_excludes_basis(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    state = _state(
        "request-1",
        intent="advance_transaction",
        emotion="hesitant",
        closing_action="enter",
        closing_sequence="gentle_invitation",
        closing_node="ask_next_step",
    )
    state["cardpoint_decision"] = {
        "category_key": "price",
        "state": "resolved",
        "confidence": "high",
    }
    state["decision_reasons"] = ["active_cardpoint_requires_pause"]
    state["closing_decision"].update(
        {
            "rule_ids": ["external:rule:101"],
            "sequence_source_id": "201",
            "node_source_id": "2011",
            "action_type_id": 3,
            "action_type_name": "预约确认",
            "script_type_id": 14,
            "script_type_name": "逼单-约时间类",
            "catalog_checksum": "catalog-checksum",
            "catalog_status": "ok",
            "rule_match_status": "matched",
            "constraint_status": "passed",
            "constraint_reasons": [],
        }
    )

    first = repository.record_v3_strategy_usage(conversation_id="conversation-1", final_state=state)
    second = repository.record_v3_strategy_usage(conversation_id="conversation-1", final_state=state)

    assert first["created"] is True
    assert second == {"status": "recorded", "id": first["id"], "created": False}
    row = _row(repository, "SELECT * FROM v3_strategy_usage_events WHERE request_id=?", ("request-1",))
    assert row["intent_code"] == "advance_transaction"
    assert row["closing_strategy_code"] == "gentle_invitation"
    assert row["emotion_before"] == "hesitant"
    assert row["emotion_after"] == ""
    assert row["policy_version"] == "2026-09-03.1"
    assert row["decision_status"] == "ok"
    assert row["adopted"] == 0
    assert row["intent_confidence"] == "high"
    assert json.loads(row["intent_secondary_json"]) == ["general_chat"]
    assert row["closing_action"] == "enter"
    assert row["closing_node_key"] == "ask_next_step"
    assert json.loads(row["closing_rule_ids_json"]) == ["external:rule:101"]
    assert row["closing_primary_rule_id"] == "external:rule:101"
    assert row["closing_sequence_source_id"] == "201"
    assert row["closing_action_type_name"] == "预约确认"
    assert row["closing_script_type_id"] == 14
    assert row["closing_catalog_status"] == "ok"
    assert row["closing_rule_match_status"] == "matched"
    assert row["closing_constraint_status"] == "passed"
    assert row["cardpoint_category_key"] == "price"
    assert row["cardpoint_state"] == "resolved"
    assert json.loads(row["decision_reasons_json"]) == ["active_cardpoint_requires_pause"]
    assert json.loads(row["decision_evidence_refs_json"])["closing_decision"] == ["order:verified"]
    assert "this must never be persisted" not in row["payload_json"]
    assert "not persisted" not in row["payload_json"]
    count = _row(repository, "SELECT COUNT(*) AS value FROM v3_strategy_usage_events")
    assert count["value"] == 1

    latest = repository.latest_v3_strategy_state(
        state["sales_contact_key"],
        corp_id="corp-1",
        wechat="sales-a",
        external_userid="external-1",
        customer_id="customer-1",
    )
    assert latest["active_cardpoint"] == ""
    summary = repository.v3_strategy_analytics_summary()
    assert summary["new_blocker_not_paused_count"] == 1


def test_usage_replay_preserves_original_event_and_delivery_fields(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    state = _state("request-replayed", closing_action="enter", closing_sequence="gentle_invitation")
    first = repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=state,
    )
    original_occurred_at = "2026-09-03T01:02:03+00:00"
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE v3_strategy_usage_events "
            "SET occurred_at=?, dispatch_id='dispatch-1', delivery_status='send_failed', "
            "delivered_at='2026-09-03T01:02:05+00:00', failed_reason='provider_error' "
            "WHERE id=?",
            (original_occurred_at, first["id"]),
        )

    replay_state = _state("request-replayed", closing_action="none", closing_sequence="none")
    replayed = repository.record_v3_strategy_usage(
        conversation_id="different-conversation-must-not-overwrite",
        final_state=replay_state,
    )

    assert replayed == {"status": "recorded", "id": first["id"], "created": False}
    row = _row(
        repository,
        "SELECT conversation_id, occurred_at, dispatch_id, delivery_status, delivered_at, "
        "failed_reason, closing_action, closing_strategy_code "
        "FROM v3_strategy_usage_events WHERE request_id=?",
        ("request-replayed",),
    )
    assert row == {
        "conversation_id": "conversation-1",
        "occurred_at": original_occurred_at,
        "dispatch_id": "dispatch-1",
        "delivery_status": "send_failed",
        "delivered_at": "2026-09-03T01:02:05+00:00",
        "failed_reason": "provider_error",
        "closing_action": "enter",
        "closing_strategy_code": "gentle_invitation",
    }


def test_usage_concurrent_replay_creates_exactly_one_event(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    def record_once(_: int) -> dict[str, Any]:
        return repository.record_v3_strategy_usage(
            conversation_id="conversation-1",
            final_state=_state("request-concurrent"),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(record_once, range(8)))

    assert sum(1 for result in results if result["created"]) == 1
    assert len({result["id"] for result in results}) == 1
    count = _row(
        repository,
        "SELECT COUNT(*) AS value FROM v3_strategy_usage_events WHERE request_id=?",
        ("request-concurrent",),
    )
    assert count["value"] == 1


def test_next_turn_links_only_the_exact_sales_contact(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.record_v3_strategy_usage(
        conversation_id="conversation-a",
        final_state=_state("request-a1", emotion="hesitant"),
    )
    second = repository.record_v3_strategy_usage(
        conversation_id="conversation-a",
        final_state=_state("request-a2", intent="advance_transaction", emotion="curious"),
    )
    repository.record_v3_strategy_usage(
        conversation_id="conversation-b",
        final_state=_state("request-b1", wechat="sales-b", emotion="guarded"),
    )

    first_usage = _row(repository, "SELECT emotion_after FROM v3_strategy_usage_events WHERE id=?", (first["id"],))
    first_outcome = _row(repository, "SELECT * FROM v3_strategy_outcome_events WHERE usage_event_id=?", (first["id"],))
    second_usage = _row(repository, "SELECT emotion_after FROM v3_strategy_usage_events WHERE id=?", (second["id"],))
    assert first_usage["emotion_after"] == "curious"
    assert first_outcome["next_usage_event_id"] == second["id"]
    assert first_outcome["next_intent_code"] == "advance_transaction"
    assert first_outcome["next_emotion_code"] == "curious"
    assert first_outcome["emotion_transition"] == "hesitant->curious"
    assert second_usage["emotion_after"] == ""

    latest_a = repository.latest_v3_strategy_state(
        _state("lookup-a", wechat="sales-a")["sales_contact_key"],
        corp_id="corp-1",
        wechat="sales-a",
        external_userid="external-1",
        customer_id="customer-1",
    )
    latest_b = repository.latest_v3_strategy_state(
        _state("lookup-b", wechat="sales-b")["sales_contact_key"],
        corp_id="corp-1",
        wechat="sales-b",
        external_userid="external-1",
        customer_id="customer-1",
    )
    assert latest_a["request_id"] == "request-a2"
    assert latest_a["previous_intent"] == "advance_transaction"
    assert latest_a["previous_emotion"] == "curious"
    assert latest_b["request_id"] == "request-b1"
    assert latest_b["previous_emotion"] == "guarded"
    assert repository.latest_v3_strategy_state(
        _state("invalid-boundary")["sales_contact_key"],
        corp_id="",
        wechat="sales-a",
        external_userid="external-1",
        customer_id="customer-1",
    ) == {}


def test_protocol_events_do_not_link_or_replace_stable_strategy_state(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.record_v3_strategy_usage(
        conversation_id="conversation-a",
        final_state=_state("request-real-1", intent="defer", emotion="hesitant"),
    )
    protocol_state = _state("request-recalled", intent="normal_exchange", emotion="neutral")
    protocol_state["reply_source"] = "platform_recalled_message"
    protocol = repository.record_v3_strategy_usage(
        conversation_id="conversation-a",
        final_state=protocol_state,
    )

    assert _row(
        repository,
        "SELECT customer_turn_eligible FROM v3_strategy_usage_events WHERE id=?",
        (protocol["id"],),
    )["customer_turn_eligible"] == 0
    with repository.store.connect() as conn:
        linked = conn.execute(
            "SELECT * FROM v3_strategy_outcome_events WHERE usage_event_id=?",
            (first["id"],),
        ).fetchone()
    assert linked is None

    latest = repository.latest_v3_strategy_state(
        protocol_state["sales_contact_key"],
        corp_id="corp-1",
        wechat="sales-a",
        external_userid="external-1",
        customer_id="customer-1",
    )
    assert latest["request_id"] == "request-real-1"

    real_next = repository.record_v3_strategy_usage(
        conversation_id="conversation-a",
        final_state=_state("request-real-2", intent="fact_inquiry", emotion="curious"),
    )
    linked = _row(
        repository,
        "SELECT * FROM v3_strategy_outcome_events WHERE usage_event_id=?",
        (first["id"],),
    )
    assert linked["next_usage_event_id"] == real_next["id"]
    assert linked["next_intent_code"] == "fact_inquiry"
    summary = repository.v3_strategy_analytics_summary()
    assert summary["usage_count"] == 2


def test_first_real_customer_turn_is_not_overwritten_by_later_state(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.record_v3_strategy_usage(
        conversation_id="conversation-a",
        final_state=_state("request-policy", emotion="hesitant"),
    )
    guard = _state("request-human-guard")
    for key in ("realtime_intent", "emotion_decision", "closing_decision"):
        guard.pop(key, None)
    guard["reply_source"] = "human_takeover_guard"
    guard["takeover_guard"] = {"decision": "return_empty", "mode": "human"}
    guard_event = repository.record_v3_strategy_usage(
        conversation_id="conversation-a",
        final_state=guard,
    )
    repository.record_v3_strategy_usage(
        conversation_id="conversation-a",
        final_state=_state("request-later", emotion="curious"),
    )

    outcome = _row(
        repository,
        "SELECT * FROM v3_strategy_outcome_events WHERE usage_event_id=?",
        (first["id"],),
    )
    assert outcome["next_usage_event_id"] == guard_event["id"]
    assert outcome["next_intent_code"] == ""
    assert outcome["next_emotion_code"] == ""


def test_outcome_refresh_skips_protocol_user_message(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=_state("request-first"),
    )
    protocol_state = _state("request-protocol")
    protocol_state["reply_source"] = "ignored_platform_auto_message"
    repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=protocol_state,
    )
    repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=_state("request-real-reply", emotion="curious"),
    )
    with repository.store.connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, customer_id, external_userid, corp_id, user_id, wechat, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "conversation-1", "customer-1", "external-1", "corp-1", "staff-1", "sales-a",
                "2026-09-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00",
            ),
        )
        conn.execute(
            "UPDATE v3_strategy_usage_events SET delivered_at=?, delivery_status='send_succeeded' WHERE id=?",
            ("2026-09-01T00:00:00+00:00", first["id"]),
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, request_id, role, content, created_at) "
            "VALUES (?, ?, ?, 'user', '', ?)",
            ("protocol-message", "conversation-1", "request-protocol", "2026-09-01T00:05:00+00:00"),
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, request_id, role, content, created_at) "
            "VALUES (?, ?, ?, 'user', '', ?)",
            ("real-message", "conversation-1", "request-real-reply", "2026-09-01T00:10:00+00:00"),
        )

    repository.refresh_v3_strategy_outcomes()

    outcome = _row(
        repository,
        "SELECT * FROM v3_strategy_outcome_events WHERE usage_event_id=?",
        (first["id"],),
    )
    assert outcome["first_reply_after_msgid"] == "real-message"
    assert outcome["first_reply_after_at"] == "2026-09-01T00:10:00+00:00"


def test_isolated_run_does_not_change_reply_or_order_outcome(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    recorded = repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=_state("request-production"),
    )
    with repository.store.connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, customer_id, external_userid, corp_id, user_id, wechat, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "conversation-1", "customer-1", "external-1", "corp-1", "staff-1", "sales-a",
                "2026-09-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00",
            ),
        )
        conn.execute(
            "UPDATE v3_strategy_usage_events SET delivered_at=?, delivery_status='send_succeeded' WHERE id=?",
            ("2026-09-01T00:00:00+00:00", recorded["id"]),
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, request_id, role, content, created_at) "
            "VALUES (?, ?, ?, 'user', '', ?)",
            (
                "isolated-message", "conversation-1", "request-isolated",
                "2026-09-01T00:10:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO runs (request_id, conversation_id, customer_id, input_snapshot, output_snapshot, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "request-isolated", "conversation-1", "customer-1",
                json.dumps({"request_context": {"test_isolated": True}}),
                json.dumps({"order_state_snapshot": {"order_state": "paid"}}),
                "2026-09-01T00:10:00+00:00",
            ),
        )

    repository.refresh_v3_strategy_outcomes()

    outcome = _row(
        repository,
        "SELECT * FROM v3_strategy_outcome_events WHERE usage_event_id=?",
        (recorded["id"],),
    )
    assert outcome["first_reply_after_msgid"] == ""
    assert outcome["customer_replied_24h"] == 0
    assert outcome["order_state_after_24h"] == ""
    assert outcome["paid_after_24h"] == 0


def test_unknown_delivery_never_calls_platform_order_provider(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    recorded = repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=_state("request-no-delivery"),
    )
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE v3_strategy_usage_events SET occurred_at=?, updated_at=? WHERE id=?",
            (old, old, recorded["id"]),
        )
    calls: list[str] = []

    result = repository.refresh_v3_strategy_outcomes(
        order_snapshot_provider=lambda event: calls.append(event["request_id"]) or {
            "status": "success", "order_state": "paid",
        },
    )
    second = repository.refresh_v3_strategy_outcomes(
        order_snapshot_provider=lambda event: calls.append(event["request_id"]) or {
            "status": "success", "order_state": "paid",
        },
    )

    assert result["order_provider_calls"] == 0
    assert second["order_provider_calls"] == 0
    assert calls == []


def test_legacy_events_without_policy_version_do_not_reduce_policy_coverage(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    state = _state("legacy-event")
    state["ai_sales_policy"] = {"runtime_mode": "off", "policy_version": ""}
    repository.record_v3_strategy_usage(conversation_id="conversation-1", final_state=state)

    summary = repository.v3_strategy_analytics_summary()

    assert summary["usage_count"] == 1
    assert summary["decision_eligible_count"] == 0
    assert summary["decision_coverage_rate"] == 0.0


def test_delivery_anchor_added_after_next_turn_recomputes_reply_and_order_windows(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=_state("request-before-delivery", emotion="hesitant"),
    )
    second = repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=_state("request-next-turn", emotion="curious"),
    )
    before_delivery = datetime.now(timezone.utc) - timedelta(minutes=90)
    delivered_at = datetime.now(timezone.utc) - timedelta(minutes=60)
    with repository.store.connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, customer_id, external_userid, corp_id, user_id, wechat, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "conversation-1", "customer-1", "external-1", "corp-1", "staff-1", "sales-a",
                before_delivery.isoformat(), before_delivery.isoformat(),
            ),
        )
        conn.execute(
            "UPDATE v3_strategy_usage_events SET dispatch_id='dispatch-1' WHERE id=?",
            (first["id"],),
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, request_id, role, content, created_at) "
            "VALUES (?, ?, ?, 'user', '', ?)",
            ("message-before-delivery", "conversation-1", "request-next-turn", before_delivery.isoformat()),
        )
        # Simulate data written by the old occurred_at fallback.  The first
        # real delivery anchor must invalidate it rather than preserve it.
        conn.execute(
            "UPDATE v3_strategy_outcome_events SET customer_replied_1h=1, "
            "customer_replied_24h=1, first_reply_after_at=?, "
            "order_state_after_24h='paid', paid_after_24h=1 "
            "WHERE usage_event_id=?",
            (before_delivery.isoformat(), first["id"]),
        )

    linked = _row(
        repository,
        "SELECT * FROM v3_strategy_outcome_events WHERE usage_event_id=?",
        (first["id"],),
    )
    assert linked["next_usage_event_id"] == second["id"]
    assert linked["attribution_anchor_source"] == "unknown"

    repository.update_v3_strategy_usage_delivery(
        dispatch_id="dispatch-1",
        delivery_status="send_succeeded",
        delivered_at=delivered_at.isoformat(),
    )
    repository.refresh_v3_strategy_outcomes()

    refreshed = _row(
        repository,
        "SELECT * FROM v3_strategy_outcome_events WHERE usage_event_id=?",
        (first["id"],),
    )
    assert refreshed["attribution_anchor_source"] == "delivered_at"
    assert refreshed["customer_replied_1h"] == 0
    assert refreshed["customer_replied_24h"] == 0
    assert refreshed["first_reply_after_at"] == ""
    assert refreshed["order_state_after_24h"] == ""
    assert refreshed["paid_after_24h"] == 0
    assert refreshed["next_usage_event_id"] == second["id"]


def test_contact_link_survives_later_customer_id_enrichment(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    before = _state("request-before", customer_id="", emotion="hesitant")
    after = _state("request-after", customer_id="customer-1", emotion="curious")
    assert before["sales_contact_key"] == after["sales_contact_key"]

    first = repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=before,
    )
    second = repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=after,
    )

    linked = _row(
        repository,
        "SELECT next_usage_event_id FROM v3_strategy_outcome_events WHERE usage_event_id=?",
        (first["id"],),
    )
    latest = repository.latest_v3_strategy_state(
        after["sales_contact_key"],
        corp_id="corp-1",
        wechat="sales-a",
        external_userid="external-1",
        customer_id="customer-1",
    )
    assert linked["next_usage_event_id"] == second["id"]
    assert latest["request_id"] == "request-after"


def test_proactive_delivery_is_blocked_by_scoped_stop_contact(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    scope = build_customer_scope(
        corp_id="corp-1",
        wechat="sales-a",
        external_userid="external-1",
        customer_id="customer-1",
    )
    repository.save_memory(
        scope.sales_contact_key,
        {
            "history_events": [
                {
                    "event_id": "stop-contact-1",
                    "event_type": "stop_contact_confirmed",
                    "facts": {"request_id": "request-1"},
                }
            ]
        },
    )
    service = MessageDeliveryService(
        Settings(AI_PATHS_DB_PATH=tmp_path / "analytics.db", AICS_STORAGE_BACKEND="sqlite"),
        repository,
    )

    with pytest.raises(RuntimeError, match="explicit_stop_contact"):
        service.assert_proactive_send_allowed(
            {
                "corp_id": "corp-1",
                "wechat": "sales-a",
                "external_userid": "external-1",
                "customer_id": "customer-1",
            }
        )


def test_new_customer_turn_cancels_existing_closing_shadow_plan(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    existing = repository.create_outreach_plan(
        customer_id="customer-1",
        corp_id="corp-1",
        user_id="staff-1",
        wechat="sales-a",
        external_userid="external-1",
        customer_stage="",
        stall_reason="",
        customer_psychology="",
        plan_goal="shadow",
        source_snapshot={"plan_type": "closing_sequence", "runtime_mode": "shadow"},
        tasks=[{"intent": "followup", "message_goal": "shadow", "before_send_check": True}],
        sop_plan_id="closing_sequence:scope:price:old-request",
    )
    plan_id = existing["plan"]["id"]
    planner = PlanGenerator(
        repository=repository,
        model_client=None,
        system_client=None,
        customer_context_service=None,
        precision_qa_playbook_service=None,
        sop_reply_pack_service=None,
        coze_client=None,
        sales_strategy_service=None,
    )
    state = _state("new-customer-turn")
    state.update(
        {
            "memory_persist_allowed": True,
            "ai_sales_policy": {
                "runtime_mode": "active",
                "closing": {"silent_tasks_mode": "shadow", "sequences": []},
            },
            "closing_decision": {
                "action": "pause",
                "sequence_key": "none",
                "node_key": "",
                "customer_state": "new_blocker",
            },
        }
    )

    result = planner.record_closing_sequence_shadow(state)
    after = repository.get_outreach_plan(plan_id)

    assert result["created"] is False
    assert result["cancelled"] == 1
    assert after["plan"]["status"] == "cancelled"
    assert after["tasks"][0]["status"] == "cancelled"


def test_local_only_stop_contact_is_replayed_after_repository_recovers(tmp_path: Path) -> None:
    class FlakyRepository:
        def __init__(self) -> None:
            self.fail = True
            self.memory: dict[str, Any] | None = None

        def load_memory(self, _customer_id: str) -> dict[str, Any] | None:
            return self.memory

        def save_memory(self, _customer_id: str, memory: dict[str, Any]) -> None:
            if self.fail:
                raise ConnectionError("db unavailable")
            self.memory = memory

        def has_stop_contact(self, _customer_id: str) -> bool:
            return any(
                event.get("event_type") == "stop_contact_confirmed"
                for event in (self.memory or {}).get("history_events") or []
            )

    repository = FlakyRepository()
    store = CustomerMemoryStore(
        Settings(_env_file=None, memory_dir=tmp_path / "memory"),
        repository,  # type: ignore[arg-type]
    )
    recorded = store.record_stop_contact(
        "sales_contact:v2:digest",
        request_id="request-1",
        evidence_refs=["current_message"],
    )
    assert recorded["status"] == "recorded_local_only"

    repository.fail = False
    assert store.has_stop_contact("sales_contact:v2:digest") is True
    assert repository.has_stop_contact("sales_contact:v2:digest") is True


def test_memory_load_falls_back_to_local_file_when_repository_misses(tmp_path: Path) -> None:
    class EmptyRepository:
        def load_memory(self, _customer_id: str) -> None:
            return None

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    store = CustomerMemoryStore(
        Settings(_env_file=None, memory_dir=memory_dir),
        EmptyRepository(),  # type: ignore[arg-type]
    )
    customer_id = "sales_contact:v2:digest"
    store._path(customer_id).write_text(  # noqa: SLF001 - regression covers the local persistence contract
        json.dumps({"customer_id": customer_id, "profile": {"name": "本地客户"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert store.load(customer_id)["profile"]["name"] == "本地客户"


def test_filters_dimensions_and_summary_use_delivery_known_denominator(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    recorded = repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=_state(
            "request-1",
            intent="advance_transaction",
            emotion="curious",
            closing_action="enter",
            closing_sequence="gentle_invitation",
        ),
    )
    repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=_state(
            "reply-1", wechat="sales-b", intent="normal_exchange", emotion="neutral",
        ),
    )
    with repository.store.connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, customer_id, external_userid, corp_id, user_id, wechat, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "conversation-1", "customer-1", "external-1", "corp-1", "staff-1", "sales-a",
                "2026-09-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00",
            ),
        )
        conn.execute(
            "UPDATE v3_strategy_usage_events SET delivery_status='send_succeeded', delivered_at=? WHERE id=?",
            ("2026-09-01T00:00:00+00:00", recorded["id"]),
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, request_id, role, content, created_at) "
            "VALUES (?, ?, ?, 'user', '', ?)",
            ("message-1", "conversation-1", "reply-1", "2026-09-01T00:30:00+00:00"),
        )
    repository.refresh_v3_strategy_outcomes()

    summary = repository.v3_strategy_analytics_summary(
        intent_code="advance_transaction",
        emotion_code="curious",
        closing_sequence_key="gentle_invitation",
        closing_action="enter",
        decision_status="ok",
    )
    assert summary["usage_count"] == 1
    assert summary["delivered_attribution_count"] == 1
    assert summary["customer_replied_24h_rate"] == 1.0
    assert summary["decision_coverage_rate"] == 1.0
    assert repository.v3_strategy_analytics_by_dimension(dimension="intent")["items"][0]["intent_code"] == "advance_transaction"
    assert repository.v3_strategy_analytics_by_dimension(dimension="emotion")["items"][0]["emotion_code"] == "curious"
    assert repository.v3_strategy_analytics_by_dimension(dimension="closing")["items"][0]["closing_sequence_key"] == "gentle_invitation"
    transitions = repository.v3_strategy_analytics_by_dimension(dimension="transitions")
    assert transitions["dimension"] == "transitions"
    assert transitions["items"] == []


def test_by_closing_uses_closing_action_adoption_without_changing_global_adoption(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    closing_state = _state(
        "request-closing",
        closing_action="enter",
        closing_sequence="gentle_invitation",
        closing_node="ask_next_step",
    )
    closing_state["closing_decision"]["rule_ids"] = ["external:rule:101"]
    repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=closing_state,
    )
    follow_state = _state(
        "request-follow",
        intent="normal_exchange",
        closing_action="none",
        closing_sequence="none",
    )
    follow_state["reply_knowledge_use"] = {"selected_script_ids": ["script-1"]}
    repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=follow_state,
    )

    summary = repository.v3_strategy_analytics_summary()
    assert summary["adopted_count"] == 1
    assert summary["adoption_rate"] == 0.5
    intent_items = repository.v3_strategy_analytics_by_dimension(dimension="intent")["items"]
    assert sum(item["adopted_count"] for item in intent_items) == 1

    closing_items = repository.v3_strategy_analytics_by_dimension(dimension="closing")["items"]
    by_action = {item["closing_action"]: item for item in closing_items}
    assert by_action["enter"]["adopted_count"] == 1
    assert by_action["enter"]["adoption_rate"] == 1.0
    assert by_action["none"]["adopted_count"] == 0
    assert by_action["none"]["adoption_rate"] == 0.0
    by_rule = repository.v3_strategy_analytics_by_dimension(
        dimension="closing_rule",
        closing_rule_id="external:rule:101",
    )
    assert by_rule["items"][0]["closing_rule_id"] == "external:rule:101"
    assert by_rule["items"][0]["adopted_count"] == 1


def test_system_guard_is_excluded_from_policy_coverage_and_not_a_failure(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    state = _state("takeover-request", intent="explicit_exit")
    for key in ("realtime_intent", "emotion_decision", "closing_decision"):
        state.pop(key, None)
    state.update(
        {
            "reply_source": "human_takeover_guard",
            "takeover_guard": {"decision": "return_empty", "mode": "human"},
            "semantic_route": {},
        }
    )

    repository.record_v3_strategy_usage(conversation_id="conversation-1", final_state=state)

    summary = repository.v3_strategy_analytics_summary()
    failures = repository.v3_strategy_analytics_failures()
    assert summary["usage_count"] == 1
    assert summary["decision_eligible_count"] == 0
    assert summary["decision_coverage_rate"] == 0.0
    assert failures["items"] == []


def test_order_provider_runs_outside_transaction_caches_and_never_erases_known_state(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=_state("request-1"),
    )
    repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=_state("request-2"),
    )
    old = "2020-08-20T00:00:00+00:00"
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE v3_strategy_usage_events SET occurred_at=?, delivered_at=?, "
            "delivery_status='platform_accepted', updated_at=?",
            (old, old, old),
        )
        conn.execute("UPDATE v3_strategy_outcome_events SET updated_at=''")
    calls: list[str] = []

    def provider(event: dict[str, Any]) -> dict[str, Any]:
        calls.append(event["customer_id"])
        # This write succeeds only because refresh releases its scan transaction before the callback.
        with repository.store.connect() as conn:
            conn.execute("UPDATE v3_strategy_usage_events SET reply_source=reply_source WHERE id=?", (event["usage_event_id"],))
        return {
            "status": "success",
            "windows": {"24h": "scheduled", "72h": "scheduled", "7d": "scheduled"},
            "source": "platform-read-api",
        }

    result = repository.refresh_v3_strategy_outcomes(order_snapshot_provider=provider)
    assert result["order_provider_calls"] == 2
    assert calls == ["customer-1", "customer-1"]
    outcome = _row(repository, "SELECT * FROM v3_strategy_outcome_events WHERE usage_event_id=?", (first["id"],))
    assert outcome["order_state_after_24h"] == "scheduled"
    assert outcome["order_state_after_72h"] == "scheduled"
    assert outcome["order_state_after_7d"] == "scheduled"
    assert outcome["scheduled_after_7d"] == 1
    assert outcome["order_source"] == "platform-read-api"
    assert outcome["order_query_status"] == "success"

    with repository.store.connect() as conn:
        conn.execute("UPDATE v3_strategy_outcome_events SET updated_at='' WHERE usage_event_id=?", (first["id"],))
    failed = repository.refresh_v3_strategy_outcomes(
        order_snapshot_provider=lambda _event: {"status": "error", "error": "timeout"},
    )
    assert failed["order_provider_errors"] == 1
    unchanged = _row(repository, "SELECT * FROM v3_strategy_outcome_events WHERE usage_event_id=?", (first["id"],))
    assert unchanged["order_state_after_7d"] == "scheduled"
    assert unchanged["scheduled_after_7d"] == 1
    assert unchanged["order_query_status"] == "error"
    assert unchanged["order_query_error"] == "timeout"


def test_order_provider_cache_is_contact_scoped_and_current_only_is_not_exact(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.record_v3_strategy_usage(
        conversation_id="conversation-a",
        final_state=_state("request-a", wechat="sales-a"),
    )
    second = repository.record_v3_strategy_usage(
        conversation_id="conversation-b",
        final_state=_state("request-b", wechat="sales-b"),
    )
    old = "2020-08-20T00:00:00+00:00"
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE v3_strategy_usage_events SET occurred_at=?, delivered_at=?, "
            "delivery_status='platform_accepted', updated_at=?",
            (old, old, old),
        )
    calls: list[str] = []

    def provider(event: dict[str, Any]) -> dict[str, Any]:
        calls.append(event["sales_contact_key"])
        return {"status": "success", "order_state": "paid", "source": "platform-read-api"}

    result = repository.refresh_v3_strategy_outcomes(order_snapshot_provider=provider)
    assert result["order_provider_calls"] == 2
    assert set(calls) == {
        _state("cache-a", wechat="sales-a")["sales_contact_key"],
        _state("cache-b", wechat="sales-b")["sales_contact_key"],
    }
    for usage_event_id in (first["id"], second["id"]):
        outcome = _row(
            repository,
            "SELECT * FROM v3_strategy_outcome_events WHERE usage_event_id=?",
            (usage_event_id,),
        )
        assert outcome["order_query_status"] == "backfill_current_only"
        assert outcome["order_state_after_24h"] == ""
        assert outcome["paid_after_72h"] == 0
        attribution = json.loads(outcome["payload_json"])["order_attribution"]
        assert attribution["mode"] == "backfill_current_only"
        assert attribution["window_definitions_hours"]["72h"] == 72


def test_current_platform_state_freezes_at_first_poll_after_due_window(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    recorded = repository.record_v3_strategy_usage(
        conversation_id="conversation-1",
        final_state=_state("request-due"),
    )
    due = (datetime.now(timezone.utc) - timedelta(hours=24, minutes=5)).isoformat()
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE v3_strategy_usage_events SET occurred_at=?, delivered_at=?, "
            "delivery_status='platform_accepted', updated_at=? WHERE id=?",
            (due, due, due, recorded["id"]),
        )

    result = repository.refresh_v3_strategy_outcomes(
        order_snapshot_provider=lambda _event: {
            "status": "ok",
            "order_state": "paid",
            "source": "platform-read-api",
            "selection_mode": "new_order_after_event",
        }
    )

    assert result["order_provider_calls"] == 1
    outcome = _row(
        repository,
        "SELECT * FROM v3_strategy_outcome_events WHERE usage_event_id=?",
        (recorded["id"],),
    )
    assert outcome["order_state_after_24h"] == "paid"
    assert outcome["paid_after_24h"] == 1
    payload = json.loads(outcome["payload_json"])
    assert payload["order_observations"][-1]["order_state"] == "paid"
    assert payload["order_attribution"]["poll_delay_seconds"]["24h"] < 3600


def test_sqlite_old_schema_upgrade_and_mysql_metadata_include_new_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    schema_path = Path(__file__).resolve().parents[1] / "ai_paths" / "app" / "services" / "storage" / "schema.sql"
    old_schema = schema_path.read_text(encoding="utf-8")
    for column in sorted(USAGE_ADDITIONS | OUTCOME_ADDITIONS):
        old_schema = "\n".join(
            line for line in old_schema.splitlines()
            if not line.strip().startswith(f"{column} ")
        )
    for name in (
        "idx_v3_strategy_usage_intent", "idx_v3_strategy_usage_emotion",
        "idx_v3_strategy_usage_closing", "idx_v3_strategy_usage_decision",
        "idx_v3_strategy_usage_closing_catalog", "idx_v3_strategy_usage_closing_rule",
    ):
        lines = old_schema.splitlines()
        old_schema = "\n".join(
            line for index, line in enumerate(lines)
            if name not in line and not (index > 0 and name in lines[index - 1])
        )
    with sqlite3.connect(db_path) as conn:
        conn.executescript(old_schema)
    settings = Settings(AI_PATHS_DB_PATH=db_path, AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    with store.connect() as conn:
        usage_columns = {row["name"] for row in conn.execute("PRAGMA table_info(v3_strategy_usage_events)")}
        outcome_columns = {row["name"] for row in conn.execute("PRAGMA table_info(v3_strategy_outcome_events)")}
    assert USAGE_ADDITIONS <= usage_columns
    assert OUTCOME_ADDITIONS <= outcome_columns
    assert USAGE_ADDITIONS <= set(EXPECTED_COLUMNS["aics_v3_strategy_usage_events"])
    assert OUTCOME_ADDITIONS <= set(EXPECTED_COLUMNS["aics_v3_strategy_outcome_events"])
