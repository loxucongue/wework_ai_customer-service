from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.services.outreach.execution import TaskExecutor  # noqa: E402
from app.services.outreach.first_day import (  # noqa: E402
    FirstDayWorkflow,
    _ai_mode_gate,
    _conversation_ai_auto_reply,
    _first_day_existing_run_retry_reason,
    _first_day_full_retry_delay_seconds,
    _first_day_wechat_allowed,
    _sop_candidate_requires_platform_refresh,
    _timestamp_at_or_after,
)
from app.runtime_services import _build_outreach_model_client  # noqa: E402
from app.services.customer_scope import build_customer_scope  # noqa: E402
from app.services.storage.repositories import AppRepository  # noqa: E402
from app.services.storage.sqlite_store import SQLiteStore  # noqa: E402
from app.workers.supervisor import WorkerSupervisor  # noqa: E402


class _StatusClient:
    def __init__(self, *, mode: str = "ai", fail: bool = False) -> None:
        self.mode = mode
        self.fail = fail
        self.calls = 0

    async def conversation_status(self, **_: object) -> dict[str, object]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("status unavailable")
        is_ai = self.mode == "ai"
        return {
            "data": {
                "takeover": {
                    "mode": self.mode,
                    "is_human": not is_ai,
                    "ai_auto_reply": is_ai,
                },
                "ai_outreach": {"send_allowed": is_ai},
            }
        }


class _Repository:
    def __init__(self) -> None:
        self.actions: list[tuple[str, object]] = []
        self.runs: dict[str, dict[str, object]] = {}

    def get_active_outreach_plan_for_customer(self, *_: object, **__: object) -> dict[str, object]:
        return {}

    def find_first_day_outreach_run_by_fingerprint(self, **_: object) -> dict[str, object]:
        return {}

    def create_first_day_outreach_run(self, **values: object) -> dict[str, object]:
        run = {"workflow_run_id": "run-1", **values}
        self.runs["run-1"] = run
        return run

    def update_first_day_outreach_run(self, workflow_run_id: str, **changes: object) -> dict[str, object]:
        self.runs.setdefault(workflow_run_id, {"workflow_run_id": workflow_run_id}).update(changes)
        self.actions.append(("update_run", (workflow_run_id, changes)))
        return self.runs[workflow_run_id]

    def update_outreach_task(self, task_id: str, **changes: object) -> dict[str, object]:
        self.actions.append(("update_task", (task_id, changes)))
        return {"id": task_id, **changes}

    def skip_remaining_outreach_tasks(self, plan_id: str, **changes: object) -> None:
        self.actions.append(("skip_remaining", (plan_id, changes)))

    def update_outreach_plan_status(self, plan_id: str, status: str) -> None:
        self.actions.append(("update_plan", (plan_id, status)))

    def add_outreach_event(self, **event: object) -> None:
        self.actions.append(("event", event))

    def reschedule_outreach_task(self, task_id: str, **changes: object) -> None:
        self.actions.append(("reschedule", (task_id, changes)))


class _Planning:
    def __init__(
        self,
        system_client: _StatusClient,
        *,
        candidates: list[dict[str, object]] | None = None,
    ) -> None:
        self.system_client = system_client
        self.candidates = candidates or []

    @staticmethod
    def _plan_lock(_: dict[str, object]) -> asyncio.Lock:
        return asyncio.Lock()

    def list_candidates(self, **_: object) -> list[dict[str, object]]:
        return list(self.candidates)


class _FirstDayRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def _sync_first_day_run_for_task(self, **changes: object) -> None:
        self.calls.append(changes)


def _identity() -> dict[str, str]:
    return {
        "customer_id": "customer-1",
        "corp_id": "corp-1",
        "user_id": "user-1",
        "wechat": "SL8003",
        "external_userid": "external-1",
    }


def test_silence_defaults_to_one_minute_and_empty_allowlist_allows_every_account() -> None:
    settings = Settings(_env_file=None)
    assert settings.outreach_first_day_silence_minutes == 1
    assert settings.outreach_decision_model == "deepseek-chat"
    assert settings.outreach_decision_model_fallbacks == ""
    assert _first_day_wechat_allowed("SL8003", "") is True
    assert _first_day_wechat_allowed("ANY_ACCOUNT", "") is True


