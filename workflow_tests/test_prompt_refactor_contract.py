from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.graph.nodes.image_info import build_vision_prompt
from app.graph.nodes.turn_evidence_view import turn_evidence_for_model
from app.graph.planner.brain_v2 import (
    planner_v2_messages_for_model,
    planner_v2_repair_messages_for_model,
    planner_v2_timeout_retry_messages_for_model,
)
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.graph.planner.brain_v2_prompts import (
    PLANNER_RISK_PATCH_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    PLANNER_TRANSACTION_OUTPUT_GATE_PROMPT,
    PLANNER_TRANSACTION_PATCH_PROMPT,
)
from app.prompts.global_contract import (
    GLOBAL_BUSINESS_RHYTHM_CONTRACT,
    GLOBAL_REPLY_CONTRACT,
    GLOBAL_STRUCTURED_NODE_CONTRACT,
)
from app.prompts.profile_analyzer import PROFILE_ANALYZER_SYSTEM_PROMPT
from app.prompts.reply_synthesizer import REPLY_SYSTEM_PROMPT, REPLY_TRANSACTION_PATCH_PROMPT
from app.prompts.reply_synthesizer import build_reply_messages
from app.prompts.sop_chat_gate import build_sop_chat_gate_messages
from app.policies.business_rules import (
    load_business_rules,
    planner_business_rules_prompt_section,
    reply_business_rules_for_model,
)
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


def test_transaction_prompts_allow_card_without_order_and_keep_postpaid_information_only() -> None:
    assert "create_work_order" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "create_order_plan" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "payment_result=success" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "缺少成功 order_id 或开单失败不得取消卡片" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "订单和开单只用于后台关联" in PLANNER_TRANSACTION_PATCH_PROMPT
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
    assert "不是发卡前置" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "缺少成功 order_id 或开单失败不得取消卡片" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "没有订单或开单失败时仍可由模型判断本轮发卡" in PLANNER_TRANSACTION_PATCH_PROMPT


def test_transaction_prompts_allow_only_authoritative_single_store_card_binding() -> None:
    assert "唯一可信交易门店锚点" in GLOBAL_BUSINESS_RHYTHM_CONTRACT
    assert "store_address_delivery.unique_latest_store_id" in PLANNER_TRANSACTION_PATCH_PROMPT
    assert "最近发过多家" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "上一条唯一推荐+“这家可以”则承接" in REPLY_TRANSACTION_PATCH_PROMPT
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
        "客户只是反馈距离、说近/远、几公里、还可以",
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
        "不得写成 `direct_reply + tool_calls`",
        '"name":"distance_calculate"',
        "不得把 `after_paid_next_step` 填进 `payment_action`",
        "`payment_action=confirm_next_step`",
        "不在线追问用药或症状",
        "连“可以继续约”也不能确认",
        "答清后仍无门店就问城市区域",
        "健康、孕期或过敏只引导到店专业检测",
        "隐形消费或收费透明顾虑答清、活动已说明但无门店",
        "发卡前置是活动报价已完成/已铺垫",
        "短消息须承接最近未完动作",
        "不列选项重问意图",
        "不能答无需预约金",
        "有 case_facts 同轮发 image",
        "已答风险在普通门店/时间轮完全不复述",
        "不得称已报名或已留名额",
        "仅客户当前询问可退或退款时主动展开",
        "“好/嗯”只是确认，不重开旧顾虑",
    ]:
        assert business_rule in PLANNER_SYSTEM_PROMPT
    assert GLOBAL_STRUCTURED_NODE_CONTRACT in PLANNER_SYSTEM_PROMPT
    assert GLOBAL_BUSINESS_RHYTHM_CONTRACT in PLANNER_SYSTEM_PROMPT
    assert "evidence_summary" not in PLANNER_SYSTEM_PROMPT
    assert "explain-only direct_reply 不完整" in PLANNER_TRANSACTION_OUTPUT_GATE_PROMPT
    assert "`store_binding=ambiguous`" in PLANNER_TRANSACTION_OUTPUT_GATE_PROMPT
    assert "上一条唯一推荐某店后客户接受“这家”" in PLANNER_TRANSACTION_OUTPUT_GATE_PROMPT
    assert "不得在草稿中复述健康、过敏、检测或适配提醒" in PLANNER_TRANSACTION_OUTPUT_GATE_PROMPT


