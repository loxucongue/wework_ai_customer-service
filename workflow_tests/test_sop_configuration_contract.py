from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services.sop_event_service import _send_once_key as event_send_once_key
from app.services.sop_execution_service import _send_once_key as chat_send_once_key
from app.services.sop_execution_service import first_add_candidate_packs
from app.services.sop_reply_pack_service import SopReplyPackService


OPENING_EFFICACY_IMAGE = (
    "https://test.by4dev.4ba.cn/ai-paths/sop-media/20260720/"
    "b441a00bbf264ea0-s10-opening-efficacy.png"
)
ACTIVITY_AD_IMAGE = (
    "https://test.by4dev.4ba.cn/ai-paths/sop-media/20260702/"
    "d89bd3bcde50f4f6-1329752764320508_1782884693042278684_8BIuTnVvSC.png"
)


def _load_config() -> dict:
    return SopReplyPackService(
        SimpleNamespace(sop_reply_packs_path=Path("config/sop_reply_packs.json"))
    ).load()


def _pack(config: dict, pack_id: str) -> dict:
    return next(pack for pack in config["packs"] if pack.get("id") == pack_id)


def _image_urls(pack: dict) -> list[str]:
    return [
        str(message.get("content", {}).get("url") or "")
        for message in pack.get("reply_messages") or []
        if message.get("type") == "image"
    ]


def test_opening_efficacy_image_does_not_replace_activity_ad_or_case_media() -> None:
    config = _load_config()
    opening = _pack(config, "s10_new_customer_opening")
    activity = _pack(config, "s10_activity_intro")
    cases = _pack(config, "s10_need_and_case")

    assert _image_urls(opening) == [OPENING_EFFICACY_IMAGE]
    assert _image_urls(activity) == [ACTIVITY_AD_IMAGE]
    assert OPENING_EFFICACY_IMAGE not in _image_urls(activity)
    assert OPENING_EFFICACY_IMAGE not in _image_urls(cases)
    assert ACTIVITY_AD_IMAGE not in _image_urls(opening)


def test_activity_quote_uses_separate_chat_and_silent_event_packs() -> None:
    config = _load_config()
    activity = _pack(config, "s10_activity_intro")
    legacy = _pack(config, "event_s10_price_quote_60min")

    assert activity["enabled"] is True
    assert set(activity["scopes"]) == {"chat_gate"}
    assert activity["delay_minutes"] == 60
    assert legacy["enabled"] is True
    assert set(legacy["scopes"]) == {"event_first_add"}

    candidates = first_add_candidate_packs(
        config,
        completed_sop_pack_ids=["event_s10_store_prompt_5min", "event_s10_effect_warmup_30min"],
        completed_sop_categories=["store_prompt", "effect_case"],
        delay_minutes=60,
    )
    candidate_ids = [item["id"] for item in candidates]
    assert "s10_activity_intro" not in candidate_ids
    assert "event_s10_price_quote_60min" in candidate_ids


def test_activity_quote_send_once_key_is_shared_across_both_entrypoints() -> None:
    config = _load_config()
    chat_pack = _pack(config, "s10_activity_intro")
    event_pack = _pack(config, "event_s10_price_quote_60min")
    identity = {
        "corp_id": "ww943af61cd5d2afe4",
        "wechat": "CS001",
        "external_userid": "external-1",
        "customer_id": "customer-1",
    }

    assert chat_pack["send_once_group"] == event_pack["send_once_group"] == "activity_price_quote"
    assert chat_send_once_key(identity, chat_pack["send_once_group"]) == event_send_once_key(
        identity,
        event_pack["send_once_group"],
    )


def test_activity_quote_content_and_image_match_across_entrypoints() -> None:
    config = _load_config()
    chat_pack = _pack(config, "s10_activity_intro")
    event_pack = _pack(config, "event_s10_price_quote_60min")

    assert chat_pack["reply_messages"][0]["content"]["text"] == event_pack["reply_messages"][0]["content"]["text"]
    assert _image_urls(chat_pack) == _image_urls(event_pack) == [ACTIVITY_AD_IMAGE]


def test_chat_quote_completion_removes_silent_event_quote_candidate() -> None:
    config = _load_config()
    candidates = first_add_candidate_packs(
        config,
        completed_sop_pack_ids=["s10_activity_intro"],
        completed_sop_categories=["s10_activity_intro"],
        delay_minutes=60,
    )

    assert "event_s10_price_quote_60min" not in [item["id"] for item in candidates]


def test_activity_quote_configuration_has_no_canonicalization_error() -> None:
    audit = _load_config()["audit"]
    error_codes = {
        issue["code"]
        for issue in audit["issues"]
        if issue.get("severity") == "error"
    }
    assert "shared_activity_quote_scope_missing" not in error_codes
    assert "event_activity_quote_missing" not in error_codes


def test_silent_event_deposit_waits_for_quote_and_minimum_gap() -> None:
    config = _load_config()
    completed_ids = [
        "event_s10_store_prompt_5min",
        "event_s10_effect_warmup_30min",
        "event_s10_price_quote_60min",
    ]
    completed_categories = ["store_prompt", "effect_case", "price_quote"]
    before_gap = first_add_candidate_packs(
        config,
        completed_sop_pack_ids=completed_ids,
        completed_sop_categories=completed_categories,
        delay_minutes=70,
        payment_state="unpaid",
        delivery_evidence={
            "event_at": "2026-07-24T02:10:00+00:00",
            "category_last_sent_at": {"price_quote": "2026-07-24T02:01:00+00:00"},
        },
    )
    after_gap = first_add_candidate_packs(
        config,
        completed_sop_pack_ids=completed_ids,
        completed_sop_categories=completed_categories,
        delay_minutes=70,
        payment_state="unpaid",
        delivery_evidence={
            "event_at": "2026-07-24T02:11:00+00:00",
            "category_last_sent_at": {"price_quote": "2026-07-24T02:01:00+00:00"},
        },
    )

    assert "event_s10_deposit_push_70min" not in [item["id"] for item in before_gap]
    assert "event_s10_deposit_push_70min" in [item["id"] for item in after_gap]


def test_unpaid_followups_use_payment_card_delivery_time() -> None:
    config = _load_config()
    completed_ids = [
        "event_s10_store_prompt_5min",
        "event_s10_effect_warmup_30min",
        "event_s10_price_quote_60min",
        "event_s10_deposit_push_70min",
    ]
    completed_categories = ["store_prompt", "effect_case", "price_quote", "deposit_push"]
    before_hour = first_add_candidate_packs(
        config,
        completed_sop_pack_ids=completed_ids,
        completed_sop_categories=completed_categories,
        delay_minutes=180,
        payment_state="unpaid",
        delivery_evidence={
            "event_at": "2026-07-24T03:00:00+00:00",
            "payment_card_last_sent_at": "2026-07-24T02:01:00+00:00",
        },
    )
    at_hour = first_add_candidate_packs(
        config,
        completed_sop_pack_ids=completed_ids,
        completed_sop_categories=completed_categories,
        delay_minutes=180,
        payment_state="unpaid",
        delivery_evidence={
            "event_at": "2026-07-24T03:01:00+00:00",
            "payment_card_last_sent_at": "2026-07-24T02:01:00+00:00",
        },
    )

    assert "event_s10_unpaid_effect_1h" not in [item["id"] for item in before_hour]
    assert "event_s10_unpaid_effect_1h" in [item["id"] for item in at_hour]