def test_outreach_model_client_never_inherits_global_gpt_candidates() -> None:
    settings = Settings(
        _env_file=None,
        MODEL_STRONG="gpt-5.4",
        MODEL_BALANCED="gpt-5.4-mini",
        MODEL_EMERGENCY_FALLBACKS="gpt-5.4,gpt-5.4-mini",
        OUTREACH_DECISION_MODEL="deepseek-chat",
        OUTREACH_DECISION_MODEL_FALLBACKS="",
    )
    client = _build_outreach_model_client(settings)
    assert client.settings.model_strong == "deepseek-chat"
    assert client.settings.model_balanced == "deepseek-chat"
    assert client.settings.model_reply == "deepseek-chat"
    assert client.settings.model_strong_fallbacks == ""
    assert client.settings.model_balanced_fallbacks == ""
    assert client.settings.model_emergency_fallbacks == ""


def test_ai_mode_parser_requires_explicit_ai_and_rejects_human() -> None:
    assert _conversation_ai_auto_reply(
        {"data": {"takeover": {"mode": "ai", "is_human": False, "ai_auto_reply": True}}}
    ) is True
    assert _conversation_ai_auto_reply(
        {"data": {"takeover": {"mode": "human", "is_human": True, "ai_auto_reply": False}}}
    ) is False
    assert _conversation_ai_auto_reply(
        {
            "data": {
                "ai_auto_reply": True,
                "takeover": {"mode": "human", "is_human": True, "ai_auto_reply": False},
            }
        }
    ) is False
    assert _conversation_ai_auto_reply({"data": {"takeover": {}}}) is None


def test_ai_mode_gate_treats_missing_conversation_as_permanent_skip() -> None:
    class MissingConversationClient:
        async def conversation_status(self, **_: object) -> dict[str, object]:
            raise RuntimeError(
                'outreach_system_http_404: {"code":40402,"msg":"conversation not found"}'
            )

    result = asyncio.run(_ai_mode_gate(MissingConversationClient(), _identity()))
    assert result["eligible"] is False
    assert result["available"] is True
    assert result["reason"] == "ai_mode_conversation_not_found"


def test_activation_watermark_does_not_limit_contact_age() -> None:
    cutoff = datetime.now(timezone.utc).replace(microsecond=0)
    old_contact = (cutoff - timedelta(days=90)).isoformat()
    latest_outbound = (cutoff + timedelta(seconds=5)).isoformat()
    candidate = {
        "candidate_source": "conversation",
        "sales_contact_started_at": old_contact,
        "last_customer_message_at": (cutoff - timedelta(minutes=2)).isoformat(),
        "last_staff_message_at": latest_outbound,
        "latest_outbound_message_at": latest_outbound,
        "reply_wait_minutes": 1,
        "awaiting_customer_reply": True,
    }
    assert FirstDayWorkflow._rough_first_day_silence_candidate_reason(
        candidate,
        silent_minutes=1,
        eligible_after=cutoff.isoformat(),
    ) == ""
    assert _timestamp_at_or_after(latest_outbound, cutoff.isoformat()) is True
    assert _timestamp_at_or_after((cutoff - timedelta(seconds=1)).isoformat(), cutoff.isoformat()) is False


