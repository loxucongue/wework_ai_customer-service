from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.services.storage.serialization import utc_now_iso


logger = logging.getLogger(__name__)


SOP_PLATFORM_TASK_SYSTEM_PROMPT = """
你是第三方 SOP 待发送任务的发送审核节点。第三方平台已经决定触达策略、时间、频率和候选内容；你只负责结合最新客户上下文判断当前任务现在发送还是不发送。

边界：
1. scene 和 message_content 是本次任务目标的最高权重输入，但不得覆盖已付、已预约、删除好友、投诉退款、健康风险、明确停止联系、人工接管等实时事实。
2. decision 只能是 send 或 no_send。禁止 defer、reschedule、retry_later，禁止输出 scheduled_at、delay_minutes 或创建后续任务。
3. no_send 表示当前任务处理完成，reply_messages 必须为空。
4. send 时 reply_messages 必须非空。useAiCopy=false 时必须原样保留平台消息；useAiCopy=true 时只能自然改写 text，使其承接最新聊天，不能改变价格、项目、门店、退款、支付等事实。若 AI 话术任务的 message_content 为空，只能基于 scene 中明确提供的场景说明、知识文本或生成说明写 1–2 条 text。
5. image、video、link 的 URL、内容和顺序不可修改，不得新增平台未提供的素材。不得生成 payment_collection。
6. 客户有尚未回答的新问题、当前正在忙且刚被承接、已经收到相同内容、关系已终止或任务内容与实时状态冲突时，选择 no_send。
7. 不要因为普通沉默就自动 no_send；内容与当前状态一致且仍有价值时可以 send。
8. reply_messages 使用统一结构：{"type":"text","order":1,"content":{"text":"客户可见内容"}}。

只返回小写 json 对象，字段严格为：
{
  "decision": "send | no_send",
  "reason": "发送或不发送的依据",
  "reply_messages": []
}
""".strip()


