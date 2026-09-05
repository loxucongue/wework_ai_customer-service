from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ai_paths.app.config import Settings
from ai_paths.app.graph.nodes.reply_contract import _conversation
from ai_paths.app.graph.nodes.reply_generation import _run_model_led_reply_pipeline
from ai_paths.app.schemas import ChatRequest
from ai_paths.app.services.model_selection import model_names
from ai_paths.app.services.platform_reply_coordinator import PlatformReplyCoordinator, _merged_content


def test_reply_tier_is_deepseek_only_even_with_global_gpt_emergency_models() -> None:
    settings = Settings(
        MODEL_PROVIDER="relay",
        MODEL_REPLY="deepseek-chat",
        MODEL_REPLY_FALLBACKS="",
        MODEL_EMERGENCY_FALLBACKS="gpt-5.4,gpt-5.4-mini",
    )

    assert model_names(settings, "reply") == ["deepseek-chat"]


def test_merged_content_contains_only_customer_words_in_order() -> None:
    assert _merged_content(["现在多少钱啊？", "有便宜吗"]) == "现在多少钱啊？\n有便宜吗"
    assert "客户连续发送" not in _merged_content(["现在多少钱啊？", "有便宜吗"])


def test_coordinator_supersedes_old_request_and_keeps_raw_merged_messages() -> None:
    asyncio.run(_run_coordinator_supersede_case())


async def _run_coordinator_supersede_case() -> None:
    coordinator = PlatformReplyCoordinator(Settings())
    first = ChatRequest(
        content="现在多少钱啊？",
        customer_id="customer",
        corp_id="corp",
        wechat="sl8003",
        external_userid="external",
    )
    second = first.model_copy(update={"content": "有便宜吗"})
    first_decision = await coordinator.begin(
        first,
        request_id="request-1",
        request_context={"msgid": "message-1"},
    )
    second_decision = await coordinator.begin(
        second,
        request_id="request-2",
        request_context={"msgid": "message-2"},
    )

    assert first_decision.record is not None and first_decision.record.cancel_event.is_set()
    assert second_decision.mode == "merged_latest"
    assert second_decision.effective_content == "现在多少钱啊？\n有便宜吗"
    assert second_decision.merged_customer_messages == ["现在多少钱啊？", "有便宜吗"]


def test_reply_history_keeps_twelve_visible_deduplicated_turns() -> None:
    turns = [
        {"message_ref": "draft", "role": "assistant", "content": "未发送草稿", "status": "superseded"},
        {"message_ref": "duplicate-1", "role": "customer", "content": "重复"},
        {"message_ref": "duplicate-2", "role": "customer", "content": "重复"},
        *[
            {"message_ref": f"visible-{index}", "role": "customer" if index % 2 else "assistant", "content": f"消息{index}"}
            for index in range(1, 15)
        ],
    ]
    result = _conversation(
        {
            "conversation_turns": turns,
            "normalized_content": "本轮消息",
        }
    )

    assert len(result) == 12
    assert all(item["message_ref"] != "draft" for item in result)
    assert [item["content"] for item in result].count("重复") == 0
    assert result[-1]["content"] == "消息14"


class _RetryingReplyClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.settings = SimpleNamespace(
            model_reply_primary_budget_seconds=30.0,
            model_reply_recovery_budget_seconds=15.0,
        )
        self.last_usage: dict[str, object] = {}

    async def chat_json(self, messages, *, tier, temperature=0.0, deadline_monotonic=None):
        self.calls.append(tier)
        if len(self.calls) == 1:
            raise TimeoutError("primary timeout")
        return {
            "sales_judgment": {
                "customer_friction_observation": "",
                "primary_objective": "自然回应",
                "posture": "answer",
            },
            "reply_messages": [{"type": "text", "content": "你好呀，在的～是想了解淡斑吗？"}],
        }


def test_full_reply_retry_stays_on_reply_tier() -> None:
    asyncio.run(_run_full_reply_retry_case())


async def _run_full_reply_retry_case() -> None:
    client = _RetryingReplyClient()
    state = {
        "evidence_join": {
            "schema_version": "reply_chain_evidence_join_v1",
            "shared_context": {"current_message": {"content": "你好"}, "conversation": []},
        },
        "request_context": {"interface_version": "v3"},
    }

    messages, model_call, source = await _run_model_led_reply_pipeline(
        state=state,
        model_client=client,
        model_messages=[{"role": "system", "content": "return json"}],
        validated_model_messages=lambda payload, _state: list(payload["reply_messages"]),
        debug_message_contents=lambda messages: [str(item.get("content") or "") for item in messages],
        warnings=[],
    )

    assert messages[0]["content"].startswith("你好呀")
    assert source == "single_full_task_retry_model"
    assert client.calls == ["reply", "reply"]
    assert model_call["retry"]["tier"] == "reply"
