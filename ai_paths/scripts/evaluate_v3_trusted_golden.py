from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_set(value: Any) -> set[str]:
    return {str(item).strip() for item in _as_list(value) if str(item).strip()}


def _message_types(messages: Any) -> set[str]:
    return {
        str(item.get("type") or "").strip()
        for item in _as_list(messages)
        if isinstance(item, dict) and str(item.get("type") or "").strip()
    }


def _result_case_id(result: dict[str, Any]) -> str:
    return str(
        result.get("case_id")
        or result.get("scenario_id")
        or result.get("id")
        or ""
    ).strip()


def _metrics_from_result(result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("content_selection_metrics")
    if isinstance(metrics, dict):
        return metrics
    metadata = result.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("content_selection_metrics"), dict):
        return metadata["content_selection_metrics"]
    return {}


def _gate_candidates(result: dict[str, Any]) -> set[str]:
    metrics = _metrics_from_result(result)
    return (
        _string_set(metrics.get("nominated_ids"))
        or _string_set(result.get("gate_candidate_ids"))
        or _string_set(result.get("candidate_content_ids"))
    )


def _adopted_assets(result: dict[str, Any]) -> set[str]:
    metrics = _metrics_from_result(result)
    return (
        _string_set(metrics.get("adopted_ids"))
        or _string_set(result.get("selected_content_ids"))
        or _string_set(result.get("adopted_content_ids"))
    )


def _delivered_assets(result: dict[str, Any]) -> set[str]:
    metrics = _metrics_from_result(result)
    delivered = (
        _string_set(metrics.get("delivered_ids"))
        or _string_set(result.get("delivered_asset_ids"))
        or _string_set(result.get("delivered_content_ids"))
    )
    messages = _as_list(result.get("reply_messages"))
    if "payment_collection" in _message_types(messages):
        delivered.add("payment_collection")
    if "store_address" in _message_types(messages):
        delivered.add("store_address")
    if "s10_activity_intro" in delivered:
        delivered.add("activity_image")
    if "s10_need_and_case" in delivered or any(
        item.startswith("configured_effect_case_") for item in delivered
    ):
        delivered.add("case_image")
    return delivered


def _has_payment_card(result: dict[str, Any]) -> bool:
    if "payment_collection" in _delivered_assets(result):
        return True
    return "payment_collection" in _message_types(result.get("reply_messages"))


def _human_verdict(result: dict[str, Any]) -> str:
    review = result.get("human_review")
    if isinstance(review, dict):
        status = str(review.get("status") or "").strip().lower()
        verdict = str(review.get("verdict") or "").strip().lower()
        if status in {"reviewed", "approved"} and verdict in {"pass", "fail"}:
            return verdict
    verdict = str(result.get("human_verdict") or "").strip().lower()
    return verdict if verdict in {"pass", "fail"} else ""


def _critic_status(result: dict[str, Any]) -> str:
    critic = result.get("critic")
    if isinstance(critic, dict):
        status = str(critic.get("status") or critic.get("verdict") or "").strip().lower()
        if status in {"pass", "fail"}:
            return status
    status = str(result.get("critic_status") or "").strip().lower()
    return status if status in {"pass", "fail"} else ""


