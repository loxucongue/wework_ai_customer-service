from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.services.outreach.execution import TaskExecutor  # noqa: E402
from app.services.outreach.first_day import (  # noqa: E402
    FirstDayWorkflow,
    _conversation_ai_auto_reply,
    _first_day_existing_run_retry_reason,
    _first_day_wechat_allowed,
    _timestamp_at_or_after,
)
from app.services.storage.repositories import AppRepository  # noqa: E402
from app.services.storage.sqlite_store import SQLiteStore  # noqa: E402


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
    def __init__(self, system_client: _StatusClient) -> None:
        self.system_client = system_client

    @staticmethod
    def _plan_lock(_: dict[str, object]) -> asyncio.Lock:
        return asyncio.Lock()


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
    assert _first_day_wechat_allowed("SL8003", "") is True
    assert _first_day_wechat_allowed("ANY_ACCOUNT", "") is True


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
