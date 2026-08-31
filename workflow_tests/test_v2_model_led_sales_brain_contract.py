from __future__ import annotations

import inspect
import json

from app.graph.nodes.parallel_reply_chain import (
    _content_gate_shared_context,
    _tool_planner_shared_context,
    _v2_activity_offer_delivered,
    create_parallel_evidence_node,
)
from app.graph.nodes.v2_derived_observations import build_v2_derived_observations
from app.graph.nodes.reply_quality import collect_reply_observation_metrics
from app.graph.nodes.reply_nodes import (
    _parallel_generic_reply_repair_messages,
    _reply_retry_messages,
    _validate_parallel_raw_reply_schema,
)
from app.graph.nodes.reply_validation import (
    _validate_parallel_claimed_deposit_evidence,
    _validate_parallel_media_facts,
    _validate_parallel_payment_boundaries,
    _validate_parallel_selected_content_delivery,
    _validate_store_address_message_facts,
    _validate_store_resolution_delivery_mode,
    _validate_store_resolution_v2_contract,
)
from app.graph.nodes.v2_reply_admission import (
    _validate_structured_delivery_conversation_shape,
    validate_model_led_reply_admission,
    validate_v2_reply_admission,
)
from app.policies.business_rules import parallel_reply_business_rules_for_model
from app.prompts.reply_synthesizer import (
    PARALLEL_REPLY_SYSTEM_PROMPT,
    _compact_reply_status,
    _render_authoritative_facts,
    _render_registration_fact_status,
    _render_tool_facts,
    _render_must_follow,
    build_parallel_reply_messages,
    restore_reply_output_references,
)
from app.prompts.v3_sop_chat_gate import (
    PARALLEL_CONTENT_GATE_SYSTEM_PROMPT,
    SOP_CHAT_GATE_SYSTEM_PROMPT,
    build_sop_chat_gate_messages,
)


def test_reply_distinguishes_payment_information_from_payment_action() -> None:
    assert "客户只问付款规则，表示正在了解交易事实，不自动等于当前要付款" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "客户尚未进入付款讨论时" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_corrects_conflicting_customer_fact_before_answering() -> None:
    assert "先明确否定或纠正该具体内容" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不能先说“对、是的、没错”" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "自然承接、共情和赞同也必须服从权威事实" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "先肯定一个错误结论" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_status_exposes_missing_store_and_appointment_as_facts() -> None:
    status = _compact_reply_status(
        {
            "orders_and_payment": {"resolved_payment": {"deposit_state": "required_unpaid"}},
            "request_store_facts": {},
            "registration_facts": {},
            "sent_messages": {},
        }
    )

    assert status["当前门店"] == "没有已确认成交门店"
    assert status["当前预约"] == "无权威预约事实"
    assert status["当前事实边界"] == [
        "没有已确认成交门店；不能声称已预约、已登记或已安排到店",
        "没有权威预约、排客或接待位事实；不能声称已经留位或安排完成",
        "没有权威登记完成事实，不能声称已经记录客户到店意向",
        "预约金未付，不能声称已经留好活动名额",
    ]


def test_reply_status_distinguishes_delivered_store_candidates_from_confirmed_store() -> None:
    status = _compact_reply_status(
        {
            "request_store_facts": {},
            "sent_messages": {
                "store_address_delivery": {
                    "latest_batch_store_ids": ["587", "373", "282"],
                    "latest_batch_count": 3,
                }
            },
        }
    )

    assert status["当前门店"] == {
        "状态": "最近已交付候选门店，尚未确认成交门店",
        "候选数量": 3,
        "候选门店ID": ["587", "373", "282"],
    }
    assert status["当前事实边界"][0] == (
        "最近已交付候选门店不等于客户已选定成交门店；不能把候选发送说成已预约、已登记或已安排到店"
    )


def test_reply_status_treats_normalized_none_appointment_as_inactive() -> None:
    status = _compact_reply_status(
        {
            "orders_and_payment": {
                "appointment": {
                    "has_active": False,
                    "status": "none",
                    "source": "none",
                }
            }
        }
    )

    assert "没有权威预约、排客或接待位事实" in "；".join(status["当前事实边界"])


def test_reply_status_keeps_confirmed_appointment_without_false_boundary() -> None:
    status = _compact_reply_status(
        {
            "orders_and_payment": {
                "appointment": {
                    "has_active": True,
                    "status": "confirmed",
                    "appointment_time": "2026-08-27 14:00",
                }
            }
        }
    )

    assert "没有权威预约、排客或接待位事实" not in "；".join(status["当前事实边界"])


def test_reply_status_does_not_treat_string_false_as_active_appointment() -> None:
    status = _compact_reply_status(
        {
            "orders_and_payment": {
                "appointment": {"has_active": "false", "status": "none"}
            }
        }
    )

    assert "没有权威预约、排客或接待位事实" in "；".join(status["当前事实边界"])


def test_reply_status_hides_raw_order_query_exception() -> None:
    status = _compact_reply_status(
        {
            "orders_and_payment": {
                "resolved_payment": {"deposit_state": "required_unpaid"},
                "orders_error": "RuntimeError: platform params and stack trace",
            },
            "request_store_facts": {},
            "registration_facts": {},
            "sent_messages": {},
        }
    )

    assert status["订单"]["query_status"] == "查询未完整返回"
    assert "RuntimeError" not in json.dumps(status, ensure_ascii=False)


