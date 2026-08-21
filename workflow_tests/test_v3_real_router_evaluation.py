from __future__ import annotations

from scripts.run_v3_real_router_evaluation import (
    _conversation_until_last_customer,
    _masked_case_id,
    _normalized_conversation,
    _redact,
)


def test_redaction_removes_identity_phone_and_signed_url() -> None:
    raw = (
        "客户 wmanZQSQAASecret 电话13477803587 "
        "https://example.test/media.mp3?token=secret&sig=value"
    )

    redacted = _redact(raw, ["wmanZQSQAASecret"])

    assert "wmanZQSQAASecret" not in redacted
    assert "13477803587" not in redacted
    assert "secret" not in redacted
    assert "[手机号已脱敏]" in redacted


def test_conversation_stops_before_messages_after_last_customer_turn() -> None:
    normalized = _normalized_conversation(
        [
            {"role": "assistant", "content": "您好", "created_at": "2026-08-21 10:00:00"},
            {"role": "customer", "content": "多少钱", "created_at": "2026-08-21 10:01:00"},
            {"role": "assistant", "content": "268元", "created_at": "2026-08-21 10:02:00"},
        ],
        identity_values=[],
    )

    history, current = _conversation_until_last_customer(normalized) or ([], {})

    assert [item["content"] for item in history] == ["您好"]
    assert current["content"] == "多少钱"
    assert current["message_ref"] == "current_message"


def test_platform_from_field_maps_customer_and_staff_roles() -> None:
    normalized = _normalized_conversation(
        [
            {"from": "staff", "content": "您好"},
            {"from": "customer", "content": "离我远吗"},
        ],
        identity_values=[],
    )

    assert [item["role"] for item in normalized] == ["assistant", "customer"]


def test_simulated_case_id_is_stable_and_contains_no_real_identity() -> None:
    identity = {
        "corp_id": "corp-secret",
        "wechat": "DY258",
        "external_userid": "external-secret",
        "customer_id": "22000000",
    }

    first = _masked_case_id(identity, 1)
    second = _masked_case_id(identity, 1)

    assert first == second
    assert first.startswith("sim_router_001_")
    assert "secret" not in first
    assert "22000000" not in first
