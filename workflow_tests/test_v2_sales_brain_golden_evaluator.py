from __future__ import annotations

import json
from pathlib import Path

from scripts.evaluate_v2_sales_brain_golden import annotation_agreement, selection_metrics


ROOT = Path(__file__).resolve().parents[1]


def test_golden_manifest_has_pending_dual_annotations_and_minimum_coverage() -> None:
    manifest = json.loads(
        (ROOT / "workflow_tests/fixtures/v2_sales_brain_golden_manifest_v1.json").read_text(encoding="utf-8")
    )

    assert 30 <= len(manifest["cases"]) <= 50
    assert len({item["id"] for item in manifest["cases"]}) == len(manifest["cases"])
    assert all(item["annotator_a"] is None and item["annotator_b"] is None for item in manifest["cases"])
    assert annotation_agreement(manifest)["ready_for_critic_calibration"] is False


def test_layered_metrics_separate_gate_reply_and_delivery_failures() -> None:
    annotation = {
        "acceptable_actions": ["advance"],
        "required_asset_ids": ["activity"],
        "acceptable_asset_ids": ["case"],
        "forbidden_actions": [],
        "quality_flags": {},
    }
    manifest = {
        "cases": [
            {"id": "gate_miss", "annotator_a": annotation, "annotator_b": annotation},
            {"id": "reply_miss", "annotator_a": annotation, "annotator_b": annotation},
            {"id": "delivery_miss", "annotator_a": annotation, "annotator_b": annotation},
        ]
    }
    results = {
        "results": [
            {"scenario_id": "gate_miss", "content_selection_metrics": {"nominated_ids": [], "adopted_ids": [], "delivered_ids": []}},
            {"scenario_id": "reply_miss", "content_selection_metrics": {"nominated_ids": ["activity"], "adopted_ids": [], "delivered_ids": []}},
            {"scenario_id": "delivery_miss", "content_selection_metrics": {"nominated_ids": ["activity"], "adopted_ids": ["activity"], "delivered_ids": []}},
        ]
    }

    rows = {item["id"]: item for item in selection_metrics(manifest, results)["cases"]}
    assert rows["gate_miss"]["gate_recall"] == 0
    assert rows["reply_miss"]["gate_recall"] == 1
    assert rows["reply_miss"]["reply_adoption"] == 0
    assert rows["delivery_miss"]["reply_adoption"] == 1
    assert rows["delivery_miss"]["delivery_completion"] == 0
