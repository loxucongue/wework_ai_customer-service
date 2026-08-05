from __future__ import annotations

import json
import re
import subprocess
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks

from app.chat_runtime import ChatRuntime
from app.config import Settings
from app.graph.graph_builder import build_reply_graphs
from app.schemas import ChatRequest
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.platform_reply_coordinator import PlatformReplyCoordinator
from app.services.precision_qa_playbook_service import PrecisionQaPlaybookService
from app.services.sop_event_service import SopEventService
from app.services.sop_execution_service import SopExecutionService
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.storage import AppRepository, SQLiteStore
from app.services.trace_logger import TraceLogger
from app.services.voice_transcription import transcribe_voice_request
from app.services.workflow_compat import normalize_workflow_request
from app.simulation.adapters import (
    SimulationCozeClient,
    SimulationCustomerContextService,
    SimulationModelClient,
    SimulationOutreachClient,
    SimulationPlatformAgentClient,
    SimulationStoreKnowledgeService,
    SimulationStoreService,
    SimulationVoiceTranscriptionClient,
    SimulationWorld,
)
from app.simulation.isolation import assert_simulation_identity, assert_simulation_isolated


@dataclass
class SimulationBundle:
    settings: Settings
    world: SimulationWorld
    repository: AppRepository
    memory_store: CustomerMemoryStore
    model_client: ModelClient
    voice_transcription_client: SimulationVoiceTranscriptionClient
    outreach_client: SimulationOutreachClient
    chat_runtime: ChatRuntime
    sop_event_service: SopEventService

    async def aclose(self) -> None:
        await self.model_client.aclose()


