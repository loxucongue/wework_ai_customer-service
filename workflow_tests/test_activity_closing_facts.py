from __future__ import annotations

import json

from app.policies.business_rules import (
    planner_business_rules_prompt_section,
    reply_business_rules_for_model,
)
from app.policies.s10_offer import ACTIVE_S10_OFFER_CONTEXT, s10_offer_prompt_section
from app.prompts.global_contract import GLOBAL_BUSINESS_RHYTHM_CONTRACT


def test_activity_closing_facts_reach_planner_and_reply() -> None:
    planner_facts = json.loads(planner_business_rules_prompt_section())
    reply_facts = reply_business_rules_for_model(stage="S3", sub_rule_id="S3_PAYMENT_COLLECTION")
    expected_gift = {
        "name": "美白管理",
        "stated_value": 180,
        "eligibility": "当前登记预约客户",
        "customer_visible_fact": "当前登记预约赠送价值180元的美白管理",
        "boundary": "只能按当前活动事实说明赠送价值180元的美白管理，不得扩展为其他赠品、现金抵扣、指定老师服务或永久有效",
    }

    assert planner_facts["offer_facts"]["registration_gift"] == expected_gift
    assert reply_facts["offer_facts"]["registration_gift"] == expected_gift
    assert "当前登记预约赠送价值180元的美白管理" in planner_facts["offer_facts"][
        "approved_closing_reasons"
    ]
    assert reply_facts["offer_facts"]["approved_closing_reasons"] == planner_facts["offer_facts"][
        "approved_closing_reasons"
    ]
    assert "不得新增赠品" in planner_facts["offer_facts"]["closing_reason_policy"]
    assert "offer_facts.approved_closing_reasons" in GLOBAL_BUSINESS_RHYTHM_CONTRACT


def test_legacy_unapproved_gift_is_removed_from_runtime_offer() -> None:
    context_text = json.dumps(ACTIVE_S10_OFFER_CONTEXT, ensure_ascii=False)
    prompt = s10_offer_prompt_section()

    assert ACTIVE_S10_OFFER_CONTEXT["registration_gift"]["stated_value"] == 180
    assert "价值180元" in ACTIVE_S10_OFFER_CONTEXT["hard_close_benefit"]
    assert "280元小气泡" not in context_text
    assert "280元小气泡" not in prompt
    assert "价值 180 元的美白管理" in prompt
