from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

from app.policies.business_rules import load_business_rules
import pytest

from app.services.sop_execution_service import SopExecutionService, first_add_candidate_packs
from app.services.sop_reply_pack_service import SopReplyPackService
from app.prompts.sop_chat_gate import PARALLEL_CONTENT_GATE_SYSTEM_PROMPT


OPENING_EFFICACY_IMAGE = (
    "https://test.by4dev.4ba.cn/uploads/images/"
    "1786332826302_f0f91788-3af9-445f-b9e6-cf3b00787765.png"
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
        SimpleNamespace(
            sop_reply_packs_path=Path("config/sop_reply_packs.json"),
            sop_reply_packs_overlay_path=Path("config/v2_sop_asset_overlay.json"),
        )
    ).load()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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
    assert opening["enabled"] is True
    assert opening["parallel_candidate_enabled"] is False


def test_v2_overlay_changes_only_code_owned_candidate_metadata(tmp_path: Path) -> None:
    base_path = tmp_path / "sop_reply_packs.json"
    overlay_path = tmp_path / "v2_sop_asset_overlay.json"
    base = json.loads(Path("config/sop_reply_packs.json").read_text(encoding="utf-8"))
    _write_json(base_path, base)
    _write_json(
        overlay_path,
        {
            "version": 1,
            "packs": [
                {
                    "id": "s10_activity_intro",
                    "parallel_candidate_enabled": False,
                    "asset_role": "activity_offer_v2",
                }
            ],
        },
    )

    without_overlay = SopReplyPackService(
        SimpleNamespace(sop_reply_packs_path=base_path)
    ).load()
    with_overlay = SopReplyPackService(
        SimpleNamespace(
            sop_reply_packs_path=base_path,
            sop_reply_packs_overlay_path=overlay_path,
        )
    ).load()

    base_activity = _pack(without_overlay, "s10_activity_intro")
    v2_activity = _pack(with_overlay, "s10_activity_intro")
    assert v2_activity["parallel_candidate_enabled"] is False
    assert v2_activity["asset_role"] == "activity_offer_v2"
    assert v2_activity["reply_messages"] == base_activity["reply_messages"]
    assert v2_activity["purpose"] == base_activity["purpose"]


def test_v2_effect_asset_can_finish_with_evidence_without_forced_diagnosis() -> None:
    config = _load_config()
    effect = _pack(config, "s10_need_and_case")

    assert effect["render_strategy"] == "adaptable_evidence_first"
    assert any("自然收住" in item for item in effect["reasoning_moves"])
    assert any("脸上还是手上" in item for item in effect["anti_patterns"])
    assert any("条件式预告" in item for item in effect["reasoning_moves"])
    assert any("助手动作" in item for item in effect["anti_patterns"])
    assert [item["type"] for item in effect["reply_messages"]] == ["text", "image", "image"]


def test_v2_activity_asset_can_support_adjacent_value_without_fixed_stage_mapping() -> None:
    activity = _pack(_load_config(), "s10_activity_intro")

    assert activity["render_strategy"] == "adaptable"
    assert "相邻决策价值" in activity["customer_uncertainty"]
    assert any("相邻新价值" in item for item in activity["reasoning_moves"])
    assert any("完整上下文" in item for item in activity["anti_patterns"])
    assert any("许可式问题" in item for item in activity["anti_patterns"])
    assert any("预约金卡" in item for item in activity["reasoning_moves"])
    assert activity["reply_messages"][1]["type"] == "image"


def test_parallel_gate_adjacent_value_must_cross_decision_dimension() -> None:
    assert "不同决策维度的新价值" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "不能标成相邻 `supporting`" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "B 只能再考虑活动、地址或真实卡点中的一个" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT


@pytest.mark.parametrize("field", ["reply_messages", "purpose", "enabled", "order"])
def test_v2_overlay_rejects_business_content_fields(tmp_path: Path, field: str) -> None:
    base_path = tmp_path / "sop_reply_packs.json"
    overlay_path = tmp_path / "v2_sop_asset_overlay.json"
    _write_json(
        base_path,
        json.loads(Path("config/sop_reply_packs.json").read_text(encoding="utf-8")),
    )
    _write_json(
        overlay_path,
        {"packs": [{"id": "s10_activity_intro", field: "forbidden"}]},
    )

    service = SopReplyPackService(
        SimpleNamespace(
            sop_reply_packs_path=base_path,
            sop_reply_packs_overlay_path=overlay_path,
        )
    )
    with pytest.raises(ValueError, match="business-content fields"):
        service.load()


