from __future__ import annotations

import json

from ai_paths.app.prompts.reply_synthesizer import (
    PARALLEL_REPLY_SYSTEM_PROMPT,
    build_parallel_reply_messages,
    _render_authoritative_facts,
    _render_delivery_assets,
    _render_knowledge_evidence,
    _render_mainline_execution_contract,
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
    assert "不得谎称“我不是机器人/我是真人客服”" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_remains_the_only_sales_decision_and_keeps_safety_boundaries() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "V3 唯一的最终销售大脑" in prompt
    assert "明确“别联系、别发了、不要打扰”" in prompt
    assert "医疗高风险、具体严重客诉或退款纠纷" in prompt
    assert "只有明确停止联系才持久记录退订" in prompt
    assert "有活动卡点时" in prompt
    assert "cardpoint 为 active/repeated 时 closing 必须 pause" in prompt
    assert "门店查询只证明位置需求和本轮返回的公开门店事实" in prompt
    assert "首次泛问价格只报活动价和包含价值" in prompt
    assert "不设最低长度" in prompt
    assert "短承接可以只有几个字" in prompt
    assert "每条 text 目标约20–60个汉字" not in prompt
    assert "整轮严格遵守“本轮客户可见输出上限”" in prompt
    assert "不同义重复，不硬切句" in prompt
    assert "禁止客服菜单" in prompt
    assert "不要从混乱、过期或测试记录恢复旧话题" in prompt
    assert "权威事实、本轮确认、匹配门店、当前卡点" in prompt
    assert "首次泛问价格" in prompt and "绝不发 `payment_collection`" in prompt
    assert "先写非空 `reply_messages`" in prompt
    assert "只有明确停止联系才持久记录退订" in prompt
    assert "门店卡、姓名、电话和时间意向都不等于预约完成" in prompt
    assert prompt.index('"reply_messages"') < prompt.index('"sales_judgment"')


def test_reply_does_not_ask_model_for_code_derived_observation_fields() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "flow_action、策略/规则/节点名称和 decision_status 由代码派生，不要生成" in prompt
    assert "缺失不得改变客户回复或触发第二次业务判断" in prompt
    assert "secondary_tasks 最多 3 个真实目录对象且不重复主任务" in prompt


def test_realtime_prompt_budgets_prevent_rule_bloat_regression() -> None:
    assert len(V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT) <= 6_500
    assert len(PARALLEL_REPLY_SYSTEM_PROMPT) <= 7_000


def test_generic_price_question_does_not_volunteer_scope_jargon() -> None:
    assert "首次泛问价格只报活动价和包含价值，不主动说“单部位体验”" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_uses_positive_evidence_before_effect_boundaries() -> None:
    assert "效果或信任顾虑要先建立信心，再管理个体差异" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不要先用“很难、不能、不一定”" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不得把“很多、不少、满意度较高”自行升级" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不能仅因话术 action 与序列节点不同就全部不用" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '`knowledge_use` 的唯一格式' in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '`knowledge_use` 是每轮固定输出的来源记录' in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '"knowledge_use":{"sequence_id":"","step_id":"","script_id":"","reason":""}' in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "也可以只选话术" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "首次无因软拒绝" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "重复暂缓优先" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "临时不可交流" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "软拒绝不能退化" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_requires_safe_directly_relevant_script_for_active_blocker() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "必须选一个最相关序列和最多一个主话术" in prompt
    assert "长话术只取语义完整且安全的一两句" in prompt
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
    assert "不要问“要不要看”" in prompt
    assert "必须同轮短文字引出并交付" in prompt
    assert "同轮短文字引出并交付一个 `selected_content_ids`" in prompt
    assert "不能只写“我可以发给您”" in prompt


def test_reply_connects_store_detail_to_the_true_mainline_stage() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "详情问答不重复地址卡，先完整回答" in prompt
    assert "只有语境自然且主线允许时才衔接下一机会" in prompt
    assert "行动意愿才可优先于 next_missing_stage" in prompt
    assert "单独“发位置、可以、有时间”" in prompt
    assert "发位置/地址”本身不等于要预约" in prompt
    assert "到店不用等、优先接待" in prompt
    assert "不得说可直接到店" in prompt
    assert "明确再次索要地址/位置/导航时必须重发" in prompt
    assert "不能把预约问句伪标成 ask_missing_fact/deliver_value" in prompt


def test_dynamic_mainline_contract_blocks_customer_visible_booking_shortcut() -> None:
    rendered = _render_mainline_execution_contract(
        {
            "next_missing_stage": "activity_offer",
            "allowed_next_sales_action_types": ["send_store", "explain_activity"],
        }
    )

    assert "本轮禁止邀请预约" in rendered
    assert "询问工作日/周末或到店时间" in rendered
    assert "最早缺失主线=activity_offer" in rendered
    assert "权威活动价格或包含价值" in rendered
    assert "只有决定在本轮衔接活动机会时" in rendered
    assert "不要求本轮立即执行" in rendered

    effect = _render_mainline_execution_contract(
        {
            "next_missing_stage": "effect_evidence",
            "allowed_next_sales_action_types": ["deliver_value", "send_effect_material"],
        }
    )
    assert "到店看效果/方案、先留名额、要不要看案例、我先把效果说明发您" in effect
    assert "选择该动作且有可用案例时直接发送" in effect
    assert "不能只预告以后再讲" in effect


def test_router_treats_explicit_location_delivery_as_store_detail_not_booking() -> None:
    prompt = V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT

    assert "发位置/发地址/发导航" in prompt
    assert "store_query.required=true" in prompt
    assert "purpose=store_detail" in prompt
    assert "不把“可以”升级成预约意愿" in prompt
    assert "不能输出 already_completed 或 none" in prompt


def test_reply_does_not_promise_unavailable_body_part_material() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "本轮没有该部位真实素材时" in prompt
    assert "不得承诺去找、稍后发" in prompt
    assert "不得声称到店一定能看对应案例" in prompt


def test_reply_keeps_pure_life_sharing_natural() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "纯确认、感谢、玩笑、夸赞、祝福、表情或生活分享" in prompt
    assert "可只回应并 `keep_open`" in prompt


def test_reply_treats_mainline_as_an_opportunity_not_a_per_turn_obligation() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "next_missing_stage" in prompt
    assert "不是本轮必须执行的任务" in prompt
    assert "只有当前问题处理后、客户仍适合交流且衔接自然时" in prompt
    assert "直接回答或自然承接可用 `keep_open`" in prompt


def test_reply_separates_temporary_unavailability_from_soft_refusal() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "pause_current_turn + keep_open" in prompt
    assert "只短承接，不补销售价值、不追问时间" in prompt
    assert "首次无因软拒绝（如“我考虑一下”）且历史未问过，必须问真实顾虑" in prompt
    assert "首次无因软拒绝且历史未问过只能 ask_missing_fact" in prompt
    assert "不得再问或塞同一价值" in prompt


def test_reply_isolates_internal_router_and_training_language() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "回复不是决策报告" in prompt
    assert "不复述 Router 字段" in prompt
    assert "匹配门店、当前卡点、主要担心的是" in prompt
    assert "禁止客服菜单、培训稿" in prompt
    assert "审计腔" in prompt
    assert "客户只说“说人话/别总结”且无业务请求时，只短答“好，你说”并 `keep_open`" in prompt


def test_reply_context_prioritizes_chat_and_facts_before_mainline_and_router() -> None:
    messages = build_parallel_reply_messages(
        {
            "presentation_limits": {"max_messages": 8, "max_text_chars": 300},
            "mainline_delivery_state": {
                "next_missing_stage": "effect_evidence",
                "allowed_next_sales_action_types": ["deliver_value", "keep_open"],
            },
            "evidence": {
                "shared_context": {
                    "current_time": {"iso": "2026-09-10T12:00:00+08:00", "timezone": "Asia/Shanghai"},
                    "current_message": {"content": "好", "message_ref": "current_message"},
                    "conversation": [
                        {"role": "customer", "content": "好", "message_ref": "current_message"}
                    ],
                },
                "semantic_route": {},
            },
        },
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False),
    )
    content = messages[1]["content"]

    assert content.index("【当前时间】") < content.index("【完整聊天】")
    assert content.index("【完整聊天】") < content.index("【当前结构事实与不能越过的边界】")
    assert content.index("【当前结构事实与不能越过的边界】") < content.index(
        "【销售主线机会与本轮动作边界（不是每轮流程任务）】"
    )
    assert content.index("【销售主线机会与本轮动作边界（不是每轮流程任务）】") < content.index(
        "【Router 辅助检索判断：只作内部证据，不得复述给客户】"
    )
    assert content.index("【Router 辅助检索判断：只作内部证据，不得复述给客户】") < content.index(
        "【本轮客户可见输出上限（只有上限，没有最低长度）】"
    )


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


def test_generic_price_context_does_not_volunteer_single_area_wording() -> None:
    rendered = _render_authoritative_facts(
        {"AUTHORITATIVE FACTS": {"offer": {"new_customer_price": 268}}},
        topic_ids=["activity_offer"],
    )

    assert "普通询价不主动补‘单部位体验’" in rendered
    assert "客户明确问单/多部位范围时再按事实解释" in rendered


def test_reply_prompt_handles_generic_store_distrust_before_store_lookup() -> None:
    assert "客户只泛称某些店“骗子/不靠谱”" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不把普通信任质疑升级为投诉或停止营销" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "先交付一个最相关的真实信任证据或效果素材" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "没有任何可用事实或素材时才只问其具体遇到了什么" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "候选话术仅在含无依据事实时丢弃" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "本轮唯一正确下一步是先问其遇到了什么" not in PARALLEL_REPLY_SYSTEM_PROMPT