def test_unverified_paid_registration_context_only_allows_payment_verification() -> None:
    rendered = _render_registration_fact_status({"authoritative_paid": False})

    assert "尚未权威核实为已付" in rendered
    assert "只能核对付款方式或凭证" in rendered
    assert "不得按已付收姓名、电话、门店或到店意向" in rendered


def test_store_search_incomplete_context_uses_final_conclusion_without_raw_errors() -> None:
    rendered = _render_tool_facts(
        {
            "normalized_tool_facts": {
                "usable_facts": [
                    "customer_store_lookup: matched_stores=0 status=no_candidate_stores tool_error=RuntimeError"
                ],
                "structured_facts": {
                    "store_resolution_fact": {
                        "status": "search_incomplete",
                        "raw_place": "遂宁市",
                        "candidate_search_complete": False,
                    },
                    "tool_errors": [
                        {"tool": "customer_store_lookup", "error": "RuntimeError: invalid account"}
                    ],
                },
            },
            "authority_conflicts": ["store fact conflict"],
        },
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )

    assert "查询未完整返回" in rendered
    assert "不能判断当地有店、无店或可以安排到店" in rendered
    assert "RuntimeError" not in rendered
    assert "no_candidate_stores" not in rendered
    assert "门店查询状态" not in rendered
    assert "权威冲突" not in rendered


def test_raw_tool_failure_is_rendered_without_exception_payload() -> None:
    rendered = _render_tool_facts(
        {
            "tool_facts": {
                "customer_store_lookup": {
                    "status": "failed",
                    "error": "RuntimeError: invalid account with internal parameters",
                }
            }
        },
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )

    assert rendered == "customer_store_lookup：查询未完整返回"


def test_v3_effect_fact_is_pure_fact_without_legacy_scene_procedure() -> None:
    rules = parallel_reply_business_rules_for_model()
    rendered = _render_authoritative_facts(rules, topic_ids=["effect_evidence"])

    assert "绝大多数客户都是一次就好" in rendered
    assert "不得承诺所有客户一次全部去除" not in rendered
    assert "先明确说明当前淡斑效果活动价" not in rendered
    assert "客户问一次效果" not in rendered


def test_reply_treats_hard_boundaries_as_constraints_not_customer_disclaimers() -> None:
    assert "硬边界只负责限制不能越过什么" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不要主动补“不保证、不一定、每个人不一样、不能百分百”" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "内部效果边界" not in _render_authoritative_facts(
        parallel_reply_business_rules_for_model(),
        topic_ids=["effect_evidence"],
    )


def test_reply_context_places_authoritative_facts_after_reference_scripts() -> None:
    messages = build_parallel_reply_messages(
        {
            "evidence": {
                "shared_context": {
                    "current_message": {"message_ref": "current_message", "content": "一次能好吗"},
                    "conversation": [],
                    "authoritative_facts": {},
                    "rules": parallel_reply_business_rules_for_model(),
                },
                "semantic_route": {"relevant_fact_topic_ids": ["effect_evidence"]},
                "knowledge_evidence": {"candidates": [{"source_id": "D01", "reference_text": "参考表达"}]},
            },
            "valid_message_refs": ["current_message"],
            "valid_customer_message_refs": ["current_message"],
        },
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )
    context = messages[1]["content"]

    assert context.index("【跟进序列与优秀话术参考】") < context.index("【本轮相关权威事实：最终口径】")


def test_reply_context_orders_router_relevant_assets_before_other_candidates() -> None:
    messages = build_parallel_reply_messages(
        {
            "evidence": {
                "shared_context": {
                    "current_message": {"message_ref": "current_message", "content": "多少钱"},
                    "conversation": [],
                    "authoritative_facts": {},
                    "rules": parallel_reply_business_rules_for_model(),
                },
                "semantic_route": {"relevant_fact_topic_ids": ["activity_offer"]},
                "content_candidates": [
                    {
                        "content_id": "case_asset",
                        "name": "效果图",
                        "asset_role": "effect_evidence",
                        "messages": [{"type": "image", "content": "https://example.invalid/case.jpg"}],
                    },
                    {
                        "content_id": "s10_activity_intro",
                        "name": "活动介绍",
                        "asset_role": "activity_offer",
                        "messages": [{"type": "image", "content": "https://example.invalid/activity.jpg"}],
                    },
                ],
            },
            "valid_message_refs": ["current_message"],
            "valid_customer_message_refs": ["current_message"],
        },
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )
    context = messages[1]["content"]

    assert context.index("素材 s10_activity_intro") < context.index("素材 case_asset")
    assert "相关性：与 Router 本轮选择的事实主题直接对应" in context


