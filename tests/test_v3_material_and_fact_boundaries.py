from __future__ import annotations

import asyncio
import base64

import pytest

from app.graph.nodes.reply_admission import validate_model_led_reply_admission
from app.graph.nodes.reply_nodes import (
    _parallel_generic_reply_repair_messages,
    _repair_policy_skeleton,
    _reply_repair_hint,
)
from app.graph.nodes.reply_validation import _validate_parallel_reply_consistency
from app.graph.nodes.sales_fact_validation import (
    validate_customer_visible_identity_boundaries,
    validate_sales_price_fact_boundaries,
)
from app.policies.business_rules import parallel_reply_business_rules_for_model
from app.services.material_fingerprint import (
    _FINGERPRINT_CACHE,
    _FINGERPRINT_FAILURE_CACHE,
    _FINGERPRINT_INFLIGHT,
    diversify_material_candidates,
    fingerprint_media_bytes,
    media_fingerprints_match,
)
from app.services.store_fact_followup import build_store_fact_followup
from app.services.v3_semantic_router_service import (
    _apply_missing_store_location_shortcut,
    _rank_script_groups,
    _store_tool_plan,
)


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2cS8AAAAASUVORK5CYII="
)


def _image_candidate(content_id: str, url: str, *, relevance: str = "available") -> dict:
    message = {"type": "image", "content": {"url": url}}
    return {
        "content_id": content_id,
        "name": content_id,
        "purpose": "效果证明",
        "asset_role": "effect_evidence",
        "relevance": relevance,
        "delivery_status": "available",
        "messages": [message],
        "media": [message],
    }


def test_media_fingerprint_has_sha_and_visual_fallback() -> None:
    value = fingerprint_media_bytes(PNG_BYTES)

    assert len(value["sha256"]) == 64
    assert value["mode"] in {"sha256_only", "sha256_and_dhash"}
    assert media_fingerprints_match(value, fingerprint_media_bytes(PNG_BYTES)) is True
    assert media_fingerprints_match(
        {"sha256": "left", "perceptual_hash": "0000000000000000"},
        {"sha256": "right", "perceptual_hash": "0000000000000003"},
    ) is True


def test_same_image_on_different_urls_is_removed() -> None:
    async def fetcher(url: str) -> bytes:
        assert url in {"https://cdn.example/a.png", "https://cdn.example/b.png"}
        return PNG_BYTES

    result = asyncio.run(
        diversify_material_candidates(
            [
                _image_candidate("case-a", "https://cdn.example/a.png", relevance="direct"),
                _image_candidate("case-b", "https://cdn.example/b.png", relevance="direct"),
            ],
            fetcher=fetcher,
            total_budget_seconds=1,
        )
    )

    assert [item["content_id"] for item in result["candidates"]] == ["case-a"]
    assert result["audit"]["duplicate_media_removed"] == 1


def test_timed_out_fingerprint_task_releases_single_flight_and_caches_late_result() -> None:
    url = "https://cdn.example/late.png"
    _FINGERPRINT_CACHE.pop(url, None)
    _FINGERPRINT_FAILURE_CACHE.pop(url, None)
    _FINGERPRINT_INFLIGHT.pop(url, None)

    async def fetcher(value: str) -> bytes:
        assert value == url
        await asyncio.sleep(0.08)
        return PNG_BYTES

    async def scenario() -> None:
        first = await diversify_material_candidates(
            [_image_candidate("late", url, relevance="direct")],
            fetcher=fetcher,
            total_budget_seconds=0.05,
        )
        assert first["audit"]["fallback_used"] is True
        assert url in _FINGERPRINT_INFLIGHT
        await asyncio.sleep(0.06)
        await asyncio.sleep(0)
        assert url not in _FINGERPRINT_INFLIGHT
        assert url in _FINGERPRINT_CACHE

    asyncio.run(scenario())


def test_recent_material_only_loses_to_fresh_candidate_in_same_semantic_tier() -> None:
    async def fetcher(url: str) -> bytes:
        return url.encode("utf-8")

    result = asyncio.run(
        diversify_material_candidates(
            [
                _image_candidate("recent", "https://cdn.example/recent.png", relevance="supporting"),
                _image_candidate("fresh", "https://cdn.example/fresh.png", relevance="supporting"),
                _image_candidate("direct", "https://cdn.example/direct.png", relevance="direct"),
            ],
            recent_material_ids=["recent"],
            fetcher=fetcher,
            total_budget_seconds=1,
        )
    )

    assert [item["content_id"] for item in result["candidates"]] == [
        "direct",
        "fresh",
        "recent",
    ]