def test_v2_overlay_rejects_unknown_pack_id(tmp_path: Path) -> None:
    base_path = tmp_path / "sop_reply_packs.json"
    overlay_path = tmp_path / "v2_sop_asset_overlay.json"
    _write_json(
        base_path,
        json.loads(Path("config/sop_reply_packs.json").read_text(encoding="utf-8")),
    )
    _write_json(overlay_path, {"packs": [{"id": "missing_pack", "asset_role": "x"}]})

    service = SopReplyPackService(
        SimpleNamespace(
            sop_reply_packs_path=base_path,
            sop_reply_packs_overlay_path=overlay_path,
        )
    )
    with pytest.raises(ValueError, match="unknown base pack"):
        service.load()


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


def test_activity_intro_image_matches_business_rule_fact_source() -> None:
    activity = _pack(_load_config(), "s10_activity_intro")
    offer = load_business_rules().get("offer") or {}

    assert offer["activity_intro_image_url"] == _image_urls(activity)[0]


def test_activity_intro_is_offer_only_and_deposit_close_owns_payment_card() -> None:
    activity = _pack(_load_config(), "s10_activity_intro")
    deposit = _pack(_load_config(), "s10_deposit_close")
    activity_messages = activity.get("reply_messages") or []
    deposit_messages = deposit.get("reply_messages") or []

    assert activity["asset_role"] == "activity_offer"
    assert deposit["asset_role"] == "deposit_close"
    assert deposit["requires_prior_asset_roles"] == ["activity_offer"]
    assert [message.get("type") for message in activity_messages] == ["text", "image", "text"]
    assert all(message.get("type") != "payment_collection" for message in activity_messages)
    assert "完成线上活动登记后" in activity_messages[-1]["content"]["text"]
    assert [message.get("type") for message in deposit_messages] == [
        "text",
        "image",
        "payment_collection",
    ]
    assert deposit_messages[-1]["content"] == {"amount": 10, "remark": ""}
    assert "未做或不满意可退" in deposit_messages[0]["content"]["text"]


def test_effect_store_and_deposit_sop_packs_are_configured() -> None:
    config = _load_config()
    cases = _pack(config, "s10_need_and_case")
    store_prompt = _pack(config, "s10_store_prompt")
    deposit = _pack(config, "s10_deposit_close")

    assert _image_urls(cases) == EFFECT_CASE_IMAGES
    case_text = " ".join(
        str(message.get("content", {}).get("text") or "")
        for message in cases["reply_messages"]
        if message.get("type") == "text"
    )
    assert "真实对比" in case_text
    assert "具体改善程度以实际情况为准" in case_text
    assert "不伤皮肤" not in case_text
    assert store_prompt["enabled"] is True
    assert store_prompt["selection_constraints"] == {
        "forbidden_when_authoritative_facts_present": ["location_card"]
    }
    assert store_prompt["parallel_candidate_enabled"] is False
    assert deposit["enabled"] is True
    assert _image_urls(deposit) == [DEPOSIT_LIGHT_IMAGE]
    assert [message.get("type") for message in deposit["reply_messages"]] == [
        "text",
        "image",
        "payment_collection",
    ]


def test_static_store_prompt_is_not_exposed_to_normal_reply_gate() -> None:
    service = object.__new__(SopExecutionService)
    service.sop_reply_pack_service = SopReplyPackService(
        SimpleNamespace(
            sop_reply_packs_path=Path("config/sop_reply_packs.json"),
            sop_reply_packs_overlay_path=Path("config/v2_sop_asset_overlay.json"),
        )
    )

    catalog = service.reply_chain_content_catalog()

    assert "s10_store_prompt" not in {
        str(item.get("content_id") or "")
        for item in catalog.get("sop_packs") or []
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
    assert "268元" in visible_text
    assert "10元预约金" not in visible_text
    assert "到店抵扣" not in visible_text


def test_static_sop_copy_does_not_contain_absolute_effect_or_safety_claims() -> None:
    config = _load_config()
    visible_text = "\n".join(
        str((message.get("content") or {}).get("text") or "")
        for pack in config["packs"]
        for message in pack.get("reply_messages") or []
        if message.get("type") == "text"
    )

    forbidden_phrases = [
        "公认最先进",
        "最先进最有效",
        "做完不伤害皮肤",
        "不伤害皮肤",
        "随做随走不影响出门上班",
        "随做随走，不影响上班出门",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in visible_text


def test_platform_triggered_first_add_uses_only_explicit_proactive_assets() -> None:
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

    candidate_ids = {item["id"] for item in candidates}
    assert candidate_ids == {
        "s10_need_and_case",
        "s10_activity_intro",
        "s10_store_prompt",
        "s10_objection_resolution",
        "s10_deposit_close",
    }
    assert "s10_new_customer_opening" not in candidate_ids
    assert all(item["proactive_candidate_enabled"] for item in candidates)


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