def test_runtime_business_fact_views_preserve_all_current_rule_semantics() -> None:
    authoritative = load_business_rules()
    planner_facts = json.loads(planner_business_rules_prompt_section())
    reply_facts = reply_business_rules_for_model(stage="S3", sub_rule_id="S3_PAYMENT_COLLECTION")
    expected_ids = {
        str(rule.get("id") or "")
        for stage in authoritative.get("stages") or []
        if isinstance(stage, dict)
        for rule in stage.get("rules") or []
        if isinstance(rule, dict)
    }

    catalog = planner_facts.get("scene_catalog") or []
    assert len(catalog) == 14
    assert {str(item.get("id") or "") for item in catalog} == expected_ids
    for item in catalog:
        for field in ("stage", "stage_goal", "id", "scenes", "decision", "tools", "reply_focus", "fact_boundary"):
            assert field in item
    assert planner_facts.get("conversion_psychology", {}).get("principles")
    assert planner_facts.get("conversion_psychology", {}).get("customer_types")
    assert planner_facts.get("transaction_policy", {}).get("appointment_flow_mode") == "registration_only"
    assert planner_facts.get("tool_policy", {}).get("boundaries")

    current_rules = reply_facts.get("current_stage_rules", {}).get("rules") or []
    assert len(current_rules) == 1
    for field in ("id", "scenes", "decision", "tools", "reply_focus", "fact_boundary"):
        assert field in current_rules[0]
    assert reply_facts.get("conversion_psychology", {}).get("principles")
    assert reply_facts.get("transaction_policy", {}).get("payment_order_policy")
    assert reply_facts.get("customer_visible_evidence_policy")
    assert reply_facts.get("tool_policy", {}).get("boundaries")

    planner_text = json.dumps(planner_facts, ensure_ascii=False, separators=(",", ":"))
    reply_text = json.dumps(reply_facts, ensure_ascii=False, separators=(",", ":"))
    assert "case_image_fallback_urls" not in planner_text
    assert "case_image_fallback_urls" not in reply_text
    assert "activity_intro_image_url" not in planner_text
    assert "activity_intro_image_url" not in reply_text


def test_planner_actual_messages_include_risk_transaction_and_rule_contracts() -> None:
    state = {
        "normalized_content": "厦门湖里有门店吗",
        "conversation_history": ["小贝: 您在哪个城市或区？", "用户: 厦门湖里"],
    }
    initial = planner_v2_messages_for_model(state)
    repair = planner_v2_repair_messages_for_model(
        state,
        original_plan={"decision": "direct_reply", "reply_messages": []},
        violations=[{"code": "empty_direct_reply"}],
    )
    for messages in (initial, repair):
        joined = "\n".join(str(item.get("content") or "") for item in messages)
        assert PLANNER_RISK_PATCH_PROMPT in joined
        assert PLANNER_TRANSACTION_PATCH_PROMPT in joined
        assert '"scene_catalog"' in joined
        assert '"conversion_psychology"' in joined
        assert '"transaction_policy"' in joined


def test_chat_gate_actual_messages_keep_sop_precision_and_ai_boundaries() -> None:
    messages = build_sop_chat_gate_messages(
        {
            "current_message": "是不是做一次就可以",
            "recent_conversation": ["用户: 脸上有斑", "小贝: 我先给您看下活动"],
            "precision_qa_index": [
                {"id": "one_session_effect", "resume_mainline_stage": "activity_intro"}
            ],
            "unfinished_sops": [
                {
                    "id": "s10_activity_intro",
                    "mainline_stage": "activity_intro",
                    "reply_messages": [{"type": "text", "content": "活动价268"}],
                }
            ],
        }
    )
    joined = "\n".join(str(item.get("content") or "") for item in messages)
    for marker in [
        "sop_only",
        "ai_then_sop",
        "ai_only",
        "精准回答",
        "回到最早未完成销售主线",
        "SOP 是阶段素材，不是不能改的原稿",
        "调整、删除、拆分、合并或插入普通 text",
        "客户回复城市、区、地标、定位，或索要地址导航",
        "选择 `ai_only`",
        "不能用宽泛项目介绍或案例包抢答",
    ]:
        assert marker in joined


