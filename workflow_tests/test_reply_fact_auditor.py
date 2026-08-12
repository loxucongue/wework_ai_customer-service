from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.graph.nodes.reply_nodes import (
    _normalized_sales_judgment,
    _parallel_generic_reply_repair_messages,
    _parallel_reply_fact_audit_input,
    _parallel_reply_repair_context,
    _reply_validation_state,
    _run_parallel_reply_fact_audit,
    _validated_parallel_reply_fact_audit,
    create_synthesize_reply_node,
    _run_model_led_reply_pipeline,
)
from app.graph.nodes.reply_validation import (
    _validate_parallel_selected_content_delivery,
    _validate_parallel_reply_consistency,
    completed_parallel_selected_content_ids,
    debug_message_contents,
    validated_model_messages,
)
from app.prompts.reply_fact_auditor import REPLY_FACT_AUDITOR_SYSTEM_PROMPT
from app.prompts.reply_synthesizer import PARALLEL_REPLY_SYSTEM_PROMPT


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


def test_fact_audit_accepts_valid_pass() -> None:
    result = _validated_parallel_reply_fact_audit(
        {"status": "pass", "violations": []},
        messages=[{"type": "text", "content": "本次活动价268元。"}],
        valid_refs={"history:1"},
    )

    assert result == {"status": "pass", "violations": []}


def test_fact_audit_requires_exact_visible_quote_and_real_reference() -> None:
    messages = [{"type": "text", "content": "已经给您预约成功了。"}]

    with pytest.raises(ValueError, match="reply_fact_audit_quote_not_in_message"):
        _validated_parallel_reply_fact_audit(
            {
                "status": "fail",
                "violations": [
                    {
                        "code": "wrong_temporality",
                        "message_index": 0,
                        "quote": "已经登记成功",
                        "evidence_refs": [],
                        "reason": "没有完成事实",
                    }
                ],
            },
            messages=messages,
            valid_refs={"history:1"},
        )

    with pytest.raises(ValueError, match="reply_fact_audit_has_invalid_evidence_ref"):
        _validated_parallel_reply_fact_audit(
            {
                "status": "fail",
                "violations": [
                    {
                        "code": "wrong_temporality",
                        "message_index": 0,
                        "quote": "已经给您预约成功了",
                        "evidence_refs": ["invented:1"],
                        "reason": "没有完成事实",
                    }
                ],
            },
            messages=messages,
            valid_refs={"history:1"},
        )


def test_parallel_structure_validator_does_not_interpret_visible_business_language() -> None:
    state = _state()
    messages = validated_model_messages(
        {"reply_messages": [{"type": "text", "content": "已经给您预约成功了。"}]},
        state,
    )

    _validate_parallel_reply_consistency(messages, state)


def test_parallel_reply_rejects_fabricated_fact_reference() -> None:
    with pytest.raises(ValueError, match="invalid_parallel_used_fact_refs"):
        _reply_validation_state(
            _state(),
            {
                "used_fact_refs": ["current_message", "fabricated_fact:paid"],
                "selected_content_ids": [],
                "action": "none",
            },
        )


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


def test_fact_auditor_contract_cannot_make_sales_decisions() -> None:
    assert "不是客服、销售、策略评审或回复改写器" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "不得评价销售力度、语气、主线、推进时机、资产选择或发卡资格" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert '"reply_messages"' not in REPLY_FACT_AUDITOR_SYSTEM_PROMPT


