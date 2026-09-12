from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.graph.nodes.material_selection import _mainline_delivery_state  # noqa: E402
from app.graph.nodes.reply_admission import (  # noqa: E402
    _validate_customer_visible_mainline_boundary,
    _validate_mainline_sales_action,
)
from app.services.v3_semantic_router_service import (  # noqa: E402
    CURRENT_INTENT_CONTINUATION_SIGNALS,
)


def _mainline(
    *,
    roles: tuple[str, ...] = (),
    appointment_active: bool = False,
    paid: bool = False,
    signals: tuple[str, ...] = (),
    friction: str = "none",
    payment_card_available: bool = False,
) -> dict:
    return _mainline_delivery_state(
        structured_delivered_assets=[{"asset_role": role} for role in roles],
        registration_fact_status={
            "has_active_appointment": appointment_active,
            "authoritative_paid": paid,
        },
        payment_channel_availability={
            "payment_card": {"available": payment_card_available}
        },
        semantic_route={
            "current_intent": {"continuation_signals": list(signals)},
            "current_friction": {"status": friction},
        },
    )


def _state(mainline: dict, *, action: str, customer_state: str = "continue_sales") -> dict:
    return {
        "evidence_join": {},
        "mainline_delivery_state": mainline,
        "reply_sales_judgment": {"next_sales_action": {"type": action}},
        "reply_policy_decision": {
            "closing_decision": {"customer_state": customer_state}
        },
    }


def test_effect_stage_cannot_be_skipped_by_booking_but_current_store_delivery_is_allowed() -> None:
    mainline = _mainline()

    assert mainline["next_missing_stage"] == "effect_evidence"
    assert "invite_booking" not in mainline["allowed_next_sales_action_types"]
    _validate_mainline_sales_action(_state(mainline, action="send_store"))
    with pytest.raises(ValueError, match="exceeds_delivered_mainline"):
        _validate_mainline_sales_action(_state(mainline, action="invite_booking"))


def test_booking_only_opens_after_effect_activity_and_store_are_delivered() -> None:
    mainline = _mainline(
        roles=("effect_evidence", "activity_offer", "address_evidence")
    )

    assert mainline["next_missing_stage"] == "appointment"
    assert "invite_booking" in mainline["allowed_next_sales_action_types"]
    _validate_mainline_sales_action(_state(mainline, action="invite_booking"))


@pytest.mark.parametrize(
    "reply",
    [
        "我先发一个真实效果案例给您。您大概工作日还是周末过来呢？",
        "我帮您预约一下，您哪天方便？",
        "效果图发您参考，什么时候方便到店？",
        "您直接预约到店就行。",
        "我帮您做个预约登记吧。",
        "不着急，您周末方便过来吗？",
    ],
)
def test_visible_booking_invitation_cannot_hide_behind_a_value_delivery_action(reply: str) -> None:
    mainline = _mainline()
    state = _state(mainline, action="deliver_value")

    with pytest.raises(ValueError, match="next_sales_action_exceeds_delivered_mainline"):
        _validate_customer_visible_mainline_boundary(
            [{"type": "text", "content": reply}],
            state,
        )


def test_visible_mainline_guard_allows_negated_or_reached_booking_language() -> None:
    early_state = _state(_mainline(), action="deliver_value")
    allowed_explanations = (
        "不用现在定哪天，我先把效果图发您看看。",
        "周末营业时间我还需要以门店确认为准。",
        "预约到店后会先了解清楚您的情况。",
        "您问的是周末营业时间，到店接待需要门店确认。",
        "您问什么时候可以到店，这个时间目前还没同步。",
        "您不用现在直接预约到店，先看效果。",
        "不是让您现在直接预约到店，我先发案例。",
        "不建议您直接预约到店，先了解清楚。",
    )
    for reply in allowed_explanations:
        _validate_customer_visible_mainline_boundary(
            [{"type": "text", "content": reply}],
            early_state,
        )

    ready = _mainline(roles=("effect_evidence", "activity_offer", "address_evidence"))
    _validate_customer_visible_mainline_boundary(
        [{"type": "text", "content": "您大概工作日还是周末过来呢？我帮您预约。"}],
        _state(ready, action="invite_booking"),
    )


