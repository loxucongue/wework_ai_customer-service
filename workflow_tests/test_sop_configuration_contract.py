from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


def test_active_sop_configuration_contains_chat_gate_packs_only() -> None:
    config = _load_config()

    assert config["packs"]
    assert all(pack.get("scope") == "chat_gate" for pack in config["packs"])
    assert all(pack.get("scopes") == ["chat_gate"] for pack in config["packs"])
    assert not any(str(pack.get("id") or "").startswith("event_") for pack in config["packs"])


def test_chat_activity_quote_remains_available_to_normal_ai_reply_gate() -> None:
    activity = _pack(_load_config(), "s10_activity_intro")

    assert activity["enabled"] is True
    assert activity["scopes"] == ["chat_gate"]
    assert _image_urls(activity) == [ACTIVITY_AD_IMAGE]


def test_first_activity_intro_does_not_send_payment_card_in_same_turn() -> None:
    activity = _pack(_load_config(), "s10_activity_intro")

    assert "payment_collection" not in {
        message.get("type") for message in activity.get("reply_messages") or []
    }


def test_activity_intro_tail_does_not_ask_default_single_person_count() -> None:
    activity = _pack(_load_config(), "s10_activity_intro")
    visible_text = "\n".join(
        str((message.get("content") or {}).get("text") or "")
        for message in activity.get("reply_messages") or []
        if message.get("type") == "text"
    )

    for phrase in ["自己一位参加吗", "1位参加对吧", "几位参加", "按人数"]:
        assert phrase not in visible_text
    assert "10元预约金入口" in visible_text


def test_legacy_first_add_candidate_generation_is_empty_after_retirement() -> None:
    config = _load_config()

    candidates = first_add_candidate_packs(
        config,
        completed_sop_pack_ids=[],
        completed_sop_categories=[],
        delay_minutes=24 * 60,
        payment_state="unpaid",
        delivery_evidence={
            "event_at": "2026-08-03T02:00:00+00:00",
            "payment_card_last_sent_at": "",
        },
    )

    assert candidates == []


def test_active_configuration_has_no_event_scope_audit_error() -> None:
    audit = _load_config()["audit"]
    error_codes = {
        issue["code"]
        for issue in audit["issues"]
        if issue.get("severity") == "error"
    }

    assert "non_chat_gate_scope" not in error_codes
    assert "event_activity_quote_missing" not in error_codes
