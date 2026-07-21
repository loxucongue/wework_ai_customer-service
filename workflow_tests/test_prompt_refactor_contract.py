from __future__ import annotations

import json
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
from app.policies.business_rules import planner_business_rules_prompt_section, reply_business_rules_for_model
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


def test_transaction_prompts_require_order_and_keep_postpaid_information_only() -> None:
    assert "create_work_order" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "create_order_plan" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "payment_result=success" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "缺少成功 order_id 或开单失败必须取消卡片" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "同门店、同金额有效未付订单" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "不调用 available_time/create_order_plan" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "客户口头说“我付了”不能单独确认已付" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "姓名、电话、门店、到店日期和时间" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "不查档期、不创建排客" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "既有 appointment_created/confirmed" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "感谢和欢迎到店" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "不得新调 create_order_plan" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "完整11位电话" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "不能只回一句“199是别的口径”" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "current-turn transaction results" not in REPLY_TRANSACTION_PATCH_PROMPT
    assert "`transaction_facts` 是本轮刚执行完成的权威工具事实" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "尊敬的客户" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "成交推进不是无限循环" in GLOBAL_REPLY_CONTRACT
    assert "排客完成终态" in GLOBAL_BUSINESS_RHYTHM_CONTRACT
    assert "不要求先确认门店、先有订单或开单成功" not in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "缺少成功 order_id 或开单失败都不得取消卡片" not in REPLY_TRANSACTION_PATCH_PROMPT


def test_transaction_prompts_allow_only_authoritative_single_store_card_binding() -> None:
    assert "唯一可信交易门店锚点" in GLOBAL_BUSINESS_RHYTHM_CONTRACT
    assert "store_address_delivery.unique_latest_store_id" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "最近发过多家" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "store_binding_decision" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "accepted_explicit/accepted_implicit" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "辅助字段缺失或平台开单失败时，本轮仍正常回答" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "绝不能因为开单未成功而输出空回复" in REPLY_SYSTEM_PROMPT


def test_planner_prompt_is_intent_driven_and_keeps_business_boundaries() -> None:
    for marker in [
        "Role And Mission",
        "Input Contract",
        "Fact Priority",
        "Decision Procedure",
        "Tool Map",
        "High-Value Calibration",
        "Decision And Output Schema",
        "store_candidate",
        "appointment_decision",
        "sop_progress_evidence",
        "sales_progression",
    ]:
        assert marker in PLANNER_SYSTEM_PROMPT

    for business_rule in [
        "发卡次数是证据不是阈值",
        "未做或不满意可退",
        "实际按付款记录核对",
        "2位20、3位30、4位40",
        "客户可见不输出公里、分钟、车程",
        "旧健康风险、旧门店、旧预约任务只有在客户当前明确延续时才主导本轮",
        "preferred_store/store_candidate 不是 confirmed store",
        "平台同城展示误解与信任顾虑",
        "已筛选的斑点改善意向人群",
        "不要让客户发照片做线上诊断",
        "sent_message_summary.case_image_delivery",
        "小程序收款卡/收款码或转账",
        "manual_transfer",
        "requested_district_stores",
        "平台结构化 POI",
        "不反问客户是否要看或了解",
        "当前普通已付流程只登记到店意向",
        "human_handoff_notice",
        "真实客户问题不能用它逃避回答",
        "工具完成后由最终 Reply 一次生成客户可见回复",
    ]:
        assert business_rule in PLANNER_SYSTEM_PROMPT
    assert GLOBAL_STRUCTURED_NODE_CONTRACT in PLANNER_SYSTEM_PROMPT
    assert GLOBAL_BUSINESS_RHYTHM_CONTRACT in PLANNER_SYSTEM_PROMPT
    assert "evidence_summary" not in PLANNER_SYSTEM_PROMPT
    assert len(PLANNER_SYSTEM_PROMPT) < 14_000


def test_runtime_business_fact_views_do_not_repeat_full_rule_packs() -> None:
    planner_facts = planner_business_rules_prompt_section()
    reply_facts = json.dumps(
        reply_business_rules_for_model(stage="S3", sub_rule_id="S3_PAYMENT_COLLECTION"),
        ensure_ascii=False,
        separators=(",", ":"),
    )

    assert len(GLOBAL_BUSINESS_RHYTHM_CONTRACT) < 4_000
    assert len(planner_facts) < 2_000
    assert len(reply_facts) < 2_000
    assert "case_image_fallback_urls" not in planner_facts
    assert "case_image_fallback_urls" not in reply_facts
    assert "conversion_psychology" not in reply_facts


