from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.graph.nodes.reply_nodes import (
    _normalized_content_decisions,
    _normalized_sales_judgment,
    _parallel_generic_reply_repair_messages,
    _parallel_reply_repair_context,
    _reply_validation_state,
    create_synthesize_reply_node,
    _run_model_led_reply_pipeline,
)
from app.graph.nodes.reply_validation import (
    _validate_parallel_payment_boundaries,
    _validate_parallel_reply_consistency,
    completed_parallel_selected_content_ids,
    debug_message_contents,
    validate_reply_consistency,
    validated_model_messages,
)
from app.prompts.reply_synthesizer import PARALLEL_REPLY_SYSTEM_PROMPT
from app.prompts.v3_sop_chat_gate import PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
from app.graph.nodes.parallel_reply_chain import TOOL_PLANNER_SYSTEM_PROMPT
from app.policies.business_rules import parallel_reply_business_rules_for_model


def _state() -> dict:
    return {
        "content": "怎么参加",
        "normalized_content": "怎么参加",
        "shared_context": {
            "current_message": {
                "content": "怎么参加",
                "message_type": "text",
                "sent_at": "2026-08-11T10:00:00+08:00",
            },
            "conversation": [
                {
                    "message_ref": "history:1",
                    "role": "assistant",
                    "content": "本次活动价268元。",
                    "sent_at": "2026-08-11T09:58:00+08:00",
                }
            ],
            "authoritative_facts": {},
            "rules": {"MUST FOLLOW": [], "AUTHORITATIVE FACTS": []},
        },
        "evidence_join": {
            "schema_version": "reply_chain_evidence_join_v1",
            "shared_context": {
                "current_message": {
                    "content": "怎么参加",
                    "message_type": "text",
                    "sent_at": "2026-08-11T10:00:00+08:00",
                },
                "conversation": [
                    {
                        "message_ref": "history:1",
                        "role": "assistant",
                        "content": "本次活动价268元。",
                        "sent_at": "2026-08-11T09:58:00+08:00",
                    }
                ],
                "authoritative_facts": {},
                "rules": {"MUST FOLLOW": [], "AUTHORITATIVE FACTS": []},
            },
            "content_candidates": [],
            "tool_facts": {},
            "normalized_tool_facts": {},
        },
        "fact_envelope": {"structured_facts": {}},
        "request_context": {},
        "conversation_history": [],
    }


def test_v3_never_falls_back_to_legacy_semantic_validation_without_evidence_join() -> None:
    state = {
        "request_context": {
            "interface_version": "v3",
            "reply_chain_mode": "model_led_sales_brain_v3",
            "v3_sidecar": True,
        }
    }
    messages = [{"type": "text", "content": "我先把您问的说清楚。"}]

    with pytest.raises(ValueError, match="v3_evidence_join_required"):
        validated_model_messages({"reply_messages": messages}, state)
    with pytest.raises(ValueError, match="v3_evidence_join_required"):
        validate_reply_consistency(messages, state)


def test_legacy_reply_validation_contract_is_unchanged_without_v3_marker() -> None:
    messages = [{"type": "text", "content": "我先把您问的说清楚。"}]

    assert validated_model_messages({"reply_messages": messages}, {}) == [
        {"type": "text", "order": 1, "content": "我先把您问的说清楚。"}
    ]


def test_parallel_structure_validator_does_not_interpret_visible_business_language() -> None:
    state = _state()
    messages = validated_model_messages(
        {"reply_messages": [{"type": "text", "content": "已经给您预约成功了。"}]},
        state,
    )

    _validate_parallel_reply_consistency(messages, state)


def test_parallel_reply_drops_fabricated_audit_reference_without_rejecting_reply() -> None:
    state = _reply_validation_state(
        _state(),
        {
            "used_fact_refs": ["current_message", "fabricated_fact:paid"],
            "selected_content_ids": [],
            "action": "none",
        },
    )

    assert state["reply_used_fact_refs"] == ["current_message"]


