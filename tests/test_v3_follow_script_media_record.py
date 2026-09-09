from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.chat_runtime import (  # noqa: E402
    _case_image_send_record,
    _record_sent_case_images,
    record_reply_memory,
)
from app.config import Settings  # noqa: E402
from app.graph.nodes.material_selection import parallel_reply_payload  # noqa: E402
from app.graph.nodes.derived_observations import build_derived_observations  # noqa: E402
from app.graph.nodes.reply_context import _sent_sop_like_categories  # noqa: E402
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model  # noqa: E402
from app.services.async_reply_delivery import AsyncReplyDeliveryFinalizer  # noqa: E402
from app.services.memory_store import CustomerMemoryStore  # noqa: E402
from app.services.v3_semantic_router_service import script_content_candidates  # noqa: E402


IMAGE_URL = "https://cdn.example.com/distance-case-310.jpg"


def _knowledge() -> dict:
    return {
        "candidates": [
            {
                "script_id": "310",
                "source_id": "distance-310",
                "script_name": "距离顾虑案例",
                "checkpoint_code": "distance",
                "checkpoint_name": "距离顾虑",
                "action_code": "act006",
                "action_name": "效果证明",
                "paragraphs": [
                    {
                        "paragraph_no": 1,
                        "messages": [
                            {"type": "text", "content": "不少客户也会专程过来。"},
                            {"type": "image", "url": IMAGE_URL},
                        ],
                    }
                ],
            }
        ]
    }


def _candidate() -> dict:
    candidate = script_content_candidates(_knowledge())[0]
    assert candidate["asset_role"] == "sales_reference"
    return candidate


def _state(*, selected: bool = True) -> dict:
    candidate = _candidate()
    return {
        "request_id": "request-follow-script-media",
        "request_context": {"interface_version": "v3"},
        "selected_content_ids": [candidate["content_id"]] if selected else [],
        "evidence_join": {"content_candidates": [candidate]},
        "trace": [],
    }


def test_adopted_follow_script_image_is_recorded_by_content_script_and_url(
    tmp_path: Path,
) -> None:
    state = _state()
    memory = CustomerMemoryStore(Settings().model_copy(update={"memory_dir": tmp_path / "memory"}))

    _record_sent_case_images(
        memory,
        state,
        customer_id="corp:wechat:external",
        reply_messages=[
            {"type": "text", "content": "给您看一个真实案例。"},
            {"type": "image", "content": IMAGE_URL},
        ],
    )

    record = state["case_image_send_record"]
    assert record["status"] == "recorded"
    assert record["image_urls"] == [IMAGE_URL]
    assert record["matched_content_ids"] == ["follow_script:distance-310:p1"]
    assert record["matched_script_ids"] == ["310"]
    assert record["matched_script_codes"] == ["distance-310"]
    assert record["asset_roles"] == ["sales_reference"]
    assert set(record["document_ids"]) == {
        "follow_script:distance-310:p1",
        "310",
        "distance-310",
    }

    history_events = memory.load("corp:wechat:external")["history_events"]
    summary = sent_message_summary_for_model({"history_events": history_events})
    delivery = summary["case_image_delivery"]
    assert delivery["sent_image_urls"] == [IMAGE_URL]
    assert set(delivery["recent_document_ids"]) == set(record["document_ids"])
    assert delivery["effect_evidence_sent"] is False
    assert "case_image_sent" not in summary

    next_turn = script_content_candidates(
        _knowledge(),
        sent_image_urls=delivery["sent_image_urls"],
    )[0]
    assert next_turn["delivery_status"] == "completed"
    assert next_turn["messages"] == []


