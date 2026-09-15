from __future__ import annotations

import json

from ai_paths.app.prompts.reply_synthesizer import (
    PARALLEL_REPLY_SYSTEM_PROMPT,
    build_parallel_reply_messages,
    _render_authoritative_facts,
    _render_activity_delivery_checklist,
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
    assert "明确再次索要地址/位置/导航时必须重发" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不得谎称“我不是机器人/我是真人客服”" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_remains_the_only_sales_decision_and_keeps_safety_boundaries() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "V3 唯一的最终销售大脑" in prompt
    assert "明确退订" in prompt
    assert "医疗高风险" in prompt and "具体严重客诉/退款纠纷" in prompt
    assert "只有明确退订形成永久 stop-contact" in prompt
    assert "活动卡点 active/repeated 时先解卡" in prompt
    assert "`closing=pause`" in prompt
    assert "门店查询只证明位置需求和本轮返回的公开门店事实" in prompt
    assert "首次泛问价格只报活动价和包含价值" in prompt
    assert "不设最低长度" in prompt
    assert "短承接可以只有几个字" in prompt
    assert "每条 text 目标约20–60个汉字" not in prompt
    assert "遵守本轮输出上限" in prompt
    assert "仅删套话与重复" in prompt
    assert "不硬切句" in prompt
    assert "禁止客服菜单" in prompt
    assert "不从混乱、过期或测试记录恢复旧话题" in prompt
    assert "权威事实、本轮确认、匹配门店、当前卡点" in prompt
    assert "首次泛问价格" in prompt and "不发 `payment_collection`" in prompt
    assert "以下四项必须是顶层同级字段，禁止嵌套" in prompt
    assert "`send_payment` 必须带付款卡，禁说确认后再发" in prompt
    assert "只有明确退订形成永久 stop-contact" in prompt
    assert "门店卡、姓名、电话和时间意向都不等于预约完成" in prompt
    assert prompt.index('"reply_messages"') < prompt.index('"sales_judgment"')


def test_reply_does_not_ask_model_for_code_derived_observation_fields() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "flow_action、策略/规则/节点名称和 decision_status 由代码派生，不要生成" in prompt
    assert "缺失不得改变客户回复或触发第二次业务判断" in prompt
    assert "secondary_tasks 最多3个且不重复主任务" in prompt


def test_realtime_prompt_budgets_prevent_rule_bloat_regression() -> None:
    assert len(V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT) <= 6_500
    assert len(PARALLEL_REPLY_SYSTEM_PROMPT) <= 7_000


def test_generic_price_question_does_not_volunteer_scope_jargon() -> None:
    assert "首次泛问价格只报活动价和包含价值，不主动说“单部位体验”" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_uses_positive_evidence_before_effect_boundaries() -> None:
    assert "效果或信任顾虑要先建立信心，再管理个体差异" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不要先用“很难、不能、不一定”" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不得把“很多、不少、满意度较高”自行升级" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "可以使用、改写或跳过" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '`knowledge_use` 固定输出 sequence_id/step_id/script_id/reason' in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '只记录实际采用的真实 ID，未采用全部留空' in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '"knowledge_use":{"sequence_id":"","step_id":"","script_id":"","reason":""}' in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "候选序列提供节奏，候选话术提供论据" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不强制每次“考虑一下”都追问" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "已经反复讨论且没有新论据时短承接" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "客户正在开车、工作、休息" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "代替已经成熟的销售动作" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_allows_only_safe_relevant_arguments_without_forced_adoption() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "可以使用、改写或跳过" in prompt
    assert "不为凑采用率" in prompt
    assert "实际采用某一思路、论据或社会证明才复制真实 ID" in prompt
    contract = _render_reference_contract(
        {"follow_script_reference_options": [{"content_id": "follow_script:225:p1"}]},
        json_dumps=lambda value: str(value),
    )
    assert "也必须把对应数字话术ID填入 knowledge_use.script_id" in contract


def test_reply_directly_delivers_available_value_without_permission_gate() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "能用本轮事实直接回答或交付时就直接做" in prompt
    assert "不要问“要不要看”" in prompt
    assert "必须同轮短文字引出并交付" in prompt
    assert "不能只写“我可以发给您”" in prompt


def test_reply_connects_store_detail_to_the_true_mainline_stage() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "停车、营业时间先完整回答且不重复地址卡" in prompt
    assert "只有语境自然且主线允许时才衔接下一机会" in prompt
    assert "单独“发位置、可以、有时间”" in prompt
    assert "到店不用等或优先接待" in prompt
    assert "不得说可直接到店" in prompt
    assert "明确再次索要地址/位置/导航时必须重发" in prompt
    assert "只有语境自然且主线允许时才衔接下一机会" in prompt


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
    assert "完整说明权威价格、包含价值和相关条件权益" in rendered
    assert "认可具体效果/方案" in rendered
    assert "明确说顾虑已解除" in rendered
    assert "不是每轮任务" in rendered

    effect = _render_mainline_execution_contract(
        {
            "next_missing_stage": "effect_evidence",
            "allowed_next_sales_action_types": ["deliver_value", "send_effect_material"],
        }
    )
    assert "到店看效果/方案、先留名额、要不要看案例、我先把效果说明发您" in effect
    assert "选择该动作且有可用案例时直接发送" in effect
    assert "不能只预告以后再讲" in effect
    assert "主动提交肤质或部位" in effect

    store = _render_mainline_execution_contract(
        {
            "next_missing_stage": "store",
            "allowed_next_sales_action_types": ["ask_missing_fact", "send_store"],
        }
    )
    assert "认可价格且缺城市时必须直接问城市" in store

    payment = _render_mainline_execution_contract(
        {
            "next_missing_stage": "appointment",
            "allowed_next_sales_action_types": ["invite_booking", "send_payment"],
        }
    )
    assert "明确索要付款入口且 payment_card_available=true" in payment
    assert "人数未知按单人10元交付，不再追问人数" in payment


def test_router_treats_explicit_location_delivery_as_store_detail_not_booking() -> None:
    prompt = V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT

    assert "发位置/发地址/发导航" in prompt
    assert "store_query.required=true" in prompt
    assert "purpose=store_detail" in prompt
    assert "不把“可以”升级成预约意愿" in prompt
    assert "不能输出 already_completed 或 none" in prompt


def test_reply_does_not_promise_unavailable_body_part_material() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "客户指定部位而没有对应素材时如实说明" in prompt
    assert "不虚构或承诺寻找" in prompt


def test_reply_keeps_pure_life_sharing_natural() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "普通确认、感谢、祝福、玩笑、单独表情" in prompt
    assert "只按当前语境承接" in prompt


def test_reply_treats_mainline_as_an_opportunity_not_a_per_turn_obligation() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "next_missing_stage" in prompt
    assert "不构成本轮必须聊该话题的命令" in prompt
    assert "积极完成一个相邻动作" in prompt


def test_reply_separates_temporary_unavailability_from_soft_refusal() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "明确稍后聊" in prompt
    assert "原因未知且澄清能改变回应时自然问一句" in prompt
    assert "已有足够信息可直接给价值" in prompt
    assert "已经反复讨论且没有新论据时短承接" in prompt


def test_reply_isolates_internal_router_and_training_language() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "Router、主线、序列和话术只提供证据与方向" in prompt
    assert "匹配门店、当前卡点、主要担心的是" in prompt
    assert "禁止客服菜单、培训稿" in prompt
    assert "内部审计语言" in prompt


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

    for title in ("当前对话", "已知客户情况", "已交付内容", "相关事实与执行边界", "候选论据与销售方向", "输出要求"):
        assert f"【{title}】" in content
    assert content.index("【当前对话】") < content.index("【已知客户情况】")
    assert content.index("【已知客户情况】") < content.index("【已交付内容】")
    assert content.index("【已交付内容】") < content.index("【相关事实与执行边界】")
    assert content.index("【相关事实与执行边界】") < content.index("【候选论据与销售方向】")


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


def test_store_distance_objection_is_contextual_not_a_fixed_reply_recipe() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "有顾虑就回应真正影响当前决定的顾虑" in prompt
    assert "候选序列提供节奏，候选话术提供论据" in prompt
    assert "第一句只用" not in prompt


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


def test_activity_stage_receives_dynamic_complete_offer_without_payment_card() -> None:
    rules = {
        "AUTHORITATIVE FACTS": {
            "offer": {
                "public_names": ["测试活动"],
                "new_customer_price": 321,
                "includes": ["项目甲", "权益乙"],
                "body_scope": "测试范围",
                "offer_structure": "测试活动结构",
                "registration_skin_test": "先完成测试资格登记",
                "registration_gift": {"gift": "权益礼"},
                "quota": "测试名额规则",
            }
        }
    }
    rendered = _render_activity_delivery_checklist(
        rules,
        mainline_delivery_state={"next_missing_stage": "activity_offer"},
    )

    for expected in (
        "活动价格=321",
        "包含项目=项目甲、权益乙",
        "适用范围=测试范围",
        "资格/预约条件=先完成测试资格登记",
        "活动权益=gift=权益礼",
        "名额口径=测试名额规则",
    ):
        assert expected in rendered

    context = build_parallel_reply_messages(
        {
            "mainline_delivery_state": {
                "next_missing_stage": "activity_offer",
                "allowed_next_sales_action_types": ["explain_activity", "keep_open"],
            },
            "structured_delivery_options": {
                "payment_collection": {"amount": 10, "remark": "不应出现"}
            },
            "evidence": {
                "semantic_route": {"relevant_fact_topic_ids": ["effect_evidence"]},
                "shared_context": {
                    "rules": rules,
                    "authoritative_facts": {},
                    "conversation": [],
                    "current_message": {"content": "这个效果可以"},
                },
            },
        },
        json_dumps=json.dumps,
    )[1]["content"]

    assert "活动价=321元" in context
    assert "包含项目=项目甲、权益乙" in context
    assert "payment_collection｜原样使用" not in context


def test_reply_prompt_handles_generic_store_distrust_before_store_lookup() -> None:
    assert "客户只泛称某些店“骗子/不靠谱”" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不把普通信任质疑升级为投诉或停止营销" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "使用一个最相关的真实证据" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "没有时再澄清具体顾虑" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "只丢弃冲突部分" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "本轮唯一正确下一步是先问其遇到了什么" not in PARALLEL_REPLY_SYSTEM_PROMPT