def test_reply_content_selection_contract_assigns_only_passive_media_delivery_to_code() -> None:
    assert "系统只会把该候选中已经配置的图片或视频原样交付" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不会替你选择资产、补客户文案、发门店卡或发付款卡" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "候选中的图片和视频会按该 ID 原样交付，你不必复制 URL" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "处理思路与视觉凭证用途不同，不算重复" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_active_parallel_repair_is_readable_and_only_downgrades_unsupported_side_effects() -> None:
    source = inspect.getsource(_parallel_generic_reply_repair_messages)

    assert "不是第二个销售大脑" in source
    assert "副作用条件无法证明" in source
    assert "不得重新判断客户心理" in source


def test_parallel_reply_receives_model_led_sales_principles_without_scene_catalog() -> None:
    rules = parallel_reply_business_rules_for_model()

    assert "先解决客户此刻真正关心的问题" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不做被动客服" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "客户说 X" not in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "scene_catalog" not in rules
    assert "conversion_stage" not in str(rules)


def test_derived_observations_are_raw_rebuildable_values_without_predicates() -> None:
    result = build_v2_derived_observations(
        conversation=[
            {
                "message_ref": "chat_1",
                "role": "assistant",
                "content": "活动图已发",
                "sent_at": "2026-08-13T10:00:00+08:00",
            },
            {
                "message_ref": "chat_2",
                "role": "customer",
                "content": "我考虑一下",
                "sent_at": "2026-08-13T10:01:00+08:00",
            },
        ],
        history_events=[
            {
                "event_type": "activity_intro_image_sent",
                "created_at": "2026-08-13T10:00:00+08:00",
                "source_ref": "event:activity_1",
            },
            {
                "event_type": "v2_reply_model_observation",
                "created_at": "2026-08-13T10:00:30+08:00",
                "source_ref": "event:observation_1",
                "facts": {
                    "primary_objective": "说明活动价值",
                    "customer_friction_observation": "客户说要考虑",
                },
            },
        ],
        current_message={
            "content": "我考虑一下",
            "sent_at": "2026-08-13T10:01:00+08:00",
        },
    )

    serialized = str(result).lower()
    assert result["recent_asset_deliveries"][0]["source_refs"]
    assert result["prior_model_observations"][0]["authority"] == "v2_prior_model_observation_not_customer_fact"
    assert "high_intent" not in serialized
    assert "should_close" not in serialized
    assert "objection_resolved" not in serialized


def test_derived_observations_keep_v2_and_v3_model_observations_separate() -> None:
    history_events = [
        {
            "event_type": "v2_reply_model_observation",
            "event_id": "v2_obs",
            "created_at": "2026-08-13T10:00:00+08:00",
            "facts": {
                "primary_objective": "V2 目标",
                "customer_friction_observation": "V2 阻力",
            },
        },
        {
            "event_type": "v3_reply_model_observation",
            "event_id": "v3_obs",
            "created_at": "2026-08-13T10:01:00+08:00",
            "facts": {
                "primary_objective": "V3 目标",
                "customer_friction_observation": "V3 阻力",
            },
        },
    ]

    v2_result = build_v2_derived_observations(
        conversation=[],
        history_events=history_events,
        current_message={"content": "继续", "sent_at": "2026-08-13T10:02:00+08:00"},
        interface_version="v2",
    )
    v3_result = build_v2_derived_observations(
        conversation=[],
        history_events=history_events,
        current_message={"content": "继续", "sent_at": "2026-08-13T10:02:00+08:00"},
        interface_version="v3",
    )

    assert v2_result["prior_model_observations"][0]["primary_objective"] == "V2 目标"
    assert v3_result["prior_model_observations"][0]["primary_objective"] == "V3 目标"
    assert v3_result["prior_model_observations"][0]["authority"] == "v3_prior_model_observation_not_customer_fact"


def test_tool_planner_cannot_receive_sales_observations() -> None:
    payload = _tool_planner_shared_context(
        {
            "shared_context": {
                "derived_observations": {"prior_model_observations": [{"primary_objective": "close"}]},
                "content_indexes": {"available_sop": {}},
                "sales_guidance": {"anything": True},
                "authoritative_facts": {},
            }
        }
    )

    assert "derived_observations" not in payload
    assert "content_indexes" not in payload
    assert "sales_guidance" not in payload


def test_v3_active_evidence_node_does_not_call_legacy_gate_planner_or_sales_recall() -> None:
    source = inspect.getsource(create_parallel_evidence_node)

    assert "semantic_router_service.route" in source
    assert "_run_content_gate(" not in source
    assert "_run_tool_planner(" not in source
    assert "_run_sales_recall(" not in source


def test_content_gate_receives_delivery_observations_but_not_prior_model_judgment() -> None:
    payload = _content_gate_shared_context(
        {
            "shared_context": {
                "derived_observations": {
                    "recent_asset_deliveries": [{"asset_kind": "activity_image"}],
                    "prior_model_observations": [{"primary_objective": "close"}],
                }
            }
        }
    )

    observations = payload["derived_observations"]
    assert observations["recent_asset_deliveries"]
    assert "prior_model_observations" not in observations


def test_parallel_content_gate_uses_the_retrieval_prompt_not_the_legacy_scene_prompt() -> None:
    messages = build_sop_chat_gate_messages(
        {
            "reply_chain_mode": "parallel_candidate_only",
            "content_assets": [],
            "candidate_limit": 2,
        }
    )

    assert messages[0]["content"] == PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert messages[0]["content"] != SOP_CHAT_GATE_SYSTEM_PROMPT
    assert "selected_scene_id" not in messages[0]["content"]
    assert "不回复客户" in messages[0]["content"]


