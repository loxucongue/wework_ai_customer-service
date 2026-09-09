from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.schemas import ChatRequest, ChatResponse, ReplyMessage  # noqa: E402
from app.services.v3_reply_recovery_worker import V3ReplyRecoveryWorker  # noqa: E402


def _request_payload() -> dict[str, Any]:
    return {
        "content": "多少钱？",
        "customer_id": "customer-1",
        "platform_customer_id": "customer-1",
        "corp_id": "corp-1",
        "conversation_history": [],
        "user_id": 88,
        "wechat": "SL8003",
        "external_userid": "external-1",
        "request_context": {
            "interface_version": "v3",
            "msgid": "message-original",
            "corp_id": "corp-1",
            "customer_id": "customer-1",
            "external_userid": "external-1",
            "user_id": 88,
            "wechat": "SL8003",
        },
    }


def _claim(*, attempts: int = 1, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "request_id": "request-1",
        "conversation_id": "conversation-1",
        "response_id": "response-1",
        "generation_status": "recovery_claimed",
        "recovery_kind": "reply_timeout",
        "recovery_attempts": attempts,
        "request": payload or _request_payload(),
    }


class _Repository:
    def __init__(self, claims: list[dict[str, Any]] | None = None) -> None:
        self.claims = list(claims or [])
        self.newer_results: list[dict[str, Any]] = []
        self.stop_contact = False
        self.completed: list[dict[str, Any]] = []
        self.cancelled: list[dict[str, Any]] = []
        self.retried: list[dict[str, Any]] = []
        self.manual: list[dict[str, Any]] = []
        self.claim_calls = 0

    def claim_v3_fallback_recoveries(self, **_: Any) -> list[dict[str, Any]]:
        self.claim_calls += 1
        values, self.claims = self.claims, []
        return values

    def newer_customer_message_for_v3_recovery(self, _: str) -> dict[str, Any]:
        if self.newer_results:
            return self.newer_results.pop(0)
        return {"has_newer": False, "reason": ""}

    def has_stop_contact(self, _: str) -> bool:
        return self.stop_contact

    def complete_v3_fallback_recovery(self, **kwargs: Any) -> dict[str, Any]:
        self.completed.append(kwargs)
        return {"updated": 1, "status": "recovered"}

    def cancel_v3_fallback_recovery(self, **kwargs: Any) -> dict[str, Any]:
        self.cancelled.append(kwargs)
        return {"updated": 1, "status": "recovery_failed"}

    def retry_v3_fallback_recovery(self, **kwargs: Any) -> dict[str, Any]:
        self.retried.append(kwargs)
        attempts = int(self.current_claim.get("recovery_attempts") or 0)
        exhausted = attempts >= int(kwargs.get("max_attempts") or 2)
        return {
            "updated": 1,
            "status": "manual_review" if exhausted else "fallback_pending",
            "exhausted": exhausted,
        }

    def mark_v3_recovery_manual_review(self, **kwargs: Any) -> dict[str, Any]:
        self.manual.append(kwargs)
        return {"updated": 1, "status": "manual_review"}

    def get_v3_generation_result(self, **_: Any) -> dict[str, Any]:
        return dict(self.current_claim)

    @property
    def current_claim(self) -> dict[str, Any]:
        return getattr(self, "_current_claim", _claim())

    @current_claim.setter
    def current_claim(self, value: dict[str, Any]) -> None:
        self._current_claim = value


class _SystemClient:
    available = True

    def __init__(self) -> None:
        self.human = False
        self.status_available = True
        self.dispatch: dict[str, Any] = {}
        self.status_calls = 0

    async def conversation_status(self, **_: Any) -> dict[str, Any]:
        self.status_calls += 1
        if not self.status_available:
            raise RuntimeError("temporary status outage")
        return {
            "data": {
                "ai_auto_reply": not self.human,
                "takeover": {"mode": "human" if self.human else "ai", "is_human": self.human},
                "ai_outreach": {"send_allowed": True},
            }
        }

    def delivery_dispatch(self, _: str) -> dict[str, Any]:
        return dict(self.dispatch)


class _SendClient:
    def __init__(self) -> None:
        self._delivery_service = SimpleNamespace(enabled=True)
        self.messages = [
            {
                "direction": "customer",
                "msgid": "message-original",
                "created_at": "2026-09-09T00:00:00+00:00",
                "content": "多少钱？",
            }
        ]
        self.relation = {"available": True, "status": "active", "is_deleted": False}
        self.fetch_calls = 0
        self.send_calls: list[dict[str, Any]] = []
        self.send_result: dict[str, Any] = {
            "status": "accepted",
            "delivery_status": "platform_accepted",
            "dispatch_id": "dispatch-1",
        }

    async def fetch_conversation(self, **_: Any) -> dict[str, Any]:
        self.fetch_calls += 1
        return {
            "status": "ok",
            "messages": list(self.messages),
            "customer_relation": dict(self.relation),
        }

    async def send_reply_messages(self, **kwargs: Any) -> dict[str, Any]:
        self.send_calls.append(kwargs)
        return dict(self.send_result)


