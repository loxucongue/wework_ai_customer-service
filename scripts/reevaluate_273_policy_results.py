from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.policies.business_scene_table import infer_policy_family  # noqa: E402
from scripts.run_273_policy_regression import (  # noqa: E402
    business_family_matched,
    judge_result,
    write_outputs,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _refresh_row(row: dict[str, Any]) -> dict[str, Any]:
    refreshed = dict(row)
    expected = infer_policy_family(
        stage=str(refreshed.get("customer_stage") or ""),
        scene_type=str(refreshed.get("scene_type") or ""),
        question=str(refreshed.get("question") or ""),
        business_logic=str(refreshed.get("business_logic") or ""),
    )
    refreshed["expected_policy_family_id"] = expected
    refreshed["business_standard_matched"] = business_family_matched(
        expected,
        str(refreshed.get("policy_family_id") or ""),
    )
    meta = {
        "policy_family_id": str(refreshed.get("policy_family_id") or ""),
        "exact_policy_id": str(refreshed.get("exact_policy_id") or refreshed.get("policy_id") or ""),
        "policy_id": str(refreshed.get("policy_id") or ""),
        "active_scene_id": str(refreshed.get("active_scene_id") or ""),
        "active_scene_match_level": str(refreshed.get("active_scene_match_level") or ""),
        "active_scene_score": refreshed.get("active_scene_score", 0),
        "scene_guidance_injected": bool(refreshed.get("scene_guidance_injected")),
        "planner_source": str(refreshed.get("planner_source") or ""),
        "tool_result_keys": refreshed.get("tool_result_keys") or [],
        "primary_task": refreshed.get("primary_task") or {},
    }
    text_replies = [text for text in [refreshed.get("reply_1"), refreshed.get("reply_2")] if str(text or "").strip()]
    refreshed["judgement"] = judge_result(
        refreshed,
        str(refreshed.get("error") or ""),
        [str(text) for text in text_replies],
        list(refreshed.get("reply_types") or []),
        meta,
    )
    return refreshed


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/reevaluate_273_policy_results.py <results.jsonl>")
    source = Path(sys.argv[1]).resolve()
    rows = [_refresh_row(row) for row in _read_jsonl(source)]
    rows.sort(key=lambda item: int(item.get("index", 0)))
    json_path, md_path = write_outputs(rows)
    print(f"json={json_path}")
    print(f"report={md_path}")


if __name__ == "__main__":
    main()
