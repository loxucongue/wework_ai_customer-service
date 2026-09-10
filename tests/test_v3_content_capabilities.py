from __future__ import annotations

import copy

import pytest

from app.graph.nodes.material_selection import parallel_reply_payload
from app.graph.nodes.reply_contract import _dedupe_content_candidates, _v3_available_assets_for_turn
from app.graph.nodes.reply_nodes import _materialize_selected_content_media
from app.graph.nodes.reply_validation import (
    _validate_parallel_selected_content_delivery,
    completed_parallel_selected_content_ids,
)
from app.services.content_capabilities import content_only_candidate


@pytest.mark.parametrize(
    "role",
    ["activity_offer", "activity_intro", "sales_reference", "effect_evidence", "supporting_content", "deposit_close"],
)
@pytest.mark.parametrize("action", ["payment_collection", "store_address", "booking_confirmed", "create_work_order"])
def test_all_content_roles_exclude_action_payloads_without_changing_copy(role, action):
    text = {"type": "text", "content": "这个活动给你留着，到时候我帮您安排。"}
    image = {"type": "image", "content": "https://example.invalid/a.png"}
    card = {"type": action, "content": {"amount": 10, "store_id": "demo-store"}}
    raw = {"content_id": "asset", "asset_role": role, "commit_actions": [{"action": "create_work_order"}]}
    for field in ("messages", "media", "reference_messages", "required_structured_media", "reply_messages"):
        raw[field] = [text, image, card]
    before = copy.deepcopy(raw)
    normalized = content_only_candidate(raw)
    assert raw == before
    assert "commit_actions" not in normalized
    assert all(
        normalized[key] == [text, image] for key in raw if isinstance(raw[key], list) and key != "commit_actions"
    )
    state = {"evidence_join": {"content_candidates": [raw]}, "reply_selected_content_ids": ["asset"]}
    payload = parallel_reply_payload(state)
    assert payload["allowed_selected_content_ids"] == ["asset"]
    _validate_parallel_selected_content_delivery([text, image], state)
    assert completed_parallel_selected_content_ids([text, image], state, ["asset"]) == ["asset"]
    rendered, adopted = _materialize_selected_content_media([text], state)
    assert [m["type"] for m in rendered] == ["text", "image"]
    assert adopted == ["asset"]


def test_action_only_candidate_cannot_be_adopted_or_completed():
    candidate = {"content_id": "activity", "messages": [{"type": "payment_collection", "content": {"amount": 10}}]}
    state = {"evidence_join": {"content_candidates": [candidate]}}
    assert parallel_reply_payload(state)["allowed_selected_content_ids"] == []
    assert completed_parallel_selected_content_ids([], state, ["activity"]) == []
    assert _dedupe_content_candidates([candidate])[0]["messages"] == []


def test_activity_candidate_keeps_image_but_not_payment_card():
    candidate = {
        "content_id": "activity",
        "asset_role": "activity_offer",
        "messages": [
            {"type": "image", "content": "https://example.invalid/offer.png"},
            {"type": "payment_collection", "content": {"amount": 10}},
        ],
    }
    candidates = _v3_available_assets_for_turn({}, [candidate], sent_summary={})
    assert [m["type"] for m in candidates[0]["messages"]] == ["image"]
