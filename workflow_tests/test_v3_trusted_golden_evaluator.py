from __future__ import annotations

from scripts.evaluate_v3_trusted_golden import evaluate


def test_v3_golden_evaluator_separates_gate_reply_delivery_and_forbidden_actions() -> None:
    golden = {
        "schema_version": "v3_trusted_golden_set_v1",
        "git_commit": "abc123",
        "cases": [
            {
                "case_id": "golden_v3_price",
                "category": "活动介绍与首次询价",
                "evaluation_partition": "holdout",
                "annotation": {
                    "required_gate_asset_ids": ["activity_offer"],
                    "acceptable_gate_asset_ids": ["activity_offer", "effect_evidence"],
                    "required_deliveries": ["activity_offer_image"],
                    "forbidden_actions": ["payment_collection"],
                    "quality_expectations": {
                        "current_question_solved": True,
                        "natural_advance": True,
                        "introduces_new_concern": False,
                        "incorrect_pause": False,
                        "incorrect_recovery": False,
                    },
                },
            }
        ],
    }
    results = {
        "git_commit": "def456",
        "results": [
            {
                "case_id": "golden_v3_price",
                "gate_candidate_ids": ["activity_offer", "unrelated_asset"],
                "selected_content_ids": ["activity_offer"],
                "delivered_asset_ids": ["activity_offer_image"],
                "reply_messages": [{"type": "payment_collection", "content": {"amount": 10}}],
                "critic": {"status": "pass"},
            }
        ],
    }

    report = evaluate(golden, results)
    case = report["cases"][0]

    assert report["evaluated_cases"] == 1
    assert report["knowledge_recall"] == 1.0
    assert report["gate_recall"] == report["knowledge_recall"]
    assert report["reply_adoption"] == 1.0
    assert report["delivery_completion"] == 1.0
    assert case["false_nomination"] == 0.5
    assert case["forbidden_action_hit"] == ["payment_collection"]
    assert report["first_inquiry_payment_card_rate"] == 1.0


def test_v3_golden_evaluator_reports_critic_holdout_metrics() -> None:
    golden = {
        "schema_version": "v3_trusted_golden_set_v1",
        "cases": [
            {
                "case_id": "ok",
                "evaluation_partition": "holdout",
                "annotation": {"quality_expectations": {"introduces_new_concern": False}},
            },
            {
                "case_id": "bad",
                "evaluation_partition": "holdout",
                "annotation": {"quality_expectations": {"introduces_new_concern": True}},
            },
        ],
    }
    results = {
        "results": [
            {
                "case_id": "ok",
                "critic": {"status": "pass"},
                "human_review": {"status": "reviewed", "verdict": "pass"},
            },
            {
                "case_id": "bad",
                "critic": {"status": "fail"},
                "human_review": {"status": "reviewed", "verdict": "fail"},
            },
        ]
    }

    report = evaluate(golden, results)

    assert report["critic"]["holdout"]["evaluated"] == 2
    assert report["critic"]["holdout"]["accuracy"] == 1.0
    assert report["critic"]["holdout"]["false_positive_rate"] == 0.0
