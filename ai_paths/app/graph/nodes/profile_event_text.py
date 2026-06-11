from __future__ import annotations

from typing import Any


def event_type_for_intent(intent: str) -> str:
    normalized = str(intent or "").strip()
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
        "appointment_confirm": "appoint_intent",
        "appointment_change": "appoint_intent",
        "appointment_cancel": "appoint_intent",
        "after_sales": "after_sales",
        "competitor_compare": "competitor_compare",
        "campaign_compare": "competitor_compare",
        "human_request": "human_request",
        "complaint_refund": "human_request",
    }.get(normalized, "")


def event_summary(event_type: str, facts: dict[str, Any]) -> str:
    if event_type == "price_inquiry":
        project = facts.get("project") or "相关项目"
        goal = facts.get("customer_goal")
        suffix = f"，关联目标为{goal}" if goal else ""
        return f"客户咨询{project}价格{suffix}。"
    if event_type == "project_inquiry":
        directions = [str(item) for item in facts.get("project_directions") or [] if str(item).strip()]
        if directions:
            return f"客户咨询改善方向，当前可围绕{ '、'.join(directions[:2]) }承接。"
        goal = facts.get("customer_goal")
        if goal:
            return f"客户咨询适合的改善方向，目标是{goal}。"
        return "客户咨询适合的改善方向或可做项目。"
    if event_type == "image_inquiry":
        visible = [str(item) for item in facts.get("visible_concerns") or [] if str(item).strip()]
        if visible:
            return f"客户上传图片咨询，可见问题包括{ '、'.join(visible[:3]) }。"
        return "客户上传图片进行面诊类咨询。"
    if event_type == "trust_issue":
        return "客户表达正规性、收费或服务保障顾虑。"
    if event_type == "store_inquiry":
        city = str(facts.get("city") or "").strip()
        return f"客户咨询{city}门店、地址或路线信息。" if city else "客户咨询门店、地址或路线信息。"
    if event_type == "appoint_intent":
        bits = [
            str(facts.get("preferred_store") or "").strip(),
            str(facts.get("preferred_date") or "").strip(),
            str(facts.get("preferred_time") or "").strip(),
        ]
        detail = " ".join(bit for bit in bits if bit)
        return f"客户表达预约或到店意向，偏好为{detail}。" if detail else "客户表达预约或到店意向。"
    if event_type == "after_sales":
        return "客户咨询售后、恢复或效果反馈问题。"
    if event_type == "competitor_compare":
        return "客户提到竞品、别家报价或外部对比信息。"
    if event_type == "campaign_inquiry":
        return "客户咨询活动、优惠或广告价格信息。"
    if event_type == "human_request":
        return "客户问题需要专业同事协助承接。"
    if event_type == "profile_update":
        goal = str(facts.get("customer_goal") or "").strip()
        visible = [str(item) for item in facts.get("visible_concerns") or [] if str(item).strip()]
        if goal:
            return f"本轮补充客户画像，目标是{goal}。"
        if visible:
            return f"本轮补充客户画像，可见问题包括{ '、'.join(visible[:3]) }。"
        return "本轮补充客户画像信息。"
    return "客户产生新的业务咨询事件。"


def event_impact(event_type: str) -> str:
    return {
        "price_inquiry": "后续回复应承接价格敏感、活动规则和配置解释。",
        "project_inquiry": "后续可围绕需求、图片和适合方向继续沟通。",
        "image_inquiry": "后续应优先使用图片理解结果承接项目和案例。",
        "trust_issue": "后续应优先建立信任，避免强推。",
        "store_inquiry": "后续可继续承接门店推荐、地址和到店路径。",
        "appoint_intent": "后续应确认门店和时间，并检查已有预约。",
        "after_sales": "后续应谨慎收集项目、时间、症状，必要时升级专业协助。",
        "competitor_compare": "后续应避免简单跟价，拆清对比维度。",
        "campaign_inquiry": "后续应核对活动条件和真实价格口径。",
        "profile_update": "后续回复应承接已知需求、预算、城市和图片信息。",
        "human_request": "后续应由专业同事协助处理，避免继续自动承诺。",
    }.get(event_type, "后续客服回复可参考该事件。")