def test_non_payment_action_does_not_fail_on_unused_deposit_audit_metadata() -> None:
    validation_state = _reply_validation_state(
        _state(),
        {
            "used_fact_refs": ["current_message"],
            "selected_content_ids": [],
            "action": "ask",
            "payment_assessment": {
                "status": "unverified_paid_claim",
                "evidence_refs": ["current_message"],
            },
            "deposit_evidence": {
                "offer_prior_turn_refs": [],
                "supporting_key": "unused_non_payment_metadata",
                "supporting_refs": [],
                "current_intent_refs": [],
            },
        },
    )

    assert validation_state["reply_action"] == "ask"
    assert validation_state["reply_deposit_evidence"]["supporting_key"] == "unused_non_payment_metadata"


def test_failed_raw_reply_is_not_exposed_as_validated_metadata() -> None:
    from app.graph.nodes.reply_nodes import _reply_metadata_from_model_call

    assert _reply_metadata_from_model_call(
        {
            "raw_json_output": {
                "action": "registration",
                "deposit_evidence": {"supporting_key": "invalid_failed_draft"},
            },
            "retry": {
                "raw_json_output": {
                    "action": "payment",
                    "deposit_evidence": {"supporting_key": "activity"},
                },
                "error": "invalid_reply_deposit_supporting_key",
            },
        }
    ) == {}


def test_parallel_active_validation_has_no_semantic_text_checkers() -> None:
    source = inspect.getsource(_validate_parallel_reply_consistency)
    forbidden = {
        "_validate_deposit_refund_policy",
        "_validate_unverified_refund_execution_claims",
        "_validate_structured_delivery_promises",
        "_validate_offer_total_and_tail_amount",
        "_validate_parallel_unpaid_registration_request",
        "_validate_payment_confirmation_claim",
        "_validate_effect_absolute_safety_claims",
        "_validate_appointment_lookup_promise",
        "_validate_parallel_registration_confirmation_facts",
        "_validate_parallel_appointment_confirmation_facts",
        "_validate_fact_boundaries",
        "_validate_store_delivery_text_matches_cards",
    }

    assert all(name not in source for name in forbidden)
    assert "re.search" not in source
    assert "re.finditer" not in source


def test_parallel_reply_prompt_treats_structured_delivery_as_evidence_not_full_conversation() -> None:
    assert "每轮只做一个主要目标，不等于只做一件事" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "回答问题、发卡或发图只是交付过程" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "直接交付，不先问客户要不要看" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "结构消息只交付事实、素材或交易入口，不天然等于完整微信对话" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "后面的销售推进不能覆盖前面的事实和沟通边界" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "最后一步只能是" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "问一个答案会改变后续证据、事实查询或成交动作的具体问题" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "没有值得问的问题时，不为保持对话而硬问" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "问题、素材和动作只选本轮最相关的一项，不拼客服菜单" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "门店查询只证明位置需求，不等于报名、预约或登记" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "它不能查客流、留位、安排接待或替客户预约" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "客户可见消息必须真的完成这个目标" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "无意义许可问题" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_parallel_reply_contract_exposes_exact_deposit_supporting_key_enum() -> None:
    assert "supporting_key=address、effect 或 objection" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "未发卡时省略" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_tool_planner_requeries_when_customer_explicitly_requests_store_card_resend() -> None:
    assert "明确要求重发地址、位置、导航或门店卡" in TOOL_PLANNER_SYSTEM_PROMPT
    assert "完整历史交给 resolve_customer_store 内的地点解析模型处理" in TOOL_PLANNER_SYSTEM_PROMPT


def test_content_gate_does_not_reopen_location_capture_for_known_store_resend() -> None:
    assert "已交付资产默认不重发" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT
    assert "客户明确要求重发" in PARALLEL_CONTENT_GATE_SYSTEM_PROMPT


