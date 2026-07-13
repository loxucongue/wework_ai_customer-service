from __future__ import annotations

from pathlib import Path

from app.graph.nodes.image_info import build_vision_prompt
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.graph.planner.brain_v2_prompts import PLANNER_SYSTEM_PROMPT, PLANNER_TRANSACTION_PATCH_PROMPT
from app.prompts.global_contract import (
    GLOBAL_BUSINESS_RHYTHM_CONTRACT,
    GLOBAL_REPLY_CONTRACT,
    GLOBAL_STRUCTURED_NODE_CONTRACT,
)
from app.prompts.profile_analyzer import PROFILE_ANALYZER_SYSTEM_PROMPT
from app.prompts.reply_synthesizer import REPLY_SYSTEM_PROMPT, REPLY_TRANSACTION_PATCH_PROMPT
from app.services.outreach_prompts import OUTREACH_MESSAGE_SYSTEM_PROMPT, OUTREACH_PLAN_SYSTEM_PROMPT


ROOT = Path(__file__).resolve().parents[1]
PROMPT_FILES = [
    ROOT / "ai_paths/app/prompts/global_contract.py",
    ROOT / "ai_paths/app/graph/planner/brain_v2_prompts.py",
    ROOT / "ai_paths/app/prompts/reply_synthesizer.py",
    ROOT / "ai_paths/app/prompts/profile_analyzer.py",
    ROOT / "ai_paths/app/graph/nodes/image_info.py",
    ROOT / "ai_paths/app/services/sop_execution_service.py",
    ROOT / "ai_paths/app/services/outreach_prompts.py",
]


def test_prompt_files_do_not_contain_common_encoding_damage() -> None:
    suspicious = (
        "?" * 4,
        "?" + "7" + "?",
        "?" + "30" + "?",
        "\ufffd",
        chr(0x95C2),
        chr(0x9366),
        chr(0x951B),
        chr(0x9286),
    )
    for path in PROMPT_FILES:
        text = path.read_text(encoding="utf-8")
        for marker in suspicious:
            assert marker not in text, f"{path} contains possible encoding damage: {marker}"


def test_transaction_prompts_keep_order_payment_and_appointment_sequence() -> None:
    assert "create_work_order" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "create_order_plan" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "payment_result=success" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "没有成功 order_id 时不发卡" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "不再让客户补登记" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "本次交易终态" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "感谢和欢迎到店" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "改约决策分两轮闭环" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "完整 11 位号码" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "不能只回一句“199是别的口径”" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "current-turn transaction results" not in REPLY_TRANSACTION_PATCH_PROMPT
    assert "transaction_facts 是本轮刚执行完成的权威工具事实" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "尊敬的客户" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "成交推进不是无限循环" in GLOBAL_REPLY_CONTRACT
    assert "排客完成终态" in GLOBAL_BUSINESS_RHYTHM_CONTRACT


def test_planner_prompt_is_intent_driven_and_keeps_business_boundaries() -> None:
    for marker in [
        "Fact Source Priority",
        "Decision SOP",
        "Tool Map",
        "Negative Cases",
        "Few-Shot Calibration",
        "不新增 thought",
        "store_candidate",
        "appointment_decision",
    ]:
        assert marker in PLANNER_SYSTEM_PROMPT

    for business_rule in [
        "已发送过 payment_collection 只是频率证据，不是硬去重",
        "历史累计次数都不能单独决定发或不发",
        "到店抵扣，未做或不满意可退",
        "实际按付款记录核对",
        "预约金只锁活动名额、到店时间按客户方便安排",
        "2位一共20元，3位一共30元，4位一共40元",
        "客户可见回复只说哪家更近，不说公里、分钟、车程",
        "旧健康风险、旧门店、旧预约任务只有在客户当前明确延续时才主导本轮",
        "客户给出明确城市、区域或地标并问门店/附近/地址/停车/营业时间/导航时，输出 need_tools",
        "平台同城投放或展示定位",
        "同城真实门店",
        "不说广告错误",
        "已筛选后的斑点改善意向客户",
        "不要让客户先发照片给你线上诊断",
        "斑点能不能做、淡斑效果、怕没效果、怕反黑、要效果图属于案例/效果链路",
        "preferred_store / store_candidate 不是 confirmed_store",
        "没有 available_time、appointment_record 或 request confirmed appointment 事实时",
        "SOP 三板斧",
        "不要只答疑停住",
        "小程序收款卡片/收款码",
        "转账和截图备注",
        "manual_transfer",
        "收款卡是当前最自然的下一步",
        "已有同城 store_facts",
        "requested_district_stores",
        "不论该区是 1 家还是多家",
        "不要问“要不要了解/要不要看/是否需要/要不要我发”",
        "不要每轮复读",
        "今天发送次数",
        "普通顾虑被解决后的明确接受也可以进入 send_now",
        "卡片操作和一个理由放在前面",
        "不重复卡片",
        "只用于客户已付后的姓名、电话、门店、日期或排期承接",
        "主任/总监到店",
    ]:
        assert business_rule in PLANNER_SYSTEM_PROMPT
    assert GLOBAL_STRUCTURED_NODE_CONTRACT in PLANNER_SYSTEM_PROMPT
    assert GLOBAL_BUSINESS_RHYTHM_CONTRACT in PLANNER_SYSTEM_PROMPT
    assert "evidence_summary" not in PLANNER_SYSTEM_PROMPT