def test_role_tagged_effect_asset_advances_effect_delivery_stage(tmp_path: Path) -> None:
    memory = CustomerMemoryStore(Settings().model_copy(update={"memory_dir": tmp_path / "memory"}))
    memory.record_case_images_sent(
        "corp:wechat:external",
        document_ids=["case-1"],
        image_urls=["https://cdn.example.com/case-1.jpg"],
        asset_roles=["effect_evidence"],
        request_id="effect-1",
        interface_version="v3",
    )

    summary = sent_message_summary_for_model(
        {"history_events": memory.load("corp:wechat:external")["history_events"]}
    )

    assert summary["case_image_sent"] is True
    assert summary["case_image_delivery"]["effect_evidence_sent"] is True


def test_legacy_case_image_event_without_roles_keeps_effect_semantics(tmp_path: Path) -> None:
    memory = CustomerMemoryStore(Settings().model_copy(update={"memory_dir": tmp_path / "memory"}))
    memory.record_case_images_sent(
        "corp:wechat:external",
        document_ids=["legacy-case-1"],
        image_urls=["https://cdn.example.com/legacy-case-1.jpg"],
        request_id="legacy-effect-1",
        interface_version="v3",
    )

    summary = sent_message_summary_for_model(
        {"history_events": memory.load("corp:wechat:external")["history_events"]}
    )

    assert summary["case_image_delivery"]["asset_roles"] == ["effect_evidence"]
    assert summary["case_image_sent"] is True


def test_explicit_sales_reference_role_overrides_generic_case_url() -> None:
    state = _state()
    state["fact_envelope"] = {
        "structured_facts": {
            "case_facts": [
                {"document_id": "generic-case", "image_url": IMAGE_URL}
            ]
        }
    }

    record = _case_image_send_record(
        state,
        [{"type": "image", "content": IMAGE_URL}],
    )

    assert record["asset_roles"] == ["sales_reference"]
    assert record["selected_effect_asset_ids"] == []


def test_cross_turn_summaries_preserve_case_image_roles() -> None:
    sales_reference_event = {
        "event_id": "sales-reference-1",
        "event_type": "case_image_sent",
        "event_time": "2026-09-09T10:00:00+08:00",
        "facts": {
            "image_urls": [IMAGE_URL],
            "asset_roles": ["sales_reference"],
        },
    }
    legacy_effect_event = {
        "event_id": "legacy-effect-1",
        "event_type": "case_image_sent",
        "event_time": "2026-09-09T09:00:00+08:00",
        "facts": {"image_urls": ["https://cdn.example.com/legacy-effect.jpg"]},
    }

    assert _sent_sop_like_categories(
        {"history_events": [sales_reference_event]},
        sent_message_summary={},
    ) == []
    assert _sent_sop_like_categories(
        {"history_events": [legacy_effect_event]},
        sent_message_summary={},
    ) == ["effect_case"]

    observations = build_derived_observations(
        conversation=[],
        history_events=[sales_reference_event, legacy_effect_event],
        current_message={},
    )
    roles = {
        item["source_refs"][0]: item["last_delivery_facts"]["asset_roles"]
        for item in observations["recent_asset_deliveries"]
    }
    assert roles["history_event:sales-reference-1"] == ["sales_reference"]
    assert roles["history_event:legacy-effect-1"] == ["effect_evidence"]


def test_text_activity_delivery_advances_stage_without_keyword_inference(tmp_path: Path) -> None:
    memory = CustomerMemoryStore(Settings().model_copy(update={"memory_dir": tmp_path / "memory"}))
    state = {
        "request_id": "activity-text-1",
        "sales_contact_key": "corp:wechat:external",
        "request_context": {"interface_version": "v3"},
        "evidence_join": {"content_candidates": []},
        "reply_sales_judgment": {
            "next_sales_action": {
                "type": "explain_activity",
                "target_stage": "activity_offer",
            }
        },
        "trace": [],
    }
    record_reply_memory(
        memory,
        final_state=state,
        reply_messages=[{"type": "text", "content": "本轮活动介绍"}],
    )

    summary = sent_message_summary_for_model(
        {"history_events": memory.load("corp:wechat:external")["history_events"]}
    )
    shared = {
        "conversation": [],
        "current_message": {"content": "在哪"},
        "authoritative_facts": {"sent_messages": summary},
    }
    mainline = parallel_reply_payload(
        {
            "evidence_join": {
                "shared_context": shared,
                "content_candidates": [],
                "sales_recall": {},
                "semantic_route": {},
            }
        }
    )["mainline_delivery_state"]

    assert summary["activity_offer_sent"] is True
    assert mainline["activity_offer_delivered"] is True


