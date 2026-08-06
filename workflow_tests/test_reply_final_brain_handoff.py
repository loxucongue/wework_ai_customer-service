from __future__ import annotations

import json

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.reply_final_brain_handoff import reply_final_brain_handoff_shadow_from_planner_output


def test_reply_final_brain_handoff_groups_legacy_planner_semantics() -> None:
    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "planner_reply_messages": [{"type": "text", "content": {"text": "customer candidate"}}],
            "planner_decision": "need_tools",
            "conversion_stage": "payment",
            "payment_decision": {"action": "send_now"},
            "reply_strategy": {"tone": "warm"},
            "tool_plan_preview": {
                "fact_requirement": "required",
                "read_tool_calls": [{"name": "customer_store_lookup"}],
                "migration_audit": {"legacy_residue_count": 2},
            },
            "reply_chain_join_shadow": {
                "final_route": "reply_with_tools",
                "direct_reply_allowed": False,
            },
        }
    )

    assert handoff["schema_version"] == "reply_final_brain_handoff_shadow_v1"
    assert handoff["target_owner"] == "reply"
    assert handoff["input_groups"]["customer_message_candidates"]["planner_reply_messages"][0]["type"] == "text"
    assert handoff["input_groups"]["turn_outcome_signals"]["planner_decision"] == "need_tools"
    assert handoff["input_groups"]["turn_outcome_signals"]["conversion_stage"] == "payment"
    assert handoff["input_groups"]["sales_decision_signals"]["payment_decision"]["action"] == "send_now"
    assert handoff["input_groups"]["sales_decision_signals"]["reply_strategy"]["tone"] == "warm"
    assert handoff["input_groups"]["fact_and_tool_evidence"]["tool_plan_preview"]["read_tool_count"] == 1
    assert handoff["migration_audit"]["legacy_business_field_count"] == 5
    assert handoff["migration_audit"]["requires_reply_schema_before_activation"] is True
    assert "customer_visible_text" in handoff["ownership_contract"]["tool_planner_must_not_own"]


def test_reply_final_brain_handoff_allows_fact_only_output() -> None:
    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": {
                "fact_requirement": "none",
                "migration_audit": {"legacy_residue_count": 0},
            }
        }
    )

    assert handoff["migration_audit"]["legacy_business_field_count"] == 0
    assert "customer_message_candidates" not in handoff["input_groups"]
    assert "sales_decision_signals" not in handoff["input_groups"]
    assert handoff["input_groups"]["fact_and_tool_evidence"]["tool_plan_preview"]["fact_requirement"] == "none"


def test_reply_final_brain_handoff_is_not_consumed_by_current_model_payloads() -> None:
    state = {
        "normalized_content": "how to book",
        "conversation_history": ["user: how to book"],
        "reply_final_brain_handoff_shadow": {
            "schema_version": "reply_final_brain_handoff_shadow_v1",
            "input_groups": {
                "customer_message_candidates": {
                    "planner_reply_messages": [{"content": {"text": "shadow-only-handoff-marker"}}],
                }
            },
        },
        "request_context": {},
    }

    planner_payload = _planner_payload_for_model(state)
    reply_payload = reply_user_payload_for_model(state)
    combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

    assert "reply_final_brain_handoff_shadow" not in planner_payload
    assert "reply_final_brain_handoff_shadow" not in reply_payload
    assert "shadow-only-handoff-marker" not in combined
