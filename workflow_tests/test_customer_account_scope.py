from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.services.customer_payment_state import normalize_prepay_facts, paid_order_protection_fact, resolved_payment_fact
from app.services.customer_scope import build_customer_scope
from app.services.sop_event_service import _send_once_key as event_send_once_key
from app.services.storage import AppRepository, SQLiteStore


CORP_ID = "corp-test"
CUSTOMER_ID = "21984814"
EXTERNAL_USERID = "external-21984814"


def test_sales_contact_scope_uses_wechat_but_not_operator_user_id() -> None:
    first = build_customer_scope(
        corp_id=CORP_ID,
        wechat="CS001",
        external_userid=EXTERNAL_USERID,
        customer_id=CUSTOMER_ID,
        user_id="user-a",
    )
    same_account = build_customer_scope(
        corp_id=CORP_ID,
        wechat="CS001",
        external_userid=EXTERNAL_USERID,
        customer_id=CUSTOMER_ID,
        user_id="user-b",
    )
    other_account = build_customer_scope(
        corp_id=CORP_ID,
        wechat="CS002",
        external_userid=EXTERNAL_USERID,
        customer_id=CUSTOMER_ID,
        user_id="user-a",
    )

    assert first.persistence_allowed is True
    assert first.sales_contact_key == same_account.sales_contact_key
    assert first.sales_contact_key != other_account.sales_contact_key
    assert first.global_customer_key == other_account.global_customer_key


def test_missing_wechat_disables_scoped_persistence_and_send_once() -> None:
    scope = build_customer_scope(
        corp_id=CORP_ID,
        wechat="",
        external_userid=EXTERNAL_USERID,
        customer_id=CUSTOMER_ID,
    )
    assert scope.persistence_allowed is False
    assert scope.sales_contact_key == ""
    assert "wechat" in scope.missing
    assert event_send_once_key(
        {"corp_id": CORP_ID, "wechat": "", "external_userid": EXTERNAL_USERID},
        "s10_activity_intro",
    ) == ""


def test_same_customer_has_independent_sop_send_once_per_wechat() -> None:
    first = event_send_once_key(
        {"corp_id": CORP_ID, "wechat": "CS001", "external_userid": EXTERNAL_USERID},
        "s10_activity_intro",
    )
    second = event_send_once_key(
        {"corp_id": CORP_ID, "wechat": "CS002", "external_userid": EXTERNAL_USERID},
        "s10_activity_intro",
    )
    assert first != second
    assert "wechat:cs001" in first
    assert "wechat:cs002" in second


def test_paid_order_uses_three_calendar_month_order_created_proxy() -> None:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    protected = paid_order_protection_fact(
        {"prepay_paid": 10, "created_at": "2026-04-20T12:00:00+00:00"},
        now=now,
    )
    expired = paid_order_protection_fact(
        {"prepay_paid": 10, "created_at": "2026-04-20T11:59:59+00:00"},
        now=now,
    )
    unknown = paid_order_protection_fact({"prepay_paid": 10}, now=now)

    assert protected["paid_protection_status"] == "protected"
    assert expired["paid_protection_status"] == "expired"
    assert unknown["paid_protection_status"] == "unknown_time_protected"
    assert protected["paid_time_source"] == "order_created_at_proxy"


def test_paid_order_accepts_platform_camel_case_creation_time() -> None:
    fact = paid_order_protection_fact(
        {"prepay_paid": 10, "createTime": "2020-01-01T00:00:00+00:00"},
        now=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert fact["paid_protection_status"] == "expired"
    assert fact["paid_time_source"] == "order_created_at_proxy"


def test_expired_paid_order_is_not_current_paid_fact() -> None:
    old_order = {
        "id": "old-paid",
        "status": "pending",
        "prepay_required": 10,
        "prepay_paid": 10,
        "created_at": "2020-01-01T00:00:00+00:00",
    }
    normalized = normalize_prepay_facts(old_order)
    assert normalized["deposit_state"] == "historical_paid_expired"
    assert resolved_payment_fact(orders=[{**old_order, **normalized}]) == {}


def test_admin_clear_only_removes_selected_wechat_memory_and_sop(tmp_path: Path) -> None:
    store = SQLiteStore(SimpleNamespace(db_path=tmp_path / "scope.db"))
    store.initialize()
    repository = AppRepository(store)
    scope_a = build_customer_scope(
        corp_id=CORP_ID,
        wechat="CS001",
        external_userid=EXTERNAL_USERID,
        customer_id=CUSTOMER_ID,
    )
    scope_b = build_customer_scope(
        corp_id=CORP_ID,
        wechat="CS002",
        external_userid=EXTERNAL_USERID,
        customer_id=CUSTOMER_ID,
    )
    repository.save_memory(scope_a.sales_contact_key, {"portrait": {"account": "A"}})
    repository.save_memory(scope_b.sales_contact_key, {"portrait": {"account": "B"}})

    with store.connect() as conn:
        for wechat in ("CS001", "CS002"):
            conn.execute(
                """
                INSERT INTO conversations
                    (id, customer_id, external_userid, corp_id, user_id, wechat, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, '', ?, '', ?, ?)
                """,
                (f"conv-{wechat}", CUSTOMER_ID, EXTERNAL_USERID, CORP_ID, wechat, "2026-07-20T00:00:00+00:00", "2026-07-20T00:00:00+00:00"),
            )
            event_id = f"event-{wechat}"
            conn.execute(
                """
                INSERT INTO sop_events
                    (id, event_id, event_type, source, received_at, updated_at)
                VALUES (?, ?, 'test', 'test', ?, ?)
                """,
                (event_id, event_id, "2026-07-20T00:00:00+00:00", "2026-07-20T00:00:00+00:00"),
            )
            conn.execute(
                """
                INSERT INTO sop_send_tasks
                    (id, event_id, idempotency_key, customer_id, external_userid, corp_id, wechat,
                     sop_pack_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 's10_activity_intro', 'sent', ?, ?)
                """,
                (
                    f"task-{wechat}",
                    event_id,
                    f"idem-{wechat}",
                    CUSTOMER_ID,
                    EXTERNAL_USERID,
                    CORP_ID,
                    wechat,
                    "2026-07-20T00:00:00+00:00",
                    "2026-07-20T00:00:00+00:00",
                ),
            )

    result = repository.clear_customer_records(
        CUSTOMER_ID,
        wechat="CS001",
        corp_id=CORP_ID,
        external_userid=EXTERNAL_USERID,
        clear_memory=True,
        clear_sop=True,
    )

    assert result["status"] == "ok"
    assert repository.load_memory(scope_a.sales_contact_key) is None
    assert repository.load_memory(scope_b.sales_contact_key)["portrait"]["account"] == "B"
    assert repository.list_sent_sop_pack_ids_for_customer(
        customer_id=CUSTOMER_ID,
        external_userid=EXTERNAL_USERID,
        corp_id=CORP_ID,
        wechat="CS001",
    ) == []
    assert repository.list_sent_sop_pack_ids_for_customer(
        customer_id=CUSTOMER_ID,
        external_userid=EXTERNAL_USERID,
        corp_id=CORP_ID,
        wechat="CS002",
    ) == ["s10_activity_intro"]
