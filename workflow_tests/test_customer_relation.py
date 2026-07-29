from __future__ import annotations

from app.services.customer_relation import (
    customer_relation_is_deleted,
    normalize_customer_relation,
)


def test_normalize_active_customer_relation() -> None:
    relation = normalize_customer_relation(
        {
            "code": 0,
            "data": {
                "customer_relation": {
                    "status": "active",
                    "is_deleted": False,
                    "deleted_at": None,
                    "updated_at": "2026-07-28T14:48:51.104327+08:00",
                }
            },
        }
    )

    assert relation == {
        "available": True,
        "status": "active",
        "is_deleted": False,
        "deleted_at": "",
        "updated_at": "2026-07-28T14:48:51.104327+08:00",
    }
    assert not customer_relation_is_deleted(relation)


def test_normalize_deleted_customer_relation_from_status_or_boolean() -> None:
    by_status = normalize_customer_relation(
        {"data": {"customer_relation": {"status": "deleted", "is_deleted": False}}}
    )
    by_boolean = normalize_customer_relation(
        {"customer_relation": {"status": "active", "is_deleted": "true"}}
    )

    assert customer_relation_is_deleted(by_status)
    assert customer_relation_is_deleted(by_boolean)


def test_missing_customer_relation_is_not_treated_as_active() -> None:
    relation = normalize_customer_relation({"data": {"messages": []}})

    assert relation["available"] is False
    assert customer_relation_is_deleted(relation) is False