class _CustomerContext:
    def __init__(self) -> None:
        self.context: dict[str, Any] = {
            "source": "platform_agent",
            "orders": [],
            "appointment": {},
        }
        self.calls = 0

    def load(self, **_: Any) -> dict[str, Any]:
        self.calls += 1
        return dict(self.context)


class _Generator:
    def __init__(self) -> None:
        self.calls = 0
        self.error: Exception | None = None

    async def __call__(self, request: ChatRequest, request_id: str) -> ChatResponse:
        self.calls += 1
        if self.error:
            raise self.error
        assert request.content == "多少钱？"
        return ChatResponse(
            request_id=request_id,
            response_id="response-1",
            reply_messages=[ReplyMessage(type="text", order=1, content="这次活动是268元哦～")],
            meta={"reply_source": "v3_reply_recovery"},
        )


def _worker(
    repository: _Repository | None = None,
    *,
    system: _SystemClient | None = None,
    send: _SendClient | None = None,
    context: _CustomerContext | None = None,
    generator: _Generator | None = None,
) -> tuple[V3ReplyRecoveryWorker, _Repository, _SystemClient, _SendClient, _CustomerContext, _Generator]:
    repository = repository or _Repository()
    system = system or _SystemClient()
    send = send or _SendClient()
    context = context or _CustomerContext()
    generator = generator or _Generator()
    worker = V3ReplyRecoveryWorker(
        repository=repository,
        generator=generator,
        system_client=system,
        send_client=send,
        customer_context_service=context,
        settings=SimpleNamespace(
            v3_reply_recovery_enabled=True,
            v3_reply_recovery_poll_seconds=0.01,
            v3_reply_recovery_batch_size=5,
            v3_reply_recovery_max_attempts=2,
        ),
    )
    return worker, repository, system, send, context, generator


def test_success_rechecks_every_gate_and_sends_with_stable_idempotency() -> None:
    worker, repository, system, send, context, generator = _worker()
    claim = _claim()
    repository.current_claim = claim

    result = asyncio.run(worker.process_claim(claim))

    assert result["status"] == "recovered"
    assert system.status_calls == 2
    assert send.fetch_calls == 2
    assert context.calls == 2
    assert generator.calls == 1
    assert len(send.send_calls) == 1
    outbound = send.send_calls[0]
    assert outbound["delivery_idempotency_key"] == "v3-recovery:request-1"
    assert outbound["source_kind"] == "v3_reply_recovery"
    assert outbound["reply_messages"][0]["client_message_id"]
    assert repository.completed[0]["dispatch_id"] == "dispatch-1"


def test_new_local_customer_message_cancels_before_model_or_send() -> None:
    worker, repository, _, send, _, generator = _worker()
    claim = _claim()
    repository.current_claim = claim
    repository.newer_results = [{"has_newer": True, "reason": "customer_sent_newer_message"}]

    result = asyncio.run(worker.process_claim(claim))

    assert result["status"] == "cancelled"
    assert result["reason"] == "customer_sent_newer_message"
    assert generator.calls == 0
    assert send.send_calls == []
    assert repository.cancelled


def test_guard_change_during_generation_cancels_before_send() -> None:
    worker, repository, system, send, context, generator = _worker()
    claim = _claim()
    repository.current_claim = claim
    repository.newer_results = [
        {"has_newer": False, "reason": ""},
        {"has_newer": True, "reason": "customer_sent_newer_message"},
    ]

    result = asyncio.run(worker.process_claim(claim))

    assert result["status"] == "cancelled"
    assert generator.calls == 1
    assert send.send_calls == []
    assert system.status_calls == 1
    assert context.calls == 1


def test_human_mode_and_deleted_relation_are_business_cancellations() -> None:
    system = _SystemClient()
    system.human = True
    worker, repository, _, send, _, generator = _worker(system=system)
    claim = _claim()
    repository.current_claim = claim

    human_result = asyncio.run(worker.process_claim(claim))

    assert human_result["status"] == "cancelled"
    assert human_result["reason"] == "human_mode"
    assert generator.calls == 0
    assert send.send_calls == []

    system = _SystemClient()
    send = _SendClient()
    send.relation = {"available": True, "status": "deleted", "is_deleted": True}
    worker, repository, _, _, _, generator = _worker(system=system, send=send)
    repository.current_claim = claim
    deleted_result = asyncio.run(worker.process_claim(claim))
    assert deleted_result["status"] == "cancelled"
    assert deleted_result["reason"] == "customer_relation_deleted"
    assert generator.calls == 0


