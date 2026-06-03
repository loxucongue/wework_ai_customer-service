from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph import planner_helpers
from app.graph.nodes.common import dedupe_strings
from app.graph.nodes.image_info import has_image_concern
from app.graph.nodes.project_kb_context import case_request_lacks_specific_context
from app.graph.nodes.store_context import extract_city, extract_time_text
from app.graph.state import AgentState
from app.policies.constants import PROJECT_KEYWORDS


@dataclass(frozen=True)
class ProfileExtractionCallbacks:
    canonical_price_project: Callable[[str], str]
    contextual_price_project: Callable[[AgentState], str]
    extract_price_digits: Callable[[str], list[str]]
    extract_project: Callable[[str], str]
    known_visible_concerns: Callable[[AgentState], list[str]]
    project_direction_names: Callable[[AgentState], list[str]]


def extract_profile_update(state: AgentState, callbacks: ProfileExtractionCallbacks) -> dict[str, Any]:
    content = state.get("normalized_content") or ""
    image_info = state.get("image_info", {})
    intents = {item.get("intent") for item in state.get("intents", [])}

    needs: list[str] = []
    pain_points: list[str] = []
    projects: list[str] = []
    concerns: list[str] = []
    style_tags: list[str] = []
    budget_sens = "unknown"

    if "祛斑" in content or "淡斑" in content or "斑" in content:
        needs.extend(["祛斑", "淡斑"])
        pain_points.append("点状斑点" if "点状" in content else "面部斑点")
    if has_image_concern(image_info, ["点状斑", "点状褐色", "褐色斑点", "斑点", "色沉", "肤色不均"]):
        needs.extend(["祛斑", "淡斑"])
        if has_image_concern(image_info, ["点状斑", "点状褐色", "褐色斑点"]):
            pain_points.append("点状斑点")
        if has_image_concern(image_info, ["片状", "色沉", "肤色不均"]):
            pain_points.append("面部色沉")
        if not any(point in pain_points for point in ["点状斑点", "面部色沉"]):
            pain_points.append("面部斑点")
    if "色沉" in content or "暗沉" in content:
        pain_points.append("面部色沉")
        needs.append("肤色改善")
    if has_image_concern(image_info, ["暗沉", "肤色不均"]):
        pain_points.append("肤色不均")
        needs.append("肤色改善")
    if has_image_concern(image_info, ["毛孔"]):
        pain_points.append("毛孔明显")
        needs.append("肤质改善")
    if has_image_concern(image_info, ["痘印"]):
        pain_points.append("痘印")
        needs.append("淡化痘印")
    if "痘印" in content:
        pain_points.append("痘印")
        needs.append("淡化痘印")
    if "毛孔" in content:
        pain_points.append("毛孔明显")
        needs.append("肤质改善")
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
        normalized = str(concern)
        if normalized and normalized not in pain_points:
            pain_points.append(normalized)
    if image_info.get("has_image"):
        style_tags.append("发图咨询")
    for project in PROJECT_KEYWORDS:
        if project in content and project not in projects:
            projects.append(project)
    project_directions = [] if case_request_lacks_specific_context(state, known_visible_concerns_from_state=callbacks.known_visible_concerns) else callbacks.project_direction_names(state)
    for direction in project_directions:
        if direction and direction not in projects:
            projects.append(direction)

    if "trust_issue" in intents:
        concerns.append("担心正规性或服务保障")
        style_tags.append("谨慎观望")
    if "price_inquiry" in intents:
        concerns.append("关注价格")
        style_tags.append("直接问价")
    if any(term in content for term in ["预算", "太贵", "贵了", "贵不贵", "便宜", "多少钱", "价格"]):
        concerns.append("关注预算")
        style_tags.append("预算敏感")
        budget_sens = "high" if any(term in content for term in ["预算", "太贵", "贵了", "便宜点", "别太高"]) else "medium"
    if any(term in content for term in ["有没有效果", "能不能解决", "能改善", "解决", "明显变化"]):
        concerns.append("关注改善效果")
    if any(term in content for term in ["疼", "痛", "恢复", "反黑", "副作用", "风险"]):
        concerns.append("关注舒适度和恢复风险")
    if "competitor_compare" in intents:
        style_tags.append("喜欢对比")
    if "appointment_intent" in intents:
        style_tags.append("有到店意向")
    if any(term in content for term in ["不懂", "不知道", "不专业", "不太懂"]):
        style_tags.append("需要引导")

    update: dict[str, Any] = {}
    if needs or pain_points or projects or concerns or style_tags:
        update["portrait"] = {
            "summary": profile_summary(needs, pain_points, projects, concerns),
            "needs": dedupe_strings(needs),
            "pain_points": dedupe_strings(pain_points),
            "projects": dedupe_strings(projects),
            "concerns": dedupe_strings(concerns),
            "budget_sens": budget_sens,
            "intent_level": intent_level_for_profile(intents, content),
            "trust_level": "low" if "trust_issue" in intents else "unknown",
            "decision_stage": decision_stage_for_profile(intents, content),
            "style_tags": dedupe_strings(style_tags),
        }
    city = extract_city(content)
    basic_info: dict[str, Any] = {}
    if city:
        basic_info["city"] = city
    active_task = state.get("active_task") or {}
    if isinstance(active_task, dict) and active_task.get("type") == "appointment_visit":
        slots = active_task.get("known_slots") if isinstance(active_task.get("known_slots"), dict) else {}
        if slots:
            basic_info["appointment_preference"] = {
                key: value
                for key, value in slots.items()
                if key in {"city", "store_name", "date", "time", "people_count"} and value
            }
    if basic_info:
        update["basic_info"] = basic_info
    return update


