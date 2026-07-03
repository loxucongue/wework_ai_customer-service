from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from tempfile import TemporaryDirectory

from app.services.sop_event_service import SopEventService
from app.services.sop_execution_service import SopExecutionService
from app.services.storage import AppRepository, SQLiteStore


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

        self.assertEqual(result["status"], "processed_with_errors")
        self.assertEqual(repo.tasks[0]["status"], "skipped_missing_identity")
        self.assertEqual(client.fetch_calls, [])
        self.assertIn("corp_id", repo.tasks[0]["error"])
        self.assertNotIn("wechat", repo.tasks[0]["error"])

    async def test_event_identity_falls_back_from_existing_wechat_identity(self) -> None:
        repo = _Repo()
        repo.identity_lookup = {
            "corp_id": "ww943af61cd5d2afe4",
            "user_id": "7294",
            "wechat": "CS001",
            "identity_source": "conversations",
        }
        client = _OutreachClient(messages=[{"direction": "customer", "content": "你好"}])
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "send opening"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = {
            "event_id": "evt_identity_fallback",
            "event_type": "sop_friend_added_schedule_batch",
            "account": {"enterprise_id": "ent", "wework_user_id": "CS001"},
            "sop": {"delay_minutes": 1},
            "customers": [{"customer": {"external_userid": "ext_user"}}],
        }

        repo.create_sop_event(payload)
        result = await service.process_event("evt_identity_fallback")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(client.fetch_calls[0]["corp_id"], "ww943af61cd5d2afe4")
        self.assertEqual(client.fetch_calls[0]["user_id"], "7294")
        self.assertEqual(client.fetch_calls[0]["wechat"], "CS001")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(repo.tasks[0]["send_payload"]["identity"]["identity_source"], "conversations")

    async def test_event_identity_falls_back_to_default_platform_identity(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[{"direction": "customer", "content": "hello"}])
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "send opening"})
        service = _service(
            repo=repo,
            client=client,
            selector=selector,
            default_identity={
                "corp_id": "ww943af61cd5d2afe4",
                "user_id": "test2",
                "wechat": "auto-3a03ca3ecaae3ae2",
            },
        )
        payload = {
            "event_id": "evt_identity_default",
            "event_type": "sop_friend_added_schedule_batch",
            "account": {"enterprise_id": "ent", "wework_user_id": "SL097", "assignee_id": "test"},
            "sop": {"delay_minutes": 1},
            "customers": [{"customer": {"external_userid": "ext_user"}}],
        }

        repo.create_sop_event(payload)
        result = await service.process_event("evt_identity_default")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(client.fetch_calls[0]["corp_id"], "ww943af61cd5d2afe4")
        self.assertEqual(client.fetch_calls[0]["user_id"], "test")
        self.assertEqual(client.fetch_calls[0]["wechat"], "SL097")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(repo.tasks[0]["send_payload"]["identity"]["identity_source"], "default_platform_identity")
        self.assertEqual(repo.tasks[0]["send_payload"]["identity"]["identity_default_fields"], "corp_id")

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

    async def test_first_added_event_ignores_conversation_before_first_add_time(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {"from": "staff", "content": "旧会话", "msgtime": 1782286005000},
                {"from": "customer", "content": "新回复", "msgtime": "2026-07-02T04:00:00+00:00"},
                {"from": "staff", "content": "事件后回复", "msgtime": "2026-07-02T04:20:00+00:00"},
            ]
        )
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "send opening"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_first_filter_history",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-02T04:10:00+00:00",
            sop={"delay_minutes": 1},
            customers=[{"first_added_event": {"trace_id": "trace_filter", "timestamp": "2026-07-02T03:50:13+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_first_filter_history")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(selector.calls[0]["conversation_messages"], [{"from": "customer", "content": "新回复", "msgtime": "2026-07-02T04:00:00+00:00"}])
        conversation_filter = repo.tasks[0]["send_payload"]["conversation_filter"]
        self.assertEqual(conversation_filter["input_count"], 3)
        self.assertEqual(conversation_filter["kept_count"], 1)
        self.assertEqual(conversation_filter["dropped_before_first_add"], 1)
        self.assertEqual(conversation_filter["dropped_after_event_created"], 1)

    async def test_immediate_first_added_event_uses_immediate_first_add_sop(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        selector = _Selector({"send_sop": True, "sop_pack_id": "immediate_opening", "reason": "send immediate opening"})
        service = _service(repo=repo, client=client, selector=selector, pack_service=_ImmediatePackService())
        payload = _base_payload(
            event_id="evt_immediate",
            event_type="sop_friend_added_immediate",
            sop={"delay_minutes": 0},
            customers=[{"first_added_event": {"trace_id": "trace_immediate"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_immediate")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(repo.tasks[0]["sop_pack_id"], "immediate_opening")
        self.assertEqual(selector.calls[0]["event_type"], "sop_friend_added_immediate")
        self.assertEqual(selector.calls[0]["candidate_packs"][0]["id"], "immediate_opening")

    async def test_immediate_first_added_event_can_use_dual_scope_opening_pack(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        selector = _Selector({"send_sop": True, "sop_pack_id": "s10_new_customer_opening", "reason": "send opening"})
        service = _service(repo=repo, client=client, selector=selector, pack_service=_DualScopeOpeningPackService())
        payload = _base_payload(
            event_id="evt_immediate_dual_scope",
            event_type="sop_friend_added_immediate",
            sop={"delay_minutes": 0},
            customers=[{"first_added_event": {"trace_id": "trace_immediate_dual"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_immediate_dual_scope")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(repo.tasks[0]["sop_pack_id"], "s10_new_customer_opening")
        self.assertEqual(selector.calls[0]["candidate_packs"][0]["id"], "s10_new_customer_opening")

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

    async def test_first_added_event_send_once_duplicate_does_not_send_again(self) -> None:
        repo = _Repo()
        repo.tasks.append(
            {
                "id": "existing_task",
                "event_id": "evt_existing",
                "idempotency_key": "existing_idem",
                "send_once_key": "sop_pack:opening|corp:ww943af61cd5d2afe4|external:ext_user",
                "customer_id": "ext_user",
                "external_userid": "ext_user",
                "corp_id": "ww943af61cd5d2afe4",
                "user_id": "7294",
                "wechat": "CS001",
                "sop_pack_id": "opening",
                "sop_category": "opening",
                "status": "pending",
                "error": "",
            }
        )
        client = _OutreachClient(messages=[])
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "race selected same pack"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_duplicate_send_once",
            event_type="sop_friend_added_schedule_batch",
            sop={"delay_minutes": 10},
            customers=[{"first_added_event": {"trace_id": "trace_duplicate"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_duplicate_send_once")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(client.send_calls, [])
        self.assertEqual(repo.tasks[-1]["status"], "skipped_send_once_duplicate")
        self.assertEqual(repo.tasks[-1]["event_id"], "evt_duplicate_send_once")
        self.assertIn("existing_task", repo.tasks[-1]["error"])

    def test_repository_send_once_key_creates_audit_duplicate_task(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "ai_paths.db"))
            store.initialize()
            repo = AppRepository(store)
            repo.create_sop_event({"event_id": "evt_first", "event_type": "sop_friend_added_schedule_batch"})
            repo.create_sop_event({"event_id": "evt_second", "event_type": "sop_friend_added_schedule_batch"})
            first = repo.create_sop_send_task(
                event_id="evt_first",
                idempotency_key="idem_first",
                send_once_key="sop_pack:opening|corp:ww|external:ext",
                customer_id="ext",
                external_userid="ext",
                corp_id="ww",
                user_id="7294",
                wechat="CS001",
                sop_pack_id="opening",
                sop_pack_name="新客开场",
                sop_category="opening",
                trigger_source="sop_event",
                reply_messages=[{"type": "text", "content": {"text": "开场"}}],
                status="pending",
            )
            second = repo.create_sop_send_task(
                event_id="evt_second",
                idempotency_key="idem_second",
                send_once_key="sop_pack:opening|corp:ww|external:ext",
                customer_id="ext",
                external_userid="ext",
                corp_id="ww",
                user_id="7294",
                wechat="CS001",
                sop_pack_id="opening",
                sop_pack_name="新客开场",
                sop_category="opening",
                trigger_source="sop_event",
                reply_messages=[{"type": "text", "content": {"text": "开场"}}],
                status="pending",
            )

            self.assertTrue(first["created"])
            self.assertEqual(first["status"], "pending")
            self.assertTrue(second["created"])
            self.assertEqual(second["status"], "skipped_send_once_duplicate")
            self.assertEqual(second["dedupe_reason"], "send_once_key")
            self.assertEqual(second["duplicate_of_task_id"], first["id"])
            self.assertEqual(second["send_once_key"], "")
            self.assertEqual(repo.list_sop_send_tasks_for_event("evt_second")[0]["status"], "skipped_send_once_duplicate")

    def test_repository_wechat_identity_lookup_is_case_insensitive(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "ai_paths.db"))
            store.initialize()
            repo = AppRepository(store)
            with store.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO conversations
                        (id, customer_id, external_userid, corp_id, user_id, wechat, title, created_at, updated_at)
                    VALUES
                        ('conv_case', 'customer_case', 'external_case', 'ww943af61cd5d2afe4', '7294', 'CS001', '', '2026-07-02T00:00:00+00:00', '2026-07-02T00:00:00+00:00')
                    """
                )

            identity = repo.find_sop_event_identity(wechat="cs001")

            self.assertEqual(identity["corp_id"], "ww943af61cd5d2afe4")
            self.assertEqual(identity["user_id"], "7294")
            self.assertEqual(identity["wechat"], "CS001")
            self.assertEqual(identity["identity_source"], "conversations")

    def test_repository_sent_sop_lists_respect_sent_before(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "ai_paths.db"))
            store.initialize()
            repo = AppRepository(store)
            repo.create_sop_event({"event_id": "evt_before", "event_type": "chat_gate"})
            repo.create_sop_event({"event_id": "evt_after", "event_type": "chat_gate"})
            before = repo.create_sop_send_task(
                event_id="evt_before",
                idempotency_key="idem_before",
                customer_id="ext",
                external_userid="ext",
                corp_id="ww",
                user_id="7294",
                wechat="CS001",
                sop_pack_id="before_pack",
                sop_pack_name="before_pack",
                sop_category="before_category",
                trigger_source="chat_gate",
                reply_messages=[],
                status="pending",
            )
            after = repo.create_sop_send_task(
                event_id="evt_after",
                idempotency_key="idem_after",
                customer_id="ext",
                external_userid="ext",
                corp_id="ww",
                user_id="7294",
                wechat="CS001",
                sop_pack_id="after_pack",
                sop_pack_name="after_pack",
                sop_category="after_category",
                trigger_source="chat_gate",
                reply_messages=[],
                status="pending",
            )
            repo.update_sop_send_task(before["id"], status="sent", sent_at="2026-07-02T06:00:00+00:00")
            repo.update_sop_send_task(after["id"], status="sent", sent_at="2026-07-02T07:00:00+00:00")

            self.assertEqual(
                repo.list_sent_sop_pack_ids_for_customer(
                    customer_id="ext",
                    external_userid="ext",
                    sent_before="2026-07-02T06:30:00+00:00",
                ),
                ["before_pack"],
            )
            self.assertEqual(
                repo.list_sent_sop_categories_for_customer(
                    customer_id="ext",
                    external_userid="ext",
                    sent_before="2026-07-02T06:30:00+00:00",
                ),
                ["before_category"],
            )

    async def test_event_judge_prompt_defaults_to_platform_sop_unless_conflict_or_overlap(self) -> None:
        model = _PromptCaptureModel({"send_sop": True, "sop_pack_id": "effect_followup", "need_ai_reply": False, "reason": "ok"})
        service = SopExecutionService(repository=_Repo(), sop_reply_pack_service=_PackService(), model_client=model)

        result = await service._judge_event_sop(
            {
                "mode": "first_add_flow",
                "event": {"event_type": "sop_friend_added_schedule_batch", "delay_minutes": 30},
                "recent_conversation": [{"role": "staff", "content": "前序破冰"}],
                "candidate_sops": [{"id": "effect_followup", "sop_category": "effect_case"}],
                "completed_sop_pack_ids": [],
                "completed_sop_categories": [],
            }
        )

        self.assertTrue(result["send_sop"])
        system_prompt = str(model.messages[0]["content"])
        self.assertIn("默认按照平台 SOP 全流程发送", system_prompt)
        self.assertIn("明确拒发理由只有四类", system_prompt)
        self.assertIn("正在和销冠连续对话", system_prompt)
        self.assertIn("冲突", system_prompt)
        self.assertIn("严重重合", system_prompt)
        self.assertIn("客户未回复、staff-only 连续 SOP 消息", system_prompt)
        self.assertNotIn("不因为事件时间到了就机械发送", system_prompt)


def _service(
    repo: Any,
    client: Any,
    selector: Any | None = None,
    pack_service: Any | None = None,
    default_identity: dict[str, Any] | None = None,
) -> SopEventService:
    return SopEventService(
        repository=repo,
        sop_reply_pack_service=pack_service or _PackService(),
        outreach_send_client=client,
        sop_execution_service=selector or _Selector({"send_sop": False, "reason": "default reject"}),
        default_identity=default_identity,
    )


def _base_payload(
    *,
    event_id: str,
    event_type: str,
    sop: dict[str, Any],
    customers: list[dict[str, Any]],
    created_at: str = "",
) -> dict[str, Any]:
    payload = {
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
    if created_at:
        payload["created_at"] = created_at
    return payload


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


class _ImmediatePackService:
    def load(self) -> dict[str, Any]:
        return {
            "packs": [
                {
                    "id": "immediate_opening",
                    "enabled": True,
                    "scope": "event_first_add",
                    "sop_category": "opening",
                    "name": "即时开场",
                    "purpose": "首次加微即时开场",
                    "order": 1,
                    "send_once": True,
                    "event_type": "sop_friend_added_immediate",
                    "delay_minutes": 0,
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "即时开场话术"}}],
                },
                {
                    "id": "future_schedule_should_not_match",
                    "enabled": True,
                    "scope": "event_first_add",
                    "sop_category": "intro",
                    "name": "后续定时包",
                    "purpose": "不应被即时事件选中",
                    "order": 2,
                    "send_once": True,
                    "event_type": "sop_friend_added_schedule_batch",
                    "delay_minutes": 1,
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "后续话术"}}],
                },
            ]
        }


class _DualScopeOpeningPackService:
    def load(self) -> dict[str, Any]:
        return {
            "packs": [
                {
                    "id": "s10_new_customer_opening",
                    "enabled": True,
                    "scope": "chat_gate",
                    "scopes": ["chat_gate", "event_first_add"],
                    "sop_category": "s10_new_customer_opening",
                    "name": "新客破冰",
                    "purpose": "首次加微立即破冰",
                    "order": 10,
                    "send_once": True,
                    "event_type": "",
                    "delay_minutes": 0,
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "新客破冰话术"}}],
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


class _PromptCaptureModel:
    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.messages: list[dict[str, Any]] = []
        self.kwargs: dict[str, Any] = {}
        self.last_usage: dict[str, Any] = {}

    async def chat_json(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.messages = messages
        self.kwargs = kwargs
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
        self.identity_lookup: dict[str, str] = {}

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

    def list_sent_sop_pack_ids_for_customer(
        self,
        *,
        customer_id: str,
        external_userid: str,
        sent_before: str = "",
    ) -> list[str]:
        return sorted(self.sent_ids)

    def list_sent_sop_categories_for_customer(
        self,
        *,
        customer_id: str,
        external_userid: str,
        sent_before: str = "",
    ) -> list[str]:
        return sorted(self.sent_categories)

    def find_sop_event_identity(self, *, customer_id: str = "", external_userid: str = "", wechat: str = "") -> dict[str, str]:
        return dict(self.identity_lookup)

    def create_sop_send_task(self, **kwargs: Any) -> dict[str, Any]:
        for task in self.tasks:
            if task["idempotency_key"] == kwargs["idempotency_key"]:
                existing = dict(task)
                existing["created"] = False
                return existing
        send_once_key = str(kwargs.get("send_once_key") or "")
        if send_once_key:
            for task in self.tasks:
                if task.get("send_once_key") == send_once_key and task.get("status") in {"pending", "sent"}:
                    audit = {
                        "id": f"task_{len(self.tasks) + 1}",
                        **kwargs,
                        "send_once_key": "",
                        "status": "skipped_send_once_duplicate",
                        "error": f"duplicate_sop_pack_task:{task['id']}",
                        "created": True,
                        "dedupe_reason": "send_once_key",
                        "duplicate_of_task_id": task["id"],
                    }
                    self.tasks.append(audit)
                    return dict(audit)
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