def test_v2_admission_has_no_visible_text_or_semantic_regex_logic() -> None:
    source = inspect.getsource(validate_model_led_reply_admission)

    assert "re.search" not in source
    assert "re.find" not in source
    assert "message_text" not in source
    assert "visible_text=False" in source


def test_structured_delivery_shape_requires_text_without_interpreting_wording() -> None:
    try:
        _validate_structured_delivery_conversation_shape(
            [{"type": "store_address", "content": {"store_id": "160"}}]
        )
    except ValueError as exc:
        assert str(exc) == "structured_delivery_requires_text_message"
    else:
        raise AssertionError("structured delivery without text must be repaired by Reply")

    _validate_structured_delivery_conversation_shape(
        [
            {"type": "text", "content": "由 Reply 自主生成的客户可见表达"},
            {"type": "store_address", "content": {"store_id": "160"}},
        ]
    )


def test_structured_only_reply_retries_the_complete_reply_task() -> None:
    original = [
        {"role": "system", "content": "完整 Reply 系统合同"},
        {"role": "user", "content": "完整聊天与权威证据"},
    ]
    repaired = _reply_retry_messages(
        original,
        ValueError("v2_reply_admission_violations::structured_delivery_requires_text_message"),
        previous_payload={
            "reply_messages": [
                {"type": "store_address", "content": {"store_id": "160"}}
            ],
            "sales_judgment": {
                "primary_objective": "回答门店",
                "customer_friction_observation": "",
                "posture": "answer",
            },
        },
        validation_context={"schema_version": "parallel_reply_repair_context_v2"},
    )
    rendered = "\n".join(str(item.get("content") or "") for item in repaired)

    assert repaired[:2] == original
    assert "重新执行一次完整 Reply 任务" in rendered
    assert "由你重新决定自然表达和本轮唯一相邻销售动作" in rendered
    assert "通用校验修复器" not in rendered


def test_model_led_admission_reachable_validators_do_not_interpret_visible_prose() -> None:
    validators = (
        _validate_parallel_claimed_deposit_evidence,
        _validate_parallel_payment_boundaries,
        _validate_parallel_media_facts,
        _validate_parallel_selected_content_delivery,
        _validate_store_resolution_v2_contract,
        _validate_store_resolution_delivery_mode,
        _validate_store_address_message_facts,
    )

    for validator in validators:
        source = inspect.getsource(validator)
        assert "re.search" not in source
        assert "re.find" not in source
        assert "_combined_text" not in source
    assert "check_visible_text=False" in inspect.getsource(validate_model_led_reply_admission)


def test_parallel_reply_repair_cannot_reach_legacy_scenario_hints() -> None:
    messages = [{"role": "system", "content": "原始 Reply 任务"}]
    repaired = _reply_retry_messages(
        messages,
        ValueError("parallel_reply_hard_violations::unsupported_parallel_media_fact"),
        previous_payload={"reply_messages": [{"type": "text", "content": "原回复"}]},
        validation_context={
            "schema_version": "parallel_reply_repair_context_v2",
            "current_message": {"message_ref": "current_message", "content": "继续"},
        },
    )
    rendered = "\n".join(str(item.get("content") or "") for item in repaired)

    assert "通用校验修复器" in rendered
    assert "不是第二个销售大脑" in rendered
    assert "客户心理" in rendered
    assert repaired[0]["content"].startswith("你是最终 Reply 的通用校验修复器")


def test_v2_admission_rejects_adopted_asset_when_structured_media_is_missing() -> None:
    state = {
        "evidence_join": {
            "schema_version": "parallel_reply_input_v2",
            "content_candidates": [
                {
                    "content_id": "s10_activity_intro",
                    "delivery_status": "available",
                    "messages": [
                        {"type": "text", "content": "活动介绍"},
                        {"type": "image", "content": {"url": "https://example.test/activity.png"}},
                    ],
                }
            ],
        },
        "reply_selected_content_ids": ["s10_activity_intro"],
        "reply_used_fact_refs": [],
    }

    try:
        validate_v2_reply_admission(
            [{"type": "text", "content": "活动介绍"}],
            state,
        )
    except ValueError as exc:
        assert "selected_content_delivery_missing:content_id=s10_activity_intro" in str(exc)
    else:
        raise AssertionError("adopted content asset must deliver its structured media")


def test_v2_admission_does_not_require_duplicate_content_asset_fact_refs() -> None:
    state = {
        "evidence_join": {
            "content_candidates": [
                {
                    "content_id": "follow_script:D01",
                    "delivery_status": "available",
                    "messages": [{"type": "text", "content": "参考表达"}],
                }
            ]
        },
        "reply_selected_content_ids": ["follow_script:D01"],
        "reply_used_fact_refs": [],
    }

    validate_v2_reply_admission([{"type": "text", "content": "结合上下文改写后的回复"}], state)