def test_reply_actual_messages_keep_precision_rules_and_stage_business_rules() -> None:
    payload = {
        "current_message": "一二公里",
        "conversation_history": [
            "用户: 双流区",
            "小贝: 已发成都双流店和成都双流高新店地址卡",
        ],
        "planner_decision": "direct_reply",
        "planner_stage": "S2",
        "planner_sub_rule_id": "S2_DISTRICT_OR_LANDMARK",
        "sales_progression": {
            "status": "continue",
            "target_stage": "need_and_case",
            "action": "ask_need_context",
        },
        "business_rules": reply_business_rules_for_model(
            stage="S2",
            sub_rule_id="S2_DISTRICT_OR_LANDMARK",
        ),
    }
    messages = build_reply_messages(
        payload,
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )
    joined = "\n".join(str(item.get("content") or "") for item in messages)
    for marker in [
        "Precision Reply Contract",
        "先精准解决当前顾虑",
        "business_rules",
        "conversion_psychology",
        "transaction_policy",
        "current_stage_rules",
        "近轮已经发过真实门店卡",
        "恢复下一主线",
        "不要再让客户选择门店",
        "直接用一句自然过渡进入活动或价格铺垫",
    ]:
        assert marker in joined


def test_planner_timeout_recovery_keeps_current_scene_and_flat_tool_contracts() -> None:
    messages = planner_v2_timeout_retry_messages_for_model(
        {"normalized_content": "洪湖市有门店吗", "conversation_history": []},
        previous_error="TimeoutError",
    )
    joined = "\n".join(str(item.get("content") or "") for item in messages)

    assert "Current Recovery Business Rules" in joined
    assert '"id":"S2_CITY_ONLY"' in joined
    assert '"name":"customer_store_lookup"' in joined
    assert "禁止 `tool_name/arguments/tool/args`" in joined
    assert "不调用 available_time/create_order_plan" in joined

    selected_messages = planner_v2_timeout_retry_messages_for_model(
        {
            "normalized_content": "是不是一次就能做好",
            "conversation_history": [],
            "sop_gate_decision": {"priority_question_id": "one_session_effect"},
        },
        previous_error="TimeoutError",
    )
    selected_payload = json.loads(selected_messages[-1]["content"])
    selected = selected_payload["precision_qa_playbook"]["selected_question"]
    assert selected["id"] == "one_session_effect"
    assert selected.get("must_answer")


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
        "近轮已经发过真实门店卡，客户本轮只是评价距离或说大概几公里",
        "小程序收款卡/收款码",
        "requested_district_stores",
        "不要问“要不要了解、要不要看、是否需要、要不要我发”",
        "降压挽回",
        "主任、总监、专家或特殊老师只有工具事实",
        "绝不能因为开单未成功而输出空回复",
        "不查 `available_time`",
        "不能停在费用说明",
        "不在线追问用药和身体症状",
        "不能说“可以继续约",
        "也不能承诺稍后发入口",
        "健康、孕期和过敏统一引导到店专业检测",
        "不直接判定只能等产后或以后",
        "直接续最近未完动作",
        "不猜网络延迟、页面故障或银行原因",
        "未付且客户未主动登记时不提前索要姓名电话",
        "两店并列未选/未推荐才 ambiguous",
        "`current_known_store` 单店不得覆盖",
        "已答健康/过敏后转问门店、地址或时间",
        "不借题堆价格、退款或无关门店信息",
        "不能说核实/处理退款",
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
    assert "历史风险视为已处理背景" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "严禁自行复活健康、过敏、检测或适配提醒" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "`current_known_store` 不覆盖歧义" in REPLY_TRANSACTION_PATCH_PROMPT
    assert "unresolved/no_match" in REPLY_SYSTEM_PROMPT
    assert "不得用常识、相似地名或猜测补成某个城市" in REPLY_SYSTEM_PROMPT
    assert "`store_address` 数量必须恰好为1" in REPLY_SYSTEM_PROMPT
    assert "孤立地名" in PLANNER_SYSTEM_PROMPT
    assert "禁止 `nearby_candidates/distance_calculate`" in PLANNER_SYSTEM_PROMPT