def test_fact_auditor_contract_distinguishes_payment_and_registration_states() -> None:
    for section in (
        "# 1. 唯一职责",
        "# 2. 证据层级",
        "# 3. 可以审计的内容",
        "# 4. 语言行为与时态",
        "# 5. 一致性边界",
        "# 6. 输出合同",
    ):
        assert section in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "未来动作/能力说明" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "条件规则" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "提问/邀请/资料请求" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "先登记再到店" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "不表示已经登记或已经到店" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "咨询、口头意向、资料收集、活动登记、到店意向、后台订单、预约成功和正式排客是不同状态" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "承诺后续查询哪家更近不属于当前比较结论" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "单纯说“可以给您看、下一步给您介绍、需要的话再发”不是已交付声明" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "不得截取“最近、已经、确认、登记、报名、发送”等孤立词" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "就必须通过" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "审计采用闭世界原则" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "找不到才报告 `unsupported_claim` 或 `wrong_temporality`" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "找不到就报告 `unfulfilled_delivery`" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "输出必须自洽" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "就不得保留该 violation" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "所有证据来源必须合并判断，而不是互斥选择" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "不得把声明引用当成该句话唯一允许使用的证据" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "一般事实/流程说明" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "不表示本轮已经为当前客户完成拍摄或安排" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "回复没有改写成当前客户必然达到同样效果" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "不得要求当前完成事件作为额外证据" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "审计对象是回复实际说出的完整命题" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT
    assert "权威事实已经直接支持命题时必须通过" in REPLY_FACT_AUDITOR_SYSTEM_PROMPT


def test_model_led_reply_repair_reserves_fact_audit_budget() -> None:
    source = inspect.getsource(_run_model_led_reply_pipeline)

    assert '"model_fact_audit_timeout_seconds"' in source
    assert "round_deadline - fact_audit_budget" in source


def test_parallel_reply_prompt_is_structured_sales_brain_not_scene_matcher() -> None:
    for section in (
        "# 1. 使命",
        "# 2. 权威层级",
        "# 3. 不可违反边界",
        "# 4. 销售原则",
        "# 5. 决策协议",
        "# 6. 输出合同",
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
        "tool_fact_reference_options",
        "authoritative_fact_reference_options",
        "structured_delivery_decisions",
        "registration_fact_status",
        "store_fact_status",
        "smallest_next_commitment",
        "active_friction",
        "decision_opportunity",
    )
    for contract in required_contracts:
        assert contract in PARALLEL_REPLY_SYSTEM_PROMPT
    assert '"structured_delivery_decisions": []' in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "绝不能凭输出示例或经验虚构默认 fact_ref" in PARALLEL_REPLY_SYSTEM_PROMPT

    semantic_boundaries = (
        "不能靠关键词",
        "匹配固定场景",
        "不是业务事实、固定场景、客户标签或成品话术",
        "只有你负责理解客户、判断销售节奏",
        "这个选择属于你的销售判断",
        "代码只核验引用、结构和真实 ID",
        "收缩客户的不确定性，不扩大问题空间",
        "提问必须有决策价值",
        "只有系统确实能根据答案提供不同的权威事实",
        "`active_friction` 只记录客户已经表达的阻力",
    )
    for boundary in semantic_boundaries:
        assert boundary in PARALLEL_REPLY_SYSTEM_PROMPT

    factual_boundaries = (
        "人工转账是允许的付款方式",
        "订单不是发卡前置",
        "同轮最多一张",
        "不能提前保证未知结果",
        "不得因为原始消息类型含糊而把已知状态降级",
    )
    for boundary in factual_boundaries:
        assert boundary in PARALLEL_REPLY_SYSTEM_PROMPT


def test_sales_judgment_keeps_model_owned_motion_fields_without_interpreting_them() -> None:
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
        "established_keys": ["effect", "activity"],
        "active_friction": "担心预约金是不是额外收费",
        "decision_opportunity": "澄清预约金用途后确认是否保留名额",
        "primary_objective": "降低付款顾虑",
        "smallest_next_commitment": "确认是否接受10元抵扣机制",
        "posture": "advance",
        "reason": "客户仍在询问而非明确退出",
    }


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

    _validate_parallel_selected_content_delivery(
        [{"type": "text", "content": "活动价格之前已经给您介绍过了。"}],
        state,
    )
    assert completed_parallel_selected_content_ids(
        [{"type": "text", "content": "活动价格之前已经给您介绍过了。"}],
        state,
        ["s10_activity_intro"],
    ) == []


def test_available_content_still_requires_its_structured_media() -> None:
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

    with pytest.raises(ValueError, match="selected_content_delivery_missing"):
        _validate_parallel_selected_content_delivery(
            [{"type": "text", "content": "活动价268元。"}],
            state,
        )


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


