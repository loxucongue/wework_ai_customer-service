from __future__ import annotations

from typing import Callable

from app.graph.nodes.common import dedupe_strings
from app.graph.nodes.image_info import has_image_concern
from app.graph.nodes.memory_usage_policy import should_suppress_profile_memory_for_reply
from app.graph.nodes.profile_update_summary import decision_stage, intent_level, profile_summary
from app.graph.nodes.project_kb_context import case_request_lacks_specific_context
from app.graph.nodes.store_context import extract_city
from app.graph.planner.runtime_plan import planner_task_views
from app.graph.state import AgentState
from app.policies.constants import PROJECT_KEYWORDS


def extract_profile_update(
    state: AgentState,
    *,
    contextual_price_project: Callable[[AgentState], str],
    extract_project: Callable[[str], str],
    known_visible_concerns: Callable[[AgentState], list[str]],
    project_direction_names: Callable[[AgentState], list[str]],
) -> dict[str, object]:
    if should_suppress_profile_memory_for_reply(state):
        return {}

    content = str(state.get("normalized_content") or "")
    image_info = state.get("image_info", {}) or {}
    task_views = planner_task_views(state)
    task_types = _task_type_names(task_views)

    needs: list[str] = []
    pain_points: list[str] = []
    projects: list[str] = []
    concerns: list[str] = []
    style_tags: list[str] = []
    budget_sens = "unknown"

    _collect_need_signals(content, image_info, needs, pain_points, style_tags)
    _collect_project_signals(
        content,
        state,
        contextual_price_project,
        extract_project,
        project_direction_names,
        projects,
    )
    _collect_concern_signals(content, task_types, concerns, style_tags)

    if any(term in content for term in ["预算", "太贵", "贵了", "便宜", "多少钱", "价格"]):
        concerns.append("关注预算")
        style_tags.append("预算敏感")
        budget_sens = "high" if any(term in content for term in ["预算", "太贵", "贵了", "便宜点", "最低价"]) else "medium"

    visible_concerns = known_visible_concerns(state)
    pain_points.extend(str(item).strip() for item in visible_concerns if str(item).strip())

    update: dict[str, object] = {}
    if needs or pain_points or projects or concerns or style_tags:
        update["portrait"] = {
            "summary": profile_summary(needs, pain_points, projects, concerns),
            "needs": dedupe_strings(needs),
            "pain_points": dedupe_strings(pain_points),
            "projects": dedupe_strings(projects),
            "concerns": dedupe_strings(concerns),
            "budget_sens": budget_sens,
            "intent_level": intent_level(task_types, content),
            "trust_level": "low" if _is_trust_task(task_types) else "unknown",
            "decision_stage": decision_stage(task_types, content),
            "style_tags": dedupe_strings(style_tags),
        }

    basic_info = _basic_info_update(content, state)
    if basic_info:
        update["basic_info"] = basic_info
    return update


def _task_type_names(task_views: list[dict[str, object]]) -> set[str]:
    return {
        str(view.get("type") or "").strip()
        for view in task_views
        if isinstance(view, dict) and str(view.get("type") or "").strip()
    }


def _is_trust_task(task_types: set[str]) -> bool:
    return "trust_issue" in task_types


