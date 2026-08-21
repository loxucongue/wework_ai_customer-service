from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "workflow_tests" / "fixtures" / "v3_trusted_golden_set_v1.json"
RUNTIME_ROOT = ROOT / "ai_paths" / "app"


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_v3_trusted_golden_set_is_approved_and_partitioned() -> None:
    payload = _load_fixture()
    cases = payload["cases"]

    assert payload["status"] == "approved_for_offline_evaluation"
    assert len(cases) == 50
    assert len({item["case_id"] for item in cases}) == 50
    assert all(item["review"]["status"] == "approved" for item in cases)
    assert sum(item["evaluation_partition"] == "calibration" for item in cases) == 15
    assert sum(item["evaluation_partition"] == "holdout" for item in cases) == 35


def test_v3_trusted_golden_set_preserves_sources_and_annotation_contract() -> None:
    payload = _load_fixture()
    cases = payload["cases"]

    assert Counter(item["source_kind"] for item in cases) == {
        "real_replay": 30,
        "fixture": 10,
        "counterfactual": 10,
    }
    assert payload["appointment_blocker_source_audit"] == {
        "source_record_count": 104,
        "prohibited_count": 34,
        "asset_pending_review_count": 79,
        "raw_replies_are_golden_answers": False,
    }
    required_annotation_fields = {
        "customer_goal",
        "must_answer_points",
        "acceptable_postures",
        "required_gate_asset_ids",
        "acceptable_gate_asset_ids",
        "required_deliveries",
        "forbidden_actions",
        "forbidden_claims",
        "quality_expectations",
        "reference_reply_direction",
        "reference_reply_examples",
    }
    for item in cases:
        annotation = item["annotation"]
        assert required_annotation_fields <= set(annotation)
        assert annotation["must_answer_points"]
        assert 1 <= len(annotation["reference_reply_examples"]) <= 2


def test_v3_trusted_golden_set_is_valid_utf8_without_mojibake() -> None:
    raw = FIXTURE.read_text(encoding="utf-8")

    assert "\ufeff" not in raw
    assert "\ufffd" not in raw
    assert "????" not in raw
    for marker in ("Ã", "Â", "æ˜Žç¡®", "å®Œæˆ"):
        assert marker not in raw


def test_v3_trusted_golden_set_cannot_become_runtime_prompt_data() -> None:
    payload = _load_fixture()
    contract = payload["usage_contract"]

    assert contract["offline_evaluation_only"] is True
    assert contract["runtime_prompt_import_forbidden"] is True
    assert contract["runtime_gate_import_forbidden"] is True
    assert contract["runtime_reply_import_forbidden"] is True
    assert contract["text_similarity_scoring_forbidden"] is True

    runtime_source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in RUNTIME_ROOT.rglob("*.py")
    )
    assert "v3_trusted_golden_set_v1" not in runtime_source
    assert "workflow_tests/fixtures" not in runtime_source


def test_confirmed_refusal_and_registration_boundaries_are_preserved() -> None:
    cases = {item["case_id"]: item for item in _load_fixture()["cases"]}

    for case_id in ("golden_v3_005", "golden_v3_046"):
        points = "；".join(cases[case_id]["annotation"]["must_answer_points"])
        assert "明确拒绝" in points
        assert "停止当前营销推进" in points
        assert "清洁小气泡" not in points

    payment_points = "；".join(cases["golden_v3_004"]["annotation"]["must_answer_points"])
    assert "完成线上活动登记后" in payment_points
    assert "免费皮肤检测" in payment_points


def test_payment_delivery_cases_include_prior_structured_activity_evidence() -> None:
    cases = _load_fixture()["cases"]

    for case in cases:
        annotation = case["annotation"]
        if "payment_collection" not in annotation["required_deliveries"]:
            continue
        delivered_ids = {
            str(item.get("asset_id") or "")
            for item in case["input"].get("delivered_assets") or []
            if isinstance(item, dict)
        }
        assert "s10_activity_intro" in delivered_ids, case["case_id"]
