from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.graph.nodes.reply_delivery_manifest import (
    ACTIVITY_INTRO_FACTS,
    authorize_sop_delivery_manifest,
    build_sop_delivery_manifest,
    merge_manifest_into_reply_contract,
)
from app.graph.nodes.reply_validation import (
    _validate_known_customer_fields_not_requested,
    _validate_effect_trust_contract,
    validate_semantic_contract_evidence,
)
from app.graph.planner.brain_v2_normalizer import (
    _append_required_payment_collection,
    _enforce_payment_authorization_contract,
    _payment_consistency_violations,
    _sales_progression_contract_violations,
    _sop_delivery_decision_consistency_violations,
    _merge_current_question_contract,
    _normalize_current_turn_resolution,
    _normalize_reply_contract,
)
from app.services.reply_governance import reply_governance_flags
from app.services.sop_event_decision import normalize_event_decision
from app.services.memory_store import CustomerMemoryStore
from app.services.sop_execution_service import (
    _chat_gate_shadow_comparison,
    _chat_gate_safety_decision_violations,
    _chat_gate_scene_decision_violations,
    _event_normalizer_shadow_comparison,
    _has_persisted_stop_contact,
    _sop_event_system_prompt,
)
from app.policies.sales_flow import precision_qa_for_id, precision_qa_index_for_gate
from app.prompts.sop_chat_gate import build_sop_chat_gate_messages


def test_rollout_flags_default_to_shadow_only() -> None:
    flags = reply_governance_flags(Settings(_env_file=None))

    assert flags == {
        "model_semantic_routing_enabled": False,
        "semantic_contract_enabled": False,
        "model_payment_sequencing_enabled": False,
        "event_schema_only_normalizer_enabled": False,
        "shadow_mode": True,
        "configured": {
            "model_semantic_routing_enabled": False,
            "semantic_contract_enabled": False,
            "model_payment_sequencing_enabled": False,
            "event_schema_only_normalizer_enabled": False,
        },
    }


def test_configured_flags_do_not_change_runtime_while_shadowing() -> None:
    flags = reply_governance_flags(
        Settings(
            _env_file=None,
            REPLY_MODEL_SEMANTIC_ROUTING_ENABLED=True,
            REPLY_SEMANTIC_CONTRACT_ENABLED=True,
            REPLY_MODEL_PAYMENT_SEQUENCING_ENABLED=True,
            SOP_EVENT_SCHEMA_ONLY_NORMALIZER_ENABLED=True,
            REPLY_GOVERNANCE_SHADOW_MODE=True,
        )
    )

    assert flags["configured"]["model_semantic_routing_enabled"] is True
    assert flags["configured"]["semantic_contract_enabled"] is True
    assert flags["model_semantic_routing_enabled"] is False
    assert flags["semantic_contract_enabled"] is False


def test_gate_prompt_only_exposes_split_ownership_in_governed_path() -> None:
    legacy_prompt = build_sop_chat_gate_messages({})[0]["content"]
    governed_prompt = build_sop_chat_gate_messages(
        {"decision_ownership": {"scene_and_sales_rhythm": "model"}}
    )[0]["content"]

    assert '"safety_decision"' not in legacy_prompt
    assert '"scene_decision"' not in legacy_prompt
    assert '"safety_decision"' in governed_prompt
    assert '"scene_decision"' in governed_prompt


def test_gate_scene_catalog_exposes_reserved_effect_contracts() -> None:
    index = {item["scene_id"]: item for item in precision_qa_index_for_gate()}

    assert index["effect_definition_trust"]["priority"] == "reserved"
    assert index["one_session_effect"]["priority"] == "reserved"
    assert precision_qa_for_id("effect_definition_trust")["gate_route"] == "ai_only"


