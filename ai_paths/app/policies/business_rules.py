from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def load_business_rules() -> dict[str, Any]:
    path = Path(__file__).with_name("business_rules.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def business_rules_prompt_section() -> str:
    rules = load_business_rules()
    return json.dumps(rules, ensure_ascii=False, separators=(",", ":"))


def planner_business_rules_prompt_section() -> str:
    """Return compact rule packs for Planner without dropping direct-reply policy."""
    rules = load_business_rules()
    return json.dumps(_planner_rule_packs(rules), ensure_ascii=False, separators=(",", ":"))


def reply_business_rules_for_model(*, stage: str = "", sub_rule_id: str = "") -> dict[str, Any]:
    """Return only the business rules needed by the final reply model."""
    rules = load_business_rules()
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    conversion = rules.get("conversion_psychology") if isinstance(rules.get("conversion_psychology"), dict) else {}
    relevant_stage = _relevant_stage_rules(rules, stage=stage, sub_rule_id=sub_rule_id)
    return {
        "version": rules.get("version"),
        "identity": rules.get("identity") or {},
        "brand_trust_policy": rules.get("brand_trust_policy") or {},
        "offer": {
            "public_names": offer.get("public_names") or [],
            "new_customer_price": offer.get("new_customer_price"),
            "prepay_amount": offer.get("prepay_amount"),
            "tail_amount": offer.get("tail_amount"),
            "refund_rule": offer.get("refund_rule"),
            "includes": offer.get("includes") or [],
            "quota": offer.get("quota"),
            "payment_message_type": offer.get("payment_message_type"),
            "activity_intro_image_url": offer.get("activity_intro_image_url"),
            "case_image_fallback_urls": offer.get("case_image_fallback_urls") or [],
            "activity_intro_image_policy": offer.get("activity_intro_image_policy") or {},
        },
        "conversion_psychology": {
            "goal": conversion.get("goal"),
            "principles": conversion.get("principles") or [],
            "stages": conversion.get("stages") or [],
        },
        "current_stage_rules": relevant_stage,
        "tools": rules.get("tools") or {},
        "forbidden": rules.get("forbidden") or [],
    }


def _relevant_stage_rules(rules: dict[str, Any], *, stage: str, sub_rule_id: str) -> dict[str, Any]:
    stages = rules.get("stages") if isinstance(rules.get("stages"), list) else []
    stage = str(stage or "").strip()
    sub_rule_id = str(sub_rule_id or "").strip()
    for item in stages:
        if not isinstance(item, dict) or str(item.get("id") or "") != stage:
            continue
        rules_list = item.get("rules") if isinstance(item.get("rules"), list) else []
        selected_rules = [
            rule
            for rule in rules_list
            if isinstance(rule, dict) and (not sub_rule_id or str(rule.get("id") or "") == sub_rule_id)
        ]
        if not selected_rules:
            selected_rules = rules_list
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "goal": item.get("goal"),
            "rules": selected_rules,
        }
    return {}


def _planner_rule_packs(rules: dict[str, Any]) -> dict[str, Any]:
    direct_reply_ids = {
        "S1_GREETING",
        "S1_PROJECT_DIRECTION",
        "S1_BRAND_TRUST",
        "S2_CITY_ONLY",
        "S2_PRE_VISIT_TRANSPORT_POLICY",
        "S3_PRICE",
        "S3_PAYMENT_COLLECTION",
        "S4_HESITATION",
    }
    stages = rules.get("stages") if isinstance(rules.get("stages"), list) else []
    scene_catalog: list[dict[str, Any]] = []
    direct_reply_rule_pack: list[dict[str, Any]] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        stage_id = str(stage.get("id") or "")
        stage_goal = str(stage.get("goal") or "")
        for rule in stage.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            rule_id = str(rule.get("id") or "")
            tools = [str(item) for item in (rule.get("tools") or []) if str(item or "").strip()]
            catalog_item = {
                "stage": stage_id,
                "sub_rule_id": rule_id,
                "scene_goal": stage_goal,
                "when_to_use": rule.get("scenes") or [],
                "tools": tools,
                "fact_boundary": _fact_boundary_for_rule(rule_id, tools),
            }
            scene_catalog.append(catalog_item)
            if rule_id in direct_reply_ids:
                direct_reply_rule_pack.append(
                    {
                        **catalog_item,
                        "decision_policy": str(rule.get("decision") or ""),
                        "reply_focus": str(rule.get("reply_focus") or ""),
                    }
                )

    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    conversion = rules.get("conversion_psychology") if isinstance(rules.get("conversion_psychology"), dict) else {}
    return {
        "version": rules.get("version"),
        "identity": rules.get("identity") or {},
        "offer_facts": {
            "public_names": offer.get("public_names") or [],
            "new_customer_price": offer.get("new_customer_price"),
            "prepay_amount": offer.get("prepay_amount"),
            "tail_amount": offer.get("tail_amount"),
            "refund_rule": offer.get("refund_rule"),
            "quota": offer.get("quota"),
            "includes": offer.get("includes") or [],
            "payment_message_type": offer.get("payment_message_type"),
            "activity_intro_image_url": offer.get("activity_intro_image_url"),
            "case_image_fallback_urls": offer.get("case_image_fallback_urls") or [],
            "activity_intro_image_policy": offer.get("activity_intro_image_policy") or {},
        },
        "brand_trust_policy": rules.get("brand_trust_policy") or {},
        "conversion_psychology": {
            "goal": conversion.get("goal"),
            "principles": conversion.get("principles") or [],
            "customer_types": conversion.get("customer_types") or {},
            "stages": conversion.get("stages") or [],
        },
        "direct_reply_style": {
            "tone": "短、直、肯定、有推进；先答当前问题，再推进一个动作。",
            "two_text_policy": "纯文本直回且同时包含回答和下一步时，拆成两条短text；第一条回答，第二条8-25字轻推。",
            "need_tools_transition": "need_tools 的客户可见过渡句只能是“稍等一下哈”。",
        },
        "scene_catalog": scene_catalog,
        "direct_reply_rule_pack": direct_reply_rule_pack,
        "tool_rule_pack": _planner_tool_rule_pack(),
        "forbidden": rules.get("forbidden") or [],
    }