class SopPlatformTaskService:
    RECOVERY_STATUSES = [
        "platform_claiming",
        "platform_processing",
        "platform_processing_retry",
        "platform_send_uncertain",
        "platform_complete_pending",
    ]

    def __init__(
        self,
        *,
        settings: Any,
        repository: Any,
        platform_client: Any,
        system_client: Any,
        model_client: Any,
        customer_context_service: Any,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.platform_client = platform_client
        self.system_client = system_client
        self.model_client = model_client
        self.customer_context_service = customer_context_service
        self._locks: dict[str, asyncio.Lock] = {}
        queue_size = max(1, int(getattr(settings, "sop_platform_queue_size", 24) or 24))
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self._queued_ids: set[str] = set()
        self._in_flight_ids: set[str] = set()
        self._terminal_ids: set[str] = set()
        self._terminal_order: deque[str] = deque()
        self._workers: list[asyncio.Task[None]] = []
        self._recovery_worker: asyncio.Task[None] | None = None
        self._running = False
        self._counters: Counter[str] = Counter()
        self._timings: dict[str, deque[float]] = {
            name: deque(maxlen=500)
            for name in ("pull", "claim", "context", "model", "send", "task", "queue_lag")
        }
        self._last_poll_at = ""
        self._last_poll_error = ""
        self._pending_total = 0
        self._oldest_due_lag_seconds = 0.0

    async def run(self) -> None:
        if self._running:
            raise RuntimeError("third-party SOP worker is already running")
        self._running = True
        concurrency = max(1, int(getattr(self.settings, "sop_platform_task_concurrency", 6) or 6))
        self._workers = [
            asyncio.create_task(self._queue_worker(index), name=f"sop-platform-worker-{index}")
            for index in range(concurrency)
        ]
        self._recovery_worker = asyncio.create_task(
            self._recovery_loop(),
            name="sop-platform-recovery",
        )
        try:
            while True:
                result = await self.poll_once()
                if result.get("pending_count") or result.get("error_count"):
                    logger.info("Third-party SOP worker result: %s", result)
                await asyncio.sleep(max(0.2, float(self.settings.sop_platform_poll_seconds)))
        finally:
            self._running = False
            tasks = [*self._workers]
            if self._recovery_worker is not None:
                tasks.append(self._recovery_worker)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._workers = []
            self._recovery_worker = None

    async def poll_once(self) -> dict[str, int]:
        free_slots = max(0, self._queue.maxsize - self._queue.qsize())
        if free_slots <= 0:
            return {
                "pending_count": self._pending_total,
                "enqueued_count": 0,
                "queue_depth": self._queue.qsize(),
                "in_flight_count": len(self._in_flight_ids),
                "error_count": 0,
            }
        limit = min(
            free_slots,
            max(1, min(int(self.settings.sop_platform_batch_size), 500)),
        )
        started = time.perf_counter()
        self._last_poll_at = utc_now_iso()
        try:
            page = await self.platform_client.pending(limit=limit)
            self._last_poll_error = ""
        except Exception as exc:
            self._last_poll_error = f"{type(exc).__name__}: {exc}"
            self._counters["poll_error"] += 1
            raise
        finally:
            self._observe("pull", time.perf_counter() - started)
        if isinstance(page, list):
            page = {"items": page, "total": len(page)}
        tasks = page.get("items") if isinstance(page.get("items"), list) else []
        tasks = sorted(
            (dict(item) for item in tasks if isinstance(item, dict)),
            key=lambda item: (_task_scheduled_epoch(item) or float("inf"), _task_id(item)),
        )
        self._pending_total = max(len(tasks), int(page.get("total") or 0))
        now_epoch = time.time()
        lags = [max(0.0, now_epoch - value) for value in map(_task_scheduled_epoch, tasks) if value]
        self._oldest_due_lag_seconds = max(lags, default=0.0)
        if self._oldest_due_lag_seconds > 120:
            logger.warning(
                "Third-party SOP queue lag is %.1fs (pending=%s)",
                self._oldest_due_lag_seconds,
                self._pending_total,
            )
        enqueued = 0
        pulled_at = utc_now_iso()
        for task in tasks:
            task_id = _task_id(task)
            if (
                not task_id
                or task_id in self._queued_ids
                or task_id in self._in_flight_ids
                or task_id in self._terminal_ids
            ):
                self._counters["duplicate_poll"] += 1
                continue
            if self._queue.full():
                break
            task["_aics_pulled_at"] = pulled_at
            self._queued_ids.add(task_id)
            self._queue.put_nowait(task)
            enqueued += 1
        self._counters["fetched"] += len(tasks)
        self._counters["enqueued"] += enqueued
        return {
            "pending_count": self._pending_total,
            "enqueued_count": enqueued,
            "queue_depth": self._queue.qsize(),
            "in_flight_count": len(self._in_flight_ids),
            "terminal_dedupe_count": len(self._terminal_ids),
            "error_count": 0,
        }

    async def _queue_worker(self, _index: int) -> None:
        while True:
            platform_task = await self._queue.get()
            task_id = _task_id(platform_task)
            self._queued_ids.discard(task_id)
            self._in_flight_ids.add(task_id)
            started = time.perf_counter()
            scheduled = _task_scheduled_epoch(platform_task)
            if scheduled:
                self._observe("queue_lag", max(0.0, time.time() - scheduled))
            try:
                result = await self.process_task(platform_task)
                self._record_result(result)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._counters["retry"] += 1
                logger.exception("Third-party SOP task failed and remains recoverable: %s", task_id)
            finally:
                self._observe("task", time.perf_counter() - started)
                self._in_flight_ids.discard(task_id)
                self._queue.task_done()

    async def _recovery_loop(self) -> None:
        while True:
            try:
                await self.process_recoveries()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._counters["recovery_error"] += 1
                logger.exception("Third-party SOP recovery iteration failed")
            await asyncio.sleep(max(1.0, float(self.settings.sop_platform_poll_seconds)))

    async def process_recoveries(self) -> int:
        events = self.repository.list_sop_events_by_statuses(
            self.RECOVERY_STATUSES,
            limit=self.settings.sop_platform_recovery_batch_size,
            event_type="platform_sop_task",
        )
        concurrency = max(1, int(getattr(self.settings, "sop_platform_recovery_concurrency", 2) or 2))
        semaphore = asyncio.Semaphore(concurrency)

        async def recover(event: dict[str, Any]) -> int:
            payload = event.get("raw_payload") if isinstance(event.get("raw_payload"), dict) else {}
            task = payload.get("platform_task") if isinstance(payload.get("platform_task"), dict) else {}
            if not task:
                self.repository.update_sop_event_status(
                    str(event.get("event_id") or ""),
                    status="platform_failed",
                    error="missing_platform_task_payload",
                )
                return 0
            task_id = _task_id(task)
            if task_id in self._queued_ids or task_id in self._in_flight_ids:
                return 0
            async with semaphore:
                try:
                    result = await self.process_task(task, recovery_status=str(event.get("status") or ""))
                    self._record_result(result)
                    return 1 if result.get("processed") else 0
                except Exception:
                    return 0

        if not events:
            return 0
        return sum(await asyncio.gather(*(recover(event) for event in events)))

    async def process_task(self, platform_task: dict[str, Any], *, recovery_status: str = "") -> dict[str, Any]:
        task_id = _task_id(platform_task)
        if not task_id:
            raise ValueError("platform task_id is required")
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            return await self._process_locked(platform_task, task_id=task_id, recovery_status=recovery_status)

    def runtime_status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "queue_depth": self._queue.qsize(),
            "queue_capacity": self._queue.maxsize,
            "queued_count": len(self._queued_ids),
            "in_flight_count": len(self._in_flight_ids),
            "pending_total": self._pending_total,
            "oldest_due_lag_seconds": round(self._oldest_due_lag_seconds, 3),
            "last_poll_at": self._last_poll_at,
            "last_poll_error": self._last_poll_error,
            "counters": dict(self._counters),
            "timings_ms": {name: _timing_summary(values) for name, values in self._timings.items()},
        }

    def _observe(self, name: str, elapsed_seconds: float) -> None:
        values = self._timings.get(name)
        if values is not None:
            values.append(max(0.0, float(elapsed_seconds)) * 1000)

    def _record_result(self, result: dict[str, Any]) -> None:
        status = str(result.get("status") or "unknown")
        if status in {"sent", "completed_without_send", "platform_completed", "shadow_send", "shadow_no_send"}:
            self._remember_terminal(str(result.get("task_id") or ""))
        if status == "sent":
            self._counters["sent"] += 1
        elif status in {"completed_without_send", "shadow_no_send"}:
            self._counters["no_send"] += 1
        elif status == "shadow_send":
            self._counters["shadow_send"] += 1
        elif status == "platform_send_uncertain":
            self._counters["send_uncertain"] += 1
            logger.error("Third-party SOP send result is uncertain: %s", result.get("task_id"))

    def _remember_terminal(self, task_id: str) -> None:
        if not task_id or task_id in self._terminal_ids:
            return
        self._terminal_ids.add(task_id)
        self._terminal_order.append(task_id)
        while len(self._terminal_order) > 50_000:
            self._terminal_ids.discard(self._terminal_order.popleft())

    async def _process_locked(
        self,
        platform_task: dict[str, Any],
        *,
        task_id: str,
        recovery_status: str,
    ) -> dict[str, Any]:
        event_id = f"platform_sop_task:{task_id}"
        event_payload = {
            "event_id": event_id,
            "event_type": "platform_sop_task",
            "source": "third_party_sop_pending",
            "request_reply": False,
            "created_at": str(platform_task.get("scheduledAt") or platform_task.get("scheduled_at") or ""),
            "platform_task": platform_task,
        }
        event = self.repository.create_sop_event(event_payload)
        current_status = str(event.get("status") or "")
        if current_status == "platform_completed":
            return {"processed": False, "status": current_status, "task_id": task_id}
        identity = _task_identity(platform_task)
        local_task = self.repository.create_sop_send_task(
            event_id=event_id,
            idempotency_key=f"platform-sop:{task_id}",
            customer_id=identity["customer_id"],
            external_userid=identity["external_userid"],
            corp_id=identity["corp_id"],
            user_id=identity["user_id"],
            wechat=identity["wechat"],
            sop_pack_id=f"platform-sop-{task_id}",
            sop_pack_name=str(platform_task.get("ruleName") or platform_task.get("sceneName") or "第三方SOP任务"),
            sop_category="platform_task",
            trigger_source="third_party_sop_pending",
            reply_messages=_platform_messages(platform_task),
            status="platform_received",
        )
        local_status = str(local_task.get("status") or "")
        if self.settings.sop_platform_shadow_mode and local_status in {"shadow_send", "shadow_no_send"}:
            return {"processed": False, "status": local_status, "task_id": task_id}

        if not self.settings.sop_platform_shadow_mode and recovery_status == "platform_complete_pending":
            started = time.perf_counter()
            completed = await self.platform_client.consume(task_id=task_id, status=30)
            self._observe("claim", time.perf_counter() - started)
            _require_platform_status(completed, 30)
            self.repository.update_sop_event_status(event_id, status="platform_completed")
            return {
                "processed": True,
                "status": local_status or "completed",
                "task_id": task_id,
                "platform_response": completed,
            }

        if not self.settings.sop_platform_shadow_mode and recovery_status == "platform_send_uncertain":
            stored_payload = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
            send_payload = stored_payload.get("request") if isinstance(stored_payload.get("request"), dict) else {}
            if not send_payload:
                raise RuntimeError("uncertain send recovery is missing the original idempotent request")
            started = time.perf_counter()
            send_result = await self.system_client.send(**send_payload)
            self._observe("send", time.perf_counter() - started)
            send_status = str((send_result.get("data") or {}).get("send_status") or send_result.get("msg") or "")
            if send_status == "accepted_no_response":
                raise RuntimeError("active_send_timeout_unknown_result")
            self.repository.update_sop_send_task(
                str(local_task.get("id") or ""),
                status="sent",
                send_payload=stored_payload,
                send_response=send_result,
                sent_at=utc_now_iso(),
            )
            self.repository.update_sop_event_status(event_id, status="platform_complete_pending")
            completed = await self.platform_client.consume(task_id=task_id, status=30)
            _require_platform_status(completed, 30)
            self.repository.update_sop_event_status(event_id, status="platform_completed")
            return {"processed": True, "status": "sent", "task_id": task_id, "platform_response": completed}

        preflight_reason = _task_preflight_no_send_reason(
            platform_task,
            identity=identity,
            settings=self.settings,
        )
        if self.settings.sop_platform_shadow_mode and preflight_reason:
            decision = {"decision": "no_send", "reason": preflight_reason, "reply_messages": []}
            self.repository.update_sop_send_task(
                str(local_task.get("id") or ""),
                status="shadow_no_send",
                send_payload={"decision": decision, "context": {"source": "preflight"}},
            )
            self.repository.update_sop_event_status(event_id, status="shadow_no_send")
            self._counters[preflight_reason] += 1
            return {"processed": True, "status": "shadow_no_send", "task_id": task_id, "decision": decision}

        claimed = recovery_status in {
            "platform_processing",
            "platform_processing_retry",
            "platform_send_uncertain",
            "platform_complete_pending",
        }
        if not self.settings.sop_platform_shadow_mode and not claimed:
            self.repository.update_sop_event_status(event_id, status="platform_claiming")
            started = time.perf_counter()
            claim_response = await self.platform_client.consume(task_id=task_id, status=20)
            self._observe("claim", time.perf_counter() - started)
            _require_platform_status(claim_response, 20)
            self.repository.update_sop_event_status(event_id, status="platform_processing")

        try:
            if preflight_reason:
                context = {"source": "preflight", "task_timing": _task_timing(platform_task)}
                decision = {"decision": "no_send", "reason": preflight_reason, "reply_messages": []}
                self._counters[preflight_reason] += 1
            else:
                started = time.perf_counter()
                context = await self._load_context(platform_task, identity=identity)
                self._observe("context", time.perf_counter() - started)
                started = time.perf_counter()
                decision = await self._decide(platform_task, context=context)
                self._observe("model", time.perf_counter() - started)
            if self.settings.sop_platform_shadow_mode:
                status = f"shadow_{decision['decision']}"
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status=status,
                    send_payload={"decision": decision, "context": _context_audit(context)},
                )
                self.repository.update_sop_event_status(event_id, status=status)
                return {"processed": True, "status": status, "task_id": task_id, "decision": decision}

            if decision["decision"] == "no_send":
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status="completed_without_send",
                    send_payload={"decision": decision, "context": _context_audit(context)},
                )
            else:
                send_payload = {
                    **identity,
                    "plan_id": f"platform-sop-{task_id}",
                    "task_id": f"platform-sop-send-{task_id}",
                    "reply_messages": decision["reply_messages"],
                }
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status="sending",
                    send_payload={"decision": decision, "request": send_payload, "context": _context_audit(context)},
                )
                started = time.perf_counter()
                send_result = await self.system_client.send(**send_payload)
                self._observe("send", time.perf_counter() - started)
                send_status = str((send_result.get("data") or {}).get("send_status") or send_result.get("msg") or "")
                if send_status == "accepted_no_response":
                    self.repository.update_sop_event_status(
                        event_id,
                        status="platform_send_uncertain",
                        error="active_send_timeout_unknown_result",
                    )
                    raise RuntimeError("active_send_timeout_unknown_result")
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status="sent",
                    send_payload={"decision": decision, "request": send_payload, "context": _context_audit(context)},
                    send_response=send_result,
                    sent_at=utc_now_iso(),
                )
            self.repository.update_sop_event_status(event_id, status="platform_complete_pending")
            completed = await self.platform_client.consume(task_id=task_id, status=30)
            _require_platform_status(completed, 30)
            self.repository.update_sop_event_status(event_id, status="platform_completed")
            return {
                "processed": True,
                "status": "sent" if decision["decision"] == "send" else "completed_without_send",
                "task_id": task_id,
                "platform_response": completed,
            }
        except Exception as exc:
            event_after_error = self.repository.get_sop_event(event_id)
            event_status = str(event_after_error.get("status") or "")
            if event_status not in {"platform_send_uncertain", "platform_complete_pending"}:
                self.repository.update_sop_event_status(
                    event_id,
                    status="platform_processing_retry",
                    error=f"{type(exc).__name__}: {exc}",
                )
            if event_status not in {"platform_send_uncertain", "platform_complete_pending"}:
                self.repository.update_sop_send_task(
                    str(local_task.get("id") or ""),
                    status="processing_retry",
                    send_payload={"platform_task_id": task_id},
                    error=f"{type(exc).__name__}: {exc}",
                )
            raise

    async def _load_context(self, platform_task: dict[str, Any], *, identity: dict[str, str]) -> dict[str, Any]:
        missing = [key for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat") if not identity[key]]
        if missing:
            raise RuntimeError(f"platform task missing identity: {','.join(missing)}")
        conversation = await self.system_client.conversation(**identity, limit=50)
        data = conversation.get("data") if isinstance(conversation.get("data"), dict) else conversation
        relation = data.get("customer_relation") if isinstance(data.get("customer_relation"), dict) else {}
        messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        if relation.get("is_deleted") is True or str(relation.get("status") or "").lower() == "deleted":
            return {
                "customer_relation": relation,
                "recent_conversation": messages[-50:],
                "conversation_count": len(messages),
                "customer_context": {"source": "skipped_customer_deleted"},
                "task_timing": _task_timing(platform_task),
            }
        request_context = {
            "source_protocol": "third_party_sop_pending",
            "corp_id": identity["corp_id"],
            "customer_id": identity["customer_id"],
            "external_userid": identity["external_userid"],
            "user_id": identity["user_id"],
            "wechat": identity["wechat"],
            "order_id": platform_task.get("orderId") or platform_task.get("order_id"),
            "order_no": platform_task.get("orderNo") or platform_task.get("order_no"),
        }
        customer_context = await asyncio.to_thread(
            self.customer_context_service.load,
            customer_id=identity["customer_id"],
            memory={},
            request_context=request_context,
        )
        return {
            "customer_relation": relation,
            "recent_conversation": messages[-50:],
            "conversation_count": len(messages),
            "customer_context": customer_context,
            "task_timing": _task_timing(platform_task),
        }

    async def _decide(self, platform_task: dict[str, Any], *, context: dict[str, Any]) -> dict[str, Any]:
        relation = context.get("customer_relation") if isinstance(context.get("customer_relation"), dict) else {}
        if relation.get("is_deleted") is True or str(relation.get("status") or "").lower() == "deleted":
            return {"decision": "no_send", "reason": "customer_relation_deleted", "reply_messages": []}
        original_messages = _platform_messages(platform_task)
        use_ai_copy = _bool(platform_task.get("useAiCopy", platform_task.get("use_ai_copy")))
        if not original_messages and not use_ai_copy:
            raise RuntimeError("platform task message_content is empty or unsupported")
        if not original_messages and not _has_trusted_ai_copy_source(platform_task):
            return {"decision": "no_send", "reason": "missing_trusted_platform_content", "reply_messages": []}
        model_input = {
            "task": {
                "task_id": _task_id(platform_task),
                "scene": platform_task.get("scene") if isinstance(platform_task.get("scene"), dict) else {},
                "trigger_event": platform_task.get("triggerEvent") or platform_task.get("trigger_event"),
                "use_ai_copy": use_ai_copy,
                "message_content": original_messages,
                "timing": _task_timing(platform_task),
            },
            "latest_context": context,
            "output_contract": {
                "decision": "send | no_send",
                "reason": "string",
                "reply_messages": "send must be non-empty; no_send must be []",
            },
        }
        messages = [
            {"role": "system", "content": SOP_PLATFORM_TASK_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(model_input, ensure_ascii=False)},
        ]
        deadline = time.monotonic() + max(5.0, float(self.settings.sop_platform_model_timeout_seconds))
        raw = await self.model_client.chat_json(
            messages,
            tier="balanced",
            temperature=0.0,
            deadline_monotonic=deadline,
            max_parallel_candidates=1,
        )
        error = _decision_error(raw, original_messages=original_messages, use_ai_copy=model_input["task"]["use_ai_copy"])
        if error:
            repair_messages = [
                *messages,
                {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                {
                    "role": "user",
                    "content": (
                        f"输出不合法：{error}。只返回规定的 json；只能 send/no_send，不得延时或新增任务。"
                    ),
                },
            ]
            raw = await self.model_client.chat_json(
                repair_messages,
                tier="balanced",
                temperature=0.0,
                deadline_monotonic=deadline,
                max_parallel_candidates=1,
            )
            error = _decision_error(raw, original_messages=original_messages, use_ai_copy=model_input["task"]["use_ai_copy"])
        if error:
            raise RuntimeError(f"invalid_sop_platform_model_output: {error}")
        decision = str(raw.get("decision") or "")
        if decision == "no_send":
            return {"decision": decision, "reason": str(raw.get("reason") or ""), "reply_messages": []}
        output_messages = raw.get("reply_messages") if isinstance(raw.get("reply_messages"), list) else []
        if not model_input["task"]["use_ai_copy"]:
            output_messages = original_messages
        return {
            "decision": decision,
            "reason": str(raw.get("reason") or ""),
            "reply_messages": output_messages,
        }


def _decision_error(raw: Any, *, original_messages: list[dict[str, Any]], use_ai_copy: bool) -> str:
    if not isinstance(raw, dict):
        return "output must be an object"
    unexpected = set(raw).difference({"decision", "reason", "reply_messages"})
    if unexpected:
        return f"unexpected output fields: {','.join(sorted(unexpected))}"
    decision = str(raw.get("decision") or "").strip()
    if decision not in {"send", "no_send"}:
        return "decision must be send or no_send"
    messages = raw.get("reply_messages")
    if not isinstance(messages, list):
        return "reply_messages must be a list"
    if decision == "no_send":
        return "no_send reply_messages must be empty" if messages else ""
    if not messages:
        return "send reply_messages must not be empty"
    if not use_ai_copy:
        return ""
    if not original_messages:
        if len(messages) > 2:
            return "AI copy without message_content may contain at most two text messages"
        for index, candidate in enumerate(messages, start=1):
            if not isinstance(candidate, dict) or candidate.get("type") != "text":
                return f"generated reply message {index} must be text"
            if candidate.get("order") != index:
                return f"generated reply message {index} order must be {index}"
            content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
            if not str(content.get("text") or "").strip():
                return f"generated reply message {index} text is empty"
        return ""
    if len(messages) != len(original_messages):
        return "AI copy may not add or remove platform messages"
    for index, (candidate, original) in enumerate(zip(messages, original_messages), start=1):
        if not isinstance(candidate, dict):
            return f"reply message {index} must be an object"
        if candidate.get("type") != original.get("type") or candidate.get("order") != original.get("order"):
            return f"reply message {index} type/order must remain unchanged"
        message_type = str(original.get("type") or "")
        if message_type == "text":
            content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
            if not str(content.get("text") or "").strip():
                return f"reply message {index} text is empty"
        elif candidate != original:
            return f"reply message {index} media/link content must remain unchanged"
    return ""


def _platform_messages(platform_task: dict[str, Any]) -> list[dict[str, Any]]:
    raw = platform_task.get("message_content")
    if not isinstance(raw, list):
        raw = platform_task.get("messageContent")
    if not isinstance(raw, list):
        return []
    output: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        message_type = str(item.get("type") or "").strip().lower()
        content = item.get("content")
        if message_type == "text":
            text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
            if text:
                output.append({"type": "text", "order": index, "content": {"text": text}})
        elif message_type in {"image", "video"}:
            url = str(content.get("url") if isinstance(content, dict) else content or "").strip()
            if url:
                output.append({"type": message_type, "order": index, "content": {"url": url}})
        elif message_type == "link":
            if isinstance(content, dict):
                normalized_content = dict(content)
            else:
                normalized_content = {"url": str(content or "").strip()}
            if str(normalized_content.get("url") or "").strip():
                output.append({"type": "link", "order": index, "content": normalized_content})
    return output


def _task_preflight_no_send_reason(
    platform_task: dict[str, Any],
    *,
    identity: dict[str, str],
    settings: Any,
) -> str:
    missing = [key for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat") if not identity[key]]
    if missing:
        return "invalid_identity"
    payload_error = _platform_message_error(platform_task)
    if payload_error:
        return "invalid_message_content"
    messages = _platform_messages(platform_task)
    use_ai_copy = _bool(platform_task.get("useAiCopy", platform_task.get("use_ai_copy")))
    if not messages and not use_ai_copy:
        return "invalid_message_content"
    if not messages and not _has_trusted_ai_copy_source(platform_task):
        return "missing_trusted_platform_content"
    scheduled = _task_scheduled_epoch(platform_task)
    max_age = max(0, int(getattr(settings, "sop_platform_max_task_age_seconds", 21600) or 0))
    if scheduled and max_age and time.time() - scheduled > max_age:
        return "stale_task"
    live_not_before = _parse_epoch(getattr(settings, "sop_platform_live_not_before", ""))
    if live_not_before and (not scheduled or scheduled < live_not_before):
        return "pre_cutover_task"
    return ""


def _platform_message_error(platform_task: dict[str, Any]) -> str:
    raw = platform_task.get("message_content")
    if not isinstance(raw, list):
        raw = platform_task.get("messageContent")
    if raw is None:
        return ""
    if not isinstance(raw, list):
        return "message_content_not_list"
    for item in raw:
        if not isinstance(item, dict):
            return "message_not_object"
        message_type = str(item.get("type") or "").strip().lower()
        if message_type not in {"text", "image", "video", "link"}:
            return "unsupported_message_type"
        content = item.get("content")
        if message_type == "text":
            text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
            if not text:
                return "empty_text"
            continue
        if message_type == "link" and isinstance(content, dict):
            url = str(content.get("url") or "").strip()
        else:
            url = str(content.get("url") if isinstance(content, dict) else content or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "invalid_media_url"
    return ""


def _task_identity(task: dict[str, Any]) -> dict[str, str]:
    external = str(
        task.get("customer_wechat_id")
        or task.get("customerWechatId")
        or task.get("external_userid")
        or task.get("customerWechat")
        or ""
    ).strip()
    return {
        "corp_id": str(task.get("corp_id") or task.get("corpId") or task.get("wecomCorpId") or "").strip(),
        "customer_id": str(task.get("customerId") or task.get("customer_id") or external).strip(),
        "external_userid": external,
        "user_id": str(task.get("user_wechat_id") or task.get("userWechatId") or task.get("user_id") or "").strip(),
        "wechat": str(task.get("user_wechat") or task.get("userWechat") or task.get("wechat") or "").strip(),
    }


def _task_id(task: dict[str, Any]) -> str:
    return str(task.get("task_id") or task.get("taskId") or task.get("id") or "").strip()


def _task_scheduled_epoch(task: dict[str, Any]) -> float:
    return _parse_epoch(
        task.get("scheduledAt")
        or task.get("scheduled_at")
        or task.get("executeTime")
        or task.get("execute_time")
    )


def _parse_epoch(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000 if number > 10_000_000_000 else number
    text = str(value).strip()
    try:
        number = float(text)
        return number / 1000 if number > 10_000_000_000 else number
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _task_timing(task: dict[str, Any]) -> dict[str, Any]:
    scheduled = _task_scheduled_epoch(task)
    return {
        "scheduled_at": task.get("scheduledAt") or task.get("scheduled_at") or "",
        "pulled_at": task.get("_aics_pulled_at") or "",
        "lateness_seconds": round(max(0.0, time.time() - scheduled), 3) if scheduled else None,
    }


def _timing_summary(values: deque[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "avg": 0.0, "p50": 0.0, "p90": 0.0, "max": 0.0}
    ordered = sorted(values)
    count = len(ordered)

    def percentile(ratio: float) -> float:
        return ordered[min(count - 1, max(0, int((count - 1) * ratio)))]

    return {
        "count": count,
        "avg": round(sum(ordered) / count, 3),
        "p50": round(percentile(0.5), 3),
        "p90": round(percentile(0.9), 3),
        "max": round(ordered[-1], 3),
    }


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _has_trusted_ai_copy_source(task: dict[str, Any]) -> bool:
    scene = task.get("scene") if isinstance(task.get("scene"), dict) else {}
    engine = scene.get("engine") if isinstance(scene.get("engine"), dict) else {}
    return any(
        str(value or "").strip()
        for value in (
            scene.get("sceneDesc"),
            scene.get("knowledgeText"),
            engine.get("generateNote"),
        )
    )


def _require_platform_status(response: dict[str, Any], expected: int) -> None:
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    try:
        actual = int(data.get("status"))
    except (TypeError, ValueError):
        raise RuntimeError("platform consume response is missing status") from None
    if actual != expected:
        raise RuntimeError(f"platform consume status mismatch: expected {expected}, got {actual}")


def _context_audit(context: dict[str, Any]) -> dict[str, Any]:
    relation = context.get("customer_relation") if isinstance(context.get("customer_relation"), dict) else {}
    customer_context = context.get("customer_context") if isinstance(context.get("customer_context"), dict) else {}
    return {
        "conversation_count": int(context.get("conversation_count") or 0),
        "customer_relation": relation,
        "customer_context_source": customer_context.get("source"),
        "customer_context_error": customer_context.get("error"),
    }
