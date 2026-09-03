from __future__ import annotations

import json
import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.graph.nodes.reply_nodes import (  # noqa: E402
    _normalized_policy_decision,
    _policy_safety_floor,
    _validate_policy_reply_consistency,
    _validate_policy_safety_floor,
)
from app.graph.nodes.reply_generation import (  # noqa: E402
    ReplyModelPipelineError,
    _policy_safety_failure_recovery,
    _run_reply_model_pipeline,
)
from app.graph.nodes.reply_validation import _requested_store_scope_regions  # noqa: E402
from app.chat_runtime import _record_stop_contact_fact  # noqa: E402
from app.services.outreach.planning import _closing_shadow_terminal_reason  # noqa: E402
from app.services.outreach.execution import TaskExecutor  # noqa: E402
from app.services.ai_sales_policy_service import _audit_policy  # noqa: E402


def _state() -> dict[str, Any]:
    policy_path = PROJECT_ROOT / "ai_paths" / "app" / "policies" / "ai_sales_policy_v1.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["runtime_mode"] = "active"
    return {
        "ai_sales_policy": policy,
        "sales_strategy_catalog": {
            "categories": [{"category_key": "price", "name": "价格"}],
            "tactic_tags": [],
        },
        "shared_context": {
            "conversation": [
                {"role": "customer", "message_ref": "customer:previous"},
                {"role": "assistant", "message_ref": "assistant:previous"},
            ]
        },
    }


def _valid_decision() -> dict[str, Any]:
    return {
        "primary_task": {
            "type": "answer_current_question",
            "goal": "回答当前问题",
            "basis": ["客户当前在问价格"],
        },
        "secondary_tasks": [],
        "realtime_intent": {
            "type": "fact_inquiry",
            "secondary_types": [],
            "confidence": "high",
            "evidence_refs": ["current_message"],
            "basis": ["客户直接提问"],
        },
        "emotion_decision": {
            "label": "neutral",
            "confidence": "medium",
            "pressure": "normal",
            "evidence_refs": ["current_message"],
            "basis": ["没有明显情绪信号"],
        },
        "closing_decision": {
            "action": "none",
            "sequence_key": "none",
            "node_key": "",
            "trigger": "none",
            "customer_state": "none",
            "pressure": "none",
            "evidence_refs": ["current_message"],
            "basis": ["当前先回答问题"],
        },
    }


def test_policy_catalog_and_runtime_contract_are_fail_closed() -> None:
    policy_path = PROJECT_ROOT / "ai_paths" / "app" / "policies" / "ai_sales_policy_v1.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    assert _audit_policy(policy)["error_count"] == 0

    wrong_schema = json.loads(json.dumps(policy, ensure_ascii=False))
    wrong_schema["decision_schema_version"] = "unknown"
    assert any(
        item["code"] == "decision_schema_version"
        for item in _audit_policy(wrong_schema)["issues"]
    )

    wrong_catalog = json.loads(json.dumps(policy, ensure_ascii=False))
    wrong_catalog["intent"]["realtime_intents"].pop()
    assert any(item["code"] == "intent_catalog" for item in _audit_policy(wrong_catalog)["issues"])

    unsafe_shadow = json.loads(json.dumps(policy, ensure_ascii=False))
    unsafe_shadow["closing"]["silent_tasks_mode"] = "active"
    assert any(item["code"] == "silent_tasks_mode" for item in _audit_policy(unsafe_shadow)["issues"])


def test_policy_decision_normalizes_cross_field_constraints_and_refs() -> None:
    raw = _valid_decision()
    raw["realtime_intent"] = {
        "type": "defer",
        "secondary_types": ["fact_inquiry", "unknown_intent", "fact_inquiry"],
        "confidence": "high",
        "evidence_refs": ["current_message", "assistant:previous", "customer:previous"],
        "basis": ["客户希望暂缓"],
    }
    raw["emotion_decision"] = {
        "label": "hesitant",
        "confidence": "high",
        "pressure": "normal",
        "evidence_refs": ["current_message"],
        "basis": ["客户仍在权衡"],
    }
    raw["closing_decision"] = {
        "action": "advance",
        "sequence_key": "gentle_invite",
        "node_key": "confirm_visit",
        "trigger": "positive_progress",
        "customer_state": "new_blocker",
        "pressure": "normal",
        "evidence_refs": ["current_message"],
        "basis": ["出现新卡点"],
    }

    result = _normalized_policy_decision(raw, state=_state())

    assert result["decision_status"] == "degraded"
    assert result["realtime_intent"]["secondary_types"] == ["fact_inquiry"]
    assert result["realtime_intent"]["evidence_refs"] == ["current_message", "customer:previous"]
    assert result["emotion_decision"]["pressure"] == "low"
    assert result["closing_decision"]["action"] == "pause"
    assert result["closing_decision"]["node_key"] == ""
    assert result["closing_decision"]["pressure"] == "low"
    assert result["policy_decision"]["closing_decision"] == result["closing_decision"]