def intent_level_for_profile(intents: set[Any], content: str) -> str:
    appointment_signal = bool(intents & {"appointment_confirm", "appointment_intent"}) or any(
        term in content for term in ["想约", "预约", "到店", "过去看看"]
    )
    if appointment_signal and not planner_helpers._is_soft_fee_concern(content):
        return "strong"
    if intents & {"price_inquiry", "campaign_inquiry", "image_inquiry", "project_inquiry", "store_inquiry"}:
        return "medium"
    if intents & {"trust_issue", "competitor_compare"}:
        return "medium"
    return "weak"


def decision_stage_for_profile(intents: set[Any], content: str) -> str:
    appointment_signal = bool(intents & {"appointment_confirm", "appointment_intent"}) or any(
        term in content for term in ["想约", "预约", "到店", "过去看看"]
    )
    if appointment_signal and not planner_helpers._is_soft_fee_concern(content):
        return "考虑到店"
    if intents & {"competitor_compare"}:
        return "对比中"
    if intents & {"price_inquiry", "campaign_inquiry"}:
        return "预算评估中"
    if intents & {"image_inquiry"}:
        return "看图评估中"
    return "了解中"


def extract_event_updates(
    state: AgentState,
    profile_update: dict[str, Any],
    callbacks: ProfileExtractionCallbacks,
) -> list[dict[str, Any]]:
    content = state.get("normalized_content") or ""
    intents = state.get("intents", [])
    if not intents and not profile_update:
        return []

    events: list[dict[str, Any]] = []
    for index, item in enumerate(intents[:3], start=1):
        event_type = event_type_for_intent(str(item.get("intent")))
        if not event_type:
            continue
        facts = event_facts(event_type, content, state, callbacks)
        events.append(
            {
                "event_id": f"evt_{state.get('request_id', 'unknown')}_{index}",
                "event_time": "",
                "event_type": event_type,
                "stage": state.get("route_result", {}).get("scene", "S3_deep_consult"),
                "summary": event_summary(event_type, facts),
                "facts": facts,
                "impact": event_impact(event_type),
                "confidence": 0.78,
            }
        )
    if profile_update and not events:
        facts = event_common_facts(content, state, callbacks)
        events.append(
            {
                "event_id": f"evt_{state.get('request_id', 'unknown')}_profile",
                "event_time": "",
                "event_type": "profile_update",
                "stage": state.get("route_result", {}).get("scene", "S3_deep_consult"),
                "summary": event_summary("profile_update", facts),
                "facts": facts,
                "impact": "补充客户画像，后续回复应承接已知需求和顾虑。",
                "confidence": 0.72,
            }
        )
    return events


