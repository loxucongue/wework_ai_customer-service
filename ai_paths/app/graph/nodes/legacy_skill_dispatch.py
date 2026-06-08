from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.state import AgentState


@dataclass(frozen=True)
class LegacySkillDispatchCallbacks:
    price_skill_output: Callable[[str, dict[str, Any], AgentState], dict[str, Any]]
    trust_skill_output: Callable[[str, dict[str, Any]], dict[str, Any]]
    project_skill_output: Callable[[str, dict[str, Any], AgentState], dict[str, Any]]
    competitor_skill_output: Callable[[str, dict[str, Any]], dict[str, Any]]
    after_sales_skill_output: Callable[[str, dict[str, Any]], dict[str, Any]]
    store_skill_output: Callable[[str, dict[str, Any]], dict[str, Any]]
    basic_skill_output: Callable[..., dict[str, Any]]
    json_dumps: Callable[[Any], str]


def skill_output(
    skill: str,
    content: str,
    tool_results: dict[str, Any],
    state: AgentState,
    callbacks: LegacySkillDispatchCallbacks,
) -> dict[str, Any]:
    if skill == "price_consult":
        return callbacks.price_skill_output(content, tool_results, state)
    if skill == "trust_build":
        return callbacks.trust_skill_output(content, tool_results)
    if skill == "project_consult":
        return callbacks.project_skill_output(content, tool_results, state)
    if skill == "competitor":
        return callbacks.competitor_skill_output(content, tool_results)
    if skill == "after_sales":
        return callbacks.after_sales_skill_output(content, tool_results)
    if skill == "store":
        return callbacks.store_skill_output(content, tool_results)
    if skill == "appointment":
        opening = tool_results.get("appointment_opening") if isinstance(tool_results, dict) else {}
        if isinstance(opening, dict) and opening:
            facts: list[str] = [callbacks.json_dumps({"appointment_opening": _compact_appointment_opening(opening)})]
            status = str(opening.get("status") or "")
            if status in {"created", "dry_run_created"}:
                reply_points = ["预约金订单/预约入口信息已创建，客户可见回复只说明接下来发预约入口并按页面确认，不能说已预约成功或已锁位。"]
                suggested_next_step = "返回appointment_push结构化消息给系统侧发送预约入口"
            elif status == "needs_customer_confirmation":
                reply_points = ["预约开单信息已基本齐全，但客户还没明确确认，需要复述门店、日期、时间和预约金后请客户确认。"]
                suggested_next_step = "等待客户确认是否按这些信息开预约入口"
            else:
                reply_points = ["预约开单未完成或需要门店同事核对，不能承诺已开单或已约好。"]
                suggested_next_step = "让门店同事协助核对预约开单"
            return callbacks.basic_skill_output(
                skill,
                reply_points,
                suggested_next_step=suggested_next_step,
                facts=facts,
            )
        action = tool_results.get("appointment_action") if isinstance(tool_results, dict) else {}
        if isinstance(action, dict) and action and action.get("status") not in {"not_applicable"}:
            facts = [callbacks.json_dumps({"appointment_action": _compact_appointment_action(action)})]
            operation = str(action.get("operation") or "")
            status = str(action.get("status") or "")
            if status in {"scheduled", "dry_run_scheduled"}:
                reply_points = ["排客已处理，客户可见回复只说明已继续帮他往门店时间安排推进，不要说床位一定锁定。"]
                suggested_next_step = "继续承接客户到店安排或提醒后续门店确认"
            elif status in {"changed", "dry_run_changed"}:
                reply_points = ["改约已处理，客户可见回复只说明时间已按新日期继续调整，不要说绝对成功。"]
                suggested_next_step = "继续承接新的到店时间确认"
            elif status in {"cancelled", "dry_run_cancelled"}:
                reply_points = ["取消排客已处理，客户可见回复只说明当前安排已取消，如还想来可重新确认时间。"]
                suggested_next_step = "等待客户是否重新约时间"
            elif status == "preferred_time_unavailable":
                reply_points = ["客户偏好的时间不在可约时段里，必须先解释这个时间暂时没看到，再给当前能选的时间。"]
                suggested_next_step = "继续确认新的可约时间"
            elif status == "missing_info":
                reply_points = ["预约动作还缺关键字段，只追问一个最关键字段，不要假装已经排客或改约成功。"]
                suggested_next_step = "继续收集预约动作缺失字段"
            else:
                reply_points = [f"预约动作{operation or '处理'}未完成，需要按事实状态回复，不要承诺已处理成功。"]
                suggested_next_step = "按当前预约动作状态继续承接"
            return callbacks.basic_skill_output(
                skill,
                reply_points,
                suggested_next_step=suggested_next_step,
                facts=facts,
            )
        active_task = state.get("active_task") or {}
        facts = [callbacks.json_dumps(active_task)] if isinstance(active_task, dict) and active_task else []
        return callbacks.basic_skill_output(
            skill,
            ["预约相关问题必须复用已知门店、日期、时间和人数，继续推进当前预约任务；不要切回项目咨询。"],
            suggested_next_step=str(active_task.get("next_action") or "按当前预约诉求处理") if isinstance(active_task, dict) else "按当前预约诉求处理",
            facts=facts,
        )
    return callbacks.basic_skill_output(skill, ["按客户当前问题做轻量承接。"])


def _compact_appointment_opening(opening: dict[str, Any]) -> dict[str, Any]:
    facts = opening.get("facts") if isinstance(opening.get("facts"), dict) else {}
    return {
        "status": opening.get("status"),
        "order_id": opening.get("order_id") or "",
        "store_id": facts.get("store_id") or "",
        "store_name": facts.get("store_name") or "",
        "appointment_date": facts.get("date") or "",
        "appointment_time": facts.get("time") or "",
        "prepay": facts.get("prepay") or "",
        "missing": opening.get("missing") or [],
        "error": opening.get("error") or "",
    }


def _compact_appointment_action(action: dict[str, Any]) -> dict[str, Any]:
    facts = action.get("facts") if isinstance(action.get("facts"), dict) else {}
    return {
        "operation": action.get("operation") or "",
        "status": action.get("status") or "",
        "reason": action.get("reason") or "",
        "order_id": facts.get("order_id") or "",
        "store_id": facts.get("store_id") or "",
        "store_name": facts.get("store_name") or "",
        "appointment_date": facts.get("date") or "",
        "appointment_time": facts.get("time") or "",
        "available_time_slots": facts.get("available_time_slots") or [],
        "preferred_time_available": facts.get("preferred_time_available"),
        "missing": action.get("missing") or [],
        "error": action.get("error") or "",
    }
