from __future__ import annotations

import json
from typing import Any


def parallel_reply_chain_comparison(
    *,
    gate_router_shadow: dict[str, Any],
    tool_plan_preview: dict[str, Any],
    join_shadow: dict[str, Any],
    runner_shadow: dict[str, Any],
) -> dict[str, Any]:
    """Compare serial shadow outputs with the parallel runner replay.

    This is a migration diagnostic only. It compares routing and tool-plan
    structure; it does not decide customer semantics or produce customer text.
    """

    runner_mode = str(runner_shadow.get("mode") or "missing")
    branch_errors = _branch_errors(runner_shadow)
    output_contract_blockers = _runner_output_contract_blockers(runner_shadow)
    runner_gate = _runner_gate_shadow(runner_shadow)
    runner_tool_plan = _runner_tool_plan(runner_shadow)
    comparable = (
        runner_mode == "completed_shadow"
        and not branch_errors
        and not output_contract_blockers
        and bool(runner_gate)
        and bool(runner_tool_plan)
    )
    diffs = _diffs(
        gate_router_shadow=gate_router_shadow,
        tool_plan_preview=tool_plan_preview,
        runner_gate=runner_gate,
        runner_tool_plan=runner_tool_plan,
    ) if comparable else []

    status = "matched_shadow_replay" if comparable and not diffs else "diffs_found" if comparable else "not_comparable"
    return _drop_empty(
        {
            "schema_version": "parallel_reply_chain_comparison_v1",
            "mode": "shadow_diff_only",
            "status": status,
            "comparable": comparable,
            "diffs": diffs,
            "serial": {
                "gate_route": _route(gate_router_shadow),
                "fact_requirement": _fact_requirement(tool_plan_preview),
                "read_tool_signatures": _read_tool_signatures(tool_plan_preview),
                "join_final_route": str(join_shadow.get("final_route") or ""),
            },
            "parallel_replay": {
                "runner_mode": runner_mode,
                "gate_route": _route(runner_gate),
                "fact_requirement": _fact_requirement(runner_tool_plan),
                "read_tool_signatures": _read_tool_signatures(runner_tool_plan),
                "branch_errors": branch_errors,
                "output_contract_blockers": output_contract_blockers,
            },
            "review_gate": {
                "requires_human_review_before_behavior_switch": True,
                "can_enable_behavior_switch": False,
                "reason": "shadow_comparison_is_evidence_only_not_a_release_approval",
            },
            "safety": {
                "diagnostic_only": True,
                "no_runtime_behavior_change": True,
                "no_model_calls": True,
                "no_tool_execution": True,
                "no_customer_messages_sent": True,
                "no_database_writes": True,
            },
        }
    )


def _diffs(
    *,
    gate_router_shadow: dict[str, Any],
    tool_plan_preview: dict[str, Any],
    runner_gate: dict[str, Any],
    runner_tool_plan: dict[str, Any],
) -> list[dict[str, str]]:
    diffs: list[dict[str, str]] = []
    _add_diff(diffs, "gate_route", _route(gate_router_shadow), _route(runner_gate))
    _add_diff(diffs, "fact_requirement", _fact_requirement(tool_plan_preview), _fact_requirement(runner_tool_plan))
    _add_diff(
        diffs,
        "read_tool_signatures",
        _json(_read_tool_signatures(tool_plan_preview)),
        _json(_read_tool_signatures(runner_tool_plan)),
    )
    return diffs


def _add_diff(diffs: list[dict[str, str]], field: str, serial: str, parallel: str) -> None:
    if serial != parallel:
        diffs.append({"field": field, "serial": serial, "parallel": parallel})


def _runner_gate_shadow(runner_shadow: dict[str, Any]) -> dict[str, Any]:
    output = _branch_output(runner_shadow, "sop_chat_gate")
    candidate = output.get("gate_router_shadow") if isinstance(output.get("gate_router_shadow"), dict) else output
    return candidate if isinstance(candidate, dict) else {}


def _runner_tool_plan(runner_shadow: dict[str, Any]) -> dict[str, Any]:
    output = _branch_output(runner_shadow, "tool_planner")
    candidate = output.get("tool_plan_preview") if isinstance(output.get("tool_plan_preview"), dict) else output
    return candidate if isinstance(candidate, dict) else {}


def _branch_output(runner_shadow: dict[str, Any], branch_name: str) -> dict[str, Any]:
    branches = runner_shadow.get("branches") if isinstance(runner_shadow.get("branches"), dict) else {}
    branch = branches.get(branch_name) if isinstance(branches.get(branch_name), dict) else {}
    output = branch.get("output") if isinstance(branch.get("output"), dict) else {}
    return output


def _branch_errors(runner_shadow: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    branches = runner_shadow.get("branches") if isinstance(runner_shadow.get("branches"), dict) else {}
    for name, branch in branches.items():
        if not isinstance(branch, dict):
            continue
        if branch.get("status") == "error":
            errors.append(f"{name}:{branch.get('error') or 'error'}")
    return errors


def _runner_output_contract_blockers(runner_shadow: dict[str, Any]) -> list[str]:
    if str(runner_shadow.get("mode") or "") != "completed_shadow":
        return []
    audit = runner_shadow.get("branch_output_contract_audit")
    if not isinstance(audit, dict) or audit.get("schema_version") != "parallel_branch_output_contract_audit_v1":
        return ["missing_runner_branch_output_contract_audit"]
    if audit.get("ready") is True:
        return []
    blockers = audit.get("blockers")
    if isinstance(blockers, list) and blockers:
        return [str(item) for item in blockers if str(item)]
    return ["runner_branch_output_contract_not_ready"]


def _route(value: dict[str, Any]) -> str:
    return str(value.get("route_suggestion") or "").strip()


def _fact_requirement(value: dict[str, Any]) -> str:
    return str(value.get("fact_requirement") or "none").strip()


def _read_tool_signatures(tool_plan: dict[str, Any]) -> list[dict[str, Any]]:
    tools = tool_plan.get("read_tool_calls") if isinstance(tool_plan.get("read_tool_calls"), list) else []
    signatures: list[dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        signatures.append(
            {
                "name": str(item.get("name") or item.get("tool") or ""),
                "arguments": item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
            }
        )
    return signatures


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