def test_reply_prompt_has_fact_priority_examples_and_customer_rules() -> None:
    for marker in [
        "Role And Mission",
        "Input Contract",
        "Fact Priority",
        "Reply Procedure",
        "Sales Rhythm",
        "Effect And Safety",
        "Store And Location",
        "Payment And Order",
        "Message Schema",
        "Calibration",
    ]:
        assert marker in REPLY_SYSTEM_PROMPT

    for business_rule in [
        "客户已是斑点改善意向人群",
        "这类大多数客户可以做、改善反馈不错",
        "不要让客户发照片做线上诊断",
        "未做或不满意可退",
        "实际按付款记录核对",
        "每位10元预约金",
        "human_handoff_notice",
        "旧健康风险、旧门店、旧订单和旧预约不得覆盖当前普通问题",
        "平台同城展示",
        "SOP 已铺垫后",
        "小程序收款卡/收款码",
        "requested_district_stores",
        "不要问“要不要了解、要不要看、是否需要、要不要我发”",
        "降压挽回",
        "主任、总监、专家或特殊老师只有工具事实",
        "绝不能因为开单未成功而输出空回复",
        "不查 `available_time`",
    ]:
        assert business_rule in REPLY_SYSTEM_PROMPT
    assert GLOBAL_REPLY_CONTRACT in REPLY_SYSTEM_PROMPT
    assert GLOBAL_BUSINESS_RHYTHM_CONTRACT in REPLY_SYSTEM_PROMPT
    assert "store_candidate" in REPLY_SYSTEM_PROMPT
    assert "appointment_decision" in REPLY_SYSTEM_PROMPT
    assert "sales_progression" in REPLY_SYSTEM_PROMPT
    assert "planner_direct_reply_draft" in REPLY_SYSTEM_PROMPT
    assert "不能删掉草稿里的具体回答、付款选择、保留名额、登记或门店动作" in REPLY_SYSTEM_PROMPT
    assert "不能删掉其中的具体成交动作" in REPLY_SYSTEM_PROMPT
    assert len(REPLY_SYSTEM_PROMPT) < 16_000


def test_reply_runtime_does_not_generate_business_candidates_in_python() -> None:
    source = (ROOT / "ai_paths/app/graph/nodes/reply_context.py").read_text(encoding="utf-8")
    assert "def _sop_next_candidates" not in source
    assert '"next_candidates"' not in source
    assert 'state.get("sales_progression")' in source
    assert '"planner_direct_reply_draft"' in source
    assert "def _planner_direct_reply_draft_for_reply" in source


def test_planner_requires_authoritative_recent_case_image_evidence() -> None:
    assert "sent_message_summary.case_image_delivery" in PLANNER_SYSTEM_PROMPT
    assert "completed_pack_ids/completed_categories" in PLANNER_SYSTEM_PROMPT
    assert "不能单独证明客户近期看过图" in PLANNER_SYSTEM_PROMPT
    assert "没有权威近期图片证据时查 `case_studies`" in PLANNER_SYSTEM_PROMPT


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


def test_sop_gate_hands_location_slot_completion_to_ai() -> None:
    source = (ROOT / "ai_paths/app/services/sop_execution_service.py").read_text(encoding="utf-8")
    for marker in [
        "门店匹配槽位已补齐",
        "必须 send_sop=false、need_ai_reply=true",
        "位置事实 + 到店时间顾虑",
        "不能替代门店匹配回复",
        "客户回“我现在在黄浦区，现在上班没时间，先加微信后面联系”",
    ]:
        assert marker in source


def test_sop_gate_requires_contextual_first_text_and_preserves_numeric_facts() -> None:
    source = (ROOT / "ai_paths/app/services/sop_execution_service.py").read_text(encoding="utf-8")
    for marker in [
        "最早一条可编辑 text",
        "先用一句短话直接承接",
        "不要机械添加",
        "所有数字及其出现次数",
        "text_adjustments",
        "message_operations",
        "insert_text_after",
        "除 `remove_message` 删除不支持发送的 `payment_collection` 外",
        "payment_collection_gate.status",
        "remove_message",
        "payment_collection_requires_matching_current_order",
        "skipped_deposit_paid",
        "failed_order_fetch",
    ]:
        assert marker in source


