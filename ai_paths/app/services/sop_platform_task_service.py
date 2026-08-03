from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.services.storage.serialization import utc_now_iso


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

    async def poll_once(self) -> dict[str, int]:
        recovered = await self.process_recoveries()
        tasks = await self.platform_client.pending()
        processed = 0
        errors = 0
        for task in tasks:
            try:
                result = await self.process_task(task)
                if result.get("processed"):
                    processed += 1
            except Exception:
                errors += 1
        return {
            "pending_count": len(tasks),
            "processed_count": processed,
            "recovered_count": recovered,
            "error_count": errors,
        }

    async def process_recoveries(self) -> int:
        events = self.repository.list_sop_events_by_statuses(
            self.RECOVERY_STATUSES,
            limit=self.settings.sop_platform_recovery_batch_size,
            event_type="platform_sop_task",
        )
        processed = 0
        for event in events:
            payload = event.get("raw_payload") if isinstance(event.get("raw_payload"), dict) else {}
            task = payload.get("platform_task") if isinstance(payload.get("platform_task"), dict) else {}
            if not task:
                self.repository.update_sop_event_status(
                    str(event.get("event_id") or ""),
                    status="platform_failed",
                    error="missing_platform_task_payload",
                )
                continue
            try:
                result = await self.process_task(task, recovery_status=str(event.get("status") or ""))
                if result.get("processed"):
                    processed += 1
            except Exception:
                continue
        return processed

    async def process_task(self, platform_task: dict[str, Any], *, recovery_status: str = "") -> dict[str, Any]:
        task_id = _task_id(platform_task)
        if not task_id:
            raise ValueError("platform task_id is required")
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            return await self._process_locked(platform_task, task_id=task_id, recovery_status=recovery_status)

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
            completed = await self.platform_client.consume(task_id=task_id, status=30)
            _require_platform_status(completed, 30)
            self.repository.update_sop_event_status(event_id, status="platform_completed")
            return {
                "processed": True,
                "status": local_status or "completed",
                "task_id": task_id,
                "platform_response": completed,
            }

        claimed = recovery_status in {
            "platform_processing",
            "platform_processing_retry",
            "platform_send_uncertain",
            "platform_complete_pending",
        }
        if not self.settings.sop_platform_shadow_mode and not claimed:
            self.repository.update_sop_event_status(event_id, status="platform_claiming")
            claim_response = await self.platform_client.consume(task_id=task_id, status=20)
            _require_platform_status(claim_response, 20)
            self.repository.update_sop_event_status(event_id, status="platform_processing")

        try:
            context = await self._load_context(platform_task, identity=identity)
            decision = await self._decide(platform_task, context=context)
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
                send_result = await self.system_client.send(**send_payload)
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
            if event_status != "platform_complete_pending":
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
        raw = await self.model_client.chat_json(messages, tier="balanced", temperature=0.0, deadline_monotonic=deadline)
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