def test_v2_payment_material_requires_structured_activity_delivery() -> None:
    assert not _v2_activity_offer_delivered(
        sop_progress={},
        sent_messages={},
        history_events=[
            {
                "event_type": "conversation_message",
                "content": "268元，先付10元，到店抵扣",
            }
        ],
    )
    assert _v2_activity_offer_delivered(
        sop_progress={"completed_pack_ids": ["s10_activity_intro"]},
        sent_messages={},
        history_events=[],
    )


def test_prompts_preserve_node_power_boundaries_without_scene_matching() -> None:
    assert "V3 唯一的最终销售大脑" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "其他节点只提供事实或候选证据，不能替你作销售决定" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不输出客户话术、销售动作、工具调用或写操作" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    for forbidden in ("selected_scene_id", "conversion_stage", "customer_type"):
        assert forbidden not in PARALLEL_REPLY_SYSTEM_PROMPT
        assert forbidden not in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT


def test_reply_prompt_requires_real_progress_and_complete_deposit_facts() -> None:
    assert "不能只回复“好，到时联系我”就送客" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不得制造稀缺感" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "推进必须是本轮实际完成回答、证据交付或有效行动" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "相关的可信理由、证据或价值" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "最后完成一个清楚的低摩擦动作" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "真实素材能直接降低当前疑虑时直接交付" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "每轮只必须填写两个基础字段" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "content_decisions" not in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "used_fact_refs" not in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "只能从【本轮相关权威事实】完整说明金额、抵扣、尾款和可退条件" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不凭记忆补数字" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "推进客户的判断，不是虚构系统流程" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "留名额、记时间、看客流、安排接待" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不可覆盖的硬状态" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "approved_sales_expression" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "free_human_expression" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "客户数量、来源地、出行方式、专程到店行为和效果反馈同样属于事实" not in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "植入新顾虑或虚构后台动作" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "权威事实是证据库，不是输出清单" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "阻力未知时，问一个开放且低摩擦的问题" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '"customer_goal":""' not in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '"customer_friction_observation":""' in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "以下顶层字段仅在条件成立时增加" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "当前消息优先" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "客户没有继续追问不等于旧顾虑已经解决" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_prompt_keeps_old_friction_as_low_weight_observation_without_a_state_machine() -> None:
    assert "客户没有继续追问不等于旧顾虑已经解决" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "只能在仍影响当前决定时作为参考" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不能盖过当前消息或机械续跑旧序列" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不要输出或依赖固定情绪标签" in PARALLEL_REPLY_SYSTEM_PROMPT
    for forbidden in ("卡点栈", "意向分", "客户画像", "强信号", "弱信号"):
        assert forbidden not in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_prompt_preserves_sales_facts_without_semantic_code_patches() -> None:
    assert "ID 必须逐字来自输入" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不能直接升级为当前客户未来一定会怎样" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不得自行反向编造“必须按疗程、通常要多次”" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不把犹豫直接升级成登记、留名额或付款" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不替对方诊断失败原因" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "怎么参加、怎么报名、现在怎么付" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不重走已经完成的门店、活动或需求确认" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_v3_compact_rules_include_body_area_price_and_transport_facts() -> None:
    rules = parallel_reply_business_rules_for_model()
    offer = rules["AUTHORITATIVE FACTS"]["offer"]

    assert "一个268元只对应一个部位" in offer["body_area_price_rule"]
    assert "不能提前承诺" in offer["body_area_price_rule"]
    assert "交通费用需客户自理" in offer["transport_cost_rule"]

    rendered = _render_authoritative_facts(rules)
    assert "部位价格：" in rendered
    assert "交通费用：" in rendered


def test_v3_reply_only_renders_router_selected_fact_topics_plus_core_price() -> None:
    rules = parallel_reply_business_rules_for_model()

    core_only = _render_authoritative_facts(rules, topic_ids=[])
    effect_only = _render_authoritative_facts(rules, topic_ids=["effect_evidence"])
    health_only = _render_authoritative_facts(rules, topic_ids=["health_risk"])

    assert "核心活动：" in core_only
    assert "活动价=268元" in core_only
    assert "到店方式：" not in core_only
    assert "预约制事实：" not in core_only
    assert "效果：" not in core_only
    assert "当前健康风险：" not in core_only
    assert "效果：" in effect_only
    assert "当前健康风险：" not in effect_only
    assert "当前健康风险：" in health_only
    assert "效果：" not in health_only


def test_v3_store_facts_are_split_by_current_information_need() -> None:
    rules = parallel_reply_business_rules_for_model()

    base = _render_authoritative_facts(rules, topic_ids=["store_policy"])
    detail = _render_authoritative_facts(rules, topic_ids=["store_arrival_detail"])
    trust = _render_authoritative_facts(rules, topic_ids=["store_trust"])

    assert "公开地址：" in base
    assert "预约制事实：" in base
    assert "真假质疑" not in base
    assert "详细到店指引：" not in base
    assert "详细到店指引：" in detail
    assert "精确地址：" in detail
    assert "预约制事实：" not in detail
    assert "门店信任核验：" in trust
    assert "详细到店指引：" not in trust


