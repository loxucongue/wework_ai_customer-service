from __future__ import annotations

import json
import unittest
from typing import Any

from app.services.outreach_service import OutreachService


class PersonalizedOutreachPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_platform_task_plan_uses_latest_context_and_persists_reviewable_drafts(self) -> None:
        repository = _Repository()
        model = _ModelClient()
        service = OutreachService(repository=repository, model_client=model, system_client=object())

        result = await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[
                {
                    "direction": "customer",
                    "content": "门店太远了，我再考虑下",
                    "created_at": "2026-07-27T10:00:00+08:00",
                },
                {
                    "direction": "staff",
                    "content": "活动价已经给您介绍过了",
                    "created_at": "2026-07-27T10:01:00+08:00",
                },
            ],
            conversation_activity={
                "real_customer_message_count": 1,
                "latest_customer_message_at": "2026-07-27T10:00:00+08:00",
            },
            customer_context={"orders": [], "deposit_state": "unknown"},
            platform_task={
                "event_id": "platform-task-1",
                "messages": [{"type": "text", "content": {"text": "平台统一跟进"}}],
            },
        )

        self.assertTrue(result["created"])
        self.assertFalse(result["reused"])
        self.assertEqual(len(model.calls), 1)
        model_input = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertEqual(model_input["recent_messages"][0]["content"], "门店太远了，我再考虑下")
        self.assertTrue(model_input["trigger_context"]["platform_task_filtered"])
        self.assertEqual(model_input["trigger_context"]["activation_policy"], "review_required")
        self.assertEqual(repository.created_plan["customer_id"], "22000001")
        self.assertEqual(
            repository.created_plan["tasks"][0]["reply_messages"][0]["content"]["text"],
            "亲，您上次主要是觉得距离不太方便。活动名额可以先留着，到店时间按您方便安排。",
        )
        self.assertTrue(repository.created_plan["tasks"][0]["before_send_check"])

    async def test_existing_draft_plan_is_reused_without_another_model_call(self) -> None:
        repository = _Repository()
        repository.active_plan = {
            "plan": {
                "id": "plan-existing",
                "status": "draft",
                "created_at": "2026-07-28T10:00:00+08:00",
            },
            "tasks": [{"id": "task-existing"}],
            "events": [],
        }
        model = _ModelClient()
        service = OutreachService(repository=repository, model_client=model, system_client=object())

        result = await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[],
            conversation_activity={
                "real_customer_message_count": 1,
                "latest_customer_message_at": "2026-07-28T09:00:00+08:00",
            },
            customer_context={"orders": []},
            platform_task={"event_id": "platform-task-2", "messages": []},
        )

        self.assertTrue(result["reused"])
        self.assertFalse(result["created"])
        self.assertEqual(model.calls, [])
        self.assertEqual(repository.events[0]["event_type"], "platform_task_filtered_plan_reused")

    async def test_customer_reply_after_draft_plan_regenerates_from_latest_conversation(self) -> None:
        repository = _Repository()
        repository.active_plan = {
            "plan": {
                "id": "plan-old",
                "status": "draft",
                "created_at": "2026-07-27T10:00:00+08:00",
            },
            "tasks": [{"id": "task-old"}],
            "events": [],
        }
        model = _ModelClient()
        service = OutreachService(repository=repository, model_client=model, system_client=object())

        result = await service.ensure_platform_task_plan(
            identity={
                "customer_id": "22000001",
                "corp_id": "corp-1",
                "user_id": "7294",
                "wechat": "DY258",
                "external_userid": "external-1",
            },
            conversation_messages=[
                {
                    "direction": "customer",
                    "content": "我现在主要担心到店还要加钱",
                    "created_at": "2026-07-28T11:00:00+08:00",
                }
            ],
            conversation_activity={
                "real_customer_message_count": 2,
                "latest_customer_message_at": "2026-07-28T11:00:00+08:00",
            },
            customer_context={"orders": []},
            platform_task={"event_id": "platform-task-new-reply", "messages": []},
        )

        self.assertTrue(result["created"])
        self.assertFalse(result["reused"])
        self.assertEqual(repository.updated_statuses, [("plan-old", "cancelled")])
        self.assertEqual(repository.events[0]["event_type"], "platform_task_plan_superseded_by_customer_reply")
        self.assertEqual(len(model.calls), 1)

    async def test_plan_without_reviewable_draft_fails_instead_of_creating_empty_task(self) -> None:
        repository = _Repository()
        model = _ModelClient(response={"should_create_plan": True, "steps": [{"step": 1, "delay_minutes": 30}]})
        service = OutreachService(repository=repository, model_client=model, system_client=object())

        with self.assertRaisesRegex(RuntimeError, "missing_reviewable_drafts"):
            await service.ensure_platform_task_plan(
                identity={
                    "customer_id": "22000001",
                    "corp_id": "corp-1",
                    "user_id": "7294",
                    "wechat": "DY258",
                    "external_userid": "external-1",
                },
                conversation_messages=[{"direction": "customer", "content": "我考虑一下"}],
                conversation_activity={"real_customer_message_count": 1},
                customer_context={"orders": []},
                platform_task={"event_id": "platform-task-3", "messages": []},
            )

        self.assertEqual(repository.created_plan, {})


class _ModelClient:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {
            "should_create_plan": True,
            "conversion_stage": "P3_STORE_MATCH",
            "stall_reason": "store_unclear",
            "customer_psychology": "距离顾虑",
            "plan_goal": "让客户重新开口并保留活动资格",
            "steps": [
                {
                    "step": 1,
                    "delay_minutes": 90,
                    "intent": "store_convenience",
                    "before_send_check": True,
                    "message_goal": "化解距离顾虑",
                    "draft_text": "亲，您上次主要是觉得距离不太方便。活动名额可以先留着，到店时间按您方便安排。",
                    "should_send_payment_collection": False,
                    "content_sources": ["s10_offer"],
                }
            ],
        }
        self.calls: list[dict[str, Any]] = []

    async def chat_json(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": messages, **kwargs})
        return dict(self.response)


class _Repository:
    def __init__(self) -> None:
        self.active_plan: dict[str, Any] = {}
        self.created_plan: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []
        self.updated_statuses: list[tuple[str, str]] = []

    def get_active_outreach_plan_for_customer(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(self.active_plan)

    def recent_customer_context(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"memory": {"last_customer_message_at": "2026-07-27T10:00:00+08:00"}}

    def add_outreach_event(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(kwargs)
        return {"event_id": f"event-{len(self.events)}"}

    def update_outreach_plan_status(self, plan_id: str, status: str) -> dict[str, Any]:
        self.updated_statuses.append((plan_id, status))
        self.active_plan = {}
        return {"plan": {"id": plan_id, "status": status}}

    def create_outreach_plan(self, **kwargs: Any) -> dict[str, Any]:
        self.created_plan = kwargs
        return {
            "plan": {"id": "plan-created", "status": "draft"},
            "tasks": kwargs["tasks"],
            "events": [],
        }


if __name__ == "__main__":
    unittest.main()
