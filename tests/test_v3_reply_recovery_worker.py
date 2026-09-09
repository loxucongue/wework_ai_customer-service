from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
from typing import Any

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.schemas import ChatRequest, ChatResponse, ReplyMessage  # noqa: E402
from app.services.outreach_send_client import OutreachSendClient  # noqa: E402
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
        self.staged: list[dict[str, Any]] = []
        self.finalized: list[dict[str, Any]] = []
        self.cancelled: list[dict[str, Any]] = []
        self.retried: list[dict[str, Any]] = []
        self.manual: list[dict[str, Any]] = []
        self.dispatches: dict[str, dict[str, Any]] = {}
        self.claim_calls = 0
        self.stale_recovery_calls = 0

    def recover_stale_v3_generations(self, **_: Any) -> dict[str, int]:
        self.stale_recovery_calls += 1
        return {"completed": 1, "fallback_pending": 2, "manual_review": 0}

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

    def stage_v3_fallback_recovery_delivery(self, **kwargs: Any) -> dict[str, Any]:
        self.staged.append(kwargs)
        return {"updated": 1, "status": "delivery_pending"}

    def complete_v3_fallback_recovery(self, **kwargs: Any) -> dict[str, Any]:
        self.completed.append(kwargs)
        return self.stage_v3_fallback_recovery_delivery(**kwargs)

    def finalize_v3_recovery_delivery(self, **kwargs: Any) -> dict[str, Any]:
        self.finalized.append(kwargs)
        status = "recovered" if kwargs.get("delivery_status") == "send_succeeded" else "manual_review"
        return {"found": True, "updated": 1, "status": status}

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

    def get_message_dispatch(self, dispatch_id: str) -> dict[str, Any]:
        return dict(self.dispatches.get(dispatch_id) or {})

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
        self._delivery_service = SimpleNamespace(enabled=True, callback_required=True)
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
            meta={
                "reply_source": "v3_reply_recovery",
                "reply_sales_judgment": {
                    "next_sales_action": {
                        "type": "explain_activity",
                        "target_stage": "activity_offer",
                    }
                },
            },
        )


def _worker(
    repository: _Repository | None = None,
    *,
    system: _SystemClient | None = None,
    send: _SendClient | None = None,
    context: _CustomerContext | None = None,
    generator: _Generator | None = None,
    delivery_finalizer: Any | None = None,
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
        delivery_finalizer=delivery_finalizer,
        settings=SimpleNamespace(
            v3_reply_recovery_enabled=True,
            v3_reply_recovery_poll_seconds=0.01,
            v3_reply_recovery_batch_size=5,
            v3_reply_recovery_max_attempts=2,
        ),
    )
    return worker, repository, system, send, context, generator


def test_platform_acceptance_rechecks_every_gate_and_waits_for_delivery_callback() -> None:
    worker, repository, system, send, context, generator = _worker()
    claim = _claim()
    repository.current_claim = claim

    result = asyncio.run(worker.process_claim(claim))

    assert result["status"] == "delivery_pending"
    assert system.status_calls == 2
    assert send.fetch_calls == 2
    assert context.calls == 2
    assert generator.calls == 1
    assert len(send.send_calls) == 1
    outbound = send.send_calls[0]
    assert outbound["delivery_idempotency_key"] == "v3-recovery:request-1"
    assert outbound["source_kind"] == "v3_reply_recovery"
    assert outbound["reply_messages"][0]["client_message_id"]
    assert outbound["source_context"]["sales_stage_record"] == {
        "stage": "activity_offer",
        "action_type": "explain_activity",
    }
    assert repository.staged[0]["dispatch_id"] == "dispatch-1"
    assert repository.finalized == []


def test_confirmed_delivery_uses_recovery_finalizer_boundary() -> None:
    class _Finalizer:
        def __init__(self) -> None:
            self.dispatches: list[dict[str, Any]] = []

        def finalize(self, dispatch: dict[str, Any]) -> dict[str, Any]:
            self.dispatches.append(dispatch)
            return {"found": True, "updated": 1, "status": "recovered"}

    repository = _Repository()
    repository.dispatches["dispatch-1"] = {
        "id": "dispatch-1",
        "source_kind": "v3_reply_recovery",
        "source_request_id": "request-1",
        "status": "send_succeeded",
        "source_context": {"sales_stage_record": {"stage": "activity_offer"}},
    }
    send = _SendClient()
    send.send_result = {
        "status": "sent",
        "delivery_status": "send_succeeded",
        "dispatch_id": "dispatch-1",
    }
    finalizer = _Finalizer()
    worker, repository, _, _, _, _ = _worker(
        repository,
        send=send,
        delivery_finalizer=finalizer,
    )
    claim = _claim()
    repository.current_claim = claim

    result = asyncio.run(worker.process_claim(claim))

    assert result["status"] == "recovered"
    assert len(finalizer.dispatches) == 1
    assert finalizer.dispatches[0]["status"] == "send_succeeded"


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