def test_recent_script_and_high_similarity_are_penalized_not_deleted() -> None:
    candidates = [
        {
            "id": 1,
            "script_code": "old",
            "script_name": "效果解释",
            "checkpoint_code": "effect",
            "action_code": "act001",
            "paragraphs": [
                {"paragraph_no": 1, "messages": [{"type": "text", "content": "我们的技术和效果都很成熟"}]}
            ],
        },
        {
            "id": 2,
            "script_code": "fresh",
            "script_name": "效果解释",
            "checkpoint_code": "effect",
            "action_code": "act002",
            "paragraphs": [
                {"paragraph_no": 1, "messages": [{"type": "text", "content": "先给您看一组真实案例更直观"}]}
            ],
        },
    ]

    selected = _rank_script_groups(
        candidates,
        query_text="想看效果",
        max_groups=1,
        recent_script_ids=["old"],
        recent_assistant_texts=["我们的技术和效果都很成熟"],
    )

    assert selected[0]["script_code"] == "fresh"
    assert _rank_script_groups(
        [candidates[0]],
        query_text="想看效果",
        max_groups=1,
        recent_script_ids=["old"],
        recent_assistant_texts=["我们的技术和效果都很成熟"],
    )[0]["script_code"] == "old"


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("这次活动268元就是操作全脸。", "offer_268_full_face_claim_conflict"),
        ("左脸268元，右脸也是268元。", "offer_bilateral_cheek_split_price_conflict"),
        ("脸和手一起做只要268元。", "offer_face_hand_total_268_conflict"),
        ("脸部和手部都是268元。", "offer_face_hand_price_scope_ambiguous"),
        ("周年庆活动是268元，针对脸部和手部的斑点都适用。", "offer_face_hand_price_scope_ambiguous"),
        ("268元就能改善脸部和手部的斑点。", "offer_face_hand_price_scope_ambiguous"),
        ("第二次再做也是268元。", "offer_repeat_visit_268_unverified"),
    ],
)
def test_invalid_268_claims_are_rejected(reply: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        validate_sales_price_fact_boundaries([{"type": "text", "content": reply}])


@pytest.mark.parametrize(
    "reply",
    [
        "268元不是无差别操作全脸，是按脸部这个部位的实际斑点做针对性改善。",
        "脸颊两侧不会按左脸、右脸拆开收费，脸部一个部位是268元。",
        "脸和手不是总共268元，两个部位需要分别计算。",
        "脸部和手部单独做都是268元，一个268只对应一个部位。",
        "脸部活动价268元，手部需要单独计算。",
        "第二次价格不能承诺还是268元，要以届时活动和门店确认为准。",
    ],
)
def test_valid_268_boundaries_are_allowed(reply: str) -> None:
    validate_sales_price_fact_boundaries([{"type": "text", "content": reply}])


@pytest.mark.parametrize(
    "reply",
    [
        "我不是机器人哦，是真人客服。",
        "我这边不是AI，您放心。",
        "我是真人销售，刚才在忙。",
        "我是人工顾问，刚才在忙。",
        "我是真人在回复。",
        "我不是ai，您放心。",
        "这边不是机器人，是人工客服。",
    ],
)
def test_false_human_identity_claims_are_rejected(reply: str) -> None:
    with pytest.raises(ValueError, match="customer_visible_false_human_identity_claim"):
        validate_customer_visible_identity_boundaries([{"type": "text", "content": reply}])


def test_natural_acknowledgement_does_not_require_an_identity_claim() -> None:
    validate_customer_visible_identity_boundaries(
        [{"type": "text", "content": "在的，刚才消息连着过来，我现在接着给您说明。"}]
    )
    validate_customer_visible_identity_boundaries(
        [{"type": "text", "content": "我是小贝，这边负责线上智能接待。"}]
    )
    validate_customer_visible_identity_boundaries(
        [{"type": "text", "content": "到店后这边是真人老师给您操作。"}]
    )


def test_parallel_validation_runs_hours_and_price_boundaries() -> None:
    state = {
        "request_context": {"interface_version": "v3"},
        "evidence_join": {
            "schema_version": "v3_evidence_join_v1",
            "structured_facts": {},
            "normalized_tool_facts": {"structured_facts": {}},
            "content_candidates": [],
        },
    }
    with pytest.raises(ValueError, match="business_hours_fact_required"):
        _validate_parallel_reply_consistency(
            [{"type": "text", "content": "门店营业时间是9:00-20:00。"}],
            state,
        )
    with pytest.raises(ValueError, match="offer_268_full_face_claim_conflict"):
        _validate_parallel_reply_consistency(
            [{"type": "text", "content": "268元就是做全脸。"}],
            state,
        )
    with pytest.raises(ValueError, match="customer_visible_placeholder_fact"):
        _validate_parallel_reply_consistency(
            [{"type": "text", "content": "我们公司在XX市XX区XX路XX号。"}],
            state,
        )
    with pytest.raises(ValueError, match="case_image_structure_required"):
        _validate_parallel_reply_consistency(
            [{"type": "text", "content": "手部效果图我这边有，我发您参考下。"}],
            state,
        )


@pytest.mark.parametrize(
    ("reply", "violation"),
    [
        ("我们公司在XX市，您告诉我城市我再查。", "customer_visible_placeholder_fact"),
        ("手部效果图我这边有，我发您参考下。", "case_image_structure_required"),
        ("有的，我找一张手部斑点改善的效果图给您参考。", "case_image_structure_required"),
        ("268元脸部和手部都一起做。", "offer_face_hand_total_268_conflict"),
        ("门店营业时间是9:00-20:00。", "business_hours_fact_required"),
        ("我不是机器人哦，是真人客服。", "customer_visible_false_human_identity_claim"),
    ],
)
def test_model_led_admission_enforces_customer_visible_fact_boundaries(
    reply: str,
    violation: str,
) -> None:
    state = {
        "request_context": {"interface_version": "v3"},
        "evidence_join": {
            "schema_version": "v3_evidence_join_v1",
            "structured_facts": {},
            "normalized_tool_facts": {"structured_facts": {}},
            "content_candidates": [],
        },
        "reply_selected_content_ids": [],
    }

    with pytest.raises(ValueError, match=violation):
        validate_model_led_reply_admission(
            [{"type": "text", "content": reply}],
            state,
        )


@pytest.mark.parametrize(
    "reply",
    [
        "效果这块我跟您讲清楚，我这边先给您发活动价。",
        "我这边先给您发门店地址，效果您前面已经了解了。",
        "这个位置不能直接导航过去，需要先确认。",
    ],
)
def test_delivery_promise_checks_do_not_block_unrelated_or_negated_text(reply: str) -> None:
    state = {
        "request_context": {"interface_version": "v3"},
        "evidence_join": {
            "schema_version": "v3_evidence_join_v1",
            "structured_facts": {},
            "normalized_tool_facts": {"structured_facts": {}},
            "content_candidates": [],
        },
        "reply_selected_content_ids": [],
    }

    validate_model_led_reply_admission(
        [{"type": "text", "content": reply}],
        state,
    )


def test_repair_hints_make_placeholder_and_mainline_corrections_explicit() -> None:
    placeholder = _reply_repair_hint("customer_visible_placeholder_fact")
    mainline = _reply_repair_hint(
        "next_sales_action_exceeds_delivered_mainline:invite_booking:effect_evidence"
    )
    media = _reply_repair_hint(
        "case_image_structure_required_when_reply_promises_delivery"
    )
    body_area_price = _reply_repair_hint("offer_face_hand_price_scope_ambiguous")
    combined_price = _reply_repair_hint("offer_face_hand_total_268_conflict")
    identity = _reply_repair_hint("customer_visible_false_human_identity_claim")
    paused = _reply_repair_hint("paused_turn_cannot_advance_transaction")
    invalid_store = _reply_repair_hint("invalid_parallel_reply_message_content:2:store_address")

    assert "XX市" in placeholder and "所在城市" in placeholder
    assert "allowed_next_sales_action_types" in mainline
    assert "删除预约时间" in mainline
    assert "allowed_selected_content_ids" in media
    assert "手部单独做是268元活动价" in body_area_price
    assert "一个268元只对应一个部位" in combined_price
    assert "删除这类身份断言" in identity
    assert "不能邀约、催时间或推进付款" in paused
    assert "不得从历史订单、旧门店卡或旧城市恢复门店话题" in invalid_store


def test_paused_turn_repair_drops_invalid_sales_action_and_visible_draft() -> None:
    invalid_text = "您周末过来吧，我先帮您预约，预约金10元。"
    repaired_messages = _parallel_generic_reply_repair_messages(
        [{"role": "user", "content": "当前客户消息：还是太远了"}],
        ValueError("reply_admission_violations::paused_turn_cannot_advance_transaction"),
        previous_payload={
            "reply_messages": [{"type": "text", "content": invalid_text}],
            "sales_judgment": {"next_sales_action": {"type": "invite_booking"}},
            "policy_decision": {"closing_decision": {"customer_state": "pause_current_turn"}},
        },
        validation_context={
            "mainline_delivery_state": {
                "next_missing_stage": "appointment",
                "allowed_next_sales_action_types": ["deliver_value", "keep_open", "invite_booking"],
            }
        },
    )

    assistant_payload = repaired_messages[-2]["content"]
    repair_contract = repaired_messages[-1]["content"]
    assert invalid_text not in assistant_payload
    assert "sales_judgment" not in assistant_payload
    assert "不能邀约、催时间或推进付款" in repair_contract


def test_every_visible_rewrite_rebuilds_sales_judgment_with_visible_text() -> None:
    repaired_messages = _parallel_generic_reply_repair_messages(
        [{"role": "user", "content": "当前客户消息：我要了解的是斑点，不是抗衰"}],
        ValueError("reply_admission_violations::offer_face_hand_price_scope_ambiguous"),
        previous_payload={
            "reply_messages": [{"type": "text", "content": "脸部和手部都按268元。"}],
            "sales_judgment": {"next_sales_action": {"type": "explain_activity"}},
            "policy_decision": {"realtime_intent": {"type": "fact_inquiry"}},
        },
        validation_context={
            "mainline_delivery_state": {
                "next_missing_stage": "activity_offer",
                "allowed_next_sales_action_types": ["explain_activity", "keep_open"],
            }
        },
    )

    assistant_payload = repaired_messages[-2]["content"]
    assert "reply_messages" not in assistant_payload
    assert "sales_judgment" not in assistant_payload
    assert "policy_decision" in assistant_payload


def test_visible_identity_repair_removes_stale_policy_prose_and_lists_hard_exclusions() -> None:
    repaired_messages = _parallel_generic_reply_repair_messages(
        [{"role": "user", "content": "当前客户消息：你发我看看啊，你是机器人吗"}],
        ValueError(
            "reply_admission_violations::customer_visible_placeholder_fact;;"
            "customer_visible_false_human_identity_claim"
        ),
        previous_payload={
            "reply_messages": [{"type": "text", "content": "我是真人客服，XX市门店发您。"}],
            "sales_judgment": {"next_sales_action": {"type": "send_store"}},
            "policy_decision": {
                "primary_task": {
                    "type": "answer_current_question",
                    "goal": "继续旧厦门门店话题",
                    "basis": ["旧门店"],
                },
                "realtime_intent": {"type": "fact_inquiry", "confidence": "high"},
                "emotion_decision": {"label": "impatient", "pressure": "low"},
                "closing_decision": {
                    "action": "none",
                    "customer_state": "continue_sales",
                    "trigger": "none",
                },
            },
        },
        validation_context={
            "mainline_delivery_state": {
                "next_missing_stage": "effect_evidence",
                "allowed_next_sales_action_types": ["deliver_value", "send_effect_material"],
            }
        },
    )

    previous = repaired_messages[-2]["content"]
    contract = repaired_messages[-1]["content"]
    assert "继续旧厦门门店话题" not in previous
    assert "旧门店" not in previous
    assert "\"type\":\"answer_current_question\"" in previous
    assert "我不是机器人" in contract
    assert "XX市" in contract


def test_repair_policy_skeleton_retains_only_enums_and_ids() -> None:
    value = _repair_policy_skeleton(
        {
            "primary_task": {"type": "answer_current_question", "goal": "旧话题", "basis": ["x"]},
            "realtime_intent": {"type": "fact_inquiry", "confidence": "high"},
            "emotion_decision": {"label": "neutral", "pressure": "normal", "reason": "旧说明"},
            "closing_decision": {
                "action": "none",
                "sequence_key": "none",
                "customer_state": "continue_sales",
                "reason": "旧节点说明",
            },
        }
    )

    assert value["primary_task"] == {"type": "answer_current_question"}
    assert "reason" not in value["emotion_decision"]
    assert "reason" not in value["closing_decision"]


def test_store_address_promise_without_card_is_an_admission_violation() -> None:
    from app.graph.nodes.reply_admission import validate_model_led_reply_admission

    state = {
        "evidence_join": {"schema_version": "v3_evidence_join_v1"},
        "fact_envelope": {
            "structured_facts": {
                "store_resolution_fact": {
                    "status": "search_incomplete",
                    "delivery_mode": "none",
                    "delivery_store_ids": [],
                }
            }
        },
    }
    with pytest.raises(ValueError, match="store_address_text_without_card"):
        validate_model_led_reply_admission(
            [{"type": "text", "content": "您稍等，我马上把济南门店地址发您。"}],
            state,
        )

    hint = _reply_repair_hint("store_address_text_without_card")
    assert "search_incomplete" in hint
    assert "旧门店卡" in hint


def test_invalid_store_structure_repair_drops_stale_visible_draft_without_current_contract() -> None:
    invalid_text = "您之前问的济南门店是厦门二店，地址我发给您。"
    repaired_messages = _parallel_generic_reply_repair_messages(
        [{"role": "user", "content": "当前客户消息：多少钱？"}],
        ValueError("invalid_parallel_reply_message_content:2:store_address"),
        previous_payload={
            "reply_messages": [
                {"type": "text", "content": invalid_text},
                {"type": "store_address", "content": "厦门二店"},
            ],
            "sales_judgment": {"next_sales_action": {"type": "deliver_value"}},
        },
        validation_context={
            "structured_delivery_options": {"store_address": {}},
            "mainline_delivery_state": {
                "next_missing_stage": "effect_evidence",
                "allowed_next_sales_action_types": ["deliver_value", "send_effect_material"],
            },
        },
    )

    assistant_payload = repaired_messages[-2]["content"]
    repair_contract = repaired_messages[-1]["content"]
    assert invalid_text not in assistant_payload
    assert "reply_messages" not in assistant_payload
    assert "不得从历史订单、旧门店卡或旧城市恢复门店话题" in repair_contract


def test_identity_repair_drops_invalid_visible_draft_and_restores_mainline_contract() -> None:
    invalid_text = "我不是机器人，是真人客服。您周末过来吗？"
    repaired_messages = _parallel_generic_reply_repair_messages(
        [{"role": "user", "content": "当前客户消息：你是机器人吗"}],
        ValueError(
            "reply_admission_violations::customer_visible_false_human_identity_claim;;"
            "next_sales_action_exceeds_delivered_mainline:invite_booking:effect_evidence"
        ),
        previous_payload={
            "reply_messages": [{"type": "text", "content": invalid_text}],
            "sales_judgment": {"next_sales_action": {"type": "invite_booking"}},
        },
        validation_context={
            "mainline_delivery_state": {
                "next_missing_stage": "effect_evidence",
                "allowed_next_sales_action_types": ["deliver_value", "send_effect_material"],
            }
        },
    )

    assistant_payload = repaired_messages[-2]["content"]
    repair_contract = repaired_messages[-1]["content"]
    assert invalid_text not in assistant_payload
    assert "sales_judgment" not in assistant_payload
    assert '"next_missing_stage":"effect_evidence"' in repair_contract
    assert '"allowed_next_sales_action_types":["deliver_value","send_effect_material"]' in repair_contract


def test_mainline_repair_drops_invalid_visible_draft_and_requires_scalar_action() -> None:
    invalid_text = "您大概工作日还是周末到店？我帮您预约登记。"
    repaired_messages = _parallel_generic_reply_repair_messages(
        [{"role": "user", "content": "当前客户消息：有时间"}],
        ValueError(
            "reply_admission_violations::"
            "next_sales_action_exceeds_delivered_mainline:invite_booking:effect_evidence"
        ),
        previous_payload={
            "reply_messages": [{"type": "text", "content": invalid_text}],
            "sales_judgment": {
                "next_sales_action": {"type": "invite_booking"},
            },
            "policy_decision": {"realtime_intent": {"type": "transaction_progress"}},
        },
        validation_context={
            "mainline_delivery_state": {
                "next_missing_stage": "effect_evidence",
                "allowed_next_sales_action_types": ["deliver_value", "send_effect_material"],
            }
        },
    )

    assistant_payload = repaired_messages[-2]["content"]
    repair_contract = repaired_messages[-1]["content"]
    assert invalid_text not in assistant_payload
    assert "sales_judgment" not in assistant_payload
    assert "单个字符串" in repair_contract
    assert '"allowed_next_sales_action_types":["deliver_value","send_effect_material"]' in repair_contract
    assert '"next_missing_stage":"effect_evidence"' in repair_contract
    assert "不能只改动作标签" in repair_contract
    assert "工作日/周末" in repair_contract


def test_missing_store_location_skips_parser_and_requests_region() -> None:
    route = {
        "store_query": {
            "required": True,
            "purpose": "store_search",
            "destination_hint": "",
            "location_evidence_refs": [],
        }
    }

    output = _apply_missing_store_location_shortcut(route, shared_context={})
    plan = _store_tool_plan(output)

    assert output["store_query"]["skip_store_parser"] is True
    assert output["store_query"]["next_action"] == "ask_city_or_district"
    assert plan["decision"] == "need_customer_input"
    assert plan["tool_calls"] == []


def test_store_location_shortcut_keeps_sourced_destination_and_known_store_detail() -> None:
    sourced = {
        "store_query": {
            "required": True,
            "purpose": "store_search",
            "destination_hint": "厦门市",
            "location_evidence_refs": ["current_message"],
        }
    }
    detail = {
        "store_query": {
            "required": True,
            "purpose": "hours",
            "destination_hint": "",
            "location_evidence_refs": [],
        }
    }

    assert _apply_missing_store_location_shortcut(sourced, shared_context={}) == sourced
    assert _apply_missing_store_location_shortcut(detail, shared_context={}) == detail


def test_store_fact_followup_never_fabricates_missing_hours() -> None:
    output = build_store_fact_followup(
        store_resolution_fact={"status": "send_single", "delivery_store_ids": ["101"]},
        store_facts=[
            {
                "store_id": "101",
                "store_name": "厦门店",
                "address": "厦门市思明区示例路1号",
                "floor": "3楼",
                "room": "301",
            }
        ],
        requested_detail_kind="hours",
        authoritative_paid=True,
    )

    assert output["unique_store_delivery"]["status"] == "available"
    assert output["requested_detail"] == {
        "kind": "hours",
        "status": "missing",
        "facts": {},
        "missing_fact_keys": ["business_hours"],
    }
    assert "business_hours" not in output["post_payment_arrival_guidance"]["facts"]
    assert output["post_payment_arrival_guidance"]["facts"]["floor"] == "3楼"
    assert output["internal_followup"] == {
        "task_type": "store_fact_followup",
        "status": "pending",
        "store_id": "101",
        "detail_kind": "hours",
        "missing_fact_keys": ["business_hours"],
        "customer_send_authorized": False,
    }


def test_paid_customer_needs_private_facts_before_arrival_guidance_is_available() -> None:
    output = build_store_fact_followup(
        store_resolution_fact={"status": "send_single", "delivery_store_ids": ["101"]},
        store_facts=[
            {
                "store_id": "101",
                "store_name": "厦门店",
                "address": "厦门市思明区示例路1号",
                "business_hours": "09:00-20:00",
            }
        ],
        requested_detail_kind="arrival_guidance",
        authoritative_paid=True,
    )

    assert output["requested_detail"]["status"] == "missing"
    assert output["post_payment_arrival_guidance"] == {
        "status": "unavailable",
        "store_id": "",
        "facts": {},
        "reason": "private_arrival_guidance_facts_required",
    }
    assert output["internal_followup"]["status"] == "pending"


def test_offer_model_facts_include_all_price_boundaries() -> None:
    offer = parallel_reply_business_rules_for_model()["AUTHORITATIVE FACTS"]["offer"]

    assert offer["new_customer_price"] == 268
    assert "脸颊两侧" in offer["face_area_price_rule"]
    assert "不得把268元表述为" in offer["face_full_operation_boundary"]
    assert "两个部位" in offer["multi_area_price_rule"]
    assert "第二次" in offer["repeat_visit_price_rule"]
