from __future__ import annotations

from ai_paths.app.graph.nodes.material_selection import parallel_reply_payload
from ai_paths.app.graph.nodes.reply_admission import validate_model_led_reply_admission
from ai_paths.app.graph.nodes.reply_nodes import (
    _hard_pause_from_policy,
    _link_adopted_script_media,
    _materialize_selected_content_media,
)
from ai_paths.app.graph.nodes.semantic_evidence import _sent_case_image_urls
from ai_paths.app.services.v3_semantic_router_service import script_content_candidates


IMAGE_URL = "https://example.com/effect.png"
VIDEO_URL = "https://example.com/case.mp4"


def _knowledge(*, include_video: bool = False, text_only: bool = False) -> dict:
    messages = [{"type": "text", "content": "用效果价值处理距离顾虑"}]
    if not text_only:
        messages.append({"type": "image", "url": IMAGE_URL})
    if include_video:
        messages.append({"type": "video", "url": VIDEO_URL})
    return {
        "candidates": [
            {
                "source_id": "187",
                "script_id": "187",
                "script_name": "侧面烘托",
                "checkpoint_name": "距离卡点",
                "action_name": "效果案例",
                "paragraphs": [{"paragraph_no": 1, "messages": messages}],
            }
        ]
    }


def _state(candidates: list[dict]) -> dict:
    shared = {
        "schema_version": "shared_context_v2",
        "conversation": [],
        "current_message": {"content": "不行，太远了"},
        "authoritative_facts": {},
    }
    return {
        "shared_context": shared,
        "evidence_join": {
            "schema_version": "deterministic_evidence_join_v1",
            "shared_context": shared,
            "content_candidates": candidates,
            "sales_recall": _knowledge(),
            "semantic_route": {},
            "tool_facts": {},
        },
        "sales_recall": _knowledge(),
        "request_context": {"interface_version": "v3"},
    }


def test_script_media_candidate_is_available_and_text_only_script_is_not_selectable() -> None:
    media_candidate = script_content_candidates(_knowledge())[0]
    text_candidate = script_content_candidates(_knowledge(text_only=True))[0]
    state = _state([media_candidate, text_candidate])

    payload = parallel_reply_payload(state)

    assert media_candidate["delivery_status"] == "available"
    assert text_candidate["delivery_status"] == "reference_only"
    assert payload["allowed_selected_content_ids"] == ["follow_script:187:p1"]


def test_already_sent_script_image_is_removed_from_delivery_candidates() -> None:
    candidate = script_content_candidates(
        _knowledge(),
        sent_image_urls=[IMAGE_URL],
    )[0]
    state = _state([candidate])

    assert candidate["messages"] == []
    assert candidate["delivery_status"] == "completed"
    assert candidate["delivery_observation"]["sent_count"] == 1
    assert parallel_reply_payload(state)["allowed_selected_content_ids"] == []


def test_sent_image_is_removed_but_unsent_video_remains_available() -> None:
    candidate = script_content_candidates(
        _knowledge(include_video=True),
        sent_image_urls=[IMAGE_URL],
    )[0]

    assert candidate["messages"] == [{"type": "video", "content": VIDEO_URL}]
    assert candidate["delivery_status"] == "available"


def test_sent_case_urls_are_read_from_shared_authoritative_summary() -> None:
    state = {
        "shared_context": {
            "authoritative_facts": {
                "sent_messages": {
                    "case_image_delivery": {
                        "sent_image_urls": [IMAGE_URL, "", IMAGE_URL],
                    }
                }
            }
        }
    }

    assert _sent_case_image_urls(state) == [IMAGE_URL]


def test_mainline_delivery_uses_append_only_sent_message_facts() -> None:
    shared = {
        "schema_version": "shared_context_v2",
        "conversation": [],
        "current_message": {"content": "可以停车吗"},
        "authoritative_facts": {
            "sent_messages": {
                "case_image_sent": True,
                "activity_intro_image_sent": True,
                "store_address_delivery": {
                    "batch_confidence": "high",
                    "request_id": "prior-store-request",
                    "latest_batch_store_ids": ["306"],
                },
            }
        },
    }
    state = {
        "evidence_join": {
            "schema_version": "deterministic_evidence_join_v1",
            "shared_context": shared,
            "content_candidates": [],
            "sales_recall": {},
            "semantic_route": {},
        }
    }

    mainline = parallel_reply_payload(state)["mainline_delivery_state"]

    assert mainline["effect_evidence_delivered"] is True
    assert mainline["activity_offer_delivered"] is True
    assert mainline["store_address_delivered"] is True
    assert mainline["next_missing_stage"] == "appointment"