def test_pending_unpaid_customer_is_still_eligible_for_recovery() -> None:
    context = _CustomerContext()
    context.context = {
        "source": "platform_agent",
        "orders": [
            {
                "id": "order-1",
                "status": "pending",
                "prepay_required": 10,
                "prepay_paid": False,
                "deposit_state": "required_unpaid",
            }
        ],
        "appointment": {"status": "pending", "order_id": "order-1"},
    }
    worker, repository, _, send, _, generator = _worker(context=context)
    claim = _claim()
    repository.current_claim = claim

    result = asyncio.run(worker.process_claim(claim))

    assert result["status"] == "delivery_pending"
    assert generator.calls == 1
    assert len(send.send_calls) == 1


def test_waiting_schedule_label_alone_is_not_an_authoritative_appointment() -> None:
    context = _CustomerContext()
    context.context = {
        "source": "platform_agent",
        "orders": [],
        "appointment": {"status": "waiting_schedule", "order_id": "order-1"},
    }
    worker, repository, _, send, _, generator = _worker(context=context)
    claim = _claim()
    repository.current_claim = claim

    result = asyncio.run(worker.process_claim(claim))

    assert result["status"] == "delivery_pending"
    assert generator.calls == 1
    assert len(send.send_calls) == 1


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
    context.context = {
        "source": "platform_agent",
        "orders": [
            {
                "id": "order-1",
                "status": "pending",
                "prepay_required": 10,
                "prepay_paid": True,
                "deposit_state": "paid_by_order",
            }
        ],
        "appointment": {"status": "pending", "order_id": "order-1"},
    }
    worker, repository, _, send, _, generator = _worker(context=context)
    repository.current_claim = claim
    paid_pending_result = asyncio.run(worker.process_claim(claim))
    assert paid_pending_result["status"] == "cancelled"
    assert paid_pending_result["reason"] == "order_state_changed"
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


def test_real_appointment_id_or_valid_time_cancels_recovery() -> None:
    claim = _claim()
    for appointment in (
        {"status": "pending", "appointment_id": "appointment-1"},
        {"status": "pending", "appointment_time": "2026-09-10 10:00:00"},
        {"status": "visited"},
        {"status": "finished"},
    ):
        context = _CustomerContext()
        context.context["appointment"] = appointment
        worker, repository, _, send, _, generator = _worker(context=context)
        repository.current_claim = claim

        result = asyncio.run(worker.process_claim(claim))

        assert result["status"] == "cancelled"
        assert result["reason"] == "appointment_state_changed"
        assert generator.calls == 0
        assert send.send_calls == []


def test_deployment_example_keeps_recovery_disabled_by_default() -> None:
    example = (PROJECT_ROOT / "deploy" / "v3.env.example").read_text(encoding="utf-8")

    assert "V3_REPLY_RECOVERY_ENABLED=false" in example
    assert "V3_REPLY_RECOVERY_ENABLED=true" not in example


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

    send = _SendClient()
    send._delivery_service.callback_required = False
    worker, repository, _, _, _, generator = _worker(send=send)
    claim = _claim()
    repository.current_claim = claim
    no_callback = asyncio.run(worker.process_claim(claim))
    assert no_callback["status"] == "manual_review"
    assert no_callback["reason"] == "recovery_delivery_tracking_unavailable"
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


def test_existing_accepted_dispatch_waits_without_model_or_resend() -> None:
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

    assert result["status"] == "delivery_pending"
    assert result["duplicate_dispatch"] is True
    assert repository.staged[0]["dispatch_id"] == "dispatch-existing"
    assert repository.finalized == []
    assert generator.calls == 0
    assert send.send_calls == []


def test_run_once_claims_configured_batch() -> None:
    claim = _claim()
    repository = _Repository([claim])
    repository.current_claim = claim
    worker, _, _, _, _, _ = _worker(repository)

    results = asyncio.run(worker.run_once())

    assert repository.claim_calls == 1
    assert [item["status"] for item in results] == ["delivery_pending"]
    assert worker.status()["counters"]["claimed"] == 1
    assert repository.stale_recovery_calls == 1
    assert worker.status()["counters"]["stale_completed"] == 1
    assert worker.status()["counters"]["stale_fallback_pending"] == 2


def test_confirmed_dispatch_is_the_only_path_to_recovered() -> None:
    system = _SystemClient()
    system.dispatch = {
        "id": "dispatch-confirmed",
        "status": "send_succeeded",
        "reply_messages": [{"type": "text", "order": 1, "content": "confirmed"}],
    }
    worker, repository, _, send, _, generator = _worker(system=system)
    claim = _claim()
    repository.current_claim = claim

    result = asyncio.run(worker.process_claim(claim))

    assert result["status"] == "recovered"
    assert repository.finalized == [
        {
            "request_id": "request-1",
            "dispatch_id": "dispatch-confirmed",
            "delivery_status": "send_succeeded",
            "error": "",
        }
    ]
    assert generator.calls == 0
    assert send.send_calls == []