def test_parallel_generic_repair_distinguishes_strategy_refs_from_delivery_refs() -> None:
    repaired = _parallel_generic_reply_repair_messages(
        [{"role": "user", "content": "完整证据"}],
        ValueError("invalid_structured_delivery_fact_ref: strategy_value_and_price"),
        previous_payload={"action": "none"},
        validation_context={"current_message": {"content": "我看其他是199啊"}},
    )

    repair_prompt = repaired[-1]["content"]
    assert '"failure_class":"structure_and_provenance"' in repair_prompt
    assert "所有 ID、URL、金额、结构消息和 evidence_refs 只能取自 valid_reference_contract" in repair_prompt


def test_parallel_generic_repair_does_not_replace_external_fact_guess_with_another_guess() -> None:
    repaired = _parallel_generic_reply_repair_messages(
        [{"role": "user", "content": "完整证据"}],
        Exception("事实错误"),
        previous_payload={"action": "none"},
        validation_context={"current_message": {"content": "我看其他是199啊"}},
    )

    repair_prompt = repaired[-1]["content"]
    assert '"failure_class":"deterministic_fact_conflict"' in repair_prompt
    assert "不得重新判断客户心理、成交阶段或销售节奏" in repair_prompt
    assert "不得按错误码生成新销售话术" in repair_prompt


def test_parallel_generic_repair_exposes_exact_payment_card_payload() -> None:
    repaired = _parallel_generic_reply_repair_messages(
        [{"role": "user", "content": "完整证据"}],
        ValueError("invalid_parallel_reply_message_content:1:payment_collection"),
        previous_payload={
            "action": "payment",
            "payment_assessment": {
                "status": "payment_request",
                "payment_channel": "payment_card",
                "evidence_refs": ["current_message"],
            },
        },
        validation_context={
            "current_message": {"content": "怎么参加"},
            "structured_delivery_options": {
                "payment_collection": {
                    "fact_ref": "authoritative_fact:payment_collection_option",
                    "message_payloads": [
                        {
                            "type": "payment_collection",
                            "content": {"amount": 10, "remark": ""},
                        }
                    ],
                }
            },
        },
    )

    repair_prompt = repaired[-1]["content"]
    assert "exact_payment_delivery_contract" in repair_prompt
    assert '"amount":10' in repair_prompt
    assert "所有 ID、URL、金额、结构消息" in repair_prompt