def test_gate_retrieves_direct_evidence_across_short_factual_followups() -> None:
    assert "补充上一轮客户问题的事实" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "此前只是被提名或文字预告不算交付" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "不要把旧问题当成必须追回的任务" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "客户切换新话题时先解决新问题" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "当前消息优先" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_authoritative_facts_rank_above_historical_assistant_text() -> None:
    hard_state_line = next(
        line for line in PARALLEL_REPLY_SYSTEM_PROMPT.splitlines() if line.startswith("不可覆盖的硬状态")
    )
    authority_line = next(
        line for line in PARALLEL_REPLY_SYSTEM_PROMPT.splitlines() if line.startswith("普通销售语义冲突时")
    )
    assert "权威支付与订单" in hard_state_line
    assert authority_line.index("本轮相关权威事实") < authority_line.index("完整聊天")
    assert authority_line.index("完整聊天与真实发送记录") < authority_line.index("Router 辅助检索判断")
    assert "历史中的“小贝/销售”消息只用于理解对话和已发送内容" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不是当前事实、服务能力或履约依据" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_prompt_requires_antecedent_and_does_not_treat_politeness_as_stop() -> None:
    assert "只能承接紧邻上一轮唯一明确的问题或命题" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不能把“下午吧”猜成下午到店" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "礼貌词不决定销售姿态" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不因“谢谢”自动 pause" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "新案例、活动图或门店事实，本身就可以完成推进" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "仍在老家或人在外地，不等于正在确认到店时间" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "推进必须在本轮发生" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不要求客户自行诊断斑型、成因或专业分类" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不为了追求更精细而重新打开该维度" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_prompt_does_not_treat_new_message_as_cancelling_explicit_stop() -> None:
    assert "客户明确停止后，只有主动重新进入活动、项目、门店、预约或付款讨论才恢复" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_prompt_keeps_only_posture_as_compact_turn_observation() -> None:
    assert "sales_judgment.posture" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "它只是本轮观察，不是持久化销售阶段" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '"action":"none|ask|offer|payment|registration"' not in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_context_is_dense_readable_and_does_not_repeat_raw_tool_json() -> None:
    messages = build_parallel_reply_messages(
        {
            "evidence": {
                "shared_context": {
                    "current_time": {
                        "iso": "2026-08-22T14:10:00+08:00",
                        "timezone": "Asia/Shanghai",
                    },
                    "current_message": {
                        "message_ref": "current_message",
                        "content": "效果真的看得出来吗？",
                        "sent_at": "2026-08-22 14:10:00",
                    },
                    "conversation": [
                        {
                            "message_ref": "chat_1",
                            "role": "customer",
                            "content": "我主要想淡斑。",
                            "sent_at": "2026-08-22 14:08:00",
                        },
                        {
                            "message_ref": "chat_2",
                            "role": "assistant",
                            "content": "我先按您的情况给您讲清楚。",
                            "sent_at": "2026-08-22 14:09:00",
                        },
                    ],
                    "authoritative_facts": {},
                    "rules": parallel_reply_business_rules_for_model(),
                },
                "semantic_route": {
                    "checkpoint": {
                        "primary_code": "effect",
                        "evidence_refs": ["current_message"],
                    }
                },
                "knowledge_evidence": {},
                "content_candidates": [],
                "tool_facts": {
                    "raw_duplicate_marker": {
                        "status": "ok",
                        "payload": "SHOULD_NOT_BE_RENDERED",
                    }
                },
                "normalized_tool_facts": {
                    "structured_facts": {
                        "case_facts": [
                            {
                                "case_id": "case_1",
                                "summary": "真实效果案例",
                            }
                        ]
                    }
                },
            },
            "structured_delivery_options": {},
            "valid_message_refs": ["chat_1", "chat_2", "current_message"],
            "valid_customer_message_refs": ["chat_1", "current_message"],
            "valid_deposit_evidence_refs": ["chat_1", "current_message"],
            "allowed_selected_content_ids": [],
        },
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )

    context = messages[1]["content"]
    for section in (
        "【当前结构事实与不能越过的边界】",
        "【本轮真实执行能力】",
        "【完整聊天】",
        "【本轮相关权威事实：最终口径】",
        "【跟进序列与优秀话术参考】",
        "【可用真实素材】",
        "【当前工具权威事实：不得虚构或违背】",
        "【Router 辅助检索判断：可被 Reply 覆盖】",
    ):
        assert section in context
    assert "当前不可凭空执行" in context
    assert "历史小贝说过的价格、名额、登记、预约、接待和未来动作不能证明当前仍可执行" in context
    assert context.count("【本轮相关权威事实：最终口径】") == 1
    assert context.count("【Router 辅助检索判断：可被 Reply 覆盖】") == 1
    assert context.count("【当前工具权威事实：不得虚构或违背】") == 1
    assert context.index("【完整聊天】") < context.index("【当前结构事实与不能越过的边界】")
    assert context.index("【当前结构事实与不能越过的边界】") < context.index("【当前工具权威事实：不得虚构或违背】")
    assert context.index("【完整聊天】") < context.index("【当前工具权威事实：不得虚构或违背】")
    assert context.index("【当前工具权威事实：不得虚构或违背】") < context.index("【Router 辅助检索判断：可被 Reply 覆盖】")
    assert "客户：我主要想淡斑。" in context
    assert "小贝：我先按您的情况给您讲清楚。" in context
    assert "客户：效果真的看得出来吗？" in context
    assert "案例事实：case_id=case_1；summary=真实效果案例" in context
    assert "SHOULD_NOT_BE_RENDERED" not in context
    assert '"tool_facts"' not in context
    assert '"normalized_tool_facts"' not in context
    for removed_contract in ("used_fact_refs", "content_decisions", "action_reason"):
        assert removed_contract not in context