def test_paid_or_booked_customer_cancels_recovery() -> None:
    context = _CustomerContext()
    context.context = {
        "source": "platform_agent",
        "orders": [{"id": "order-1", "status": "paid", "prepay_paid": True}],
        "appointment": {},
    }
    worker, repository, _, send, _, generator = _worker(context=context)
    claim = _claim()
    repository.current_claim = claim

    paid_result = asyncio.run(worker.process_claim(claim))

    assert paid_result["status"] == "cancelled"
    assert paid_result["reason"] == "order_state_changed"
    assert generator.calls == 0
    assert send.send_calls == []

    context = _CustomerContext()
    context.context["appointment"] = {"id": "appointment-1", "status": "scheduled"}
    worker, repository, _, _, _, generator = _worker(context=context)
    repository.current_claim = claim
    booked_result = asyncio.run(worker.process_claim(claim))
    assert booked_result["status"] == "cancelled"
    assert booked_result["reason"] == "appointment_state_changed"
    assert generator.calls == 0


def test_platform_new_customer_message_cancels_recovery() -> None:
    send = _SendClient()
    send.messages.append(
        {
            "direction": "customer",
            "msgid": "message-new",
            "created_at": "2026-09-09T00:00:10+00:00",
            "content": "我又问了一个问题",
        }
    )
    worker, repository, _, _, _, generator = _worker(send=send)
    claim = _claim()
    repository.current_claim = claim

    result = asyncio.run(worker.process_claim(claim))

    assert result["status"] == "cancelled"
    assert result["reason"] == "platform_customer_sent_newer_message"
    assert generator.calls == 0


def test_omitted_image_or_disabled_delivery_goes_directly_to_manual_review() -> None:
    payload = _request_payload()
    payload["file_image"] = "[base64 image omitted: 100000 chars]"
    worker, repository, _, send, _, generator = _worker()
    claim = _claim(payload=payload)
    repository.current_claim = claim

    omitted = asyncio.run(worker.process_claim(claim))

    assert omitted["status"] == "manual_review"
    assert omitted["reason"] == "recovery_image_payload_omitted"
    assert generator.calls == 0
    assert send.send_calls == []

    send = _SendClient()
    send._delivery_service.enabled = False
    worker, repository, _, _, _, generator = _worker(send=send)
    claim = _claim()
    repository.current_claim = claim
    disabled = asyncio.run(worker.process_claim(claim))
    assert disabled["status"] == "manual_review"
    assert disabled["reason"] == "recovery_delivery_tracking_unavailable"
    assert generator.calls == 0


def test_transient_failure_retries_after_about_thirty_seconds_then_exhausts() -> None:
    generator = _Generator()
    generator.error = TimeoutError("model timeout")
    worker, repository, _, send, _, _ = _worker(generator=generator)
    first = _claim(attempts=1)
    repository.current_claim = first
    before = datetime.now(timezone.utc)

    retry = asyncio.run(worker.process_claim(first))

    assert retry["status"] == "retry_scheduled"
    retry_at = datetime.fromisoformat(retry["next_retry_at"])
    assert 28 <= (retry_at - before).total_seconds() <= 32
    assert send.send_calls == []

    second = _claim(attempts=2)
    repository.current_claim = second
    exhausted = asyncio.run(worker.process_claim(second))
    assert exhausted["status"] == "manual_review"
    assert repository.retried[-1]["max_attempts"] == 2


def test_existing_accepted_dispatch_is_reconciled_without_model_or_resend() -> None:
    system = _SystemClient()
    system.dispatch = {
        "id": "dispatch-existing",
        "status": "platform_accepted",
        "reply_messages": [{"type": "text", "order": 1, "content": "已经接受的补答"}],
    }
    worker, repository, _, send, _, generator = _worker(system=system)
    claim = _claim()
    repository.current_claim = claim

    result = asyncio.run(worker.process_claim(claim))

    assert result["status"] == "recovered"
    assert result["duplicate_dispatch"] is True
    assert repository.completed[0]["dispatch_id"] == "dispatch-existing"
    assert generator.calls == 0
    assert send.send_calls == []


def test_run_once_claims_configured_batch() -> None:
    claim = _claim()
    repository = _Repository([claim])
    repository.current_claim = claim
    worker, _, _, _, _, _ = _worker(repository)

    results = asyncio.run(worker.run_once())

    assert repository.claim_calls == 1
    assert [item["status"] for item in results] == ["recovered"]
    assert worker.status()["counters"]["claimed"] == 1
