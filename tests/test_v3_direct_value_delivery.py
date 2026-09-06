from __future__ import annotations

from ai_paths.app.graph.nodes.material_selection import parallel_reply_payload
from ai_paths.app.graph.nodes.reply_nodes import _materialize_selected_content_media
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
