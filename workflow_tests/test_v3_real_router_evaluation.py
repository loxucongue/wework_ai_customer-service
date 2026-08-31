from __future__ import annotations

from scripts.run_v3_real_router_evaluation import _evaluate_expected_annotation, _structural_issues


def test_router_evaluation_rejects_semantic_summaries_without_message_refs() -> None:
    issues = _structural_issues(
        {
            "status": "ok",
            "semantic_route": {
                "status": "ok",
                "current_intent": {"summary": "客户在问费用", "evidence_refs": []},
                "current_friction": {
                    "summary": "客户认为价格过高",
                    "evidence_refs": [],
                },
                "historical_unresolved_friction": {
                    "summary": "此前效果问题没有回答",
                    "evidence_refs": [],
                },
                "checkpoint": {"evidence_refs": []},
                "store_query": {"location_evidence_refs": []},
            },
        },
        {"current_message", "conv_001"},
    )

    assert issues == [
        "missing_current_intent_refs",
        "missing_current_friction_refs",
        "missing_historical_friction_refs",
    ]


def test_router_evaluation_accepts_real_refs_and_rejects_fabricated_refs() -> None:
    issues = _structural_issues(
        {
            "status": "ok",
            "semantic_route": {
                "status": "ok",
                "current_intent": {
                    "summary": "客户在问费用",
                    "evidence_refs": ["current_message"],
                },
                "current_friction": {"summary": "", "evidence_refs": []},
                "historical_unresolved_friction": {"summary": "", "evidence_refs": []},
                "checkpoint": {"evidence_refs": []},
                "store_query": {"location_evidence_refs": ["made_up_ref"]},
            },
        },
        {"current_message", "conv_001"},
    )

    assert issues == ["invalid_message_refs:made_up_ref"]


def test_router_evaluation_accepts_none_alongside_inquiry() -> None:
    item = {
        "expected_annotation": {
            "status": "technical_reviewed",
            "primary_checkpoint": "",
            "acceptable_primary_checkpoints": ["inquiry"],
            "acceptable_sequence_ids": [],
            "acceptable_step_ids": [],
            "acceptable_action_codes": [],
            "forbidden_sequence_ids": [],
            "sequence_required": False,
            "forbid_sequence": True,
            "store_query_required": False,
        },
        "actual": {
            "primary_checkpoint": "",
            "sequence_ids": [],
            "relevant_step_ids": [],
            "script_queries": [],
            "store_query": {"required": False},
            "sequences": [],
        },
        "runtime": {"structural_issues": []},
    }

    result = _evaluate_expected_annotation(item)

    assert result["checkpoint_pass"] is True
    assert result["overall_pass"] is True