def test_reply_prompt_has_fact_priority_examples_and_customer_rules() -> None:
    for marker in ["Response SOP", "Fact Source Priority", "Message Map", "Few-Shot Calibration"]:
        assert marker in REPLY_SYSTEM_PROMPT

    for business_rule in [
        "先肯定对应需求大多数可以做",
        "已经筛选后的斑点改善意向客户",
        "不要引导客户发照片给你做线上诊断",
        "发我正面清晰照",
        "到店抵扣，未做或不满意可退",
        "实际按付款记录核对",
        "10/20/30/40",
        "human_handoff_notice",
        "旧画像健康风险、旧门店、旧预约任务不得覆盖客户当前普通问题",
        "到店先做皮肤检测/专业检测",
        "平台同城投放/平台展示定位",
        "发送门店卡",
        "广告错误/骗您的",
        "SOP 三板斧后",
        "明确付款选择或成交动作",
        "小程序收款卡片/收款码",
        "转账、截图和备注登记",
        "manual_transfer",
        "requested_district_stores",
        "不论是 1 家还是多家",
        "不要问客户“要不要了解活动/要不要我给您看/是否需要/您看下吗”",
        "不要每次复读同一句",
        "不是必须照抄的客户文案",
        "短确认后的收款动作要像继续聊天",
        "当前仍是未付状态",
        "没有可关联的真实收款订单",
        "主任/总监到店",
    ]:
        assert business_rule in REPLY_SYSTEM_PROMPT
    assert GLOBAL_REPLY_CONTRACT in REPLY_SYSTEM_PROMPT
    assert GLOBAL_BUSINESS_RHYTHM_CONTRACT in REPLY_SYSTEM_PROMPT
    assert "store_candidate" in REPLY_SYSTEM_PROMPT
    assert "appointment_decision" in REPLY_SYSTEM_PROMPT


def test_runtime_prompts_no_longer_carry_legacy_non_refund_policy() -> None:
    for path in [
        ROOT / "ai_paths/app/prompts/global_contract.py",
        ROOT / "ai_paths/app/graph/planner/brain_v2_prompts.py",
        ROOT / "ai_paths/app/prompts/reply_synthesizer.py",
        ROOT / "ai_paths/app/prompts/profile_analyzer.py",
        ROOT / "ai_paths/app/services/outreach_prompts.py",
    ]:
        assert "不做退10元" not in path.read_text(encoding="utf-8")


def test_project_constitution_documents_define_two_reply_test_modes() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    model_policy = (ROOT / "docs/agentic_prompting_and_model_policy.md").read_text(encoding="utf-8")
    for text in [agents, model_policy]:
        assert "Single-node model effect tests" in text or "单节点模型效果测试" in text
        assert "Full-chain online tests" in text or "全链路线上测试" in text