def test_mainline_delivery_exposes_first_missing_stage_without_forcing_action() -> None:
    state = {
        "evidence_join": {
            "schema_version": "deterministic_evidence_join_v1",
            "shared_context": {
                "schema_version": "shared_context_v2",
                "conversation": [],
                "current_message": {"content": "多少钱？"},
                "authoritative_facts": {
                    "sent_messages": {
                        "case_image_sent": True,
                        "activity_intro_image_sent": False,
                    }
                },
            },
            "content_candidates": [],
            "sales_recall": {},
            "semantic_route": {},
        }
    }

    mainline = parallel_reply_payload(state)["mainline_delivery_state"]

    assert mainline["next_missing_stage"] == "activity_offer"
    assert "不是强制销售动作" in mainline["meaning"]


def test_selected_script_media_is_appended_after_customer_visible_text() -> None:
    candidate = script_content_candidates(_knowledge())[0]
    state = _state([candidate])
    state["reply_selected_content_ids"] = ["follow_script:187:p1"]

    messages, materialized = _materialize_selected_content_media(
        [{"type": "text", "order": 1, "content": "我给您看个真实改善对比。"}],
        state,
    )

    assert materialized == ["follow_script:187:p1"]
    assert messages == [
        {"type": "text", "order": 1, "content": "我给您看个真实改善对比。"},
        {"type": "image", "content": IMAGE_URL, "order": 2},
    ]


def test_adopted_script_automatically_links_its_own_deliverable_media() -> None:
    candidate = script_content_candidates(_knowledge())[0]
    state = _state([candidate])
    payload = {
        "selected_content_ids": [],
        "knowledge_use": {"script_id": "187"},
        "policy_decision": {
            "realtime_intent": {"type": "blocker_expression"},
            "emotion_decision": {"flow_action": "keep"},
        },
        "safety_assessment": {"status": "none"},
    }

    linked = _link_adopted_script_media(payload, state)

    assert linked == "follow_script:187:p1"
    assert payload["selected_content_ids"] == ["follow_script:187:p1"]


def test_adopted_script_media_is_not_linked_across_safety_stop() -> None:
    candidate = script_content_candidates(_knowledge())[0]
    state = _state([candidate])
    payload = {
        "selected_content_ids": [],
        "knowledge_use": {"script_id": "187"},
        "policy_decision": {
            "realtime_intent": {"type": "explicit_exit"},
            "emotion_decision": {"flow_action": "stop_marketing"},
        },
        "safety_assessment": {"status": "explicit_reject"},
    }

    assert _link_adopted_script_media(payload, state) == ""
    assert payload["selected_content_ids"] == []


def test_distance_script_hold_language_keeps_direct_effect_media_delivery() -> None:
    knowledge = _knowledge(include_video=True)
    knowledge["candidates"][0]["source_id"] = "225"
    knowledge["candidates"][0]["script_id"] = "225"
    candidates = script_content_candidates(knowledge)
    state = _state(candidates)
    payload = {
        "selected_content_ids": [],
        "knowledge_use": {"script_id": "225"},
        "policy_decision": {
            "realtime_intent": {"type": "blocker_expression"},
            "emotion_decision": {"flow_action": "keep"},
        },
        "safety_assessment": {"status": "none"},
    }

    linked = _link_adopted_script_media(payload, state)
    state["reply_selected_content_ids"] = payload["selected_content_ids"]
    messages, materialized = _materialize_selected_content_media(
        [
            {
                "type": "text",
                "order": 1,
                "content": "那没关系呀，我们不少客户专程过来，主要还是看重技术和效果。我先帮您把活动名额留着。",
            }
        ],
        state,
    )

    assert linked == "follow_script:225:p1"
    assert materialized == ["follow_script:225:p1"]
    assert [item["type"] for item in messages] == ["text", "image", "video"]
    validate_model_led_reply_admission(messages, state)


def test_impatient_is_not_a_hard_marketing_stop_and_does_not_block_requested_media() -> None:
    candidate = script_content_candidates(_knowledge())[0]
    state = _state([candidate])
    policy = {
        "realtime_intent": {"type": "fact_inquiry"},
        "emotion_decision": {
            "label": "impatient",
            "confidence": "high",
            "flow_action": "pause_marketing_turn",
            "evidence_refs": ["current_message"],
        },
    }
    payload = {
        "selected_content_ids": [],
        "knowledge_use": {"script_id": "187"},
        "policy_decision": policy,
        "safety_assessment": {"status": "none"},
    }

    assert _hard_pause_from_policy(policy) is False
    assert _link_adopted_script_media(payload, state) == "follow_script:187:p1"
