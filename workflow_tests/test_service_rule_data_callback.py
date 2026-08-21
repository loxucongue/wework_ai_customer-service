from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.services.service_rule_data_service import ServiceRuleDataService, customer_reply_type
from app.services.storage import AppRepository, build_store


class _FakeClient:
    available = True

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def send(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return {"code": 200, "message": "ok", "data": {"duplicate": False}}


def _repository(tmp_path) -> AppRepository:
    settings = Settings(_env_file=None, AI_PATHS_DB_PATH=tmp_path / "callback.db")
    store = build_store(settings)
    store.initialize()
    return AppRepository(store)


def _create_sent_platform_task(
    repository: AppRepository,
    *,
    task_id: str,
    wechat: str = "DY258",
    external_userid: str = "external-1",
    sent_at: str,
) -> None:
    event_id = f"platform_sop_task:{task_id}"
    repository.create_sop_event(
        {
            "event_id": event_id,
            "event_type": "sop_platform_task",
            "source": "third_party_sop",
            "platform_task": {
                "taskId": task_id,
                "scene": {"sceneCode": "customer_opening", "nodeNo": "3"},
            },
        }
    )
    local = repository.create_sop_send_task(
        event_id=event_id,
        idempotency_key=f"platform-sop:{task_id}",
        customer_id="customer-1",
        external_userid=external_userid,
        corp_id="corp-1",
        user_id="7294",
        wechat=wechat,
        sop_pack_id=f"platform-sop-{task_id}",
        sop_pack_name="opening",
        sop_category="platform_task",
        trigger_source="third_party_sop_pending",
        reply_messages=[{"type": "text", "content": "hello"}],
        status="platform_received",
    )
    repository.update_sop_send_task(
        str(local["id"]),
        status="sent",
        send_payload={"platform_task_id": task_id},
        sent_at=sent_at,
    )


def _state(*, msgtype: str = "voice") -> dict:
    message_time = datetime.now(timezone.utc)
    return {
        "request_id": "request-1",
        "customer_id": "customer-1",
        "external_userid": "external-1",
        "corp_id": "corp-1",
        "wechat": "DY258",
        "sales_contact_key": "sales_contact:v2:corp-1:DY258:external-1",
        "content": "我觉得还是有点远",
        "reply_messages": [{"type": "text", "order": 1, "content": {"text": "我理解"}}],
        "request_context": {
            "interface_version": "v3",
            "msgid": "customer-msg-1",
            "msgtime": str(int(message_time.timestamp() * 1000)),
            "msgtype": msgtype,
        },
        "reply_knowledge_use": {
            "sequence_id": "18",
            "step_id": "181",
            "checkpoint_code": "distance",
            "action_code": "empathy",
            "selected_script_ids": ["D02", "D03"],
        },
        "sales_recall": {
            "candidates": [
                {"source_id": "D02", "script_id": "37"},
                {"source_id": "D03", "script_id": "38"},
            ]
        },
    }


def test_customer_reply_type_contract() -> None:
    assert customer_reply_type("text") == "text"
    assert customer_reply_type("image") == "image"
    assert customer_reply_type("video") == "video"
    assert customer_reply_type("voice") == "voice"
    assert customer_reply_type("audio") == "voice"
    assert customer_reply_type("short_video") == "video"
    assert customer_reply_type("pic") == "image"
    assert customer_reply_type("location") == "other"
    assert customer_reply_type("", has_image=True) == "image"


def test_enqueue_uses_latest_prior_task_and_first_adopted_script(tmp_path) -> None:
    repository = _repository(tmp_path)
    now = datetime.now(timezone.utc)
    _create_sent_platform_task(
        repository,
        task_id="101",
        sent_at=(now - timedelta(minutes=20)).isoformat(),
    )
    _create_sent_platform_task(
        repository,
        task_id="102",
        sent_at=(now - timedelta(minutes=5)).isoformat(),
    )
    client = _FakeClient()
    service = ServiceRuleDataService(repository=repository, client=client)

    result = service.enqueue_customer_open(_state())
    rows = repository.claim_due_strategy_data_callbacks(limit=5)
    payload = rows[0]["payload"]

    assert result["status"] == "pending"
    assert result["task_id"] == "102"
    assert payload["recordKind"] == "customer_open"
    assert payload["taskId"] == 102
    assert payload["customerReplyType"] == "voice"
    assert payload["replyMsgId"] == "customer-msg-1"
    assert payload["checkpointCode"] == "distance"
    assert payload["actionCode"] == "empathy"
    assert payload["sendContent"] == "我理解"
    assert payload["followSequenceId"] == 18
    assert payload["followSequenceStepId"] == 181
    assert payload["followScriptId"] == 37


def test_outbox_is_idempotent_and_worker_marks_sent(tmp_path) -> None:
    repository = _repository(tmp_path)
    now = datetime.now(timezone.utc)
    _create_sent_platform_task(
        repository,
        task_id="201",
        sent_at=(now - timedelta(minutes=1)).isoformat(),
    )
    client = _FakeClient()
    service = ServiceRuleDataService(repository=repository, client=client)
    state = _state(msgtype="text")

    first = service.enqueue_customer_open(state)
    second = service.enqueue_customer_open(state)
    processed = asyncio.run(service.process_due_once())

    assert first["outbox_id"] == second["outbox_id"]
    assert processed == 1
    assert len(client.payloads) == 1
    assert repository.strategy_data_outbox_status() == {"sent": 1}


def test_different_wechat_task_is_not_reused(tmp_path) -> None:
    repository = _repository(tmp_path)
    _create_sent_platform_task(
        repository,
        task_id="301",
        wechat="OTHER",
        sent_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )
    service = ServiceRuleDataService(repository=repository, client=_FakeClient())

    result = service.enqueue_customer_open(_state())

    assert result == {"status": "skipped", "reason": "no_prior_sent_platform_task"}


def test_task_sent_after_customer_message_is_not_linked(tmp_path) -> None:
    repository = _repository(tmp_path)
    _create_sent_platform_task(
        repository,
        task_id="401",
        sent_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )
    service = ServiceRuleDataService(repository=repository, client=_FakeClient())

    result = service.enqueue_customer_open(_state())

    assert result == {"status": "skipped", "reason": "no_prior_sent_platform_task"}


def test_missing_corp_boundary_cannot_reuse_platform_task(tmp_path) -> None:
    repository = _repository(tmp_path)
    _create_sent_platform_task(
        repository,
        task_id="501",
        sent_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )
    service = ServiceRuleDataService(repository=repository, client=_FakeClient())
    state = _state()
    state["corp_id"] = ""

    result = service.enqueue_customer_open(state)

    assert result == {"status": "skipped", "reason": "no_prior_sent_platform_task"}


def test_non_numeric_script_code_is_not_sent_as_follow_script_id(tmp_path) -> None:
    repository = _repository(tmp_path)
    _create_sent_platform_task(
        repository,
        task_id="601",
        sent_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )
    service = ServiceRuleDataService(repository=repository, client=_FakeClient())
    state = _state()
    state["sales_recall"]["candidates"][0]["script_id"] = "D02"

    service.enqueue_customer_open(state)
    rows = repository.claim_due_strategy_data_callbacks(limit=1)

    assert "followScriptId" not in rows[0]["payload"]


def test_v3_callback_worker_is_started_before_general_background_worker_guard() -> None:
    from app import main

    source = inspect.getsource(main.startup)
    callback_start = source.index("service_rule_data_service.available")
    general_worker_guard = source.index("if not settings.background_workers_enabled")

    assert callback_start < general_worker_guard