def test_model_led_reply_timeout_retries_full_original_task_instead_of_structural_repair() -> None:
    original_messages = [
        {"role": "system", "content": "完整销售大脑合同；只输出 json。"},
        {"role": "user", "content": '{"current_message":"怎么参加"}'},
    ]
    valid_reply = {
        "reply_messages": [{"type": "text", "content": "参加流程我给您接着说明清楚。"}],
        "used_fact_refs": ["current_message"],
        "selected_content_ids": [],
        "action": "offer",
        "action_reason": "回答客户当前问题",
        "sales_judgment": {
            "customer_goal": "参加活动",
            "established_keys": [],
            "primary_objective": "说明参加流程",
            "posture": "answer",
            "reason": "先完整回答当前问题",
        },
        "payment_assessment": {"status": "none", "payment_channel": "none", "evidence_refs": []},
        "deposit_evidence": {
            "offer_prior_turn_refs": [],
            "supporting_key": "",
            "supporting_refs": [],
            "current_intent_refs": [],
        },
        "safety_assessment": {"status": "none", "evidence_refs": []},
        "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        "commit_actions": [],
    }

    class _Client:
        available = True
        last_usage = None

        def __init__(self) -> None:
            self.settings = SimpleNamespace()
            self.calls: list[list[dict]] = []
            self.call_kwargs: list[dict] = []

        async def chat_json(self, messages, **kwargs):
            self.calls.append(messages)
            self.call_kwargs.append(kwargs)
            if len(self.calls) == 1:
                raise TimeoutError("provider timeout")
            return valid_reply

    client = _Client()
    state = {
        **_state(),
        "model_deadline": {"deadline_monotonic": None},
    }

    messages, model_call, source = asyncio.run(
        _run_model_led_reply_pipeline(
            state=state,
            model_client=client,
            model_messages=original_messages,
            validated_model_messages=lambda payload, _state: payload["reply_messages"],
            debug_message_contents=lambda items: [str(item.get("content") or "") for item in items],
            warnings=[],
        )
    )

    assert len(client.calls) == 2
    assert client.calls[1][:2] == original_messages
    assert "重新执行原始 Reply 任务" in client.calls[1][-1]["content"]
    assert "局部结构修复" in client.calls[1][-1]["content"]
    assert "最小修复" not in client.calls[1][-1]["content"]
    assert model_call["retry"]["mode"] == "full_task_retry"
    assert model_call["retry"]["tier"] == "fast"
    assert client.call_kwargs[0]["tier"] == "reply"
    assert client.call_kwargs[1]["tier"] == "fast"
    assert source == "single_full_task_retry_model"
    assert messages[0]["content"] == valid_reply["reply_messages"][0]["content"]

    class _SecondaryClient(_Client):
        secondary_available = True

        def __init__(self) -> None:
            super().__init__()
            self.secondary_calls = 0

        async def chat_json(self, messages, **kwargs):
            self.calls.append(messages)
            self.call_kwargs.append(kwargs)
            raise TimeoutError("primary provider timeout")

        async def chat_json_secondary(self, messages, **kwargs):
            self.secondary_calls += 1
            self.calls.append(messages)
            self.call_kwargs.append(kwargs)
            return valid_reply

    secondary_client = _SecondaryClient()
    _, secondary_model_call, secondary_source = asyncio.run(
        _run_model_led_reply_pipeline(
            state=state,
            model_client=secondary_client,
            model_messages=original_messages,
            validated_model_messages=lambda payload, _state: payload["reply_messages"],
            debug_message_contents=lambda items: [
                str(item.get("content") or "") for item in items
            ],
            warnings=[],
        )
    )

    assert secondary_client.secondary_calls == 1
    assert secondary_model_call["retry"]["tier"] == "secondary"
    assert secondary_source == "single_full_task_retry_model"


def test_parallel_reply_prompt_is_structured_sales_brain_not_scene_matcher() -> None:
    for section in (
        "# 一、决策优先级",
        "# 二、销售判断",
        "# 三、知识与素材",
        "# 四、真人微信表达",
        "# 五、事实与交易边界",
        "# 六、输出合同",
    ):
        assert section in PARALLEL_REPLY_SYSTEM_PROMPT
    for legacy in (
        "selected_scene_id",
        "selected_question.id",
        "main_blocker",
        "conversion_stage",
        "sales_assessment",
    ):
        assert legacy not in PARALLEL_REPLY_SYSTEM_PROMPT
    required_contracts = (
        "sales_judgment",
        "selected_content_ids",
        "payment_assessment",
        "deposit_evidence",
        "safety_assessment",
        "party_size_assessment",
        "commit_actions",
    )
    for contract in required_contracts:
        assert contract in PARALLEL_REPLY_SYSTEM_PROMPT
    for removed_contract in ("used_fact_refs", "content_decisions", "action_reason", "customer_goal"):
        assert removed_contract not in PARALLEL_REPLY_SYSTEM_PROMPT
    semantic_boundaries = (
        "不做被动客服",
        "V3 唯一的最终销售大脑",
        "你可以采用、组合、跳步或忽略",
    )
    for boundary in semantic_boundaries:
        assert boundary in PARALLEL_REPLY_SYSTEM_PROMPT

    factual_boundaries = (
        "订单不是前置",
        "同轮最多一张",
        "不可覆盖的硬状态",
        "hard_fact",
    )
    for boundary in factual_boundaries:
        assert boundary in PARALLEL_REPLY_SYSTEM_PROMPT