def test_single_node_sop_aftercare_datasets_are_split_and_comprehensive() -> None:
    import json

    planner_path = ROOT / "workflow_tests/fixtures/sop_aftercare_planner_cases.json"
    reply_path = ROOT / "workflow_tests/fixtures/sop_aftercare_reply_cases.json"
    planner_cases = json.loads(planner_path.read_text(encoding="utf-8"))
    reply_cases = json.loads(reply_path.read_text(encoding="utf-8"))

    assert len(planner_cases) >= 12
    assert len(reply_cases) >= 12

    planner_ids = {item["id"] for item in planner_cases}
    assert {
        "planner_effect_objection_after_case_sent",
        "planner_effect_objection_without_case_fact",
        "planner_rebound_objection_after_sop",
        "planner_distance_objection_after_store_discussed",
        "planner_ad_location_trust_same_city",
        "planner_price_all_in_after_sop",
        "planner_transfer_payment_method",
        "planner_payment_options_send_card",
        "planner_recent_card_no_repeat_without_progress",
        "planner_sop_objection_resolved_active_card",
        "planner_ack_after_payment_explanation_frequency",
        "planner_historical_six_today_zero_high_intent",
        "planner_practical_delay_keeps_activity_slot",
        "planner_card_entry_single_person",
        "planner_card_entry_friend_party",
        "planner_paid_next_step_no_card",
        "planner_director_pressure_without_fact",
        "planner_director_pressure_with_fact",
    }.issubset(planner_ids)

    reply_ids = {item["id"] for item in reply_cases}
    assert {
        "reply_effect_objection_with_case",
        "reply_rebound_objection",
        "reply_distance_objection_after_store",
        "reply_ad_location_trust_same_city",
        "reply_price_all_in_push_softly",
        "reply_transfer_payment_no_card",
        "reply_payment_options_send_card",
        "reply_recent_card_no_repeat_without_progress",
        "reply_sop_objection_resolved_active_card",
        "reply_ack_after_payment_explanation_frequency",
        "reply_historical_six_today_zero_high_intent",
        "reply_practical_delay_keeps_activity_slot",
        "reply_card_entry_single_person",
        "reply_card_entry_friend_party",
        "reply_paid_next_step_no_card",
        "reply_store_discussed_price_push",
        "reply_director_pressure_without_fact",
        "reply_director_pressure_with_fact",
    }.issubset(reply_ids)

    for item in planner_cases:
        assert item["node"] == "planner"
        assert item["current_message"].strip()
        assert item["input_payload"]["conversation_history"]
        assert "sop_progress" in item["input_payload"]
        assert "turn_evidence" in item["input_payload"]
        assert "fact_envelope" in item["input_payload"]
        assert "expected_decision" in item
        assert "decision" in item["expected_decision"]

    for item in reply_cases:
        assert item["node"] == "reply"
        assert item["current_message"].strip()
        assert item["input_payload"]["conversation_history"]
        assert "planner_decision" in item["input_payload"]
        assert "fact_envelope" in item["input_payload"]
        assert "expected" in item
        assert item["expected"]["must_include_semantics"]
        assert "message_types" in item["expected"]

    planner_card_case = next(item for item in planner_cases if item["id"] == "planner_card_entry_friend_party")
    assert set(planner_card_case["expected_decision"]["allowed_payment_actions"]) == {"none", "explain_existing"}
    assert "payment_collection" in planner_card_case["expected_decision"]["must_not_message_types"]
    assert "有效订单" in planner_card_case["expected_decision"]["notes"]

    planner_transfer_case = next(item for item in planner_cases if item["id"] == "planner_transfer_payment_method")
    assert planner_transfer_case["expected_decision"]["payment_action"] == "manual_transfer"
    assert "payment_collection" in planner_transfer_case["expected_decision"]["must_not_message_types"]

    reply_transfer_case = next(item for item in reply_cases if item["id"] == "reply_transfer_payment_no_card")
    assert "转账" in " ".join(reply_transfer_case["expected"]["must_include_semantics"])
    assert "payment_collection" in reply_transfer_case["expected"]["must_not_include"]
    assert "payment_collection" not in reply_transfer_case["expected"]["message_types"]

    planner_options_case = next(item for item in planner_cases if item["id"] == "planner_payment_options_send_card")
    assert set(planner_options_case["expected_decision"]["allowed_payment_actions"]) == {"none", "explain_existing"}
    assert "payment_collection" in planner_options_case["expected_decision"]["must_not_message_types"]

    planner_ack_case = next(
        item for item in planner_cases if item["id"] == "planner_ack_after_payment_explanation_frequency"
    )
    frequency = planner_ack_case["input_payload"]["sent_message_summary"]["payment_collection"]
    assert frequency["today_count"] == 0
    assert frequency["prior_count"] == 6
    assert frequency["total_count"] == 6
    assert set(planner_ack_case["expected_decision"]["allowed_payment_actions"]) == {
        "explain_existing",
        "send_now",
    }

    planner_practical_delay_case = next(
        item for item in planner_cases if item["id"] == "planner_practical_delay_keeps_activity_slot"
    )
    assert set(planner_practical_delay_case["expected_decision"]["allowed_payment_actions"]) == {
        "explain_existing",
        "send_now",
    }

    planner_recent_card_case = next(
        item for item in planner_cases if item["id"] == "planner_recent_card_no_repeat_without_progress"
    )
    assert "payment_collection" in planner_recent_card_case["expected_decision"]["must_not_message_types"]

    reply_active_card_case = next(
        item for item in reply_cases if item["id"] == "reply_sop_objection_resolved_active_card"
    )
    active_order_facts = reply_active_card_case["input_payload"]["fact_envelope"]["structured_facts"]["order_facts"]
    assert active_order_facts[0]["status"] == "reused"

    reply_practical_delay_case = next(
        item for item in reply_cases if item["id"] == "reply_practical_delay_keeps_activity_slot"
    )
    assert "payment_collection" in reply_practical_delay_case["expected"]["message_types"]
    assert "到店时间不用现在定" in " ".join(reply_practical_delay_case["expected"]["must_include_semantics"])

    for prompt_or_fixture in [
        PLANNER_SYSTEM_PROMPT,
        REPLY_SYSTEM_PROMPT,
        planner_path.read_text(encoding="utf-8"),
        reply_path.read_text(encoding="utf-8"),
    ]:
        assert "红包" not in prompt_or_fixture

    reply_card_case = next(item for item in reply_cases if item["id"] == "reply_card_entry_friend_party")
    assert reply_card_case["expected"]["payment_amount"] == 20
    assert "payment_collection" in reply_card_case["expected"]["message_types"]

    no_director_case = next(item for item in reply_cases if item["id"] == "reply_director_pressure_without_fact")
    assert "主任" in no_director_case["expected"]["must_not_include"]
    assert not no_director_case["input_payload"]["fact_envelope"]["structured_facts"]["operator_facts"]

    director_case = next(item for item in reply_cases if item["id"] == "reply_director_pressure_with_fact")
    assert director_case["input_payload"]["fact_envelope"]["structured_facts"]["operator_facts"]
    assert "主任到店" in " ".join(director_case["expected"]["must_include_semantics"])

    assert any(
        "重新问哪个门店" in " ".join(item["expected"].get("must_not_include", []))
        for item in reply_cases
    )


