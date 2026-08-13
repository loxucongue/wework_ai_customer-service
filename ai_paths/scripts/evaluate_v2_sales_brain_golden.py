from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _string_set(value: Any) -> set[str]:
    return {str(item).strip() for item in value or [] if str(item).strip()}


def _jaccard(left: Any, right: Any) -> float:
    a, b = _string_set(left), _string_set(right)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def annotation_agreement(manifest: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in manifest.get("cases") or []:
        if not isinstance(case, dict):
            continue
        left, right = case.get("annotator_a"), case.get("annotator_b")
        if not isinstance(left, dict) or not isinstance(right, dict):
            continue
        flag_keys = set((left.get("quality_flags") or {})) | set((right.get("quality_flags") or {}))
        comparable_flags = [
            key
            for key in flag_keys
            if (left.get("quality_flags") or {}).get(key) is not None
            and (right.get("quality_flags") or {}).get(key) is not None
        ]
        flag_agreement = (
            sum(
                (left.get("quality_flags") or {}).get(key)
                == (right.get("quality_flags") or {}).get(key)
                for key in comparable_flags
            )
            / len(comparable_flags)
            if comparable_flags
            else None
        )
        rows.append(
            {
                "id": case.get("id"),
                "action_jaccard": _jaccard(left.get("acceptable_actions"), right.get("acceptable_actions")),
                "required_asset_jaccard": _jaccard(left.get("required_asset_ids"), right.get("required_asset_ids")),
                "flag_agreement": flag_agreement,
            }
        )
    numeric = [
        value
        for row in rows
        for value in (row["action_jaccard"], row["required_asset_jaccard"], row["flag_agreement"])
        if value is not None
    ]
    return {
        "annotated_case_count": len(rows),
        "agreement": sum(numeric) / len(numeric) if numeric else None,
        "ready_for_critic_calibration": bool(numeric) and sum(numeric) / len(numeric) >= 0.8,
        "cases": rows,
    }


def _consensus_assets(case: dict[str, Any], key: str) -> set[str]:
    annotations = [case.get("annotator_a"), case.get("annotator_b")]
    values = [_string_set(item.get(key)) for item in annotations if isinstance(item, dict)]
    return set.intersection(*values) if len(values) == 2 else set()


def selection_metrics(manifest: dict[str, Any], results: dict[str, Any]) -> dict[str, Any]:
    by_id = {
        str(item.get("scenario_id") or item.get("id") or ""): item
        for item in results.get("results") or results.get("attempts") or []
        if isinstance(item, dict)
    }
    rows: list[dict[str, Any]] = []
    for case in manifest.get("cases") or []:
        if not isinstance(case, dict) or not isinstance(case.get("annotator_a"), dict) or not isinstance(case.get("annotator_b"), dict):
            continue
        result = by_id.get(str(case.get("id") or ""))
        if not isinstance(result, dict):
            continue
        required = _consensus_assets(case, "required_asset_ids")
        acceptable = required | _consensus_assets(case, "acceptable_asset_ids")
        metrics = result.get("content_selection_metrics") or result.get("metadata", {}).get("content_selection_metrics") or {}
        nominated = _string_set(metrics.get("nominated_ids"))
        adopted = _string_set(metrics.get("adopted_ids"))
        delivered = _string_set(metrics.get("delivered_ids"))
        rows.append(
            {
                "id": case.get("id"),
                "gate_recall": 1.0 if not required else len(required & nominated) / len(required),
                "false_nomination": len(nominated - acceptable) / len(nominated) if nominated else 0.0,
                "reply_adoption": len(required & adopted) / len(required) if required else 1.0,
                "false_adoption": len(adopted - acceptable) / len(adopted) if adopted else 0.0,
                "delivery_completion": len(adopted & delivered) / len(adopted) if adopted else 1.0,
            }
        )
    summary: dict[str, Any] = {"evaluated_case_count": len(rows), "cases": rows}
    for key in ("gate_recall", "false_nomination", "reply_adoption", "false_adoption", "delivery_completion"):
        summary[key] = sum(row[key] for row in rows) / len(rows) if rows else None
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate dual annotations and V2 content selection layers.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    output = {"annotation_agreement": annotation_agreement(manifest)}
    if args.results:
        output["selection_metrics"] = selection_metrics(
            manifest,
            json.loads(args.results.read_text(encoding="utf-8")),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