def test_sales_judgment_keeps_only_compact_model_owned_fields() -> None:
    judgment = _normalized_sales_judgment(
        {
            "customer_goal": "先考虑是否值得参加",
            "established_keys": ["effect", "activity"],
            "active_friction": "担心预约金是不是额外收费",
            "decision_opportunity": "澄清预约金用途后确认是否保留名额",
            "primary_objective": "降低付款顾虑",
            "smallest_next_commitment": "确认是否接受10元抵扣机制",
            "posture": "advance",
            "reason": "客户仍在询问而非明确退出",
        }
    )

    assert judgment == {
        "customer_goal": "先考虑是否值得参加",
        "primary_objective": "降低付款顾虑",
        "customer_friction_observation": "",
        "posture": "advance",
        "reason": "客户仍在询问而非明确退出",
    }


def test_content_decisions_are_normalized_as_audit_metadata_only() -> None:
    decisions = _normalized_content_decisions(
        [
            {
                "content_id": "s10_need_and_case",
                "decision": "ADOPT",
                "reason": "directly_useful",
            },
            {
                "content_id": "s10_need_and_case",
                "decision": "skip",
                "reason": "higher_priority",
            },
            {
                "content_id": "s10_activity_intro",
                "decision": "skip",
                "reason": "higher_priority",
            },
        ]
    )

    assert decisions == [
        {
            "content_id": "s10_need_and_case",
            "decision": "adopt",
            "reason": "directly_useful",
        },
        {
            "content_id": "s10_activity_intro",
            "decision": "skip",
            "reason": "",
        },
    ]


def test_completed_content_can_be_referenced_without_replaying_old_media() -> None:
    state = {
        "reply_selected_content_ids": ["s10_activity_intro"],
        "reply_used_fact_refs": ["current_message", "content_asset:s10_activity_intro"],
        "evidence_join": {
            "content_candidates": [
                {
                    "content_id": "s10_activity_intro",
                    "delivery_status": "completed",
                    "messages": [
                        {"type": "text", "content": "活动价268元。"},
                        {"type": "image", "content": "https://example.invalid/activity.jpg"},
                    ],
                }
            ]
        },
    }

    assert completed_parallel_selected_content_ids(
        [{"type": "text", "content": "活动价格之前已经给您介绍过了。"}],
        state,
        ["s10_activity_intro"],
    ) == []


def test_partial_available_content_is_not_committed_as_complete() -> None:
    state = {
        "reply_selected_content_ids": ["s10_activity_intro"],
        "evidence_join": {
            "content_candidates": [
                {
                    "content_id": "s10_activity_intro",
                    "delivery_status": "available",
                    "messages": [
                        {"type": "text", "content": "活动价268元。"},
                        {"type": "image", "content": "https://example.invalid/activity.jpg"},
                    ],
                }
            ]
        },
    }

    assert completed_parallel_selected_content_ids(
        [{"type": "text", "content": "活动价268元。"}],
        state,
        ["s10_activity_intro"],
    ) == []


def test_selected_text_only_asset_needs_no_redundant_structured_delivery_proof() -> None:
    state = {
        "reply_used_fact_refs": [],
        "evidence_join": {
            "content_candidates": [
                {
                    "content_id": "text_only_asset",
                    "delivery_status": "available",
                    "messages": [{"type": "text", "content": "活动事实"}],
                }
            ]
        },
    }
    messages = [{"type": "text", "content": "我把活动内容给您说清楚。"}]

    assert completed_parallel_selected_content_ids(
        messages, state, ["text_only_asset"]
    ) == ["text_only_asset"]

    state["reply_used_fact_refs"] = ["content_asset:text_only_asset"]
    assert completed_parallel_selected_content_ids(
        messages, state, ["text_only_asset"]
    ) == ["text_only_asset"]


