from __future__ import annotations

import unittest
from typing import Any

from app.services.sop_event_service import SopEventService


class SopEventFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_active_send_identity_skips_before_conversation_fetch(self) -> None:
        repo = _Repo()
        client = _OutreachClient()
        service = _service(repo=repo, client=client)
        payload = {
            "event_id": "evt_missing",
            "event_type": "sop_friend_added_schedule_batch",
            "account": {"enterprise_id": "ent", "wework_user_id": "DY1032"},
            "customers": [{"customer": {"external_userid": "ext"}}],
        }

        repo.create_sop_event(payload)
        result = await service.process_event("evt_missing")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_missing_identity")
        self.assertEqual(client.fetch_calls, [])
        self.assertIn("corp_id", repo.tasks[0]["error"])
        self.assertIn("wechat", repo.tasks[0]["error"])

    async def test_first_added_event_fetches_conversation_then_selects_first_add_sop(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[{"direction": "customer", "content": "你好"}])
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "send opening"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_first",
            event_type="sop_friend_added_schedule_batch",
            sop={"delay_minutes": 3},
            customers=[{"first_added_event": {"trace_id": "trace_1"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_first")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(client.fetch_calls[0]["limit"], 30)
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(repo.tasks[0]["sop_pack_id"], "opening")
        self.assertEqual(repo.tasks[0]["reply_messages"][0]["content"]["text"], "开场话术")
        self.assertEqual(selector.calls[0]["event_type"], "sop_friend_added_schedule_batch")
        self.assertEqual(selector.calls[0]["candidate_packs"][0]["id"], "opening")

    async def test_platform_task_only_judges_and_sends_platform_actions(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[{"direction": "staff", "content": "前面已正常沟通"}])
        selector = _Selector({"send_sop": True, "reason": "actions still useful"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_platform",
            event_type="sop_platform_task",
            sop={
                "platform_task_id": "task_1",
                "actions": [{"type": "text", "content": {"text": "平台建议文案"}}],
            },
            customers=[{}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_platform")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(repo.tasks[0]["sop_pack_id"], "platform_actions")
        self.assertEqual(repo.tasks[0]["reply_messages"][0]["content"]["text"], "平台建议文案")
        self.assertEqual(selector.calls[0]["event_type"], "sop_platform_task")
        self.assertEqual(selector.calls[0]["candidate_packs"], [])
        self.assertEqual(selector.calls[0]["actions_reply_messages"][0]["content"]["text"], "平台建议文案")

    async def test_first_added_event_does_not_use_chat_gate_pack(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        selector = _Selector({"send_sop": True, "sop_pack_id": "chat_opening", "reason": "should not be available"})
        service = _service(repo=repo, client=client, selector=selector, pack_service=_ChatOnlyPackService())
        payload = _base_payload(
            event_id="evt_chat_scope_only",
            event_type="sop_friend_added_schedule_batch",
            sop={"delay_minutes": 3},
            customers=[{"first_added_event": {"trace_id": "trace_scope"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_chat_scope_only")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_no_candidate_sop")
        self.assertEqual(selector.calls, [])

    async def test_first_added_event_skips_same_category_already_sent_by_chat_gate(self) -> None:
        repo = _Repo()
        repo.sent_categories.add("effect_case")
        client = _OutreachClient(messages=[])
        selector = _Selector({"send_sop": True, "sop_pack_id": "effect_followup", "reason": "should be filtered"})
        service = _service(repo=repo, client=client, selector=selector, pack_service=_EffectEventPackService())
        payload = _base_payload(
            event_id="evt_category_skip",
            event_type="sop_friend_added_schedule_batch",
            sop={"delay_minutes": 30},
            customers=[{"first_added_event": {"trace_id": "trace_category"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_category_skip")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_no_candidate_sop")
        self.assertEqual(selector.calls, [])


def _service(repo: Any, client: Any, selector: Any | None = None, pack_service: Any | None = None) -> SopEventService:
    return SopEventService(
        repository=repo,
        sop_reply_pack_service=pack_service or _PackService(),
        outreach_send_client=client,
        sop_execution_service=selector or _Selector({"send_sop": False, "reason": "default reject"}),
    )


def _base_payload(*, event_id: str, event_type: str, sop: dict[str, Any], customers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "corp_id": "ww943af61cd5d2afe4",
        "customer_id": "ext_user",
        "external_userid": "ext_user",
        "user_id": "7294",
        "wechat": "CS001",
        "sop": sop,
        "customers": customers,
    }


class _PackService:
    def load(self) -> dict[str, Any]:
        return {
            "packs": [
                {
                    "id": "opening",
                    "enabled": True,
                    "scope": "event_first_add",
                    "sop_category": "opening",
                    "name": "新客开场",
                    "purpose": "首次加微开场",
                    "order": 10,
                    "send_once": True,
                    "event_type": "sop_friend_added_schedule_batch",
                    "delay_minutes": 1,
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "开场话术"}}],
                },
                {
                    "id": "platform_pack_should_not_be_used",
                    "enabled": True,
                    "scope": "event_platform_task",
                    "sop_category": "platform_actions",
                    "name": "平台包",
                    "purpose": "不应被平台任务自动改选",
                    "order": 20,
                    "send_once": True,
                    "event_type": "sop_platform_task",
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "错误话术"}}],
                },
            ]
        }


class _ChatOnlyPackService:
    def load(self) -> dict[str, Any]:
        return {
            "packs": [
                {
                    "id": "chat_opening",
                    "enabled": True,
                    "scope": "chat_gate",
                    "sop_category": "opening",
                    "name": "聊天入口开场",
                    "purpose": "只允许聊天入口使用",
                    "order": 10,
                    "event_type": "sop_friend_added_schedule_batch",
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "不应被事件使用"}}],
                }
            ]
        }


class _EffectEventPackService:
    def load(self) -> dict[str, Any]:
        return {
            "packs": [
                {
                    "id": "effect_followup",
                    "enabled": True,
                    "scope": "event_first_add",
                    "sop_category": "effect_case",
                    "name": "效果补发",
                    "purpose": "效果案例跟进",
                    "order": 10,
                    "event_type": "sop_friend_added_schedule_batch",
                    "delay_minutes": 30,
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "效果跟进"}}],
                }
            ]
        }


class _Selector:
    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    async def evaluate_event_suggestion(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self.output)


class _OutreachClient:
    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.messages = messages or []
        self.fetch_calls: list[dict[str, Any]] = []
        self.send_calls: list[dict[str, Any]] = []

    async def fetch_conversation(self, **kwargs: Any) -> dict[str, Any]:
        self.fetch_calls.append(kwargs)
        return {"status": "ok", "request": kwargs, "message_count": len(self.messages), "messages": self.messages}

    async def send_reply_messages(self, **kwargs: Any) -> dict[str, Any]:
        self.send_calls.append(kwargs)
        return {
            "status": "sent",
            "send_payload": {"reply_messages": kwargs.get("reply_messages", [])},
            "response": {"code": 0, "msg": "ok"},
        }


class _Repo:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.tasks: list[dict[str, Any]] = []
        self.sent_ids: set[str] = set()
        self.sent_categories: set[str] = set()

    def create_sop_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload["event_id"])
        created = event_id not in self.events
        self.events.setdefault(
            event_id,
            {"event_id": event_id, "raw_payload": payload, "status": "accepted", "error": "", "created": created},
        )
        event = dict(self.events[event_id])
        event["created"] = created
        return event

    def get_sop_event(self, event_id: str) -> dict[str, Any]:
        return dict(self.events.get(event_id, {}))

    def update_sop_event_status(self, event_id: str, *, status: str, error: str = "") -> dict[str, Any]:
        self.events[event_id]["status"] = status
        self.events[event_id]["error"] = error
        return dict(self.events[event_id])

    def list_sent_sop_pack_ids_for_customer(self, *, customer_id: str, external_userid: str) -> list[str]:
        return sorted(self.sent_ids)

    def list_sent_sop_categories_for_customer(self, *, customer_id: str, external_userid: str) -> list[str]:
        return sorted(self.sent_categories)

    def create_sop_send_task(self, **kwargs: Any) -> dict[str, Any]:
        for task in self.tasks:
            if task["idempotency_key"] == kwargs["idempotency_key"]:
                existing = dict(task)
                existing["created"] = False
                return existing
        task = {"id": f"task_{len(self.tasks) + 1}", **kwargs, "created": True}
        self.tasks.append(task)
        return dict(task)

    def update_sop_send_task(
        self,
        task_id: str,
        *,
        status: str,
        send_payload: dict[str, Any] | None = None,
        send_response: dict[str, Any] | None = None,
        error: str = "",
        sent_at: str = "",
    ) -> dict[str, Any]:
        for task in self.tasks:
            if task["id"] == task_id:
                task["status"] = status
                task["send_payload"] = send_payload or {}
                task["send_response"] = send_response or {}
                task["error"] = error
                task["sent_at"] = sent_at
                if status == "sent" and task.get("sop_pack_id"):
                    self.sent_ids.add(str(task["sop_pack_id"]))
                if status == "sent" and task.get("sop_category"):
                    self.sent_categories.add(str(task["sop_category"]))
                return dict(task)
        return {}


if __name__ == "__main__":
    unittest.main()