def test_fact_audit_model_receives_only_facts_and_visible_reply() -> None:
    class _Client:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                model_fact_audit_enabled=True,
                model_fact_audit_timeout_seconds=15.0,
                model_fact_audit_tier="reply",
            )
            self.calls: list[tuple[list[dict], str]] = []

        async def chat_json(self, messages, *, tier, **_kwargs):
            self.calls.append((messages, tier))
            return {"status": "pass", "violations": []}

    client = _Client()
    result, call = asyncio.run(
        _run_parallel_reply_fact_audit(
            state=_state(),
            model_client=client,
            messages=[{"type": "text", "content": "本次活动价268元。"}],
            payload={"used_fact_refs": ["history:1"], "selected_content_ids": []},
        )
    )

    assert result["status"] == "pass"
    assert call["input"]["tier"] == "reply"
    assert client.calls[0][1] == "reply"
    assert "reply_messages" in client.calls[0][0][1]["content"]


def test_fact_audit_invalid_quote_gets_one_schema_only_retry() -> None:
    class _Client:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                model_fact_audit_enabled=True,
                model_fact_audit_timeout_seconds=15.0,
                model_fact_audit_tier="reply",
            )
            self.calls: list[list[dict]] = []

        async def chat_json(self, messages, *, tier, **_kwargs):
            assert tier == "reply"
            self.calls.append(messages)
            if len(self.calls) == 1:
                return {
                    "status": "fail",
                    "violations": [
                        {
                            "code": "wrong_temporality",
                            "message_index": 0,
                            "quote": "已经登记资料",
                            "evidence_refs": [],
                            "reason": "没有登记完成事实",
                        }
                    ],
                }
            return {
                "status": "fail",
                "violations": [
                    {
                        "code": "wrong_temporality",
                        "message_index": 0,
                        "quote": "已经给您预约成功了",
                        "evidence_refs": [],
                        "reason": "没有预约完成事实",
                    }
                ],
            }

    client = _Client()
    result, call = asyncio.run(
        _run_parallel_reply_fact_audit(
            state=_state(),
            model_client=client,
            messages=[{"type": "text", "content": "已经给您预约成功了。"}],
            payload={"used_fact_refs": ["current_message"], "selected_content_ids": []},
        )
    )

    assert len(client.calls) == 2
    assert result["status"] == "fail"
    assert call["validation_retry"]["validation_error"] == "reply_fact_audit_quote_not_in_message"
    retry_system = call["validation_retry"]["input"]["messages"][0]["content"]
    assert "不得输出 reply_messages" in retry_system


def test_fact_audit_input_includes_reply_claims_as_non_authoritative_metadata() -> None:
    audit_input = _parallel_reply_fact_audit_input(
        state=_state(),
        messages=[{"type": "text", "content": "请先把付款截图发我核对。"}],
        payload={
            "used_fact_refs": ["current_message"],
            "selected_content_ids": [],
            "action": "ask",
            "payment_assessment": {
                "status": "unverified_paid_claim",
                "evidence_refs": ["current_message"],
            },
        },
    )

    assert audit_input["reply_audit_metadata"]["authority"] == "reply_owned_non_authoritative_claims"
    assert audit_input["reply_audit_metadata"]["action"] == "ask"
    assert audit_input["reply_audit_metadata"]["payment_assessment"]["status"] == "unverified_paid_claim"


def test_fact_audit_input_includes_current_structured_tool_delivery_options() -> None:
    state = _state()
    state["evidence_join"]["tool_facts"] = {
        "customer_store_lookup": {
            "store_resolution_fact": {
                "status": "send_single",
                "delivery_store_ids": ["501"],
                "candidate_search_complete": True,
            }
        }
    }

    audit_input = _parallel_reply_fact_audit_input(
        state=state,
        messages=[{"type": "text", "content": "请再补充省市。"}],
        payload={"used_fact_refs": ["current_message"], "selected_content_ids": []},
    )

    assert audit_input["structured_delivery_options"]["store_address"]["available_store_ids"] == ["501"]


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
    assert '"sales_judgment_posture":["answer","advance","switch","pause","close"]' in repair_contract
    assert "逐字段保留上一版 sales_judgment" in repair_contract
    assert "语义判断仍由 Reply" in repair_contract
    assert "不得评价或重做销售策略" in repair_contract
    assert "逐字保留原 text" in repair_contract
    assert "事实审计错误只修改 violation.quote" in repair_contract