def event_facts(
    event_type: str,
    content: str,
    state: AgentState,
    callbacks: ProfileExtractionCallbacks,
) -> dict[str, Any]:
    image_info = state.get("image_info", {})
    project = callbacks.extract_project(content)
    common = event_common_facts(content, state, callbacks)
    if event_type == "price_inquiry":
        return {
            **common,
            "project": callbacks.canonical_price_project(callbacks.contextual_price_project(state) or project),
            "price_focus": "价格咨询",
            "budget_sens": "high" if any(term in content for term in ["贵", "预算", "便宜", "太高"]) else "unknown",
            "seen_price": callbacks.extract_price_digits(content)[:3],
        }
    if event_type == "project_inquiry":
        return {
            **common,
            "project": project,
            "question_focus": "项目方向",
            "visible_concerns": image_info.get("visible_concerns", []),
            "image_desc": image_info.get("image_desc", ""),
            "project_directions": []
            if case_request_lacks_specific_context(state, known_visible_concerns_from_state=callbacks.known_visible_concerns)
            else callbacks.project_direction_names(state),
        }
    if event_type == "image_inquiry":
        return {
            **common,
            "image_type": image_info.get("image_type", ""),
            "image_intent": image_info.get("image_intent", ""),
            "body_part": image_info.get("body_part", ""),
            "visible_concerns": image_info.get("visible_concerns", []),
            "text_clues": image_info.get("text_clues", []),
        }
    if event_type == "trust_issue":
        return {**common, "concern": "正规性/服务保障", "trust_level": "low"}
    if event_type == "store_inquiry":
        return {**common, "city": extract_city(content), "location_focus": "门店/地址/路线", "matched_stores": matched_store_names(state)}
    if event_type == "appoint_intent":
        return {
            **common,
            "intent_level": "medium",
            "preferred_time": extract_time_text(content) or active_task_slot(state, "time"),
            "preferred_store": active_task_slot(state, "store_name"),
            "preferred_date": active_task_slot(state, "date"),
            "people_count": active_task_slot(state, "people_count"),
        }
    if event_type == "after_sales":
        return {**common, "issue": "售后/恢复咨询", "severity": "unknown"}
    if event_type == "competitor_compare":
        return {**common, "compare_focus": "竞品/报价对比"}
    if event_type == "campaign_inquiry":
        return {**common, "campaign_focus": "活动/优惠咨询", "seen_price": callbacks.extract_price_digits(content)[:3]}
    if event_type == "human_request":
        return {**common, "request": "需要专业人士协助"}
    return common


def event_common_facts(
    content: str,
    state: AgentState,
    callbacks: ProfileExtractionCallbacks,
) -> dict[str, Any]:
    image_info = state.get("image_info", {}) or {}
    facts: dict[str, Any] = {}
    city = extract_city(content)
    if city:
        facts["city"] = city
    visible = image_info.get("visible_concerns") or callbacks.known_visible_concerns(state)
    if visible:
        facts["visible_concerns"] = list(visible[:6])
    directions = callbacks.project_direction_names(state)
    if directions:
        facts["project_directions"] = directions[:3]
    customer_goal = customer_goal_from_content(content)
    if customer_goal:
        facts["customer_goal"] = customer_goal
    if any(term in content for term in ["预算", "贵", "便宜", "多少钱", "价格"]):
        facts["budget_or_price_signal"] = content[:80]
    return facts


def customer_goal_from_content(content: str) -> str:
    if any(term in content for term in ["斑", "色沉", "肤色不均"]):
        return "改善斑点/色沉/肤色不均"
    if any(term in content for term in ["毛孔", "出油", "黑头"]):
        return "改善毛孔出油"
    if any(term in content for term in ["痘印", "痘坑", "闭口"]):
        return "改善痘印痘坑/闭口"
    if any(term in content for term in ["暗沉", "提亮", "美白", "变白"]):
        return "提亮肤色"
    if any(term in content for term in ["松弛", "法令纹", "抗衰"]):
        return "抗衰紧致"
    return ""


def active_task_slot(state: AgentState, key: str) -> str:
    active_task = state.get("active_task") or {}
    if not isinstance(active_task, dict):
        return ""
    slots = active_task.get("known_slots")
    if not isinstance(slots, dict):
        return ""
    return str(slots.get(key) or "").strip()


def matched_store_names(state: AgentState) -> list[str]:
    lookup = (state.get("tool_results") or {}).get("store_lookup") or {}
    stores = lookup.get("stores") if isinstance(lookup, dict) else []
    result: list[str] = []
    for store in stores if isinstance(stores, list) else []:
        if isinstance(store, dict):
            name = str(store.get("name") or "").strip()
            if name and name not in result:
                result.append(name)
    return result[:5]