def test_policy_decision_preserves_up_to_three_secondary_intents() -> None:
    raw = _valid_decision()
    raw["secondary_tasks"] = [
        {"type": "resolve_blocker", "goal": "处理顾虑", "basis": []},
        {"type": "closing_progression", "goal": "适度推进", "basis": []},
    ]
    raw["realtime_intent"]["secondary_types"] = [
        "blocker_expression",
        "transaction_progress",
        "information_submission",
    ]

    result = _normalized_policy_decision(raw, state=_state())

    assert result["decision_status"] == "ok"
    assert result["realtime_intent"]["secondary_types"] == [
        "blocker_expression",
        "transaction_progress",
        "information_submission",
    ]
    assert [item["type"] for item in result["secondary_tasks"]] == [
        "resolve_blocker",
        "closing_progression",
    ]


def test_missing_policy_decision_degrades_without_raising() -> None:
    result = _normalized_policy_decision(None, state=_state())

    assert result["decision_status"] == "degraded"
    assert "missing_policy_decision" in result["decision_reasons"]
    assert result["policy_decision"]["primary_task"] == {}
    assert result["policy_decision"]["realtime_intent"] == {}
    assert result["policy_decision"]["closing_decision"]["action"] == "none"

    with pytest.raises(ValueError, match="policy_decision_schema_invalid"):
        _validate_policy_reply_consistency(
            {
                "reply_messages": [{"type": "text", "order": 1, "content": "继续了解一下"}],
                "sales_judgment": {"posture": "advance"},
            },
            _state(),
        )


def test_explicit_exit_forces_hard_stop_and_complete() -> None:
    raw = _valid_decision()
    raw["primary_task"] = {
        "type": "closing_progression",
        "goal": "继续推进",
        "basis": ["错误的推进决定"],
    }
    raw["secondary_tasks"] = [
        {"type": "answer_current_question", "goal": "继续回复", "basis": []}
    ]
    raw["realtime_intent"] = {
        "type": "explicit_exit",
        "secondary_types": ["normal_exchange"],
        "confidence": "high",
        "evidence_refs": ["current_message"],
        "basis": ["客户明确要求停止联系"],
    }
    raw["closing_decision"] = {
        "action": "advance",
        "sequence_key": "final_confirm",
        "node_key": "confirm_intent",
        "trigger": "positive_progress",
        "customer_state": "engaged",
        "pressure": "normal",
        "evidence_refs": ["current_message"],
        "basis": ["错误的继续推进决定"],
    }

    result = _normalized_policy_decision(raw, state=_state())

    assert result["decision_status"] == "degraded"
    assert result["primary_task"]["type"] == "hard_stop"
    assert result["secondary_tasks"] == []
    assert result["closing_decision"]["action"] == "complete"
    assert result["closing_decision"]["node_key"] == ""
    assert result["closing_decision"]["trigger"] == "none"
    assert result["closing_decision"]["customer_state"] == "hard_stop"
    assert result["closing_decision"]["pressure"] == "none"
    assert "explicit_exit_requires_hard_stop" in result["decision_reasons"]
    assert "explicit_exit_requires_complete" in result["decision_reasons"]


