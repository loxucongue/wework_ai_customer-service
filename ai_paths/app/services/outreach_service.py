from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.model_client import ModelClient
from app.services.outreach_prompts import (
    OUTREACH_MESSAGE_SYSTEM_PROMPT,
    OUTREACH_PLAN_SYSTEM_PROMPT,
    S10_OUTREACH_CONTEXT,
)
from app.services.outreach_system_client import OutreachSystemClient
from app.services.storage import AppRepository
from app.services.storage.serialization import dumps, utc_now_iso


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _message_time_iso(value: Any) -> str:
    raw = _string(value)
    if not raw:
        return ""
    if raw.isdigit():
        number = int(raw)
        if number > 10_000_000_000:
            number = number // 1000
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
    parsed = _parse_iso(raw)
    return parsed.isoformat() if parsed else raw


def _add_minutes(value: str, minutes: int) -> str:
    start = _parse_iso(value) or datetime.now(timezone.utc)
    return (start + timedelta(minutes=max(0, int(minutes)))).isoformat()


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


class OutreachService:
    def __init__(
        self,
        *,
        repository: AppRepository,
        model_client: ModelClient,
        system_client: OutreachSystemClient,
    ) -> None:
        self.repository = repository
        self.model_client = model_client
        self.system_client = system_client

    def list_candidates(
        self,
        *,
        limit: int = 50,
        silent_minutes_min: int = 60,
        outreach_status: str = "",
        lifecycle_stage: str = "",
        no_plan_only: bool = False,
    ) -> list[dict[str, Any]]:
        return self.repository.list_outreach_candidates(
            limit=limit,
            silent_minutes_min=silent_minutes_min,
            outreach_status=outreach_status,
            lifecycle_stage=lifecycle_stage,
            no_plan_only=no_plan_only,
        )

    async def refresh_customer_conversation(
        self,
        *,
        customer_id: str,
        corp_id: str,
        user_id: str,
        wechat: str,
        external_userid: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        payload = await self.system_client.conversation(
            corp_id=corp_id,
            customer_id=customer_id,
            external_userid=external_userid or customer_id,
            user_id=user_id,
            wechat=wechat,
            limit=limit,
        )
        messages = self._conversation_messages(payload)
        latest_customer = self._latest_message_time(messages, sender="customer")
        latest_staff = self._latest_message_time(messages, sender="staff")
        if latest_customer:
            self.repository.touch_customer_message_time(
                customer_id,
                field="last_customer_message_at",
                value=latest_customer,
            )
        if latest_staff:
            self.repository.touch_customer_message_time(
                customer_id,
                field="last_staff_message_at",
                value=latest_staff,
            )
        self.repository.add_outreach_event(
            plan_id="",
            task_id="",
            customer_id=customer_id,
            event_type="conversation_refreshed",
            event_summary="Refreshed customer conversation from system API",
            payload={"latest_customer_message_at": latest_customer, "message_count": len(messages)},
        )
        return {
            "raw": payload,
            "messages": messages,
            "latest_customer_message_at": latest_customer,
            "latest_staff_message_at": latest_staff,
        }

    def cached_customer_conversation(self, customer_id: str, *, limit: int = 10, error: str = "") -> dict[str, Any]:
        context = self.repository.recent_customer_context(customer_id)
        messages = self._local_context_messages(context.get("recent_messages") or [], limit=limit)
        if not messages:
            return {}
        return {
            "ok": True,
            "source": "local_cache",
            "warning": "平台历史聊天查询超时，已显示本地缓存记录",
            "error": error,
            "raw": {},
            "messages": messages,
            "latest_customer_message_at": self._latest_message_time(messages, sender="customer"),
            "latest_staff_message_at": self._latest_message_time(messages, sender="staff"),
        }

    async def generate_plan(
        self,
        *,
        customer_id: str,
        corp_id: str = "",
        user_id: str = "",
        wechat: str = "",
        external_userid: str = "",
        current_stage: str = "",
        business_goal: str = "",
    ) -> dict[str, Any]:
        context = self.repository.recent_customer_context(customer_id)
        memory = context.get("memory") or {}
        recent_messages = context.get("recent_messages") or []
        goal = business_goal or "推进客户支付10元预约金并到店"
        source_snapshot = {
            "customer_id": customer_id,
            "corp_id": corp_id,
            "user_id": user_id,
            "wechat": wechat,
            "external_userid": external_userid,
            "memory": memory,
            "recent_messages": recent_messages,
            "current_stage": current_stage,
            "business_goal": goal,
            "offer_context": S10_OUTREACH_CONTEXT,
        }
        response = await self.model_client.chat_json(
            [
                {"role": "system", "content": OUTREACH_PLAN_SYSTEM_PROMPT},
                {"role": "user", "content": dumps(source_snapshot)},
            ],
            tier="balanced",
            temperature=0.25,
        )
        if not bool(response.get("should_create_plan", True)):
            self.repository.add_outreach_event(
                plan_id="",
                task_id="",
                customer_id=customer_id,
                event_type="plan_rejected",
                event_summary=str(response.get("stall_reason") or "AI decided not to create outreach plan"),
                payload=response,
            )
            return {"created": False, "ai_result": response}
        now = utc_now_iso()
        tasks = []
        for index, step in enumerate(response.get("steps") or [], start=1):
            if not isinstance(step, dict):
                continue
            delay = int(step.get("delay_minutes") or (60 * index))
            tasks.append(
                {
                    "step_index": int(step.get("step") or index),
                    "scheduled_at": _add_minutes(now, delay),
                    "intent": str(step.get("intent") or "outreach"),
                    "message_goal": str(step.get("message_goal") or ""),
                    "content_sources": step.get("content_sources") or [],
                    "before_send_check": bool(step.get("before_send_check", True)),
                }
            )
        if not tasks:
            tasks = [
                {
                    "step_index": 1,
                    "scheduled_at": _add_minutes(now, 60),
                    "intent": "trust_rebuild",
                    "message_goal": "重新承接客户顾虑，邀请继续沟通",
                    "content_sources": ["s10_offer"],
                    "before_send_check": True,
                }
            ]
        source_snapshot["ai_result"] = response
        return {
            "created": True,
            **self.repository.create_outreach_plan(
                customer_id=customer_id,
                corp_id=corp_id,
                user_id=user_id,
                wechat=wechat,
                external_userid=external_userid,
                customer_stage=str(response.get("customer_stage") or ""),
                stall_reason=str(response.get("stall_reason") or ""),
                customer_psychology=str(response.get("customer_psychology") or ""),
                plan_goal=str(response.get("plan_goal") or ""),
                source_snapshot=source_snapshot,
                tasks=tasks[:3],
            ),
        }

    def activate_plan(self, plan_id: str) -> dict[str, Any]:
        self.repository.add_outreach_event(
            plan_id=plan_id,
            task_id="",
            customer_id=self._plan_customer_id(plan_id),
            event_type="plan_activated",
            event_summary="Outreach plan activated",
        )
        return self.repository.update_outreach_plan_status(plan_id, "active")

    def pause_plan(self, plan_id: str) -> dict[str, Any]:
        return self.repository.update_outreach_plan_status(plan_id, "paused")

    def resume_plan(self, plan_id: str) -> dict[str, Any]:
        return self.repository.update_outreach_plan_status(plan_id, "active")

    def cancel_plan(self, plan_id: str) -> dict[str, Any]:
        return self.repository.update_outreach_plan_status(plan_id, "cancelled")

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        return self.repository.get_outreach_plan(plan_id)

    def list_events(self, *, limit: int = 100, customer_id: str = "", plan_id: str = "") -> list[dict[str, Any]]:
        return self.repository.list_outreach_events(limit=limit, customer_id=customer_id, plan_id=plan_id)

    async def execute_due_tasks(self, *, limit: int = 20) -> dict[str, Any]:
        tasks = self.repository.list_due_outreach_tasks(limit=limit)
        results = []
        for task in tasks:
            results.append(await self.execute_task(task["id"]))
        return {"count": len(results), "results": results}

    async def execute_task(self, task_id: str) -> dict[str, Any]:
        task = self.repository.get_outreach_task(task_id)
        if not task:
            return {"ok": False, "error": "task_not_found"}
        self.repository.update_outreach_task(task_id, status="checking")
        plan_detail = self.repository.get_outreach_plan(str(task["plan_id"]))
        plan = plan_detail.get("plan") or {}
        try:
            if task.get("before_send_check"):
                try:
                    refresh = await self.refresh_customer_conversation(
                        customer_id=str(task["customer_id"]),
                        corp_id=str(task.get("corp_id") or plan.get("corp_id") or ""),
                        user_id=str(task.get("user_id") or plan.get("user_id") or ""),
                        wechat=str(task.get("wechat") or plan.get("wechat") or ""),
                        external_userid=str(task.get("external_userid") or plan.get("external_userid") or ""),
                        limit=10,
                    )
                    if self._customer_replied_after_plan(plan, refresh.get("latest_customer_message_at")):
                        self.repository.update_outreach_task(task_id, status="skipped")
                        self.repository.update_outreach_plan_status(str(task["plan_id"]), "paused")
                        self.repository.add_outreach_event(
                            plan_id=str(task["plan_id"]),
                            task_id=task_id,
                            customer_id=str(task["customer_id"]),
                            event_type="task_skipped_customer_replied",
                            event_summary="Customer replied before outreach task execution",
                            payload=refresh,
                        )
                        return {"ok": True, "status": "skipped", "reason": "customer_replied"}
                except Exception as exc:
                    self.repository.add_outreach_event(
                        plan_id=str(task["plan_id"]),
                        task_id=task_id,
                        customer_id=str(task["customer_id"]),
                        event_type="before_send_check_failed",
                        event_summary="Conversation check failed before outreach send; continuing with send",
                        payload={"error": f"{type(exc).__name__}: {exc}"},
                    )
            reply_messages = task.get("reply_messages") or []
            if not reply_messages:
                reply_messages = await self._generate_task_messages(task=task, plan=plan)
            send_result = await self.system_client.send(
                corp_id=str(task.get("corp_id") or plan.get("corp_id") or ""),
                customer_id=str(task["customer_id"]),
                external_userid=str(task.get("external_userid") or plan.get("external_userid") or task["customer_id"]),
                user_id=str(task.get("user_id") or plan.get("user_id") or ""),
                wechat=str(task.get("wechat") or plan.get("wechat") or ""),
                plan_id=str(task["plan_id"]),
                task_id=task_id,
                reply_messages=reply_messages,
            )
        except Exception as exc:
            message = str(exc)
            self.repository.update_outreach_task(task_id, status="failed", error_message=message)
            self.repository.add_outreach_event(
                plan_id=str(task["plan_id"]),
                task_id=task_id,
                customer_id=str(task["customer_id"]),
                event_type="task_failed",
                event_summary=message[:240],
                payload={"error": message},
            )
            return {"ok": False, "status": "failed", "error": message}
        data = send_result.get("data") if isinstance(send_result.get("data"), dict) else {}
        sent_at = utc_now_iso()
        self.repository.update_outreach_task(
            task_id,
            status="sent",
            reply_messages=reply_messages,
            sent_at=sent_at,
            send_status=str(data.get("send_status") or send_result.get("msg") or "accepted"),
            system_msgid=str(data.get("system_msgid") or ""),
        )
        self.repository.touch_customer_message_time(str(task["customer_id"]), field="last_outreach_at", value=sent_at)
        self.repository.update_customer_outreach_state(
            str(task["customer_id"]),
            outreach_status="waiting",
            outreach_plan_id=str(task["plan_id"]),
            last_outreach_at=sent_at,
        )
        self.repository.update_outreach_plan_status(str(task["plan_id"]), "waiting")
        self.repository.add_outreach_event(
            plan_id=str(task["plan_id"]),
            task_id=task_id,
            customer_id=str(task["customer_id"]),
            event_type="task_sent",
            event_summary="Outreach task sent",
            payload={"reply_messages": reply_messages, "send_result": send_result},
        )
        return {"ok": True, "status": "sent", "send_result": send_result}

    async def preview_task(self, task_id: str) -> dict[str, Any]:
        task = self.repository.get_outreach_task(task_id)
        if not task:
            return {"ok": False, "error": "task_not_found"}
        plan_detail = self.repository.get_outreach_plan(str(task["plan_id"]))
        plan = plan_detail.get("plan") or {}
        reply_messages = task.get("reply_messages") or []
        if not reply_messages:
            reply_messages = await self._generate_task_messages(task=task, plan=plan)
            task = self.repository.update_outreach_task(task_id, status=str(task.get("status") or "pending"), reply_messages=reply_messages)
        self.repository.add_outreach_event(
            plan_id=str(task["plan_id"]),
            task_id=task_id,
            customer_id=str(task["customer_id"]),
            event_type="task_previewed",
            event_summary="Generated outreach task messages for review without sending",
            payload={"reply_messages": reply_messages},
        )
        return {"ok": True, "status": "previewed", "reply_messages": reply_messages, "task": task}

    async def _generate_task_messages(self, *, task: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
        context = self.repository.recent_customer_context(str(task["customer_id"]))
        payload = {
            "task": task,
            "plan": plan,
            "customer_context": context,
            "offer_context": S10_OUTREACH_CONTEXT,
        }
        response = await self.model_client.chat_json(
            [
                {"role": "system", "content": OUTREACH_MESSAGE_SYSTEM_PROMPT},
                {"role": "user", "content": dumps(payload)},
            ],
            tier="balanced",
            temperature=0.35,
        )
        messages = response.get("reply_messages")
        if not isinstance(messages, list) or not messages:
            raise RuntimeError("outreach_message_model_empty")
        return [item for item in messages if isinstance(item, dict)][:3]

    def _plan_customer_id(self, plan_id: str) -> str:
        detail = self.repository.get_outreach_plan(plan_id)
        return str(detail.get("plan", {}).get("customer_id") or "")

    @staticmethod
    def _conversation_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        messages = data.get("messages") if isinstance(data, dict) else []
        return [item for item in messages if isinstance(item, dict)] if isinstance(messages, list) else []

    @staticmethod
    def _local_context_messages(recent_messages: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for item in recent_messages[-max(1, min(limit, 50)):]:
            role = _string(item.get("role"))
            created_at = _string(item.get("created_at"))
            if role == "user":
                content = _string(item.get("content"))
                if content:
                    output.append(
                        {
                            "direction": "customer",
                            "sender_type": "customer",
                            "sender_name": "客户",
                            "content": content,
                            "msgtype": "text",
                            "created_at": created_at,
                        }
                    )
                continue
            reply_messages = item.get("reply_messages") if isinstance(item.get("reply_messages"), list) else []
            if reply_messages:
                for reply in reply_messages:
                    if not isinstance(reply, dict):
                        continue
                    output.append(
                        {
                            "direction": "staff",
                            "sender_type": "staff",
                            "sender_name": "员工",
                            "content": reply.get("content"),
                            "msgtype": _string(reply.get("type")) or "text",
                            "created_at": created_at,
                        }
                    )
                continue
            content = _string(item.get("content"))
            if content:
                output.append(
                    {
                        "direction": "staff",
                        "sender_type": "staff",
                        "sender_name": "员工",
                        "content": content,
                        "msgtype": "text",
                        "created_at": created_at,
                    }
                )
        return output

    @staticmethod
    def _latest_message_time(messages: list[dict[str, Any]], *, sender: str) -> str:
        candidates = []
        for item in messages:
            direction = _string(item.get("direction") or item.get("from") or item.get("sender_type")).lower()
            if sender == "customer" and direction not in {"customer", "user", "external"}:
                continue
            if sender == "staff" and direction not in {"staff", "assistant", "service", "ai"}:
                continue
            value = _message_time_iso(item.get("msgtime") or item.get("created_at") or item.get("send_time"))
            if value:
                candidates.append(value)
        return max(candidates) if candidates else ""

    @staticmethod
    def _customer_replied_after_plan(plan: dict[str, Any], latest_customer_message_at: Any) -> bool:
        latest = _parse_iso(_string(latest_customer_message_at))
        anchor = _parse_iso(_string((plan.get("source_snapshot") or {}).get("memory", {}).get("last_customer_message_at")))
        if not anchor:
            anchor = _parse_iso(_string(plan.get("created_at")))
        return bool(latest and anchor and latest > anchor)