def test_activity_stage_requires_matching_visible_action_and_target(tmp_path: Path) -> None:
    memory = CustomerMemoryStore(Settings().model_copy(update={"memory_dir": tmp_path / "memory"}))
    base = {
        "sales_contact_key": "corp:wechat:external",
        "request_context": {"interface_version": "v3"},
        "evidence_join": {"content_candidates": []},
        "trace": [],
    }
    for request_id, action, target, messages in (
        (
            "wrong-target",
            "explain_activity",
            "effect_evidence",
            [{"type": "text", "content": "介绍活动"}],
        ),
        (
            "wrong-action",
            "deliver_value",
            "activity_offer",
            [{"type": "text", "content": "介绍活动"}],
        ),
        ("no-text", "explain_activity", "activity_offer", []),
    ):
        state = {
            **base,
            "request_id": request_id,
            "reply_sales_judgment": {
                "next_sales_action": {"type": action, "target_stage": target}
            },
        }
        record_reply_memory(memory, final_state=state, reply_messages=messages)

    summary = sent_message_summary_for_model(
        {"history_events": memory.load("corp:wechat:external")["history_events"]}
    )
    assert summary.get("activity_offer_sent", False) is False


def test_confirmed_async_delivery_records_declared_activity_stage(tmp_path: Path) -> None:
    class Repository:
        def add_assistant_message(self, **_kwargs):
            return None

    memory = CustomerMemoryStore(Settings().model_copy(update={"memory_dir": tmp_path / "memory"}))
    finalizer = AsyncReplyDeliveryFinalizer(Repository(), memory)  # type: ignore[arg-type]
    finalizer.finalize(
        {
            "status": "send_succeeded",
            "source_request_id": "recovery-activity-1",
            "conversation_id": "conversation-1",
            "reply_messages": [{"type": "text", "content": "这次活动包含项目体验和检测。"}],
            "source_context": {
                "memory_persist_allowed": True,
                "sales_contact_key": "corp:wechat:external",
                "sales_stage_record": {
                    "stage": "activity_offer",
                    "action_type": "explain_activity",
                },
            },
        }
    )

    summary = sent_message_summary_for_model(
        {"history_events": memory.load("corp:wechat:external")["history_events"]}
    )
    assert summary["activity_offer_sent"] is True


def test_unadopted_follow_script_image_is_not_recorded(tmp_path: Path) -> None:
    state = _state(selected=False)
    memory = CustomerMemoryStore(Settings().model_copy(update={"memory_dir": tmp_path / "memory"}))

    _record_sent_case_images(
        memory,
        state,
        customer_id="corp:wechat:external",
        reply_messages=[{"type": "image", "content": IMAGE_URL}],
    )

    record = state["case_image_send_record"]
    assert record["status"] == "skipped"
    assert record["document_ids"] == []
    assert record["image_urls"] == []
    assert record["unmatched_image_urls"] == [IMAGE_URL]
    assert memory.load("corp:wechat:external")["history_events"] == []


def test_explicit_empty_final_selection_does_not_use_stale_validation_selection() -> None:
    candidate = _candidate()
    state = {
        "selected_content_ids": [],
        "reply_selected_content_ids": [candidate["content_id"]],
        "evidence_join": {"content_candidates": [candidate]},
    }

    record = _case_image_send_record(
        state,
        [{"type": "image", "content": IMAGE_URL}],
    )

    assert record["document_ids"] == []
    assert record["image_urls"] == []
    assert record["unmatched_image_urls"] == [IMAGE_URL]