def test_answer_action_is_schema_compatibility_normalized_to_none() -> None:
    payload = {
        "reply_messages": [{"type": "text", "content": "可以先了解一下。"}],
        "used_fact_refs": ["current_message"],
        "selected_content_ids": [],
        "action": "answer",
        "payment_assessment": {"status": "none", "evidence_refs": []},
        "deposit_evidence": {},
        "safety_assessment": {"status": "none", "evidence_refs": []},
        "party_size_assessment": {"status": "unknown", "party_size": None, "evidence_refs": []},
        "sales_judgment": {"posture": "answer"},
        "commit_actions": [],
    }

    validation_state = _reply_validation_state(_state(), payload)

    assert payload["action"] == "none"
    assert validation_state["reply_action"] == "none"


def test_generic_repair_context_exposes_customer_source_text_for_reference_selection() -> None:
    state = _state()
    state["evidence_join"]["shared_context"]["conversation"] = [
        {
            "message_ref": "history:customer-effect",
            "role": "customer",
            "content": "做一次能看出变化吗",
        },
        {
            "message_ref": "history:assistant-effect",
            "role": "assistant",
            "content": "我给您看过同类效果。",
        },
    ]
    state["evidence_join"]["valid_customer_message_refs"] = [
        "current_message",
        "history:customer-effect",
    ]

    context = _parallel_reply_repair_context(state)

    assert {
        "ref": "history:customer-effect",
        "role": "customer",
        "content": "做一次能看出变化吗",
    } in context["prior_message_options"]
    assert "store_fact_status" in context
    assert "registration_fact_status" in context


def test_generic_repair_contract_exposes_authoritative_paid_without_deciding_customer_semantics() -> None:
    repair_messages = _parallel_generic_reply_repair_messages(
        [{"role": "system", "content": "system"}],
        ValueError("payment_assessment_authoritative_paid_requires_fact"),
        previous_payload={
            "action": "registration",
            "payment_assessment": {
                "status": "authoritative_paid",
                "evidence_refs": ["current_message"],
            },
        },
        validation_context={
            "schema_version": "parallel_reply_repair_context_v2",
            "current_message": {"content": "我付好了"},
            "prior_message_options": [],
            "authoritative_fact_reference_options": [
                {"ref": "payment_fact:authoritative_paid", "kind": "paid_deposit"}
            ],
            "authoritative_paid": False,
        },
    )
    repair_contract = repair_messages[-1]["content"]

    assert '"authoritative_paid":false' in repair_contract
    assert '"ref":"payment_fact:authoritative_paid"' in repair_contract
    assert '"status":"authoritative_paid"' in repair_contract
    assert '"sales_judgment_posture"' not in repair_contract
    assert "不是重新制定销售策略" in repair_contract
    assert '"failure_class":"deterministic_fact_conflict"' in repair_contract
    assert "不得重新判断客户心理、成交阶段或销售节奏" in repair_contract
    assert "不得按错误码生成新销售话术" in repair_contract
    assert "所有 ID、URL、金额、结构消息和 evidence_refs" in repair_contract


def test_unavailable_payment_channel_metadata_does_not_block_reply_without_payment_card() -> None:
    state = _state()
    state["reply_action"] = "none"
    state["reply_payment_assessment"] = {
        "status": "authoritative_paid",
        "evidence_refs": ["payment_fact:authoritative_paid"],
    }
    state["reply_payment_channel"] = "payment_card"
    state["reply_payment_channel_explicit"] = True
    state["reply_payment_channel_availability"] = {
        "payment_card": {"available": False},
    }

    _validate_parallel_payment_boundaries(
        [{"type": "text", "content": "收到，接下来登记姓名和电话就可以。"}],
        state,
    )


def test_parallel_rules_expose_customer_charge_fact_to_reply_and_auditor() -> None:
    from app.policies.business_rules import parallel_reply_business_rules_for_model

    rules = parallel_reply_business_rules_for_model()

    assert rules["AUTHORITATIVE FACTS"]["customer_charge_policy"]["rule_level"] == "business_fact"
    assert "不会强制客户接受额外项目" in (
        rules["AUTHORITATIVE FACTS"]["customer_charge_policy"]["customer_visible_fact"]
    )


