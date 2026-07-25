from __future__ import annotations

import asyncio
import json
import time
import unittest
from datetime import date, timedelta
from typing import Any

from app.config import Settings
from app.graph.nodes.image_info import fallback_image_info
from app.graph.planner.brain_v2 import _planner_payload_for_model, run_planner_brain_v2
from app.services.model_client import ModelClient
from app.services.model_selection import api_key, base_url, is_claude_model, model_names
from app.services.runtime_budget import (
    build_runtime_budget,
    can_start_model_retry,
    graph_deadline_monotonic,
    model_deadline_monotonic,
    runtime_budget_snapshot,
)


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


class ModelTimeoutAndPlannerPayloadTests(unittest.IsolatedAsyncioTestCase):
    def test_json_marker_is_injected_when_prompt_only_contains_uppercase_json(self) -> None:
        messages = [{"role": "user", "content": "Return valid JSON only."}]

        normalized = ModelClient._ensure_json_marker(messages)

        self.assertEqual(normalized[0], {"role": "system", "content": "Return valid json only."})
        self.assertEqual(normalized[1:], messages)

    def test_json_marker_is_forced_to_first_system_message_even_when_lowercase_literal_is_present(self) -> None:
        messages = [{"role": "user", "content": "Return valid json only."}]

        normalized = ModelClient._ensure_json_marker(messages)

        self.assertEqual(normalized[0], {"role": "system", "content": "Return valid json only."})
        self.assertEqual(normalized[1:], messages)

    def test_json_marker_is_not_duplicated_when_already_in_first_system_message(self) -> None:
        messages = [{"role": "system", "content": "Return valid json only.\n\nBusiness contract"}]

        normalized = ModelClient._ensure_json_marker(messages)

        self.assertIs(normalized, messages)

    def test_json_messages_merge_adjacent_system_blocks_for_relay_compatibility(self) -> None:
        messages = [
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "system", "content": "Business contract"},
            {"role": "user", "content": "hello"},
            {"role": "system", "content": "Final gate"},
        ]

        normalized = ModelClient._prepare_json_messages(messages)

        self.assertEqual(len(normalized), 3)
        self.assertEqual(normalized[0]["role"], "system")
        self.assertIn("Return valid json only.", normalized[0]["content"])
        self.assertIn("Business contract", normalized[0]["content"])
        self.assertEqual(normalized[1], {"role": "user", "content": "hello"})
        self.assertEqual(normalized[2], {"role": "system", "content": "Final gate"})

    def test_round_budget_shadow_records_without_blocking_retry(self) -> None:
        now = time.monotonic()
        budget = build_runtime_budget(
            _settings(
                model_round_budget_enforced=False,
                model_round_timeout_seconds=20,
                model_reply_reserve_seconds=15,
                model_min_retry_remaining_seconds=8,
            ),
            started_monotonic=now,
        )
        state = {"runtime_budget": budget}

        snapshot = runtime_budget_snapshot(
            state,
            tier="planner",
            reserve_reply=True,
            now_monotonic=now,
        )

        self.assertEqual(snapshot["mode"], "shadow")
        self.assertTrue(snapshot["would_skip_retry"])
        self.assertTrue(can_start_model_retry(state, tier="planner", reserve_reply=True, now_monotonic=now))
        self.assertIsNone(model_deadline_monotonic(state, tier="planner", reserve_reply=True))

    def test_round_budget_enforced_reserves_reply_time_for_planner(self) -> None:
        now = time.monotonic()
        budget = build_runtime_budget(
            _settings(
                model_round_budget_enforced=True,
                model_round_timeout_seconds=60,
                model_reply_reserve_seconds=15,
            ),
            started_monotonic=now,
        )
        state = {"runtime_budget": budget}

        planner_deadline = model_deadline_monotonic(state, tier="planner", reserve_reply=True)
        reply_deadline = model_deadline_monotonic(state, tier="reply")

        self.assertAlmostEqual(float(planner_deadline or 0), now + 45, places=3)
        self.assertAlmostEqual(float(reply_deadline or 0), now + 60, places=3)

    def test_round_budget_uses_strong_limit_only_for_strong_reply(self) -> None:
        now = time.monotonic()
        state = {
            "runtime_budget": build_runtime_budget(
                _settings(
                    model_round_budget_enforced=True,
                    model_round_timeout_seconds=60,
                    model_strong_round_timeout_seconds=75,
                ),
                started_monotonic=now,
            )
        }

        ordinary_deadline = graph_deadline_monotonic(state, phase="full", strong_reply=False)
        strong_deadline = graph_deadline_monotonic(state, phase="full", strong_reply=True)

        self.assertAlmostEqual(float(ordinary_deadline or 0), now + 60, places=3)
        self.assertAlmostEqual(float(strong_deadline or 0), now + 75, places=3)

    async def test_model_client_uses_five_second_connect_timeout(self) -> None:
        client = ModelClient(_settings(model_timeout_seconds=45))

        http_client = client._http_client()

        self.assertEqual(http_client.timeout.connect, 5)
        self.assertEqual(http_client.timeout.read, 45)
        await client.aclose()

    def test_planner_retries_primary_once_then_falls_back_to_qwen_turbo(self) -> None:
        settings = _settings(model_planner="qwen-plus", model_planner_fallbacks="")

        self.assertEqual(model_names(settings, "planner"), ["qwen-plus", "qwen-turbo"])

    def test_planner_keeps_qwen_turbo_after_custom_fallbacks(self) -> None:
        settings = _settings(model_planner="qwen-plus", model_planner_fallbacks="custom-model")

        self.assertEqual(model_names(settings, "planner"), ["qwen-plus", "custom-model", "qwen-turbo"])

    def test_relay_planner_does_not_append_qwen_turbo(self) -> None:
        settings = _settings(
            model_provider="relay",
            model_planner="gpt-5.4-mini",
            model_planner_fallbacks="gpt-5.4",
        )

        self.assertEqual(model_names(settings, "planner"), ["gpt-5.4-mini", "gpt-5.4"])

    def test_reply_dedupes_repeated_fallbacks_without_turbo(self) -> None:
        settings = _settings(model_reply="qwen-plus", model_reply_fallbacks="qwen-plus,qwen-plus")

        self.assertEqual(model_names(settings, "reply"), ["qwen-plus"])

    def test_planner_uses_a_longer_quality_first_hedge_delay(self) -> None:
        client = ModelClient(_settings(model_hedge_delay_seconds=0.01, model_planner_hedge_delay_seconds=0.02))

        self.assertEqual(client._hedge_delay_for_tier("planner"), 0.02)
        self.assertEqual(client._hedge_delay_for_tier("reply"), 0.01)

    async def test_model_client_hedges_slow_primary_with_fallback(self) -> None:
        class HedgeModelClient(ModelClient):
            def __init__(self, settings: Settings) -> None:
                super().__init__(settings)
                self.models: list[str] = []

            async def _post_chat(
                self,
                payload: dict[str, Any],
                *,
                tier: str,
                fallback_index: int,
                errors: list[str],
            ) -> dict[str, Any]:
                self.models.append(str(payload.get("model") or ""))
                if fallback_index == 0:
                    await asyncio.sleep(0.2)
                    return {"choices": [{"message": {"content": "slow"}}]}
                return {"choices": [{"message": {"content": "fast"}}]}

        client = HedgeModelClient(
            _settings(
                model_provider="relay",
                model_relay_api_key="relay-key",
                model_fast="slow-model",
                model_fast_fallbacks="fast-model",
                model_hedge_delay_seconds=0.01,
                model_timeout_seconds=1,
            )
        )

        result = await client.chat_text([{"role": "user", "content": "hi"}], tier="fast")

        self.assertEqual(result, "fast")
        self.assertEqual(client.models[:2], ["slow-model", "fast-model"])
        usage = client.last_usage or {}
        self.assertEqual(usage.get("primary_model"), "slow-model")
        self.assertEqual(usage.get("hedge_model"), "fast-model")
        self.assertEqual(usage.get("winner_model"), "fast-model")
        self.assertIn("slow-model", usage.get("cancelled_models") or [])

    async def test_model_client_cancellation_cleans_up_hedged_candidates(self) -> None:
        class CancelledModelClient(ModelClient):
            def __init__(self, settings: Settings) -> None:
                super().__init__(settings)
                self.active_candidates = 0

            async def _post_chat(
                self,
                payload: dict[str, Any],
                *,
                tier: str,
                fallback_index: int,
                errors: list[str],
            ) -> dict[str, Any]:
                self.active_candidates += 1
                try:
                    await asyncio.sleep(10)
                    return {"choices": [{"message": {"content": "late"}}]}
                finally:
                    self.active_candidates -= 1

        client = CancelledModelClient(
            _settings(
                model_provider="relay",
                model_relay_api_key="relay-key",
                model_fast="slow-primary",
                model_fast_fallbacks="slow-fallback",
                model_hedge_delay_seconds=0.01,
                model_timeout_seconds=30,
            )
        )
        task = asyncio.create_task(client.chat_text([{"role": "user", "content": "hi"}], tier="fast"))
        await asyncio.sleep(0.03)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

        self.assertEqual(client.active_candidates, 0)

    async def test_model_client_records_timeout_candidate_and_hedge_metadata(self) -> None:
        class TimeoutModelClient(ModelClient):
            async def _post_chat(
                self,
                payload: dict[str, Any],
                *,
                tier: str,
                fallback_index: int,
                errors: list[str],
            ) -> dict[str, Any]:
                await asyncio.sleep(2)
                return {"choices": [{"message": {"content": "{\"ok\": true}"}}]}

        client = TimeoutModelClient(
            _settings(
                model_provider="relay",
                model_relay_api_key="relay-key",
                model_fast="slow-primary",
                model_fast_fallbacks="slow-fallback",
                model_hedge_delay_seconds=0.01,
                model_timeout_seconds=1,
            )
        )

        with self.assertRaises(TimeoutError):
            await client.chat_json([{"role": "user", "content": "Return JSON."}], tier="fast")

        usage = client.last_usage or {}
        self.assertEqual(usage.get("candidate_models"), ["slow-primary", "slow-fallback"])
        self.assertEqual(usage.get("started_models"), ["slow-primary", "slow-fallback"])
        self.assertEqual(usage.get("pending_models"), ["slow-primary", "slow-fallback"])
        self.assertTrue(usage.get("hedge_started"))
        self.assertEqual(usage.get("configured_total_timeout_seconds"), 1.0)
        self.assertGreater(float(usage.get("total_timeout_seconds") or 0), 0)
        self.assertLessEqual(float(usage.get("total_timeout_seconds") or 0), 1.0)
        self.assertEqual(set(usage.get("cancelled_models") or []), {"slow-primary", "slow-fallback"})
        self.assertIn("TimeoutError", str(usage.get("error")))

    async def test_model_usage_is_isolated_between_concurrent_business_requests(self) -> None:
        class ConcurrentUsageClient(ModelClient):
            async def _post_chat(
                self,
                payload: dict[str, Any],
                *,
                tier: str,
                fallback_index: int,
                errors: list[str],
            ) -> dict[str, Any]:
                model = str(payload.get("model") or "")
                await asyncio.sleep(0.02 if model == "fast-model" else 0.01)
                self.last_usage = {"model": model, "tier": tier, "usage": {}}
                return {"choices": [{"message": {"content": model}}]}

        client = ConcurrentUsageClient(
            _settings(
                model_provider="relay",
                model_relay_api_key="relay-key",
                model_fast="fast-model",
                model_fast_fallbacks="",
                model_balanced="balanced-model",
                model_balanced_fallbacks="",
                model_hedge_max_parallel=1,
            )
        )

        async def run(tier: str) -> tuple[str, str]:
            result = await client.chat_text([{"role": "user", "content": tier}], tier=tier)  # type: ignore[arg-type]
            await asyncio.sleep(0.03)
            return result, str((client.last_usage or {}).get("winner_model") or "")

        fast, balanced = await asyncio.gather(run("fast"), run("balanced"))

        self.assertEqual(fast, ("fast-model", "fast-model"))
        self.assertEqual(balanced, ("balanced-model", "balanced-model"))

    async def test_transient_retries_share_one_absolute_deadline(self) -> None:
        class InvalidJsonClient(ModelClient):
            def __init__(self, settings: Settings) -> None:
                super().__init__(settings)
                self.calls = 0

            async def _post_chat(
                self,
                payload: dict[str, Any],
                *,
                tier: str,
                fallback_index: int,
                errors: list[str],
            ) -> dict[str, Any]:
                self.calls += 1
                await asyncio.sleep(0.08)
                return {"choices": [{"message": {"content": "not-json"}}]}

        client = InvalidJsonClient(
            _settings(
                model_provider="relay",
                model_relay_api_key="relay-key",
                model_fast="json-model",
                model_fast_fallbacks="",
                model_hedge_max_parallel=1,
                model_request_retry_attempts=3,
                model_request_retry_delay_seconds=0,
                model_timeout_seconds=1,
            )
        )
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            await client.chat_json(
                [{"role": "user", "content": "Return JSON."}],
                tier="fast",
                deadline_monotonic=started + 0.13,
            )

        self.assertLess(time.monotonic() - started, 0.23)
        self.assertEqual(client.calls, 2)
        usage = client.last_usage or {}
        self.assertEqual(usage.get("attempts"), 2)
        self.assertEqual(usage.get("remaining_budget_ms"), 0)

    async def test_model_client_retries_transient_invalid_json_once(self) -> None:
        class RetryJsonClient(ModelClient):
            def __init__(self, settings: Settings) -> None:
                super().__init__(settings)
                self.calls = 0

            async def _post_chat(
                self,
                payload: dict[str, Any],
                *,
                tier: str,
                fallback_index: int,
                errors: list[str],
            ) -> dict[str, Any]:
                self.calls += 1
                content = "not-json" if self.calls == 1 else '{"ok": true}'
                return {"choices": [{"message": {"content": content}}]}

        client = RetryJsonClient(
            _settings(
                model_provider="relay",
                model_relay_api_key="relay-key",
                model_fast="json-model",
                model_fast_fallbacks="",
                model_hedge_max_parallel=1,
                model_request_retry_attempts=2,
                model_request_retry_delay_seconds=0,
            )
        )

        result = await client.chat_json([{"role": "user", "content": "Return JSON."}], tier="fast")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(client.calls, 2)
        usage = client.last_usage or {}
        self.assertEqual(usage.get("request_attempt"), 2)
        self.assertEqual(len(usage.get("request_retry_errors") or []), 1)

    async def test_planner_timeout_uses_compact_fast_retry(self) -> None:
        class RetryPlannerClient:
            available = True

            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []
                self.last_usage: dict[str, Any] = {}

            async def chat_json(self, messages: list[dict[str, Any]], *, tier: str, temperature: float = 0.1) -> dict[str, Any]:
                self.calls.append({"tier": tier, "messages": messages, "temperature": temperature})
                self.last_usage = {
                    "provider": "test",
                    "model": f"model-{tier}",
                    "tier": tier,
                    "candidate_models": [f"model-{tier}"],
                    "started_models": [f"model-{tier}"],
                    "hedge_started": False,
                    "usage": {},
                }
                if len(self.calls) == 1:
                    self.last_usage["error"] = "TimeoutError: total timeout 35.0s"
                    raise TimeoutError("total timeout 35.0s")
                return {
                    "decision": "direct_reply",
                    "stage": "S1",
                    "sub_rule_id": "S1_CONTEXT",
                    "conversion_stage": "interest_capture",
                    "customer_type": "unknown",
                    "main_blocker": "none",
                    "next_step": "no_action",
                    "payment_action": "none",
                    "payment_decision": {"action": "none", "source": "planner"},
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "在的，我继续帮您处理。"}}],
                    "tool_calls": [],
                }

        client = RetryPlannerClient()
        plan, model_call = await run_planner_brain_v2(
            {"normalized_content": "？", "conversation_history": ["用户: 还在吗"]},
            client,  # type: ignore[arg-type]
        )

        self.assertEqual([call["tier"] for call in client.calls], ["planner", "fast"])
        retry_user_payload = client.calls[1]["messages"][-1]["content"]
        self.assertIn("timeout_recovery", retry_user_payload)
        self.assertEqual(len(client.calls[1]["messages"]), 3)
        self.assertIn("Planner Timeout Recovery", client.calls[1]["messages"][0]["content"])
        self.assertIn("Current Recovery Business Rules", client.calls[1]["messages"][1]["content"])
        retry_messages = json.dumps(client.calls[1]["messages"], ensure_ascii=False)
        self.assertIn("scene_index", retry_messages)
        self.assertNotIn("customer_psychology", retry_user_payload)
        self.assertNotIn("Planner Rule Packs", retry_messages)
        self.assertEqual(plan["planner_decision"], "direct_reply")
        self.assertEqual([item["type"] for item in plan["planner_reply_messages"]], ["text"])
        self.assertEqual(model_call["nested_calls"][0]["name"], "planner_brain_timeout_retry")
        self.assertIn("TimeoutError", model_call.get("initial_error", ""))

    async def test_planner_normalizer_accepts_exact_snapshot_store_from_shared_evidence(self) -> None:
        target_date = (date.today() + timedelta(days=1)).isoformat()

        class StorePlannerClient:
            available = True

            def __init__(self) -> None:
                self.calls = 0
                self.last_usage: dict[str, Any] = {}

            async def chat_json(
                self,
                messages: list[dict[str, Any]],
                *,
                tier: str,
                temperature: float = 0.1,
            ) -> dict[str, Any]:
                self.calls += 1
                return {
                    "decision": "need_tools",
                    "stage": "S3",
                    "sub_rule_id": "S3_APPOINTMENT_TIME",
                    "conversion_stage": "time_confirm",
                    "customer_type": "time",
                    "main_blocker": "time",
                    "next_step": "confirm_time",
                    "payment_action": "none",
                    "payment_decision": {"action": "none", "source": "none"},
                    "appointment_decision": {"action": "check_availability", "commitment_level": "tentative"},
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "我看下明天下午的时间。"}}],
                    "tool_calls": [{"name": "available_time", "store_id": "12", "date": target_date}],
                }

        client = StorePlannerClient()
        plan, _ = await run_planner_brain_v2(
            {
                "normalized_content": "明天下午三点可以吗？",
                "conversation_history": [
                    "用户: 我想去厦门思明店。",
                    "小贝: 好的，厦门思明店可以继续看时间。",
                ],
            },
            client,  # type: ignore[arg-type]
        )

        self.assertEqual(client.calls, 1)
        self.assertFalse(plan["tool_policy_violations"])
        self.assertEqual(plan["planner_tool_calls"][0]["store_id"], "12")
        self.assertEqual(plan["current_known_store"]["store_id"], "12")

    async def test_planner_timeout_fallback_does_not_emit_handoff_notice(self) -> None:
        class FailingPlannerClient:
            available = True

            def __init__(self) -> None:
                self.calls = 0
                self.last_usage: dict[str, Any] = {}

            async def chat_json(self, messages: list[dict[str, Any]], *, tier: str, temperature: float = 0.1) -> dict[str, Any]:
                self.calls += 1
                self.last_usage = {
                    "provider": "test",
                    "model": f"model-{tier}",
                    "tier": tier,
                    "candidate_models": [f"model-{tier}"],
                    "started_models": [f"model-{tier}"],
                    "hedge_started": False,
                    "error": f"TimeoutError: {tier}",
                    "usage": {},
                }
                raise TimeoutError(f"{tier} timeout")

        client = FailingPlannerClient()
        plan, model_call = await run_planner_brain_v2(
            {"normalized_content": "？", "conversation_history": ["用户: 还在吗"]},
            client,  # type: ignore[arg-type]
        )

        self.assertEqual(client.calls, 2)
        self.assertEqual(plan["planner_sub_rule_id"], "PLANNER_SYSTEM_UNAVAILABLE")
        self.assertEqual([item["type"] for item in plan["planner_reply_messages"]], ["text"])
        self.assertNotIn("human_handoff_notice", [item["type"] for item in plan["planner_reply_messages"]])
        self.assertIn("timeout_retry_failed", model_call.get("error", ""))

    def test_relay_provider_uses_relay_credentials(self) -> None:
        settings = _settings(
            model_provider="relay",
            model_relay_api_key="relay-key",
            model_relay_base_url="https://relay.example.com/v1",
            aliyun_dashscope_api_key="aliyun-key",
        )

        self.assertEqual(api_key(settings), "relay-key")
        self.assertEqual(base_url(settings), "https://relay.example.com/v1")

    def test_relay_provider_accepts_anthropic_env_aliases(self) -> None:
        settings = _settings(
            model_provider="relay",
            anthropic_auth_token="anthropic-style-key",
            anthropic_base_url="https://linkai.shop/v1",
        )

        self.assertEqual(api_key(settings), "anthropic-style-key")
        self.assertEqual(base_url(settings), "https://linkai.shop/v1")

    def test_relay_provider_accepts_claude_test_key_alias(self) -> None:
        settings = _settings(
            model_provider="relay",
            claude_relay_api_key="claude-test-key",
            anthropic_auth_token="anthropic-style-key",
            anthropic_base_url="https://linkai.shop/v1",
        )

        self.assertEqual(api_key(settings), "claude-test-key")

    def test_relay_provider_uses_model_specific_key_for_claude_candidates(self) -> None:
        settings = _settings(
            model_provider="relay",
            model_relay_api_key="gpt-key",
            claude_relay_api_key="claude-key",
            anthropic_auth_token="anthropic-style-key",
        )

        self.assertEqual(api_key(settings, model="gpt-5.4-mini"), "gpt-key")
        self.assertEqual(api_key(settings, model="claude-haiku-4-5-20251001"), "claude-key")
        self.assertEqual(api_key(settings, model="anthropic/claude-opus-4-7"), "claude-key")

    def test_claude_model_detection_accepts_gateway_ids(self) -> None:
        self.assertTrue(is_claude_model("claude-haiku-4-5-20251001"))
        self.assertTrue(is_claude_model("anthropic/claude-opus-4-7"))
        self.assertFalse(is_claude_model("gpt-5.4-mini"))

    def test_relay_auto_uses_anthropic_messages_when_only_anthropic_base_is_set(self) -> None:
        client = ModelClient(
            _settings(
                model_provider="relay",
                anthropic_auth_token="anthropic-style-key",
                anthropic_base_url="https://linkai.shop/v1",
                model_relay_protocol="auto",
            )
        )

        self.assertTrue(client._uses_anthropic_messages_api())
        self.assertEqual(client._anthropic_messages_url(), "https://linkai.shop/v1/messages")

    def test_anthropic_payload_moves_system_messages_to_system_field(self) -> None:
        client = ModelClient(_settings(model_provider="relay", anthropic_auth_token="key", anthropic_base_url="https://linkai.shop"))

        payload = client._anthropic_messages_payload(
            {
                "model": "anthropic/claude-haiku-4-5-20251001",
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": "Return JSON only."},
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi"},
                    {"role": "user", "content": [{"type": "text", "text": "next"}]},
                ],
            }
        )

        self.assertEqual(payload["system"], "Return JSON only.")
        self.assertEqual(payload["messages"], [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "next"},
        ])

    async def test_model_client_can_disable_response_format_for_relay_json(self) -> None:
        class CaptureModelClient(ModelClient):
            def __init__(self, settings: Settings) -> None:
                super().__init__(settings)
                self.payload: dict[str, Any] | None = None

            async def _post_chat(
                self,
                payload: dict[str, Any],
                *,
                tier: str,
                fallback_index: int,
                errors: list[str],
            ) -> dict[str, Any]:
                self.payload = payload
                return {"choices": [{"message": {"content": "{\"ok\": true}"}}]}

        client = CaptureModelClient(
            _settings(
                model_provider="relay",
                model_relay_api_key="relay-key",
                model_response_format_enabled=False,
                model_fast="openai/gpt-5.4-mini",
                model_fast_fallbacks="",
            )
        )

        result = await client.chat_json([{"role": "user", "content": "Return JSON."}], tier="fast")

        self.assertEqual(result, {"ok": True})
        self.assertIsNotNone(client.payload)
        self.assertNotIn("response_format", client.payload or {})
        self.assertEqual((client.payload or {}).get("reasoning"), {"enabled": False})
        self.assertEqual((client.payload or {}).get("max_tokens"), 2048)

    async def test_model_client_uses_json_mode_and_disables_reasoning_for_claude_model(self) -> None:
        class CaptureModelClient(ModelClient):
            def __init__(self, settings: Settings) -> None:
                super().__init__(settings)
                self.payload: dict[str, Any] | None = None

            async def _post_chat(
                self,
                payload: dict[str, Any],
                *,
                tier: str,
                fallback_index: int,
                errors: list[str],
            ) -> dict[str, Any]:
                self.payload = payload
                return {"choices": [{"message": {"content": "{\"ok\": true}"}}]}

        client = CaptureModelClient(
            _settings(
                model_provider="relay",
                model_relay_api_key="relay-key",
                claude_relay_api_key="claude-key",
                model_response_format_enabled=True,
                model_fast="claude-haiku-4-5-20251001",
                model_fast_fallbacks="",
            )
        )

        result = await client.chat_json([{"role": "user", "content": "Return JSON."}], tier="fast")

        self.assertEqual(result, {"ok": True})
        self.assertIsNotNone(client.payload)
        self.assertEqual((client.payload or {}).get("response_format"), {"type": "json_object"})
        self.assertEqual((client.payload or {}).get("reasoning"), {"enabled": False})

    async def test_model_client_can_enable_reasoning_for_text_relay(self) -> None:
        class CaptureModelClient(ModelClient):
            def __init__(self, settings: Settings) -> None:
                super().__init__(settings)
                self.payload: dict[str, Any] | None = None

            async def _post_chat(
                self,
                payload: dict[str, Any],
                *,
                tier: str,
                fallback_index: int,
                errors: list[str],
            ) -> dict[str, Any]:
                self.payload = payload
                return {"choices": [{"message": {"content": "ok"}}]}

        client = CaptureModelClient(
            _settings(
                model_provider="relay",
                model_relay_api_key="relay-key",
                model_reasoning_enabled=True,
                model_reasoning_effort="medium",
                model_reasoning_max_tokens=200,
                model_fast="gpt-5.4-mini",
                model_fast_fallbacks="",
            )
        )

        result = await client.chat_text([{"role": "user", "content": "hi"}], tier="fast")

        self.assertEqual(result, "ok")
        self.assertEqual((client.payload or {}).get("reasoning"), {"enabled": True, "effort": "medium", "max_tokens": 200})

    def test_planner_payload_drops_empty_optional_sections(self) -> None:
        payload = _planner_payload_for_model(
            {
                "normalized_content": "多少钱",
                "conversation_history": [],
                "image_info": {},
                "request_context": {"category_id": ""},
                "customer_profile": {},
                "history_events": [],
                "customer_context": {},
                "customer_store_knowledge": {},
                "sent_message_summary": {},
            }
        )

        self.assertEqual(payload["current_message"], "多少钱")
        self.assertNotIn("conversation_history", payload)
        self.assertNotIn("image_info", payload)
        self.assertNotIn("category_id", payload)
        self.assertNotIn("customer_profile", payload)
        self.assertNotIn("history_events", payload)
        self.assertNotIn("customer_context", payload)
        self.assertNotIn("store_scope_summary", payload)
        self.assertIn("available_tools", payload)

    def test_planner_payload_drops_no_image_fact_when_image_info_is_normalized(self) -> None:
        payload = _planner_payload_for_model(
            {
                "normalized_content": "你好",
                "image_info": fallback_image_info(has_image=False),
                "customer_store_knowledge": {},
            }
        )

        self.assertNotIn("image_info", payload)

    def test_planner_payload_keeps_loaded_empty_store_scope(self) -> None:
        payload = _planner_payload_for_model(
            {
                "normalized_content": "store?",
                "customer_store_knowledge": {
                    "source": "platform_scope",
                    "store_count": 0,
                    "stores": [],
                    "missing_snapshot_store_ids": [],
                },
            }
        )

        self.assertEqual(payload["store_scope_summary"], {"source": "platform_scope", "store_count": 0})


if __name__ == "__main__":
    unittest.main()
