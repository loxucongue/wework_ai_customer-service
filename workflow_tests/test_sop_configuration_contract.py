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


def test_activity_quote_uses_one_pack_for_chat_and_first_add_event() -> None:
    config = _load_config()
    activity = _pack(config, "s10_activity_intro")
    legacy = _pack(config, "event_s10_price_quote_60min")

    assert activity["enabled"] is True
    assert set(activity["scopes"]) == {"chat_gate", "event_first_add"}
    assert activity["delay_minutes"] == 60
    assert legacy["enabled"] is False

    candidates = first_add_candidate_packs(
        config,
        completed_sop_pack_ids=["s10_new_customer_opening", "event_s10_effect_warmup_30min"],
        completed_sop_categories=[],
        delay_minutes=60,
    )
    candidate_ids = [item["id"] for item in candidates]
    assert "s10_activity_intro" in candidate_ids
    assert "event_s10_price_quote_60min" not in candidate_ids


def test_activity_quote_send_once_key_is_shared_across_both_entrypoints() -> None:
    identity = {
        "corp_id": "ww943af61cd5d2afe4",
        "wechat": "CS001",
        "external_userid": "external-1",
        "customer_id": "customer-1",
    }

    assert chat_send_once_key(identity, "s10_activity_intro") == event_send_once_key(
        identity,
        "s10_activity_intro",
    )


def test_activity_quote_configuration_has_no_canonicalization_error() -> None:
    audit = _load_config()["audit"]
    error_codes = {
        issue["code"]
        for issue in audit["issues"]
        if issue.get("severity") == "error"
    }
    assert "shared_activity_quote_scope_missing" not in error_codes
    assert "legacy_activity_quote_enabled" not in error_codes
