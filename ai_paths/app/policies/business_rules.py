from __future__ import annotations

import json
from copy import deepcopy
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
    """Return the current business rule pack used by Planner at runtime."""
    return json.dumps(_planner_runtime_rules(load_business_rules()), ensure_ascii=False, separators=(",", ":"))


def planner_recovery_business_rules_prompt_section() -> str:
    """Return a compact but complete routing index for timeout recovery."""
    rules = load_business_rules()
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    return json.dumps(
        {
            "version": rules.get("version"),
            "offer_facts": _offer_facts(offer),
            "store_address_disclosure_policy": rules.get("store_address_disclosure_policy") or {},
            "transaction_policy": rules.get("transaction_policy") or {},
            "scene_index": [
                {
                    "stage": item.get("stage"),
                    "id": item.get("id"),
                    "scenes": item.get("scenes") or [],
                    "decision": item.get("decision"),
                    "tools": item.get("tools") or [],
                    "fact_boundary": item.get("fact_boundary"),
                }
                for item in _scene_catalog(rules)
            ],
            "hard_forbidden": rules.get("forbidden") or [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def reply_business_rules_for_model(*, stage: str = "", sub_rule_id: str = "") -> dict[str, Any]:
    """Return the current-stage rules and shared facts needed by Reply."""
    rules = load_business_rules()
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    current_stage_rules = _relevant_stage_rules(rules, stage=stage, sub_rule_id=sub_rule_id)
    return {
        "version": rules.get("version"),
        "rule_taxonomy": _rule_taxonomy(rules),
        "rule_layers": _rule_layers_for_model(
            rules,
            current_rules=current_stage_rules.get("rules") if isinstance(current_stage_rules, dict) else [],
        ),
        "identity_facts": _selected_dict_fields(
            rules.get("identity"),
            ("public_role", "style", "goal"),
        ),
        "brand_trust_facts": _selected_dict_fields(
            rules.get("brand_trust_policy"),
            ("allowed_points", "forbidden_points"),
        ),
        "offer_facts": _offer_facts(offer),
        "store_address_disclosure_policy": rules.get("store_address_disclosure_policy") or {},
        "customer_visible_evidence_policy": rules.get("customer_visible_evidence_policy") or {},
        "transaction_policy": rules.get("transaction_policy") or {},
        "conversion_psychology": _conversion_psychology(rules),
        "current_stage_rules": current_stage_rules,
        "tool_policy": _tool_policy(rules),
        "hard_forbidden": rules.get("forbidden") or [],
    }


def parallel_reply_business_rules_for_model() -> dict[str, Any]:
    """Return principles and facts, never the legacy scene/stage playbook."""

    rules = load_business_rules()
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    transaction = rules.get("transaction_policy") if isinstance(rules.get("transaction_policy"), dict) else {}
    return {
        "version": rules.get("version"),
        "MUST FOLLOW": {
            "hard_forbidden": rules.get("forbidden") or [],
            "payment_hard_blocks": transaction.get("payment_hard_blocks") or [],
            "validation_owner": "code validates only facts, structure, permissions, arithmetic, idempotency and explicit model assessments",
        },
        "AUTHORITATIVE FACTS": {
            "identity": _selected_dict_fields(rules.get("identity"), ("public_role", "style", "goal")),
            "brand_trust": _selected_dict_fields(
                rules.get("brand_trust_policy"),
                ("allowed_points", "forbidden_points"),
            ),
            "offer": _offer_facts(offer),
            "health_risk_policy": rules.get("health_risk_policy") or {},
            "store_address_disclosure_policy": rules.get("store_address_disclosure_policy") or {},
            "customer_charge_policy": rules.get("customer_charge_policy") or {},
            "customer_visible_evidence_policy": rules.get("customer_visible_evidence_policy") or {},
            "transaction_policy": transaction,
        },
        "SALES PRINCIPLES": {
            "source": "shared_context.sales_guidance.principles",
            "scope": "V2 ordinary reply only",
            "runtime_contract": "high-level distilled reasoning only; no raw source replies or scene matching",
        },
        "CONTENT ASSET POLICY": {
            "gate_candidates_are_optional_evidence": True,
            "adaptable_text_may_be_rewritten": True,
            "structured_media_and_facts_must_not_be_invented_or_mutated": True,
            "precision_examples_are_offline_only": True,
        },
        "TOOL FACT BOUNDARIES": _tool_policy(rules).get("boundaries") or {},
    }


def _model_led_sales_principles(rules: dict[str, Any]) -> dict[str, Any]:
    configured = rules.get("model_led_sales_principles")
    return deepcopy(configured) if isinstance(configured, dict) else {}


def outreach_business_facts_for_model() -> dict[str, Any]:
    """Return the authoritative facts needed by personalized outreach."""
    rules = load_business_rules()
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    return {
        "identity": _selected_dict_fields(
            rules.get("identity"),
            ("public_role", "style", "goal"),
        ),
        "brand_trust": _selected_dict_fields(
            rules.get("brand_trust_policy"),
            ("allowed_points", "forbidden_points"),
        ),
        "offer": _offer_facts(offer),
        "outreach_knowledge_facts": rules.get("outreach_knowledge_facts") or {},
        "transaction_policy": rules.get("transaction_policy") or {},
        "hard_forbidden": rules.get("forbidden") or [],
    }


def sop_platform_business_facts_for_model() -> dict[str, Any]:
    """Return the compact authoritative fallback facts for third-party SOP copy."""
    rules = load_business_rules()
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    return {
        "version": rules.get("version"),
        "identity": _selected_dict_fields(
            rules.get("identity"),
            ("public_role", "style", "goal"),
        ),
        "brand_trust": _selected_dict_fields(
            rules.get("brand_trust_policy"),
            ("allowed_points", "forbidden_points"),
        ),
        "offer": _offer_facts(offer),
        "transaction_policy": rules.get("transaction_policy") or {},
        "hard_forbidden": rules.get("forbidden") or [],
    }


def _planner_runtime_rules(rules: dict[str, Any]) -> dict[str, Any]:
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    scene_catalog = _scene_catalog(rules)
    return {
        "version": rules.get("version"),
        "rule_taxonomy": _rule_taxonomy(rules),
        "rule_layers": _rule_layers_for_model(rules, current_rules=scene_catalog),
        "identity_facts": _selected_dict_fields(rules.get("identity"), ("public_role", "style", "goal")),
        "brand_trust_facts": _selected_dict_fields(
            rules.get("brand_trust_policy"),
            ("allowed_points", "forbidden_points"),
        ),
        "offer_facts": _offer_facts(offer),
        "store_address_disclosure_policy": rules.get("store_address_disclosure_policy") or {},
        "customer_visible_evidence_policy": rules.get("customer_visible_evidence_policy") or {},
        "transaction_policy": rules.get("transaction_policy") or {},
        "conversion_psychology": _conversion_psychology(rules),
        "scene_catalog": scene_catalog,
        "tool_policy": _tool_policy(rules),
        "hard_forbidden": rules.get("forbidden") or [],
    }


def _rule_taxonomy(rules: dict[str, Any]) -> dict[str, Any]:
    taxonomy = rules.get("rule_taxonomy")
    if isinstance(taxonomy, dict):
        return taxonomy
    return {
        "levels": {
            "hard_law": "Absolute safety, factual, structural, and compliance boundaries. Code may hard-check these.",
            "business_fact": "Authoritative business facts. The model must not contradict them, but decides when they matter.",
            "sales_principle": "General reasoning principle owned by the model and evaluated semantically.",
            "content_asset": "Approved facts and media that Gate may nominate and Reply may adapt or ignore.",
            "deprecated": "Superseded historical rule kept only for migration audit and never rendered into prompts.",
        },
        "validation_modes": {
            "hard_law": "hard_check",
            "business_fact": "fact_consistency",
            "sales_principle": "semantic_review",
            "content_asset": "asset_integrity",
            "deprecated": "audit_only",
        },
    }


def _rule_layers_for_model(rules: dict[str, Any], *, current_rules: list[dict[str, Any]]) -> dict[str, Any]:
    current_rules = current_rules or []
    hard_rules = [
        item
        for item in current_rules
        if isinstance(item, dict) and str(item.get("rule_level") or "") == "hard_law"
    ]
    business_rules = [
        item
        for item in current_rules
        if isinstance(item, dict) and str(item.get("rule_level") or "") == "business_fact"
    ]
    principle_rules = [
        item
        for item in current_rules
        if isinstance(item, dict) and str(item.get("rule_level") or "") == "sales_principle"
    ]
    content_asset_rules = [
        item
        for item in current_rules
        if isinstance(item, dict) and str(item.get("rule_level") or "") == "content_asset"
    ]
    return {
        "MUST FOLLOW": {
            "description": "Hard boundaries. Never violate; code may also enforce them.",
            "hard_forbidden": rules.get("forbidden") or [],
            "hard_rule_ids": [item.get("id") for item in hard_rules if item.get("id")],
        },
        "AUTHORITATIVE FACTS": {
            "description": "Business facts are true constraints, but the model decides whether the current reply should mention them.",
            "business_fact_rule_ids": [item.get("id") for item in business_rules if item.get("id")],
            "offer": _offer_facts(rules.get("offer") if isinstance(rules.get("offer"), dict) else {}),
            "store_address_disclosure_policy": rules.get("store_address_disclosure_policy") or {},
            "transaction_policy": rules.get("transaction_policy") or {},
        },
        "SALES PRINCIPLES": {
            "description": "Model-owned reasoning guidance. These rules never become Python hard failures.",
            "sales_principle_rule_ids": [item.get("id") for item in principle_rules if item.get("id")],
            "model_led_sales_principles": _model_led_sales_principles(rules),
        },
        "CONTENT ASSETS": {
            "description": "Gate-nominated facts and media only; Reply decides whether and how to use them.",
            "content_asset_rule_ids": [item.get("id") for item in content_asset_rules if item.get("id")],
        },
    }


def _offer_facts(offer: dict[str, Any]) -> dict[str, Any]:
    return {
        "public_names": offer.get("public_names") or [],
        "new_customer_price": offer.get("new_customer_price"),
        "original_price": offer.get("original_price"),
        "original_price_visibility": offer.get("original_price_visibility"),
        "prepay_amount": offer.get("prepay_amount"),
        "tail_amount": offer.get("tail_amount"),
        "refund_rule": offer.get("refund_rule"),
        "arrival_time_rule": offer.get("arrival_time_rule"),
        "reservation_completion_rule": offer.get("reservation_completion_rule"),
        "registration_skin_test": offer.get("registration_skin_test"),
        "service_duration": offer.get("service_duration"),
        "daily_life_impact": offer.get("daily_life_impact"),
        "registered_visit_option": offer.get("registered_visit_option"),
        "action_cost_fact_policy": offer.get("action_cost_fact_policy"),
        "body_scope": offer.get("body_scope"),
        "includes": offer.get("includes") or [],
        "supported_online_scope": offer.get("supported_online_scope") or [],
        "unsupported_online_projects": offer.get("unsupported_online_projects") or [],
        "scope_answer_policy": offer.get("scope_answer_policy"),
        "quota": offer.get("quota"),
        "registration_gift": offer.get("registration_gift") or {},
        "scarcity_reasons": offer.get("scarcity_reasons") or [],
        "approved_closing_reasons": offer.get("approved_closing_reasons") or [],
        "closing_reason_policy": offer.get("closing_reason_policy"),
        "payment_message_type": offer.get("payment_message_type"),
    }


def _conversion_psychology(rules: dict[str, Any]) -> dict[str, Any]:
    conversion = rules.get("conversion_psychology")
    if not isinstance(conversion, dict):
        return {}
    return _selected_dict_fields(
        conversion,
        ("goal", "principles", "customer_types", "stages"),
    )


def _rule_metadata(rule: dict[str, Any]) -> dict[str, Any]:
    """Read explicit ownership metadata; never infer sales semantics in Python."""

    return {
        "rule_level": str(rule.get("rule_level") or "deprecated"),
        "owner": str(rule.get("owner") or "none"),
        "validation_mode": str(rule.get("validation_mode") or "audit_only"),
        "source": str(rule.get("source") or ""),
        "regression_ids": [str(item) for item in rule.get("regression_ids") or [] if str(item).strip()],
    }


def _scene_catalog(rules: dict[str, Any]) -> list[dict[str, Any]]:
    transaction = rules.get("transaction_policy") if isinstance(rules.get("transaction_policy"), dict) else {}
    stages = rules.get("stages") if isinstance(rules.get("stages"), list) else []
    output: list[dict[str, Any]] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        for rule in stage.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            tools = [str(item) for item in rule.get("tools") or [] if str(item or "").strip()]
            metadata = _rule_metadata(rule)
            output.append(
                {
                    "stage": stage.get("id"),
                    "stage_name": stage.get("name"),
                    "stage_goal": stage.get("goal"),
                    "id": rule.get("id"),
                    "scenes": rule.get("scenes") or [],
                    "decision": rule.get("decision"),
                    "tools": tools,
                    "reply_focus": rule.get("reply_focus"),
                    "fact_boundary": _fact_boundary_for_rule(
                        str(rule.get("id") or ""),
                        tools,
                        transaction_policy=transaction,
                    ),
                    **metadata,
                }
            )
    return output


def _tool_policy(rules: dict[str, Any]) -> dict[str, Any]:
    configured = rules.get("tools") if isinstance(rules.get("tools"), dict) else {}
    transaction = rules.get("transaction_policy") if isinstance(rules.get("transaction_policy"), dict) else {}
    return {
        "configured": configured,
        "boundaries": {
            "kb_search": "案例和效果图只来自本轮真实 case_studies 结果。",
            "customer_store_lookup": "保留当前消息和近期已确认地址证据；具体门店、地址、停车和营业时间只来自真实结果。",
            "distance_calculate": "仅用合法经纬度执行 Haversine 直线距离排序；客户可见不输出公里、分钟、车程或路线。",
            "appointment_record_query": "只查既有预约；无记录不能承诺改约、取消或已安排。",
            "create_work_order": str(transaction.get("payment_order_policy_description") or "订单和开单不作为发卡前置；支付后再收姓名电话并尝试后台关联。"),
            "add_customer_mobile": "只同步客户明确提供的完整11位手机号；失败不能让回复为空。",
            "available_time/create_order_plan": str(transaction.get("post_paid_flow_description") or "当前普通流程不查询档期、不创建排客。"),
            "professional_assist": "只处理当前明确风险、投诉、退款、付款异常或强人工诉求；旧风险不得劫持普通问题。",
        },
    }


def _fact_boundary_for_rule(
    rule_id: str,
    tools: list[str],
    *,
    transaction_policy: dict[str, Any],
) -> str:
    if "kb_search(case_studies)" in tools:
        return "案例与效果图必须来自真实 case_studies 工具事实；旧 SOP 完成状态不能替代近期真实发图证据。"
    if "distance_calculate" in tools:
        return "门店存在性和详情来自 customer_store_lookup；距离工具只按合法经纬度执行 Haversine 排序。最终只按 store_resolution_fact.status 和 delivery_store_ids 发卡，不得自行增减；客户可见不输出公里、分钟、车程或路线。"
    if "customer_store_lookup" in tools:
        return "具体门店、地址、停车和营业时间必须来自 customer_store_lookup。省或城市信息不足时补问；POI 推断出的上级行政区必须先确认；完整省市区、详细地址或定位卡才可直接匹配。"
    if "appointment_record_query" in tools:
        return "既有预约、改约和取消必须以 appointment_record_query 的真实结果为准。"
    if "professional_assist" in tools:
        return "只根据当前明确风险或纠纷事实调用 professional_assist；客户文本仍需正面回答并追加内部 notice。"
    if rule_id == "S3_PRICE":
        return "价格、预约金、尾款、退款和名额事实只能使用 offer_facts 当前口径。"
    if rule_id == "S3_PAYMENT_COLLECTION":
        return str(transaction_policy.get("payment_order_policy_description") or "活动报价已完成或已铺垫后可按成交节奏发卡；订单和开单不作为发卡前置。")
    if rule_id == "S3_APPOINTMENT_TIME":
        return str(transaction_policy.get("post_paid_flow_description") or "当前普通流程只登记到店意向。")
    if rule_id == "S1_BRAND_TRUST":
        return "只能使用 brand_trust_facts；不输出企微主体，不编具体门店名。"
    if rule_id == "S2_PRE_VISIT_TRANSPORT_POLICY":
        return "只能说明没有接送服务、交通费用需自理；不承诺报销或包接送。"
    return "可直接回答规则口径；任何门店、案例、订单、支付、预约或风险事实仍以结构化输入和工具结果为准。"


def _selected_dict_fields(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value.get(key)
        for key in keys
        if value.get(key) not in (None, "", [], {})
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
        transaction = rules.get("transaction_policy") if isinstance(rules.get("transaction_policy"), dict) else {}
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "goal": item.get("goal"),
            "rules": [
                _stage_rule_for_model(rule, transaction_policy=transaction)
                for rule in selected_rules
                if isinstance(rule, dict)
            ],
        }
    return {}


def _stage_rule_for_model(rule: dict[str, Any], *, transaction_policy: dict[str, Any]) -> dict[str, Any]:
    tools = [str(tool) for tool in rule.get("tools") or [] if str(tool or "").strip()]
    rule_id = str(rule.get("id") or "")
    return {
        "id": rule.get("id"),
        "scenes": rule.get("scenes") or [],
        "decision": rule.get("decision"),
        "tools": tools,
        "reply_focus": rule.get("reply_focus"),
        "fact_boundary": _fact_boundary_for_rule(
            rule_id,
            tools,
            transaction_policy=transaction_policy,
        ),
        **_rule_metadata(rule),
    }
