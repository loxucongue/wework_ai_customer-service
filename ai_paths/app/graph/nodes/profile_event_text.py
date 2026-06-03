from __future__ import annotations

from typing import Any


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
        city = facts.get("city") or ""
        return f"客户咨询{city}门店、地址或路线信息。"
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
