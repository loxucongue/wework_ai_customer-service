from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.services.ai_sales_policy_service import AiSalesPolicyService


POLICY_PATH = Path(__file__).resolve().parents[1] / "ai_paths" / "app" / "policies" / "ai_sales_policy_v1.json"


class MutableProvider:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def load_raw(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return deepcopy(self.payload), {"provider": "test", "source": "fixture"}


def _raw_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _runtime_policy() -> dict[str, Any]:
    settings = Settings(_env_file=None, AI_SALES_POLICY_ENABLED=True)
    return AiSalesPolicyService(settings, provider=MutableProvider(_raw_policy())).runtime_snapshot()


def _planner_payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "decision": "direct_reply",
        "stage": "S2",
        "sub_rule_id": "TEST_POLICY",
        "conversion_stage": "objection_resolution",
        "customer_type": "price",
        "main_blocker": "price",
        "next_step": "solve_blocker",
        "reply_messages": [{"type": "text", "content": "planner draft"}],
        "tool_calls": [],
    }
    payload.update(updates)
    return payload


def test_local_policy_is_valid_and_runtime_ready() -> None:
    policy = AiSalesPolicyService(Settings()).load()

    assert policy["schema_version"] == "ai_sales_policy_v1"
    assert policy["runtime_mode"] == "active"
    assert policy["closing"]["silent_tasks_mode"] == "shadow"
    assert policy["audit"]["status"] == "ok"
    assert len(policy["checksum"]) == 64


def test_policy_rejects_raw_prompt_fields() -> None:
    raw = _raw_policy()
    raw["closing"]["raw_prompt"] = "bypass runtime contract"

    with pytest.raises(ValueError, match="raw prompt field is forbidden"):
        AiSalesPolicyService(Settings(), provider=MutableProvider(raw)).load()


def test_policy_uses_last_known_good_after_invalid_refresh() -> None:
    provider = MutableProvider(_raw_policy())
    service = AiSalesPolicyService(Settings(), provider=provider)
    first = service.load()
    provider.payload = {"schema_version": "broken"}

    recovered = service.load()

    assert recovered["checksum"] == first["checksum"]
    assert recovered["runtime_health"]["status"] == "degraded"
    assert recovered["runtime_health"]["using_last_known_good"] is True


def test_normalizer_keeps_model_semantics_but_only_accepts_configured_keys() -> None:
    state = {
        "ai_sales_policy": _runtime_policy(),
        "content": "price concern",
        "normalized_content": "price concern",
        "conversation_history": [],
    }
    plan = build_planner_plan_v2(
        state,
        _planner_payload(
            primary_task={"type": "resolve_blocker", "goal": "answer concern", "basis": ["current turn"]},
            secondary_tasks=[{"type": "closing_progression", "goal": "low pressure", "basis": []}],
            realtime_intent={"type": "blocker_expression", "confidence": "high", "basis": ["price"]},
            emotion_decision={
                "label": "hesitant",
                "pressure": "low",
                "flow_action": "lower_pressure",
                "basis": ["weighing options"],
            },
            closing_decision={
                "action": "enter",
                "sequence_key": "price_hesitation",
                "node_key": "value_reframe",
                "trigger": "positive_progress",
                "customer_state": "hesitant",
                "pressure": "low",
                "basis": ["direction accepted"],
            },
        ),
    )

    assert plan["primary_task"]["type"] == "resolve_blocker"
    assert plan["secondary_tasks"][0]["type"] == "closing_progression"
    assert plan["realtime_intent"]["type"] == "blocker_expression"
    assert plan["emotion_decision"]["flow_action"] == "lower_pressure"
    assert plan["closing_decision"]["node_key"] == "value_reframe"

    invalid = build_planner_plan_v2(
        state,
        _planner_payload(
            primary_task={"type": "python_keyword_route"},
            realtime_intent={"type": "made_up_intent"},
            emotion_decision={"label": "made_up_emotion"},
            closing_decision={
                "action": "enter",
                "sequence_key": "made_up_sequence",
                "node_key": "made_up_node",
            },
        ),
    )
    assert invalid["primary_task"] == {}
    assert invalid["realtime_intent"] == {}
    assert invalid["emotion_decision"] == {}
    assert invalid["closing_decision"]["action"] == "none"


def test_planner_and_reply_receive_same_policy_without_storage_path() -> None:
    policy = _runtime_policy()
    state = {
        "ai_sales_policy": policy,
        "content": "hello",
        "normalized_content": "hello",
        "conversation_history": [],
        "primary_task": {"type": "normal_conversation", "goal": "continue", "basis": []},
        "realtime_intent": {"type": "normal_exchange", "confidence": "high", "basis": []},
        "emotion_decision": {"label": "neutral", "pressure": "normal", "flow_action": "keep", "basis": []},
        "closing_decision": {"action": "none", "sequence_key": "none"},
    }

    planner_payload = _planner_payload_for_model(state)
    reply_payload = reply_user_payload_for_model(state)

    assert planner_payload["ai_sales_policy"]["policy_version"] == policy["policy_version"]
    assert reply_payload["ai_sales_policy"]["checksum"] == policy["checksum"]
    assert "source" not in planner_payload["ai_sales_policy"]
    assert "source" not in reply_payload["ai_sales_policy"]
    assert reply_payload["primary_task"]["type"] == "normal_conversation"
