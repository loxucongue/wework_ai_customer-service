from __future__ import annotations

from app.config import Settings
from app.graph.nodes.reply_nodes import _reply_metadata_from_model_call
from app.graph.nodes.v2_derived_observations import build_v2_derived_observations
from app.services.memory_store import CustomerMemoryStore


def _state() -> dict:
    return {
        "sales_recall": {
            "sequence_candidates": [
                {
                    "sequence_id": "seq-distance-1",
                    "sequence_name": "距离卡点跟进",
                    "checkpoint_code": "distance",
                    "steps": [
                        {
                            "step_id": "step-case",
                            "action_code": "case",
                        }
                    ],
                }
            ],
            "candidates": [
                {
                    "source_id": "D02",
                    "script_code": "D02",
                    "sequence_links": [
                        {
                            "sequence_id": "seq-distance-1",
                            "step_id": "step-case",
                            "action_code": "case",
                        }
                    ],
                },
                {
                    "source_id": "D03",
                    "script_code": "D03",
                    "sequence_links": [],
                },
            ],
        }
    }


def _model_call(*, selected_content_ids: list[str], knowledge_use: dict | None = None) -> dict:
    return {
        "validated_json_output": {
            "reply_messages": [{"type": "text", "content": "先看一组真实案例。"}],
            "used_fact_refs": ["current_message"],
            "selected_content_ids": selected_content_ids,
            "content_decisions": [],
            "action": "offer",
            "action_reason": "用案例承接距离顾虑",
            "sales_judgment": {},
            "knowledge_use": knowledge_use or {},
            "payment_assessment": {},
            "deposit_evidence": {},
            "safety_assessment": {},
            "party_size_assessment": {},
            "commit_actions": [],
        }
    }


def test_reply_records_only_actually_adopted_script_and_sequence() -> None:
    metadata = _reply_metadata_from_model_call(
        _model_call(
            selected_content_ids=["follow_script:D02"],
            knowledge_use={
                "sequence_id": "seq-distance-1",
                "step_id": "step-case",
                "reason": "采用效果案例步骤",
            },
        ),
        state=_state(),
    )

    assert metadata["knowledge_use"] == {
        "sequence_id": "seq-distance-1",
        "sequence_name": "距离卡点跟进",
        "step_id": "step-case",
        "checkpoint_code": "distance",
        "action_code": "case",
        "selected_script_ids": ["D02"],
        "reason": "采用效果案例步骤",
        "authority": "reply_selected_reference_not_customer_fact",
    }


def test_queried_but_not_adopted_script_is_not_recorded() -> None:
    metadata = _reply_metadata_from_model_call(
        _model_call(selected_content_ids=[], knowledge_use={}),
        state=_state(),
    )

    assert metadata["knowledge_use"]["selected_script_ids"] == []
    assert metadata["knowledge_use"]["sequence_id"] == ""


def test_unique_script_link_can_restore_provenance_without_semantic_inference() -> None:
    metadata = _reply_metadata_from_model_call(
        _model_call(selected_content_ids=["follow_script:D02"], knowledge_use={}),
        state=_state(),
    )

    assert metadata["knowledge_use"]["sequence_id"] == "seq-distance-1"
    assert metadata["knowledge_use"]["step_id"] == "step-case"
    assert metadata["knowledge_use"]["selected_script_ids"] == ["D02"]


def test_usage_is_persisted_idempotently_and_returned_as_low_authority_observation(tmp_path) -> None:
    store = CustomerMemoryStore(Settings(_env_file=None, memory_dir=tmp_path))
    usage = {
        "sequence_id": "seq-distance-1",
        "sequence_name": "距离卡点跟进",
        "step_id": "step-case",
        "checkpoint_code": "distance",
        "action_code": "case",
        "selected_script_ids": ["D02"],
        "reason": "采用效果案例步骤",
    }

    first = store.record_follow_knowledge_usage(
        "contact-key",
        request_id="request-1",
        knowledge_use=usage,
        interface_version="v3",
    )
    second = store.record_follow_knowledge_usage(
        "contact-key",
        request_id="request-1",
        knowledge_use=usage,
        interface_version="v3",
    )
    memory = store.load("contact-key")
    events = [
        item
        for item in memory["history_events"]
        if item.get("event_type") == "v3_follow_knowledge_usage"
    ]
    observations = build_v2_derived_observations(
        conversation=[],
        history_events=memory["history_events"],
        current_message={},
        interface_version="v3",
    )

    assert first["status"] == "recorded"
    assert second["status"] == "recorded"
    assert len(events) == 1
    latest = observations["latest_follow_knowledge_usage"]
    assert latest["sequence_id"] == "seq-distance-1"
    assert latest["step_id"] == "step-case"
    assert latest["selected_script_ids"] == ["D02"]
    assert latest["authority"] == "prior_reply_reference_selection_not_customer_fact"


def test_empty_usage_does_not_write_customer_profile(tmp_path) -> None:
    store = CustomerMemoryStore(Settings(_env_file=None, memory_dir=tmp_path))

    result = store.record_follow_knowledge_usage(
        "contact-key",
        request_id="request-2",
        knowledge_use={},
        interface_version="v3",
    )

    assert result == {"status": "skipped", "reason": "no_adopted_follow_knowledge"}
    assert store.load("contact-key").get("history_events", []) == []
