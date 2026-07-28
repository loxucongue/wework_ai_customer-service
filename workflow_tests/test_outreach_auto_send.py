from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.services.outreach_service import OutreachService
from app.services.storage import AppRepository, SQLiteStore


class OutreachAutoSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_approved_task_sends_after_fresh_conversation_and_order_checks(self) -> None:
        repository = _ExecutionRepository(order_status="no_order")
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(orders=[]),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(system.sent), 1)
        self.assertIn(("task-1", "sent"), repository.task_statuses)
        self.assertIn(("plan-1", "waiting"), repository.plan_statuses)

    async def test_auto_approved_task_is_skipped_when_order_became_booked(self) -> None:
        repository = _ExecutionRepository(order_status="no_order")
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(
                orders=[{"id": "order-1", "status": "waiting_schedule", "is_current_order": True}]
            ),
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result, {"ok": True, "status": "skipped", "reason": "order_state_changed"})
        self.assertEqual(system.sent, [])
        self.assertIn(("plan-1", "completed"), repository.plan_statuses)
        self.assertEqual(repository.events[-1]["event_type"], "task_skipped_order_state_changed")

    async def test_unavailable_order_check_reschedules_instead_of_sending(self) -> None:
        repository = _ExecutionRepository(order_status="no_order")
        system = _SystemClient()
        service = OutreachService(
            repository=repository,
            model_client=object(),
            system_client=system,
            customer_context_service=_CustomerContextService(
                context={"source": "local_memory_fallback", "orders_error": "upstream timeout"}
            ),
            before_send_retry_seconds=45,
        )

        result = await service.execute_task("task-1")

        self.assertEqual(result["status"], "rescheduled")
        self.assertTrue(result["retryable"])
        self.assertEqual(system.sent, [])
        self.assertEqual(repository.reschedules[-1]["delay_seconds"], 45)


class OutreachRepositoryDueTaskTests(unittest.TestCase):
    def test_auto_worker_selects_only_auto_approved_and_one_step_per_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "outreach.db"))
            store.initialize()
            repository = AppRepository(store)
            auto = repository.create_outreach_plan(
                customer_id="customer-auto",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
                external_userid="external-auto",
                customer_stage="P2_OBJECTION",
                stall_reason="silent",
                customer_psychology="仍有兴趣",
                plan_goal="重新开口",
                source_snapshot={
                    "trigger_context": {
                        "source": "sop_platform_task",
                        "activation_policy": "auto_approved",
                    }
                },
                tasks=[
                    _due_task(1, "第一步"),
                    _due_task(2, "第二步"),
                ],
            )
            manual = repository.create_outreach_plan(
                customer_id="customer-manual",
                corp_id="corp",
                user_id="7294",
                wechat="DY258",
                external_userid="external-manual",
                customer_stage="P2_OBJECTION",
                stall_reason="silent",
                customer_psychology="仍有兴趣",
                plan_goal="重新开口",
                source_snapshot={"trigger_context": {"activation_policy": "review_required"}},
                tasks=[_due_task(1, "人工计划")],
            )
            repository.update_outreach_plan_status(auto["plan"]["id"], "active")
            repository.update_outreach_plan_status(manual["plan"]["id"], "active")

            tasks = repository.list_due_outreach_tasks(
                limit=20,
                now="2030-01-01T00:00:00+00:00",
                auto_approved_only=True,
            )

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["customer_id"], "customer-auto")
            self.assertEqual(tasks[0]["step_index"], 1)


def _due_task(step: int, text: str) -> dict[str, Any]:
    return {
        "step_index": step,
        "scheduled_at": "2020-01-01T00:00:00+00:00",
        "intent": "silence_probe",
        "message_goal": "重新开口",
        "content_sources": [],
        "before_send_check": True,
        "reply_messages": [{"type": "text", "order": 1, "content": {"text": text}}],
    }


class _CustomerContextService:
    def __init__(self, orders: list[dict[str, Any]] | None = None, context: dict[str, Any] | None = None) -> None:
        self.context = context or {"source": "platform_agent", "orders": orders or []}

    def load(self, **_kwargs: Any) -> dict[str, Any]:
        return dict(self.context)


class _SystemClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def conversation(self, **_kwargs: Any) -> dict[str, Any]:
        return {"data": {"messages": []}}

    async def send(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {"code": 0, "data": {"send_status": "accepted", "system_msgid": "msg-1"}}


class _ExecutionRepository:
    def __init__(self, order_status: str) -> None:
        self.order_status = order_status
        self.task_statuses: list[tuple[str, str]] = []
        self.plan_statuses: list[tuple[str, str]] = []
        self.events: list[dict[str, Any]] = []
        self.reschedules: list[dict[str, Any]] = []

    def get_outreach_task(self, task_id: str) -> dict[str, Any]:
        return {
            "id": task_id,
            "plan_id": "plan-1",
            "customer_id": "customer-1",
            "corp_id": "corp",
            "user_id": "7294",
            "wechat": "DY258",
            "external_userid": "external-1",
            "status": "pending",
            "before_send_check": True,
            "reply_messages": [{"type": "text", "order": 1, "content": {"text": "亲，前面您主要担心效果，我再给您说清楚一点。"}}],
        }

    def claim_outreach_task(self, _task_id: str) -> bool:
        return True

    def get_outreach_plan(self, _plan_id: str) -> dict[str, Any]:
        return {
            "plan": {
                "id": "plan-1",
                "customer_id": "customer-1",
                "corp_id": "corp",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
                "created_at": "2026-07-28T08:00:00+08:00",
                "source_snapshot": {
                    "memory": {"last_customer_message_at": "2026-07-28T08:00:00+08:00"},
                    "trigger_context": {
                        "source": "sop_platform_task",
                        "activation_policy": "auto_approved",
                    },
                },
            }
        }

    def recent_customer_context(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"memory": {"last_customer_message_at": "2026-07-28T08:00:00+08:00"}}

    def touch_customer_message_time(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def update_customer_outreach_state(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def update_outreach_task(self, task_id: str, *, status: str, **_kwargs: Any) -> dict[str, Any]:
        self.task_statuses.append((task_id, status))
        return self.get_outreach_task(task_id)

    def update_outreach_plan_status(self, plan_id: str, status: str) -> dict[str, Any]:
        self.plan_statuses.append((plan_id, status))
        return {"plan": {"id": plan_id, "status": status}}

    def add_outreach_event(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(kwargs)
        return kwargs

    def reschedule_outreach_task(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        self.reschedules.append({"task_id": task_id, **kwargs})
        return self.get_outreach_task(task_id)


if __name__ == "__main__":
    unittest.main()