def test_reply_context_only_exposes_payment_payload_for_payment_topic() -> None:
    base_payload = {
        "evidence": {
            "shared_context": {
                "current_message": {"message_ref": "current_message", "content": "我有空去看看"},
                "conversation": [],
                "authoritative_facts": {},
                "rules": {},
            },
            "semantic_route": {"relevant_fact_topic_ids": []},
        },
        "structured_delivery_options": {
            "payment_collection": {
                "message_payloads": [
                    {"type": "payment_collection", "content": {"amount": 10}}
                ]
            }
        },
        "valid_message_refs": ["current_message"],
        "valid_customer_message_refs": ["current_message"],
    }

    context = build_parallel_reply_messages(
        base_payload,
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )[1]["content"]
    assert "payment_collection｜原样使用=" not in context

    base_payload["evidence"]["semantic_route"]["relevant_fact_topic_ids"] = ["payment"]
    payment_context = build_parallel_reply_messages(
        base_payload,
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )[1]["content"]
    assert "payment_collection｜原样使用=" in payment_context


def test_reply_context_uses_short_reversible_refs_and_hides_signed_media_urls() -> None:
    customer_ref = "7825117591008857908_1786006573212_external"
    assistant_ref = "xiaobei-auto-reply-seq0001-0005-bundle-long-identifier-msg-2"
    image_ref = "ai-outreach-platform-sop-1483-platform-sop-send-1483-0002-image-long"
    payload = {
        "evidence": {
            "shared_context": {
                "current_message": {
                    "message_ref": "current_message",
                    "content": "一次能好吗？",
                    "sent_at": "1787651481950",
                },
                "conversation": [
                    {
                        "message_ref": customer_ref,
                        "role": "customer",
                        "content": "我在武汉沌口",
                        "sent_at": "1786006573212",
                    },
                    {
                        "message_ref": assistant_ref,
                        "role": "assistant",
                        "content": "我给您发门店位置",
                        "sent_at": "1786006940213",
                    },
                    {
                        "message_ref": image_ref,
                        "role": "assistant",
                        "message_type": "image",
                        "content": "https://oss.example.com/case.png?OSSAccessKeyId=secret&Signature=secret",
                        "sent_at": "1786008470169",
                    },
                ],
                "authoritative_facts": {},
                "rules": parallel_reply_business_rules_for_model(),
            },
            "semantic_route": {
                "current_intent": {
                    "summary": "客户询问单次效果",
                    "evidence_refs": ["current_message"],
                }
            },
            "knowledge_evidence": {},
            "content_candidates": [],
            "normalized_tool_facts": {
                "structured_facts": {
                    "store_resolution_fact": {
                        "status": "need_location_confirmation",
                        "destination_resolution": {
                            "destination_query": "武汉沌口",
                            "evidence_refs": [customer_ref],
                        },
                        "location_evidence": {
                            "source_message_refs": [customer_ref],
                            "confidence": "high",
                        },
                    }
                }
            },
        },
        "structured_delivery_options": {},
        "valid_message_refs": [customer_ref, assistant_ref, image_ref, "current_message"],
        "valid_customer_message_refs": [customer_ref, "current_message"],
        "valid_deposit_evidence_refs": [assistant_ref, "store_delivery:request-long-id", "current_message"],
        "allowed_selected_content_ids": [],
    }

    context = build_parallel_reply_messages(
        payload,
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )[1]["content"]

    assert customer_ref not in context
    assert assistant_ref not in context
    assert image_ref not in context
    assert "OSSAccessKeyId" not in context
    assert "m01｜" in context
    assert "m02｜" in context
    assert "m03｜" in context
    assert "now｜" in context
    assert "[图片消息]" in context
    assert "预约金证据 ref" not in context
    assert "只是本轮证据编号，不属于聊天内容" in context
    assert "客户证据只能引用标注为“客户”的行" in context
    assert "source_message_refs=m01" in context

    model_output = {
        "reply_messages": [{"type": "text", "content": "m01 只是普通文本，不应被替换"}],
        "payment_assessment": {"evidence_refs": ["now"]},
        "deposit_evidence": {
            "offer_prior_turn_refs": ["m02"],
            "supporting_refs": ["store_delivery:request-long-id"],
        },
    }
    restore_reply_output_references(model_output, payload)

    assert model_output["reply_messages"][0]["content"] == "m01 只是普通文本，不应被替换"
    assert model_output["payment_assessment"]["evidence_refs"] == ["current_message"]
    assert model_output["deposit_evidence"]["offer_prior_turn_refs"] == [assistant_ref]
    assert model_output["deposit_evidence"]["supporting_refs"] == ["store_delivery:request-long-id"]


