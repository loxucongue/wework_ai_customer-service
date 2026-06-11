from __future__ import annotations

from typing import Any

from app.graph.state import AgentState


def planner_primary_task(state: AgentState) -> dict[str, Any]:
    value = state.get("primary_task")
    return value if isinstance(value, dict) else {}


def planner_secondary_tasks(state: AgentState) -> list[dict[str, Any]]:
    value = state.get("secondary_tasks")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def planner_tasks(state: AgentState) -> list[dict[str, Any]]:
    primary = planner_primary_task(state)
    secondary = planner_secondary_tasks(state)
    if primary:
        return [primary, *secondary]
    return []


def planner_required_tools(state: AgentState) -> list[dict[str, Any]]:
    value = state.get("required_tools")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def planner_reply_strategy(state: AgentState) -> dict[str, Any]:
    value = state.get("reply_strategy")
    return value if isinstance(value, dict) else {}


def planner_handoff(state: AgentState) -> dict[str, Any]:
    value = state.get("handoff")
    return value if isinstance(value, dict) else {}


def planner_task_views(state: AgentState) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for task in planner_tasks(state)[:3]:
        task_type = str(task.get("type") or "").strip()
        task_subtype = str(task.get("subtype") or "").strip()
        project_name = str(task.get("project_name") or task.get("project") or "").strip()
        answer_goal = str(task.get("answer_goal") or "").strip()
        customer_need = str(task.get("customer_need") or "").strip()
        reply_goal = answer_goal or customer_need
        intent = str(task.get("intent") or task_subtype or task_type or "").strip()
        if not (task_type or intent):
            continue
        views.append(
            {
                "intent": intent,
                "type": task_type,
                "subtype": task_subtype,
                "project_name": project_name,
                "reply_goal": reply_goal,
                "reason": customer_need or answer_goal,
            }
        )
    return views


def planner_project_hints(state: AgentState) -> list[str]:
    hints: list[str] = []
    for item in planner_task_views(state):
        for key in ("project_name", "reply_goal", "reason"):
            value = str(item.get(key) or "").strip()
            if value and value not in hints:
                hints.append(value)
    return hints


def planner_scene(state: AgentState) -> str:
    primary = planner_primary_task(state)
    scene = str(primary.get("scene") or "").strip()
    return scene or "S3_deep_consult"


def planner_public_route(state: AgentState) -> dict[str, Any]:
    primary = planner_primary_task(state)
    handoff = planner_handoff(state)
    task_type = str(primary.get("type") or "").strip()
    subtype = str(primary.get("subtype") or "").strip()
    intent = str(primary.get("intent") or subtype or task_type or "").strip()
    confidence = float(primary.get("confidence") or 0.9) if primary else 0.0
    handoff_needed = bool(handoff.get("needed")) or task_type in {"human_request", "complaint_refund"}
    return {
        "scene": str(primary.get("scene") or "").strip() or "S3_deep_consult",
        "intent": intent,
        "subflow": str(primary.get("subflow") or "").strip() or ("HUMAN_HANDOFF" if handoff_needed else "DIRECT_REPLY"),
        "reason": str(primary.get("answer_goal") or primary.get("customer_need") or "").strip(),
        "confidence": confidence,
        "need_human": handoff_needed,
    }
