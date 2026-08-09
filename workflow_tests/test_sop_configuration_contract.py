from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

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
EFFECT_CASE_IMAGES = [
    "https://test.by4dev.4ba.cn/ai-paths/sop-media/20260806/effect_click_url-90f975480879.png",
    "https://test.by4dev.4ba.cn/ai-paths/sop-media/20260806/effect_second-de0488d149ea.png",
]
DEPOSIT_LIGHT_IMAGE = (
    "https://test.by4dev.4ba.cn/ai-paths/sop-media/20260806/"
    "deposit_light-d61147a81539.png"
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


def test_activity_intro_includes_deposit_card_after_refund_rule_text() -> None:
    activity = _pack(_load_config(), "s10_activity_intro")
    messages = activity.get("reply_messages") or []

    assert [message.get("type") for message in messages] == [
        "text",
        "image",
        "text",
        "payment_collection",
    ]
    assert messages[-1]["content"] == {"amount": 10, "remark": ""}
    assert "未做或不满意可退" in messages[-2]["content"]["text"]
    assert "实际按付款记录核对" in messages[-2]["content"]["text"]


def test_objection_resolution_is_explanation_material_without_payment_card() -> None:
    objection = _pack(_load_config(), "s10_objection_resolution")
    messages = objection.get("reply_messages") or []
    texts = "\n".join(str(message.get("content", {}).get("text") or "") for message in messages)

    assert objection["enabled"] is True
    assert all(message.get("type") == "text" for message in messages)
    assert "payment_collection" not in {message.get("type") for message in messages}
    assert "再付258" in texts
    assert "到店在付款" not in texts
    assert "登记记下来" not in texts


def test_effect_store_and_deposit_sop_packs_are_configured() -> None:
    config = _load_config()
    cases = _pack(config, "s10_need_and_case")
    store_prompt = _pack(config, "s10_store_prompt")
    deposit = _pack(config, "s10_deposit_close")

    assert _image_urls(cases) == EFFECT_CASE_IMAGES
    assert store_prompt["enabled"] is True
    assert store_prompt["reply_messages"][0]["content"]["text"] == "亲，您是在那个省份那个城市呢？我给您匹配最近的店铺。"
    assert deposit["enabled"] is True
    assert _image_urls(deposit) == [DEPOSIT_LIGHT_IMAGE]
    assert [message.get("type") for message in deposit["reply_messages"]] == [
        "text",
        "image",
        "payment_collection",
    ]


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


def test_save_rejects_incomplete_refund_policy_with_pack_and_message_position(tmp_path: Path) -> None:
    service = SopReplyPackService(SimpleNamespace(sop_reply_packs_path=tmp_path / "sop_reply_packs.json"))
    payload = _load_config()
    deposit = _pack(payload, "s10_deposit_close")
    deposit["reply_messages"][0]["content"]["text"] = "10元预约金到店抵扣，未做或不满意可退。"

    with pytest.raises(ValueError) as exc_info:
        service.save(payload)

    message = str(exc_info.value)
    assert "s10_deposit_close 第 1 条" in message
    assert "实际按付款记录核对" in message
