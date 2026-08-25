from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from tempfile import TemporaryDirectory

from app.schemas import ChatRequest
from app.prompts.global_contract import GLOBAL_BUSINESS_RHYTHM_CONTRACT, GLOBAL_STRUCTURED_NODE_CONTRACT
from app.services.sop_event_decision import (
    build_event_ai_reply_policy,
    combine_selected_pack_messages,
    normalize_event_decision,
)
from app.services.sop_event_service import SopEventService, _normalize_quiet_backlog_fusion_messages
from app.services.sop_execution_service import (
    SopExecutionService,
    _event_selector_input,
    _chat_gate_output_violations,
    _sop_event_system_prompt,
    first_add_candidate_packs,
    is_platform_auto_opening_message,
)
from app.services.sop_message_sanitizer import apply_sop_text_adjustments, sanitize_sop_reply_messages
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.memory_store import CustomerMemoryStore
from app.services.customer_scope import build_customer_scope
from app.services.storage import AppRepository, SQLiteStore


class SopEventFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_chat_gate_multi_person_payment_rejects_assistant_only_evidence(self) -> None:
        selector_input = {
            "mainline": {"stages": [{"id": "deposit_decision"}]},
            "precision_qa_index": [],
            "conversation_evidence": [
                {"message_ref": "chat_1", "direction": "assistant", "content": "两位一共20元"},
                {"message_ref": "current_message", "direction": "customer", "content": "手和脸都可以做吗"},
            ],
            "unfinished_sops": [
                {
                    "id": "two_people_pack",
                    "mainline_stage": "deposit_decision",
                    "payment_collection_gate": {"amounts": [20]},
                }
            ],
        }
        output = {
            "route": "sop_only",
            "coverage": "exact",
            "sop_pack_id": "two_people_pack",
            "resume_stage": "deposit_decision",
            "party_size_evidence": {
                "party_size": 2,
                "customer_evidence_ref": "chat_1",
                "evidence_quote": "两位一共20元",
            },
        }

        violations = _chat_gate_output_violations(output, selector_input)

        self.assertIn("multi_person_payment_requires_customer_evidence_ref", violations)

    def test_chat_gate_multi_person_payment_accepts_exact_customer_reference(self) -> None:
        selector_input = {
            "mainline": {"stages": [{"id": "deposit_decision"}]},
            "precision_qa_index": [],
            "conversation_evidence": [
                {"message_ref": "current_message", "direction": "customer", "content": "我们两个人一起过去"},
            ],
            "unfinished_sops": [
                {
                    "id": "two_people_pack",
                    "mainline_stage": "deposit_decision",
                    "payment_collection_gate": {"amounts": [20]},
                }
            ],
        }
        output = {
            "route": "sop_only",
            "coverage": "exact",
            "sop_pack_id": "two_people_pack",
            "resume_stage": "deposit_decision",
            "party_size_evidence": {
                "party_size": 2,
                "customer_evidence_ref": "current_message",
                "evidence_quote": "我们两个人",
            },
        }

        self.assertEqual(_chat_gate_output_violations(output, selector_input), [])

    def test_chat_gate_repeated_soft_refusal_repairs_activity_or_payment_pack(self) -> None:
        selector_input = {
            "mainline": {"stages": [{"id": "activity_and_price"}, {"id": "deposit_decision"}]},
            "precision_qa_index": [],
            "conversation_evidence": [
                {"message_ref": "chat_1", "direction": "customer", "content": "我考虑一下"},
                {"message_ref": "current_message", "direction": "customer", "content": "不急，我想多看看，再做决定"},
            ],
            "customer_stance_evidence": {
                "soft_refusal_quote_count": 2,
                "repeated_soft_refusal": True,
                "soft_refusal_evidence": [
                    {"message_ref": "chat_1", "quote": "我考虑一下"},
                    {"message_ref": "current_message", "quote": "不急，我想多看看，再做决定"},
                ],
            },
            "unfinished_sops": [
                {
                    "id": "s10_activity_intro",
                    "mainline_stage": "activity_and_price",
                    "sop_category": "activity_intro",
                    "payment_collection_gate": {"has_payment_collection": False, "status": "not_required"},
                },
                {
                    "id": "deposit_pack",
                    "mainline_stage": "deposit_decision",
                    "sop_category": "deposit_push",
                    "payment_collection_gate": {"has_payment_collection": True, "status": "supported", "amounts": [10]},
                },
            ],
        }

        activity_output = {
            "route": "sop_only",
            "coverage": "exact",
            "sop_pack_id": "s10_activity_intro",
            "resume_stage": "activity_and_price",
        }
        payment_output = {
            "route": "sop_only",
            "coverage": "exact",
            "sop_pack_id": "deposit_pack",
            "resume_stage": "deposit_decision",
        }

        self.assertIn(
            "gate_pressure_conflict:repeated_soft_refusal_requires_ai_only",
            _chat_gate_output_violations(activity_output, selector_input),
        )
        self.assertIn(
            "gate_pressure_conflict:repeated_soft_refusal_requires_ai_only",
            _chat_gate_output_violations(payment_output, selector_input),
        )

    def test_chat_gate_first_soft_refusal_blocks_payment_card_but_not_all_sop(self) -> None:
        selector_input = {
            "mainline": {"stages": [{"id": "need_and_case"}, {"id": "deposit_decision"}]},
            "precision_qa_index": [],
            "conversation_evidence": [
                {"message_ref": "current_message", "direction": "customer", "content": "我考虑一下"},
            ],
            "customer_stance_evidence": {
                "soft_refusal_quote_count": 1,
                "repeated_soft_refusal": False,
                "soft_refusal_evidence": [{"message_ref": "current_message", "quote": "我考虑一下"}],
            },
            "unfinished_sops": [
                {
                    "id": "case_pack",
                    "mainline_stage": "need_and_case",
                    "sop_category": "effect_case",
                    "payment_collection_gate": {"has_payment_collection": False, "status": "not_required"},
                },
                {
                    "id": "deposit_pack",
                    "mainline_stage": "deposit_decision",
                    "sop_category": "deposit_push",
                    "payment_collection_gate": {"has_payment_collection": True, "status": "supported", "amounts": [10]},
                },
            ],
        }

        case_output = {
            "route": "sop_only",
            "coverage": "exact",
            "sop_pack_id": "case_pack",
            "resume_stage": "need_and_case",
        }
        payment_output = {
            "route": "sop_only",
            "coverage": "exact",
            "sop_pack_id": "deposit_pack",
            "resume_stage": "deposit_decision",
        }

        self.assertEqual(_chat_gate_output_violations(case_output, selector_input), [])
        self.assertIn(
            "gate_pressure_conflict:first_soft_refusal_must_not_send_payment_card",
            _chat_gate_output_violations(payment_output, selector_input),
        )

    def test_chat_gate_payment_collection_pack_cannot_sop_only(self) -> None:
        selector_input = {
            "mainline": {"stages": [{"id": "deposit_decision"}]},
            "precision_qa_index": [],
            "conversation_evidence": [
                {"message_ref": "current_message", "direction": "customer", "content": "检测收费吗，是三百多全包。"},
            ],
            "unfinished_sops": [
                {
                    "id": "charge_pack",
                    "mainline_stage": "deposit_decision",
                    "sop_category": "deposit_push",
                    "payment_collection_gate": {"has_payment_collection": True, "status": "supported", "amounts": [10]},
                    "reply_messages": [
                        {"type": "text", "order": 1, "content": {"text": "明码标价"}},
                        {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
                    ],
                },
            ],
        }
        sop_only_output = {
            "route": "sop_only",
            "coverage": "exact",
            "sop_pack_id": "charge_pack",
            "resume_stage": "deposit_decision",
        }
        ai_then_sop_output = {
            "route": "ai_then_sop",
            "coverage": "partial",
            "sop_pack_id": "charge_pack",
            "resume_stage": "deposit_decision",
        }

        self.assertIn(
            "gate_payment_conflict:payment_collection_pack_cannot_sop_only",
            _chat_gate_output_violations(sop_only_output, selector_input),
        )
        self.assertNotIn(
            "gate_payment_conflict:payment_collection_pack_cannot_sop_only",
            _chat_gate_output_violations(ai_then_sop_output, selector_input),
        )

    def test_chat_gate_first_soft_refusal_blocks_activity_when_not_asking_activity(self) -> None:
        selector_input = {
            "current_message": "还不急先了解",
            "mainline": {"stages": [{"id": "activity_and_price"}]},
            "precision_qa_index": [],
            "conversation_evidence": [
                {"message_ref": "current_message", "direction": "customer", "content": "还不急先了解"},
            ],
            "customer_stance_evidence": {
                "soft_refusal_quote_count": 1,
                "repeated_soft_refusal": False,
                "soft_refusal_evidence": [{"message_ref": "current_message", "quote": "还不急先了解"}],
            },
            "unfinished_sops": [
                {
                    "id": "s10_activity_intro",
                    "mainline_stage": "activity_and_price",
                    "sop_category": "activity_intro",
                    "payment_collection_gate": {"has_payment_collection": False, "status": "not_required"},
                },
            ],
        }
        output = {
            "route": "sop_only",
            "coverage": "exact",
            "sop_pack_id": "s10_activity_intro",
            "resume_stage": "activity_and_price",
        }

        self.assertIn(
            "gate_pressure_conflict:first_soft_refusal_must_not_send_activity_pack",
            _chat_gate_output_violations(output, selector_input),
        )

    def test_chat_gate_first_soft_refusal_allows_activity_when_customer_asks_activity(self) -> None:
        selector_input = {
            "current_message": "我考虑一下，活动怎么参加",
            "mainline": {"stages": [{"id": "activity_and_price"}]},
            "precision_qa_index": [],
            "conversation_evidence": [
                {"message_ref": "current_message", "direction": "customer", "content": "我考虑一下，活动怎么参加"},
            ],
            "customer_stance_evidence": {
                "soft_refusal_quote_count": 1,
                "repeated_soft_refusal": False,
                "soft_refusal_evidence": [{"message_ref": "current_message", "quote": "我考虑一下，活动怎么参加"}],
            },
            "unfinished_sops": [
                {
                    "id": "s10_activity_intro",
                    "mainline_stage": "activity_and_price",
                    "sop_category": "activity_intro",
                    "payment_collection_gate": {"has_payment_collection": False, "status": "not_required"},
                },
            ],
        }
        output = {
            "route": "sop_only",
            "coverage": "exact",
            "sop_pack_id": "s10_activity_intro",
            "resume_stage": "activity_and_price",
        }

        self.assertEqual(_chat_gate_output_violations(output, selector_input), [])

    async def test_retired_sop_event_route_only_records_audit(self) -> None:
        repo = _Repo()
        client = _OutreachClient()
        service = _service(repo=repo, client=client)

        result = await service.accept_audit_only(
            {
                "event_id": "evt_retired_route",
                "event_type": "sop_friend_added_schedule_batch",
                "customers": [{"customer": {"external_userid": "ext"}}],
            }
        )

        self.assertTrue(result["accepted"])
        self.assertFalse(result["executed"])
        self.assertEqual(result["status"], "retired_legacy_route")
        self.assertEqual(repo.events["evt_retired_route"]["status"], "retired_legacy_route")
        self.assertEqual(repo.tasks, [])
        self.assertEqual(client.fetch_calls, [])
        self.assertEqual(client.send_calls, [])

    async def test_deleted_customer_is_marked_before_sop_model_or_send(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            fetch_result={
                "status": "ok",
                "message_count": 1,
                "messages": [{"direction": "customer", "content": "你好"}],
                "customer_relation": {
                    "available": True,
                    "status": "deleted",
                    "is_deleted": True,
                    "deleted_at": "2026-07-29T10:00:00+08:00",
                    "updated_at": "2026-07-29T10:00:00+08:00",
                },
            }
        )
        personalized = _PersonalizedOutreachService()
        service = _service(
            repo=repo,
            client=client,
            personalized_outreach_service=personalized,
        )
        payload = _base_payload(
            event_id="evt_customer_deleted",
            event_type="sop_platform_task",
            sop={"day_stage": "day2", "actions": [{"type": "text", "content": "统一跟进"}]},
            customers=[{}],
        )

        repo.create_sop_event(payload)
        await service.process_event("evt_customer_deleted")

        self.assertEqual(repo.tasks[0]["status"], "skipped_customer_deleted")
        self.assertEqual(repo.tasks[0]["sop_pack_id"], "customer_deleted")
        self.assertEqual(personalized.calls, [])
        self.assertEqual(client.send_calls, [])

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
        client = _OutreachClient(messages=[])
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "send opening"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = {
            "event_id": "evt_identity_fallback",
            "event_type": "sop_friend_added_schedule_batch",
            "created_at": "2026-07-11T02:00:00+00:00",
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
            "event_id": "evt_identity_default",
            "event_type": "sop_friend_added_schedule_batch",
            "created_at": "2026-07-11T02:00:00+00:00",
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

        self.assertEqual(result["status"], "processed_with_errors")
        self.assertEqual(client.fetch_calls, [])

    async def test_first_added_event_fetches_conversation_then_selects_first_add_sop(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "from": "staff",
                    "source": "ai_reply",
                    "msgtype": "text",
                    "content": "前序开场",
                    "msgtime": "2026-07-11T01:30:00+00:00",
                }
            ]
        )
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "send opening"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_first",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-11T02:00:00+00:00",
            sop={"delay_minutes": 3},
            customers=[{"first_added_event": {"trace_id": "trace_1", "timestamp": "2026-07-11T01:00:00+00:00"}}],
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

    async def test_first_added_event_blocks_when_conversation_fetch_fails(self) -> None:
        for suffix, error in (("conflict", "http_status:409"), ("timeout", "TimeoutError: timed out")):
            with self.subTest(error=error):
                repo = _Repo()
                client = _OutreachClient(fetch_result={"status": "failed", "error": error})
                selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "should not run"})
                service = _service(repo=repo, client=client, selector=selector)
                event_id = f"evt_first_fetch_failed_{suffix}"
                payload = _base_payload(
                    event_id=event_id,
                    event_type="sop_friend_added_schedule_batch",
                    sop={"delay_minutes": 1},
                    customers=[{"first_added_event": {"trace_id": f"trace_fetch_failed_{suffix}"}}],
                )

                repo.create_sop_event(payload)
                result = await service.process_event(event_id)

                self.assertEqual(result["status"], "processed_with_errors")
                self.assertEqual(repo.tasks[0]["status"], "failed_conversation_fetch")
                self.assertIn(error, repo.tasks[0]["error"])
                self.assertEqual(selector.calls, [])
                self.assertEqual(client.send_calls, [])

    async def test_event_skips_auto_opening_over_30_minutes_during_quiet_hours(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "direction": "customer",
                    "content": "我已经添加了你，现在我们可以开始聊天了。",
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
            customers=[{"first_added_event": {"trace_id": "trace_quiet_inactive", "timestamp": "2026-07-10T17:00:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_quiet_inactive")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_quiet_hours_inactive")
        quiet = repo.tasks[0]["send_payload"]["quiet_hours"]
        self.assertEqual(quiet["timezone"], "Asia/Shanghai")
        self.assertEqual(quiet["window"], "00:00-08:00")
        self.assertEqual(quiet["quiet_activity_source"], "platform_auto_opening")
        self.assertEqual(quiet["inactivity_minutes"], 31)
        self.assertTrue(repo.tasks[0]["send_payload"]["backlog_marker"]["pending"])
        self.assertEqual(
            repo.tasks[0]["send_payload"]["backlog_marker"]["reason"],
            "suppressed_due_to_quiet_hours",
        )
        self.assertEqual(selector.calls, [])
        self.assertEqual(client.send_calls, [])

    async def test_event_continues_auto_opening_within_30_minutes_during_quiet_hours(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "direction": "customer",
                    "content": "我已经添加了你，现在我们可以开始聊天了。",
                    "msgtime": "2026-07-10T17:31:00+00:00",
                }
            ]
        )
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "continue first-add SOP"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_quiet_auto_opening_recent",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-10T18:00:00+00:00",
            sop={"delay_minutes": 3},
            customers=[{"first_added_event": {"trace_id": "trace_quiet_auto_opening_recent", "timestamp": "2026-07-10T17:00:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_quiet_auto_opening_recent")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(repo.tasks[0]["sop_pack_id"], "opening")
        self.assertEqual(len(selector.calls), 1)
        self.assertEqual(selector.calls[0]["conversation_messages"][0]["content"], "我已经添加了你，现在我们可以开始聊天了。")
        self.assertEqual(len(client.send_calls), 1)

    async def test_event_skips_when_no_customer_activity_time_during_quiet_hours(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "should not run"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_quiet_no_activity_time",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-10T18:00:00+00:00",
            sop={"delay_minutes": 3},
            customers=[{"first_added_event": {"trace_id": "trace_quiet_no_activity_time", "timestamp": "2026-07-10T17:00:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_quiet_no_activity_time")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_quiet_hours_inactive")
        quiet = repo.tasks[0]["send_payload"]["quiet_hours"]
        self.assertEqual(quiet["quiet_activity_source"], "")
        self.assertIsNone(quiet["inactivity_minutes"])
        self.assertEqual(selector.calls, [])
        self.assertEqual(client.send_calls, [])

    async def test_event_skips_real_customer_inactive_over_30_minutes_during_quiet_hours(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "direction": "customer",
                    "content": "我先看看",
                    "msgtime": "2026-07-10T17:00:00+00:00",
                },
                {
                    "direction": "staff",
                    "content": "好的亲，您看下",
                    "msgtime": "2026-07-10T17:20:00+00:00",
                },
            ]
        )
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "should not run"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_quiet_real_customer_inactive",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-10T18:00:00+00:00",
            sop={"delay_minutes": 3},
            customers=[{"first_added_event": {"trace_id": "trace_quiet_real_customer_inactive", "timestamp": "2026-07-10T16:50:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_quiet_real_customer_inactive")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_quiet_hours_inactive")
        quiet = repo.tasks[0]["send_payload"]["quiet_hours"]
        self.assertEqual(quiet["timezone"], "Asia/Shanghai")
        self.assertEqual(quiet["window"], "00:00-08:00")
        self.assertEqual(quiet["inactivity_minutes"], 60)
        self.assertEqual(selector.calls, [])
        self.assertEqual(client.send_calls, [])

    async def test_event_skips_recent_customer_message_during_quiet_hours(self) -> None:
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
            customers=[{"first_added_event": {"trace_id": "trace_quiet_active", "timestamp": "2026-07-10T17:00:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_quiet_active")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_customer_pending_ai_reply")
        self.assertEqual(repo.tasks[0]["send_payload"]["conversation_activity"]["reason"], "customer_pending_ai_reply")
        self.assertEqual(selector.calls, [])
        self.assertEqual(client.send_calls, [])

    async def test_event_skips_customer_price_question_outside_quiet_hours(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "direction": "customer",
                    "content": "测完皮肤后的价格怎么算？",
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
            customers=[{"first_added_event": {"trace_id": "trace_daytime_inactive", "timestamp": "2026-07-10T23:30:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_daytime_inactive")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_customer_pending_ai_reply")
        self.assertEqual(repo.tasks[0]["send_payload"]["conversation_activity"]["reason"], "customer_pending_ai_reply")
        self.assertEqual(selector.calls, [])
        self.assertEqual(client.send_calls, [])

    async def test_event_skips_when_assistant_just_replied(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "direction": "customer",
                    "content": "成都大邑",
                    "msgtime": "2026-07-11T02:30:00+00:00",
                },
                {
                    "direction": "staff",
                    "source": "ai_reply",
                    "msgtype": "text",
                    "content": "亲，成都大邑过去可以先看双流店，您看这家方便吗？",
                    "msgtime": "2026-07-11T02:35:28+00:00",
                },
            ]
        )
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "should not run"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_recent_assistant_activity",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-11T02:35:55+00:00",
            sop={"delay_minutes": 30},
            customers=[{"first_added_event": {"trace_id": "trace_recent_assistant", "timestamp": "2026-07-11T01:50:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_recent_assistant_activity")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_recent_active_conversation")
        recent_activity = repo.tasks[0]["send_payload"]["recent_assistant_activity"]
        self.assertTrue(recent_activity["skip"])
        self.assertEqual(recent_activity["reason"], "recent_assistant_activity")
        self.assertEqual(recent_activity["silence_after_assistant_minutes"], 0)
        self.assertEqual(selector.calls, [])
        self.assertEqual(client.send_calls, [])

    def test_sop_event_daily_touch_soft_limit_is_exposed_as_model_evidence(self) -> None:
        repo = _Repo()
        service = _service(repo=repo, client=_OutreachClient(), daily_touch_soft_limit=4)

        evidence = service._event_policy_evidence(
            payload={"created_at": "2026-07-20T02:00:00+00:00"},
            identity={"customer_id": "ext", "external_userid": "ext"},
            conversation_activity={},
        )

        self.assertEqual(evidence["touch_frequency"]["daily_soft_limit"], 4)
        self.assertFalse(evidence["touch_frequency"]["daily_soft_limit_reached"])
        self.assertFalse(evidence["touch_frequency"]["silent_soft_limit_reached"])
        self.assertFalse(evidence["touch_frequency"]["has_new_customer_progress_since_last_touch"])

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

    async def test_first_added_event_keeps_post_event_reply_and_skips(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {"from": "staff", "content": "旧会话", "msgtime": 1782286005000},
                {"from": "staff", "content": "首次加微后开场", "msgtime": "2026-07-02T04:00:00+00:00"},
                {"from": "customer", "content": "我在深圳，有附近地址吗？", "msgtime": "2026-07-02T04:20:00+00:00"},
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
        self.assertEqual(repo.tasks[0]["status"], "skipped_customer_pending_ai_reply")
        self.assertEqual(repo.tasks[0]["send_payload"]["conversation_activity"]["reason"], "customer_pending_ai_reply")
        self.assertEqual(selector.calls, [])
        self.assertEqual(client.send_calls, [])
        conversation_filter = repo.tasks[0]["send_payload"]["conversation_filter"]
        self.assertEqual(conversation_filter["input_count"], 3)
        self.assertEqual(conversation_filter["kept_count"], 2)
        self.assertEqual(conversation_filter["dropped_before_first_add"], 1)
        self.assertEqual(conversation_filter["dropped_after_event_created"], 0)
        self.assertEqual(conversation_filter["kept_after_event_created"], 1)

    async def test_first_added_event_continues_with_staff_only_messages(self) -> None:
        repo = _Repo()
        messages = [
            {
                "from": "staff",
                "source": "ai_reply",
                "msgtype": "text",
                "content": "我先把活动介绍发您",
                "msgtime": "2026-07-02T04:20:00+00:00",
            }
        ]
        client = _OutreachClient(messages=messages)
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "staff only"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_staff_only",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-02T04:10:00+00:00",
            sop={"delay_minutes": 1},
            customers=[{"first_added_event": {"trace_id": "trace_staff_only", "timestamp": "2026-07-02T03:50:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_staff_only")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(selector.calls[0]["conversation_messages"], messages)
        self.assertEqual(repo.tasks[0]["send_payload"]["conversation_filter"]["kept_after_event_created"], 1)

    async def test_first_added_event_skips_when_staff_replied_within_active_chat_window(self) -> None:
        repo = _Repo()
        messages = [
            {
                "from": "customer",
                "msgtype": "text",
                "content": "我在深圳",
                "msgtime": "2026-07-02T04:12:00+00:00",
            },
            {
                "from": "staff",
                "source": "ai_reply",
                "msgtype": "text",
                "content": "亲，您在深圳哪个区，我给您匹配门店",
                "msgtime": "2026-07-02T04:20:00+00:00",
            },
        ]
        client = _OutreachClient(messages=messages)
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "light touch while waiting"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_staff_waiting_after_customer",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-02T04:25:00+00:00",
            sop={"delay_minutes": 5},
            customers=[{"first_added_event": {"trace_id": "trace_staff_waiting", "timestamp": "2026-07-02T03:50:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_staff_waiting_after_customer")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_recent_active_conversation")
        self.assertEqual(repo.tasks[0]["send_payload"]["recent_assistant_activity"]["threshold_minutes"], 8)
        self.assertEqual(selector.calls, [])

    def test_event_decision_rejects_unexecutable_ai_handoff(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [{"id": "effect", "order": 10}],
            "ai_reply_policy": {"allowed": False},
        }

        output, violations = normalize_event_decision(
            {"decision": "handoff_to_ai_reply", "reason": "普通 AI 更自然"},
            selector_input,
        )

        self.assertFalse(output["send_sop"])
        self.assertTrue(output["need_ai_reply"])
        self.assertIn("handoff_to_ai_reply_not_allowed_for_proactive_event", violations)

    def test_event_decision_allows_ai_touch_without_selected_pack(self) -> None:
        output, violations = normalize_event_decision(
            {
                "decision": "send_ai_touch",
                "strategy": "soft_touch",
                "touch_goal": "resume_mainline",
                "selected_pack_ids": [],
                "ai_touch_messages": [
                    {"type": "text", "content": {"text": "亲，前面的内容我先不重复发了，您主要担心效果还是门店？"}}
                ],
            },
            {
                "mode": "first_add_flow",
                "candidate_sops": [{"id": "effect", "order": 10, "sop_category": "effect_case"}],
                "event_policy_evidence": {},
            },
        )

        self.assertEqual(violations, [])
        self.assertFalse(output["send_sop"])
        self.assertEqual(output["touch_goal"], "resume_mainline")
        self.assertEqual(output["ai_touch_messages"][0]["type"], "text")

    def test_event_decision_requires_ai_touch_text(self) -> None:
        _, violations = normalize_event_decision(
            {"decision": "handoff_or_safety_notice", "strategy": "safety_notice"},
            {"mode": "first_add_flow", "candidate_sops": [], "event_policy_evidence": {}},
        )

        self.assertIn("ai_touch_messages_required", violations)

    def test_event_decision_allows_only_two_adjacent_packs_for_merge(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {"id": "store", "order": 10},
                {"id": "effect", "order": 20},
                {"id": "activity", "order": 30},
            ],
            "ai_reply_policy": {"allowed": False},
        }

        valid, valid_violations = normalize_event_decision(
            {"decision": "merge", "selected_pack_ids": ["store", "effect"]},
            selector_input,
        )
        _, invalid_violations = normalize_event_decision(
            {"decision": "merge", "selected_pack_ids": ["store", "activity"]},
            selector_input,
        )

        self.assertEqual(valid_violations, [])
        self.assertTrue(valid["send_sop"])
        self.assertIn("merge_requires_adjacent_mainline_packs", invalid_violations)

    def test_event_decision_rejects_skipping_earliest_unfinished_pack(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {"id": "store", "order": 10},
                {"id": "effect", "order": 20},
                {"id": "activity", "order": 30},
            ],
            "ai_reply_policy": {"allowed": False},
        }

        _, send_violations = normalize_event_decision(
            {"decision": "send", "selected_pack_ids": ["effect"]},
            selector_input,
        )
        _, merge_violations = normalize_event_decision(
            {"decision": "merge", "selected_pack_ids": ["effect", "activity"]},
            selector_input,
        )

        self.assertIn("selected_packs_must_start_with_earliest_candidate", send_violations)
        self.assertIn("selected_packs_must_start_with_earliest_candidate", merge_violations)

    def test_event_decision_allows_next_pack_when_earliest_candidate_is_completed(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {"id": "store", "order": 10, "sop_category": "store_prompt"},
                {"id": "effect", "order": 20, "sop_category": "effect_case"},
            ],
            "completed_sop_pack_ids": ["store"],
            "completed_sop_categories": ["store_prompt"],
            "ai_reply_policy": {"allowed": False},
        }

        output, violations = normalize_event_decision(
            {"decision": "send", "selected_pack_ids": ["effect"]},
            selector_input,
        )

        self.assertEqual(violations, [])
        self.assertEqual(output["selected_pack_ids"], ["effect"])

    def test_event_decision_rejects_opening_when_timeline_completed_it(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {
                    "id": "s10_new_customer_opening",
                    "order": 10,
                    "sop_category": "s10_new_customer_opening",
                    "mainline_stage": "opening_and_positioning",
                },
                {
                    "id": "s10_need_and_case",
                    "order": 20,
                    "sop_category": "s10_need_and_case",
                    "mainline_stage": "need_and_case",
                },
            ],
            "completed_sop_pack_ids": [],
            "completed_sop_categories": [],
            "mainline_stage_status": [
                {
                    "stage_id": "opening_and_positioning",
                    "structural_completed": True,
                    "timeline_completed": True,
                },
                {"stage_id": "need_and_case", "structural_completed": False},
            ],
            "ai_reply_policy": {"allowed": False},
        }

        _, opening_violations = normalize_event_decision(
            {"decision": "send", "selected_pack_ids": ["s10_new_customer_opening"]},
            selector_input,
        )
        output, next_violations = normalize_event_decision(
            {"decision": "send", "selected_pack_ids": ["s10_need_and_case"]},
            selector_input,
        )

        self.assertIn("selected_packs_must_start_with_earliest_candidate", opening_violations)
        self.assertEqual(next_violations, [])
        self.assertEqual(output["selected_pack_ids"], ["s10_need_and_case"])

    def test_event_selector_marks_opening_completed_from_customer_timeline(self) -> None:
        selector_input = _event_selector_input(
            payload={"event_type": "sop_friend_added_schedule_batch"},
            customer={},
            event_type="sop_friend_added_schedule_batch",
            conversation_messages=[
                {
                    "direction": "customer",
                    "source": "archive_history",
                    "message_type": "text",
                    "content": "苗儿19539465093",
                    "message_time": 1784817876425,
                },
                {
                    "direction": "staff",
                    "source": "system_task",
                    "message_type": "text",
                    "content": "姓名手机号已记，门店先按上海静安店登记。",
                    "message_time": 1784817912564,
                },
            ],
            conversation_activity={
                "customer_replied": True,
                "real_customer_message_count": 1,
                "latest_customer_message_at": "2026-07-23T14:49:34.265000+00:00",
                "latest_assistant_message_at": "2026-07-23T14:54:28.232000+00:00",
                "last_message_direction": "staff",
            },
            customer_memory={},
            customer_context={},
            candidate_packs=[
                {
                    "id": "s10_new_customer_opening",
                    "order": 10,
                    "sop_category": "s10_new_customer_opening",
                    "mainline_stage": "opening_and_positioning",
                    "reply_messages": [{"type": "text", "content": {"text": "你好"}}],
                },
                {
                    "id": "s10_need_and_case",
                    "order": 20,
                    "sop_category": "s10_need_and_case",
                    "mainline_stage": "need_and_case",
                    "reply_messages": [{"type": "text", "content": {"text": "案例"}}],
                },
            ],
            actions_reply_messages=[],
            completed_sop_pack_ids=[],
            completed_sop_categories=[],
            event_policy_evidence={},
        )

        stage_status = {
            item["stage_id"]: item
            for item in selector_input["mainline_stage_status"]
        }
        opening = stage_status["opening_and_positioning"]
        self.assertTrue(opening["structural_completed"])
        self.assertTrue(opening["timeline_completed"])
        self.assertEqual(opening["timeline_evidence"]["source"], "recent_conversation_timeline")
        self.assertEqual(opening["timeline_evidence"]["real_customer_message_count"], 1)

    def test_event_selector_treats_later_completed_stage_as_prior_progress(self) -> None:
        selector_input = _event_selector_input(
            payload={"event_type": "sop_friend_added_schedule_batch"},
            customer={},
            event_type="sop_friend_added_schedule_batch",
            conversation_messages=[],
            conversation_activity={},
            customer_memory={},
            customer_context={},
            candidate_packs=[
                {
                    "id": "event_s10_store_prompt_5min",
                    "order": 15,
                    "sop_category": "store_prompt",
                    "mainline_stage": "location_capture",
                    "reply_messages": [{"type": "text", "content": {"text": "城市"}}],
                },
                {
                    "id": "event_s10_deposit_push_70min",
                    "order": 50,
                    "sop_category": "deposit_push",
                    "mainline_stage": "deposit_decision",
                    "reply_messages": [{"type": "text", "content": {"text": "预约金"}}],
                },
            ],
            actions_reply_messages=[],
            completed_sop_pack_ids=["s10_activity_intro"],
            completed_sop_categories=["s10_activity_intro"],
            event_policy_evidence={},
        )

        stage_status = {
            item["stage_id"]: item
            for item in selector_input["mainline_stage_status"]
        }
        self.assertTrue(stage_status["location_capture"]["structural_completed"])
        self.assertTrue(stage_status["location_capture"]["progression_completed"])
        self.assertTrue(stage_status["need_and_case"]["progression_completed"])
        self.assertTrue(stage_status["activity_and_price"]["structural_completed"])

        output, violations = normalize_event_decision(
            {"decision": "send", "selected_pack_ids": ["event_s10_deposit_push_70min"]},
            selector_input,
        )

        self.assertEqual(violations, [])
        self.assertEqual(output["selected_pack_ids"], ["event_s10_deposit_push_70min"])

    def test_event_selector_marks_activity_complete_from_recent_assistant_facts(self) -> None:
        selector_input = _event_selector_input(
            payload={"event_type": "sop_friend_added_schedule_batch"},
            customer={},
            event_type="sop_friend_added_schedule_batch",
            conversation_messages=[
                {
                    "direction": "assistant",
                    "message_type": "text",
                    "content": "现在活动价268，每位10元预约金锁活动名额，到店抵扣，未做或不满意可退。",
                }
            ],
            conversation_activity={},
            customer_memory={},
            customer_context={},
            candidate_packs=[
                {
                    "id": "s10_activity_intro",
                    "order": 40,
                    "sop_category": "s10_activity_intro",
                    "mainline_stage": "activity_and_price",
                    "reply_messages": [{"type": "text", "content": {"text": "活动"}}],
                },
                {
                    "id": "event_s10_deposit_push_70min",
                    "order": 50,
                    "sop_category": "deposit_push",
                    "mainline_stage": "deposit_decision",
                    "reply_messages": [{"type": "text", "content": {"text": "预约金"}}],
                },
            ],
            actions_reply_messages=[],
            completed_sop_pack_ids=["s10_new_customer_opening", "s10_need_and_case"],
            completed_sop_categories=["s10_new_customer_opening", "s10_need_and_case"],
            event_policy_evidence={},
        )

        stage_status = {
            item["stage_id"]: item
            for item in selector_input["mainline_stage_status"]
        }
        self.assertTrue(stage_status["activity_and_price"]["structural_completed"])
        self.assertTrue(stage_status["activity_and_price"]["timeline_completed"])
        self.assertEqual(
            stage_status["activity_and_price"]["timeline_evidence"]["source"],
            "recent_assistant_activity_price_facts",
        )

        output, violations = normalize_event_decision(
            {
                "decision": "send",
                "selected_pack_ids": ["event_s10_deposit_push_70min"],
                "stage_skip_evidence": [
                    {
                        "stage_id": "activity_and_price",
                        "pack_id": "s10_activity_intro",
                        "evidence": "近期聊天已完整讲过268、10元预约金、到店抵扣和可退。",
                    }
                ],
            },
            selector_input,
        )

        self.assertEqual(violations, [])
        self.assertEqual(output["selected_pack_ids"], ["event_s10_deposit_push_70min"])

    def test_event_selector_marks_location_complete_from_recent_assistant_prompt(self) -> None:
        selector_input = _event_selector_input(
            payload={"event_type": "sop_friend_added_schedule_batch"},
            customer={},
            event_type="sop_friend_added_schedule_batch",
            conversation_messages=[
                {
                    "direction": "assistant",
                    "message_type": "text",
                    "content": "亲，您在哪个城市哪个区呢？我帮您看附近门店。",
                }
            ],
            conversation_activity={},
            customer_memory={},
            customer_context={},
            candidate_packs=[
                {
                    "id": "event_s10_store_prompt_5min",
                    "order": 15,
                    "sop_category": "store_prompt",
                    "mainline_stage": "location_capture",
                    "reply_messages": [{"type": "text", "content": {"text": "城市"}}],
                },
                {
                    "id": "s10_need_and_case",
                    "order": 30,
                    "sop_category": "s10_need_and_case",
                    "mainline_stage": "need_and_case",
                    "reply_messages": [{"type": "text", "content": {"text": "案例"}}],
                },
            ],
            actions_reply_messages=[],
            completed_sop_pack_ids=["s10_new_customer_opening"],
            completed_sop_categories=["s10_new_customer_opening"],
            event_policy_evidence={},
        )

        stage_status = {
            item["stage_id"]: item
            for item in selector_input["mainline_stage_status"]
        }
        self.assertTrue(stage_status["location_capture"]["structural_completed"])
        self.assertTrue(stage_status["location_capture"]["timeline_completed"])
        self.assertEqual(
            stage_status["location_capture"]["timeline_evidence"]["source"],
            "recent_assistant_location_capture_prompt",
        )

        output, violations = normalize_event_decision(
            {
                "decision": "send",
                "selected_pack_ids": ["s10_need_and_case"],
                "stage_skip_evidence": [
                    {
                        "stage_id": "location_capture",
                        "pack_id": "event_s10_store_prompt_5min",
                        "evidence": "近期助手已问过城市区域或定位。",
                    }
                ],
            },
            selector_input,
        )

        self.assertEqual(violations, [])
        self.assertEqual(output["selected_pack_ids"], ["s10_need_and_case"])

    def test_event_selector_paid_state_completes_pre_payment_stages(self) -> None:
        selector_input = _event_selector_input(
            payload={"event_type": "sop_friend_added_schedule_batch"},
            customer={},
            event_type="sop_friend_added_schedule_batch",
            conversation_messages=[],
            conversation_activity={},
            customer_memory={"basic_info": {"deposit_state": "paid_by_order"}},
            customer_context={},
            candidate_packs=[
                {
                    "id": "event_s10_store_prompt_5min",
                    "order": 15,
                    "sop_category": "store_prompt",
                    "mainline_stage": "location_capture",
                    "reply_messages": [{"type": "text", "content": {"text": "城市"}}],
                },
                {
                    "id": "event_s10_unpaid_effect_1h",
                    "order": 60,
                    "sop_category": "payment_followup",
                    "mainline_stage": "deposit_decision",
                    "reply_messages": [{"type": "text", "content": {"text": "付款"}}],
                },
            ],
            actions_reply_messages=[],
            completed_sop_pack_ids=[],
            completed_sop_categories=[],
            event_policy_evidence={},
        )

        stage_status = {
            item["stage_id"]: item
            for item in selector_input["mainline_stage_status"]
        }
        for stage_id in [
            "opening_and_positioning",
            "location_capture",
            "need_and_case",
            "activity_and_price",
            "deposit_decision",
        ]:
            self.assertTrue(stage_status[stage_id]["structural_completed"])
            self.assertTrue(stage_status[stage_id]["payment_completed"])

    def test_event_decision_requires_evidence_when_skipping_earlier_mainline_stage(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {
                    "id": "s10_need_and_case",
                    "order": 20,
                    "sop_category": "s10_need_and_case",
                    "mainline_stage": "need_and_case",
                },
                {
                    "id": "s10_activity_intro",
                    "order": 30,
                    "sop_category": "s10_activity_intro",
                    "mainline_stage": "activity_and_price",
                },
            ],
            "completed_sop_pack_ids": ["s10_new_customer_opening"],
            "completed_sop_categories": ["s10_new_customer_opening"],
            "mainline_stage_status": [
                {"stage_id": "need_and_case", "structural_completed": False},
                {"stage_id": "activity_and_price", "structural_completed": False},
            ],
            "ai_reply_policy": {"allowed": False},
        }

        _, no_evidence = normalize_event_decision(
            {"decision": "send", "selected_pack_ids": ["s10_activity_intro"]},
            selector_input,
        )
        output, with_evidence = normalize_event_decision(
            {
                "decision": "send",
                "selected_pack_ids": ["s10_activity_intro"],
                "stage_skip_evidence": [
                    {
                        "stage_id": "need_and_case",
                        "pack_id": "s10_need_and_case",
                        "evidence": "近期已发真实案例图并承接了斑点改善效果。",
                    }
                ],
            },
            selector_input,
        )

        self.assertIn("selected_packs_must_start_with_earliest_candidate", no_evidence)
        self.assertEqual(with_evidence, [])
        self.assertEqual(output["stage_skip_evidence"][0]["stage_id"], "need_and_case")

    def test_event_decision_validates_frequency_guard_against_structured_evidence(self) -> None:
        unsupported = {
            "mode": "first_add_flow",
            "candidate_sops": [{"id": "effect", "order": 10}],
            "event_policy_evidence": {
                "touch_frequency": {
                    "daily_soft_limit_reached": True,
                    "silent_soft_limit_reached": True,
                    "has_new_customer_progress_since_last_touch": True,
                },
                "pending_backlog": {"has_pending": False},
            },
        }
        supported = {
            **unsupported,
            "event_policy_evidence": {
                "touch_frequency": {
                    "daily_soft_limit_reached": True,
                    "silent_soft_limit_reached": True,
                    "has_new_customer_progress_since_last_touch": False,
                },
                "pending_backlog": {"has_pending": False},
            },
        }

        _, unsupported_violations = normalize_event_decision(
            {"decision": "skip", "strategy": "frequency_guard"},
            unsupported,
        )
        _, supported_violations = normalize_event_decision(
            {"decision": "skip", "strategy": "frequency_guard"},
            supported,
        )

        self.assertIn("frequency_guard_not_supported_by_event_evidence", unsupported_violations)
        self.assertNotIn("frequency_guard_not_supported_by_event_evidence", supported_violations)

    def test_event_decision_rejects_skip_with_send_strategy(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [{"id": "close", "order": 10}],
            "event_policy_evidence": {},
        }

        _, invalid_violations = normalize_event_decision(
            {"decision": "skip", "strategy": "continue_mainline"},
            selector_input,
        )
        _, valid_violations = normalize_event_decision(
            {"decision": "skip", "strategy": "conflict_guard"},
            selector_input,
        )

        self.assertIn("non_send_decision_conflicts_with_send_strategy", invalid_violations)
        self.assertNotIn("non_send_decision_conflicts_with_send_strategy", valid_violations)

    def test_platform_actions_with_content_cannot_be_downgraded_to_ai_touch(self) -> None:
        output, violations = normalize_event_decision(
            {
                "decision": "send_ai_touch",
                "strategy": "soft_touch",
                "ai_touch_messages": [{"type": "text", "content": "平台文案"}],
            },
            {
                "mode": "platform_actions",
                "candidate_sops": [],
                "platform_actions_summary": {"message_count": 1},
                "platform_actions": {
                    "editable_text_messages": [{"order": 1, "text": "平台文案"}],
                    "readonly_messages": [],
                },
            },
        )

        self.assertEqual(violations, [])
        self.assertEqual(output["decision"], "send")
        self.assertTrue(output["send_sop"])
        self.assertEqual(output["selected_pack_ids"], [])
        self.assertEqual(output["ai_touch_messages"], [])

    def test_event_decision_requires_evidence_source_for_conflict_guard(self) -> None:
        no_evidence = {
            "mode": "first_add_flow",
            "candidate_sops": [{"id": "close", "order": 10}],
            "completed_sop_pack_ids": [],
            "completed_sop_categories": [],
            "recent_conversation": [],
            "event_policy_evidence": {},
        }
        with_customer_evidence = {
            **no_evidence,
            "recent_conversation": [{"role": "customer", "content": "我先不考虑"}],
        }

        _, no_evidence_violations = normalize_event_decision(
            {"decision": "skip", "strategy": "conflict_guard"},
            no_evidence,
        )
        _, customer_evidence_violations = normalize_event_decision(
            {"decision": "skip", "strategy": "conflict_guard"},
            with_customer_evidence,
        )

        self.assertIn("conflict_guard_missing_evidence_source", no_evidence_violations)
        self.assertNotIn("conflict_guard_missing_evidence_source", customer_evidence_violations)

    def test_repeated_candidates_need_ai_touch_unless_customer_or_policy_blocks(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [{"id": "close", "order": 10, "sop_category": "final_close"}],
            "completed_sop_pack_ids": ["close"],
            "completed_sop_categories": ["final_close"],
            "recent_conversation": [],
            "event_policy_evidence": {},
        }

        _, violations = normalize_event_decision(
            {"decision": "skip", "strategy": "conflict_guard"},
            selector_input,
        )
        self.assertIn("repeated_candidates_should_use_ai_touch", violations)

        _, stage_repeat_violations = normalize_event_decision(
            {"decision": "skip", "strategy": "conflict_guard"},
            {
                "mode": "first_add_flow",
                "candidate_sops": [{"id": "effect_warmup", "order": 10, "sop_category": "effect_case"}],
                "completed_sop_pack_ids": [],
                "completed_sop_categories": [],
                "mainline_stage_status": {"need_and_case": {"structural_completed": True}},
                "recent_conversation": [],
                "event_policy_evidence": {},
            },
        )
        self.assertIn("repeated_candidates_should_use_ai_touch", stage_repeat_violations)

        _, customer_block_violations = normalize_event_decision(
            {"decision": "skip", "strategy": "conflict_guard"},
            {
                **selector_input,
                "event_policy_evidence": {"customer_rejection": True},
            },
        )
        self.assertNotIn("repeated_candidates_should_use_ai_touch", customer_block_violations)

    async def test_repeated_candidates_repair_failure_falls_back_to_ai_touch(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [{"id": "close", "order": 10, "sop_category": "final_close"}],
            "completed_sop_pack_ids": ["close"],
            "completed_sop_categories": ["final_close"],
            "recent_conversation": [],
            "event_policy_evidence": {},
        }
        model = _SequenceModel(
            [
                {"decision": "skip", "strategy": "conflict_guard", "reason": "duplicate"},
                {"decision": "skip", "strategy": "conflict_guard", "reason": "still duplicate"},
            ]
        )
        service = SopExecutionService(repository=_Repo(), sop_reply_pack_service=_PackService(), model_client=model)

        output = await service._judge_event_sop(selector_input)

        self.assertEqual(output["decision"], "send_ai_touch")
        self.assertEqual(output["touch_goal"], "resume_mainline")
        self.assertEqual(output["sop_pack_id"], "")
        self.assertTrue(output["ai_touch_messages"])
        self.assertIn("repeated_candidates_should_use_ai_touch", output["initial_violations"])

    async def test_completed_activity_repair_failure_falls_back_to_deposit_candidate(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {"id": "deposit", "order": 40, "sop_category": "deposit_push", "mainline_stage": "deposit_decision"}
            ],
            "completed_sop_pack_ids": [],
            "completed_sop_categories": [],
            "mainline_stage_status": {"activity_and_price": {"structural_completed": True}},
            "recent_conversation": [],
            "event_policy_evidence": {},
        }
        model = _SequenceModel(
            [
                {"decision": "skip", "strategy": "conflict_guard", "reason": "duplicate"},
                {"decision": "skip", "strategy": "conflict_guard", "reason": "still duplicate"},
            ]
        )
        service = SopExecutionService(repository=_Repo(), sop_reply_pack_service=_PackService(), model_client=model)

        output = await service._judge_event_sop(selector_input)

        self.assertEqual(output["decision"], "send")
        self.assertEqual(output["sop_pack_id"], "deposit")
        self.assertEqual(output["selected_pack_ids"], ["deposit"])
        self.assertIn("completed_activity_with_deposit_candidate_should_continue", output["initial_violations"])

    def test_backlog_touch_requires_mainline_candidate_when_available(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {"id": "effect", "order": 20, "sop_category": "s10_need_and_case", "mainline_stage": "need_and_case"},
                {"id": "activity", "order": 30, "sop_category": "s10_activity_intro", "mainline_stage": "activity_and_price"},
            ],
            "completed_sop_pack_ids": ["opening"],
            "completed_sop_categories": ["s10_new_customer_opening"],
            "recent_conversation": [],
            "event_policy_evidence": {"quiet_hour_backlog": True, "backlog_count": 3},
        }

        _, violations = normalize_event_decision(
            {
                "decision": "send_ai_touch",
                "strategy": "soft_touch",
                "ai_touch_messages": [{"type": "text", "content": {"text": "亲，方便时回我一下"}}],
            },
            selector_input,
        )

        self.assertIn("backlog_should_use_mainline_candidate", violations)

    async def test_backlog_repair_failure_falls_back_to_mainline_candidate(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {"id": "effect", "order": 20, "sop_category": "s10_need_and_case", "mainline_stage": "need_and_case"},
                {"id": "activity", "order": 30, "sop_category": "s10_activity_intro", "mainline_stage": "activity_and_price"},
            ],
            "completed_sop_pack_ids": ["opening"],
            "completed_sop_categories": ["s10_new_customer_opening"],
            "recent_conversation": [],
            "event_policy_evidence": {"quiet_hour_backlog": True, "backlog_count": 3},
        }
        model = _SequenceModel(
            [
                {
                    "decision": "send_ai_touch",
                    "strategy": "soft_touch",
                    "ai_touch_messages": [{"type": "text", "content": {"text": "亲，方便时回我一下"}}],
                },
                {
                    "decision": "send_ai_touch",
                    "strategy": "soft_touch",
                    "ai_touch_messages": [{"type": "text", "content": {"text": "亲，还是方便时回我一下"}}],
                },
            ]
        )
        service = SopExecutionService(repository=_Repo(), sop_reply_pack_service=_PackService(), model_client=model)

        output = await service._judge_event_sop(selector_input)

        self.assertEqual(output["decision"], "merge")
        self.assertEqual(output["selected_pack_ids"], ["effect", "activity"])
        self.assertEqual(output["strategy"], "recover_backlog")
        self.assertIn("backlog_should_use_mainline_candidate", output["initial_violations"])

    def test_completed_activity_with_deposit_candidate_cannot_skip(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {"id": "deposit", "order": 40, "sop_category": "deposit_push"},
            ],
            "completed_sop_pack_ids": [],
            "completed_sop_categories": [],
            "recent_conversation": [],
            "mainline_stage_status": {
                "activity_and_price": {
                    "semantic_completed": True,
                    "evidence": [{"source": "recent_chat"}],
                }
            },
            "event_policy_evidence": {},
        }

        _, violations = normalize_event_decision(
            {"decision": "skip", "strategy": "conflict_guard"},
            selector_input,
        )
        self.assertIn("completed_activity_with_deposit_candidate_should_continue", violations)

    def test_event_decision_rejects_payment_pack_when_structural_gate_is_not_supported(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {
                    "id": "activity",
                    "order": 30,
                    "payment_collection_gate": {"status": "not_required"},
                },
                {
                    "id": "deposit",
                    "order": 40,
                    "payment_collection_gate": {"status": "activity_intro_required"},
                },
            ],
            "event_policy_evidence": {"ai_reply_policy": {"allowed": False}},
        }

        _, send_violations = normalize_event_decision(
            {"decision": "send", "selected_pack_ids": ["deposit"]},
            selector_input,
        )
        _, merge_violations = normalize_event_decision(
            {"decision": "merge", "selected_pack_ids": ["activity", "deposit"]},
            selector_input,
        )

        self.assertIn("selected_payment_pack_not_currently_supported", send_violations)
        self.assertIn("selected_payment_pack_not_currently_supported", merge_violations)

    def test_event_decision_does_not_allow_activity_intro_blocked_card_removal(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {
                    "id": "activity",
                    "order": 30,
                    "editable_text_messages": [{"order": 1, "text": "活动介绍和付款说明"}],
                    "readonly_messages": [
                        {"order": 2, "type": "image", "facts": {"asset": "configured"}},
                        {"order": 3, "type": "payment_collection", "facts": {"amount": 10}},
                    ],
                    "payment_collection_gate": {"status": "activity_intro_required"},
                }
            ],
            "event_policy_evidence": {"ai_reply_policy": {"allowed": False}},
        }

        _, violations = normalize_event_decision(
            {
                "decision": "send",
                "selected_pack_ids": ["activity"],
                "message_operations": [
                    {"op": "replace_text", "order": 1, "text": "活动介绍"},
                    {"op": "remove_message", "order": 3},
                ],
            },
            selector_input,
        )

        self.assertIn("selected_payment_pack_not_currently_supported", violations)

    def test_event_decision_does_not_allow_card_removal_to_bypass_activity_intro(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {
                    "id": "deposit",
                    "order": 40,
                    "editable_text_messages": [{"order": 1, "text": "预约金说明"}],
                    "readonly_messages": [
                        {"order": 2, "type": "payment_collection", "facts": {"amount": 10}},
                    ],
                    "payment_collection_gate": {"status": "activity_intro_required"},
                }
            ],
            "event_policy_evidence": {"ai_reply_policy": {"allowed": False}},
        }

        _, violations = normalize_event_decision(
            {
                "decision": "send",
                "selected_pack_ids": ["deposit"],
                "message_operations": [
                    {"op": "replace_text", "order": 1, "text": "活动介绍"},
                    {"op": "remove_message", "order": 2},
                ],
            },
            selector_input,
        )

        self.assertIn("selected_payment_pack_not_currently_supported", violations)

    def test_event_decision_does_not_allow_supported_payment_card_removal(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {
                    "id": "deposit",
                    "order": 40,
                    "editable_text_messages": [{"order": 1, "text": "payment intro"}],
                    "readonly_messages": [
                        {"order": 2, "type": "payment_collection", "facts": {"amount": 10}},
                    ],
                    "payment_collection_gate": {"status": "supported"},
                }
            ],
            "event_policy_evidence": {"ai_reply_policy": {"allowed": False}},
            "completed_sop_pack_ids": ["s10_activity_intro"],
            "completed_sop_categories": ["activity_intro"],
        }

        _, violations = normalize_event_decision(
            {
                "decision": "send",
                "selected_pack_ids": ["deposit"],
                "message_operations": [{"op": "remove_message", "order": 2}],
            },
            selector_input,
        )

        self.assertIn("supported_payment_collection_must_not_be_removed", violations)

    def test_event_decision_rejects_pack_already_completed_by_id_or_category(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "candidate_sops": [
                {"id": "effect_a", "order": 10, "sop_category": "effect_case"},
                {"id": "effect_b", "order": 20, "sop_category": "effect_case"},
            ],
            "completed_sop_pack_ids": ["effect_a"],
            "completed_sop_categories": ["effect_case"],
            "event_policy_evidence": {"ai_reply_policy": {"allowed": False}},
        }

        _, by_id = normalize_event_decision(
            {"decision": "send", "selected_pack_ids": ["effect_a"]},
            selector_input,
        )
        _, by_category = normalize_event_decision(
            {"decision": "send", "selected_pack_ids": ["effect_b"]},
            selector_input,
        )

        self.assertIn("selected_sop_pack_already_completed", by_id)
        self.assertIn("selected_sop_pack_already_completed", by_category)

    def test_event_ai_reply_policy_requires_pending_message_and_runtime(self) -> None:
        no_runtime = build_event_ai_reply_policy(
            {"latest_customer_pending_ai_reply": True},
            runtime_handoff_available=False,
        )
        allowed = build_event_ai_reply_policy(
            {"latest_customer_pending_ai_reply": True},
            runtime_handoff_available=True,
        )

        self.assertFalse(no_runtime["allowed"])
        self.assertEqual(no_runtime["reason"], "ordinary_ai_reply_runtime_not_attached")
        self.assertTrue(allowed["allowed"])

    def test_merge_preserves_pack_order_and_reindexes_messages(self) -> None:
        messages = combine_selected_pack_messages(
            [
                {
                    "id": "activity",
                    "order": 20,
                    "reply_messages": [{"type": "text", "order": 1, "content": {"text": "活动"}}],
                },
                {
                    "id": "effect",
                    "order": 10,
                    "reply_messages": [
                        {"type": "text", "order": 1, "content": {"text": "效果"}},
                        {"type": "image", "order": 2, "content": {"url": "https://example.com/case.png"}},
                    ],
                },
            ]
        )

        self.assertEqual([item["order"] for item in messages], [1, 2, 3])
        self.assertEqual(messages[0]["content"]["text"], "效果")
        self.assertEqual(messages[2]["content"]["text"], "活动")

    def test_event_policy_evidence_separates_today_history_and_pending_backlog(self) -> None:
        repo = _Repo()
        repo.tasks = [
            {
                "id": "sent_today",
                "event_id": "sent_today_event",
                "status": "sent",
                "sent_at": "2026-07-11T00:15:00+00:00",
                "created_at": "2026-07-11T00:14:00+00:00",
                "send_payload": {},
            },
            {
                "id": "sent_prior",
                "event_id": "sent_prior_event",
                "status": "sent",
                "sent_at": "2026-07-10T02:00:00+00:00",
                "created_at": "2026-07-10T01:59:00+00:00",
                "send_payload": {},
            },
            {
                "id": "quiet_backlog",
                "event_id": "quiet_event",
                "status": "skipped_quiet_hours_inactive",
                "created_at": "2026-07-11T00:30:00+00:00",
                "send_payload": {
                    "backlog_marker": {
                        "pending": True,
                        "reason": "suppressed_due_to_quiet_hours",
                        "event": {"delay_minutes": 60, "stage_tag": "price_quote"},
                    }
                },
            },
        ]
        service = _service(repo=repo, client=_OutreachClient())

        evidence = service._event_policy_evidence(
            payload={"created_at": "2026-07-11T01:00:00+00:00"},
            identity={
                "customer_id": "customer",
                "external_userid": "external",
                "corp_id": "ww943af61cd5d2afe4",
                "wechat": "CS001",
            },
            conversation_activity={"latest_customer_message_at": "2026-07-10T01:00:00+00:00"},
        )

        frequency = evidence["touch_frequency"]
        self.assertEqual(frequency["today_count"], 1)
        self.assertEqual(frequency["prior_count"], 1)
        self.assertEqual(frequency["total_count"], 2)
        self.assertEqual(frequency["consecutive_silent_touch_count"], 2)
        self.assertEqual(evidence["pending_backlog"]["count"], 1)
        self.assertEqual(
            evidence["pending_backlog"]["items"][0]["event"]["stage_tag"],
            "price_quote",
        )

    async def test_quiet_hour_backlog_fuses_suppressed_first_add_sops_after_0830(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[{"direction": "assistant", "content": "我先给您发一下活动。"}])
        selector = _FusionSelector(
            {
                "send_sop": True,
                "covered_pack_ids": ["effect", "activity"],
                "reply_messages": [
                    {"type": "source", "source_id": "effect:1"},
                    {"type": "source", "source_id": "effect:2"},
                    {"type": "source", "source_id": "activity:1"},
                ],
            }
        )
        service = _service(
            repo=repo,
            client=client,
            selector=selector,
            pack_service=_BacklogPackService(),
            quiet_backlog_fusion_time="08:30",
        )
        payload = _base_payload(
            event_id="evt_quiet_backlog",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-20T02:10:00+08:00",
            sop={"delay_minutes": 60, "stage_tag": "activity"},
            customers=[{"first_added_event": {"trace_id": "trace_backlog"}}],
        )

        repo.create_sop_event(payload)
        await service.process_event("evt_quiet_backlog")
        result = await service.process_due_quiet_backlog_fusions(now=_dt("2026-07-20T08:31:00+08:00"))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "sent")
        self.assertEqual(len(selector.calls), 1)
        self.assertEqual(selector.calls[0]["mode"], "quiet_backlog_fusion")
        self.assertEqual(selector.calls[0]["backlog_task_ids"], ["task_1"])
        sent_messages = client.send_calls[-1]["reply_messages"]
        self.assertEqual([item["type"] for item in sent_messages], ["text", "image", "text"])
        self.assertEqual(sent_messages[0]["content"]["text"], "亲，先给您看下做完后的效果参考。")
        self.assertEqual(sent_messages[1]["content"]["url"], "https://example.com/case.png")
        self.assertEqual(sent_messages[2]["content"]["text"], "现在活动价268元，线上10元先锁名额。")

    async def test_quiet_hour_backlog_only_sends_verbatim_source_text(self) -> None:
        source_lookup = {
            "activity:1": {
                "type": "text",
                "order": 1,
                "content": {"text": "活动价268元，线上名额有限。"},
            },
            "activity:2": {
                "type": "text",
                "order": 2,
                "content": {"text": "到店抵扣；未做或不满意可退，实际按付款记录核对。"},
            },
        }

        messages = _normalize_quiet_backlog_fusion_messages(
            [
                {"type": "text", "text": "活动价改成199元。"},
                {"type": "group", "source_ids": ["activity:1", "activity:2"]},
            ],
            source_lookup,
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(
            messages[0]["content"]["text"],
            "活动价268元，线上名额有限。\n\n到店抵扣；未做或不满意可退，实际按付款记录核对。",
        )

    async def test_quiet_hour_backlog_waits_until_configured_morning_time(self) -> None:
        repo = _Repo()
        selector = _FusionSelector({"send_sop": True, "reply_messages": [{"type": "text", "text": "补发"}]})
        service = _service(
            repo=repo,
            client=_OutreachClient(messages=[]),
            selector=selector,
            pack_service=_BacklogPackService(),
            quiet_backlog_fusion_time="08:30",
        )
        payload = _base_payload(
            event_id="evt_quiet_wait",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-20T02:10:00+08:00",
            sop={"delay_minutes": 60, "stage_tag": "activity"},
            customers=[{"first_added_event": {"trace_id": "trace_backlog_wait"}}],
        )

        repo.create_sop_event(payload)
        await service.process_event("evt_quiet_wait")
        result = await service.process_due_quiet_backlog_fusions(now=_dt("2026-07-20T08:29:00+08:00"))

        self.assertEqual(result, [])
        self.assertEqual(selector.calls, [])

    async def test_quiet_hour_backlog_next_poll_skips_completed_batch(self) -> None:
        repo = _Repo()
        selector = _FusionSelector(
            {
                "send_sop": True,
                "covered_pack_ids": ["effect"],
                "reply_messages": [{"type": "source", "source_id": "effect:1"}],
            }
        )
        service = _service(
            repo=repo,
            client=_OutreachClient(messages=[]),
            selector=selector,
            pack_service=_BacklogPackService(),
            quiet_backlog_fusion_time="08:30",
        )
        service.quiet_backlog_fusion_batch_size = 1
        for index in range(2):
            payload = _base_payload(
                event_id=f"evt_quiet_batch_{index}",
                event_type="sop_friend_added_schedule_batch",
                created_at="2026-07-20T02:10:00+08:00",
                sop={"delay_minutes": 60, "stage_tag": "activity"},
                customers=[
                    {
                        "customer_id": f"customer_{index}",
                        "external_userid": f"external_{index}",
                        "first_added_event": {"trace_id": f"trace_batch_{index}"},
                    }
                ],
            )
            repo.create_sop_event(payload)
            await service.process_event(payload["event_id"])

        first = await service.process_due_quiet_backlog_fusions(now=_dt("2026-07-20T08:31:00+08:00"))
        second = await service.process_due_quiet_backlog_fusions(now=_dt("2026-07-20T08:32:00+08:00"))
        third = await service.process_due_quiet_backlog_fusions(now=_dt("2026-07-20T08:33:00+08:00"))

        self.assertEqual([item["status"] for item in first], ["sent"])
        self.assertEqual([item["status"] for item in second], ["sent"])
        self.assertEqual(third, [])
        self.assertEqual(len(selector.calls), 2)
        self.assertEqual(
            {call["backlog_task_ids"][0] for call in selector.calls},
            {"task_1", "task_2"},
        )

    async def test_quiet_hour_backlog_admin_logs_link_source_tasks_and_result(self) -> None:
        repo = _Repo()
        selector = _FusionSelector(
            {
                "send_sop": True,
                "covered_pack_ids": ["effect"],
                "reply_messages": [{"type": "source", "source_id": "effect:1"}],
            }
        )
        service = _service(
            repo=repo,
            client=_OutreachClient(messages=[]),
            selector=selector,
            pack_service=_BacklogPackService(),
            quiet_backlog_fusion_time="08:30",
        )
        payload = _base_payload(
            event_id="evt_quiet_admin",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-08-25T02:10:00+08:00",
            sop={"delay_minutes": 60, "stage_tag": "activity"},
            customers=[{"first_added_event": {"trace_id": "trace_admin"}}],
        )
        repo.create_sop_event(payload)
        await service.process_event(payload["event_id"])
        await service.process_due_quiet_backlog_fusions(now=_dt("2026-08-25T08:31:00+08:00"))

        result = service.admin_quiet_backlog_logs(local_date="2026-08-25")

        self.assertEqual(result["summary"]["night_task_count"], 1)
        self.assertEqual(result["summary"]["customer_count"], 1)
        self.assertEqual(result["summary"]["fusion_count"], 1)
        self.assertEqual(result["summary"]["sent_count"], 1)
        self.assertEqual(result["items"][0]["source_task_ids"], ["task_1"])
        detail = service.admin_quiet_backlog_detail(result["items"][0]["event_id"])
        self.assertEqual(detail["source_tasks"][0]["id"], "task_1")
        self.assertEqual(detail["timeline"][-1]["stage"], "sent")

    async def test_quiet_hour_backlog_fuses_third_party_first_add_original_messages(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[{"direction": "assistant", "content": "昨晚比较晚。"}])
        selector = _FusionSelector(
            {
                "send_sop": True,
                "covered_pack_ids": ["platform-sop-101"],
                "reply_messages": [
                    {"type": "source", "source_id": "platform-sop-101:1"},
                    {"type": "source", "source_id": "platform-sop-101:2"},
                ],
            }
        )
        service = _service(
            repo=repo,
            client=client,
            selector=selector,
            pack_service=_BacklogPackService(),
            quiet_backlog_fusion_time="08:30",
        )
        platform_event = {
            "event_id": "platform_sop_task:101",
            "event_type": "platform_sop_task",
            "created_at": "2026-07-20T02:10:00+08:00",
            "platform_task": {
                "taskId": 101,
                "triggerEvent": "add_wecom",
                "ruleName": "夜间首加效果活动",
            },
        }
        repo.create_sop_event(platform_event)
        repo.create_sop_send_task(
            event_id="platform_sop_task:101",
            idempotency_key="platform-sop:101",
            send_once_key="platform-sop:101",
            customer_id="customer",
            external_userid="external",
            corp_id="ww943af61cd5d2afe4",
            user_id="staff",
            wechat="CS001",
            sop_pack_id="platform-sop-101",
            sop_pack_name="夜间首加效果活动",
            sop_category="platform_task",
            trigger_source="third_party_sop_pending",
            reply_messages=[
                {"type": "text", "order": 1, "content": {"text": "这是活动和效果介绍。"}},
                {"type": "image", "order": 2, "content": {"url": "https://example.com/platform-case.png"}},
            ],
            status="completed_without_send",
        )
        repo.tasks[-1]["send_payload"] = {
            "decision": {"reason": "quiet_hours_first_add_inactive"},
        }
        repo.tasks[-1]["created_at"] = "2026-07-20T02:10:00+08:00"

        result = await service.process_due_quiet_backlog_fusions(now=_dt("2026-07-20T08:31:00+08:00"))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "sent")
        self.assertEqual(selector.calls[0]["backlog_sops"][0]["id"], "platform-sop-101")
        self.assertEqual(selector.calls[0]["backlog_sops"][0]["messages"][0]["text"], "这是活动和效果介绍。")
        sent_messages = client.send_calls[-1]["reply_messages"]
        self.assertEqual([item["type"] for item in sent_messages], ["text", "image"])
        self.assertEqual(sent_messages[0]["content"]["text"], "这是活动和效果介绍。")
        self.assertEqual(sent_messages[1]["content"]["url"], "https://example.com/platform-case.png")

    async def test_event_merge_sends_two_adjacent_packs_as_one_sequence(self) -> None:
        class _MergePackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "effect",
                            "enabled": True,
                            "scope": "event_first_add",
                            "sop_category": "effect_case",
                            "name": "效果铺垫",
                            "order": 10,
                            "event_type": "sop_friend_added_schedule_batch",
                            "delay_minutes": 30,
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "先给您看效果。"}},
                                {"type": "image", "order": 2, "content": {"url": "https://example.com/case.png"}},
                            ],
                        },
                        {
                            "id": "activity",
                            "enabled": True,
                            "scope": "event_first_add",
                            "sop_category": "activity_intro",
                            "name": "活动介绍",
                            "order": 20,
                            "event_type": "sop_friend_added_schedule_batch",
                            "delay_minutes": 60,
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "新客活动是268。"}}
                            ],
                        },
                    ]
                }

        repo = _Repo()
        memory = _MemoryStore()
        client = _OutreachClient(messages=[])
        selector = _Selector(
            {
                "decision": "merge",
                "send_sop": True,
                "selected_pack_ids": ["effect", "activity"],
                "text_adjustments": [],
                "message_operations": [],
                "reason": "recover adjacent backlog",
            }
        )
        service = _service(
            repo=repo,
            client=client,
            selector=selector,
            pack_service=_MergePackService(),
            memory_store=memory,
        )
        payload = _base_payload(
            event_id="evt_merge",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-11T02:00:00+00:00",
            sop={"delay_minutes": 60},
            customers=[{"first_added_event": {"trace_id": "trace_merge", "timestamp": "2026-07-11T01:00:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_merge")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(repo.tasks[0]["sop_pack_id"], "merge:effect+activity")
        self.assertEqual(repo.tasks[0]["send_payload"]["selected_sop_pack_ids"], ["effect", "activity"])
        sent_messages = client.send_calls[0]["reply_messages"]
        self.assertEqual([item["order"] for item in sent_messages], [1, 2, 3])
        self.assertEqual([item["type"] for item in sent_messages], ["text", "image", "text"])
        self.assertEqual([item["sop_pack_id"] for item in memory.record_calls], ["effect", "activity"])

    async def test_first_added_event_ignores_platform_auto_opening_message(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "from": "customer",
                    "source": "wecom_system",
                    "msgtype": "text",
                    "content": "我已经添加了你，现在我们可以开始聊天了。",
                    "msgtime": "2026-07-02T04:00:00+00:00",
                }
            ]
        )
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "auto opening is not reply"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_auto_opening",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-02T04:10:00+00:00",
            sop={"delay_minutes": 1},
            customers=[{"first_added_event": {"trace_id": "trace_auto_opening", "timestamp": "2026-07-02T03:50:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_auto_opening")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(repo.tasks[0]["send_payload"]["conversation_activity"]["ignored_auto_opening_count"], 1)

    async def test_first_added_event_skips_customer_message_without_reliable_time(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[{"from": "customer", "msgtype": "text", "content": "活动多少钱？"}])
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "should not run"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_customer_time_unknown",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-02T04:10:00+00:00",
            sop={"delay_minutes": 1},
            customers=[{"first_added_event": {"trace_id": "trace_customer_time_unknown", "timestamp": "2026-07-02T03:50:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_customer_time_unknown")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_customer_timing_uncertain")
        activity = repo.tasks[0]["send_payload"]["conversation_activity"]
        self.assertTrue(activity["uncertain_customer_timing"])
        self.assertEqual(activity["reason"], "customer_message_time_unknown")
        self.assertEqual(selector.calls, [])
        self.assertEqual(client.send_calls, [])

    async def test_active_reply_config_no_longer_supplies_first_add_event_track(self) -> None:
        pack_service = SopReplyPackService(SimpleNamespace(sop_reply_packs_path=Path("config/sop_reply_packs.json")))
        config = pack_service.load()
        self.assertEqual(
            first_add_candidate_packs(
                config,
                completed_sop_pack_ids=[],
                completed_sop_categories=[],
                delay_minutes=70,
                payment_state="unpaid",
            ),
            [],
        )

    async def test_first_add_event_can_send_ai_touch_when_fixed_pack_is_unsuitable(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        selector = _Selector(
            {
                "decision": "send_ai_touch",
                "strategy": "soft_touch",
                "touch_goal": "resume_mainline",
                "send_sop": False,
                "ai_touch_messages": [
                    {
                        "type": "text",
                        "content": {"text": "亲，前面的内容我先不重复发了，您主要担心效果还是门店？"},
                    }
                ],
                "reason": "fixed pack repeats recent conversation; use light touch",
            }
        )
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_ai_touch",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-11T02:00:00+00:00",
            sop={"delay_minutes": 30},
            customers=[{"first_added_event": {"trace_id": "trace_ai_touch", "timestamp": "2026-07-11T01:00:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_ai_touch")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(repo.tasks[0]["sop_pack_id"], "send_ai_touch")
        self.assertEqual(repo.tasks[0]["sop_category"], "sop_ai_touch")
        self.assertEqual(client.send_calls[0]["reply_messages"][0]["type"], "text")

    async def test_supported_event_payment_pack_cannot_be_downgraded_for_missing_order(self) -> None:
        repo = _Repo()
        repo.sent_ids.add("s10_activity_intro")
        repo.sent_categories.add("activity_intro")
        client = _OutreachClient(messages=[])
        selector = _Selector(
            {
                "send_sop": True,
                "sop_pack_id": "deposit_pack",
                "reason": "unsupported downgrade attempt",
                "message_operations": [
                    {"op": "remove_message", "order": 2},
                    {
                        "op": "replace_text",
                        "order": 1,
                        "text": "亲，10元预约金是先留活动名额，到店抵扣；您后面想来我再帮您登记。",
                    },
                ],
            }
        )
        service = _service(repo=repo, client=client, selector=selector, pack_service=_DepositPackService())
        payload = _base_payload(
            event_id="evt_deposit_text_touch",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-11T02:00:00+00:00",
            sop={"delay_minutes": 70},
            customers=[{"first_added_event": {"trace_id": "trace_deposit_text_touch", "timestamp": "2026-07-11T01:00:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_deposit_text_touch")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual([item["type"] for item in repo.tasks[0]["reply_messages"]], ["text", "payment_collection"])
        self.assertEqual(
            repo.tasks[0]["send_payload"]["message_adjustment"]["rejected"][0]["reason"],
            "payment_collection_not_removable",
        )
        self.assertEqual(len(client.send_calls), 1)

    async def test_paid_current_order_skips_sop_before_model_and_send(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "should not run"})
        context_service = _CustomerContextService(
            {
                "source": "platform_agent",
                "orders": [
                    {
                        "id": "order-paid",
                        "status": "waiting_schedule",
                        "store_id": "386",
                        "prepay_required": 10,
                        "prepay_paid": 10,
                        "is_current_order": True,
                    }
                ],
            }
        )
        service = _service(
            repo=repo,
            client=client,
            selector=selector,
            customer_context_service=context_service,
        )
        payload = _base_payload(
            event_id="evt_paid_skip",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-11T02:00:00+00:00",
            sop={"delay_minutes": 5},
            customers=[{"first_added_event": {"trace_id": "trace_paid", "timestamp": "2026-07-11T01:00:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_paid_skip")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "skipped_deposit_paid")
        self.assertEqual(repo.tasks[0]["send_payload"]["order_gate"]["deposit_state"], "paid_by_order")
        self.assertEqual(len(context_service.load_calls), 1)
        self.assertEqual(selector.calls, [])
        self.assertEqual(client.send_calls, [])

    async def test_order_query_failure_blocks_sop_before_model_and_send(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "should not run"})
        context_service = _CustomerContextService(
            {
                "source": "platform_agent",
                "orders": [],
                "orders_error": "TimeoutError: timed out",
            }
        )
        service = _service(
            repo=repo,
            client=client,
            selector=selector,
            customer_context_service=context_service,
        )
        payload = _base_payload(
            event_id="evt_order_fetch_failed",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-11T02:00:00+00:00",
            sop={"delay_minutes": 5},
            customers=[{"first_added_event": {"trace_id": "trace_order_failed", "timestamp": "2026-07-11T01:00:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_order_fetch_failed")

        self.assertEqual(result["status"], "retry_pending_model")
        self.assertEqual(repo.tasks[0]["status"], "failed_order_fetch")
        self.assertIn("TimeoutError", repo.tasks[0]["error"])
        self.assertEqual(selector.calls, [])
        self.assertEqual(client.send_calls, [])

        context_service.context = {"source": "platform_agent", "orders": []}
        recovered = await service.process_due_model_retries()

        self.assertEqual(recovered[0]["status"], "processed")
        self.assertEqual(len(client.send_calls), 1)

    async def test_successful_send_records_minimal_sop_history_and_last_outreach_time(self) -> None:
        repo = _Repo()
        existing_memory = {
            "portrait": {"concern": "价格"},
            "basic_info": {"city": "深圳"},
            "lifecycle_stage": "new_customer",
            "history_events": [{"event_id": "history_1", "event_type": "store_matched"}],
        }
        memory_store = _MemoryStore(existing_memory)
        client = _OutreachClient(messages=[])
        selector = _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "send opening"})
        service = _service(repo=repo, client=client, selector=selector, memory_store=memory_store)
        payload = _base_payload(
            event_id="evt_record_sop_sent",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-11T02:00:00+00:00",
            sop={"delay_minutes": 1},
            customers=[{"first_added_event": {"trace_id": "trace_record_sop_sent", "timestamp": "2026-07-11T01:00:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_record_sop_sent")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        scope_key = build_customer_scope(
            corp_id="ww943af61cd5d2afe4",
            wechat="CS001",
            external_userid="ext_user",
            customer_id="ext_user",
        ).sales_contact_key
        self.assertEqual(memory_store.load_calls, [scope_key])
        self.assertEqual(selector.calls[0]["customer_memory"], existing_memory)
        self.assertEqual(len(memory_store.record_calls), 1)
        record = memory_store.record_calls[0]
        self.assertEqual(record["customer_id"], scope_key)
        self.assertEqual(record["sop_pack_id"], "opening")
        self.assertEqual(record["message_types"], ["text"])
        self.assertEqual(record["source_event_id"], "evt_record_sop_sent")
        self.assertNotIn("reply_messages", record)
        self.assertEqual(repo.message_times[0]["field"], "last_outreach_at")
        self.assertTrue(repo.message_times[0]["value"])

    async def test_rejected_or_failed_send_does_not_record_sop_history(self) -> None:
        cases = (
            (_Selector({"send_sop": False, "reason": "reject"}), _OutreachClient(messages=[]), "skipped_model_rejected"),
            (
                _Selector({"send_sop": True, "sop_pack_id": "opening", "reason": "send"}),
                _OutreachClient(messages=[], send_result={"status": "failed", "reason": "send_failed"}),
                "failed",
            ),
        )
        for index, (selector, client, expected_status) in enumerate(cases):
            with self.subTest(expected_status=expected_status):
                repo = _Repo()
                memory_store = _MemoryStore()
                service = _service(repo=repo, client=client, selector=selector, memory_store=memory_store)
                event_id = f"evt_no_record_{index}"
                payload = _base_payload(
                    event_id=event_id,
                    event_type="sop_friend_added_schedule_batch",
                    created_at="2026-07-11T02:00:00+00:00",
                    sop={"delay_minutes": 1},
                    customers=[{"first_added_event": {"trace_id": f"trace_no_record_{index}", "timestamp": "2026-07-11T01:00:00+00:00"}}],
                )

                repo.create_sop_event(payload)
                await service.process_event(event_id)

                self.assertEqual(repo.tasks[0]["status"], expected_status)
                self.assertEqual(memory_store.record_calls, [])
                self.assertEqual(repo.message_times, [])

    def test_memory_store_sop_event_contains_no_message_body_or_identity(self) -> None:
        with TemporaryDirectory() as tmpdir:
            memory_store = CustomerMemoryStore(SimpleNamespace(memory_dir=Path(tmpdir)))

            result = memory_store.record_sop_pack_sent(
                "customer_1",
                sop_pack_id="opening",
                sop_category="opening",
                source_event_id="evt_1",
                message_types=["text", "image", "text"],
                sent_at="2026-07-11T02:00:00+00:00",
                task_id="task_1",
            )
            memory = memory_store.load("customer_1")

        self.assertEqual(result["status"], "recorded")
        event = memory["history_events"][0]
        self.assertEqual(event["event_type"], "sop_pack_sent")
        self.assertEqual(event["facts"]["message_types"], ["text", "image"])
        serialized = str(event)
        self.assertNotIn("content", serialized)
        self.assertNotIn("corp_id", serialized)
        self.assertNotIn("external_userid", serialized)

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
        selector = _Selector({"send_sop": True, "reason": "send platform deposit"})
        service = _service(
            repo=repo,
            client=client,
            selector=selector,
            memory_store=_MemoryStore(
                {
                    "basic_info": {
                        "confirmed_store_id": "386",
                        "confirmed_store_name": "厦门百星店",
                        "order_state": {"order_id": "order-30", "store_id": "386", "prepay_required": 30},
                    }
                }
            ),
            customer_context_service=_CustomerContextService(
                {
                    "source": "platform_agent",
                    "orders": [
                        {
                            "id": "order-30",
                            "status": "pending",
                            "store_id": "386",
                            "prepay_required": 30,
                            "prepay_paid": 0,
                            "is_current_order": True,
                        }
                    ],
                }
            ),
        )
        payload = _base_payload(
            event_id="evt_group_payment",
            event_type="sop_platform_task",
            sop={"platform_task_id": "group_payment", "actions": _DepositPackService().load()["packs"][0]["reply_messages"]},
            customers=[{}],
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
        selector = _Selector({"send_sop": True, "reason": "send platform deposit"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_group_payment_over_limit",
            event_type="sop_platform_task",
            sop={"platform_task_id": "group_payment_over_limit", "actions": _DepositPackService().load()["packs"][0]["reply_messages"]},
            customers=[{}],
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

    def test_sop_message_operations_can_adjust_text_count_without_touching_structures(self) -> None:
        messages, summary = apply_sop_text_adjustments(
            [
                {"type": "text", "order": 1, "content": {"text": "您好，温馨提醒您及时参与活动。"}},
                {"type": "text", "order": 2, "content": {"text": "感谢您的关注。"}},
                {"type": "image", "order": 3, "content": {"url": "https://example.com/a.jpg"}},
            ],
            [],
            [
                {"op": "replace_text", "order": 1, "text": "亲，前面说的活动还在，我简单接着跟您说。"},
                {"op": "insert_text_after", "after_order": 1, "text": "您刚才有顾虑的话直接问我就行。"},
                {"op": "remove_text", "order": 2},
            ],
        )

        self.assertEqual([item["type"] for item in messages], ["text", "text", "image"])
        self.assertEqual(messages[0]["content"]["text"], "亲，前面说的活动还在，我简单接着跟您说。")
        self.assertEqual(messages[1]["content"]["text"], "您刚才有顾虑的话直接问我就行。")
        self.assertEqual(messages[2]["content"]["url"], "https://example.com/a.jpg")
        self.assertEqual([item["op"] for item in summary["applied_operations"]], ["replace_text", "insert_text_after", "remove_text"])

    def test_sop_message_operations_reject_new_numeric_facts(self) -> None:
        messages, summary = apply_sop_text_adjustments(
            [{"type": "text", "order": 1, "content": {"text": "亲，活动还在。"}}],
            [],
            [{"op": "insert_text_after", "after_order": 1, "text": "现在只要10元。"}],
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(summary["applied_operations"], [])
        self.assertEqual(summary["rejected"][0]["reason"], "numeric_facts_changed")

    def test_sop_message_operations_preserve_trailing_action_after_structured_message(self) -> None:
        messages, summary = apply_sop_text_adjustments(
            [
                {"type": "text", "order": 1, "content": {"text": "这次活动内容我跟您说清楚。"}},
                {"type": "image", "order": 2, "content": {"url": "https://example.com/activity.jpg"}},
                {"type": "text", "order": 3, "content": {"text": "您到店时间后面按方便安排。"}},
                {"type": "text", "order": 4, "content": {"text": "您这边是一位参加吗？"}},
            ],
            [],
            [
                {"op": "remove_text", "order": 3},
                {"op": "remove_text", "order": 4},
            ],
        )

        self.assertEqual([item["type"] for item in messages], ["text", "image", "text"])
        self.assertEqual(messages[-1]["content"]["text"], "您这边是一位参加吗？")
        self.assertEqual(summary["applied_operations"], [{"op": "remove_text", "order": 3}])
        self.assertEqual(summary["rejected"][-1]["reason"], "trailing_action_text_required")

    async def test_platform_task_sends_platform_actions_after_event_model_approval(self) -> None:
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
        self.assertEqual(repo.tasks[0]["send_payload"]["routing_mode"], "event_guarded_platform_actions")
        self.assertEqual(len(selector.calls), 1)

    async def test_platform_payment_action_is_deferred_when_event_model_confirms_customer_busy(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "direction": "customer",
                    "content": "我在工作，晚点说",
                    "msgtime": "2026-07-31T09:00:00+08:00",
                },
                {
                    "direction": "staff",
                    "content": "好，您先忙，方便时再回我",
                    "msgtime": "2026-07-31T09:01:00+08:00",
                },
            ]
        )
        selector = _Selector(
            {
                "decision": "defer",
                "strategy": "availability_guard",
                "send_sop": False,
                "reason": "customer_busy_with_valid_acknowledgement",
                "contact_availability_decision": {
                    "status": "busy_now",
                    "customer_evidence_ref": "conv_1",
                    "assistant_acknowledgement_ref": "conv_2",
                    "reason": "客户当前在工作，助手已承接等待",
                },
                "availability_guard": {"active": True, "minutes_since_ack": 45},
            }
        )
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_platform_busy_payment",
            event_type="sop_platform_task",
            sop={
                "actions": [
                    {"type": "text", "content": "现在交10元预约金留名额"},
                    {"type": "payment_collection", "content": {"amount": 10}},
                ]
            },
            customers=[{"conversation": {"ai_auto_reply": True}}],
            created_at="2026-07-31T09:46:00+08:00",
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_platform_busy_payment")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "deferred_model")
        self.assertEqual(client.send_calls, [])
        self.assertEqual(len(selector.calls), 1)
        self.assertEqual(selector.calls[0]["actions_reply_messages"][1]["type"], "payment_collection")
        self.assertIn("event_policy_evidence", selector.calls[0])

    async def test_day1_platform_task_is_suppressed_to_avoid_duplicate_first_day_touch(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        service = _service(repo=repo, client=client)
        payload = _base_payload(
            event_id="evt_platform_day1",
            event_type="sop_platform_task",
            sop={
                "day_stage": "day1",
                "platform_task": {"message_content": [{"type": "text", "content": "平台第一天任务"}]},
            },
            customers=[{}],
        )

        repo.create_sop_event(payload)
        await service.process_event("evt_platform_day1")

        self.assertEqual(repo.tasks[0]["status"], "skipped_day1_platform_task")
        self.assertEqual(client.send_calls, [])

    async def test_platform_conversation_fetch_failure_is_retried_before_routing(self) -> None:
        repo = _Repo()
        client = _OutreachClient(fetch_result={"status": "failed", "error": "TimeoutError: timed out"})
        service = _service(
            repo=repo,
            client=client,
            selector=_Selector({"send_sop": True, "reason": "send after conversation recovery"}),
        )
        payload = _base_payload(
            event_id="evt_platform_fetch_retry",
            event_type="sop_platform_task",
            sop={"day_stage": "day2", "actions": [{"type": "text", "content": "平台后续触达"}]},
            customers=[{}],
        )

        repo.create_sop_event(payload)
        first = await service.process_event("evt_platform_fetch_retry")

        self.assertEqual(first["status"], "retry_pending_model")
        self.assertEqual(repo.tasks[0]["status"], "failed_conversation_fetch")
        self.assertEqual(client.send_calls, [])

        client.fetch_result = {"status": "ok", "messages": []}
        recovered = await service.process_due_model_retries()

        self.assertEqual(recovered[0]["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(len(client.send_calls), 1)

    async def test_day2_spoken_customer_without_order_filters_platform_task_and_creates_plan(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "direction": "customer",
                    "content": "你们效果真的好吗",
                    "msgtime": "2026-07-27T03:00:00+00:00",
                }
            ]
        )
        personalized = _PersonalizedOutreachService()
        service = _service(
            repo=repo,
            client=client,
            personalized_outreach_service=personalized,
        )
        payload = _base_payload(
            event_id="evt_platform_day2_personalized",
            event_type="sop_platform_task",
            sop={
                "day_stage": "day2",
                "platform_task": {"message_content": [{"type": "text", "content": "平台统一效果跟进"}]},
            },
            customers=[{}],
            created_at="2026-07-28T02:00:00+00:00",
        )

        repo.create_sop_event(payload)
        await service.process_event("evt_platform_day2_personalized")

        self.assertEqual(repo.tasks[0]["status"], "filtered_personalized_outreach")
        self.assertEqual(client.send_calls, [])
        self.assertEqual(len(personalized.calls), 1)
        self.assertEqual(
            personalized.calls[0]["platform_task"]["messages"][0]["content"]["text"],
            "平台统一效果跟进",
        )

    async def test_day2_spoken_customer_with_pending_order_uses_personalized_plan(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[{"direction": "customer", "content": "我再考虑下"}])
        personalized = _PersonalizedOutreachService()
        context = _CustomerContextService(
            {"source": "platform_agent", "orders": [{"id": "order-1", "status": 1, "is_current_order": True}]}
        )
        service = _service(
            repo=repo,
            client=client,
            customer_context_service=context,
            personalized_outreach_service=personalized,
        )
        payload = _base_payload(
            event_id="evt_platform_day2_pending",
            event_type="sop_platform_task",
            sop={"day_stage": "day2", "actions": [{"type": "text", "content": "统一催付"}]},
            customers=[{}],
        )

        repo.create_sop_event(payload)
        await service.process_event("evt_platform_day2_pending")

        self.assertEqual(repo.tasks[0]["status"], "filtered_personalized_outreach")
        self.assertEqual(len(personalized.calls), 1)
        self.assertEqual(client.send_calls, [])

    async def test_day2_personalized_plan_failure_is_retried_without_forwarding_platform_task(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[{"direction": "customer", "content": "我担心效果"}])
        personalized = _FlakyPersonalizedOutreachService()
        service = _service(
            repo=repo,
            client=client,
            personalized_outreach_service=personalized,
        )
        payload = _base_payload(
            event_id="evt_platform_day2_plan_retry",
            event_type="sop_platform_task",
            sop={"day_stage": "day2", "actions": [{"type": "text", "content": "平台统一效果跟进"}]},
            customers=[{}],
        )

        repo.create_sop_event(payload)
        first = await service.process_event("evt_platform_day2_plan_retry")

        self.assertEqual(first["status"], "retry_pending_model")
        self.assertEqual(repo.tasks[0]["status"], "failed_personalized_outreach_plan")
        self.assertEqual(client.send_calls, [])

        personalized.fail = False
        recovered = await service.process_due_model_retries()

        self.assertEqual(recovered[0]["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "filtered_personalized_outreach")
        self.assertEqual(len(personalized.calls), 2)
        self.assertEqual(client.send_calls, [])

    async def test_day2_never_spoke_and_booked_customers_keep_platform_delivery(self) -> None:
        for suffix, messages, orders in (
            ("silent", [], []),
            (
                "booked",
                [{"direction": "customer", "content": "好的"}],
                [{"id": "order-2", "status": 2, "is_current_order": True}],
            ),
        ):
            repo = _Repo()
            client = _OutreachClient(messages=messages)
            context = _CustomerContextService({"source": "platform_agent", "orders": orders})
            service = _service(
                repo=repo,
                client=client,
                selector=_Selector({"send_sop": True, "reason": "platform followup remains useful"}),
                customer_context_service=context,
            )
            payload = _base_payload(
                event_id=f"evt_platform_day2_{suffix}",
                event_type="sop_platform_task",
                sop={"day_stage": "day2", "actions": [{"type": "text", "content": "平台后续触达"}]},
                customers=[{}],
            )

            repo.create_sop_event(payload)
            await service.process_event(f"evt_platform_day2_{suffix}")

            self.assertEqual(repo.tasks[0]["status"], "sent")
            self.assertEqual(len(client.send_calls), 1)
            self.assertEqual(
                repo.tasks[0]["send_payload"]["platform_task_routing"]["route"],
                "direct",
            )

    async def test_day2_customer_remains_personalized_when_early_reply_is_outside_recent_window(self) -> None:
        repo = _Repo()
        client = _OutreachClient(
            messages=[
                {
                    "direction": "staff",
                    "content": "后续统一触达内容",
                    "msgtime": "2026-07-28T02:00:00+00:00",
                }
            ]
        )
        memory = _MemoryStore({"last_customer_message_at": "2026-07-26T02:00:00+00:00"})
        personalized = _PersonalizedOutreachService()
        service = _service(
            repo=repo,
            client=client,
            memory_store=memory,
            personalized_outreach_service=personalized,
        )
        payload = _base_payload(
            event_id="evt_platform_day2_early_spoken",
            event_type="sop_platform_task",
            sop={"day_stage": "day3", "actions": [{"type": "text", "content": "平台第三天触达"}]},
            customers=[{}],
        )

        repo.create_sop_event(payload)
        await service.process_event("evt_platform_day2_early_spoken")

        self.assertEqual(repo.tasks[0]["status"], "filtered_personalized_outreach")
        routing = repo.tasks[0]["send_payload"]["platform_task_routing"]
        self.assertFalse(routing["has_spoken_sources"]["recent_conversation"])
        self.assertTrue(routing["has_spoken_sources"]["persisted_customer_message_time"])
        self.assertEqual(client.send_calls, [])

    async def test_platform_task_uses_event_model_even_when_ai_auto_reply_is_disabled(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        selector = _Selector({"send_sop": True, "reason": "availability checked"})
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_platform_direct",
            event_type="sop_platform_task",
            sop={
                "platform_task_id": "task_direct",
                "platform_task": {"message_content": [{"type": "text", "content": "直接转发文案"}]},
            },
            customers=[{"conversation": {"ai_auto_reply": False}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_platform_direct")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(repo.tasks[0]["reply_messages"][0]["content"]["text"], "直接转发文案")
        self.assertEqual(repo.tasks[0]["send_payload"]["routing_mode"], "event_guarded_platform_actions")
        self.assertEqual(len(selector.calls), 1)

    async def test_paid_platform_task_still_sends_non_payment_arrival_followup(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        selector = _Selector({"send_sop": True, "reason": "已付后的到店提醒仍适用"})
        context_service = _CustomerContextService(
            {
                "source": "platform_agent",
                "orders": [
                    {
                        "id": "order-paid",
                        "status": "waiting_schedule",
                        "store_id": "386",
                        "prepay_required": 10,
                        "prepay_paid": 10,
                        "is_current_order": True,
                    }
                ],
            }
        )
        service = _service(repo=repo, client=client, selector=selector, customer_context_service=context_service)
        payload = _base_payload(
            event_id="evt_platform_paid_arrival",
            event_type="sop_platform_task",
            sop={"platform_task_id": "task_arrival", "actions": [{"type": "text", "content": "今天到店我给您安排接待。"}]},
            customers=[{"conversation": {"ai_auto_reply": True}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_platform_paid_arrival")

        self.assertEqual(result["status"], "processed")
        self.assertEqual(repo.tasks[0]["status"], "sent")
        self.assertEqual(len(selector.calls), 1)
        self.assertEqual(len(client.send_calls), 1)

    async def test_platform_action_decodes_json_quoted_message_content(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        service = _service(repo=repo, client=client, selector=_Selector({"send_sop": True, "reason": "send"}))
        payload = _base_payload(
            event_id="evt_platform_quoted",
            event_type="sop_platform_task",
            sop={"actions": [{"type": "text", "message_content": '"催到店话术\\n请回复时间"'}]},
            customers=[{"conversation": {"ai_auto_reply": True}}],
        )

        repo.create_sop_event(payload)
        await service.process_event("evt_platform_quoted")

        self.assertEqual(repo.tasks[0]["reply_messages"][0]["content"]["text"], "催到店话术\n请回复时间")

    async def test_platform_task_model_failure_is_persisted_for_retry(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        selector = _Selector(
            {
                "send_sop": False,
                "reason": "event_sop_model_retries_exhausted",
                "error": "TimeoutError: total timeout 45.0s",
                "model_attempts": [{"attempt": 1, "status": "failed"}],
            }
        )
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_platform_model_failed",
            event_type="sop_platform_task",
            sop={"actions": [{"type": "text", "content": "触达内容"}]},
            customers=[{"conversation": {"ai_auto_reply": True}}],
        )

        repo.create_sop_event(payload)
        result = await service.process_event("evt_platform_model_failed")

        self.assertEqual(result["status"], "retry_pending_model")
        self.assertEqual(repo.tasks[0]["status"], "failed_model_error")
        self.assertEqual(len(selector.calls), 1)
        self.assertEqual(client.send_calls, [])

    async def test_first_add_model_failure_is_persisted_and_retried(self) -> None:
        repo = _Repo()
        client = _OutreachClient(messages=[])
        selector = _Selector(
            {
                "send_sop": False,
                "reason": "event_sop_model_retries_exhausted",
                "error": "TimeoutError: total timeout 45.0s",
            }
        )
        service = _service(repo=repo, client=client, selector=selector)
        payload = _base_payload(
            event_id="evt_first_add_model_retry",
            event_type="sop_friend_added_schedule_batch",
            created_at="2026-07-11T02:00:00+00:00",
            sop={"delay_minutes": 1},
            customers=[{"first_added_event": {"trace_id": "trace_retry", "timestamp": "2026-07-11T01:00:00+00:00"}}],
        )

        repo.create_sop_event(payload)
        first = await service.process_event("evt_first_add_model_retry")

        self.assertEqual(first["status"], "retry_pending_model")
        self.assertEqual(repo.events["evt_first_add_model_retry"]["retry_count"], 1)
        self.assertEqual(client.send_calls, [])

        selector.output = {"send_sop": True, "sop_pack_id": "opening", "reason": "retry recovered"}
        retried = await service.process_due_model_retries()

        self.assertEqual(retried[0]["status"], "processed")
        self.assertEqual(len(client.send_calls), 1)
        self.assertEqual(repo.events["evt_first_add_model_retry"]["status"], "processed")
        self.assertTrue(any(task.get("status") == "model_retry_resolved" for task in repo.tasks))

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
                "send_once_key": "sop_pack:opening|corp:ww943af61cd5d2afe4|wechat:cs001|external:ext_user",
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

    def test_repository_model_retry_queue_persists_recovers_and_exhausts(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "ai_paths.db"))
            store.initialize()
            repo = AppRepository(store)
            repo.create_sop_event({"event_id": "evt_retry_queue", "event_type": "sop_friend_added_schedule_batch"})

            first = repo.schedule_sop_event_model_retry(
                "evt_retry_queue",
                error="TimeoutError: first",
                max_attempts=2,
                base_delay_seconds=0,
                max_delay_seconds=0,
            )
            self.assertEqual(first["status"], "retry_pending_model")
            self.assertEqual(first["retry_count"], 1)
            self.assertEqual(first["last_retry_error"], "TimeoutError: first")

            claimed = repo.claim_due_sop_event_model_retries(limit=1)
            self.assertEqual(claimed, [{"event_id": "evt_retry_queue"}])
            self.assertEqual(repo.get_sop_event("evt_retry_queue")["status"], "retry_processing_model")

            self.assertEqual(repo.recover_interrupted_sop_event_model_retries(), 1)
            self.assertEqual(repo.get_sop_event("evt_retry_queue")["status"], "retry_pending_model")

            second = repo.schedule_sop_event_model_retry(
                "evt_retry_queue",
                error="TimeoutError: second",
                max_attempts=2,
                base_delay_seconds=0,
                max_delay_seconds=0,
            )
            self.assertEqual(second["status"], "retry_pending_model")
            self.assertEqual(second["retry_count"], 2)

            exhausted = repo.schedule_sop_event_model_retry(
                "evt_retry_queue",
                error="TimeoutError: third",
                max_attempts=2,
                base_delay_seconds=0,
                max_delay_seconds=0,
            )
            self.assertEqual(exhausted["status"], "retry_exhausted_model")
            self.assertEqual(exhausted["retry_count"], 3)
            self.assertEqual(exhausted["next_retry_at"], "")
            self.assertEqual(exhausted["error"], "TimeoutError: third")
            self.assertEqual(repo.claim_due_sop_event_model_retries(limit=1), [])

    def test_repository_success_clears_pending_model_retry_time(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "ai_paths.db"))
            store.initialize()
            repo = AppRepository(store)
            repo.create_sop_event({"event_id": "evt_retry_resolved", "event_type": "sop_friend_added_schedule_batch"})
            pending = repo.schedule_sop_event_model_retry(
                "evt_retry_resolved",
                error="TimeoutError: retry me",
                max_attempts=2,
                base_delay_seconds=30,
                max_delay_seconds=30,
            )
            self.assertTrue(pending["next_retry_at"])

            resolved = repo.update_sop_event_status("evt_retry_resolved", status="processed")

            self.assertEqual(resolved["status"], "processed")
            self.assertEqual(resolved["next_retry_at"], "")

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

    def test_repository_sop_progress_queries_are_wechat_case_insensitive(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "ai_paths.db"))
            store.initialize()
            repo = AppRepository(store)
            repo.create_sop_event({"event_id": "evt_case_sop", "event_type": "chat_gate"})
            task = repo.create_sop_send_task(
                event_id="evt_case_sop",
                idempotency_key="idem_case_sop",
                customer_id="external_case",
                external_userid="external_case",
                corp_id="ww",
                user_id="7294",
                wechat="DY258",
                sop_pack_id="s10_new_customer_opening",
                sop_pack_name="新客破冰",
                sop_category="s10_new_customer_opening",
                trigger_source="platform_auto_opening",
                reply_messages=[],
                status="pending",
            )
            repo.update_sop_send_task(task["id"], status="sent", sent_at="2026-07-23T15:05:27+00:00")

            self.assertEqual(
                repo.list_sent_sop_pack_ids_for_customer(
                    customer_id="external_case",
                    external_userid="external_case",
                    corp_id="ww",
                    wechat="dy258",
                ),
                ["s10_new_customer_opening"],
            )
            self.assertEqual(
                repo.list_sent_sop_categories_for_customer(
                    customer_id="external_case",
                    external_userid="external_case",
                    corp_id="ww",
                    wechat="dy258",
                ),
                ["s10_new_customer_opening"],
            )
            recent = repo.list_recent_sop_send_tasks_for_customer(
                customer_id="external_case",
                external_userid="external_case",
                corp_id="ww",
                wechat="dy258",
            )
            self.assertEqual([item["id"] for item in recent], [task["id"]])

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
                    corp_id="ww",
                    wechat="CS001",
                    sent_before="2026-07-02T06:30:00+00:00",
                ),
                ["before_pack"],
            )
            self.assertEqual(
                repo.list_sent_sop_categories_for_customer(
                    customer_id="ext",
                    external_userid="ext",
                    corp_id="ww",
                    wechat="CS001",
                    sent_before="2026-07-02T06:30:00+00:00",
                ),
                ["before_category"],
            )

    def test_repository_current_sop_progress_includes_platform_auto_opening(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "ai_paths.db"))
            store.initialize()
            repo = AppRepository(store)
            repo.create_sop_event({"event_id": "platform_auto_opening:req", "event_type": "chat_gate"})
            task = repo.create_sop_send_task(
                event_id="platform_auto_opening:req",
                idempotency_key="idem_auto_opening",
                customer_id="customer",
                external_userid="ext",
                corp_id="ww",
                user_id="7294",
                wechat="CS001",
                sop_pack_id="s10_new_customer_opening",
                sop_pack_name="new customer opening",
                sop_category="s10_new_customer_opening",
                trigger_source="platform_auto_opening",
                reply_messages=[],
                status="pending",
            )
            repo.update_sop_send_task(
                task["id"],
                status="sent",
                sent_at="2026-07-02T07:00:00+00:00",
            )

            self.assertEqual(
                repo.list_sent_sop_pack_ids_for_customer(
                    customer_id="customer",
                    external_userid="ext",
                    corp_id="ww",
                    wechat="CS001",
                    sent_before="2026-07-02T06:30:00+00:00",
                ),
                [],
            )
            self.assertEqual(
                repo.list_sent_sop_pack_ids_for_customer(
                    customer_id="customer",
                    external_userid="ext",
                    corp_id="ww",
                    wechat="CS001",
                ),
                ["s10_new_customer_opening"],
            )
            self.assertEqual(
                repo.list_recent_sop_send_tasks_for_customer(
                    customer_id="customer",
                    external_userid="ext",
                    corp_id="ww",
                    wechat="CS001",
                )[0]["trigger_source"],
                "platform_auto_opening",
            )

    def test_repository_expands_merged_pack_progress_from_send_payload(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmpdir) / "ai_paths.db"))
            store.initialize()
            repo = AppRepository(store)
            repo.create_sop_event({"event_id": "evt_merge", "event_type": "sop_friend_added_schedule_batch"})
            task = repo.create_sop_send_task(
                event_id="evt_merge",
                idempotency_key="idem_merge",
                customer_id="ext",
                external_userid="ext",
                corp_id="ww",
                user_id="7294",
                wechat="CS001",
                sop_pack_id="merge:effect+activity",
                sop_pack_name="效果铺垫 + 活动介绍",
                sop_category="merge:effect+activity",
                trigger_source="sop_event",
                reply_messages=[],
                status="pending",
            )
            repo.update_sop_send_task(
                task["id"],
                status="sent",
                sent_at="2026-07-02T06:00:00+00:00",
                send_payload={
                    "selected_sop_pack_ids": ["effect", "activity"],
                    "selected_sop_categories": ["effect_warmup", "activity_intro"],
                },
            )

            self.assertEqual(
                repo.list_sent_sop_pack_ids_for_customer(
                    customer_id="ext", external_userid="ext", corp_id="ww", wechat="CS001"
                ),
                ["effect", "activity"],
            )
            self.assertEqual(
                repo.list_sent_sop_categories_for_customer(
                    customer_id="ext", external_userid="ext", corp_id="ww", wechat="CS001"
                ),
                ["effect_warmup", "activity_intro"],
            )

    def test_current_sop_reply_pack_config_audit_has_no_errors(self) -> None:
        service = SopReplyPackService(SimpleNamespace(sop_reply_packs_path=Path("config/sop_reply_packs.json")))
        config = service.load()

        self.assertEqual(config["audit"]["status"], "ok")
        self.assertEqual(config["audit"]["error_count"], 0)
        opening = next(item for item in config["packs"] if item["id"] == "s10_new_customer_opening")
        self.assertTrue(opening["enabled"])
        self.assertEqual(opening["scopes"], ["chat_gate"])
        self.assertEqual(opening["event_type"], "")
        self.assertEqual(opening["delay_minutes"], 0)
        need_and_case = next(item for item in config["packs"] if item["id"] == "s10_need_and_case")
        self.assertTrue(need_and_case["enabled"])
        self.assertEqual(need_and_case["scopes"], ["chat_gate"])
        self.assertTrue(all(item["scopes"] == ["chat_gate"] for item in config["packs"]))
        self.assertFalse(any(item["id"].startswith("event_") for item in config["packs"]))

    def test_retired_first_add_final_close_is_not_an_active_candidate(self) -> None:
        service = SopReplyPackService(SimpleNamespace(sop_reply_packs_path=Path("config/sop_reply_packs.json")))
        config = service.load()

        early = first_add_candidate_packs(
            config,
            completed_sop_pack_ids=[],
            completed_sop_categories=[],
            delay_minutes=70,
            match_context={"delay_minutes": 70},
        )
        self.assertEqual(early, [])

    def test_first_add_candidates_do_not_look_ahead_before_next_silent_stage(self) -> None:
        service = SopReplyPackService(SimpleNamespace(sop_reply_packs_path=Path("config/sop_reply_packs.json")))
        config = service.load()

        candidates = first_add_candidate_packs(
            config,
            completed_sop_pack_ids=["s10_new_customer_opening", "event_s10_store_prompt_5min"],
            completed_sop_categories=["store_prompt"],
            delay_minutes=10,
            match_context={"delay_minutes": 10},
        )

        self.assertEqual([item["id"] for item in candidates], [])

    def test_first_add_candidates_are_empty_after_event_config_retirement(self) -> None:
        service = SopReplyPackService(SimpleNamespace(sop_reply_packs_path=Path("config/sop_reply_packs.json")))
        config = service.load()

        candidates = first_add_candidate_packs(
            config,
            completed_sop_pack_ids=["s10_new_customer_opening"],
            completed_sop_categories=[],
            delay_minutes=10,
            match_context={"delay_minutes": 10},
        )

        self.assertEqual(candidates, [])

    def test_first_add_semantic_skip_candidates_are_retired(self) -> None:
        service = SopReplyPackService(SimpleNamespace(sop_reply_packs_path=Path("config/sop_reply_packs.json")))
        config = service.load()

        before_effect = first_add_candidate_packs(
            config,
            completed_sop_pack_ids=["event_s10_store_prompt_5min"],
            completed_sop_categories=["store_prompt"],
            delay_minutes=60,
            match_context={"delay_minutes": 60},
        )
        self.assertEqual(before_effect, [])

        after_effect = first_add_candidate_packs(
            config,
            completed_sop_pack_ids=["event_s10_store_prompt_5min", "event_s10_effect_warmup_30min"],
            completed_sop_categories=["store_prompt", "effect_case"],
            delay_minutes=60,
            match_context={"delay_minutes": 60},
        )
        self.assertEqual(after_effect, [])

    def test_event_payment_candidate_requires_activity_stage_skip_evidence(self) -> None:
        selector_input = {
            "mode": "first_add_flow",
            "completed_sop_pack_ids": [],
            "completed_sop_categories": ["store_prompt", "effect_case"],
            "candidate_sops": [
                {
                    "id": "event_s10_price_quote_60min",
                    "sop_category": "price_quote",
                    "order": 140,
                    "payment_collection_gate": {"status": "not_required"},
                },
                {
                    "id": "event_s10_deposit_push_70min",
                    "sop_category": "deposit_push",
                    "order": 150,
                    "payment_collection_gate": {"status": "activity_intro_required"},
                },
            ],
        }
        _, blocked = normalize_event_decision(
            {
                "decision": "send",
                "selected_pack_ids": ["event_s10_deposit_push_70min"],
                "reason": "send deposit",
            },
            selector_input,
        )
        self.assertIn("selected_packs_must_start_with_earliest_candidate", blocked)
        self.assertIn("selected_payment_pack_not_currently_supported", blocked)

        _, allowed = normalize_event_decision(
            {
                "decision": "send",
                "selected_pack_ids": ["event_s10_deposit_push_70min"],
                "stage_skip_evidence": [
                    {
                        "stage_id": "activity_and_price",
                        "pack_id": "event_s10_price_quote_60min",
                        "evidence": "近期聊天已完整说明活动价、预约金抵扣和可退口径",
                    }
                ],
                "reason": "recent conversation already covered the quote",
            },
            selector_input,
        )
        self.assertNotIn("selected_packs_must_start_with_earliest_candidate", allowed)
        self.assertNotIn("selected_payment_pack_not_currently_supported", allowed)

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
            payload={
                "event_type": "sop_friend_added_schedule_batch",
                "created_at": "2026-07-20T04:00:00+00:00",
            },
            customer={},
            identity={"customer_id": "customer", "external_userid": "external"},
            event_type="sop_friend_added_schedule_batch",
            conversation_messages=[
                {
                    "from": "staff",
                    "source": "ai_reply",
                    "msgtype": "text",
                    "content": "前序破冰",
                    "msgtime": 1783728000000,
                }
            ],
            conversation_activity={"customer_replied": False, "last_message_direction": "staff"},
            customer_memory={
                "portrait": {"concern": "价格"},
                "basic_info": {"city": "深圳", "confirmed_store_id": "101"},
                "lifecycle_stage": "new_customer",
                "history_events": [{"event_id": "history_1", "event_type": "store_matched", "summary": "已匹配门店"}],
            },
            customer_context={
                "orders": [
                    {
                        "id": "order_101",
                        "store_id": "101",
                        "status": "pending",
                        "prepay_required": 10,
                        "prepay_paid": 0,
                        "is_current_order": True,
                    }
                ]
            },
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
        self.assertEqual(system_prompt, _sop_event_system_prompt(schema_only=False))
        self.assertIn(GLOBAL_STRUCTURED_NODE_CONTRACT, system_prompt)
        self.assertIn(GLOBAL_BUSINESS_RHYTHM_CONTRACT, system_prompt)
        self.assertIn("先做拒发审查", system_prompt)
        self.assertIn("客户当前立场与候选包的核心行动相反", system_prompt)
        self.assertIn("时间/预约金卡点", system_prompt)
        self.assertIn("不要直接帮客户约到店", system_prompt)
        self.assertIn("阶段目标 + 核心事实 + 行动目标", system_prompt)
        self.assertIn("企业微信一对一聊天", system_prompt)
        self.assertIn("尊敬的客户/尊敬的顾客", system_prompt)
        self.assertIn("您好，温馨提醒", system_prompt)
        self.assertIn("销冠正在连续承接", system_prompt)
        self.assertIn("冲突", system_prompt)
        self.assertIn("严重重合", system_prompt)
        self.assertIn("客户未回复、只有 staff 消息", system_prompt)

        self.assertIn("平台提醒现在应该主动触达客户", system_prompt)
        self.assertIn("让客户重新开口", system_prompt)
        self.assertIn("assistant_waiting_customer=true", system_prompt)
        self.assertIn("最近活跃保护窗口", system_prompt)
        self.assertIn("不是要求你机械按 `delay_minutes` 强制发送", system_prompt)
        self.assertIn("最近真实聊天状态 + 已触达步骤 + 未完成步骤 + 候选包阶段目标", system_prompt)
        self.assertIn("不要无限重复追问同一个问题", system_prompt)
        self.assertIn("客户沉默时，优先推进下一个合理 SOP 价值点", system_prompt)
        self.assertIn("发送效果铺垫包", system_prompt)
        self.assertIn("计时基准、阶段前置、付款状态和当天频率资格", system_prompt)
        self.assertIn("不能在活动报价前发送预约金卡或催付", system_prompt)
        self.assertIn("text_adjustments", system_prompt)
        self.assertIn("message_operations", system_prompt)
        self.assertIn("insert_text_after", system_prompt)
        self.assertIn("payment_collection_gate.status", system_prompt)
        self.assertIn("remove_message", system_prompt)
        self.assertIn("handoff_to_ai_reply_not_allowed_for_proactive_event", Path("docs/sop_proactive_wakeup_ab_design_20260720.md").read_text(encoding="utf-8"))
        self.assertIn("Plan A Decision Contract", system_prompt)
        self.assertIn("event_policy_evidence.ai_reply_policy.allowed=true", system_prompt)
        self.assertIn("adjacent_merge_options", system_prompt)
        self.assertIn("editable_text_messages", system_prompt)
        self.assertIn("readonly_messages", system_prompt)
        self.assertIn("最新聊天 > 当前事件事实 > 已实际发送的 SOP 与结构事实", system_prompt)
        self.assertIn("这是完整的效果案例铺垫原文，用于确认模型不会只根据截断摘要改写。", user_prompt)
        self.assertIn('"amount":10', user_prompt)
        self.assertIn('"direction":"staff"', user_prompt)
        self.assertIn('"source":"ai_reply"', user_prompt)
        self.assertIn('"message_type":"text"', user_prompt)
        self.assertIn('"message_time":1783728000000', user_prompt)
        self.assertNotIn('"customer_profile"', user_prompt)
        self.assertNotIn('"lifecycle_stage"', user_prompt)
        self.assertIn('"customer_fact_snapshot"', user_prompt)
        self.assertIn('"basic_facts":{"city":"深圳","confirmed_store_id":"101"}', user_prompt)
        self.assertNotIn('"event_id":"history_1"', user_prompt)
        self.assertIn('"message_ref":"conv_1"', user_prompt)
        self.assertIn('"contact_availability_evidence"', user_prompt)
        self.assertIn('"candidate_policy"', user_prompt)
        self.assertIn('"candidate_group":"due"', user_prompt)
        self.assertIn('"payment_collection_gate"', user_prompt)
        self.assertIn('"status":"supported"', user_prompt)
        self.assertIn('"event_policy_evidence":{}', user_prompt)
        self.assertIn('"adjacent_merge_options"', user_prompt)
        self.assertIn('"event_time"', user_prompt)
        self.assertIn('"local_hour":12', user_prompt)
        self.assertIn('"mainline_stage_status"', user_prompt)
        self.assertIn("stage_skip_evidence", system_prompt)

    async def test_event_selector_prioritizes_platform_message_content_and_paid_followup_boundary(self) -> None:
        model = _PromptCaptureModel({"send_sop": True, "reason": "arrival followup is compatible"})
        service = SopExecutionService(repository=_Repo(), sop_reply_pack_service=_PackService(), model_client=model)

        result = await service.evaluate_event_suggestion(
            payload={
                "event_type": "sop_platform_task",
                "sop": {
                    "platform_task": {
                        "message_content": [
                            {"type": "text", "content": "到店赠送护理一次，今天方便几点到店？"},
                            {"type": "image", "content": "https://example.com/arrival.jpg"},
                        ]
                    }
                },
            },
            customer={"conversation": {"ai_auto_reply": True}},
            identity={"customer_id": "customer", "external_userid": "external"},
            event_type="sop_platform_task",
            conversation_messages=[],
            customer_context={
                "orders": [{"id": "paid", "prepay_required": 10, "prepay_paid": 10, "is_current_order": True}]
            },
            actions_reply_messages=[{"type": "text", "order": 1, "content": {"text": "到店赠送护理一次，今天方便几点到店？"}}],
        )

        self.assertTrue(result["send_sop"])
        selector_input = result["selector_input"]
        self.assertEqual(selector_input["current_platform_task"]["priority"], "current_outreach_objective_after_hard_facts")
        self.assertIn("到店赠送护理一次", selector_input["current_platform_task"]["message_content"][0]["content"])
        self.assertEqual(selector_input["current_payment_state"]["deposit_state"], "paid_by_order")
        self.assertIn("必须优先分析它的阶段目的", str(model.messages[0]["content"]))
        self.assertIn("已支付预约金只禁止再次发送预约金卡或催付", str(model.messages[0]["content"]))

    async def test_event_model_timeout_retries_then_succeeds(self) -> None:
        model = _SequenceModel([TimeoutError("total timeout 45.0s"), {"send_sop": True, "reason": "retry ok"}])
        service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_PackService(),
            model_client=model,
            event_model_retry_attempts=3,
            event_model_retry_delay_seconds=0,
        )

        result = await service.evaluate_event_suggestion(
            payload={"event_type": "sop_platform_task"},
            customer={},
            identity={"customer_id": "customer", "external_userid": "external"},
            event_type="sop_platform_task",
            conversation_messages=[],
            actions_reply_messages=[{"type": "text", "order": 1, "content": {"text": "触达内容"}}],
        )

        self.assertTrue(result["send_sop"])
        self.assertEqual([item["status"] for item in result["model_attempts"]], ["failed", "succeeded"])
        self.assertEqual(len(model.calls), 2)
        self.assertTrue(all(call.get("deadline_monotonic") for call in model.calls))

    async def test_platform_task_model_exhaustion_does_not_blindly_forward_actions(self) -> None:
        model = _SequenceModel([TimeoutError("timeout 1"), TimeoutError("timeout 2"), TimeoutError("timeout 3")])
        service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_PackService(),
            model_client=model,
            event_model_retry_attempts=3,
            event_model_retry_delay_seconds=0,
        )

        result = await service.evaluate_event_suggestion(
            payload={"event_type": "sop_platform_task"},
            customer={},
            identity={"customer_id": "customer", "external_userid": "external"},
            event_type="sop_platform_task",
            conversation_messages=[],
            actions_reply_messages=[{"type": "text", "order": 1, "content": {"text": "触达内容"}}],
        )

        self.assertEqual(result["mode"], "event_model_error")
        self.assertFalse(result["send_sop"])
        self.assertEqual(result["reason"], "event_sop_model_retries_exhausted")
        self.assertEqual(len(result["model_attempts"]), 3)
        self.assertIn("TimeoutError", result["error"])

    async def test_first_add_model_exhaustion_falls_back_to_first_eligible_mainline_pack(self) -> None:
        model = _SequenceModel([TimeoutError("timeout 1"), TimeoutError("timeout 2")])
        service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_PackService(),
            model_client=model,
            event_model_retry_attempts=2,
            event_model_retry_delay_seconds=0,
        )

        result = await service.evaluate_event_suggestion(
            payload={"event_type": "sop_friend_added_schedule_batch"},
            customer={},
            identity={"customer_id": "customer", "external_userid": "external"},
            event_type="sop_friend_added_schedule_batch",
            conversation_messages=[],
            candidate_packs=[
                {"id": "store_prompt", "name": "门店轻触", "sop_category": "store_prompt", "order": 120},
                {"id": "activity", "name": "活动介绍", "sop_category": "activity_intro", "order": 140},
            ],
            event_policy_evidence={},
        )

        self.assertEqual(result["mode"], "event_selected")
        self.assertTrue(result["send_sop"])
        self.assertEqual(result["sop_pack_id"], "store_prompt")
        self.assertEqual(result["reason"], "event_model_error_candidate_fallback")
        self.assertIn("TimeoutError", result["error"])

    async def test_schema_only_platform_exhaustion_consumes_without_business_fallback(self) -> None:
        model = _SequenceModel([TimeoutError("timeout 1"), TimeoutError("timeout 2")])
        service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_PackService(),
            model_client=model,
            event_model_retry_attempts=2,
            event_model_retry_delay_seconds=0,
            event_schema_only_normalizer_enabled=True,
        )

        result = await service.evaluate_event_suggestion(
            payload={"event_type": "sop_platform_task"},
            customer={},
            identity={"customer_id": "customer", "external_userid": "external"},
            event_type="sop_platform_task",
            conversation_messages=[],
            actions_reply_messages=[{"type": "text", "order": 1, "content": {"text": "平台触达内容"}}],
        )

        self.assertEqual(result["mode"], "event_model_error")
        self.assertFalse(result["send_sop"])
        self.assertEqual(result["reason"], "model_decision_failed_no_send")
        self.assertEqual(len(result["model_attempts"]), 2)

    async def test_schema_only_first_add_exhaustion_does_not_choose_pack_in_python(self) -> None:
        model = _SequenceModel([TimeoutError("timeout 1"), TimeoutError("timeout 2")])
        service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_PackService(),
            model_client=model,
            event_model_retry_attempts=2,
            event_model_retry_delay_seconds=0,
            event_schema_only_normalizer_enabled=True,
        )

        result = await service.evaluate_event_suggestion(
            payload={"event_type": "sop_friend_added_schedule_batch"},
            customer={},
            identity={"customer_id": "customer", "external_userid": "external"},
            event_type="sop_friend_added_schedule_batch",
            conversation_messages=[],
            candidate_packs=[
                {"id": "effect", "name": "效果", "sop_category": "effect_case", "order": 120},
                {"id": "activity", "name": "活动", "sop_category": "activity_intro", "order": 140},
            ],
            event_policy_evidence={},
        )

        self.assertEqual(result["mode"], "event_model_error")
        self.assertFalse(result["send_sop"])
        self.assertEqual(result["reason"], "model_decision_failed_no_send")

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

        self.assertIn("平台提醒现在应该主动触达客户", system_prompt)
        self.assertIn("让客户重新开口", system_prompt)
        self.assertIn("assistant_waiting_customer=true", system_prompt)
        self.assertIn("最近活跃保护窗口", system_prompt)
        self.assertIn("不是要求你机械按 `delay_minutes` 强制发送", system_prompt)
        self.assertIn("最近真实聊天状态 + 已触达步骤 + 未完成步骤 + 候选包阶段目标", system_prompt)
        self.assertIn("不要无限重复追问同一个问题", system_prompt)
        self.assertIn("客户沉默时，优先推进下一个合理 SOP 价值点", system_prompt)
        self.assertIn("计时基准、阶段前置、付款状态和当天频率资格", system_prompt)
        self.assertIn("不能在活动报价前发送预约金卡或催付", system_prompt)
        self.assertIn("text_adjustments", system_prompt)
        self.assertIn("message_operations", system_prompt)
        self.assertIn("insert_text_after", system_prompt)
        self.assertIn("payment_collection_gate.status", system_prompt)
        self.assertIn("remove_message", system_prompt)
        self.assertIn("客户画像和旧事件不是当前对话事实", system_prompt)

    async def test_chat_gate_ignores_platform_auto_message(self) -> None:
        model = _PromptCaptureModel({"send_sop": True, "sop_pack_id": "chat_opening", "need_ai_reply": False})
        repository = _Repo()
        service = SopExecutionService(repository=repository, sop_reply_pack_service=_DualScopeOpeningPackService(), model_client=model)
        request = ChatRequest(
            content="我已经添加了你，现在我们可以开始聊天了。",
            customer_id="customer",
            corp_id="corp",
            wechat="CS001",
            external_userid="ext",
            conversation_history=["用户: 门店在哪？"],
        )

        result = await service.evaluate_chat_gate(request, request_id="req_auto_opening", request_context={})

        self.assertEqual(result["mode"], "ignored_platform_auto_message")
        self.assertFalse(result["send_sop"])
        self.assertFalse(result["need_ai_reply"])
        self.assertEqual(result["reason"], "platform_auto_opening_ignored")
        self.assertEqual(result["reply_messages"], [])
        self.assertEqual(repository.tasks, [])
        self.assertEqual(model.messages, [])

        duplicate = await service.evaluate_chat_gate(request, request_id="req_auto_opening_duplicate", request_context={})
        self.assertEqual(duplicate["mode"], "ignored_platform_auto_message")
        self.assertFalse(duplicate["send_sop"])
        self.assertEqual(repository.tasks, [])

    async def test_chat_gate_blocks_configured_opening_for_deleted_customer_relation(self) -> None:
        model = _PromptCaptureModel({"send_sop": True, "sop_pack_id": "chat_opening"})
        repository = _Repo()
        service = SopExecutionService(
            repository=repository,
            sop_reply_pack_service=_DualScopeOpeningPackService(),
            model_client=model,
        )
        request = ChatRequest(
            content="我已经添加了你，现在我们可以开始聊天了。",
            customer_id="customer",
            corp_id="corp",
            wechat="CS001",
            external_userid="ext",
        )

        result = await service.evaluate_chat_gate(
            request,
            request_id="req_auto_opening_deleted",
            request_context={
                "customer_relation": {
                    "available": True,
                    "status": "deleted",
                    "is_deleted": True,
                }
            },
        )

        self.assertEqual(result["mode"], "ignored_platform_auto_message")
        self.assertFalse(result["send_sop"])
        self.assertFalse(result["need_ai_reply"])
        self.assertEqual(result["reason"], "platform_auto_opening_ignored")
        self.assertEqual(model.messages, [])
        self.assertEqual(repository.tasks, [])

    async def test_chat_gate_hands_workflow_customer_message_to_ai(self) -> None:
        class _PackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "static_pack",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "effect_case",
                            "name": "static pack",
                            "purpose": "static SOP pack",
                            "order": 10,
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "static message"}}
                            ],
                        }
                    ]
                }

        model = _PromptCaptureModel(
            {
                "route": "sop_only",
                "coverage": "exact",
                "priority_question_id": "",
                "sop_pack_id": "static_pack",
                "resume_stage": "",
                "reason": "候选包完整覆盖当前问题",
                "text_adjustments": [],
                "message_operations": [],
            }
        )
        service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_PackService(),
            model_client=model,
        )
        request = ChatRequest(
            content="is this real",
            customer_id="customer",
            corp_id="corp",
            external_userid="external",
            wechat="CS001",
        )

        result = await service.evaluate_chat_gate(
            request,
            request_id="req_realtime_customer",
            request_context={"source_protocol": "workflow-compatible"},
        )

        self.assertEqual(result["mode"], "sop_only")
        self.assertTrue(result["send_sop"])
        self.assertFalse(result["need_ai_reply"])
        self.assertNotEqual(model.messages, [])
        self.assertEqual(result["sop_progress_evidence"]["unfinished_sops"][0]["id"], "static_pack")

    async def test_semantic_safety_gate_persists_stop_contact_and_blocks_later_turns(self) -> None:
        class _ChatPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "static_pack",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "effect_case",
                            "name": "效果参考",
                            "purpose": "发送效果参考",
                            "order": 10,
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "效果参考"}}
                            ],
                        }
                    ]
                }

        model = _SequenceModel(
            [
                {
                    "route": "ai_only",
                    "coverage": "none",
                    "selected_scene_id": "",
                    "sop_pack_id": "",
                    "resume_stage": "",
                    "reason": "客户明确要求停止联系",
                    "safety_decision": {
                        "status": "stop",
                        "reason_type": "stop_contact",
                        "evidence_refs": ["current_message"],
                        "reason": "客户明确要求停止联系",
                    },
                    "scene_decision": {
                        "current_question": "",
                        "blocker": "none",
                        "resume_stage": "",
                        "evidence_refs": ["current_message"],
                    },
                }
            ]
        )
        with TemporaryDirectory() as directory:
            memory_store = CustomerMemoryStore(SimpleNamespace(memory_dir=Path(directory)))
            service = SopExecutionService(
                repository=_Repo(),
                sop_reply_pack_service=_ChatPackService(),
                model_client=model,
                memory_store=memory_store,
                model_semantic_routing_enabled=True,
            )
            request = ChatRequest(
                content="请不要再联系我",
                customer_id="customer",
                corp_id="corp",
                external_userid="external",
                wechat="CS001",
            )

            first = await service.evaluate_chat_gate(request, request_id="req_stop", request_context={})
            calls_after_first = len(model.calls)
            second = await service.evaluate_chat_gate(
                request.model_copy(update={"content": "好的"}),
                request_id="req_after_stop",
                request_context={},
            )

        self.assertEqual(first["mode"], "safety_stop_no_reply")
        self.assertEqual(first["stop_contact_persistence"]["status"], "recorded")
        self.assertEqual(second["mode"], "safety_stop_contact")
        self.assertEqual(len(model.calls), calls_after_first)

    async def test_chat_gate_bypasses_non_text_messages_to_ai_tools(self) -> None:
        class _PackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "static_pack",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "effect_case",
                            "name": "static pack",
                            "purpose": "static SOP pack",
                            "order": 10,
                            "reply_messages": [{"type": "text", "order": 1, "content": {"text": "static message"}}],
                        }
                    ]
                }

        for msgtype in ("image", "location", "voice", "unknown"):
            model = _PromptCaptureModel({"route": "sop_only", "sop_pack_id": "static_pack"})
            service = SopExecutionService(
                repository=_Repo(),
                sop_reply_pack_service=_PackService(),
                model_client=model,
            )
            request = ChatRequest(
                content="https://example.test/payload",
                customer_id=f"customer_{msgtype}",
                corp_id="corp",
                external_userid=f"external_{msgtype}",
                wechat="CS001",
                file_image="https://example.test/a.jpg" if msgtype == "image" else None,
            )

            result = await service.evaluate_chat_gate(
                request,
                request_id=f"req_non_text_{msgtype}",
                request_context={"source_protocol": "workflow-compatible", "msgtype": msgtype},
            )

            self.assertEqual(result["mode"], "skipped")
            self.assertTrue(result["need_ai_reply"])
            self.assertFalse(result["send_sop"])
            self.assertEqual(result["reason"], f"non_text_message_to_ai_tools:{msgtype}")
            self.assertEqual(model.messages, [])

    async def test_chat_gate_selects_precision_ai_then_sop_and_defers_send_record(self) -> None:
        class _PrecisionPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "s10_need_and_case",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "s10_need_and_case",
                            "name": "需求与案例",
                            "purpose": "发送真实案例并推进需求阶段",
                            "order": 30,
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "我给您发一组同类案例参考。"}},
                                {"type": "image", "order": 2, "content": {"url": "https://example.com/case.png"}},
                            ],
                        }
                    ]
                }

        repository = _Repo()
        model = _PromptCaptureModel(
            {
                "route": "ai_then_sop",
                "coverage": "partial",
                "priority_question_id": "one_session_effect",
                "sop_pack_id": "s10_need_and_case",
                "resume_stage": "need_and_case",
                "reason": "先回答次数问题，再发送案例",
                "text_adjustments": [],
                "message_operations": [],
            }
        )
        service = SopExecutionService(
            repository=repository,
            sop_reply_pack_service=_PrecisionPackService(),
            model_client=model,
        )

        result = await service.evaluate_chat_gate(
            ChatRequest(
                content="是不是做一次就可以",
                customer_id="customer",
                corp_id="corp",
                external_userid="external",
                wechat="CS001",
            ),
            request_id="req_precision_then_sop",
            request_context={"source_protocol": "workflow-compatible"},
        )

        self.assertEqual(result["mode"], "ai_then_sop")
        self.assertEqual(result["priority_question_id"], "one_session_effect")
        self.assertTrue(result["send_sop"])
        self.assertTrue(result["need_ai_reply"])
        self.assertEqual(result["task"]["status"], "pending")
        selector_input = result["selector_input"]
        self.assertIn("mainline", selector_input)
        self.assertIn("precision_qa_index", selector_input)

    async def test_chat_gate_platform_auto_opening_does_not_query_order_or_send_sop(self) -> None:
        repository = _Repo()
        model = _PromptCaptureModel({"send_sop": True, "sop_pack_id": "chat_opening"})
        service = SopExecutionService(
            repository=repository,
            sop_reply_pack_service=_DualScopeOpeningPackService(),
            model_client=model,
            memory_store=_MemoryStore(),
            customer_context_service=_CustomerContextService(
                {
                    "source": "platform_agent",
                    "orders": [
                        {
                            "id": "order-paid",
                            "status": "waiting_schedule",
                            "store_id": "386",
                            "prepay_required": 10,
                            "prepay_paid": 10,
                            "is_current_order": True,
                        }
                    ],
                }
            ),
        )
        request = ChatRequest(
            content="我已经添加了你，现在我们可以开始聊天了。",
            customer_id="customer",
            corp_id="corp",
            external_userid="ext",
            wechat="CS001",
        )

        result = await service.evaluate_chat_gate(request, request_id="req_auto_paid", request_context={})

        self.assertEqual(result["mode"], "ignored_platform_auto_message")
        self.assertFalse(result["send_sop"])
        self.assertEqual(result["reason"], "platform_auto_opening_ignored")
        self.assertEqual(repository.tasks, [])
        self.assertEqual(model.messages, [])

    async def test_chat_gate_applies_contextual_text_adjustment_without_changing_pack_structure(self) -> None:
        class _ContextPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "context_pack",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "intro",
                            "name": "顾虑承接",
                            "purpose": "回应客户收费顾虑",
                            "order": 10,
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "我们是明码标价，没有强制消费。"}},
                                {"type": "image", "order": 2, "content": {"url": "https://example.com/fact.png"}},
                            ],
                        }
                    ]
                }

        model = _PromptCaptureModel(
            {
                "route": "sop_only",
                "coverage": "exact",
                "priority_question_id": "price_transparency",
                "sop_pack_id": "context_pack",
                "resume_stage": "",
                "reason": "话术覆盖收费顾虑",
                "text_adjustments": [
                    {"order": 1, "text": "您担心到店后乱收费，这个我直接说清楚：我们是明码标价，没有强制消费。"}
                ],
            }
        )
        service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_ContextPackService(),
            model_client=model,
        )
        request = ChatRequest(
            content="我怕到店以后乱收费",
            customer_id="customer",
            corp_id="corp",
            external_userid="ext",
            wechat="CS001",
        )

        result = await service.evaluate_chat_gate(request, request_id="req_context_adjust", request_context={})

        self.assertTrue(result["send_sop"])
        self.assertEqual([item["type"] for item in result["reply_messages"]], ["text", "image"])
        self.assertEqual(
            result["reply_messages"][0]["content"]["text"],
            "您担心到店后乱收费，这个我直接说清楚：我们是明码标价，没有强制消费。",
        )
        self.assertEqual(result["message_adjustment"]["applied_orders"], [1])
        self.assertIn("接在当前对话后自然", str(model.messages[0]["content"]))

    async def test_chat_gate_rejects_adjustment_that_changes_numeric_occurrences(self) -> None:
        class _NumericPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "numeric_pack",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "activity_intro",
                            "name": "活动说明",
                            "purpose": "说明活动价格",
                            "order": 10,
                            "reply_messages": [
                                {
                                    "type": "text",
                                    "order": 1,
                                    "content": {"text": "活动价268元，预约金10元，到店抵扣10元。"},
                                }
                            ],
                        }
                    ]
                }

        model = _PromptCaptureModel(
            {
                "send_sop": True,
                "sop_pack_id": "numeric_pack",
                "need_ai_reply": False,
                "reason": "说明价格",
                "text_adjustments": [
                    {"order": 1, "text": "活动价268元，先付10元，到店抵扣10元，这10元可以退。"}
                ],
            }
        )
        service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_NumericPackService(),
            model_client=model,
        )
        request = ChatRequest(
            content="怎么收费",
            customer_id="customer",
            corp_id="corp",
            external_userid="ext",
            wechat="CS001",
        )

        result = await service.evaluate_chat_gate(request, request_id="req_numeric_adjust", request_context={})

        self.assertTrue(result["send_sop"])
        self.assertEqual(result["reply_messages"][0]["content"]["text"], "活动价268元，预约金10元，到店抵扣10元。")
        self.assertEqual(result["message_adjustment"]["applied_orders"], [])
        self.assertEqual(result["message_adjustment"]["rejected"][0]["reason"], "numeric_facts_changed")

    async def test_chat_gate_blocks_before_model_when_order_fetch_fails_or_deposit_is_paid(self) -> None:
        class _PlainPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "plain_pack",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "intro",
                            "name": "普通承接",
                            "purpose": "普通承接",
                            "order": 10,
                            "reply_messages": [{"type": "text", "order": 1, "content": {"text": "普通话术"}}],
                        }
                    ]
                }

        request = ChatRequest(
            content="你好",
            customer_id="customer",
            corp_id="corp",
            external_userid="ext",
            user_id=7294,
            wechat="CS001",
        )
        failed_model = _PromptCaptureModel({"send_sop": True, "sop_pack_id": "plain_pack"})
        failed_context = _CustomerContextService(
            {"source": "local_memory_fallback", "orders": [], "error": "order index timeout"}
        )
        failed_service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_PlainPackService(),
            model_client=failed_model,
            memory_store=_MemoryStore(),
            customer_context_service=failed_context,
        )

        failed = await failed_service.evaluate_chat_gate(request, request_id="req_order_failed", request_context={})

        self.assertEqual(failed["mode"], "failed_order_fetch")
        self.assertFalse(failed["send_sop"])
        self.assertEqual(failed_model.messages, [])
        self.assertEqual(len(failed_context.load_calls), 1)

        paid_model = _PromptCaptureModel({"send_sop": True, "sop_pack_id": "plain_pack"})
        paid_service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_PlainPackService(),
            model_client=paid_model,
            memory_store=_MemoryStore(),
            customer_context_service=_CustomerContextService(
                {
                    "source": "platform_agent",
                    "orders": [
                        {
                            "id": "order-paid",
                            "status": "waiting_schedule",
                            "store_id": "386",
                            "prepay_required": 10,
                            "prepay_paid": 10,
                            "is_current_order": True,
                        }
                    ],
                }
            ),
        )

        paid = await paid_service.evaluate_chat_gate(request, request_id="req_order_paid", request_context={})

        self.assertEqual(paid["mode"], "skipped_deposit_paid")
        self.assertFalse(paid["send_sop"])
        self.assertEqual(paid_model.messages, [])

    async def test_chat_gate_payment_card_does_not_require_matching_current_order(self) -> None:
        class _PaymentPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "payment_pack",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "deposit_push",
                            "name": "预约金推进",
                            "purpose": "发送预约金入口",
                            "order": 10,
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "10元预约金到店抵扣。"}},
                                {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
                            ],
                        }
                    ]
                }

        request = ChatRequest(
            content="付款入口发我",
            customer_id="customer",
            corp_id="corp",
            external_userid="ext",
            wechat="CS001",
            confirmed_store_id="386",
        )
        output = {
            "send_sop": True,
            "sop_pack_id": "payment_pack",
            "need_ai_reply": False,
            "reason": "客户索要入口",
            "text_adjustments": [],
        }
        missing_service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_PaymentPackService(),
            model_client=_PromptCaptureModel(output),
            memory_store=_MemoryStore({"basic_info": {"confirmed_store_id": "386"}}),
            customer_context_service=_CustomerContextService({"source": "platform_agent", "orders": []}),
        )

        missing = await missing_service.evaluate_chat_gate(request, request_id="req_payment_missing", request_context={})

        self.assertFalse(missing["send_sop"])
        self.assertEqual(missing["mode"], "ai_only")

        valid_service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_PaymentPackService(),
            model_client=_PromptCaptureModel(output),
            memory_store=_MemoryStore({"basic_info": {"confirmed_store_id": "386"}}),
            customer_context_service=_CustomerContextService(
                {
                    "source": "platform_agent",
                    "orders": [
                        {
                            "id": "order-unpaid",
                            "status": "pending",
                            "store_id": "386",
                            "prepay_required": 10,
                            "prepay_paid": 0,
                            "is_current_order": True,
                        }
                    ],
                }
            ),
        )

        valid = await valid_service.evaluate_chat_gate(request, request_id="req_payment_valid", request_context={})

        self.assertFalse(valid["send_sop"])
        self.assertEqual(valid["mode"], "ai_only")

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
            wechat="CS001",
        )

        result = await service.evaluate_chat_gate(request, request_id="req_progress", request_context={})

        evidence = result["sop_progress_evidence"]
        self.assertEqual(evidence["completed_pack_ids"], ["s10_new_customer_opening"])
        self.assertEqual(evidence["completed_categories"], ["opening"])
        self.assertEqual(evidence["unfinished_sops"][0]["id"], "s10_need_and_case")
        self.assertEqual(evidence["unfinished_sops"][0]["purpose"], "承接客户斑点情况和效果信心")
        self.assertNotIn("reply_messages", evidence["unfinished_sops"][0])
        self.assertNotIn("未发送的静态话术原文", str(evidence))

    async def test_chat_gate_exposes_recent_structured_case_image_delivery(self) -> None:
        class _CasePackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "s10_need_and_case",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "s10_need_and_case",
                            "name": "需求与案例",
                            "purpose": "发送真实案例并推进需求阶段",
                            "order": 20,
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "我给您发案例。"}},
                                {"type": "image", "order": 2, "content": {"url": "https://example.test/case.jpg"}},
                            ],
                        }
                    ]
                }

        repository = _Repo()
        repository.tasks.append(
            {
                "id": "task_recent_case",
                "status": "sent",
                "sop_pack_id": "event_s10_effect_warmup_30min",
                "sop_category": "effect_case",
                "sent_at": "2026-07-25T10:00:00+00:00",
                "reply_messages": [
                    {"type": "text", "order": 1, "content": {"text": "先给您看效果参考。"}},
                    {"type": "image", "order": 2, "content": {"url": "https://example.test/recent-case.jpg"}},
                ],
            }
        )
        model = _PromptCaptureModel(
            {
                "route": "ai_only",
                "coverage": "none",
                "priority_question_id": "",
                "sop_pack_id": "",
                "resume_stage": "activity_and_price",
                "reason": "刚发过真实案例图，先解释效果再推进活动主线。",
                "text_adjustments": [],
                "message_operations": [],
            }
        )
        service = SopExecutionService(
            repository=repository,
            sop_reply_pack_service=_CasePackService(),
            model_client=model,
        )
        request = ChatRequest(
            content="效果怎么样",
            customer_id="customer",
            corp_id="corp",
            external_userid="ext",
            wechat="CS001",
            conversation_history=[
                "小贝: 先给您看效果参考。",
                "小贝: [image]https://example.test/recent-case.jpg",
            ],
        )

        result = await service.evaluate_chat_gate(request, request_id="req_recent_case", request_context={})

        self.assertEqual(result["mode"], "ai_only")
        user_prompt = model.messages[1]["content"]
        self.assertIn("recent_sop_delivery_evidence", user_prompt)
        self.assertIn("event_s10_effect_warmup_30min", user_prompt)
        self.assertIn('"image_count":1', user_prompt)

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
            {
                "route": "ai_only",
                "coverage": "none",
                "priority_question_id": "effect_authenticity",
                "sop_pack_id": "",
                "resume_stage": "need_and_case",
                "reason": "收费包不覆盖效果真实性",
                "text_adjustments": [],
                "message_operations": [],
            }
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
            wechat="CS001",
            conversation_history=["小贝: 已经发过同类效果图给您参考"],
        )

        result = await service.evaluate_chat_gate(request, request_id="req_effect_objection", request_context={})

        self.assertEqual(result["mode"], "ai_only")
        self.assertTrue(result["need_ai_reply"])
        system_prompt = model.messages[0]["content"]
        user_prompt = model.messages[1]["content"]
        self.assertIn("候选 SOP 的实际消息", system_prompt)
        self.assertIn("精准问题", system_prompt)
        self.assertIn("门店、定位、图片、订单", system_prompt)
        self.assertIn("支付异常", system_prompt)
        self.assertIn("收费与预约金顾虑处理", user_prompt)
        self.assertIn("payment_collection", user_prompt)

    async def test_chat_gate_hands_project_content_doubt_to_ai_instead_of_case_pack(self) -> None:
        class _CasePackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "s10_need_and_case",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "s10_need_and_case",
                            "name": "需求与效果承接",
                            "purpose": (
                                "客户第一次问斑点、效果或是否能做时，承接需求并准备案例参考。"
                                "客户追问活动价是否只包含检测/清洁/洗脸、是否真正包含斑点改善时不适用，"
                                "应交给普通 AI 解释项目内容与费用包含后继续推进。"
                            ),
                            "order": 20,
                            "triggers": ["first_need", "first_effect_question"],
                            "reply_messages": [
                                {
                                    "type": "text",
                                    "order": 1,
                                    "content": {"text": "斑点、色沉都可以操作，效果很好的。"},
                                },
                                {
                                    "type": "image",
                                    "order": 2,
                                    "content": {"url": "https://test.by4dev.4ba.cn/example-case.png"},
                                },
                            ],
                        }
                    ]
                }

        repository = _Repo()
        model = _PromptCaptureModel(
            {
                "route": "ai_only",
                "coverage": "none",
                "priority_question_id": "project_scope",
                "sop_pack_id": "",
                "resume_stage": "need_and_case",
                "reason": "案例包不解释项目内容",
                "text_adjustments": [],
                "message_operations": [],
            }
        )
        service = SopExecutionService(
            repository=repository,
            sop_reply_pack_service=_CasePackService(),
            model_client=model,
        )
        request = ChatRequest(
            content="应该只是检测和洗脸，没有去斑的吧",
            customer_id="customer",
            corp_id="corp",
            external_userid="ext",
            wechat="CS001",
            conversation_history=["小贝: 线上预付10到店再付款258共268，明码标价哈亲放心"],
        )

        result = await service.evaluate_chat_gate(request, request_id="req_project_content_doubt", request_context={})

        self.assertEqual(result["mode"], "ai_only")
        self.assertTrue(result["need_ai_reply"])
        system_prompt = model.messages[0]["content"]
        user_prompt = model.messages[1]["content"]
        self.assertIn("项目是否真正包含斑点改善", system_prompt)
        self.assertIn("不能用宽泛项目介绍", system_prompt)
        self.assertIn("s10_need_and_case", user_prompt)
        self.assertIn("不适用", user_prompt)

    async def test_chat_gate_activity_question_selects_activity_intro_pack(self) -> None:
        class _ActivityPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "s10_activity_intro",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "s10_activity_intro",
                            "name": "活动介绍",
                            "purpose": "客户第一次了解活动、价格或预约金时，完整说明活动、价格和预约金规则。",
                            "order": 30,
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "活动价268，10元预约金到店抵扣。"}},
                                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
                            ],
                        }
                    ]
                }

        model = _PromptCaptureModel(
            {
                "route": "sop_only",
                "coverage": "exact",
                "priority_question_id": "",
                "sop_pack_id": "s10_activity_intro",
                "resume_stage": "activity_and_price",
                "reason": "活动包完整覆盖首次活动价格问题",
                "text_adjustments": [],
                "message_operations": [],
            }
        )
        service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_ActivityPackService(),
            model_client=model,
        )

        result = await service.evaluate_chat_gate(
            ChatRequest(
                content="活动怎么参加，怎么付费",
                customer_id="customer",
                corp_id="corp",
                external_userid="ext",
                wechat="CS001",
            ),
            request_id="req_activity_intro",
            request_context={"source_protocol": "workflow-compatible"},
        )

        self.assertEqual(result["mode"], "ai_only")
        self.assertFalse(result.get("send_sop"))
        user_prompt = "\n".join(str(message.get("content") or "") for message in model.messages)
        self.assertIn("s10_activity_intro", user_prompt)
        self.assertIn("活动怎么参加", user_prompt)

    async def test_chat_gate_repeated_soft_refusal_falls_back_to_ai_only_after_repair(self) -> None:
        class _ActivityPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "s10_activity_intro",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "activity_intro",
                            "name": "活动介绍",
                            "purpose": "说明活动价格",
                            "order": 10,
                            "mainline_stage": "activity_and_price",
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "活动价268，预约金10元。"}}
                            ],
                        }
                    ]
                }

        bad_output = {
            "route": "sop_only",
            "coverage": "exact",
            "priority_question_id": "",
            "sop_pack_id": "s10_activity_intro",
            "resume_stage": "activity_and_price",
            "reason": "错误忽略多次婉拒仍发活动包",
            "text_adjustments": [],
            "message_operations": [],
        }
        service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_ActivityPackService(),
            model_client=_SequenceModel([bad_output, bad_output]),
        )

        result = await service.evaluate_chat_gate(
            ChatRequest(
                content="不急，我想多看看，再做决定",
                customer_id="customer",
                corp_id="corp",
                external_userid="ext",
                wechat="CS001",
                conversation_history=[
                    "用户: 我考虑一下",
                    "小贝: 现在活动还可以参加，先交10元锁名额。",
                    "用户: 先不定了",
                ],
            ),
            request_id="req_repeated_soft_refusal",
            request_context={"source_protocol": "workflow-compatible"},
        )

        self.assertEqual(result["mode"], "ai_only")
        self.assertFalse(result["send_sop"])
        self.assertTrue(result["need_ai_reply"])
        self.assertIn("chat_gate_invalid_after_repair_continue_ai", result["reason"])
        self.assertIn("gate_pressure_conflict", str(result["selector_output"].get("initial_violations")))
        self.assertIn("gate_pressure_conflict", str(result["selector_output"].get("repair_violations")))

    async def test_chat_gate_precision_question_can_resume_activity_intro(self) -> None:
        class _ActivityPackService:
            def load(self) -> dict[str, Any]:
                return {
                    "packs": [
                        {
                            "id": "s10_activity_intro",
                            "enabled": True,
                            "scope": "chat_gate",
                            "sop_category": "s10_activity_intro",
                            "name": "活动介绍",
                            "purpose": "客户第一次了解活动、价格或预约金时，完整说明活动、价格和预约金规则。",
                            "order": 30,
                            "reply_messages": [
                                {"type": "text", "order": 1, "content": {"text": "活动价268，10元预约金到店抵扣。"}},
                            ],
                        }
                    ]
                }

        model = _PromptCaptureModel(
            {
                "route": "ai_then_sop",
                "coverage": "partial",
                "priority_question_id": "price_transparency",
                "sop_pack_id": "s10_activity_intro",
                "resume_stage": "activity_and_price",
                "reason": "先回答隐形消费，再接活动价格主线",
                "text_adjustments": [{"order": 1, "text": "我把这次活动价和预约金规则给您说清楚。"}],
                "message_operations": [],
            }
        )
        service = SopExecutionService(
            repository=_Repo(),
            sop_reply_pack_service=_ActivityPackService(),
            model_client=model,
        )

        result = await service.evaluate_chat_gate(
            ChatRequest(
                content="不会到店又加价吧",
                customer_id="customer",
                corp_id="corp",
                external_userid="ext",
                wechat="CS001",
            ),
            request_id="req_precision_activity",
            request_context={"source_protocol": "workflow-compatible"},
        )

        self.assertEqual(result["mode"], "ai_then_sop")
        self.assertTrue(result["need_ai_reply"])
        self.assertEqual(result["priority_question_id"], "price_transparency")
        self.assertEqual(result["sop_pack_id"], "s10_activity_intro")
        self.assertEqual(result["task"]["status"], "pending")

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
    memory_store: Any | None = None,
    customer_context_service: Any | None = None,
    personalized_outreach_service: Any | None = None,
    daily_touch_soft_limit: int = 2,
    quiet_backlog_fusion_time: str = "08:30",
) -> SopEventService:
    return SopEventService(
        repository=repo,
        sop_reply_pack_service=pack_service or _PackService(),
        outreach_send_client=client,
        sop_execution_service=selector or _Selector({"send_sop": False, "reason": "default reject"}),
        memory_store=memory_store,
        customer_context_service=customer_context_service or _CustomerContextService(),
        personalized_outreach_service=personalized_outreach_service,
        daily_touch_soft_limit=daily_touch_soft_limit,
        default_identity=default_identity,
        quiet_backlog_fusion_time=quiet_backlog_fusion_time,
    )


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _base_payload(
    *,
    event_id: str,
    event_type: str,
    sop: dict[str, Any],
    customers: list[dict[str, Any]],
    created_at: str = "2026-07-20T02:00:00+00:00",
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


class _BacklogPackService:
    def load(self) -> dict[str, Any]:
        return {
            "packs": [
                {
                    "id": "effect",
                    "enabled": True,
                    "scope": "event_first_add",
                    "sop_category": "effect_case",
                    "name": "效果展示",
                    "purpose": "发送淡斑效果图",
                    "order": 10,
                    "event_type": "sop_friend_added_schedule_batch",
                    "delay_minutes": 30,
                    "reply_messages": [
                        {"type": "text", "order": 1, "content": {"text": "亲，先给您看下做完后的效果参考。"}},
                        {"type": "image", "order": 2, "content": {"url": "https://example.com/case.png"}},
                    ],
                },
                {
                    "id": "activity",
                    "enabled": True,
                    "scope": "event_first_add",
                    "sop_category": "activity_intro",
                    "name": "活动介绍",
                    "purpose": "介绍活动价和预约金规则",
                    "order": 20,
                    "event_type": "sop_friend_added_schedule_batch",
                    "delay_minutes": 60,
                    "reply_messages": [
                        {"type": "text", "order": 1, "content": {"text": "现在活动价268元，线上10元先锁名额。"}}
                    ],
                },
            ]
        }


class _Selector:
    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    async def evaluate_event_suggestion(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self.output)


class _FusionSelector(_Selector):
    async def evaluate_event_suggestion(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("quiet backlog fusion must not use event suggestion selector")

    async def evaluate_quiet_backlog_fusion(self, selector_input: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(selector_input)
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


class _SequenceModel:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.last_usage: dict[str, Any] = {}

    async def chat_json(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        outcome = self.outcomes.pop(0)
        self.last_usage = {"attempt_index": len(self.calls), "candidate_models": ["test-model"]}
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)


class _OutreachClient:
    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        fetch_result: dict[str, Any] | None = None,
        send_result: dict[str, Any] | None = None,
    ) -> None:
        self.messages = messages or []
        self.fetch_result = fetch_result
        self.send_result = send_result
        self.fetch_calls: list[dict[str, Any]] = []
        self.send_calls: list[dict[str, Any]] = []

    async def fetch_conversation(self, **kwargs: Any) -> dict[str, Any]:
        self.fetch_calls.append(kwargs)
        if self.fetch_result is not None:
            result = dict(self.fetch_result)
            result.setdefault("request", kwargs)
            return result
        return {
            "status": "ok",
            "request": kwargs,
            "message_count": len(self.messages),
            "messages": self.messages,
            "customer_relation": {
                "available": True,
                "status": "active",
                "is_deleted": False,
                "deleted_at": "",
                "updated_at": "2026-07-29T10:00:00+08:00",
            },
        }

    async def send_reply_messages(self, **kwargs: Any) -> dict[str, Any]:
        self.send_calls.append(kwargs)
        if self.send_result is not None:
            return dict(self.send_result)
        return {
            "status": "sent",
            "send_payload": {"reply_messages": kwargs.get("reply_messages", [])},
            "response": {"code": 0, "msg": "ok"},
        }


class _MemoryStore:
    def __init__(self, memory: dict[str, Any] | None = None) -> None:
        self.memory = memory or {}
        self.load_calls: list[str] = []
        self.record_calls: list[dict[str, Any]] = []

    def load(self, customer_id: str) -> dict[str, Any]:
        self.load_calls.append(customer_id)
        return dict(self.memory)

    def record_sop_pack_sent(self, customer_id: str, **kwargs: Any) -> dict[str, Any]:
        self.record_calls.append({"customer_id": customer_id, **kwargs})
        return {"status": "recorded"}


class _CustomerContextService:
    def __init__(self, context: dict[str, Any] | None = None) -> None:
        self.context = context or {"source": "platform_agent", "orders": []}
        self.load_calls: list[dict[str, Any]] = []

    def load(self, **kwargs: Any) -> dict[str, Any]:
        self.load_calls.append(kwargs)
        return dict(self.context)


class _PersonalizedOutreachService:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or {"created": True, "plan": {"id": "plan-personalized"}}
        self.calls: list[dict[str, Any]] = []

    async def ensure_platform_task_plan(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self.result)


class _FlakyPersonalizedOutreachService(_PersonalizedOutreachService):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    async def ensure_platform_task_plan(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail:
            raise TimeoutError("personalized plan timed out")
        return dict(self.result)


class _Repo:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.tasks: list[dict[str, Any]] = []
        self.sent_ids: set[str] = set()
        self.sent_categories: set[str] = set()
        self.identity_lookup: dict[str, str] = {}
        self.identity_lookup_error: Exception | None = None
        self.message_times: list[dict[str, str]] = []
        self.retry_queue: list[str] = []

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

    def schedule_sop_event_model_retry(self, event_id: str, **kwargs: Any) -> dict[str, Any]:
        event = self.events[event_id]
        event["retry_count"] = int(event.get("retry_count") or 0) + 1
        max_attempts = int(kwargs.get("max_attempts") or 0)
        event["status"] = "retry_pending_model" if event["retry_count"] <= max_attempts else "retry_exhausted_model"
        event["last_retry_error"] = str(kwargs.get("error") or "")
        if event["status"] == "retry_pending_model" and event_id not in self.retry_queue:
            self.retry_queue.append(event_id)
        return dict(event)

    def claim_due_sop_event_model_retries(self, *, limit: int = 5) -> list[dict[str, str]]:
        claimed = self.retry_queue[:limit]
        self.retry_queue = self.retry_queue[limit:]
        for event_id in claimed:
            self.events[event_id]["status"] = "retry_processing_model"
        return [{"event_id": event_id} for event_id in claimed]

    def resolve_sop_event_model_retry_tasks(self, event_id: str) -> int:
        resolved = 0
        for task in self.tasks:
            if task.get("event_id") == event_id and task.get("status") == "failed_model_error":
                task["status"] = "model_retry_resolved"
                task["error"] = ""
                resolved += 1
        return resolved

    def list_sent_sop_pack_ids_for_customer(
        self,
        *,
        customer_id: str,
        external_userid: str,
        corp_id: str = "",
        wechat: str = "",
        sent_before: str = "",
    ) -> list[str]:
        return sorted(self.sent_ids)

    def list_sent_sop_categories_for_customer(
        self,
        *,
        customer_id: str,
        external_userid: str,
        corp_id: str = "",
        wechat: str = "",
        sent_before: str = "",
    ) -> list[str]:
        return sorted(self.sent_categories)

    def list_recent_sop_send_tasks_for_customer(
        self,
        *,
        customer_id: str,
        external_userid: str,
        corp_id: str = "",
        wechat: str = "",
        before: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return [dict(task) for task in reversed(self.tasks[-limit:])]

    def list_quiet_hour_backlog_tasks(self, *, start_at: str, end_at: str, limit: int = 500) -> list[dict[str, Any]]:
        start = datetime.fromisoformat(start_at)
        end = datetime.fromisoformat(end_at)
        output: list[dict[str, Any]] = []
        for task in self.tasks:
            created = datetime.fromisoformat(str(task.get("created_at") or "1970-01-01T00:00:00+00:00"))
            event_payload = self.events.get(str(task.get("event_id") or ""), {}).get("raw_payload", {})
            event_type = str(event_payload.get("event_type") or "")
            if event_type == "platform_sop_task":
                platform_task = event_payload.get("platform_task") if isinstance(event_payload.get("platform_task"), dict) else {}
                send_payload = task.get("send_payload") if isinstance(task.get("send_payload"), dict) else {}
                decision = send_payload.get("decision") if isinstance(send_payload.get("decision"), dict) else {}
                if task.get("status") != "completed_without_send":
                    continue
                if str(platform_task.get("triggerEvent") or "").lower() != "add_wecom":
                    continue
                if decision.get("reason") not in {
                    "quiet_hours_unknown_activity",
                    "quiet_hours_customer_pending_reply",
                    "quiet_hours_first_add_inactive",
                }:
                    continue
            elif task.get("status") != "skipped_quiet_hours_inactive":
                continue
            if not (start <= created < end):
                continue
            output.append({**dict(task), "raw_event_payload": event_payload, "event_type": event_payload.get("event_type")})
        return output[:limit]

    def list_quiet_backlog_fusion_tasks(self, *, start_at: str, end_at: str, limit: int = 500) -> list[dict[str, Any]]:
        start = datetime.fromisoformat(start_at)
        end = datetime.fromisoformat(end_at)
        output: list[dict[str, Any]] = []
        for task in self.tasks:
            created = datetime.fromisoformat(str(task.get("created_at") or "1970-01-01T00:00:00+00:00"))
            event_payload = self.events.get(str(task.get("event_id") or ""), {}).get("raw_payload", {})
            if event_payload.get("event_type") != "sop_quiet_backlog_fusion" or not (start <= created < end):
                continue
            output.append(dict(task))
        return list(reversed(output[-limit:]))

    def list_sop_send_tasks_by_ids(self, task_ids: list[str]) -> list[dict[str, Any]]:
        wanted = set(task_ids)
        return [dict(task) for task in self.tasks if str(task.get("id") or "") in wanted]

    def get_sop_event_detail(self, event_id: str) -> dict[str, Any]:
        event = self.events.get(event_id)
        if not event:
            return {}
        return {
            "event": {
                **dict(event),
                "event_type": str(event.get("raw_payload", {}).get("event_type") or ""),
                "received_at": str(event.get("raw_payload", {}).get("created_at") or ""),
            },
            "tasks": [dict(task) for task in self.tasks if task.get("event_id") == event_id],
        }

    def get_sop_send_task_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any]:
        for task in self.tasks:
            if task.get("idempotency_key") == idempotency_key:
                return dict(task)
        return {}

    def find_sop_event_identity(self, *, customer_id: str = "", external_userid: str = "", wechat: str = "") -> dict[str, str]:
        if self.identity_lookup_error:
            raise self.identity_lookup_error
        return dict(self.identity_lookup)

    def touch_customer_message_time(self, customer_id: str, *, field: str, value: str | None = None) -> None:
        self.message_times.append({"customer_id": customer_id, "field": field, "value": str(value or "")})

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
        event_payload = self.events.get(str(kwargs.get("event_id") or ""), {}).get("raw_payload", {})
        created_at = str(event_payload.get("created_at") or "2026-07-01T00:00:00+00:00")
        task = {
            "id": f"task_{len(self.tasks) + 1}",
            **kwargs,
            "created_at": created_at,
            "updated_at": created_at,
            "sent_at": "",
            "created": True,
        }
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
                task["updated_at"] = sent_at or task.get("updated_at", "")
                if status == "sent" and task.get("sop_pack_id"):
                    self.sent_ids.add(str(task["sop_pack_id"]))
                if status == "sent" and task.get("sop_category"):
                    self.sent_categories.add(str(task["sop_category"]))
                return dict(task)
        return {}


if __name__ == "__main__":
    unittest.main()
