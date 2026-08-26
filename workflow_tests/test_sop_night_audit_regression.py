from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.services.storage import AppRepository, SQLiteStore
from app.services.sop_platform_task_service import _quiet_hours_base_summary
from app.services.sop_event_service import _normalize_quiet_backlog_fusion_messages
from app.services.sop_execution_service import SOP_QUIET_BACKLOG_FUSION_SYSTEM_PROMPT
from scripts.consume_quiet_sop_backlog import consume_backlog
from test_sop_platform_task_flow import _Model, _service, _settings, _task, _beijing_epoch
from test_sop_event_flow import _service as event_service, _Repo, _OutreachClient


class SopNightAuditRegression(unittest.IsolatedAsyncioTestCase):
    def test_natural_text_and_source_only_media(self):
        source = {"case:1": {"type": "image", "content": {"url": "https://example.test/case.jpg"}}}
        messages = _normalize_quiet_backlog_fusion_messages([
            {"type": "text", "content": "我先发个效果参考给您看看。"},
            {"type": "image", "content": {"url": "https://untrusted.test/new.jpg"}},
            {"type": "source", "source_id": "missing"},
            {"type": "source", "source_id": "case:1"},
        ], source)
        self.assertEqual([m["type"] for m in messages], ["text", "image"])
        self.assertEqual(messages[1]["content"], source["case:1"]["content"])
        self.assertEqual([m["order"] for m in messages], [1, 2])
        self.assertIn("authoritative_business_facts", SOP_QUIET_BACKLOG_FUSION_SYSTEM_PROMPT)
        self.assertNotIn("严禁生成、改写", SOP_QUIET_BACKLOG_FUSION_SYSTEM_PROMPT)
        self.assertNotIn("不输出 markdown、客户可见话术", SOP_QUIET_BACKLOG_FUSION_SYSTEM_PROMPT)

    def test_source_messages_also_obey_five_message_limit(self):
        source = {"case:1": {"type": "image", "content": {"url": "https://example.test/case.jpg"}}}
        result = _normalize_quiet_backlog_fusion_messages([{"type": "source", "source_id": "case:1"}] * 8, source)
        self.assertEqual(len(result), 5)

    async def test_backlog_does_not_interrupt_replied_customer_or_call_model(self):
        for messages in [
            [{"direction": "customer", "content": "地址发我", "created_at": "2026-08-26T08:29:00+08:00"}],
            [{"direction": "customer", "content": "你好"}, {"direction": "assistant", "content": "您好"}],
        ]:
            repo = _Repo()
            client = _OutreachClient(messages=messages)
            service = event_service(repo=repo, client=client)
            service.sop_execution_service = SimpleNamespace(evaluate_quiet_backlog_fusion=AsyncMock())
            result = await service._process_quiet_backlog_group({
                "identity": {"corp_id": "sim_corp", "customer_id": "sim_customer", "external_userid": "sim_external",
                             "user_id": "sim_staff", "wechat": "sim_wechat"}, "tasks": [],
            }, local_date="2026-08-26")
            self.assertEqual(result["status"], "skipped_customer_replied")
            self.assertEqual(client.send_calls, [])
            service.sop_execution_service.evaluate_quiet_backlog_fusion.assert_not_awaited()

    def test_actual_time_blocks_daytime_task_and_midnight_boundaries(self):
        settings = _settings(quiet_hours_enabled=True)
        task = {"scheduledAt": _beijing_epoch("2026-08-25 12:00:00")}
        for stamp, blocked in [
            ("2026-08-25 23:59:59", False),
            ("2026-08-26 00:00:00", True),
            ("2026-08-26 07:59:59", True),
            ("2026-08-26 08:00:00", False),
        ]:
            with self.subTest(stamp=stamp), patch(
                "app.services.sop_platform_task_service.time.time", return_value=_beijing_epoch(stamp)
            ):
                self.assertEqual(_quiet_hours_base_summary(task, settings=settings)["in_quiet_hours"], blocked)

    async def test_crossing_midnight_during_processing_does_not_send(self):
        service, repo, _, system = _service(model=_Model([]), settings=_settings(quiet_hours_enabled=True))
        system.conversation_payload["data"]["messages"] = []
        summaries = [
            {"in_quiet_hours": False},
            {"in_quiet_hours": True, "blocked": True},
        ]
        with patch("app.services.sop_platform_task_service._quiet_hours_base_summary", side_effect=summaries):
            result = await service.process_task(_task(use_ai_copy=False))
        self.assertEqual(result["status"], "completed_without_send")
        self.assertEqual(system.send_calls, [])
        self.assertEqual(repo.tasks["platform-sop:101"]["send_payload"]["decision"]["reason"], "quiet_hours_all_sop_blocked")

    async def test_allowed_takeover_and_quiet_checks_are_persisted(self):
        service, repo, _, system = _service(model=_Model([]))
        system.conversation_payload["data"]["messages"] = []
        await service.process_task(_task(use_ai_copy=False))
        context = repo.tasks["platform-sop:101"]["send_payload"]["context"]
        self.assertEqual(context["takeover_status"]["mode"], "ai")
        self.assertTrue(context["takeover_status"]["send_allowed"])
        self.assertIn("processing_at_beijing", context["quiet_hours"])

    async def test_error_survives_terminal_update_with_real_repository(self):
        with TemporaryDirectory() as tmp:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmp) / "test.db"))
            store.initialize()
            repo = AppRepository(store)
            service, _, platform, system = _service(model=_Model([]))
            service.repository = repo
            system.conversation_payload["data"]["messages"] = []
            system.send_error = RuntimeError("upstream_test_failure")
            result = await service.process_task(_task(use_ai_copy=False))
            task = repo.get_sop_send_task_by_idempotency_key("platform-sop:101")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(task["error"], "RuntimeError: upstream_test_failure")
            self.assertEqual(task["send_payload"]["execution_failure"]["phase"], "sending")
            self.assertEqual(task["send_payload"]["platform_terminal_remark"], task["error"])
            self.assertEqual(platform.consume_remarks[-1], task["error"])

    async def test_event_final_send_is_blocked_at_night_even_for_platform_passthrough(self):
        repo = _Repo()
        client = _OutreachClient(messages=[])
        service = event_service(repo=repo, client=client)
        repo.tasks.append({"id": "test-task", "status": "pending", "send_payload": {}, "reply_messages": []})
        with patch("app.services.sop_event_service.utc_now_iso", return_value="2026-08-25T16:00:00+00:00"):
            result = await service._send_task(repo.tasks[0])
        self.assertEqual(result["error"], "quiet_hours_all_sop_blocked")
        self.assertEqual(client.send_calls, [])

    async def test_backlog_contact_is_not_split_at_500_row_boundary(self):
        with TemporaryDirectory() as tmp:
            store = SQLiteStore(SimpleNamespace(db_path=Path(tmp) / "test.db"))
            store.initialize()
            repo = AppRepository(store)
            repo.create_sop_event({"event_id": "test-night", "event_type": "platform_sop_task", "platform_task": {"triggerEvent": "add_wecom"}})
            start = datetime(2026, 8, 25, 16, tzinfo=timezone.utc)
            with store.connect() as conn:
                for i in range(566):
                    stamp = (start + timedelta(seconds=i)).isoformat()
                    customer = "sim_target" if i in (475, 506, 544) else "sim_other"
                    conn.execute(
                        "INSERT INTO sop_send_tasks (id,event_id,idempotency_key,customer_id,external_userid,corp_id,wechat,user_id,status,send_payload_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (str(i), "test-night", str(i), customer, customer, "sim_corp", "sim_wechat", "sim_staff", "completed_without_send", json.dumps({"decision": {"reason": "quiet_hours_all_sop_blocked"}}), stamp, stamp),
                    )
            service = event_service(repo=_Repo(), client=_OutreachClient(messages=[]))
            service.repository = repo
            service._process_quiet_backlog_group = AsyncMock(return_value={"status": "preview"})
            await service.process_due_quiet_backlog_fusions(now=datetime(2026, 8, 26, 1, tzinfo=timezone.utc))
            groups = [call.args[0] for call in service._process_quiet_backlog_group.call_args_list]
            target = next(g for g in groups if g["identity"]["customer_id"] == "sim_target")
            self.assertEqual([t["id"] for t in target["tasks"]], ["475", "506", "544"])
            dry_run = consume_backlog(repo, local_date="2026-08-26")
            self.assertEqual(dry_run["task_count"], 566)
            backup = Path(tmp) / "before.json"
            result = consume_backlog(repo, local_date="2026-08-26", apply=True, backup=backup)
            self.assertEqual(result["task_count"], 566)
            self.assertEqual(result["send_calls"], 0)
            self.assertEqual(consume_backlog(repo, local_date="2026-08-26")["task_count"], 0)
            self.assertEqual(len(json.loads(backup.read_text(encoding="utf-8"))["tasks"]), 566)
            self.assertEqual(repo.get_sop_send_task("475")["status"], "completed_without_send")


if __name__ == "__main__":
    unittest.main()