def test_governed_scene_decision_requires_valid_evidence_reference() -> None:
    selector_input = {
        "conversation_evidence": [
            {"message_ref": "current_message", "direction": "customer", "content": "只是颜色变淡吗？"}
        ]
    }

    assert _chat_gate_scene_decision_violations(
        {
            "scene_decision": {
                "current_question": "效果定义",
                "explicit_questions": [
                    {"question_id": "effect", "question": "只是变淡吗", "resolution_goal": "回答效果定义"}
                ],
                "blocker": "trust",
                "evidence_refs": [],
            }
        },
        selector_input,
    ) == ["scene_decision_requires_evidence"]
    assert _chat_gate_scene_decision_violations(
        {
            "scene_decision": {
                "current_question": "效果定义",
                "explicit_questions": [
                    {"question_id": "effect", "question": "只是变淡吗", "resolution_goal": "回答效果定义"}
                ],
                "blocker": "trust",
                "evidence_refs": ["current_message"],
            }
        },
        selector_input,
    ) == []


def test_event_prompt_only_requires_safety_evidence_in_schema_only_path() -> None:
    assert '"safety_decision"' not in _sop_event_system_prompt(schema_only=False)
    assert '"safety_decision"' in _sop_event_system_prompt(schema_only=True)


def test_deferred_sop_cannot_remain_a_locked_source_for_current_turn() -> None:
    violations = _sop_delivery_decision_consistency_violations(
        manifest={"active": True, "sop_pack_id": "s10_activity_intro"},
        delivery_decision={"action": "defer"},
        sales_progression={"source_pack_ids": ["s10_activity_intro"]},
        reply_contract={"required_deliveries": []},
    )

    assert [item["missing"] for item in violations] == [
        "selected_source_pack_conflicts_with_deferred_delivery"
    ]


def test_continued_progression_requires_a_concrete_model_action() -> None:
    violations = _sales_progression_contract_violations(
        {"status": "continue", "target_stage": "activity", "action": "none"}
    )

    assert [item["missing"] for item in violations] == [
        "continued_progression_requires_concrete_action"
    ]
    assert _sales_progression_contract_violations(
        {"status": "continue", "target_stage": "activity", "action": "deliver_value"}
    ) == []


def test_known_fields_contract_only_accepts_fact_snapshot() -> None:
    contract = _normalize_reply_contract(
        {"known_fields_not_to_request": ["name", "mobile", "location"]},
        state={"conversation_state": {"customer_fields": {}}},
        sales_progression={"required_message_types": ["text"]},
    )

    assert contract["known_fields_not_to_request"] == []


def test_activity_registration_description_is_not_a_repeated_field_request() -> None:
    state = {"reply_contract": {"known_fields_not_to_request": ["name", "mobile"]}}

    _validate_known_customer_fields_not_requested(
        [{"type": "text", "content": "线上预定每位10元并登记姓名电话，到店抵扣10元。"}],
        state,
    )
    with pytest.raises(ValueError, match="known_customer_field_requested_again"):
        _validate_known_customer_fields_not_requested(
            [{"type": "text", "content": "麻烦您把姓名和手机号再发我一下。"}],
            state,
        )


def test_gate_question_list_becomes_semantic_delivery_contract() -> None:
    state = {
        "content": "重庆有嘛\n真实嘛\n价格",
        "sop_gate_decision": {
            "scene_decision": {
                "explicit_questions": [
                    {"question_id": "store", "question": "重庆有门店吗", "resolution_goal": "回答门店存在性"},
                    {"question_id": "trust", "question": "真实吗", "resolution_goal": "回答真实性"},
                    {"question_id": "price", "question": "价格", "resolution_goal": "回答活动价格"},
                ]
            }
        },
    }
    resolution = _normalize_current_turn_resolution({}, state=state)
    contract = _merge_current_question_contract({}, current_turn_resolution=resolution)

    assert [item["question_id"] for item in resolution["explicit_questions"]] == ["store", "trust", "price"]
    assert contract["required_fact_ids"] == ["turn_store", "turn_trust", "turn_price"]


