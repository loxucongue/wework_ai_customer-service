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
    """Return stable business facts; scenario semantics stay in the Planner prompt."""
    return json.dumps(_planner_runtime_rules(load_business_rules()), ensure_ascii=False, separators=(",", ":"))


def reply_business_rules_for_model(*, stage: str = "", sub_rule_id: str = "") -> dict[str, Any]:
    """Return current-turn facts without duplicating the Reply system contract."""
    rules = load_business_rules()
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    return {
        "version": rules.get("version"),
        "identity_facts": _selected_dict_fields(
            rules.get("identity"),
            ("public_role", "style", "goal"),
        ),
        "brand_trust_facts": _selected_dict_fields(
            rules.get("brand_trust_policy"),
            ("allowed_points", "forbidden_points"),
        ),
        "offer_facts": _offer_facts(offer),
        "current_stage_rules": _relevant_stage_rules(rules, stage=stage, sub_rule_id=sub_rule_id),
    }


def _planner_runtime_rules(rules: dict[str, Any]) -> dict[str, Any]:
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    conversion = rules.get("conversion_psychology") if isinstance(rules.get("conversion_psychology"), dict) else {}
    return {
        "version": rules.get("version"),
        "identity_facts": _selected_dict_fields(rules.get("identity"), ("public_role", "style", "goal")),
        "brand_trust_facts": _selected_dict_fields(
            rules.get("brand_trust_policy"),
            ("allowed_points", "forbidden_points"),
        ),
        "offer_facts": _offer_facts(offer),
        "conversion_goal": conversion.get("goal"),
        "hard_forbidden": rules.get("forbidden") or [],
    }


def _offer_facts(offer: dict[str, Any]) -> dict[str, Any]:
    return {
        "new_customer_price": offer.get("new_customer_price"),
        "prepay_amount": offer.get("prepay_amount"),
        "tail_amount": offer.get("tail_amount"),
        "refund_rule": offer.get("refund_rule"),
        "arrival_time_rule": offer.get("arrival_time_rule"),
        "includes": offer.get("includes") or [],
        "quota": offer.get("quota"),
    }


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
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "goal": item.get("goal"),
            "rules": [
                {
                    "id": rule.get("id"),
                    "decision": rule.get("decision"),
                    "reply_focus": rule.get("reply_focus"),
                }
                for rule in selected_rules
                if isinstance(rule, dict)
            ],
        }
    return {}