def test_paid_after_flow_prioritizes_name_and_phone_without_schedule_tools() -> None:
    global_contract = (ROOT / "ai_paths/app/prompts/global_contract.py").read_text(encoding="utf-8")
    planner_prompt = (ROOT / "ai_paths/app/graph/planner/brain_v2_prompts.py").read_text(encoding="utf-8")
    reply_prompt = (ROOT / "ai_paths/app/prompts/reply_synthesizer.py").read_text(encoding="utf-8")
    for source in [global_contract, planner_prompt, reply_prompt]:
        assert "姓名" in source
        assert "电话" in source
        assert "不调用 available_time" in source or "不查档期" in source
        assert "已安排" in source or "已预留" in source
    assert "create_order_plan" in planner_prompt
    assert "order_plan" in reply_prompt
    assert "姓名和电话是第一优先级" in global_contract
    assert "已付后先收姓名和完整11位电话" in planner_prompt
    assert "先收姓名和电话" in reply_prompt


def test_sop_gate_does_not_use_case_pack_for_project_content_or_cleaning_doubt() -> None:
    source = (ROOT / "ai_paths/app/services/sop_execution_service.py").read_text(encoding="utf-8")
    for marker in [
        "项目内容、费用包含",
        "是否只是检测/清洁/洗脸",
        "真正包含斑点改善",
        "泛效果案例包不能覆盖",
        "检测清洁是前置步骤、不是全部项目",
        "客户问“应该只是检测和洗脸，没有去斑吧？”",
    ]:
        assert marker in source

    import json

    payload = json.loads((ROOT / "config/sop_reply_packs.json").read_text(encoding="utf-8"))
    pack = next(item for item in payload["packs"] if item["id"] == "s10_need_and_case")
    assert "检测/清洁/洗脸" in pack["purpose"]
    assert "项目内容与费用包含" in pack["purpose"]
    assert "不适用" in pack["purpose"]


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


def test_effect_concern_without_case_tool_remains_a_model_decision() -> None:
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
    assert not any(item.get("subtype") == "kb_search" for item in plan["tool_policy_violations"])
    assert "sent_message_summary.case_image_delivery" in PLANNER_SYSTEM_PROMPT
    assert "SOP完成、画像总结和文字承诺不能单独证明客户近期看过图" in PLANNER_SYSTEM_PROMPT
    assert "上一轮确实刚发图后的评价续问可以不重复查询" in PLANNER_SYSTEM_PROMPT
    assert "改天" in REPLY_SYSTEM_PROMPT


def test_profile_prompt_downgrades_stale_history_without_dropping_facts() -> None:
    for marker in ["Source Priority", "Analysis SOP", "Negative Cases"]:
        assert marker in PROFILE_ANALYZER_SYSTEM_PROMPT
    assert "旧健康风险、旧门店、旧预约任务只有在本轮客户继续提到时" in PROFILE_ANALYZER_SYSTEM_PROMPT
    assert "系统发过 payment_collection 不等于客户已支付" in PROFILE_ANALYZER_SYSTEM_PROMPT
    assert "要的、空了来、改天来、后面有时间去、谢谢" in PROFILE_ANALYZER_SYSTEM_PROMPT
    assert "是否发卡、解释预约金或只做门店承接，由下一轮 Planner" in PROFILE_ANALYZER_SYSTEM_PROMPT
    assert "不能把它总结成“放弃/流失/禁止推进定金”" in PROFILE_ANALYZER_SYSTEM_PROMPT
    assert GLOBAL_STRUCTURED_NODE_CONTRACT in PROFILE_ANALYZER_SYSTEM_PROMPT


def test_prompts_treat_reserved_visit_intent_as_sales_continuation() -> None:
    assert "客户“我改天去”" in PLANNER_SYSTEM_PROMPT
    assert "到店时间可后定" in PLANNER_SYSTEM_PROMPT
    assert "不要只回“空了再来”" in PLANNER_SYSTEM_PROMPT

    assert "客户说忙、天气热、改天、路远或要订行程" in REPLY_SYSTEM_PROMPT
    assert "不自动等于退出" in REPLY_SYSTEM_PROMPT
    assert "到店时间后面按客户方便安排" in REPLY_SYSTEM_PROMPT
    assert "只有 `recommended_store.reason=distance_calculate_rank_1`" in REPLY_SYSTEM_PROMPT


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
