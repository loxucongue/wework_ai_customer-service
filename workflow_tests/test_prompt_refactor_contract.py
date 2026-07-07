from __future__ import annotations

from pathlib import Path

from app.graph.nodes.image_info import build_vision_prompt
from app.graph.planner.brain_v2_prompts import PLANNER_SYSTEM_PROMPT
from app.prompts.profile_analyzer import PROFILE_ANALYZER_SYSTEM_PROMPT
from app.prompts.reply_synthesizer import REPLY_SYSTEM_PROMPT
from app.services.outreach_prompts import OUTREACH_MESSAGE_SYSTEM_PROMPT, OUTREACH_PLAN_SYSTEM_PROMPT


ROOT = Path(__file__).resolve().parents[1]
PROMPT_FILES = [
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


def test_planner_prompt_is_intent_driven_and_keeps_business_boundaries() -> None:
    for marker in [
        "Source Priority",
        "Decision SOP",
        "Tool Map",
        "Negative Cases",
        "Few-Shot Calibration",
        "不新增 thought",
    ]:
        assert marker in PLANNER_SYSTEM_PROMPT

    for business_rule in [
        "payment_collection_sent 不是硬去重",
        "不要求客户必须说没收到或再发",
        "到店抵扣，不做退10元",
        "2位一共20元，3位一共30元，4位一共40元",
        "客户可见回复只说哪家更近，不说公里、分钟、车程",
        "旧健康风险、旧门店、旧预约任务只有在客户当前明确延续时才主导本轮",
        "客户给出明确城市、区域或地标并问门店/附近/地址/停车/营业时间/导航时，输出 need_tools",
    ]:
        assert business_rule in PLANNER_SYSTEM_PROMPT


def test_reply_prompt_has_fact_priority_examples_and_customer_rules() -> None:
    for marker in ["Response SOP", "Fact Source Priority", "Message Map", "Few-Shot Calibration"]:
        assert marker in REPLY_SYSTEM_PROMPT

    for business_rule in [
        "先肯定对应需求可以做、这类大多数客户改善反馈不错",
        "到店抵扣，不做退10元",
        "10/20/30/40",
        "human_handoff_notice",
        "旧画像健康风险、旧门店、旧预约任务不得覆盖客户当前普通问题",
        "到店先做皮肤检测/专业检测",
    ]:
        assert business_rule in REPLY_SYSTEM_PROMPT


def test_profile_prompt_downgrades_stale_history_without_dropping_facts() -> None:
    for marker in ["Source Priority", "Analysis SOP", "Negative Cases"]:
        assert marker in PROFILE_ANALYZER_SYSTEM_PROMPT
    assert "旧健康风险、旧门店、旧预约任务只有在本轮客户继续提到时" in PROFILE_ANALYZER_SYSTEM_PROMPT
    assert "系统发过 payment_collection 不等于客户已支付" in PROFILE_ANALYZER_SYSTEM_PROMPT


def test_vision_prompt_is_sectioned_json_only_and_non_diagnostic() -> None:
    prompt = build_vision_prompt({"normalized_content": "脸上有斑", "conversation_history": []})
    for marker in ["Vision Node Role", "Image Analysis Policy", "Do Not", "Output Schema"]:
        assert marker in prompt
    assert "不写黄褐斑、皮炎、感染等诊断词" in prompt
    assert "不输出治疗结论、疾病判断、保证效果、同等效果承诺" in prompt


def test_sop_and_outreach_prompts_keep_gate_boundaries() -> None:
    sop_source = (ROOT / "ai_paths/app/services/sop_execution_service.py").read_text(encoding="utf-8")
    for marker in [
        "Source Priority",
        "Negative Cases",
        "Few-Shot Calibration",
        "默认按照平台 SOP 全流程发送",
        "明确拒发理由只有四类",
        "冲突",
        "严重重合",
    ]:
        assert marker in sop_source

    for prompt in [OUTREACH_PLAN_SYSTEM_PROMPT, OUTREACH_MESSAGE_SYSTEM_PROMPT]:
        assert "Negative Cases" in prompt
        assert "Few-Shot Calibration" in prompt
        assert "支付失败" in prompt