class SimulationRuntime:
    def __init__(self, *, repo_root: Path, run_root: Path | None = None, base_settings: Settings | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.run_root = (run_root or self.repo_root / ".tmp_runtime" / "simulation").resolve()
        self.base_settings = base_settings or Settings()

    async def run_scenario(
        self,
        scenario: dict[str, Any],
        *,
        attempt: int = 1,
        model_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        scenario_id = str(scenario.get("id") or "").strip()
        if not scenario_id:
            raise ValueError("scenario.id is required")
        run_id = f"{_safe_name(scenario_id)}-a{attempt}-{uuid4().hex[:8]}"
        run_dir = self.run_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        bundle = self._build_bundle(scenario=scenario, run_dir=run_dir, model_overrides=model_overrides or {})
        started = time.perf_counter()
        step_results: list[dict[str, Any]] = []
        try:
            self._seed_initial_state(bundle, scenario.get("initial") if isinstance(scenario.get("initial"), dict) else {})
            for index, raw_step in enumerate(scenario.get("timeline") or [], start=1):
                step = raw_step if isinstance(raw_step, dict) else {}
                result = await self._run_step(bundle, scenario, step, index=index)
                step_results.append(result)
            result = self._build_result(
                bundle=bundle,
                scenario=scenario,
                attempt=attempt,
                step_results=step_results,
                duration_ms=round((time.perf_counter() - started) * 1000),
                run_dir=run_dir,
            )
            (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            (run_dir / "outbox.json").write_text(
                json.dumps(bundle.world.outbox, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return result
        finally:
            await bundle.aclose()

    def _build_bundle(
        self,
        *,
        scenario: dict[str, Any],
        run_dir: Path,
        model_overrides: dict[str, str],
    ) -> SimulationBundle:
        identity = _simulation_identity(scenario)
        assert_simulation_identity(identity)
        initial = scenario.get("initial") if isinstance(scenario.get("initial"), dict) else {}
        world = SimulationWorld(
            scenario_id=str(scenario["id"]),
            identity=identity,
            customer=deepcopy(initial.get("customer") or {}),
            orders=deepcopy(initial.get("orders") or []),
            stores=deepcopy(initial.get("stores") or []),
            case_facts=deepcopy(initial.get("case_facts") or []),
            geocodes=deepcopy(initial.get("geocodes") or {}),
            distances=deepcopy(initial.get("distances") or {}),
            available_times=deepcopy(initial.get("available_times") or {}),
            voice_transcripts=deepcopy(initial.get("voice_transcripts") or {}),
            faults=deepcopy(initial.get("faults") or {}),
        )
        settings = self.base_settings.model_copy(
            update={
                "db_path": run_dir / "state.db",
                "memory_dir": run_dir / "memory",
                "log_dir": run_dir / "logs",
                "trace_log_dir": run_dir / "traces",
                "store_snapshot_path": run_dir / "store_snapshot.json",
                "platform_agent_token": "",
                "outreach_send_agent_token": "",
                "outreach_system_token": "",
                "coze_oauth_client_id": "",
                "coze_oauth_public_key_id": "",
                "coze_oauth_private_key_file": None,
                "doubao_asr_api_key": "",
                "doubao_asr_app_key": "",
                "doubao_asr_access_key": "",
                "doubao_asr_secret_key": "",
                "platform_agent_base_url": "simulation://platform",
                "outreach_send_base_url": "simulation://outbox",
                "outreach_system_base_url": "simulation://system",
                "model_fast": model_overrides.get("fast") or self.base_settings.model_fast,
                "model_planner": model_overrides.get("planner") or self.base_settings.model_planner,
                "model_balanced": model_overrides.get("balanced") or self.base_settings.model_balanced,
                "model_strong": model_overrides.get("strong") or self.base_settings.model_strong,
                "model_reply": model_overrides.get("reply") or self.base_settings.model_reply,
            }
        )

        outreach = SimulationOutreachClient(world)
        customer_context = SimulationCustomerContextService(world)
        store_knowledge = SimulationStoreKnowledgeService(world)
        store_service = SimulationStoreService(world)
        platform = SimulationPlatformAgentClient(world)
        coze = SimulationCozeClient(
            world,
            geocode_workflow_id=settings.geocode_workflow_id,
            distance_workflow_id=settings.distance_workflow_id,
        )
        voice_transcription = SimulationVoiceTranscriptionClient(world)
        assert_simulation_isolated(
            settings=settings,
            run_dir=run_dir,
            adapters=[
                outreach,
                customer_context,
                store_knowledge,
                store_service,
                platform,
                coze,
                voice_transcription,
            ],
            identity=identity,
        )

        sqlite_store = SQLiteStore(settings)
        sqlite_store.initialize()
        repository = AppRepository(sqlite_store)
        memory_store = CustomerMemoryStore(settings, repository)
        trace_logger = TraceLogger(settings)
        model_client = SimulationModelClient(settings, world)
        sop_pack_service = SopReplyPackService(settings)
        _ = PrecisionQaPlaybookService(settings)
        sop_execution = SopExecutionService(
            repository=repository,
            sop_reply_pack_service=sop_pack_service,
            model_client=model_client,
            memory_store=memory_store,
            customer_context_service=customer_context,
            event_model_retry_attempts=settings.sop_event_model_retry_attempts,
            event_model_retry_delay_seconds=settings.sop_event_model_retry_delay_seconds,
            event_model_attempt_timeout_seconds=settings.sop_event_model_attempt_timeout_seconds,
            event_model_total_timeout_seconds=settings.sop_event_model_total_timeout_seconds,
            chat_gate_total_timeout_seconds=settings.sop_chat_gate_total_timeout_seconds,
            event_model_max_concurrency=2,
        )
        sop_event = SopEventService(
            repository=repository,
            sop_reply_pack_service=sop_pack_service,
            outreach_send_client=outreach,
            sop_execution_service=sop_execution,
            memory_store=memory_store,
            customer_context_service=customer_context,
            daily_touch_soft_limit=settings.sop_event_daily_touch_soft_limit,
            default_identity=identity,
            persistent_retry_attempts=1,
            retry_batch_size=1,
        )
        graphs = build_reply_graphs(
            coze,
            trace_logger,
            model_client,
            memory_store,
            customer_context,
            store_knowledge,
            store_service,
            outreach,
            platform,
        )
        chat_runtime = ChatRuntime(
            full_graph=graphs.full_graph,
            planner_graph=graphs.planner_graph,
            finalize_graph=graphs.finalize_graph,
            trace_logger=trace_logger,
            repository=repository,
            outreach_send_client=outreach,
            memory_store=memory_store,
            platform_reply_coordinator=PlatformReplyCoordinator(settings),
            sop_execution_service=sop_execution,
            profile_event_extractor=graphs.profile_event_extractor,
            settings=settings,
        )
        return SimulationBundle(
            settings=settings,
            world=world,
            repository=repository,
            memory_store=memory_store,
            model_client=model_client,
            voice_transcription_client=voice_transcription,
            outreach_client=outreach,
            chat_runtime=chat_runtime,
            sop_event_service=sop_event,
        )

    def _seed_initial_state(self, bundle: SimulationBundle, initial: dict[str, Any]) -> None:
        history = initial.get("conversation") if isinstance(initial.get("conversation"), list) else []
        for item in history:
            if isinstance(item, str):
                role, _, content = item.partition(":")
                direction = "customer" if role.strip() in {"用户", "客户"} else "staff"
                bundle.world.conversation.append(
                    {"direction": direction, "role": "user" if direction == "customer" else "assistant", "content": content.strip(), "created_at": _utc_now()}
                )
            elif isinstance(item, dict):
                bundle.world.conversation.append(deepcopy(item))

        profile = initial.get("profile") if isinstance(initial.get("profile"), dict) else {}
        events = initial.get("history_events") if isinstance(initial.get("history_events"), list) else []
        if profile or events:
            bundle.memory_store.save_update(
                _sales_contact_key(bundle.world.identity),
                profile_update=deepcopy(profile),
                event_updates=deepcopy(events),
            )

        for index, pack_id in enumerate(initial.get("completed_sops") or [], start=1):
            seed_event_id = f"sim_seed_event_{index}"
            bundle.repository.create_sop_event(
                {
                    "event_id": seed_event_id,
                    "event_type": "simulation_seed",
                    "source": "offline_simulation",
                    "created_at": _utc_now(),
                    "request_reply": False,
                }
            )
            task = bundle.repository.create_sop_send_task(
                event_id=seed_event_id,
                idempotency_key=f"sim_seed_{bundle.world.scenario_id}_{pack_id}",
                send_once_key=f"sim_seed_once_{bundle.world.scenario_id}_{pack_id}",
                customer_id=bundle.world.identity["customer_id"],
                external_userid=bundle.world.identity["external_userid"],
                corp_id=bundle.world.identity["corp_id"],
                user_id=str(bundle.world.identity["user_id"]),
                wechat=bundle.world.identity["wechat"],
                sop_pack_id=str(pack_id),
                sop_pack_name=str(pack_id),
                sop_category="simulation_seed",
                trigger_source="sop_event",
                reply_messages=[],
                status="sent",
            )
            if task.get("id"):
                bundle.repository.update_sop_send_task(str(task["id"]), status="sent", sent_at=_utc_now())

    async def _run_step(
        self,
        bundle: SimulationBundle,
        scenario: dict[str, Any],
        step: dict[str, Any],
        *,
        index: int,
    ) -> dict[str, Any]:
        kind = str(step.get("kind") or "customer_message")
        bundle.world.apply_facts(step.get("facts") if isinstance(step.get("facts"), dict) else {})
        before_outbox = len(bundle.world.outbox)
        before_writes = len(bundle.world.external_writes)
        started = time.perf_counter()
        if kind == "advance_time":
            return {
                "index": index,
                "kind": kind,
                "duration_ms": 0,
                "advance_minutes": int(step.get("minutes") or 0),
                "note": "timestamps are carried by subsequent event payloads",
            }
        if kind in {"customer_message", "workflow_message"}:
            result = await self._run_customer_message(bundle, step, index=index)
        elif kind in {"sop_event", "platform_task"}:
            result = await self._run_sop_event(bundle, step, index=index, platform_task=kind == "platform_task")
        else:
            raise ValueError(f"unsupported timeline kind: {kind}")
        result["duration_ms"] = round((time.perf_counter() - started) * 1000)
        result["new_outbox"] = deepcopy(bundle.world.outbox[before_outbox:])
        result["new_simulated_writes"] = deepcopy(bundle.world.external_writes[before_writes:])
        return result

    async def _run_customer_message(self, bundle: SimulationBundle, step: dict[str, Any], *, index: int) -> dict[str, Any]:
        content = str(step.get("content") or "")
        msgtype = str(step.get("msgtype") or "text")
        created_at = str(step.get("created_at") or _utc_now())
        bundle.world.append_customer_message(content, msgtype=msgtype, created_at=created_at)
        payload = _workflow_payload(bundle.world, step, index=index)
        request = normalize_workflow_request(payload)
        request = await transcribe_voice_request(request, bundle.voice_transcription_client)
        request.request_context["simulation_mode"] = True
        request.request_context["memory_persist_allowed"] = True
        request.conversation_history = _conversation_history_before_current(bundle.world.conversation)
        background_tasks = BackgroundTasks()
        response = await bundle.chat_runtime.run_platform_reply(request, background_tasks=background_tasks)
        sync_messages = [item.model_dump() for item in response.reply_messages]
        if sync_messages:
            bundle.world.append_assistant_messages(sync_messages, source="workflow_sync")
        await background_tasks()
        run_detail = bundle.repository.get_run(response.request_id)
        return {
            "index": index,
            "kind": "customer_message",
            "input": {"content": content, "msgtype": msgtype, "payload": payload},
            "request_id": response.request_id,
            "sync_reply_messages": sync_messages,
            "response_meta": deepcopy(response.meta),
            "run": run_detail,
        }

    async def _run_sop_event(
        self,
        bundle: SimulationBundle,
        step: dict[str, Any],
        *,
        index: int,
        platform_task: bool,
    ) -> dict[str, Any]:
        payload = deepcopy(step.get("payload") or {})
        event_type = "sop_platform_task" if platform_task else str(payload.get("event_type") or "sop_friend_added_schedule_batch")
        event_id = str(payload.get("event_id") or f"sim_event_{bundle.world.scenario_id}_{index}")
        payload.update(
            {
                "source": "simulation",
                "event_type": event_type,
                "event_id": event_id,
                "created_at": str(step.get("created_at") or payload.get("created_at") or _utc_now()),
            }
        )
        if platform_task and isinstance(payload.get("message_content"), list):
            root_sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}
            root_sop = deepcopy(root_sop)
            root_sop["platform_task"] = {"message_content": deepcopy(payload.pop("message_content"))}
            payload["sop"] = root_sop
        if not payload.get("customers"):
            payload["customers"] = [_event_customer(bundle.world, step)]
        if not payload.get("account"):
            payload["account"] = {
                "wework_user_id": bundle.world.identity["wechat"],
                "assignee_id": str(bundle.world.identity["user_id"]),
                "enterprise_id": bundle.world.identity["corp_id"],
            }
        accepted = await bundle.sop_event_service.accept_event(payload)
        detail = bundle.repository.get_sop_event_detail(event_id)
        return {
            "index": index,
            "kind": "platform_task" if platform_task else "sop_event",
            "input": payload,
            "event_id": event_id,
            "accepted": accepted,
            "event_detail": detail,
        }

    def _build_result(
        self,
        *,
        bundle: SimulationBundle,
        scenario: dict[str, Any],
        attempt: int,
        step_results: list[dict[str, Any]],
        duration_ms: int,
        run_dir: Path,
    ) -> dict[str, Any]:
        hard_errors = _hard_check(
            scenario=scenario,
            step_results=step_results,
            outbox=bundle.world.outbox,
            stores=bundle.world.stores,
            external_writes=bundle.world.external_writes,
        )
        return {
            "scenario_id": scenario["id"],
            "category": scenario.get("category", ""),
            "critical": bool(scenario.get("critical")),
            "attempt": attempt,
            "git_commit": _git_commit(self.repo_root),
            "business_rules_version": _business_rules_version(self.repo_root),
            "models": {
                "planner": bundle.settings.model_planner,
                "reply": bundle.settings.model_reply,
                "fast": bundle.settings.model_fast,
            },
            "duration_ms": duration_ms,
            "run_dir": str(run_dir),
            "steps": step_results,
            "outbox": deepcopy(bundle.world.outbox),
            "simulated_platform_writes": deepcopy(bundle.world.external_writes),
            "tool_calls": deepcopy(bundle.world.tool_calls),
            "hard_errors": hard_errors,
            "hard_pass": not hard_errors,
            "provider_incidents": _provider_incidents(step_results),
            "infrastructure_errors": _unrecovered_infrastructure_errors(step_results),
            "semantic_goal": str(scenario.get("semantic_goal") or ""),
            "expected": deepcopy(scenario.get("expected") or {}),
        }


def _simulation_identity(scenario: dict[str, Any]) -> dict[str, Any]:
    suffix = _safe_name(str(scenario.get("id") or "scenario")).lower()
    return {
        "customer_id": f"sim_customer_{suffix}",
        "external_userid": f"sim_external_{suffix}",
        "corp_id": "sim_corp",
        "wechat": "sim_wechat",
        "user_id": 900001,
    }


def _workflow_payload(world: SimulationWorld, step: dict[str, Any], *, index: int) -> dict[str, Any]:
    content: dict[str, Any] = {
        "content": str(step.get("content") or ""),
        "msgid": f"sim_msg_{world.scenario_id}_{index}",
        "msgtime": str(step.get("msgtime") or int(datetime.now(tz=timezone.utc).timestamp() * 1000)),
        "msgtype": str(step.get("msgtype") or "text"),
    }
    for key in ("location", "location_title", "location_address", "location_zoom"):
        if step.get(key) not in (None, ""):
            content[key] = step[key]
    return {
        "workflow_id": "xiaobei-default",
        "parameters": {
            "category_id": str(step.get("category_id") or "S10N"),
            "content": content,
            "customer_id": world.identity["customer_id"],
            "user_id": world.identity["user_id"],
            "external_userid": world.identity["external_userid"],
            "corp_id": world.identity["corp_id"],
            "wechat": world.identity["wechat"],
            "messages_count": len(world.conversation),
        },
    }


def _event_customer(world: SimulationWorld, step: dict[str, Any]) -> dict[str, Any]:
    delay = int(step.get("delay_minutes") or 30)
    first_added_at = str(step.get("first_added_at") or "").strip()
    if not first_added_at:
        event_at = _parse_iso_datetime(str(step.get("created_at") or ""))
        if event_at is not None:
            first_added_at = (event_at - timedelta(minutes=delay)).isoformat()
        else:
            first_added_at = _utc_now()
    return {
        "conversation": {
            "conversation_id": f"sim_conversation_{world.scenario_id}",
            "sender_id": world.identity["external_userid"],
            "sender_name": "仿真客户",
            "external_userid": world.identity["external_userid"],
            "wework_user_id": world.identity["wechat"],
            "ai_auto_reply": True,
        },
        "customer": {"external_userid": world.identity["external_userid"], "name": "仿真客户"},
        "first_added_event": {
            "trace_id": f"sim_first_add_{world.scenario_id}",
            "device_id": "sim_device",
            "friend_id": world.identity["external_userid"],
            "timestamp": first_added_at,
        },
        "sop": {
            "delay_minutes": delay,
            "day_stage": str(step.get("day_stage") or "day1"),
            "customer_state": str(step.get("customer_state") or "first_add_ai_notice"),
            "stage_tag": str(step.get("stage_tag") or "first_add_ai_notice"),
            "policies": [],
        },
    }


def _conversation_history_before_current(messages: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for item in messages[:-1]:
        role = "用户" if str(item.get("direction") or "") == "customer" else "小贝"
        content = str(item.get("content") or "").strip()
        if content:
            output.append(f"{role}: {content}")
    return output[-20:]


def _hard_check(
    *,
    scenario: dict[str, Any],
    step_results: list[dict[str, Any]],
    outbox: list[dict[str, Any]],
    stores: list[dict[str, Any]],
    external_writes: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    allowed_store_ids = {str(item.get("store_id") or item.get("id") or "") for item in stores if isinstance(item, dict)}
    all_batches: list[list[dict[str, Any]]] = []
    for step in step_results:
        sync = step.get("sync_reply_messages")
        if isinstance(sync, list):
            all_batches.append(sync)
        for item in step.get("new_outbox") or []:
            if isinstance(item, dict) and isinstance(item.get("reply_messages"), list):
                all_batches.append(item["reply_messages"])
    visible_messages = [
        message
        for batch in all_batches
        for message in batch
        if isinstance(message, dict)
    ]
    for batch_index, messages in enumerate(all_batches, start=1):
        if not messages:
            errors.append(f"batch[{batch_index}].empty_reply")
            continue
        payment_count = 0
        for message in messages:
            if not isinstance(message, dict):
                errors.append(f"batch[{batch_index}].invalid_message")
                continue
            message_type = str(message.get("type") or "")
            content = message.get("content") if isinstance(message.get("content"), dict) else {}
            if message_type == "payment_collection":
                payment_count += 1
                amount = int(content.get("amount") or 0)
                if amount not in {10, 20, 30, 40}:
                    errors.append(f"batch[{batch_index}].invalid_payment_amount:{amount}")
            if message_type == "store_address":
                store_id = str(content.get("store_id") or "")
                if not store_id or store_id not in allowed_store_ids:
                    errors.append(f"batch[{batch_index}].store_out_of_scope:{store_id}")
        if payment_count > 1:
            errors.append(f"batch[{batch_index}].multiple_payment_cards:{payment_count}")
    expected = scenario.get("expected") if isinstance(scenario.get("expected"), dict) else {}
    if expected.get("must_reply") and not all_batches:
        errors.append("scenario.no_customer_visible_reply")
    if expected.get("must_reply") is False and visible_messages:
        errors.append("scenario.unexpected_customer_visible_reply")

    visible_types = [str(message.get("type") or "") for message in visible_messages]
    required_types = {str(item) for item in expected.get("required_types") or [] if str(item)}
    forbidden_types = {str(item) for item in expected.get("forbidden_types") or [] if str(item)}
    for message_type in sorted(required_types):
        if message_type not in visible_types:
            errors.append(f"scenario.missing_required_type:{message_type}")
    for message_type in sorted(forbidden_types):
        if message_type in visible_types:
            errors.append(f"scenario.forbidden_type:{message_type}")

    visible_store_ids = {
        str((message.get("content") or {}).get("store_id") or "")
        for message in visible_messages
        if str(message.get("type") or "") == "store_address"
        and isinstance(message.get("content"), dict)
    }
    required_store_ids = {str(item) for item in expected.get("required_store_ids") or [] if str(item)}
    required_any_store_ids = {str(item) for item in expected.get("required_any_store_ids") or [] if str(item)}
    missing_store_ids = required_store_ids - visible_store_ids
    if missing_store_ids:
        errors.append(f"scenario.missing_required_store_ids:{','.join(sorted(missing_store_ids))}")
    if required_any_store_ids and not visible_store_ids.intersection(required_any_store_ids):
        errors.append(f"scenario.missing_any_required_store_id:{','.join(sorted(required_any_store_ids))}")

    expected_amount = expected.get("payment_amount")
    if expected_amount not in (None, ""):
        payment_amounts = [
            int((message.get("content") or {}).get("amount") or 0)
            for message in visible_messages
            if str(message.get("type") or "") == "payment_collection"
            and isinstance(message.get("content"), dict)
        ]
        if int(expected_amount) not in payment_amounts:
            errors.append(f"scenario.missing_payment_amount:{int(expected_amount)}")

    visible_text = "\n".join(_message_text(message) for message in visible_messages)
    if "我在，继续帮您处理" in visible_text or visible_text.strip() == "亲，刚才这条我没接完整，麻烦您再发一下。":
        errors.append("scenario.neutral_fallback_used")
    if _contains_customer_visible_route_value(visible_text):
        errors.append("scenario.customer_visible_distance_value")
    for phrase in expected.get("forbidden_phrases") or []:
        normalized = str(phrase or "").strip()
        if normalized and normalized in visible_text:
            errors.append(f"scenario.forbidden_phrase:{normalized}")
    if any(str(item.get("transport") or "") != "simulation_only" for item in external_writes):
        errors.append("external_write_transport_detected")
    return sorted(set(errors))


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "")
    return str(content or "")


def _contains_customer_visible_route_value(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    numeric_value = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百半几]+)"
    if re.search(rf"{numeric_value}(?:公里|千米|km)", compact, flags=re.IGNORECASE):
        return True
    route_terms = "车程|步行|打车|开车|公交|地铁|骑车|过去|过来|到店|路程|导航|路上|交通"
    if re.search(rf"(?:{route_terms})[^，。！？；,.!?;]{{0,12}}{numeric_value}(?:-|到)?{numeric_value}?分钟", compact):
        return True
    return bool(
        re.search(
            rf"{numeric_value}(?:-|到)?{numeric_value}?分钟[^，。！？；,.!?;]{{0,8}}(?:{route_terms}|到)",
            compact,
        )
    )


def _provider_incidents(steps: list[dict[str, Any]]) -> list[str]:
    incidents: list[str] = []
    markers = (
        "timeout",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "connecterror",
        "readerror",
        "jsondecodeerror",
        "malformed json",
        "contain the word 'json'",
    )
    for step in steps:
        raw = "\n".join(_error_evidence(step)).lower()
        if any(marker in raw for marker in markers):
            incidents.append(f"step[{step.get('index')}].provider_retry_or_network_incident")
    return sorted(set(incidents))


def _error_evidence(value: Any) -> list[str]:
    evidence: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in {
                "error",
                "errors",
                "fallback_errors",
                "request_retry_errors",
                "json_response_format_strict_error",
            }:
                if isinstance(item, list):
                    evidence.extend(str(part) for part in item if str(part))
                elif str(item or ""):
                    evidence.append(str(item))
            else:
                evidence.extend(_error_evidence(item))
    elif isinstance(value, list):
        for item in value:
            evidence.extend(_error_evidence(item))
    return evidence


def _unrecovered_infrastructure_errors(steps: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for step in steps:
        if step.get("runner_error"):
            errors.append(f"step[{step.get('index')}].runner_error")
            continue
        if step.get("kind") == "customer_message":
            has_sync = bool(step.get("sync_reply_messages"))
            has_outbox = bool(step.get("new_outbox"))
            if not has_sync and not has_outbox:
                errors.append(f"step[{step.get('index')}].no_recovered_reply")
    return sorted(set(errors))


def _sales_contact_key(identity: dict[str, Any]) -> str:
    return "|".join(
        [
            str(identity.get("corp_id") or "").lower(),
            str(identity.get("wechat") or "").lower(),
            str(identity.get("external_userid") or identity.get("customer_id") or "").lower(),
        ]
    )


def _safe_name(value: str) -> str:
    output = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
    return output[:96] or "scenario"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _git_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()
    except Exception:
        return ""


def _business_rules_version(repo_root: Path) -> str:
    path = repo_root / "ai_paths" / "app" / "policies" / "business_rules.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(payload.get("version") or payload.get("updated_at") or "")
