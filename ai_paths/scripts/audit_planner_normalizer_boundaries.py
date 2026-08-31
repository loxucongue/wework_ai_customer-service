from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


NORMALIZER_FILES = (
    "ai_paths/app/graph/planner/brain_v2_normalizer.py",
    "ai_paths/app/graph/planner/planner_schema_normalizer.py",
    "ai_paths/app/graph/planner/planner_reply_structure_guards.py",
    "ai_paths/app/graph/planner/planner_tool_fact_guards.py",
    "ai_paths/app/graph/planner/planner_transaction_guards.py",
)

BOUNDARY_MARKERS: dict[str, dict[str, Any]] = {
    "paid_state_blocks_payment_card": {
        "category": "fact_hard_boundary",
        "owner": "code_validation",
        "reason": "Authoritative paid facts must block repeated payment_collection.",
        "markers": ["_has_paid_deposit_context", "_reconcile_paid_payment_decision"],
    },
    "health_risk_blocks_payment_card": {
        "category": "fact_hard_boundary",
        "owner": "code_validation",
        "reason": "Current health-risk holds are hard safety facts, not sales rhythm.",
        "markers": ["is_hard_health_risk_hold", "health_risk_hold"],
    },
    "payment_amount_and_single_card_shape": {
        "category": "fact_hard_boundary",
        "owner": "code_validation",
        "reason": "Payment card amount and one-card-per-turn are message-structure protections.",
        "markers": ["payment_amount_for_party_size", "_append_required_payment_collection"],
    },
    "visible_store_and_real_store_integrity": {
        "category": "fact_hard_boundary",
        "owner": "code_validation",
        "reason": "Store cards must be grounded in known/visible store facts.",
        "markers": ["store_scope_ids", "_direct_reply_store_consistency_violations"],
    },
    "unsupported_project_payment_block": {
        "category": "fact_hard_boundary",
        "owner": "code_validation",
        "reason": "Unsupported online projects cannot collect activity payment.",
        "markers": ["unsupported_online_projects", "payment_collection_blocked_by_precision_qa_boundary"],
    },
    "schema_enum_and_tool_allowlist": {
        "category": "data_cleanup",
        "owner": "schema_normalizer",
        "reason": "Schema, enum, and tool allowlist cleanup is allowed before model handoff.",
        "markers": ["normalize_enum", "ALLOWED_TOOLS", "dedupe_tools"],
    },
    "date_argument_cleanup": {
        "category": "data_cleanup",
        "owner": "tool_argument_normalizer",
        "reason": (
            "Date normalization can improve factual tool execution without deciding customer psychology. "
            "Store lookup query meaning remains model-owned and must not be rewritten from historical context in code."
        ),
        "markers": ["_normalize_available_time_dates_from_context"],
    },
    "reply_repair_hints_not_runtime_decisions": {
        "category": "soft_prompt",
        "owner": "reply_prompt_or_repair_hint",
        "reason": "Repair hints may explain a fact violation but must not become customer-psychology logic.",
        "markers": ["reply_constraints.append", "normalizer_policy_violations.append"],
    },
}

FORBIDDEN_SEMANTIC_OWNERSHIP_MARKERS = {
    "order_required_before_payment_card": [
        "work_order_required_before_payment_collection",
        "同门店同金额订单存在后才能发卡",
        "存在成功创建或复用订单才可以发送",
        "入口还没对上成功",
    ],
    "code_decides_store_card_sales_rhythm": [
        "_current_turn_suitable_for_store_card",
        "_should_resend_store_card_for_sales_rhythm",
    ],
    "code_discourages_payment_resend_by_recent_card": [
        "刚发您的小程序卡还可以直接点",
        "之前那张卡",
        "翻一下前面的入口",
    ],
}


def audit_planner_normalizer_boundaries(*, repo_root: Path, head_ref: str = "HEAD") -> dict[str, Any]:
    texts = {path: _read_text(repo_root / path) for path in NORMALIZER_FILES}
    combined = "\n".join(texts.values())
    categories: dict[str, list[dict[str, Any]]] = {
        "fact_hard_boundary": [],
        "data_cleanup": [],
        "soft_prompt": [],
        "semantic_overreach": [],
    }
    missing_required: list[str] = []
    for rule_id, spec in BOUNDARY_MARKERS.items():
        present_markers = [marker for marker in spec["markers"] if marker in combined]
        item = {
            "rule_id": rule_id,
            "owner": spec["owner"],
            "reason": spec["reason"],
            "required_markers": list(spec["markers"]),
            "present_markers": present_markers,
            "complete": len(present_markers) == len(spec["markers"]),
        }
        categories[spec["category"]].append(item)
        if not item["complete"]:
            missing_required.append(rule_id)

    forbidden_hits = _forbidden_hits(combined)
    if forbidden_hits:
        categories["semantic_overreach"] = forbidden_hits

    git_commit = _git_output(repo_root, ["git", "rev-parse", head_ref])
    blockers = [f"missing_required_boundary:{rule_id}" for rule_id in missing_required]
    blockers.extend(f"semantic_overreach_marker:{item['rule_id']}:{item['marker']}" for item in forbidden_hits)
    return {
        "schema_version": "planner_normalizer_boundary_audit_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "git_commit": git_commit,
        "head_ref": head_ref,
        "audited_files": list(NORMALIZER_FILES),
        "categories": categories,
        "summary": {
            "fact_hard_boundary_count": len(categories["fact_hard_boundary"]),
            "data_cleanup_count": len(categories["data_cleanup"]),
            "soft_prompt_count": len(categories["soft_prompt"]),
            "semantic_overreach_count": len(categories["semantic_overreach"]),
            "missing_required_count": len(missing_required),
        },
        "normalizer_boundary_passed": not blockers,
        "blockers": blockers,
        "safety": {
            "audit_only": True,
            "does_not_change_runtime_behavior": True,
            "does_not_send_customer_messages": True,
            "does_not_write_database": True,
            "does_not_call_models": True,
            "does_not_call_external_tools": True,
        },
        "source": "audit_planner_normalizer_boundaries",
    }


def _forbidden_hits(text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for rule_id, markers in FORBIDDEN_SEMANTIC_OWNERSHIP_MARKERS.items():
        for marker in markers:
            if marker in text:
                hits.append(
                    {
                        "rule_id": rule_id,
                        "marker": marker,
                        "category": "semantic_overreach",
                        "owner": "must_move_to_prompt_or_remove",
                    }
                )
    return hits


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _git_output(repo_root: Path, command: list[str]) -> str:
    try:
        return subprocess.check_output(command, cwd=repo_root, text=True).strip()
    except Exception:
        return ""


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--output")
    return parser.parse_args()


def main() -> None:
    args = _args()
    report = audit_planner_normalizer_boundaries(repo_root=Path(args.repo_root).resolve(), head_ref=args.head_ref)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
