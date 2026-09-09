from __future__ import annotations

import asyncio
import base64

import pytest

from app.graph.nodes.reply_validation import _validate_parallel_reply_consistency
from app.graph.nodes.sales_fact_validation import validate_sales_price_fact_boundaries
from app.policies.business_rules import parallel_reply_business_rules_for_model
from app.services.material_fingerprint import (
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
        "第二次价格不能承诺还是268元，要以届时活动和门店确认为准。",
    ],
)
def test_valid_268_boundaries_are_allowed(reply: str) -> None:
    validate_sales_price_fact_boundaries([{"type": "text", "content": reply}])


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


def test_offer_model_facts_include_all_price_boundaries() -> None:
    offer = parallel_reply_business_rules_for_model()["AUTHORITATIVE FACTS"]["offer"]

    assert offer["new_customer_price"] == 268
    assert "脸颊两侧" in offer["face_area_price_rule"]
    assert "不得把268元表述为" in offer["face_full_operation_boundary"]
    assert "两个部位" in offer["multi_area_price_rule"]
    assert "第二次" in offer["repeat_visit_price_rule"]
