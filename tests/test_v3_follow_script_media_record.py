from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.chat_runtime import _case_image_send_record, _record_sent_case_images  # noqa: E402
from app.config import Settings  # noqa: E402
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model  # noqa: E402
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

    next_turn = script_content_candidates(
        _knowledge(),
        sent_image_urls=delivery["sent_image_urls"],
    )[0]
    assert next_turn["delivery_status"] == "completed"
    assert next_turn["messages"] == []


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
