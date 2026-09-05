from __future__ import annotations

from ai_paths.app.prompts.reply_synthesizer import PARALLEL_REPLY_SYSTEM_PROMPT
from ai_paths.app.prompts.v3_semantic_router import V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT


def test_router_is_retrieval_evidence_not_final_sales_decision() -> None:
    prompt = V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT

    assert "不是最终销售意图" in prompt
    assert "R8 会结合全部事实重新作最终判断" in prompt
    assert "例句不是关键词规则" in prompt
    assert "不写客户话术，不决定成交、付款、暂停或最终动作" in prompt


def test_reply_remains_the_only_sales_decision_and_keeps_safety_boundaries() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "V3 唯一的最终销售大脑" in prompt
    assert "明确“别联系、别发了、不要打扰”" in prompt
    assert "有活动卡点时" in prompt
    assert "closing_decision 设为 pause" in prompt
    assert "门店查询只证明位置需求和本轮返回的公开门店事实" in prompt
    assert "活动和预约金分开" in prompt


def test_reply_does_not_ask_model_for_code_derived_observation_fields() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "flow_action、策略/规则/节点名称和 decision_status 由代码派生，不要生成" in prompt
    assert "缺失不得改变客户回复或触发第二次业务判断" in prompt


def test_realtime_prompt_budgets_prevent_rule_bloat_regression() -> None:
    assert len(V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT) <= 8_200
    assert len(PARALLEL_REPLY_SYSTEM_PROMPT) <= 11_000
