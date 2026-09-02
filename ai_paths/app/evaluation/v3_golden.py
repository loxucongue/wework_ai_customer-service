from __future__ import annotations

from copy import deepcopy
from typing import Any


def golden_case_to_simulation(case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "").strip()
    if not case_id:
        raise ValueError("golden case_id is required")
    case_input = _dict(case.get("input"))
    tool_facts = _dict(case_input.get("tool_facts"))
    payment_facts = _dict(case_input.get("payment_and_order_facts"))
    current = _dict(case_input.get("current_message"))
    annotation = _dict(case.get("annotation"))
    conversation = [_normalize_history_item(item) for item in _list(case_input.get("conversation"))]
    conversation = [item for item in conversation if item]

    completed_sops = [
        str(item.get("asset_id") or "").strip()
        for item in _list(case_input.get("delivered_assets"))
        if isinstance(item, dict) and str(item.get("asset_id") or "").startswith("s10_")
    ]
    expected = {
        "must_reply": True,
        "forbid_message_types": list(annotation.get("forbidden_actions") or []),
    }
    timeline_message = deepcopy(current)
    timeline_message.update(
        {
            "kind": "customer_message",
            "content": str(current.get("content") or ""),
            "msgtype": str(current.get("msgtype") or "text"),
            "created_at": str(current.get("created_at") or case_input.get("current_time") or ""),
            "expected": expected,
        }
    )
    for key in (
        "confirmed_store_id",
        "confirmed_store_name",
        "store_id",
        "store_name",
        "appointment_id",
        "appointment_time",
    ):
        value = case_input.get(key)
        if value not in (None, ""):
            timeline_message[key] = deepcopy(value)
    return {
        "id": case_id,
        "category": str(case.get("category") or "v3_golden"),
        "critical": str(case.get("evaluation_partition") or "") == "calibration",
        "semantic_goal": str(annotation.get("reference_reply_direction") or ""),
        "initial": {
            "conversation": conversation,
            "customer": deepcopy(_dict(case_input.get("customer"))),
            "orders": deepcopy(_list(payment_facts.get("orders"))),
            "stores": deepcopy(_list(tool_facts.get("visible_stores"))),
            "case_facts": deepcopy(_list(tool_facts.get("case_facts"))),
            "geocodes": deepcopy(_dict(tool_facts.get("geocodes"))),
            "distances": deepcopy(_dict(tool_facts.get("distances"))),
            "history_events": deepcopy(_list(tool_facts.get("history_events"))),
            "completed_sops": completed_sops,
        },
        "timeline": [timeline_message],
        "expected": expected,
    }


def simulation_result_to_golden_result(
    case: dict[str, Any],
    simulation_result: dict[str, Any],
) -> dict[str, Any]:
    steps = _list(simulation_result.get("steps"))
    last_step = next((item for item in reversed(steps) if isinstance(item, dict)), {})
    meta = _dict(last_step.get("response_meta"))
    run = _dict(last_step.get("run"))
    output = _dict(run.get("output_snapshot"))
    branch_metrics = _dict(meta.get("parallel_branch_metrics"))
    messages = _list(last_step.get("sync_reply_messages"))
    if not messages:
        for outbox_item in _list(last_step.get("new_outbox")):
            if isinstance(outbox_item, dict):
                messages.extend(_list(outbox_item.get("reply_messages")))
    return {
        "case_id": str(case.get("case_id") or ""),
        "partition": case.get("evaluation_partition"),
        "category": case.get("category"),
        "reply_messages": deepcopy(messages),
        "content_selection_metrics": deepcopy(_dict(meta.get("content_selection_metrics"))),
        "content_decisions": deepcopy(_list(meta.get("reply_content_decisions"))),
        "knowledge_use": deepcopy(
            _dict(meta.get("reply_knowledge_use") or meta.get("knowledge_use"))
        ),
        "semantic_route_summary": deepcopy(_dict(branch_metrics.get("semantic_route_summary"))),
        "sales_policy": _sales_policy_diagnostics(output),
        "model_usage": _collect_model_usage(simulation_result),
        "hard_pass": bool(simulation_result.get("hard_pass")),
        "hard_errors": deepcopy(_list(simulation_result.get("hard_errors"))),
        "infrastructure_errors": deepcopy(_list(simulation_result.get("infrastructure_errors"))),
        "duration_ms": simulation_result.get("duration_ms"),
        "request_id": last_step.get("request_id"),
        "run_dir": simulation_result.get("run_dir"),
        "human_review": {"status": "pending", "verdict": "", "notes": ""},
    }


def _sales_policy_diagnostics(output: dict[str, Any]) -> dict[str, Any]:
    """Expose only evaluation-safe decisions; exclude customer scope and raw run snapshots."""
    candidates = []
    for item in _list(output.get("cardpoint_candidates"))[:5]:
        if not isinstance(item, dict):
            continue
        candidates.append({
            "content_id": item.get("content_id"), "category_key": item.get("category_key"),
            "scenario_name": item.get("scenario_name"), "scenario_keys": deepcopy(_list(item.get("scenario_keys"))),
            "tactic_tag": item.get("tactic_tag"), "solution_idea": item.get("solution_idea"),
            "reference_text": item.get("reference_text"), "content_types": deepcopy(_list(item.get("content_types"))),
        })
    strategies = []
    for item in _list(output.get("followup_strategy_candidates"))[:5]:
        if not isinstance(item, dict):
            continue
        strategies.append({
            "strategy_key": item.get("strategy_key"), "name": item.get("name"),
            "category_key": item.get("category_key"), "scenario_keys": deepcopy(_list(item.get("scenario_keys"))),
            "score": item.get("score"),
        })
    return {
        "primary_task": deepcopy(_dict(output.get("primary_task"))),
        "secondary_tasks": deepcopy(_list(output.get("secondary_tasks"))),
        "realtime_intent": deepcopy(_dict(output.get("realtime_intent"))),
        "emotion_decision": deepcopy(_dict(output.get("emotion_decision"))),
        "closing_decision": deepcopy(_dict(output.get("closing_decision"))),
        "cardpoint_decision": deepcopy(_dict(output.get("cardpoint_decision"))),
        "cardpoint_candidates": candidates, "followup_strategy_candidates": strategies,
        "retrieval_audit": deepcopy(_dict(output.get("sales_strategy_retrieval_audit"))),
        "policy_runtime": deepcopy(_dict(output.get("ai_sales_policy"))),
        "catalog_runtime": deepcopy(_dict(output.get("sales_strategy_catalog"))),
    }


def _collect_model_usage(value: Any) -> list[dict[str, Any]]:
    usage: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            model = str(item.get("model") or "").strip()
            provider = str(item.get("semantic_provider") or item.get("provider") or "").strip()
            if model and (provider or "usage" in item or "duration_ms" in item):
                usage.append(deepcopy(item))
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return usage


def _normalize_history_item(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        return {"direction": "staff", "role": "assistant", "content": value}
    if not isinstance(value, dict):
        return None
    item = deepcopy(value)
    role = str(item.get("role") or "").lower()
    direction = str(item.get("direction") or "").lower()
    if direction not in {"customer", "staff"}:
        direction = "customer" if role in {"customer", "user"} else "staff"
    item["direction"] = direction
    item["role"] = "user" if direction == "customer" else "assistant"
    return item


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
