from __future__ import annotations

from ai_paths.app.prompts.reply_synthesizer import (
    PARALLEL_REPLY_SYSTEM_PROMPT,
    _render_authoritative_facts,
    _render_delivery_assets,
    _render_knowledge_evidence,
    _render_reference_contract,
)
from ai_paths.app.prompts.v3_semantic_router import V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT


def test_router_is_retrieval_evidence_not_final_sales_decision() -> None:
    prompt = V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT

    assert "不是最终销售意图" in prompt
    assert "R8 会结合全部事实重新作最终判断" in prompt
    assert "例句不是关键词规则" in prompt
    assert "不写客户话术，不决定成交、付款、暂停或最终动作" in prompt


def test_prompts_preserve_outstanding_fulfillment_across_merged_messages() -> None:
    assert "后句催促/问机器人不撤销前句未完成的发图、地址或答题" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "地址则 store_query=store_detail 并沿用已确认门店" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT
    assert "后句催促或问机器人不撤销前句未完成的发图、发地址或答题请求" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "明确再次索要地址允许重发" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_remains_the_only_sales_decision_and_keeps_safety_boundaries() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "V3 唯一的最终销售大脑" in prompt
    assert "明确“别联系、别发了、不要打扰”" in prompt
    assert "有活动卡点时" in prompt
    assert "closing_decision 设为 pause" in prompt
    assert "门店查询只证明位置需求和本轮返回的公开门店事实" in prompt
    assert "活动和预约金分开" in prompt
    assert "简单问题默认只发一条文字" in prompt
    assert "回答较长时最多拆成两条自然微信" in prompt
    assert "不要把一个完整句子从中间硬切开" in prompt
    assert "分别答题/解卡和补一个不同价值" in prompt
    assert "禁止客服菜单" in prompt
    assert "不要从混乱、冲突、过期或测试历史里恢复旧门店" in prompt
    assert "权威事实、本轮确认、当前可确认、经核验" in prompt
    assert "第一次只解释，不主动输出 payment_collection" in prompt
    assert "任何场景都不得只返回内部决策而漏掉客户回复" in prompt
    assert "不能自造名称" in prompt
    assert "不等于停止销售" in prompt
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


def test_reply_uses_positive_evidence_before_effect_boundaries() -> None:
    assert "效果或信任顾虑要先建立信心，再管理个体差异" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不要先用“很难、不能、不一定”" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不得把“很多、不少、满意度较高”自行升级" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不能仅因话术 action 与序列节点不同就全部不用" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '`knowledge_use` 的唯一格式' in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '`knowledge_use` 是每轮固定输出的来源记录' in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '"knowledge_use":{"sequence_id":"","step_id":"","script_id":"","reason":""}' in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "也可以只选话术" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不等于停止销售" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "必须继续给一个无需当场决定的真实价值" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不得只说“您先忙、有空再联系”就结束" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_requires_safe_directly_relevant_script_for_active_blocker() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "必须选一个最相关序列和最多一个主话术" in prompt
    assert "长话术允许只取语义完整且安全的一两句" in prompt
    assert "所有候选都无关或冲突时允许 script_id 留空" in prompt
    assert "即使只改写文字、不发送配套媒体" in prompt
    contract = _render_reference_contract(
        {"follow_script_reference_options": [{"content_id": "follow_script:225:p1"}]},
        json_dumps=lambda value: str(value),
    )
    assert "也必须把对应数字话术ID填入 knowledge_use.script_id" in contract


def test_reply_directly_delivers_available_value_without_permission_gate() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "已经具备且可在本轮直接交付的明确价值，不再向客户索取许可" in prompt
    assert "要不要我发活动价" in prompt
    assert "要不要看效果图" in prompt
    assert "必须同轮交付" in prompt
    assert "先用一条短文字给信心和观看理由，再让素材紧跟在话术下面" in prompt
    assert "不能只写“我可以发给您”" in prompt


def test_reply_connects_store_detail_to_the_true_mainline_stage() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "门店详情不能只回答" in prompt
    assert "您大概工作日还是周末方便？我帮您做预约登记" in prompt
    assert "否则补最缺的效果或活动" in prompt
    assert "先答题/发卡，再问具体时段并说明用于预约登记" in prompt
    assert "直接导航过来就行" in prompt


def test_follow_script_media_is_rendered_as_directly_deliverable_asset() -> None:
    candidate = {
        "content_id": "follow_script:187:p1",
        "name": "侧面烘托 / 第1组",
        "purpose": "距离卡点 / 效果案例",
        "asset_role": "sales_reference",
        "delivery_status": "available",
        "delivery_observation": {"sent_count": 0},
        "messages": [
            {"type": "image", "content": "https://example.com/effect.png"},
        ],
    }
    rendered = _render_delivery_assets(
        [candidate],
        json_dumps=lambda value: str(value),
        relevant_fact_topic_ids=[],
    )

    assert "无可用素材" not in rendered
    assert "follow_script:187:p1" in rendered
    assert "距离卡点 / 效果案例" in rendered
    assert "https://example.com/effect.png" in rendered
    assert "不要先问客户要不要看" in rendered


def test_follow_script_knowledge_does_not_duplicate_media_urls() -> None:
    rendered = _render_knowledge_evidence(
        {
            "candidates": [
                {
                    "source_id": "187",
                    "script_id": "187",
                    "script_name": "侧面烘托",
                    "checkpoint_name": "距离卡点",
                    "action_name": "效果案例",
                    "paragraphs": [
                        {
                            "paragraph_no": 1,
                            "messages": [
                                {"type": "text", "content": "用效果价值处理距离顾虑"},
                                {"type": "image", "url": "https://example.com/effect.png"},
                            ],
                        }
                    ],
                }
            ]
        }
    )

    assert "用效果价值处理距离顾虑" in rendered
    assert "业务原始话术含素材：图片1个" in rendered
    assert "本轮是否仍可发送" in rendered
    assert "https://example.com/effect.png" not in rendered


def test_store_distance_objection_keeps_terminal_recommendation_boundary() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "不再追问同城地铁站、路口、楼栋或更细地址" in prompt
    assert "不得客观断言门店确实远或近" in prompt
    assert "只有客户给出不同城市才重新查店" in prompt


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


def test_reply_prompt_handles_generic_store_distrust_before_store_lookup() -> None:
    assert "有的店是骗子/不靠谱" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不要立刻把话题改成查附近门店" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "候选话术出现这些内容也必须丢弃" in PARALLEL_REPLY_SYSTEM_PROMPT