def test_fact_audit_input_surfaces_claim_bearing_business_facts() -> None:
    state = _state()
    state["evidence_join"]["shared_context"]["rules"] = {
        "AUTHORITATIVE FACTS": {
            "customer_visible_evidence_policy": {
                "effect_confidence": "绝大多数客户都是一次就好",
            },
            "offer": {"new_customer_price": 268},
        }
    }

    audit_input = _parallel_reply_fact_audit_input(
        state=state,
        messages=[{"type": "text", "content": "绝大多数客户都是一次就好。"}],
        payload={"used_fact_refs": [], "selected_content_ids": []},
    )

    assert audit_input["authoritative_claim_facts"] == {
        "offer": {"new_customer_price": 268},
        "customer_visible_evidence_policy": {
            "effect_confidence": "绝大多数客户都是一次就好",
        },
    }


def test_fact_audit_failure_gets_one_generic_reply_repair() -> None:
    class _TraceLogger:
        class _Node:
            def __init__(self, state: dict) -> None:
                self.entry = {"node": "synthesize_reply", "tool_calls": []}
                state.setdefault("trace", []).append(self.entry)

            def __enter__(self):
                return {"entry": self.entry}

            def __exit__(self, *_args):
                return False

        def node(self, state, _name, _input):
            return self._Node(state)

    valid_reply = {
        "reply_messages": [{"type": "text", "content": "我先把活动内容给您说清楚。"}],
        "used_fact_refs": ["current_message"],
        "selected_content_ids": [],
        "action": "offer",
        "action_reason": "回答当前问题",
        "sales_judgment": {
            "customer_goal": "了解参加方式",
            "established_keys": [],
            "primary_objective": "介绍活动",
            "posture": "answer",
            "reason": "活动事实尚未说明",
        },
        "payment_assessment": {"status": "none", "evidence_refs": []},
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
            self.settings = SimpleNamespace(
                model_fact_audit_enabled=True,
                model_fact_audit_timeout_seconds=15.0,
                model_fact_audit_tier="reply",
            )
            self.calls = 0

        async def chat_json(self, _messages, *, tier, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    **valid_reply,
                    "reply_messages": [{"type": "text", "content": "已经给您预约成功了。"}],
                }
            if self.calls == 2:
                assert tier == "reply"
                return {
                    "status": "fail",
                    "violations": [
                        {
                            "code": "wrong_temporality",
                            "message_index": 0,
                            "quote": "已经给您预约成功了",
                            "evidence_refs": [],
                            "reason": "没有预约完成事实",
                        }
                    ],
                }
            if self.calls == 3:
                return valid_reply
            assert tier == "reply"
            return {"status": "pass", "violations": []}

    client = _Client()
    node = create_synthesize_reply_node(
        trace_logger=_TraceLogger(),
        model_client=client,
        debug_message_contents=debug_message_contents,
        reply_messages_for_model=lambda _state: [
            {"role": "system", "content": "output strict json"},
            {"role": "user", "content": "{}"},
        ],
        should_use_model_reply=lambda _state: True,
        validated_model_messages=validated_model_messages,
    )
    state = {
        **_state(),
        "request_id": "fact-audit-repair",
        "trace": [],
        "errors": [],
        "warnings": [],
        "required_tools": [],
    }

    output = asyncio.run(node(state))

    assert client.calls == 4
    assert output["reply_source"] == "single_targeted_repair_model"
    assert output["reply_messages"][0]["content"] == "我先把活动内容给您说清楚。"
    assert output["reply_fact_audit"]["status"] == "pass"