def test_reply_runtime_does_not_generate_business_candidates_in_python() -> None:
    source = (ROOT / "ai_paths/app/graph/nodes/reply_context.py").read_text(encoding="utf-8")
    assert "def _sop_next_candidates" not in source
    assert '"next_candidates"' not in source
    assert 'state.get("sales_progression")' in source
    assert '"planner_direct_reply_draft"' in source
    assert "def _planner_direct_reply_draft_for_reply" in source


def test_model_visible_turn_evidence_excludes_python_business_conclusions() -> None:
    planner_source = (ROOT / "ai_paths/app/graph/planner/brain_v2.py").read_text(encoding="utf-8")
    reply_source = (ROOT / "ai_paths/app/graph/nodes/reply_context.py").read_text(encoding="utf-8")
    view_source = (ROOT / "ai_paths/app/graph/nodes/turn_evidence_view.py").read_text(encoding="utf-8")

    assert "turn_evidence_for_model(value)" in planner_source
    assert "turn_evidence_for_model(value)" in reply_source
    for forbidden in [
        '"context_hints"',
        '"binding_source"',
        '"resolved_slots"',
        '"missing_slots"',
        '"blocked_actions"',
    ]:
        assert forbidden not in view_source
    for required in [
        '"history_evidence"',
        '"recent_assistant_action"',
        '"store_evidence"',
        '"appointment_evidence"',
        '"payment_evidence"',
        '"registration_evidence"',
        '"evidence_conflicts"',
    ]:
        assert required in view_source


def test_model_visible_turn_evidence_keeps_facts_without_business_conclusions() -> None:
    recent_text = "上一轮客服说明" * 100
    value = {
        "open_task": "deposit_push",
        "reply_anchor": "请直接催客户付款",
        "turn_evidence": {
            "history_evidence": {
                "is_short_message": True,
                "recent_assistant_action": "sent_payment_collection",
                "recent_assistant_text": recent_text,
            },
            "payment_evidence": {
                "sent_payment_collection": True,
                "payment_collection_count": 2,
                "last_assistant_payment_text": "我把10元预约金卡发您了",
                "recent_payment_texts": ["预约金10元，到店抵扣"],
                "source_policy": "evidence_only_planner_decides_payment_state",
            },
        },
    }
    evidence = turn_evidence_for_model(value)

    assert len(evidence["history_evidence"]["recent_assistant_text"]) == 600
    assert evidence["payment_evidence"]["payment_collection_count"] == 2
    assert evidence["payment_evidence"]["last_assistant_payment_text"] == "我把10元预约金卡发您了"
    assert "open_task" not in evidence
    assert "reply_anchor" not in evidence


def test_default_text_models_use_openai_family() -> None:
    settings = Settings(_env_file=None)
    for model in (
        settings.model_fast,
        settings.model_planner,
        settings.model_balanced,
        settings.model_strong,
        settings.model_reply,
    ):
        assert model.startswith("gpt-")


def test_structured_model_nodes_use_zero_temperature() -> None:
    model_client = (ROOT / "ai_paths/app/services/model_client.py").read_text(encoding="utf-8")
    profile = (ROOT / "ai_paths/app/graph/nodes/profile_nodes.py").read_text(encoding="utf-8")
    outreach = (ROOT / "ai_paths/app/services/outreach_service.py").read_text(encoding="utf-8")
    vision = (ROOT / "ai_paths/app/graph/nodes/layer_nodes.py").read_text(encoding="utf-8")

    assert "temperature: float = 0.0" in model_client
    assert "temperature=0.0" in profile
    assert outreach.count("temperature=0.0") >= 2
    assert "vision_json(" in vision and "temperature=0.0" in vision


