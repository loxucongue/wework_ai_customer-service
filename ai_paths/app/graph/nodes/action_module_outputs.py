from __future__ import annotations

from typing import Any

from app.graph.state import AgentState


def build_handoff_output(action: dict[str, Any], state: AgentState) -> dict[str, Any]:
    handoff_intent = str(action.get("intent") or "")
    if handoff_intent not in {"human_request", "complaint_refund"}:
        handoff_intent = "complaint_refund" if any(
            item.get("intent") == "complaint_refund" for item in state.get("intents", []) if isinstance(item, dict)
        ) else "human_request"
    return {
        "skill": "handoff",
        "intent": handoff_intent,
        "facts": [],
        "reply_points": ["本轮涉及投诉、退款、真实订单/付款/预约记录或高风险事项；最终回复应先承接客户诉求，再说明会让专业同事结合真实记录协助核对。"],
        "missing_slots": [],
        "risk_flags": state.get("guardrail_result", {}).get("terms", []),
        "suggested_next_step": "professional_assist",
        "confidence": 0.9,
    }


def build_active_task_output(active_task: dict[str, Any], json_dumps: Any) -> dict[str, Any]:
    return {
        "skill": "active_task",
        "intent": active_task.get("type", ""),
        "facts": [json_dumps(active_task)],
        "reply_points": [str(active_task.get("reply_focus") or "")],
        "missing_slots": active_task.get("missing_slots") or [],
        "risk_flags": [],
        "suggested_next_step": active_task.get("next_action", ""),
        "confidence": 0.85,
    }