def test_reply_requires_direct_text_answer_before_structured_supplement() -> None:
    assert "先解决客户此刻真正关心的问题" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "结构素材只能使用输入中的真实 ID、URL 或 payload" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_model_led_prompt_distinguishes_changeable_fact_gaps_from_fixed_constraints() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "缺失信息会改变事实、证据或动作时才问一个问题" in prompt
    assert "客户已经说过的不再问" in prompt
    assert "可逆犹豫不等于拒绝" in prompt


def test_model_led_reply_preserves_complete_selected_asset_contract() -> None:
    prompt = PARALLEL_REPLY_SYSTEM_PROMPT

    assert "候选中的图片和视频会按该 ID 原样交付" in prompt
    assert "门店卡、付款卡等外部副作用不会因选择内容 ID 自动执行" in prompt
    assert "结构素材只能使用输入中的真实 ID、URL 或 payload" in prompt


def test_current_gate_candidate_authorizes_media_without_audit_metadata() -> None:
    state = {
        "reply_selected_content_ids": [],
        "evidence_join": {
            "content_candidates": [
                {
                    "content_id": "s10_activity_intro",
                    "delivery_status": "available",
                    "messages": [
                        {"type": "text", "content": "活动价268元。"},
                        {"type": "image", "content": "https://example.invalid/activity.jpg"},
                    ],
                }
            ],
            "normalized_tool_facts": {"structured_facts": {"case_facts": []}},
        },
    }

    _validate_parallel_reply_consistency(
        [{"type": "image", "content": "https://example.invalid/activity.jpg"}],
        state,
    )
    assert completed_parallel_selected_content_ids(
        [{"type": "image", "content": "https://example.invalid/activity.jpg"}],
        state,
        [],
    ) == ["s10_activity_intro"]

    state["reply_selected_content_ids"] = ["s10_activity_intro"]
    state["reply_used_fact_refs"] = ["content_asset:s10_activity_intro"]
    _validate_parallel_reply_consistency(
        [{"type": "image", "content": "https://example.invalid/activity.jpg"}],
        state,
    )


def test_shared_candidate_media_does_not_guess_multiple_completed_assets() -> None:
    state = {
        "evidence_join": {
            "content_candidates": [
                {
                    "content_id": "asset_a",
                    "delivery_status": "available",
                    "messages": [
                        {"type": "image", "content": "https://example.invalid/shared.jpg"}
                    ],
                },
                {
                    "content_id": "asset_b",
                    "delivery_status": "available",
                    "messages": [
                        {"type": "image", "content": "https://example.invalid/shared.jpg"}
                    ],
                },
            ]
        }
    }

    assert completed_parallel_selected_content_ids(
        [{"type": "image", "content": "https://example.invalid/shared.jpg"}],
        state,
        [],
    ) == []


def test_recently_delivered_media_does_not_authorize_current_resend() -> None:
    state = {
        "reply_selected_content_ids": [],
        "evidence_join": {
            "shared_context": {
                "conversation": [
                    {
                        "message_ref": "conv_case_image",
                        "role": "assistant",
                        "content": "[image]https://example.invalid/case.jpg",
                    }
                ]
            },
            "content_candidates": [],
            "normalized_tool_facts": {"structured_facts": {"case_facts": []}},
        },
    }

    with pytest.raises(ValueError, match="unsupported_parallel_media_fact"):
        _validate_parallel_reply_consistency(
            [{"type": "image", "content": "https://example.invalid/case.jpg"}],
            state,
        )

    state["evidence_join"]["content_candidates"] = [
        {
            "content_id": "s10_need_and_case",
            "delivery_status": "completed",
            "messages": [
                {"type": "image", "content": "https://example.invalid/case.jpg"}
            ],
        }
    ]
    state["reply_selected_content_ids"] = ["s10_need_and_case"]
    state["reply_used_fact_refs"] = ["current_message", "content_asset:s10_need_and_case"]
    _validate_parallel_reply_consistency(
        [{"type": "image", "content": "https://example.invalid/case.jpg"}],
        state,
    )
