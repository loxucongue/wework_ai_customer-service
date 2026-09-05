from __future__ import annotations

from ai_paths.app.prompts.reply_synthesizer import PARALLEL_REPLY_SYSTEM_PROMPT, _render_authoritative_facts
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
    assert "简单问题默认只发一条文字" in prompt
    assert "禁止客服菜单" in prompt
    assert "历史相关性是硬门槛" in prompt
    assert "询价、优惠、效果等新问题本身不构成续接门店的理由" in prompt
    assert "权威事实、本轮确认、当前可确认、经核验" in prompt
    assert "第一次只解释，不主动输出 payment_collection" in prompt
    assert "任何场景都不得只返回内部决策而漏掉客户回复" in prompt
    assert "不能自造名称" in prompt
    assert "等时间方便时再聊" in prompt
    assert "不确认广告案例为真" in prompt
    assert "不得说到店还能争取活动价" in prompt
    assert "只是提交到店意向，不等于门店和档期已确认" in prompt
    assert prompt.index('"reply_messages"') < prompt.index('"sales_judgment"')


def test_reply_does_not_ask_model_for_code_derived_observation_fields() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "flow_action、策略/规则/节点名称和 decision_status 由代码派生，不要生成" in prompt
    assert "缺失不得改变客户回复或触发第二次业务判断" in prompt
    assert "secondary_tasks 最多 3 个真实目录对象且不重复主任务" in prompt


def test_realtime_prompt_budgets_prevent_rule_bloat_regression() -> None:
    assert len(V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT) <= 8_200
    assert len(PARALLEL_REPLY_SYSTEM_PROMPT) <= 9_000


def test_router_keeps_price_intents_semantically_separate() -> None:
    prompt = V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT

    assert "多少钱、费用怎么算、包含什么" in prompt
    assert "还能便宜吗、有优惠吗、预算不够" in prompt
    assert "到店会不会加钱、会不会强制消费" in prompt
    assert "别家更便宜、其他家才多少钱" in prompt
    assert "不得挑一个语义相邻但不真实的标签" in prompt


def test_reply_always_receives_online_project_scope_boundary() -> None:
    rendered = _render_authoritative_facts(
        {
            "AUTHORITATIVE FACTS": {
                "offer": {
                    "scope_answer_policy": "除皱不属于线上活动范围",
                }
            }
        },
        topic_ids=["effect_evidence"],
    )

    assert "除皱不属于线上活动范围" in rendered
    assert "不得确认‘那个案例是真的’" in rendered