def event_type_for_intent(intent: str) -> str:
    return {
        "price_inquiry": "price_inquiry",
        "ad_price_check": "price_inquiry",
        "campaign_inquiry": "campaign_inquiry",
        "project_inquiry": "project_inquiry",
        "case_request": "project_inquiry",
        "project_process": "project_inquiry",
        "image_inquiry": "image_inquiry",
        "trust_issue": "trust_issue",
        "store_inquiry": "store_inquiry",
        "appointment_intent": "appoint_intent",
        "after_sales": "after_sales",
        "competitor_compare": "competitor_compare",
        "human_request": "human_request",
    }.get(intent, "")


def event_summary(event_type: str, facts: dict[str, Any]) -> str:
    if event_type == "price_inquiry":
        project = facts.get("project") or "项目"
        goal = facts.get("customer_goal")
        suffix = f"，关联目标为{goal}" if goal else ""
        return f"客户咨询{project}价格{suffix}。"
    if event_type == "project_inquiry":
        directions = facts.get("project_directions") or []
        if directions:
            return f"客户咨询项目方向，当前可围绕{'、'.join(map(str, directions[:2]))}承接。"
        goal = facts.get("customer_goal")
        return f"客户咨询项目方向或适合项目{f'，目标是{goal}' if goal else ''}。"
    if event_type == "image_inquiry":
        visible = facts.get("visible_concerns") or []
        if visible:
            return f"客户上传图片咨询，可见/提到{'、'.join(map(str, visible[:3]))}。"
        return "客户上传图片进行面诊类咨询。"
    if event_type == "trust_issue":
        return "客户表达正规性或服务保障顾虑。"
    if event_type == "store_inquiry":
        city = facts.get("city")
        return f"客户咨询{city or ''}门店、地址或路线信息。"
    if event_type == "appoint_intent":
        bits = [str(facts.get("preferred_store") or ""), str(facts.get("preferred_date") or ""), str(facts.get("preferred_time") or "")]
        detail = " ".join(bit for bit in bits if bit)
        return f"客户表达预约或到店意向{f'：{detail}' if detail else ''}。"
    if event_type == "after_sales":
        return "客户咨询售后或恢复相关问题。"
    if event_type == "competitor_compare":
        return "客户提到竞品或外部报价对比。"
    if event_type == "campaign_inquiry":
        return "客户咨询活动、优惠或广告价。"
    if event_type == "human_request":
        return "客户问题需要专业人士协助承接。"
    if event_type == "profile_update":
        goal = facts.get("customer_goal")
        visible = facts.get("visible_concerns") or []
        if goal:
            return f"本轮补充客户画像：{goal}。"
        if visible:
            return f"本轮补充客户画像：{'、'.join(map(str, visible[:3]))}。"
        return "本轮补充客户画像信息。"
    return "客户产生新的业务咨询事件。"


def event_impact(event_type: str) -> str:
    return {
        "price_inquiry": "后续回复应承接价格敏感和项目配置说明。",
        "project_inquiry": "后续可围绕需求、照片和适合项目继续沟通。",
        "image_inquiry": "后续项目和画像节点应优先使用图片理解结果。",
        "trust_issue": "后续应优先建立信任，避免强推。",
        "store_inquiry": "后续可继续承接门店和到店路径。",
        "appoint_intent": "后续应确认门店和时间，并检查已有预约。",
        "after_sales": "后续应谨慎收集项目、时间、症状，必要时升级专业人士。",
        "competitor_compare": "后续应不跟价不诋毁，拆清对比维度。",
        "campaign_inquiry": "后续应核对活动条件和真实价格口径。",
        "profile_update": "后续回复应承接已知需求、预算、城市和图片信息。",
        "human_request": "后续由专业人士协助处理，避免继续自动承诺。",
    }.get(event_type, "后续客服回复可参考该事件。")


def profile_summary(needs: list[str], pain_points: list[str], projects: list[str], concerns: list[str]) -> str:
    parts = []
    if pain_points:
        parts.append(f"关注{ '、'.join(dedupe_strings(pain_points)[:3]) }")
    if needs:
        parts.append(f"希望{ '、'.join(dedupe_strings(needs)[:3]) }")
    if projects:
        parts.append(f"提到项目{ '、'.join(dedupe_strings(projects)[:3]) }")
    if concerns:
        parts.append(f"顾虑{ '、'.join(dedupe_strings(concerns)[:2]) }")
    return "，".join(parts) + "。" if parts else ""
