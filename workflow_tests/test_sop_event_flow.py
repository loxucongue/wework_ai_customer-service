from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from tempfile import TemporaryDirectory

from app.schemas import ChatRequest
from app.prompts.global_contract import GLOBAL_BUSINESS_RHYTHM_CONTRACT, GLOBAL_STRUCTURED_NODE_CONTRACT
from app.services.sop_event_service import SopEventService
from app.services.sop_execution_service import SOP_EVENT_SYSTEM_PROMPT, SopExecutionService, is_platform_auto_opening_message
from app.services.sop_message_sanitizer import apply_sop_text_adjustments, sanitize_sop_reply_messages
from app.services.sop_reply_pack_service import SopReplyPackService
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

    async def test_event_identity_uses_default_when_lookup_fails(self) -> None:
        repo = _Repo()
        repo.identity_lookup_error = RuntimeError("lookup down")
        client = _OutreachClient(messages=[])
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
            "event_id": "evt_identity_lookup_error",
            "event_type": "sop_friend_added_schedule_batch",
            "sop": {"delay_minutes": 1},
            "customers": [{"customer": {"external_userid": "ext_user"}}],
        }

        repo.create_sop_event(payload)
        result = await service.process_event("evt_identity_lookup_error")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(client.fetch_calls[0]["corp_id"], "ww943af61cd5d2afe4")
        self.assertEqual(client.fetch_calls[0]["user_id"], "test2")
        self.assertEqual(client.fetch_calls[0]["wechat"], "auto-3a03ca3ecaae3ae2")
        self.assertIn("identity_lookup_error", repo.tasks[0]["send_payload"]["identity"])

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

    async def test_first_added_event_uses_empty_history_when_conversation_fetch_fails(self) -> None:
        repo = _Repo()
        client = _OutreachClient(fetch_result={"status": "failed", "error": "http_status:404"})
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "send opening"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_first_fetch_failed",
            event_type="sop_friend_added_schedule_batch",
            sop={"delay_minutes": 1},
            customers=[{"first_added_event": {"trace_id": "trace_fetch_failed"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_first_fetch_failed")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(repo.tasks[0]["send_payload"]["conversation_fetch"]["status"], "fallback_empty")
        self.assertEqual(selector.calls[0]["conversation_messages"], [])

    async def test_event_skips_inactive_customer_during_quiet_hours(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "direction": "customer",
                    "content": "我晚点再看",
                    "msgtime": "2026-07-10T17:29:00+00:00",
                }
            ]
        )
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "should not run"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_quiet_inactive",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-10T18:00:00+00:00",
            sop={"delay_minutes": 3},
            customers=[{"first_added_event": {"trace_id": "trace_quiet_inactive"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_quiet_inactive")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_quiet_hours_inactive")
        self.assertEqual(repo.tasks[0]["send_payload"]["quiet_hours"]["inactivity_minutes"], 31)
        self.assertEqual(repo.tasks[0]["send_payload"]["quiet_hours"]["timezone"], "Asia/Shanghai")
        self.assertEqual(selector.calls, [])
        self.assertEqual(client.send_calls, [])

    async def test_event_allows_recent_customer_message_during_quiet_hours(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "direction": "customer",
                    "content": "我还在看",
                    "msgtime": "2026-07-10T17:31:00+00:00",
                }
            ]
        )
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "recent customer active"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_quiet_active",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-10T18:00:00+00:00",
            sop={"delay_minutes": 3},
            customers=[{"first_added_event": {"trace_id": "trace_quiet_active"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_quiet_active")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(len(selector.calls), 1)

    async def test_event_allows_inactive_customer_outside_quiet_hours(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "direction": "customer",
                    "content": "我晚点再看",
                    "msgtime": "2026-07-11T00:00:00+00:00",
                }
            ]
        )
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "daytime followup"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_daytime_inactive",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-11T02:00:00+00:00",
            sop={"delay_minutes": 3},
            customers=[{"first_added_event": {"trace_id": "trace_daytime_inactive"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_daytime_inactive")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(len(selector.calls), 1)

    async def test_event_applies_model_text_adjustment_before_sending(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        selector = _Selector(
            {
                "send_sop": True,
                "sop_pack_id": "opening",
                "reason": "opening wording adjusted for the current context",
                "text_adjustments": [{"order": 1, "text": "您好，刚加上您，我简单和您说下这次活动。"}],
            }
        )
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_text_adjustment",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-11T02:00:00+00:00",
            sop={"delay_minutes": 3},
            customers=[{"first_added_event": {"trace_id": "trace_text_adjustment"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_text_adjustment")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["reply_messages"][0]["content"]["text"], "您好，刚加上您，我简单和您说下这次活动。")
        self.assertEqual(repo.tasks[0]["send_payload"]["message_adjustment"]["applied_orders"], [1])

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

    async def test_event_payment_collection_amount_uses_recent_participant_count(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[{"direction": "customer", "content": "我带两个朋友一起过去"}])
        selector = _Selector({"send_sop": True, "sop_pack_id": "deposit_pack", "reason": "send deposit"})
        service = _service(repo=repo, client=client, selector=selector, pack_service=_DepositPackService())
        payload = _base_payload(
            event_id="evt_group_payment",
            event_type="sop_friend_added_schedule_batch",
            sop={"delay_minutes": 70},
            customers=[{"first_added_event": {"trace_id": "trace_group"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_group_payment")

        self.assertEqual(result["status"], "processed")
        messages = repo.tasks[0]["reply_messages"]
        self.assertEqual(
            messages[0]["content"]["text"],
            "可以，3位一共30元预约金，每位10元，用来锁活动名额，到店抵扣；未做或不满意可退，实际按付款记录核对。",
        )
        self.assertEqual(messages[1]["content"]["amount"], 30)

    async def test_event_payment_collection_over_four_people_asks_confirmation(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[{"direction": "customer", "content": "我带四个朋友一起过去"}])
        selector = _Selector({"send_sop": True, "sop_pack_id": "deposit_pack", "reason": "send deposit"})
        service = _service(repo=repo, client=client, selector=selector, pack_service=_DepositPackService())
        payload = _base_payload(
            event_id="evt_group_payment_over_limit",
            event_type="sop_friend_added_schedule_batch",
            sop={"delay_minutes": 70},
            customers=[{"first_added_event": {"trace_id": "trace_group_over_limit"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_group_payment_over_limit")

        self.assertEqual(result["status"], "processed")
        messages = repo.tasks[0]["reply_messages"]
        self.assertTrue(all(item["type"] != "payment_collection" for item in messages))
        self.assertIn("一共几位到店", messages[0]["content"]["text"])

    async def test_sop_message_sanitizer_handles_string_content_and_over_limit(self) -> None:
        messages, summary = sanitize_sop_reply_messages(
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": "\u7ed9\u60a8\u53d1\u9884\u7ea6\u91d1\u5165\u53e3\uff0c10\u5143\u5230\u5e97\u62b5\u6263\uff0c\u4e0d\u505a\u9000\u8fd810\u5143\u3002",
                },
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ],
            state={"conversation_history": ["\u7528\u6237: \u6211\u5e26\u56db\u4e2a\u670b\u53cb\u4e00\u8d77\u62a5\u540d"]},
        )

        self.assertEqual(summary["payment_suppressed"], "over_limit_participants")
        self.assertTrue(all(item["type"] != "payment_collection" for item in messages))
        self.assertTrue(any("\u4e00\u5171\u51e0\u4f4d\u5230\u5e97" in item["content"]["text"] for item in messages))

    def test_sop_text_adjustment_only_rewrites_existing_text_with_same_numeric_facts(self) -> None:
        messages, summary = apply_sop_text_adjustments(
            [
                {"type": "text", "order": 1, "content": {"text": "活动268元，先付10元预约金。"}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ],
            [
                {"order": 1, "text": "这次活动是268元，10元先把名额留住。"},
                {"order": 2, "text": "不能改卡片"},
            ],
        )

        self.assertEqual(messages[0]["content"]["text"], "这次活动是268元，10元先把名额留住。")
        self.assertEqual(messages[1]["type"], "payment_collection")
        self.assertEqual(summary["applied_orders"], [1])
        self.assertEqual(summary["rejected"][0]["reason"], "not_existing_text_message")

    def test_sop_text_adjustment_rejects_changed_numeric_facts(self) -> None:
        messages, summary = apply_sop_text_adjustments(
            [{"type": "text", "order": 1, "content": {"text": "活动268元，先付10元预约金。"}}],
            [{"order": 1, "text": "活动198元，先付20元预约金。"}],
        )

        self.assertEqual(messages[0]["content"]["text"], "活动268元，先付10元预约金。")
        self.assertEqual(summary["applied_orders"], [])
        self.assertEqual(summary["rejected"][0]["reason"], "numeric_facts_changed")

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

    def test_current_sop_reply_pack_config_audit_has_no_errors(self) -> None:
        service = SopReplyPackService(SimpleNamespace(sop_reply_packs_path=Path("config/sop_reply_packs.json")))
        config = service.load()

        self.assertEqual(config["audit"]["status"], "ok")
        self.assertEqual(config["audit"]["error_count"], 0)
        opening = next(item for item in config["packs"] if item["id"] == "s10_new_customer_opening")
        self.assertTrue(opening["enabled"])
        self.assertIn("event_first_add", opening["scopes"])
        self.assertEqual(opening["event_type"], "")
        self.assertEqual(opening["delay_minutes"], 0)

    async def test_event_judge_prompt_defaults_to_platform_sop_unless_conflict_or_overlap(self) -> None:
        model = _PromptCaptureModel(
            {
                "send_sop": True,
                "sop_pack_id": "effect_followup",
                "need_ai_reply": False,
                "reason": "ok",
                "text_adjustments": [{"order": 1, "text": "更自然的效果铺垫"}],
            }
        )
        service = SopExecutionService(repository=_Repo(), sop_reply_pack_service=_PackService(), model_client=model)

        result = await service.evaluate_event_suggestion(
            payload={"event_type": "sop_friend_added_schedule_batch"},
            customer={},
            identity={"customer_id": "customer", "external_userid": "external"},
            event_type="sop_friend_added_schedule_batch",
            conversation_messages=[{"direction": "staff", "content": "前序破冰"}],
            candidate_packs=[
                {
                    "id": "effect_followup",
                    "sop_category": "effect_case",
                    "reply_messages": [
                        {
                            "type": "text",
                            "order": 1,
                            "content": {"text": "这是完整的效果案例铺垫原文，用于确认模型不会只根据截断摘要改写。"},
                        },
                        {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
                    ],
                }
            ],
        )

        self.assertTrue(result["send_sop"])
        self.assertEqual(result["text_adjustments"], [{"order": 1, "text": "更自然的效果铺垫"}])
        system_prompt = str(model.messages[0]["content"])
        user_prompt = str(model.messages[1]["content"])
        self.assertEqual(system_prompt, SOP_EVENT_SYSTEM_PROMPT)
        self.assertIn(GLOBAL_STRUCTURED_NODE_CONTRACT, system_prompt)
        self.assertIn(GLOBAL_BUSINESS_RHYTHM_CONTRACT, system_prompt)
        self.assertIn("先做拒发审查", system_prompt)
        self.assertIn("客户当前立场与候选包的核心行动相反", system_prompt)
        self.assertIn("阶段目标 + 核心事实 + 行动目标", system_prompt)
        self.assertIn("企业微信一对一聊天", system_prompt)
        self.assertIn("尊敬的客户/尊敬的顾客", system_prompt)
        self.assertIn("您好，温馨提醒", system_prompt)
        self.assertIn("销冠正在连续承接", system_prompt)
        self.assertIn("冲突", system_prompt)
        self.assertIn("严重重合", system_prompt)
        self.assertIn("客户未回复、只有 staff 消息", system_prompt)
        self.assertIn("text_adjustments", system_prompt)
        self.assertIn("payment_collection`、`store_address`、`image`、`video`", system_prompt)
        self.assertIn("editable_text_messages", system_prompt)
        self.assertIn("readonly_messages", system_prompt)
        self.assertIn("这是完整的效果案例铺垫原文，用于确认模型不会只根据截断摘要改写。", user_prompt)
        self.assertIn('"amount":10', user_prompt)
        self.assertNotIn("不因为事件时间到了就机械发送", system_prompt)

    async def test_event_judge_keeps_selector_contract_for_direct_prompt_inspection(self) -> None:
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
        self.assertIn("先做拒发审查", system_prompt)
        self.assertIn("客户当前立场与候选包的核心行动相反", system_prompt)
        self.assertIn("阶段目标 + 核心事实 + 行动目标", system_prompt)
        self.assertIn("企业微信一对一聊天", system_prompt)
        self.assertIn("尊敬的客户/尊敬的顾客", system_prompt)
        self.assertIn("您好，温馨提醒", system_prompt)
        self.assertIn("销冠正在连续承接", system_prompt)
        self.assertIn("冲突", system_prompt)
        self.assertIn("严重重合", system_prompt)
        self.assertIn("客户未回复、只有 staff 消息", system_prompt)
        self.assertIn("text_adjustments", system_prompt)
        self.assertIn("payment_collection`、`store_address`、`image`、`video`", system_prompt)
        self.assertNotIn("不因为事件时间到了就机械发送", system_prompt)

    async def test_chat_gate_sends_configured_opening_for_platform_auto_message(self) -> None:
        model = _PromptCaptureModel({"send_sop": True, "sop_pack_id": "chat_opening", "need_ai_reply": False})
        repository = _Repo()
        service = SopExecutionService(repository=repository, sop_reply_pack_service=_DualScopeOpeningPackService(), model_client=model)
        request = ChatRequest(
            content="我已经添加了你，现在我们可以开始聊天了。",
            customer_id="customer",
            corp_id="corp",
            external_userid="ext",
            conversation_history=["用户: 门店在哪？"],
        )

        result = await service.evaluate_chat_gate(request, request_id="req_auto_opening", request_context={})

        self.assertEqual(result["mode"], "platform_auto_opening_sop")
        self.assertTrue(result["send_sop"])
        self.assertFalse(result["need_ai_reply"])
        self.assertEqual(result["reason"], "platform_auto_opening_first_add_sop")
        self.assertEqual(result["sop_pack_id"], "s10_new_customer_opening")
        self.assertEqual(result["reply_messages"][0]["content"]["text"], "新客破冰话术")
        self.assertEqual(result["task"]["trigger_source"], "platform_auto_opening")
        self.assertEqual(model.messages, [])

        duplicate = await service.evaluate_chat_gate(request, request_id="req_auto_opening_duplicate", request_context={})
        self.assertEqual(duplicate["mode"], "platform_auto_opening_duplicate")
        self.assertFalse(duplicate["send_sop"])
        self.assertEqual(len(repository.tasks), 2)

    async def test_chat_gate_exposes_authoritative_sop_progress_without_message_bodies(self) -> None:
        class _ProgressPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "s10_new_customer_opening",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "opening",
                            "name": "新客破冰",
                            "purpose": "首次承接",
                            "order": 10,
                            "triggers": ["首次咨询"],
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "已发送的开场原文"}}
                            ],
                        },
                        {
                            "id": "s10_need_and_case",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "effect_case",
                            "name": "需求和案例",
                            "purpose": "承接客户斑点情况和效果信心",
                            "order": 20,
                            "triggers": ["需求承接"],
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "未发送的静态话术原文"}}
                            ],
                        },
                    ]
                }

        repository = _Repo()
        repository.sent_ids.add("s10_new_customer_opening")
        repository.sent_categories.add("opening")
        model = _PromptCaptureModel(
            {"send_sop": False, "sop_pack_id": "", "need_ai_reply": True, "reason": "当前问题由普通回复承接"}
        )
        service = SopExecutionService(
            repository=repository,
            sop_reply_pack_service=_ProgressPackService(),
            model_client=model,
        )
        request = ChatRequest(
            content="我在朝阳区",
            customer_id="customer",
            corp_id="corp",
            external_userid="ext",
        )

        result = await service.evaluate_chat_gate(request, request_id="req_progress", request_context={})

        evidence = result["sop_progress_evidence"]
        self.assertEqual(evidence["completed_pack_ids"], ["s10_new_customer_opening"])
        self.assertEqual(evidence["completed_categories"], ["opening"])
        self.assertEqual(evidence["unfinished_sops"][0]["id"], "s10_need_and_case")
        self.assertEqual(evidence["unfinished_sops"][0]["purpose"], "承接客户斑点情况和效果信心")
        self.assertNotIn("reply_messages", evidence["unfinished_sops"][0])
        self.assertNotIn("未发送的静态话术原文", str(evidence))

    async def test_chat_gate_prompt_requires_actual_message_coverage(self) -> None:
        class _ObjectionPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "s10_objection_resolution",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "s10_objection_resolution",
                            "name": "收费与预约金顾虑处理",
                            "purpose": (
                                "仅用于客户当前主要顾虑是套路、隐形消费、乱收费、费用规则、"
                                "预约金抵扣/可退或活动价格真实性时。效果真实性不适用。"
                            ),
                            "order": 40,
                            "triggers": ["first_objection"],
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "我们是明码标价，没有强制消费。"}},
                                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
                            ],
                        }
                    ]
                }

        repository = _Repo()
        model = _PromptCaptureModel(
            {"send_sop": False, "sop_pack_id": "", "need_ai_reply": True, "reason": "收费包不覆盖效果真实性"}
        )
        service = SopExecutionService(
            repository=repository,
            sop_reply_pack_service=_ObjectionPackService(),
            model_client=model,
        )
        request = ChatRequest(
            content="真有这么好的效果？",
            customer_id="customer",
            corp_id="corp",
            external_userid="ext",
            conversation_history=["小贝: 已经发过同类效果图给您参考"],
        )

        result = await service.evaluate_chat_gate(request, request_id="req_effect_objection", request_context={})

        self.assertEqual(result["mode"], "no_sop_selected")
        self.assertTrue(result["need_ai_reply"])
        system_prompt = model.messages[0]["content"]
        user_prompt = model.messages[1]["content"]
        self.assertIn("reply_messages 摘要能否真实回答当前问题", system_prompt)
        self.assertIn("效果真实性、怕没效果、反黑、做坏、伤肤", system_prompt)
        self.assertIn("收费、预约金、隐形消费或活动价格规则", system_prompt)
        self.assertIn("收费与预约金顾虑处理", user_prompt)
        self.assertIn("payment_collection", user_prompt)

    def test_objection_resolution_pack_metadata_excludes_effect_objection(self) -> None:
        import json

        payload = json.loads(Path("config/sop_reply_packs.json").read_text(encoding="utf-8"))
        pack = next(item for item in payload["packs"] if item["id"] == "s10_objection_resolution")

        self.assertEqual(pack["name"], "收费与预约金顾虑处理")
        self.assertIn("隐形消费", pack["purpose"])
        self.assertIn("预约金抵扣", pack["purpose"])
        self.assertIn("效果真实性", pack["purpose"])
        self.assertIn("不适用", pack["purpose"])
        self.assertNotIn("客户担心效果、套路", pack["purpose"])
    def test_platform_auto_opening_matcher_is_narrow(self) -> None:
        self.assertTrue(is_platform_auto_opening_message("我已经添加了你，现在我们可以开始聊天了。"))
        self.assertTrue(is_platform_auto_opening_message("我已经添加了你 现在可以开始聊天了"))
        self.assertFalse(is_platform_auto_opening_message("你好"))
        self.assertFalse(is_platform_auto_opening_message("门店在哪？"))
        self.assertFalse(is_platform_auto_opening_message("这家地址发我"))


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


class _DepositPackService:
    def load(self) -> dict[str, Any]:
        return {
            "packs": [
                {
                    "id": "deposit_pack",
                    "enabled": True,
                    "scope": "event_first_add",
                    "sop_category": "deposit_push",
                    "name": "收款推进",
                    "purpose": "发预约金",
                    "order": 10,
                    "send_once": True,
                    "event_type": "sop_friend_added_schedule_batch",
                    "delay_minutes": 70,
                    "reply_messages": [
                        {"type": "text", "order": 1, "content": {"text": "我先给您发10元预约金入口，锁活动名额。"}},
                        {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
                    ],
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
    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        fetch_result: dict[str, Any] | None = None,
    ) -> None:
        self.messages = messages or []
        self.fetch_result = fetch_result
        self.fetch_calls: list[dict[str, Any]] = []
        self.send_calls: list[dict[str, Any]] = []

    async def fetch_conversation(self, **kwargs: Any) -> dict[str, Any]:
        self.fetch_calls.append(kwargs)
        if self.fetch_result is not None:
            result = dict(self.fetch_result)
            result.setdefault("request", kwargs)
            return result
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
        self.identity_lookup_error: Exception | None = None

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
        if self.identity_lookup_error:
            raise self.identity_lookup_error
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