def test_explicit_exit_rejects_same_turn_sales_structures() -> None:
    payload = {
        "reply_messages": [
            {"type": "text", "order": 1, "content": "那您先付一下预约金"},
            {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
        ],
        "action": "payment",
        "sales_judgment": {"posture": "advance"},
        "commit_actions": [{"type": "registration"}],
        "policy_decision": {
            **_valid_decision(),
            "realtime_intent": {
                "type": "explicit_exit",
                "secondary_types": [],
                "confidence": "high",
                "evidence_refs": ["current_message"],
                "basis": ["客户明确要求停止联系"],
            },
        },
    }

    with pytest.raises(ValueError, match="policy_decision_explicit_exit_conflict"):
        _validate_policy_reply_consistency(payload, _state())


def test_pause_marketing_emotion_rejects_same_turn_advance() -> None:
    decision = _valid_decision()
    decision["emotion_decision"] = {
        "label": "angry",
        "confidence": "high",
        "pressure": "none",
        "evidence_refs": ["current_message"],
        "basis": ["客户明显愤怒"],
    }
    payload = {
        "reply_messages": [{"type": "text", "order": 1, "content": "您先预约吧"}],
        "action": "offer",
        "sales_judgment": {"posture": "switch"},
        "commit_actions": [],
        "policy_decision": decision,
    }

    with pytest.raises(ValueError, match="policy_decision_pause_marketing_conflict"):
        _validate_policy_reply_consistency(payload, _state())


def test_policy_safety_recovery_removes_all_sales_actions() -> None:
    raw = {
        "reply_messages": [{"type": "payment_collection", "content": {"amount": 10}}],
        "action": "payment",
        "commit_actions": [{"type": "registration"}],
        "policy_decision": _valid_decision(),
    }
    recovered = _policy_safety_failure_recovery(
        {
            "raw_json_output": raw,
            "primary_error": "policy_decision_explicit_exit_conflict:reply_action",
        }
    )

    assert recovered is not None
    messages, payload = recovered
    assert messages == [{"type": "text", "order": 1, "content": "好的，知道了，之后不再打扰您。"}]
    assert payload["action"] == "none"
    assert payload["commit_actions"] == []
    assert payload["selected_content_ids"] == []


def test_active_cardpoint_pauses_closing_but_resolved_cardpoint_does_not() -> None:
    active = _valid_decision()
    active["closing_decision"].update(
        {
            "action": "advance",
            "sequence_key": "price_hesitation",
            "node_key": "value_reframe",
            "customer_state": "engaged",
            "pressure": "normal",
        }
    )
    active["cardpoint_decision"] = {
        "category_key": "price",
        "state": "active",
        "confidence": "high",
    }
    resolved = json.loads(json.dumps(active, ensure_ascii=False))
    resolved["cardpoint_decision"]["state"] = "resolved"

    active_result = _normalized_policy_decision(active, state=_state())
    resolved_result = _normalized_policy_decision(resolved, state=_state())

    assert active_result["closing_decision"]["action"] == "pause"
    assert active_result["closing_decision"]["pressure"] == "low"
    assert "active_cardpoint_requires_pause" in active_result["decision_reasons"]
    assert resolved_result["closing_decision"]["action"] == "advance"
    assert resolved_result["cardpoint_decision"]["state"] == "resolved"


def test_repair_cannot_remove_grounded_explicit_exit() -> None:
    primary = _valid_decision()
    primary["realtime_intent"] = {
        "type": "explicit_exit",
        "secondary_types": [],
        "confidence": "high",
        "evidence_refs": ["current_message"],
        "basis": ["客户要求停止联系"],
    }
    repaired = _valid_decision()
    floor = _policy_safety_floor({"policy_decision": primary}, _state())

    assert floor == "explicit_exit"
    with pytest.raises(ValueError, match="policy_safety_floor_removed:explicit_exit"):
        _validate_policy_safety_floor(
            {"policy_decision": repaired},
            _state(),
            floor,
        )


def test_reply_pipeline_repairs_conflicting_explicit_exit_once() -> None:
    primary_decision = _valid_decision()
    primary_decision["realtime_intent"] = {
        "type": "explicit_exit",
        "secondary_types": [],
        "confidence": "high",
        "evidence_refs": ["current_message"],
        "basis": ["客户要求停止联系"],
    }
    primary = {
        "reply_messages": [{"type": "text", "order": 1, "content": "那再了解一下吧"}],
        "action": "offer",
        "sales_judgment": {"posture": "advance"},
        "policy_decision": primary_decision,
    }
    repaired = json.loads(json.dumps(primary, ensure_ascii=False))
    repaired.update(
        {
            "reply_messages": [{"type": "text", "order": 1, "content": "好的，之后不再打扰。"}],
            "action": "none",
            "sales_judgment": {"posture": "close"},
        }
    )

    class Model:
        settings = None

        def __init__(self) -> None:
            self.outputs = [primary, repaired]
            self.calls = 0

        async def chat_json(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            output = self.outputs[self.calls]
            self.calls += 1
            return json.loads(json.dumps(output, ensure_ascii=False))

    state = {**_state(), "evidence_join": {"content_candidates": []}}
    model = Model()
    messages, model_call, source = asyncio.run(
        _run_reply_model_pipeline(
            state=state,
            model_client=model,  # type: ignore[arg-type]
            model_messages=[{"role": "user", "content": "不要联系了"}],
            validated_model_messages=lambda payload, _state: payload["reply_messages"],
            debug_message_contents=lambda messages: [str(item.get("content")) for item in messages],
            warnings=[],
        )
    )

    assert model.calls == 2
    assert source == "single_targeted_repair_model"
    assert messages[0]["content"] == "好的，之后不再打扰。"
    assert "policy_decision_explicit_exit_conflict" in model_call["primary_error"]


def test_reply_pipeline_repair_cannot_downgrade_explicit_exit() -> None:
    primary_decision = _valid_decision()
    primary_decision["realtime_intent"] = {
        "type": "explicit_exit",
        "secondary_types": [],
        "confidence": "high",
        "evidence_refs": ["current_message"],
        "basis": ["客户要求停止联系"],
    }
    primary = {
        "reply_messages": [{"type": "text", "order": 1, "content": "那再了解一下吧"}],
        "action": "offer",
        "sales_judgment": {"posture": "advance"},
        "policy_decision": primary_decision,
    }
    downgraded = {
        "reply_messages": [{"type": "text", "order": 1, "content": "好的"}],
        "action": "none",
        "sales_judgment": {"posture": "answer"},
        "policy_decision": _valid_decision(),
    }

    class Model:
        settings = None

        def __init__(self) -> None:
            self.outputs = [primary, downgraded]
            self.calls = 0

        async def chat_json(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            output = self.outputs[self.calls]
            self.calls += 1
            return json.loads(json.dumps(output, ensure_ascii=False))

    state = {**_state(), "evidence_join": {"content_candidates": []}}
    model = Model()
    with pytest.raises(ReplyModelPipelineError) as caught:
        asyncio.run(
            _run_reply_model_pipeline(
                state=state,
                model_client=model,  # type: ignore[arg-type]
                model_messages=[{"role": "user", "content": "不要联系了"}],
                validated_model_messages=lambda payload, _state: payload["reply_messages"],
                debug_message_contents=lambda messages: [str(item.get("content")) for item in messages],
                warnings=[],
            )
        )

    recovered = _policy_safety_failure_recovery(caught.value.model_call)
    assert model.calls == 2
    assert recovered is not None
    messages, safe_payload = recovered
    assert messages[0]["content"] == "好的，知道了，之后不再打扰您。"
    assert _policy_safety_floor(safe_payload, state) == "explicit_exit"


def test_broad_store_scope_validation_uses_current_resolution_ids() -> None:
    regions = _requested_store_scope_regions(
        {
            "fact_envelope": {
                "structured_facts": {
                    "store_resolution_fact": {
                        "status": "send_multiple",
                        "allow_broad_scope_delivery": True,
                        "delivery_store_ids": ["1", "2", "3", "4"],
                    }
                }
            }
        }
    )

    assert {"1", "2", "3", "4"} in regions


def test_explicit_exit_persists_scoped_stop_contact_without_storing_message() -> None:
    calls: list[dict[str, Any]] = []

    class MemoryStore:
        def record_stop_contact(self, customer_id: str, **kwargs: Any) -> dict[str, Any]:
            calls.append({"customer_id": customer_id, **kwargs})
            return {"status": "recorded", "event_id": "stop_contact_request-1"}

    state: dict[str, Any] = {
        "request_id": "request-1",
        "realtime_intent": {
            "type": "explicit_exit",
            "evidence_refs": ["current_message"],
        },
    }

    _record_stop_contact_fact(MemoryStore(), state, customer_id="sales_contact:v2:digest")

    assert calls == [
        {
            "customer_id": "sales_contact:v2:digest",
            "request_id": "request-1",
            "evidence_refs": ["current_message"],
            "reason": "explicit_exit",
        }
    ]
    assert state["stop_contact_memory_record"]["status"] == "recorded"


def test_lower_pressure_emotion_also_limits_closing_pressure() -> None:
    raw = _valid_decision()
    raw["emotion_decision"].update(
        {"label": "hesitant", "pressure": "normal", "confidence": "high"}
    )
    raw["closing_decision"].update(
        {
            "action": "enter",
            "sequence_key": "gentle_invite",
            "node_key": "confirm_visit",
            "customer_state": "hesitant",
            "pressure": "normal",
        }
    )

    result = _normalized_policy_decision(raw, state=_state())

    assert result["emotion_decision"]["pressure"] == "low"
    assert result["closing_decision"]["action"] == "enter"
    assert result["closing_decision"]["pressure"] == "low"
    assert "emotion_requires_lower_closing_pressure" in result["decision_reasons"]


def test_explicit_exit_without_valid_customer_evidence_is_not_persisted() -> None:
    class MemoryStore:
        def record_stop_contact(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("must not persist an ungrounded stop-contact decision")

    state: dict[str, Any] = {
        "request_id": "request-1",
        "realtime_intent": {"type": "explicit_exit", "evidence_refs": []},
    }

    _record_stop_contact_fact(MemoryStore(), state, customer_id="sales_contact:v2:digest")

    assert state["stop_contact_memory_record"]["status"] == "skipped"
    assert state["stop_contact_memory_record"]["reason"] == "missing_valid_customer_evidence"


def test_invalid_closing_sequence_or_node_cannot_advance() -> None:
    invalid_sequence = _valid_decision()
    invalid_sequence["closing_decision"].update(
        {
            "action": "pause",
            "sequence_key": "unknown_sequence",
            "node_key": "unknown_node",
        }
    )
    invalid_node = _valid_decision()
    invalid_node["closing_decision"].update(
        {
            "action": "advance",
            "sequence_key": "gentle_invite",
            "node_key": "unknown_node",
        }
    )

    sequence_result = _normalized_policy_decision(invalid_sequence, state=_state())
    node_result = _normalized_policy_decision(invalid_node, state=_state())

    assert sequence_result["closing_decision"]["sequence_key"] == "none"
    assert sequence_result["decision_status"] == "degraded"
    assert node_result["closing_decision"]["action"] == "pause"
    assert node_result["closing_decision"]["node_key"] == ""
    assert "closing_advance_requires_valid_node" in node_result["decision_reasons"]


def test_shadow_closing_is_cancelled_by_authoritative_terminal_facts() -> None:
    assert _closing_shadow_terminal_reason(
        {"shared_context": {"authoritative_facts": {"orders_and_payment": {
            "resolved_payment": {"deposit_state": "paid_by_order"}
        }}}}
    ) == "closing_shadow_payment_terminal"
    assert _closing_shadow_terminal_reason(
        {"tool_results": {"customer_order_context": {"order_state": "scheduled"}}}
    ) == "closing_shadow_transaction_terminal"
    assert _closing_shadow_terminal_reason(
        {"takeover_guard": {"decision": "return_empty", "mode": "human"}}
    ) == "closing_shadow_human_takeover"


def test_shadow_task_executor_never_calls_real_send_adapter() -> None:
    class Repository:
        def __init__(self) -> None:
            self.task_status = "pending"

        def get_outreach_task(self, _task_id: str) -> dict[str, Any]:
            return {
                "id": "task-1",
                "plan_id": "plan-1",
                "customer_id": "customer-1",
                "corp_id": "corp-1",
                "user_id": "staff-1",
                "wechat": "sales-a",
                "external_userid": "external-1",
                "reply_messages": [],
                "before_send_check": False,
            }

        def get_outreach_plan(self, _plan_id: str) -> dict[str, Any]:
            return {
                "plan": {
                    "id": "plan-1",
                    "corp_id": "corp-1",
                    "user_id": "staff-1",
                    "wechat": "sales-a",
                    "external_userid": "external-1",
                    "source_snapshot": {
                        "plan_type": "closing_sequence",
                        "runtime_mode": "shadow",
                    },
                }
            }

        def claim_outreach_task(self, _task_id: str) -> bool:
            return True

        def has_stop_contact(self, _customer_id: str) -> bool:
            return False

        def update_outreach_task(self, _task_id: str, **kwargs: Any) -> dict[str, Any]:
            self.task_status = str(kwargs.get("status") or self.task_status)
            return self.get_outreach_task(_task_id)

        def add_outreach_event(self, **_kwargs: Any) -> None:
            return None

        def outreach_plan_has_remaining_tasks(self, _plan_id: str) -> bool:
            return False

        def update_outreach_plan_status(self, _plan_id: str, _status: str) -> None:
            return None

    class SystemClient:
        supports_conversation_id_send = True

        def __init__(self) -> None:
            self.send_count = 0

        async def send(self, **_kwargs: Any) -> dict[str, Any]:
            self.send_count += 1
            return {"ok": True}

    class Message:
        async def _generate_task_messages(self, **_kwargs: Any) -> list[dict[str, Any]]:
            return [{"type": "text", "order": 1, "content": "shadow preview"}]

    repository = Repository()
    system_client = SystemClient()
    executor = TaskExecutor(
        repository=repository,
        system_client=system_client,
        customer_context_service=object(),
        before_send_retry_seconds=60,
        first_day_wechat_allowlist="",
        planning=object(),
        first_day=object(),
        message=Message(),
    )

    result = asyncio.run(executor.execute("task-1"))

    assert result["status"] == "shadowed"
    assert result["sent"] is False
    assert repository.task_status == "shadowed"
    assert system_client.send_count == 0