def test_repository_cas_conflict_is_not_reported_as_recovered() -> None:
    system = _SystemClient()
    system.dispatch = {
        "id": "dispatch-confirmed",
        "status": "send_succeeded",
        "reply_messages": [{"type": "text", "order": 1, "content": "confirmed"}],
    }
    worker, repository, _, _, _, _ = _worker(system=system)
    repository.current_claim = _claim()

    def conflict(**_: Any) -> dict[str, Any]:
        return {"found": True, "updated": 0, "status": "state_conflict", "reason": "changed"}

    repository.finalize_v3_recovery_delivery = conflict  # type: ignore[method-assign]
    result = asyncio.run(worker.process_claim(repository.current_claim))

    assert result["status"] == "state_conflict"
    assert result["reason"] == "changed"


class _DeliveryTracking:
    enabled = True
    callback_required = True

    def __init__(self, *, guard_error: str = "") -> None:
        self.guard_error = guard_error
        self.guard_calls = 0
        self.submissions: list[str] = []

    def assert_proactive_send_allowed(self, _: dict[str, Any]) -> None:
        self.guard_calls += 1
        if self.guard_error:
            raise RuntimeError(self.guard_error)

    def prepare_dispatch(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "dispatch": {"id": "dispatch-real", "status": "created", "created": True},
            "dispatch_id": "dispatch-real",
            "reply_messages": kwargs["reply_messages"],
            "callback_url": "https://callback.test/message-delivery",
            "callback_required": True,
        }

    def record_submission(self, _: str, *, status: str, **__: Any) -> dict[str, Any]:
        self.submissions.append(status)
        return {"status": status}

    def mark_finalized(self, _: str) -> dict[str, Any]:
        raise AssertionError("callback-confirmed recovery must not finalize on HTTP acceptance")


def _real_send_client(delivery: _DeliveryTracking) -> OutreachSendClient:
    settings = Settings().model_copy(
        update={
            "outreach_send_base_url": "https://send.test",
            "outreach_send_agent_token": "token",
        }
    )
    return OutreachSendClient(settings, delivery_service=delivery)  # type: ignore[arg-type]


def _real_send_kwargs() -> dict[str, Any]:
    return {
        "request_id": "request-1",
        "request_context": {
            "corp_id": "corp-1",
            "customer_id": "customer-1",
            "external_userid": "external-1",
            "user_id": "88",
            "wechat": "SL8003",
        },
        "fallback_customer_id": "customer-1",
        "fallback_corp_id": "corp-1",
        "fallback_user_id": "88",
        "fallback_wechat": "SL8003",
        "fallback_external_userid": "external-1",
        "reply_messages": [{"type": "text", "order": 1, "content": "reply"}],
        "source_kind": "v3_reply_recovery",
        "delivery_idempotency_key": "v3-recovery:request-1",
    }


def test_real_send_persists_submitting_before_non_idempotent_http() -> None:
    delivery = _DeliveryTracking()
    client = _real_send_client(delivery)
    observed: list[list[str]] = []

    async def request(*_: Any, **__: Any) -> httpx.Response:
        observed.append(list(delivery.submissions))
        return httpx.Response(200, json={"data": {"request_id": "platform-1"}})

    client._request_with_retry = request  # type: ignore[method-assign]
    result = asyncio.run(client.send_reply_messages(**_real_send_kwargs()))

    assert observed == [["submitting"]]
    assert delivery.submissions == ["submitting", "platform_accepted"]
    assert result["status"] == "accepted"
    assert result["delivery_status"] == "platform_accepted"


def test_real_send_guard_blocks_immediately_before_dispatch() -> None:
    delivery = _DeliveryTracking(guard_error="explicit_stop_contact")
    client = _real_send_client(delivery)
    called = 0

    async def request(*_: Any, **__: Any) -> httpx.Response:
        nonlocal called
        called += 1
        return httpx.Response(200)

    client._request_with_retry = request  # type: ignore[method-assign]
    result = asyncio.run(client.send_reply_messages(**_real_send_kwargs()))

    assert result == {"status": "skipped", "reason": "explicit_stop_contact"}
    assert delivery.guard_calls == 1
    assert delivery.submissions == []
    assert called == 0


def test_non_idempotent_transport_error_is_not_retried_and_stays_unknown() -> None:
    delivery = _DeliveryTracking()
    client = _real_send_client(delivery)
    calls = 0

    class Transport:
        async def request(self, *_: Any, **__: Any) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.RemoteProtocolError("connection closed after submit")

    client._http_client = lambda: Transport()  # type: ignore[method-assign]
    result = asyncio.run(client.send_reply_messages(**_real_send_kwargs()))

    assert calls == 1
    assert delivery.submissions == ["submitting", "submission_unknown"]
    assert result["status"] == "accepted"
    assert result["delivery_status"] == "submission_unknown"