def test_plan_generation_stops_before_conversation_or_model_when_customer_is_human() -> None:
    repository = _Repository()
    client = _StatusClient(mode="human")
    workflow = FirstDayWorkflow(
        repository=repository,
        model_client=object(),
        customer_context_service=None,
        first_day_wechat_allowlist="",
        planning=_Planning(client),
    )
    candidate = {
        **_identity(),
        "last_customer_message_at": "2026-09-05T09:00:00+00:00",
        "last_staff_message_at": "2026-09-05T09:01:00+00:00",
        "latest_outbound_message_at": "2026-09-05T09:01:00+00:00",
    }
    result = asyncio.run(
        workflow._evaluate_first_day_silence_candidate(
            candidate,
            silent_minutes=1,
            auto_activate=True,
            eligible_after="2026-09-05T09:00:30+00:00",
        )
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "human_mode"
    assert client.calls == 1
    assert repository.runs["run-1"]["status"] == "blocked"
    assert repository.runs["run-1"]["final_decision"] == "no_plan"


def test_monitor_reactivates_auto_approved_draft_plan() -> None:
    class DraftRepository(_Repository):
        def __init__(self) -> None:
            super().__init__()
            self.runs["run-1"] = {"workflow_run_id": "run-1", "status": "created"}

        def find_first_day_outreach_run_by_fingerprint(self, **_: object) -> dict[str, object]:
            return dict(self.runs["run-1"])

        def get_active_outreach_plan_for_customer(self, *_: object, **__: object) -> dict[str, object]:
            return {
                "plan": {
                    "id": "plan-1",
                    "status": "draft",
                    "source_snapshot": {
                        "workflow_run_id": "run-1",
                        "trigger_context": {
                            "trigger_type": "first_day_opened_silence",
                            "activation_policy": "auto_approved",
                        },
                    },
                },
                "tasks": [{"id": "task-1", "status": "pending"}],
            }

    class Planning(_Planning):
        @staticmethod
        def _auto_approve_plan(plan_id: str) -> dict[str, object]:
            return {"plan": {"id": plan_id, "status": "active"}}

    repository = DraftRepository()
    workflow = FirstDayWorkflow(
        repository=repository,
        model_client=object(),
        customer_context_service=None,
        first_day_wechat_allowlist="",
        planning=Planning(_StatusClient()),
    )
    candidate = {
        **_identity(),
        "last_customer_message_at": "2026-09-05T09:00:00+00:00",
        "last_staff_message_at": "2026-09-05T09:02:00+00:00",
        "latest_outbound_message_at": "2026-09-05T09:02:00+00:00",
    }

    result = asyncio.run(
        workflow._evaluate_first_day_silence_candidate(
            candidate,
            silent_minutes=1,
            auto_activate=True,
            eligible_after="2026-09-05T09:00:30+00:00",
        )
    )

    assert result["status"] == "evaluated"
    assert result["created"] is True
    assert result["reason"] == "draft_plan_reactivated"
    assert repository.runs["run-1"]["reason_code"] == "draft_plan_reactivated"


def test_send_rechecks_ai_mode_and_cancels_human_plan_without_delivery() -> None:
    repository = _Repository()
    client = _StatusClient(mode="human")
    recorder = _FirstDayRecorder()
    executor = TaskExecutor(
        repository=repository,
        system_client=client,
        customer_context_service=None,
        before_send_retry_seconds=60,
        first_day_wechat_allowlist="",
        planning=object(),
        first_day=recorder,
        message=object(),
    )
    result = asyncio.run(
        executor._check_send_eligibility(
            {
                "task_id": "task-1",
                "task": {"id": "task-1", "plan_id": "plan-1", "customer_id": "customer-1"},
                "plan": {"id": "plan-1"},
                "is_first_day_plan": True,
                "fresh_conversation_messages": [],
                "send_conversation_id": "conversation-1",
                "identity": _identity(),
            }
        )
    )
    assert result == {"ok": True, "status": "skipped", "reason": "human_mode"}
    assert any(action[0] == "update_plan" and action[1][1] == "cancelled" for action in repository.actions)
    assert not any(action[0] == "reschedule" for action in repository.actions)


def test_send_fails_closed_and_retries_when_ai_mode_is_unknown() -> None:
    repository = _Repository()
    client = _StatusClient(fail=True)
    executor = TaskExecutor(
        repository=repository,
        system_client=client,
        customer_context_service=None,
        before_send_retry_seconds=60,
        first_day_wechat_allowlist="",
        planning=object(),
        first_day=_FirstDayRecorder(),
        message=object(),
    )
    result = asyncio.run(
        executor._check_send_eligibility(
            {
                "task_id": "task-1",
                "task": {"id": "task-1", "plan_id": "plan-1", "customer_id": "customer-1"},
                "plan": {"id": "plan-1"},
                "is_first_day_plan": True,
                "fresh_conversation_messages": [],
                "send_conversation_id": "conversation-1",
                "identity": _identity(),
            }
        )
    )
    assert result["status"] == "rescheduled"
    assert result["reason"] == "ai_mode_status_unavailable"
    assert any(action[0] == "reschedule" for action in repository.actions)


def test_stale_nonterminal_plan_without_executable_tasks_does_not_block_new_cycle(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        AI_PATHS_DB_PATH=tmp_path / "outreach.db",
        AICS_STORAGE_BACKEND="sqlite",
    )
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    created = repository.create_outreach_plan(
        **_identity(),
        customer_stage="",
        stall_reason="",
        customer_psychology="",
        plan_goal="test",
        source_snapshot={"trigger_context": {"trigger_type": "first_day_opened_silence"}},
        tasks=[{"step_index": 1, "scheduled_at": datetime.now(timezone.utc).isoformat()}],
        sop_plan_id="first_day_opened_silence",
    )
    task_id = created["tasks"][0]["id"]
    assert repository.get_active_outreach_plan_for_customer(
        "customer-1",
        corp_id="corp-1",
        wechat="SL8003",
        external_userid="external-1",
    )
    repository.update_outreach_task(task_id, status="failed")
    assert repository.get_active_outreach_plan_for_customer(
        "customer-1",
        corp_id="corp-1",
        wechat="SL8003",
        external_userid="external-1",
    ) == {}


def test_platform_conversation_sync_soft_block_retries_only_once() -> None:
    existing = {
        "status": "blocked",
        "reason_code": "customer_never_spoke",
        "retry_count": 0,
    }
    assert _first_day_existing_run_retry_reason(
        existing,
        latest_customer_message_at="2026-09-05T09:42:00+00:00",
    ) == "soft_block_retry:customer_never_spoke"
    existing["retry_count"] = 1
    assert _first_day_existing_run_retry_reason(
        existing,
        latest_customer_message_at="2026-09-05T09:42:00+00:00",
    ) == ""

    duplicate = {
        "status": "blocked",
        "reason_code": "authoritative_fingerprint_already_logged",
        "retry_count": 0,
    }
    assert _first_day_existing_run_retry_reason(
        duplicate,
        latest_customer_message_at="2026-09-05T09:42:00+00:00",
    ) == "soft_block_retry:authoritative_fingerprint_already_logged"
    duplicate["retry_count"] = 1
    assert _first_day_existing_run_retry_reason(
        duplicate,
        latest_customer_message_at="2026-09-05T09:42:00+00:00",
    ) == ""


def test_failed_model_cycle_can_recover_after_model_configuration_is_fixed() -> None:
    existing = {
        "status": "failed",
        "reason_code": "workflow_failed",
        "retry_count": 0,
        "next_retry_at": "",
    }
    assert _first_day_existing_run_retry_reason(
        existing,
        latest_customer_message_at="2026-09-05T09:42:00+00:00",
    ) == "failed_recovery:workflow_failed"
    existing["retry_count"] = 2
    assert _first_day_existing_run_retry_reason(
        existing,
        latest_customer_message_at="2026-09-05T09:42:00+00:00",
    ) == ""


def test_ai_mode_status_retry_is_bounded_even_with_expired_retry_time() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    existing = {
        "status": "failed",
        "reason_code": "ai_mode_status_unavailable",
        "retry_count": 2,
        "next_retry_at": (now - timedelta(seconds=1)).isoformat(),
    }
    assert _first_day_existing_run_retry_reason(
        existing,
        latest_customer_message_at=now.isoformat(),
        now=now,
    ) == "failed_retry"
    existing["retry_count"] = 3
    assert _first_day_existing_run_retry_reason(
        existing,
        latest_customer_message_at=now.isoformat(),
        now=now,
    ) == ""


def test_unsupported_model_failure_gets_bounded_retry() -> None:
    error = "HTTP 404: model gpt-5.4 is not supported"
    assert _first_day_full_retry_delay_seconds(error, 0) == 60
    assert _first_day_full_retry_delay_seconds(error, 1) == 300
    assert _first_day_full_retry_delay_seconds(error, 2) is None


def test_plan_generation_null_failure_gets_one_recovery_retry() -> None:
    error = "'NoneType' object has no attribute 'get'"
    assert _first_day_full_retry_delay_seconds(error, 0) == 60
    assert _first_day_full_retry_delay_seconds(error, 1) is None
    assert _first_day_full_retry_delay_seconds(
        "first_day_follow_sequence_nodes_unavailable",
        0,
    ) == 60


def test_superseded_provisional_run_gets_bounded_authoritative_refreshes() -> None:
    now = datetime.now(timezone.utc)
    existing = {
        "status": "blocked",
        "reason_code": "superseded_by_retryable_authoritative_run",
        "retry_count": 1,
    }
    assert _first_day_existing_run_retry_reason(
        existing,
        latest_customer_message_at=now.isoformat(),
        now=now,
    ) == "soft_block_retry:superseded_by_retryable_authoritative_run"
    existing["retry_count"] = 2
    assert _first_day_existing_run_retry_reason(
        existing,
        latest_customer_message_at=now.isoformat(),
        now=now,
    ) == "soft_block_retry:superseded_by_retryable_authoritative_run"
    existing["retry_count"] = 3
    assert _first_day_existing_run_retry_reason(
        existing,
        latest_customer_message_at=now.isoformat(),
        now=now,
    ) == ""


def test_interrupted_preflight_retry_recovers_after_two_minutes() -> None:
    now = datetime.now(timezone.utc)
    existing = {
        "status": "running",
        "reason_code": "preflight_retry",
        "retry_count": 2,
        "plan_id": "",
        "updated_at": (now - timedelta(minutes=3)).isoformat(),
    }

    assert _first_day_existing_run_retry_reason(
        existing,
        latest_customer_message_at=now.isoformat(),
        now=now,
    ) == "stale_running_retry"


def test_authoritative_fingerprint_resumes_failed_run_without_a_plan() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    customer_at = (now - timedelta(minutes=3)).isoformat()
    authoritative_staff_at = (now - timedelta(minutes=2)).isoformat()
    candidate_staff_at = (now - timedelta(minutes=4)).isoformat()

    from app.services.outreach.first_day import _conversation_fingerprint

    authoritative_fingerprint = _conversation_fingerprint(
        corp_id="corp-1",
        wechat="SL8003",
        external_userid="external-1",
        customer_id="customer-1",
        latest_customer_message_at=customer_at,
        latest_staff_message_at=authoritative_staff_at,
    )

    class RecoveryRepository(_Repository):
        def __init__(self) -> None:
            super().__init__()
            self.runs["failed-run"] = {
                "workflow_run_id": "failed-run",
                "status": "failed",
                "reason_code": "workflow_failed",
                "retry_count": 0,
                "conversation_fingerprint": authoritative_fingerprint,
                "workflow": {},
            }

        def create_first_day_outreach_run(self, **values: object) -> dict[str, object]:
            run = {"workflow_run_id": "provisional-run", **values}
            self.runs["provisional-run"] = run
            return run

        def find_first_day_outreach_run_by_fingerprint(self, **values: object) -> dict[str, object]:
            if values.get("conversation_fingerprint") == authoritative_fingerprint:
                return dict(self.runs["failed-run"])
            return {}

        def has_outreach_evaluation_fingerprint(self, **_: object) -> bool:
            return False

        def recent_customer_context(self, *_: object, **__: object) -> dict[str, object]:
            return {"memory": {}}

    class RecoveryPlanning(_Planning):
        def __init__(self) -> None:
            super().__init__(_StatusClient())
            self.generated_with = ""

        async def refresh_customer_conversation(self, **_: object) -> dict[str, object]:
            return {
                "customer_relation": {"available": True, "deleted": False},
                "conversation_id": "conversation-1",
                "first_added_at": (now - timedelta(days=10)).isoformat(),
                "messages": [
                    {"direction": "customer", "content": "还是太远", "created_at": customer_at},
                    {"direction": "staff", "content": "可以给您发效果图", "created_at": authoritative_staff_at},
                ],
            }

        @staticmethod
        def _latest_message_time(messages: list[dict[str, object]], *, sender: str) -> str:
            direction = "customer" if sender == "customer" else "staff"
            return max(
                str(item.get("created_at") or "")
                for item in messages
                if item.get("direction") == direction
            )

        @staticmethod
        def _completed_cycle_blocks_auto_plan(**_: object) -> bool:
            return False

        async def generate_plan(self, **values: object) -> dict[str, object]:
            self.generated_with = str(values.get("workflow_run_id") or "")
            return {"created": True, "plan": {"id": "plan-1"}}

        @staticmethod
        def _auto_approve_plan(plan_id: str) -> dict[str, object]:
            return {"plan_id": plan_id, "status": "active"}

    repository = RecoveryRepository()
    planning = RecoveryPlanning()
    workflow = FirstDayWorkflow(
        repository=repository,
        model_client=object(),
        customer_context_service=object(),
        first_day_wechat_allowlist="",
        planning=planning,
    )

    async def load_context(**_: object) -> dict[str, object]:
        return {"source": "platform_agent", "orders": []}

    workflow._load_monitor_customer_context = load_context  # type: ignore[method-assign]
    result = asyncio.run(
        workflow._evaluate_first_day_silence_candidate(
            {
                **_identity(),
                "last_customer_message_at": customer_at,
                "last_staff_message_at": candidate_staff_at,
                "latest_outbound_message_at": candidate_staff_at,
            },
            silent_minutes=1,
            auto_activate=True,
            eligible_after=(now - timedelta(hours=1)).isoformat(),
        )
    )

    assert result["created"] is True
    assert planning.generated_with == "failed-run"
    assert repository.runs["failed-run"]["status"] == "running"
    assert repository.runs["failed-run"]["retry_count"] == 1
    assert repository.runs["provisional-run"]["reason_code"] == "superseded_by_retryable_authoritative_run"


def test_monitor_evaluates_longest_waiting_customer_first() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def candidate(customer_id: str, *, wait_minutes: int) -> dict[str, object]:
        return {
            **_identity(),
            "customer_id": customer_id,
            "candidate_source": "conversation",
            "last_customer_message_at": (now - timedelta(minutes=wait_minutes + 1)).isoformat(),
            "latest_outbound_message_at": (now - timedelta(minutes=wait_minutes)).isoformat(),
            "reply_wait_minutes": wait_minutes,
            "awaiting_customer_reply": True,
        }

    planning = _Planning(
        _StatusClient(),
        candidates=[candidate("newly-eligible", wait_minutes=1), candidate("oldest", wait_minutes=30)],
    )
    workflow = FirstDayWorkflow(
        repository=_Repository(),
        model_client=object(),
        customer_context_service=None,
        first_day_wechat_allowlist="",
        planning=planning,
    )
    evaluated: list[str] = []

    async def record(candidate_value: dict[str, object], **_: object) -> dict[str, object]:
        evaluated.append(str(candidate_value["customer_id"]))
        return {"status": "evaluated", "customer_id": candidate_value["customer_id"], "created": False}

    workflow._evaluate_first_day_silence_candidate = record  # type: ignore[method-assign]
    result = asyncio.run(
        workflow.evaluate_first_day_opened_silence_customers(
            limit=1,
            silent_minutes=1,
            eligible_after=(now - timedelta(hours=1)).isoformat(),
        )
    )
    assert evaluated == ["oldest"]
    assert result["evaluated_count"] == 1
    assert workflow.monitor_status()["state"] == "idle"


def test_monitor_prioritizes_retryable_fingerprint_before_fresh_candidates() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def candidate(customer_id: str, *, wait_minutes: int) -> dict[str, object]:
        return {
            **_identity(),
            "customer_id": customer_id,
            "candidate_source": "conversation",
            "last_customer_message_at": (now - timedelta(minutes=wait_minutes + 1)).isoformat(),
            "latest_outbound_message_at": (now - timedelta(minutes=wait_minutes)).isoformat(),
            "reply_wait_minutes": wait_minutes,
            "awaiting_customer_reply": True,
        }

    retry_candidate = candidate("retry-customer", wait_minutes=2)
    fresh_candidate = candidate("fresh-customer", wait_minutes=30)

    class RetrySnapshotRepository(_Repository):
        def list_first_day_outreach_runs_for_monitor(self, **_: object) -> list[dict[str, object]]:
            from app.services.outreach.first_day import _conversation_fingerprint

            return [
                {
                    **retry_candidate,
                    "workflow_run_id": "retry-run",
                    "status": "blocked",
                    "reason_code": "authoritative_fingerprint_already_logged",
                    "retry_count": 0,
                    "conversation_fingerprint": _conversation_fingerprint(
                        corp_id="corp-1",
                        wechat="SL8003",
                        external_userid="external-1",
                        customer_id="retry-customer",
                        latest_customer_message_at=str(retry_candidate["last_customer_message_at"]),
                        latest_staff_message_at=str(retry_candidate["latest_outbound_message_at"]),
                    ),
                }
            ]

    workflow = FirstDayWorkflow(
        repository=RetrySnapshotRepository(),
        model_client=object(),
        customer_context_service=None,
        first_day_wechat_allowlist="",
        planning=_Planning(_StatusClient(), candidates=[fresh_candidate, retry_candidate]),
    )
    evaluated: list[str] = []

    async def record(candidate_value: dict[str, object], **_: object) -> dict[str, object]:
        evaluated.append(str(candidate_value["customer_id"]))
        return {"status": "evaluated", "customer_id": candidate_value["customer_id"], "created": False}

    workflow._evaluate_first_day_silence_candidate = record  # type: ignore[method-assign]
    result = asyncio.run(
        workflow.evaluate_first_day_opened_silence_customers(
            limit=1,
            silent_minutes=1,
            eligible_after=(now - timedelta(hours=1)).isoformat(),
        )
    )

    assert evaluated == ["retry-customer"]
    assert result["evaluated_count"] == 1


def test_monitor_exception_closes_unplanned_running_run_for_retry() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    candidate = {
        **_identity(),
        "candidate_source": "conversation",
        "last_customer_message_at": (now - timedelta(minutes=3)).isoformat(),
        "latest_outbound_message_at": (now - timedelta(minutes=2)).isoformat(),
        "reply_wait_minutes": 2,
        "awaiting_customer_reply": True,
    }

    class RecoveryRepository(_Repository):
        def __init__(self) -> None:
            super().__init__()
            self.runs["running-run"] = {
                "workflow_run_id": "running-run",
                "status": "running",
                "retry_count": 0,
            }

        def find_latest_unplanned_first_day_outreach_run_for_customer(
            self, **_: object
        ) -> dict[str, object]:
            return dict(self.runs["running-run"])

    repository = RecoveryRepository()
    workflow = FirstDayWorkflow(
        repository=repository,
        model_client=object(),
        customer_context_service=None,
        first_day_wechat_allowlist="",
        planning=_Planning(_StatusClient(), candidates=[candidate]),
    )

    async def fail(*_: object, **__: object) -> dict[str, object]:
        raise TimeoutError("context timed out")

    workflow._evaluate_first_day_silence_candidate = fail  # type: ignore[method-assign]
    result = asyncio.run(
        workflow.evaluate_first_day_opened_silence_customers(
            limit=1,
            silent_minutes=1,
            eligible_after=(now - timedelta(hours=1)).isoformat(),
        )
    )

    assert result["error_count"] == 1
    assert result["results"][0]["workflow_run_id"] == "running-run"
    assert repository.runs["running-run"]["status"] == "failed"
    assert repository.runs["running-run"]["reason_code"] == "workflow_retry_scheduled"
    assert repository.runs["running-run"]["error_node"] == "silence_candidate_evaluation"
    assert repository.runs["running-run"]["next_retry_at"]


def test_repository_finds_only_scoped_unplanned_running_run(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        AI_PATHS_DB_PATH=tmp_path / "outreach-running-run.db",
        AICS_STORAGE_BACKEND="sqlite",
    )
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    run = repository.create_first_day_outreach_run(
        **_identity(),
        trigger_type="first_day_opened_silence",
        conversation_fingerprint="fingerprint-1",
    )

    found = repository.find_latest_unplanned_first_day_outreach_run_for_customer(
        customer_id="customer-1",
        corp_id="corp-1",
        wechat="sl8003",
        external_userid="external-1",
    )

    assert found["workflow_run_id"] == run["workflow_run_id"]
    assert repository.find_latest_unplanned_first_day_outreach_run_for_customer(
        customer_id="customer-1",
        corp_id="corp-1",
        wechat="another-wechat",
        external_userid="external-1",
    ) == {}


def test_monitor_preloads_fingerprints_and_avoids_per_candidate_lookup() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    customer_at = (now - timedelta(minutes=3)).isoformat()
    staff_at = (now - timedelta(minutes=2)).isoformat()
    candidate = {
        **_identity(),
        "candidate_source": "conversation",
        "last_customer_message_at": customer_at,
        "latest_outbound_message_at": staff_at,
        "reply_wait_minutes": 2,
        "awaiting_customer_reply": True,
    }

    class SnapshotRepository(_Repository):
        def __init__(self) -> None:
            super().__init__()
            self.find_calls = 0
            self.active_calls = 0

        def list_first_day_outreach_runs_for_monitor(self, **_: object) -> list[dict[str, object]]:
            from app.services.outreach.first_day import _conversation_fingerprint

            return [{
                **_identity(),
                "workflow_run_id": "known-run",
                "status": "blocked",
                "reason_code": "human_mode",
                "conversation_fingerprint": _conversation_fingerprint(
                    corp_id="corp-1",
                    wechat="SL8003",
                    external_userid="external-1",
                    customer_id="customer-1",
                    latest_customer_message_at=customer_at,
                    latest_staff_message_at=staff_at,
                ),
            }]

        def find_first_day_outreach_run_by_fingerprint(self, **_: object) -> dict[str, object]:
            self.find_calls += 1
            return {}

        def get_active_outreach_plan_for_customer(self, *_: object, **__: object) -> dict[str, object]:
            self.active_calls += 1
            return {}

    repository = SnapshotRepository()
    workflow = FirstDayWorkflow(
        repository=repository,
        model_client=object(),
        customer_context_service=None,
        first_day_wechat_allowlist="",
        planning=_Planning(_StatusClient(), candidates=[candidate]),
    )
    result = asyncio.run(
        workflow.evaluate_first_day_opened_silence_customers(
            limit=1,
            silent_minutes=1,
            eligible_after=(now - timedelta(hours=1)).isoformat(),
        )
    )

    assert result["skipped_count"] == 1
    assert result["skip_reasons"] == {"conversation_fingerprint_already_logged": 1}
    assert repository.find_calls == 0
    assert repository.active_calls == 0


def test_candidate_uses_newer_conversation_customer_time_over_stale_memory(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        AI_PATHS_DB_PATH=tmp_path / "candidate-time.db",
        AICS_STORAGE_BACKEND="sqlite",
    )
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    request = SimpleNamespace(
        customer_id="customer-time",
        external_userid="external-time",
        corp_id="corp-time",
        user_id="user-time",
        wechat="SL8003",
    )
    repository.upsert_conversation(conversation_id="conversation-time", request=request, title="")
    repository.add_user_message(
        conversation_id="conversation-time",
        request_id="request-user",
        content="想了解一下",
        file_image=None,
    )
    repository.add_assistant_message(
        conversation_id="conversation-time",
        request_id="request-assistant",
        reply_messages=[{"type": "text", "content": "可以的"}],
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stale_customer_at = (now - timedelta(hours=2)).isoformat()
    actual_customer_at = (now - timedelta(minutes=3)).isoformat()
    actual_staff_at = (now - timedelta(minutes=2)).isoformat()
    with store.connect() as conn:
        conn.execute(
            "UPDATE conversations SET created_at=?, updated_at=? WHERE id=?",
            ((now - timedelta(minutes=4)).isoformat(), (now - timedelta(minutes=2)).isoformat(), "conversation-time"),
        )
        conn.execute(
            "UPDATE messages SET created_at=? WHERE request_id=?",
            (actual_customer_at, "request-user"),
        )
        conn.execute(
            "UPDATE messages SET created_at=? WHERE request_id=?",
            (actual_staff_at, "request-assistant"),
        )
    scope = build_customer_scope(
        corp_id="corp-time",
        wechat="SL8003",
        external_userid="external-time",
        customer_id="customer-time",
    )
    repository.touch_customer_message_time(
        scope.sales_contact_key,
        field="last_customer_message_at",
        value=stale_customer_at,
    )

    candidates = repository.list_outreach_candidates(limit=10, silent_minutes_min=0)
    assert len(candidates) == 1
    assert candidates[0]["last_customer_message_at"] == actual_customer_at
    assert candidates[0]["latest_outbound_message_at"] == actual_staff_at
    assert candidates[0]["awaiting_customer_reply"] is True


def test_worker_health_exposes_silence_monitor_configuration() -> None:
    settings = SimpleNamespace(
        outreach_first_day_silence_enabled=True,
        outreach_first_day_silence_minutes=1,
        outreach_first_day_wechat_allowlist="",
        outreach_decision_model="deepseek-chat",
        outreach_decision_model_fallbacks="",
        outreach_silence_eligible_after="2026-09-05T09:41:20+00:00",
    )
    outreach_service = SimpleNamespace(
        monitor_status=lambda: {"state": "idle", "candidate_count": 3, "last_error": ""}
    )
    supervisor = WorkerSupervisor(
        settings,  # type: ignore[arg-type]
        SimpleNamespace(outreach_service=outreach_service),  # type: ignore[arg-type]
    )
    status = supervisor.status()
    assert status["enabled"] is True
    assert status["threshold_minutes"] == 1
    assert status["wechat_scope"] == "all"
    assert status["decision_model"] == "deepseek-chat"
    assert status["monitor"]["state"] == "idle"  # type: ignore[index]


def test_sop_discovery_refresh_retries_only_once() -> None:
    candidate = {
        "candidate_source": "sop_send_tasks",
        "last_customer_message_at": "",
    }
    assert _sop_candidate_requires_platform_refresh({"retry_count": 0}, candidate) is True
    assert _sop_candidate_requires_platform_refresh({"retry_count": 1}, candidate) is False