def test_effect_concern_without_case_tool_gets_planner_repair_violation() -> None:
    text = "\u4f1a\u4e0d\u4f1a\u53cd\u9ed1\u554a"
    plan = build_planner_plan_v2(
        {"content": text, "normalized_content": text},
        {
            "decision": "direct_reply",
            "stage": "S1",
            "customer_type": "unknown",
            "main_blocker": "none",
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "\u5230\u5e97\u5148\u8bc4\u4f30\u76ae\u80a4\u72b6\u6001\u3002"},
                }
            ],
            "tool_calls": [],
        },
    )
    assert any(
        item.get("missing") == "case_studies_required_for_effect_turn"
        for item in plan["tool_policy_violations"]
    )


def test_profile_prompt_downgrades_stale_history_without_dropping_facts() -> None:
    for marker in ["Source Priority", "Analysis SOP", "Negative Cases"]:
        assert marker in PROFILE_ANALYZER_SYSTEM_PROMPT
    assert "旧健康风险、旧门店、旧预约任务只有在本轮客户继续提到时" in PROFILE_ANALYZER_SYSTEM_PROMPT
    assert "系统发过 payment_collection 不等于客户已支付" in PROFILE_ANALYZER_SYSTEM_PROMPT
    assert GLOBAL_STRUCTURED_NODE_CONTRACT in PROFILE_ANALYZER_SYSTEM_PROMPT


def test_vision_prompt_is_sectioned_json_only_and_non_diagnostic() -> None:
    prompt = build_vision_prompt({"normalized_content": "脸上有斑", "conversation_history": []})
    for marker in ["Vision Node Role", "Image Analysis Policy", "Do Not", "Output Schema"]:
        assert marker in prompt
    assert "不写黄褐斑、皮炎、感染等诊断词" in prompt
    assert "不输出治疗结论、疾病判断、保证效果、同等效果承诺" in prompt
    assert GLOBAL_STRUCTURED_NODE_CONTRACT in prompt


def test_sop_and_outreach_prompts_keep_gate_boundaries() -> None:
    sop_source = (ROOT / "ai_paths/app/services/sop_execution_service.py").read_text(encoding="utf-8")
    for marker in [
        "SOP_EVENT_SYSTEM_PROMPT",
        "GLOBAL_STRUCTURED_NODE_CONTRACT",
        "GLOBAL_BUSINESS_RHYTHM_CONTRACT",
        "Business Background And Goal",
        "先做拒发审查",
        "客户当前立场与候选包的核心行动相反",
        "阶段目标 + 核心事实 + 行动目标",
        "Few-Shot Calibration",
        "Text Adjustment Policy",
        "企业微信一对一聊天",
        "您好，温馨提醒",
        "Text Style Calibration",
        "editable_text_messages",
        "readonly_messages",
        "冲突",
        "润色不能把拒发冲突改写成可发",
        "只是润色理由，不是拒发理由",
        "严重重合",
    ]:
        assert marker in sop_source

    for prompt in [OUTREACH_PLAN_SYSTEM_PROMPT, OUTREACH_MESSAGE_SYSTEM_PROMPT]:
        assert "Negative Cases" in prompt
        assert "Few-Shot Calibration" in prompt
        assert "支付失败" in prompt
