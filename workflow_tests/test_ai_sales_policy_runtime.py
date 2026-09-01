from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.graph.nodes.reply_context import reply_user_payload_for_model
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


def test_reply_receives_policy_without_storage_path() -> None:
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

    reply_payload = reply_user_payload_for_model(state)

    assert reply_payload["ai_sales_policy"]["checksum"] == policy["checksum"]
    assert "source" not in reply_payload["ai_sales_policy"]
    assert reply_payload["primary_task"]["type"] == "normal_conversation"
