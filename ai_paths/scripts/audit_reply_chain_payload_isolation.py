from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.prompts.sop_chat_gate import build_sop_chat_gate_messages
from app.schemas import ChatRequest
from app.services.sop_execution_service import _chat_selector_input


SHADOW_ONLY_FIELDS: tuple[str, ...] = (
    "sop_gate_preview",
    "sop_gate_router_shadow",
    "reply_chain_shadow_context",
    "tool_plan_preview",
    "read_only_tool_executor_shadow",
    "reply_chain_join_shadow",
    "reply_final_brain_handoff_shadow",
    "parallel_reply_chain_shadow",
    "reply_chain_commit_shadow",
    "reply_chain_refactor_flags",
    "parallel_gate_planner_runner_shadow",
    "parallel_reply_chain_diagnostics",
    "parallel_reply_chain_comparison",
    "reply_chain_shadow_bundle_audit",
    "reply_chain_behavior_switch_guard",
)


def audit_reply_chain_payload_isolation(*, repo_root: Path, head_ref: str = "HEAD") -> dict[str, Any]:
    """Audit that shadow-only refactor diagnostics do not enter active model payloads."""

    state = _state_with_shadow_fields()
    planner_payload = _planner_payload_for_model(state)
    reply_payload = reply_user_payload_for_model(state)
    request_context = {
        "category_id": "S10",
        **{
            field: {
                "schema_version": f"{field}_v_audit",
                "marker": f"shadow-only-gate-marker::{field}",
            }
            for field in SHADOW_ONLY_FIELDS
        },
    }
    request = ChatRequest(
        content="how to join",
        customer_id="sim_payload_isolation_customer",
        corp_id="sim_payload_isolation_corp",
        conversation_history=["user: hello", "assistant: opening"],
        request_context=request_context,
    )
    selector_input = _chat_selector_input(
        request,
        [
            {
                "id": "s10_activity_intro",
                "scope": "chat_gate",
                "scopes": ["chat_gate"],
                "name": "Activity intro",
                "purpose": "Explain the activity.",
                "mainline_stage": "activity",
                "reply_messages": [
                    {
                        "type": "text",
                        "order": 1,
                        "content": {"text": "Activity details."},
                    }
                ],
            }
        ],
        sop_progress_evidence={},
        recent_delivery_evidence=[],
        customer_memory={},
        customer_context={},
    )
    gate_messages = build_sop_chat_gate_messages(selector_input)
    payloads = {
        "planner": planner_payload,
        "reply": reply_payload,
        "sop_chat_gate_selector": selector_input,
        "sop_chat_gate_messages": gate_messages,
    }
    leaks = _leaks_by_payload(payloads)
    commit = _git_output(repo_root, ["git", "rev-parse", head_ref])
    return {
        "schema_version": "reply_chain_payload_isolation_audit_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "git_commit": commit,
        "git_commit_set": [commit] if commit else [],
        "head_ref": head_ref,
        "shadow_only_fields": list(SHADOW_ONLY_FIELDS),
        "payloads_checked": list(payloads),
        "leaked_fields_by_payload": leaks,
        "payload_isolation_passed": not any(leaks.values()),
        "active_model_payloads_checked": True,
        "safety": {
            "audit_only": True,
            "does_not_change_runtime_behavior": True,
            "does_not_send_customer_messages": True,
            "does_not_write_database": True,
            "does_not_call_models": True,
        },
        "source": "audit_reply_chain_payload_isolation",
    }


def _state_with_shadow_fields() -> dict[str, Any]:
    state: dict[str, Any] = {
        "content": "how to book",
        "normalized_content": "how to book",
        "conversation_history": ["user: how to book"],
        "request_context": {"category_id": "S10"},
    }
    for field in SHADOW_ONLY_FIELDS:
        state[field] = {
            "schema_version": f"{field}_v_audit",
            "marker": f"shadow-only-marker::{field}",
            "nested": {"notes": [f"shadow-only-nested::{field}"]},
        }
    return state


def _leaks_by_payload(payloads: dict[str, Any]) -> dict[str, list[str]]:
    leaks: dict[str, list[str]] = {}
    for payload_name, payload in payloads.items():
        encoded = json.dumps(payload, ensure_ascii=False)
        leaked: list[str] = []
        for field in SHADOW_ONLY_FIELDS:
            markers = (
                field,
                f"shadow-only-marker::{field}",
                f"shadow-only-nested::{field}",
                f"shadow-only-gate-marker::{field}",
            )
            if any(marker in encoded for marker in markers):
                leaked.append(field)
        leaks[payload_name] = leaked
    return leaks


def _git_output(repo_root: Path, command: list[str]) -> str:
    try:
        return subprocess.check_output(command, cwd=repo_root, text=True).strip()
    except Exception:
        return ""


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit that reply-chain shadow diagnostics do not leak into active Planner, "
            "Reply, or SOP Chat Gate model payloads. This is read-only and does not call models."
        )
    )
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--report", type=Path, default=Path(".tmp_runtime/payload_isolation_audit.json"))
    return parser.parse_args()


def main() -> int:
    args = _args()
    repo_root = Path(__file__).resolve().parents[2]
    report = audit_reply_chain_payload_isolation(repo_root=repo_root, head_ref=args.head_ref)
    output = args.report if args.report.is_absolute() else repo_root / args.report
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["payload_isolation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