def test_planner_requires_authoritative_recent_case_image_evidence() -> None:
    assert "sent_message_summary.case_image_delivery" in PLANNER_SYSTEM_PROMPT
    assert "completed_pack_ids/completed_categories" in PLANNER_SYSTEM_PROMPT
    assert "不能单独证明客户近期看过图" in PLANNER_SYSTEM_PROMPT
    assert "没有权威近期图片证据时查 `case_studies`" in PLANNER_SYSTEM_PROMPT
    assert "做完到底能变成什么样" in PLANNER_SYSTEM_PROMPT


def test_planner_calibrates_current_severe_risk_without_hijacking_normal_safety_objections() -> None:
    assert "起泡且疼" in PLANNER_RISK_PATCH_PROMPT
    assert "过敏肿胀" in PLANNER_RISK_PATCH_PROMPT
    assert "会不会反黑/做坏/留疤" in PLANNER_RISK_PATCH_PROMPT
    assert "必须调用 professional_assist" in PLANNER_RISK_PATCH_PROMPT


def test_reply_safety_objection_keeps_one_natural_progression_action() -> None:
    assert "不要突然整段复述268、10、258和退款规则" in REPLY_SYSTEM_PROMPT
    assert "不要反问“更担心安全还是想看案例”" in REPLY_SYSTEM_PROMPT


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
    source = (ROOT / "ai_paths/app/prompts/sop_chat_gate.py").read_text(encoding="utf-8")
    for marker in [
        "客户回复城市、区、地标、定位",
        "需要真实门店事实",
        "选择 `ai_only`",
        "门店、定位、图片、订单",
    ]:
        assert marker in source


def test_sop_gate_prefers_activity_pack_for_activity_and_payment_questions() -> None:
    source = (ROOT / "ai_paths/app/prompts/sop_chat_gate.py").read_text(encoding="utf-8")
    for marker in [
        "活动、优惠、价格、多少钱、怎么参加、怎么预约、怎么付预约金、怎么报名",
        "s10_activity_intro",
        "不要只返回 `ai_only` 让普通 AI 空泛解释",
        "活动已经铺垫后再交 `ai_only` 给 Planner 处理发卡和交易事实",
    ]:
        assert marker in source


def test_store_distance_feedback_must_resume_mainline_without_reasking_store() -> None:
    sources = [
        (ROOT / "ai_paths/app/prompts/global_contract.py").read_text(encoding="utf-8"),
        (ROOT / "ai_paths/app/graph/planner/brain_v2_prompts.py").read_text(encoding="utf-8"),
        (ROOT / "ai_paths/app/prompts/reply_synthesizer.py").read_text(encoding="utf-8"),
        (ROOT / "ai_paths/app/prompts/sop_chat_gate.py").read_text(encoding="utf-8"),
    ]
    combined = "\n".join(sources)
    for marker in [
        "客户只是反馈远近",
        "不要继续追问",
        "恢复下一主线",
        "需求案例",
        "活动价格",
    ]:
        assert marker in combined
    assert "我再接着给您发流程" in combined
    assert "入口没对上" in sources[2]


def test_sop_gate_requires_contextual_first_text_and_preserves_numeric_facts() -> None:
    prompt = (ROOT / "ai_paths/app/prompts/sop_chat_gate.py").read_text(encoding="utf-8")
    source = prompt + (ROOT / "ai_paths/app/services/sop_execution_service.py").read_text(encoding="utf-8")
    for marker in [
        "接在当前对话后自然",
        "不得改变金额、价格、退款、时间、门店、效果边界",
        "text_adjustments",
        "message_operations",
        "insert_text_after",
        "payment_collection_gate.status",
        "remove_message",
        "payment_collection_blocked_by_paid_state",
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
    source = (ROOT / "ai_paths/app/prompts/sop_chat_gate.py").read_text(encoding="utf-8")
    for marker in [
        "项目是否真正包含斑点改善",
        "不能用宽泛项目介绍或案例包抢答",
        "是不是只有检测洗脸，没有去斑",
        "必须精准回答项目范围",
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