def test_reply_prompt_uses_direct_human_wechat_style_without_defensive_preambles() -> None:
    assert "先说客户要的结论" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "少用冒号、分号、引号" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "微信短聊不是文章" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不要再加防御性免责声明把它说弱" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_reply_schema_losslessly_lifts_misnested_optional_audit_fields() -> None:
    payload = {
        "reply_messages": [{"type": "text", "content": "一次做完就能看到明显改善"}],
        "sales_judgment": {
            "customer_friction_observation": "客户询问一次效果",
            "primary_objective": "答清效果并建立信心",
            "posture": "advance",
            "selected_content_ids": ["follow_script:KD-0116:p1"],
            "knowledge_use": {
                "sequence_id": "6",
                "step_id": "41",
                "script_id": "116",
            },
        },
    }

    _validate_parallel_raw_reply_schema(payload)

    assert payload["selected_content_ids"] == ["follow_script:KD-0116:p1"]
    assert payload["knowledge_use"] == {
        "sequence_id": "6",
        "step_id": "41",
        "script_id": "116",
    }
    assert "selected_content_ids" not in payload["sales_judgment"]
    assert "knowledge_use" not in payload["sales_judgment"]


def test_reply_context_renders_candidate_objective_and_boundaries_as_reference_evidence() -> None:
    messages = build_parallel_reply_messages(
        {
            "evidence": {
                "shared_context": {
                    "current_time": {},
                    "current_message": {"content": "我再想想"},
                    "conversation": [],
                    "authoritative_facts": {},
                    "rules": parallel_reply_business_rules_for_model(),
                },
                "semantic_route": {},
                "knowledge_evidence": {
                    "status": "ok",
                    "support_level": "script_exact",
                    "candidate_objective": "换一个未重复价值",
                    "candidate_boundaries": ["虚构稀缺", "直接付款"],
                    "sequence_candidates": [],
                    "candidates": [],
                },
                "content_candidates": [],
                "normalized_tool_facts": {},
            },
            "structured_delivery_options": {},
            "valid_message_refs": [],
            "valid_customer_message_refs": [],
            "valid_deposit_evidence_refs": [],
            "allowed_selected_content_ids": [],
        },
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )

    context = messages[1]["content"]
    assert "本候选目标：换一个未重复价值" in context
    assert "本候选不适用动作：虚构稀缺、直接付款" in context


def test_reply_context_distinguishes_platform_script_id_from_content_id() -> None:
    messages = build_parallel_reply_messages(
        {
            "evidence": {
                "shared_context": {
                    "current_message": {"message_ref": "current_message", "content": "一次能做好吗"},
                    "conversation": [],
                    "authoritative_facts": {},
                    "rules": {},
                },
                "knowledge_evidence": {
                    "candidates": [
                        {
                            "script_id": "116",
                            "source_id": "KD-0116",
                            "script_name": "效果疑虑-一次就能做掉吗",
                            "reference_text": "绝大多数客户一次能看到明显改善。",
                        }
                    ]
                },
            },
            "valid_message_refs": ["current_message"],
            "valid_customer_message_refs": ["current_message"],
        },
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )

    context = messages[1]["content"]
    assert "话术ID=116" in context
    assert "内容ID=follow_script:KD-0116" in context


def test_reply_context_only_renders_final_delivery_stores() -> None:
    messages = build_parallel_reply_messages(
        {
            "evidence": {
                "shared_context": {
                    "current_message": {"message_ref": "current_message", "content": "乌林村"},
                    "conversation": [],
                    "authoritative_facts": {},
                    "rules": parallel_reply_business_rules_for_model(),
                },
                "normalized_tool_facts": {
                    "usable_facts": ["customer_store_lookup: matched_stores=荆州万达二店, 荆州沙市店"],
                    "structured_facts": {
                        "store_resolution_fact": {
                            "status": "send_single",
                            "delivery_store_ids": ["243"],
                            "ranking_method": "haversine",
                        },
                        "store_facts": [
                            {"store_id": "241", "store_name": "荆州万达二店"},
                            {"store_id": "243", "store_name": "武汉光谷店"},
                        ],
                    },
                },
            },
            "structured_delivery_options": {
                "store_address": {
                    "available_store_ids": ["243"],
                    "message_payloads": [{"type": "store_address", "content": {"store_id": "243"}}],
                }
            },
            "valid_message_refs": ["current_message"],
            "valid_customer_message_refs": ["current_message"],
            "valid_deposit_evidence_refs": ["current_message"],
            "allowed_selected_content_ids": [],
        },
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )

    context = messages[1]["content"]
    assert "武汉光谷店" in context
    assert "荆州万达二店" not in context


def test_v2_repeat_similarity_is_measurement_only() -> None:
    metrics = collect_reply_observation_metrics(
        [{"type": "text", "content": "活动价是268元，我给您说清楚。"}],
        {
            "conversation_history": ["小贝: 活动价是268元，我给您说清楚。"],
            "evidence_join": {"schema_version": "parallel_reply_input_v2"},
        },
    )
    assert metrics["previous_assistant_text_similarity"] == 1.0
    assert metrics["measurement_only"] is True