@pytest.mark.parametrize("customer_state", ["pause_current_turn", "hard_stop_marketing"])
def test_visible_booking_is_blocked_when_customer_state_forbids_current_sales_advance(
    customer_state: str,
) -> None:
    ready = _mainline(roles=("effect_evidence", "activity_offer", "address_evidence"))
    state = _state(ready, action="deliver_value", customer_state=customer_state)

    with pytest.raises(ValueError, match="next_sales_action_exceeds_delivered_mainline"):
        _validate_customer_visible_mainline_boundary(
            [{"type": "text", "content": "您先忙，周末方便过来吗？"}],
            state,
        )


def test_router_friction_does_not_veto_reply_but_paused_turn_cannot_advance_booking() -> None:
    mainline = _mainline(
        roles=("effect_evidence", "activity_offer", "address_evidence"),
        friction="explicit",
    )
    assert mainline["friction_active"] is True
    assert "invite_booking" in mainline["allowed_next_sales_action_types"]
    _validate_mainline_sales_action(_state(mainline, action="invite_booking"))

    with pytest.raises(ValueError, match="paused_turn_cannot_advance"):
        _validate_mainline_sales_action(
            _state(mainline, action="invite_booking", customer_state="pause_current_turn")
        )


def test_payment_action_availability_uses_real_card_not_router_signal() -> None:
    without_signal = _mainline(
        roles=("effect_evidence", "activity_offer", "address_evidence"),
        appointment_active=True,
        payment_card_available=True,
    )
    with_signal = _mainline(
        roles=("effect_evidence", "activity_offer", "address_evidence"),
        appointment_active=True,
        payment_card_available=True,
        signals=("explicit_booking_request",),
    )

    assert "send_payment" in without_signal["allowed_next_sales_action_types"]
    assert "send_payment" in with_signal["allowed_next_sales_action_types"]
    assert without_signal["explicit_booking_request"] is False
    assert with_signal["explicit_booking_request"] is True
    assert "explicit_booking_request" in CURRENT_INTENT_CONTINUATION_SIGNALS


def test_explicit_booking_can_send_payment_before_appointment_record_exists() -> None:
    mainline = _mainline(
        roles=("effect_evidence", "activity_offer", "address_evidence"),
        appointment_active=False,
        payment_card_available=True,
        signals=("explicit_booking_request",),
    )

    assert mainline["next_missing_stage"] == "appointment"
    assert "send_payment" in mainline["allowed_next_sales_action_types"]
    _validate_mainline_sales_action(_state(mainline, action="send_payment"))


def test_declared_send_payment_requires_payment_collection_structure() -> None:
    mainline = _mainline(
        roles=("effect_evidence", "activity_offer", "address_evidence"),
        appointment_active=True,
        payment_card_available=True,
    )
    state = _state(mainline, action="send_payment")

    with pytest.raises(ValueError, match="payment_action_requires_payment_collection"):
        _validate_mainline_sales_action(
            state,
            messages=[{"type": "text", "content": "预约金入口发您。"}],
        )

    _validate_mainline_sales_action(
        state,
        messages=[{"type": "payment_collection", "content": {"amount": 10}}],
    )


def test_hard_stop_allows_repair_to_omit_sales_action() -> None:
    _validate_mainline_sales_action(
        _state(_mainline(), action="", customer_state="hard_stop_marketing")
    )


def test_normal_sales_turn_requires_a_declared_next_action() -> None:
    with pytest.raises(ValueError, match="next_sales_action_required"):
        _validate_mainline_sales_action(_state(_mainline(), action=""))