def evaluate(golden: dict[str, Any], results: dict[str, Any]) -> dict[str, Any]:
    cases = {
        str(item.get("case_id") or ""): item
        for item in _as_list(golden.get("cases"))
        if isinstance(item, dict)
    }
    by_id = {
        _result_case_id(item): item
        for item in _as_list(results.get("results") or results.get("attempts") or results.get("cases"))
        if isinstance(item, dict) and _result_case_id(item)
    }
    rows: list[dict[str, Any]] = []
    for case_id, case in cases.items():
        result = by_id.get(case_id)
        annotation = case.get("annotation") if isinstance(case.get("annotation"), dict) else {}
        required_gate = _string_set(annotation.get("required_gate_asset_ids"))
        acceptable_gate = required_gate | _string_set(annotation.get("acceptable_gate_asset_ids"))
        required_delivery = _string_set(annotation.get("required_deliveries"))
        forbidden_actions = _string_set(annotation.get("forbidden_actions"))
        row: dict[str, Any] = {
            "case_id": case_id,
            "partition": case.get("evaluation_partition"),
            "category": case.get("category"),
            "evaluated": result is not None,
        }
        if result is None:
            rows.append(row)
            continue
        nominated = _gate_candidates(result)
        adopted = _adopted_assets(result)
        delivered = _delivered_assets(result)
        forbidden_hit = {
            action
            for action in forbidden_actions
            if action in adopted or action in delivered or (action == "payment_collection" and _has_payment_card(result))
        }
        row.update(
            {
                "gate_recall": 1.0 if not required_gate else len(required_gate & nominated) / len(required_gate),
                "false_nomination": len(nominated - acceptable_gate) / len(nominated) if nominated else 0.0,
                "reply_adoption": len(required_gate & adopted) / len(required_gate) if required_gate else 1.0,
                "false_adoption": len(adopted - acceptable_gate) / len(adopted) if adopted and acceptable_gate else 0.0,
                "delivery_completion": (
                    1.0
                    if not required_delivery
                    else len(required_delivery & delivered) / len(required_delivery)
                ),
                "forbidden_action_hit": sorted(forbidden_hit),
                "has_payment_card": _has_payment_card(result),
                "critic_status": _critic_status(result),
                "human_verdict": _human_verdict(result),
            }
        )
        rows.append(row)

    evaluated_rows = [row for row in rows if row.get("evaluated")]
    summary: dict[str, Any] = {
        "schema_version": "v3_trusted_golden_evaluation_v1",
        "golden_schema_version": golden.get("schema_version"),
        "golden_git_commit": golden.get("git_commit"),
        "result_git_commit": results.get("git_commit") or results.get("commit"),
        "total_cases": len(cases),
        "evaluated_cases": len(evaluated_rows),
        "missing_cases": [row["case_id"] for row in rows if not row.get("evaluated")],
        "cases": rows,
    }
    for key in (
        "gate_recall",
        "false_nomination",
        "reply_adoption",
        "false_adoption",
        "delivery_completion",
    ):
        values = [float(row[key]) for row in evaluated_rows if key in row]
        summary[key] = sum(values) / len(values) if values else None
    summary["forbidden_action_case_count"] = sum(
        1 for row in evaluated_rows if row.get("forbidden_action_hit")
    )
    first_inquiry_cases = [
        row
        for row in evaluated_rows
        if "首次询价" in str(row.get("category") or "")
        or "活动介绍" in str(row.get("category") or "")
    ]
    summary["first_inquiry_payment_card_rate"] = (
        sum(1 for row in first_inquiry_cases if row.get("has_payment_card")) / len(first_inquiry_cases)
        if first_inquiry_cases
        else None
    )

    calibration_rows = [
        row
        for row in evaluated_rows
        if row.get("partition") == "calibration"
        and row.get("critic_status")
        and row.get("human_verdict")
    ]
    holdout_rows = [
        row
        for row in evaluated_rows
        if row.get("partition") == "holdout"
        and row.get("critic_status")
        and row.get("human_verdict")
    ]
    summary["critic"] = {
        "status": "calibrated" if len(calibration_rows) == 15 else "pending_human_review",
        "calibration": _critic_agreement(calibration_rows),
        "holdout": _critic_agreement(holdout_rows),
    }
    return summary


def _critic_agreement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"evaluated": 0, "accuracy": None, "false_positive_rate": None, "precision": None}
    true_positive = false_positive = true_negative = false_negative = 0
    for row in rows:
        expected_pass = row.get("human_verdict") == "pass"
        predicted_pass = row.get("critic_status") == "pass"
        if predicted_pass and expected_pass:
            true_positive += 1
        elif predicted_pass and not expected_pass:
            false_negative += 1
        elif not predicted_pass and expected_pass:
            false_positive += 1
        else:
            true_negative += 1
    total = true_positive + false_positive + true_negative + false_negative
    precision_denominator = true_positive + false_positive
    false_positive_denominator = false_positive + true_negative
    return {
        "evaluated": total,
        "accuracy": (true_positive + true_negative) / total if total else None,
        "precision": true_positive / precision_denominator if precision_denominator else None,
        "false_positive_rate": (
            false_positive / false_positive_denominator
            if false_positive_denominator
            else None
        ),
        "confusion": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "true_negative": true_negative,
            "false_negative": false_negative,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate V3 outputs against the trusted golden set.")
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    results = json.loads(args.results.read_text(encoding="utf-8"))
    output = evaluate(golden, results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
