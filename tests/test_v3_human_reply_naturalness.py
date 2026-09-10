from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ai_paths.app.prompts.reply_sales_prompt_v4 import PARALLEL_REPLY_SYSTEM_PROMPT
from ai_paths.scripts.v3_reply_naturalness_cases import CASES
from scripts.v3_reply_sales_opportunity_cases import OPPORTUNITY_CASES
from scripts.evaluate_v3_reply_naturalness import score_result, _representative_probes


def test_naturalness_evaluation_matrix_has_required_coverage() -> None:
    counts = Counter(str(case["category"]) for case in CASES)

    assert len(CASES) == 60
    assert counts == {
        "short_relation": 12,
        "temporary_unavailable": 10,
        "soft_refusal": 12,
        "explicit_action": 12,
        "router_pollution": 8,
        "hard_safety": 6,
    }
    assert sum(bool(case["repeat_probe"]) for case in CASES) >= 20
    assert sum(bool(case["l3"]) for case in CASES) >= 20
    assert all("真实" not in case["id"] and case["current"] for case in CASES)


def test_naturalness_matrix_encodes_safety_and_direct_delivery_contracts() -> None:
    hard = [case for case in CASES if case["hard_safety"]]
    direct = [case for case in CASES if case["required_message_types"]]

    assert len(hard) >= 6
    assert {kind for case in direct for kind in case["required_message_types"]} == {
        "image",
        "store_address",
        "payment_collection",
    }
    assert all(case["expected_actions"] for case in CASES)
    assert all(set(case["expected_actions"]).issubset(set(case["allowed_actions"])) for case in CASES)


def test_prompt_has_no_minimum_length_or_forced_emoji_proxy() -> None:
    assert len(PARALLEL_REPLY_SYSTEM_PROMPT) <= 7_000
    assert "不设最低长度" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "短承接可以只有几个字" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "只有语境自然触发时整轮最多1个轻微信表情" in PARALLEL_REPLY_SYSTEM_PROMPT
    expression = PARALLEL_REPLY_SYSTEM_PROMPT.split("# 5. 真人微信表达", 1)[1].split("# 6.", 1)[0]
    assert "不强制问句、称呼、语气词或表情" in expression
    assert "必须添加表情" not in expression


def test_policy_normal_conversation_covers_keep_open_without_changing_schema() -> None:
    policy_path = Path(__file__).resolve().parents[1] / "ai_paths" / "app" / "policies" / "ai_sales_policy_v2.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    normal = next(item for item in policy["routing"]["business_tasks"] if item["key"] == "normal_conversation")

    assert "短回应" in normal["goal"]
    assert "临时不可交流" in normal["goal"]
    assert "重复暂缓" in normal["goal"]
    assert "keep_open" in normal["goal"]
    assert policy["decision_schema_version"] == "v3_policy_decision_v3"


def test_opportunity_matrix_covers_six_groups_and_requires_visible_delivery() -> None:
    assert Counter(case["opportunity_group"] for case in OPPORTUNITY_CASES) == {
        "opening": 5, "information": 5, "effect": 5, "price": 5, "resolved": 5, "booking": 5,
    }
    assert all("keep_open" not in case["expected_actions"] for case in OPPORTUNITY_CASES)
    assert all(case["required_text_groups"] for case in OPPORTUNITY_CASES)


def test_activity_action_label_does_not_mask_missing_offer_contents() -> None:
    case = next(case for case in OPPORTUNITY_CASES if case["activity_integrity"])
    value = {
        "reply_messages": [{"type": "text", "content": "活动268元，有需要随时找我"}],
        "sales_judgment": {"next_sales_action": {"type": "explain_activity"}},
        "policy_decision": {"closing_decision": {"customer_state": "continue_sales"}},
    }
    score = score_result(case, value)
    assert not score["passed"]
    assert not score["activity_integrity_pass"]
    assert score["passive_close_hits"]
    value["reply_messages"][0]["content"] = "新客268元，含肤况评估、一次面部护理和护理后注意事项指导，需提前预约。"
    assert score_result(case, value)["passed"]


def test_repeat_probes_include_all_six_opportunity_groups() -> None:
    probes = _representative_probes([*CASES, *OPPORTUNITY_CASES])
    assert len(probes) == 20
    assert len({case["id"] for case in probes}) == 20
    assert {case["opportunity_group"] for case in probes if case.get("opportunity_group")} == {
        "opening", "information", "effect", "price", "resolved", "booking",
    }


def test_short_relation_sales_question_is_not_missed_without_activity_keyword() -> None:
    case = CASES[5]
    value = {"reply_messages": [{"type": "text", "content": "哈哈，您脸上的斑是什么情况？"}],
             "sales_judgment": {"next_sales_action": {"type": "ask_missing_fact"}},
             "policy_decision": {"closing_decision": {"customer_state": "continue_sales"}}}
    assert score_result(case, value)["irrelevant_sales_insert"]