def _fact_boundary_for_rule(rule_id: str, tools: list[str]) -> str:
    if "kb_search(case_studies)" in tools:
        return "案例、效果图必须来自 case_studies 工具事实。"
    if "distance_calculate" in tools:
        return "最近门店排序必须来自 distance_calculate；客户可见回复只说推荐门店，不输出几公里、几分钟或车程。"
    if "customer_store_lookup" in tools:
        return "具体门店、地址、停车、营业时间必须来自 customer_store_lookup。"
    if "available_time" in tools:
        return "真实档期必须来自 available_time；没有结果不能说可约或预约成功。"
    if "appointment_record_query" in tools:
        return "预约记录、改约、取消必须先查 appointment_record_query。"
    if "professional_assist" in tools:
        return "投诉、退款、严重不适、强人工诉求走 professional_assist，并在客户回复后追加 human_handoff_notice。"
    if rule_id == "S3_PRICE":
        return "价格可直接使用 offer_facts：268、10、258、不做退10元、原价1980、名额有限。"
    if rule_id == "S1_BRAND_TRUST":
        return "品牌信任只说集团连锁、全国300多家、斑点和皮肤管理、费用透明；不说企微主体，不编门店名。"
    if rule_id == "S2_PRE_VISIT_TRANSPORT_POLICY":
        return "只能说没有接送服务、交通费用需自理；不承诺报销或包接送。"
    return "能直接回复时使用规则包；涉及事实查询时必须按工具边界处理。"


def _planner_tool_rule_pack() -> list[dict[str, Any]]:
    return [
        {
            "tool": "kb_search",
            "schema": {"name": "kb_search", "kb_name": "case_studies", "query": "<客户案例/效果诉求>"},
            "use_when": "客户要案例、效果图、做完效果参考。",
            "boundary": "未拿到工具结果前不能编案例、次数或效果。",
        },
        {
            "tool": "customer_store_lookup",
            "schema": {"name": "customer_store_lookup", "query": "<结合上下文后的完整城市/区域/地标/门店名>", "purpose": "existence | detail | nearby_candidates"},
            "use_when": "具体城市、区域、门店、地址、停车、营业时间、导航或附近候选。",
            "boundary": "只查当前客户范围门店；query 必须补全上下文，如“厦门市机场”“重庆市渝中区”。",
        },
        {
            "tool": "distance_calculate",
            "schema": {"name": "distance_calculate", "origin": "<客户地标/地址>", "candidate_source": "customer_store_lookup"},
            "use_when": "客户问最近、附近、哪家更方便、几公里或几分钟。",
            "boundary": "必须先有 customer_store_lookup 候选；distance_calculate 只用于排序推荐门店，客户可见回复不输出具体公里、分钟或车程。",
        },
        {
            "tool": "available_time",
            "schema": {"name": "available_time", "store_id": "<门店id>", "date": "<YYYY-MM-DD>"},
            "use_when": "客户问具体门店和日期能不能约、今天/明天/周末是否有时间。",
            "boundary": "store_id/date 缺一不可；没有真实结果不能说可约或预约成功。",
        },
        {
            "tool": "appointment_record_query",
            "schema": {"name": "appointment_record_query"},
            "use_when": "客户问预约状态、确认预约、改约或取消。",
            "boundary": "没有预约事实不能说已经改好、取消或预约成功。",
        },
        {
            "tool": "professional_assist",
            "schema": {"name": "professional_assist", "reason": "<需要协助原因>"},
            "use_when": "投诉、退款、付款纠纷、严重不适、健康高风险、强烈要求真人。",
            "boundary": "客户可见回复直接承接诉求；健康类引导到店检测，纠纷类核对门店/付款/项目，不说转人工或专业同事。",
        },
    ]
