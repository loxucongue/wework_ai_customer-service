from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.graph.nodes.material_selection import _mainline_delivery_state  # noqa: E402
from app.graph.nodes.reply_admission import _validate_mainline_sales_action  # noqa: E402
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


def test_active_friction_and_paused_turn_cannot_advance_booking() -> None:
    mainline = _mainline(
        roles=("effect_evidence", "activity_offer", "address_evidence"),
        friction="explicit",
    )
    assert "invite_booking" not in mainline["allowed_next_sales_action_types"]
    with pytest.raises(ValueError, match="exceeds_delivered_mainline"):
        _validate_mainline_sales_action(_state(mainline, action="invite_booking"))

    permissive = _mainline(
        roles=("effect_evidence", "activity_offer", "address_evidence")
    )
    with pytest.raises(ValueError, match="paused_turn_cannot_advance"):
        _validate_mainline_sales_action(
            _state(permissive, action="invite_booking", customer_state="pause_current_turn")
        )


def test_payment_requires_real_action_signal_and_available_card() -> None:
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

    assert "send_payment" not in without_signal["allowed_next_sales_action_types"]
    assert "send_payment" in with_signal["allowed_next_sales_action_types"]
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


def test_hard_stop_allows_repair_to_omit_sales_action() -> None:
    _validate_mainline_sales_action(
        _state(_mainline(), action="", customer_state="hard_stop_marketing")
    )


def test_normal_sales_turn_requires_a_declared_next_action() -> None:
    with pytest.raises(ValueError, match="next_sales_action_required"):
        _validate_mainline_sales_action(_state(_mainline(), action=""))
