from __future__ import annotations

import json

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.prompts.sop_chat_gate import build_sop_chat_gate_messages
from app.schemas import ChatRequest
from app.services.sop_execution_service import _chat_selector_input


SHADOW_ONLY_FIELDS = (
    "sop_gate_preview",
    "sop_gate_router_shadow",
    "reply_chain_shadow_context",
    "tool_plan_preview",
    "read_only_tool_executor_shadow",
    "reply_chain_join_shadow",
    "reply_final_brain_handoff_shadow",
    "parallel_reply_chain_shadow",
    "reply_chain_commit_shadow",
    "reply_chain_refactor_flags",
    "parallel_gate_planner_runner_shadow",
    "parallel_reply_chain_diagnostics",
    "parallel_reply_chain_comparison",
)


def _state_with_shadow_fields() -> dict:
    state = {
        "content": "how to book",
        "normalized_content": "how to book",
        "conversation_history": ["user: how to book"],
        "request_context": {"category_id": "S10"},
    }
    for field in SHADOW_ONLY_FIELDS:
        state[field] = {
            "schema_version": f"{field}_v_test",
            "marker": f"shadow-only-marker::{field}",
            "nested": {"notes": [f"shadow-only-nested::{field}"]},
        }
    return state


def test_all_reply_chain_shadow_fields_are_excluded_from_planner_and_reply_payloads() -> None:
    state = _state_with_shadow_fields()

    planner_payload = _planner_payload_for_model(state)
    reply_payload = reply_user_payload_for_model(state)
    combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

    for field in SHADOW_ONLY_FIELDS:
        assert field not in planner_payload
        assert field not in reply_payload
        assert f"shadow-only-marker::{field}" not in combined
        assert f"shadow-only-nested::{field}" not in combined


def test_reply_chain_shadow_fields_are_excluded_from_chat_gate_selector_messages() -> None:
    request_context = {
        "category_id": "S10",
        **{
            field: {
                "schema_version": f"{field}_v_test",
                "marker": f"shadow-only-gate-marker::{field}",
            }
            for field in SHADOW_ONLY_FIELDS
        },
    }
    request = ChatRequest(
        content="how to join",
        customer_id="sim_customer_001",
        corp_id="sim_corp",
        conversation_history=["user: hello", "assistant: opening"],
        request_context=request_context,
    )
    selector_input = _chat_selector_input(
        request,
        [
            {
                "id": "s10_activity_intro",
                "scope": "chat_gate",
                "scopes": ["chat_gate"],
                "name": "Activity intro",
                "purpose": "Explain the activity.",
                "mainline_stage": "activity",
                "reply_messages": [
                    {
                        "type": "text",
                        "order": 1,
                        "content": {"text": "Activity details."},
                    }
                ],
            }
        ],
        sop_progress_evidence={},
        recent_delivery_evidence=[],
        customer_memory={},
        customer_context={},
    )
    gate_messages = build_sop_chat_gate_messages(selector_input)
    combined = json.dumps(gate_messages, ensure_ascii=False)

    for field in SHADOW_ONLY_FIELDS:
        assert field not in selector_input
        assert f"shadow-only-gate-marker::{field}" not in combined