def test_deferred_manifest_does_not_impose_candidate_fact_contract_on_current_turn() -> None:
    candidate = build_sop_delivery_manifest(
        {
            "route": "ai_then_sop",
            "send_sop": True,
            "sop_pack_id": "s10_activity_intro",
            "reply_messages": [{"type": "text", "order": 1, "content": {"text": "活动原文"}}],
        }
    )
    deferred = authorize_sop_delivery_manifest(
        candidate,
        payment_decision={"action": "none"},
        precision_scene_id="",
        delivery_decision={"action": "defer"},
    )

    contract = merge_manifest_into_reply_contract({}, deferred)

    assert contract.get("required_fact_ids") in (None, [])
    assert contract.get("fact_definitions") in (None, {})


def test_unauthorized_payment_decision_removes_contradictory_card_requirement() -> None:
    progression, contract, violations = _enforce_payment_authorization_contract(
        payment_decision={"action": "explain"},
        sales_progression={"required_message_types": ["text", "payment_collection"]},
        reply_contract={
            "required_deliveries": [
                {"message_type": "text"},
                {"message_type": "payment_collection"},
            ]
        },
    )

    assert progression["required_message_types"] == ["text"]
    assert contract["required_deliveries"] == [{"message_type": "text"}]
    assert [item["missing"] for item in violations] == [
        "payment_required_delivery_without_authorization"
    ]


def test_shadow_comparisons_never_mark_candidate_as_applied() -> None:
    gate = _chat_gate_shadow_comparison(
        {"route": "sop_only", "sop_pack_id": "activity"},
        {
            "route": "ai_only",
            "sop_pack_id": "",
            "safety_decision": {"status": "continue", "reason_type": "none"},
        },
    )
    event = _event_normalizer_shadow_comparison(
        {"decision": "send", "send_sop": True, "sop_pack_id": "activity"},
        [],
        {"decision": "skip", "send_sop": False, "sop_pack_id": ""},
        ["platform_no_send_requires_safety_evidence"],
    )

    assert gate["applied"] == "baseline"
    assert gate["changed"] is True
    assert event["applied"] == "baseline"
    assert event["changed"] is True


def test_activity_manifest_exposes_fact_contract_without_changing_messages() -> None:
    manifest = build_sop_delivery_manifest(
        {
            "route": "sop_only",
            "send_sop": True,
            "sop_pack_id": "s10_activity_intro",
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "活动原文"}},
                {"type": "image", "order": 2, "content": {"url": "https://example.com/activity.png"}},
            ],
        }
    )

    assert manifest["core_fact_contract"] == "activity_intro_v1"
    assert manifest["required_fact_ids"] == list(ACTIVITY_INTRO_FACTS)
    assert [item["message_type"] for item in manifest["messages"]] == ["text", "image"]


def test_semantic_fact_evidence_must_quote_final_text() -> None:
    state = {
        "reply_governance": {"semantic_contract_enabled": True},
        "reply_contract": {
            "required_fact_ids": ["activity_price", "refund_policy"],
            "fact_definitions": {
                "activity_price": "活动价",
                "refund_policy": "退款规则",
            },
        },
    }
    messages = [
        {
            "type": "text",
            "order": 1,
            "content": "活动价是268元，未做或不满意可退，实际按付款记录核对。",
        }
    ]
    payload = {
        "contract_evidence": [
            {"fact_id": "activity_price", "message_order": 1, "evidence": "活动价是268元"},
            {
                "fact_id": "refund_policy",
                "message_order": 1,
                "evidence": "未做或不满意可退，实际按付款记录核对",
            },
        ]
    }

    validate_semantic_contract_evidence(payload, messages, state)

    payload["contract_evidence"][1]["evidence"] = "未出现在回复里的退款承诺"
    with pytest.raises(ValueError, match="semantic_contract_evidence_invalid:refund_policy"):
        validate_semantic_contract_evidence(payload, messages, state)


