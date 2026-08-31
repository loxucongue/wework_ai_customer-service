from __future__ import annotations

from app.schemas import ChatResponse, ReplyMessage
from app.services.follow_knowledge_metadata import adopted_follow_knowledge_metadata
from app.services.workflow_compat import workflow_response_from_chat


def _state() -> dict:
    return {
        "request_context": {"interface_version": "v3"},
        "reply_messages": [{"type": "text", "content": {"text": "reply"}}],
        "reply_knowledge_use": {
            "sequence_id": "18",
            "step_id": "181",
            "selected_script_ids": ["D27", "D28"],
        },
        "sales_recall": {
            "sequence_candidates": [
                {
                    "sequence_id": "18",
                    "sequence_name": "Distance follow-up",
                    "checkpoint_code": "distance",
                    "checkpoint_name": "Distance",
                    "steps": [
                        {
                            "step_id": "181",
                            "sort_order": 1,
                            "action_code": "empathy",
                            "action_name": "Empathy",
                        }
                    ],
                }
            ],
            "candidates": [
                {
                    "script_id": "37",
                    "source_id": "D27",
                    "script_name": "Distance empathy",
                    "reference_text": "I understand the travel concern.",
                    "checkpoint_code": "distance",
                    "checkpoint_name": "Distance",
                    "checkpoint_type": {"id": 12, "name": "Distance"},
                    "checkpoint_tag": {"id": 36, "name": "Too far"},
                    "action_code": "empathy",
                    "action_name": "Empathy",
                },
                {"script_id": "38", "source_id": "D28"},
            ],
        },
    }


def test_callback_metadata_uses_first_actually_adopted_script() -> None:
    metadata = adopted_follow_knowledge_metadata(_state())

    assert metadata["followSequence"] == {
        "id": 18,
        "sequenceName": "Distance follow-up",
        "checkpointCode": "distance",
        "checkpointName": "Distance",
        "sortOrder": 1,
        "actionCode": "empathy",
        "actionName": "Empathy",
    }
    assert metadata["followScript"] == {
        "id": 37,
        "scriptCode": "D27",
        "scriptName": "Distance empathy",
        "bodyText": "I understand the travel concern.",
        "checkpointCode": "distance",
        "checkpointTypeId": 12,
        "checkpointTypeName": "Distance",
        "checkpointTagId": 36,
        "checkpointTagName": "Too far",
        "checkpointName": "Distance",
        "actionCode": "empathy",
        "actionName": "Empathy",
    }


def test_callback_metadata_accepts_canonical_platform_script_id() -> None:
    state = _state()
    state["reply_knowledge_use"]["selected_script_ids"] = ["37"]

    metadata = adopted_follow_knowledge_metadata(state)

    assert metadata["followScript"]["id"] == 37
    assert metadata["followScript"]["scriptCode"] == "D27"


def test_workflow_response_returns_follow_knowledge_beside_reply_messages() -> None:
    metadata = adopted_follow_knowledge_metadata(_state())
    response = ChatResponse(
        request_id="request-1",
        reply_messages=[ReplyMessage(type="text", order=1, content={"text": "reply"})],
        meta={"follow_knowledge_callback": metadata},
    )

    body = workflow_response_from_chat(response)

    assert body["data"]["followSequence"]["id"] == 18
    assert body["data"]["followScript"]["scriptCode"] == "D27"
    assert body["data"]["reply_messages"][0]["content"]["text"] == "reply"


def test_non_v3_or_unadopted_knowledge_is_not_reported() -> None:
    state = _state()
    state["request_context"]["interface_version"] = "v2"
    assert adopted_follow_knowledge_metadata(state) == {}

    state["request_context"]["interface_version"] = "v3"
    state["reply_knowledge_use"] = {}
    assert adopted_follow_knowledge_metadata(state) == {}


def test_empty_or_fallback_reply_does_not_report_adopted_knowledge() -> None:
    state = _state()
    state["reply_messages"] = []
    assert adopted_follow_knowledge_metadata(state) == {}

    state = _state()
    state["reply_source"] = "deterministic_empty_reply_fallback"
    assert adopted_follow_knowledge_metadata(state) == {}


def test_sequence_without_an_adopted_real_step_is_not_reported() -> None:
    state = _state()
    state["reply_knowledge_use"]["step_id"] = ""

    metadata = adopted_follow_knowledge_metadata(state)

    assert "followSequence" not in metadata
    assert metadata["followScript"]["scriptCode"] == "D27"