def _collect_need_signals(
    content: str,
    image_info: dict[str, object],
    needs: list[str],
    pain_points: list[str],
    style_tags: list[str],
) -> None:
    if any(term in content for term in ["祛斑", "淡斑", "斑"]):
        needs.extend(["祛斑", "淡斑"])
        pain_points.append("点状斑点" if "点状" in content else "面部斑点")
    if has_image_concern(image_info, ["点状斑点", "点状褐色", "褐色斑点", "斑点", "色沉", "肤色不均"]):
        needs.extend(["祛斑", "淡斑"])
        if has_image_concern(image_info, ["点状斑点", "点状褐色", "褐色斑点"]):
            pain_points.append("点状斑点")
        if has_image_concern(image_info, ["片状", "色沉", "肤色不均"]):
            pain_points.append("面部色沉")
        if not any(point in pain_points for point in ["点状斑点", "面部色沉"]):
            pain_points.append("面部斑点")
    if any(term in content for term in ["色沉", "暗沉"]):
        pain_points.append("面部色沉")
        needs.append("肤色改善")
    if has_image_concern(image_info, ["暗沉", "肤色不均"]):
        pain_points.append("肤色不均")
        needs.append("肤色改善")
    if has_image_concern(image_info, ["毛孔"]) or "毛孔" in content:
        pain_points.append("毛孔明显")
        needs.append("肤质改善")
    if has_image_concern(image_info, ["痘印"]) or "痘印" in content:
        pain_points.append("痘印")
        needs.append("淡化痘印")
    if any(term in content for term in ["出油", "黑头", "闭口"]):
        pain_points.append("毛孔出油问题")
        needs.append("控油毛孔改善")
    if any(term in content for term in ["干", "干燥", "卡粉", "起皮", "补水"]):
        pain_points.append("干燥缺水")
        needs.append("补水修护")
    if any(term in content for term in ["松弛", "法令纹", "抗衰", "下垂", "紧致"]):
        pain_points.append("松弛细纹")
        needs.append("抗衰紧致")
    if any(term in content for term in ["变白", "美白", "提亮", "亮一点"]):
        needs.append("肤色改善")

    for concern in image_info.get("visible_concerns", []) or []:
        normalized = str(concern).strip()
        if normalized and normalized not in pain_points:
            pain_points.append(normalized)
    if image_info.get("has_image"):
        style_tags.append("发图咨询")


def _collect_project_signals(
    content: str,
    state: AgentState,
    contextual_price_project: Callable[[AgentState], str],
    extract_project: Callable[[str], str],
    project_direction_names: Callable[[AgentState], list[str]],
    projects: list[str],
) -> None:
    for project in PROJECT_KEYWORDS:
        if project in content and project not in projects:
            projects.append(project)
    direct_project = contextual_price_project(state) or extract_project(content)
    if direct_project and direct_project not in projects:
        projects.append(direct_project)
    if case_request_lacks_specific_context(state):
        return
    for direction in project_direction_names(state):
        if direction and direction not in projects:
            projects.append(direction)


def _collect_concern_signals(
    content: str,
    task_types: set[str],
    concerns: list[str],
    style_tags: list[str],
) -> None:
    if _is_trust_task(task_types):
        concerns.append("担心正规性或服务保障")
        style_tags.append("谨慎观望")
    if "price_inquiry" in task_types:
        concerns.append("关注价格")
        style_tags.append("直接问价")
    if any(term in content for term in ["有效果吗", "能不能解决", "能改善吗", "解决", "明显变化"]):
        concerns.append("关注改善效果")
    if any(term in content for term in ["疼", "恢复", "反黑", "副作用", "风险"]):
        concerns.append("关注舒适度和恢复风险")
    if "competitor_compare" in task_types:
        style_tags.append("喜欢对比")
    if "appointment_intent" in task_types:
        style_tags.append("有到店意向")
    if any(term in content for term in ["不懂", "不知道", "不专业", "不太懂"]):
        style_tags.append("需要引导")


def _basic_info_update(content: str, state: AgentState) -> dict[str, object]:
    basic_info: dict[str, object] = {}
    city = extract_city(content)
    if city:
        basic_info["city"] = city

    appointment_cache = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    appointment_preference = {
        "store_name": str(appointment_cache.get("store_name") or "").strip(),
        "date": str(appointment_cache.get("date") or appointment_cache.get("appointment_date") or "").strip(),
        "time": str(appointment_cache.get("time") or appointment_cache.get("appointment_time") or "").strip(),
        "people_count": str(appointment_cache.get("people_count") or "").strip(),
    }
    appointment_preference = {key: value for key, value in appointment_preference.items() if value}
    if appointment_preference:
        basic_info["appointment_preference"] = appointment_preference
    return basic_info