def test_schema_only_platform_normalizer_does_not_override_model_skip() -> None:
    selector_input = {
        "mode": "platform_actions",
        "platform_actions": {
            "editable_text_messages": [{"order": 1, "text": "平台任务"}],
            "readonly_messages": [],
        },
    }
    output, violations = normalize_event_decision(
        {"decision": "skip", "strategy": "conflict_guard"},
        selector_input,
        schema_only=True,
    )

    assert output["decision"] == "skip"
    assert "platform_no_send_requires_safety_evidence" in violations

    safe_output, safe_violations = normalize_event_decision(
        {
            "decision": "skip",
            "strategy": "conflict_guard",
            "safety_decision": {
                "block_send": True,
                "reason_type": "severe_complaint",
                "evidence_refs": ["chat_29"],
            },
        },
        selector_input,
        schema_only=True,
    )
    assert safe_output["decision"] == "skip"
    assert "platform_no_send_requires_safety_evidence" not in safe_violations


def test_effect_semantic_contract_keeps_structural_price_and_card_boundary() -> None:
    state = {
        "reply_governance": {"semantic_contract_enabled": True},
        "reply_contract": {"effect_trust_scene_id": "effect_definition_trust"},
    }
    messages = [
        {"type": "text", "order": 1, "content": "我们先看真实改善参考。"},
        {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
    ]

    with pytest.raises(ValueError, match="effect_trust_payment_collection_not_allowed"):
        _validate_effect_trust_contract(messages, state)


def test_safety_stop_requires_customer_evidence_and_no_sop() -> None:
    selector_input = {
        "decision_ownership": {"safety": "model_with_message_evidence"},
        "conversation_evidence": [
            {"message_ref": "current_message", "direction": "customer", "content": "请不要再联系我"}
        ],
    }
    valid = {
        "route": "ai_only",
        "safety_decision": {
            "status": "stop",
            "reason_type": "stop_contact",
            "evidence_refs": ["current_message"],
        },
    }
    assert _chat_gate_safety_decision_violations(valid, selector_input) == []

    invalid = {
        **valid,
        "route": "sop_only",
        "sop_pack_id": "s10_activity_intro",
        "safety_decision": {**valid["safety_decision"], "evidence_refs": ["missing"]},
    }
    violations = _chat_gate_safety_decision_violations(invalid, selector_input)
    assert "safety_stop_evidence_ref_invalid" in violations
    assert "safety_stop_must_not_select_sop" in violations


def test_stop_contact_memory_is_persisted_without_message_body() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = CustomerMemoryStore(SimpleNamespace(memory_dir=Path(directory)))
        result = store.record_stop_contact(
            "corp|wechat|customer",
            request_id="req-stop",
            evidence_refs=["current_message"],
            reason="客户明确要求停止联系",
        )
        memory = store.load("corp|wechat|customer")

    assert result["status"] == "recorded"
    assert _has_persisted_stop_contact(memory) is True
    event = memory["history_events"][-1]
    assert event["event_type"] == "stop_contact_confirmed"
    assert "请不要再联系我" not in str(event)


def test_model_payment_sequencing_does_not_require_code_inferred_activity_stage() -> None:
    state = {"reply_governance": {"model_payment_sequencing_enabled": True}}
    messages = [
        {"type": "text", "content": "亲，可以先锁住名额。"},
        {"type": "payment_collection", "content": {"amount": 10}},
    ]
    violations = _payment_consistency_violations(
        state=state,
        decision="direct_reply",
        conversion_stage="deposit_push",
        next_step="send_deposit",
        payment_state="needs_payment",
        payment_action="send_now",
        payment_decision={"action": "send_now", "amount": 10, "party_size": 1},
        sales_progression={"action": "send_payment_card"},
        messages=messages,
    )
    assert not any(item.get("missing") == "payment_collection_requires_activity_intro" for item in violations)


def test_schema_normalizer_does_not_append_payment_card_for_model() -> None:
    state = {"reply_governance": {"model_payment_sequencing_enabled": True}}
    messages = _append_required_payment_collection(
        state=state,
        decision="direct_reply",
        conversion_stage="deposit_push",
        next_step="send_deposit",
        payment_state="needs_payment",
        payment_action="send_now",
        payment_decision={"action": "send_now", "amount": 10, "party_size": 1},
        messages=[{"type": "text", "content": "亲，可以先锁住名额。"}],
    )
    assert [item["type"] for item in messages] == ["text"]
