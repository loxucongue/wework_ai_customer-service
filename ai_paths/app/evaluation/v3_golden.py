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
        "timeline": [
            {
                "kind": "customer_message",
                "content": str(current.get("content") or ""),
                "msgtype": str(current.get("msgtype") or "text"),
                "created_at": str(current.get("created_at") or case_input.get("current_time") or ""),
                "expected": expected,
            }
        ],
        "expected": expected,
    }


def simulation_result_to_golden_result(
    case: dict[str, Any],
    simulation_result: dict[str, Any],
) -> dict[str, Any]:
    steps = _list(simulation_result.get("steps"))
    last_step = next((item for item in reversed(steps) if isinstance(item, dict)), {})
    meta = _dict(last_step.get("response_meta"))
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
        "hard_pass": bool(simulation_result.get("hard_pass")),
        "hard_errors": deepcopy(_list(simulation_result.get("hard_errors"))),
        "infrastructure_errors": deepcopy(_list(simulation_result.get("infrastructure_errors"))),
        "duration_ms": simulation_result.get("duration_ms"),
        "request_id": last_step.get("request_id"),
        "run_dir": simulation_result.get("run_dir"),
        "human_review": {"status": "pending", "verdict": "", "notes": ""},
    }


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
