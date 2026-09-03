from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.services.v3_strategy_outcome_service import PlatformOrderOutcomeProvider
from app.graph.nodes.material_selection import parallel_reply_payload


class _PlatformStub:
    available = True

    def __init__(self, rows: list[dict] | None = None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls = 0

    def list_orders(self, **_: object) -> list[dict]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.rows)


def _event(customer_id: str = "customer-1") -> dict:
    return {
        "customer_id": customer_id,
        "corp_id": "corp-1",
        "wechat": "wechat-1",
        "external_userid": "external-1",
        "user_id": "user-1",
        "occurred_at": "2026-09-03T00:00:00+00:00",
        "order_state_before": "pending",
    }


def test_platform_order_outcome_uses_highest_real_progress_and_batch_cache() -> None:
    client = _PlatformStub(
        [
            {"status": 1, "created_at": "2026-09-04T00:00:00+00:00"},
            {"status": 3, "created_at": "2026-09-04T00:00:00+00:00"},
            {"status": 2, "created_at": "2026-09-04T00:00:00+00:00"},
        ]
    )
    provider = PlatformOrderOutcomeProvider(client, enabled=True)  # type: ignore[arg-type]
    event = _event()
    event["order_state_before"] = "no_order"

    first = provider(event)
    second = provider(event)

    assert first == second
    assert first["status"] == "ok"
    assert first["order_state"] == "scheduled"
    assert first["source"] == "platform_agent.order_index"
    assert client.calls == 1


def test_platform_order_cache_does_not_cross_reception_wechat() -> None:
    client = _PlatformStub([{"status": 2}])
    provider = PlatformOrderOutcomeProvider(client, enabled=True)  # type: ignore[arg-type]

    provider(_event())
    other = _event()
    other["wechat"] = "wechat-2"
    provider(other)

    assert client.calls == 2


def test_platform_order_outcome_empty_is_verified_no_order() -> None:
    provider = PlatformOrderOutcomeProvider(_PlatformStub(), enabled=True)  # type: ignore[arg-type]

    result = provider(_event())

    assert result["status"] == "ok"
    assert result["order_state"] == "no_order"


def test_platform_order_outcome_failure_stays_unknown() -> None:
    provider = PlatformOrderOutcomeProvider(
        _PlatformStub(error=TimeoutError("late")),  # type: ignore[arg-type]
        enabled=True,
    )

    result = provider(_event())

    assert result["status"] == "error"
    assert result["order_state"] == ""
    assert "TimeoutError" in result["error"]


def test_platform_order_outcome_uses_bounded_retry_and_timeout_settings() -> None:
    class FlakyPlatform:
        available = True

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def list_orders(self, **kwargs: object) -> list[dict]:
            self.calls.append(kwargs)
            if len(self.calls) < 3:
                raise TimeoutError("late")
            return []

    client = FlakyPlatform()
    provider = PlatformOrderOutcomeProvider(
        client,  # type: ignore[arg-type]
        enabled=True,
        request_timeout_seconds=1.25,
        max_retries=2,
        retry_base_seconds=0,
    )

    result = provider(_event())

    assert result["status"] == "ok"
    assert len(client.calls) == 3
    assert all(call["timeout_seconds"] == 1.25 for call in client.calls)
    assert all(call["retry_attempts"] == 1 for call in client.calls)


def test_platform_order_outcome_can_be_disabled_without_calling_platform() -> None:
    client = _PlatformStub([{"status": 3}])
    provider = PlatformOrderOutcomeProvider(client, enabled=False)  # type: ignore[arg-type]

    result = provider(_event())

    assert result["status"] == "disabled"
    assert client.calls == 0
    assert provider.runtime_status()["status"] == "disabled"


def test_old_completed_order_is_not_attributed_without_a_matching_baseline() -> None:
    client = _PlatformStub(
        [{"id": "old-1", "status": 5, "created_at": "2026-08-01T00:00:00+00:00"}]
    )
    provider = PlatformOrderOutcomeProvider(client, enabled=True)  # type: ignore[arg-type]
    event = _event()
    event["order_state_before"] = "no_order"

    result = provider(event)

    assert result["status"] == "insufficient_baseline"
    assert result["order_state"] == ""
    assert result["selection_mode"] == "missing_baseline"


def test_multiple_historical_orders_do_not_override_unique_pending_baseline() -> None:
    client = _PlatformStub(
        [
            {"id": "old-finished", "status": 5, "created_at": "2026-08-01T00:00:00+00:00"},
            {"id": "current-pending", "status": 1, "created_at": "2026-08-20T00:00:00+00:00"},
        ]
    )
    provider = PlatformOrderOutcomeProvider(client, enabled=True)  # type: ignore[arg-type]

    result = provider(_event())

    assert result["status"] == "ok"
    assert result["order_state"] == "pending"
    assert result["selected_order_id"] == "current-pending"


def test_raw_order_cache_is_re_filtered_for_each_usage_event() -> None:
    client = _PlatformStub(
        [{"id": "new-paid", "status": "paid", "created_at": "2026-09-02T00:00:00+00:00"}]
    )
    provider = PlatformOrderOutcomeProvider(client, enabled=True)  # type: ignore[arg-type]
    before_order = _event()
    before_order.update({"occurred_at": "2026-09-01T00:00:00+00:00", "order_state_before": "no_order"})
    after_order = _event()
    after_order.update({"occurred_at": "2026-09-03T00:00:00+00:00", "order_state_before": "no_order"})

    first = provider(before_order)
    second = provider(after_order)

    assert first["order_state"] == "paid"
    assert second["status"] == "insufficient_baseline"
    assert second["order_state"] == ""
    assert client.calls == 1


def test_reply_payload_exposes_only_compact_previous_policy_state() -> None:
    previous = {
        "previous_intent": "defer",
        "previous_emotion": "hesitant",
        "closing_sequence_key": "price_hesitation",
        "closing_node_key": "value_reframe",
        "customer_replied": True,
    }
    payload = parallel_reply_payload(
        {
            "evidence_join": {
                "shared_context": {
                    "current_message": {"content": "现在可以聊了"},
                    "conversation": [],
                    "authoritative_facts": {},
                    "previous_policy_state": previous,
                },
                "content_candidates": [],
                "sales_recall": {},
                "normalized_tool_facts": {},
            }
        }
    )

    assert payload["previous_policy_state"] == previous
