from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AI_PATHS = ROOT / "ai_paths"
if str(AI_PATHS) not in sys.path:
    sys.path.insert(0, str(AI_PATHS))

from app.services.sop_platform_task_service import (  # noqa: E402
    SopPlatformTaskService,
    _platform_messages,
)
from app.config import Settings  # noqa: E402
from app.services.model_client import ModelClient  # noqa: E402


BEIJING = timezone(timedelta(hours=8))


class SimulationRepository:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, dict[str, Any]] = {}

    def create_sop_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload["event_id"])
        created = event_id not in self.events
        if created:
            self.events[event_id] = {
                "event_id": event_id,
                "event_type": payload.get("event_type"),
                "status": "accepted",
                "raw_payload": dict(payload),
            }
        return {**self.events[event_id], "created": created}

    def get_sop_event(self, event_id: str) -> dict[str, Any]:
        return dict(self.events.get(event_id) or {})

    def get_sop_send_task_by_idempotency_key(self, key: str) -> dict[str, Any]:
        return dict(self.tasks.get(key) or {})

    def find_sop_send_task_delivery_duplicate(
        self,
        send_once_key: str,
        *,
        exclude_idempotency_key: str = "",
    ) -> dict[str, Any]:
        for task in self.tasks.values():
            if task.get("idempotency_key") == exclude_idempotency_key:
                continue
            if task.get("send_once_key") != send_once_key:
                continue
            if task.get("status") in {"sent", "sending"}:
                return dict(task)
        return {}

    def update_sop_event_status(self, event_id: str, *, status: str, error: str = "") -> dict[str, Any]:
        self.events[event_id]["status"] = status
        self.events[event_id]["error"] = error
        return dict(self.events[event_id])

    def create_sop_send_task(self, **payload: Any) -> dict[str, Any]:
        key = str(payload["idempotency_key"])
        created = key not in self.tasks
        if created:
            self.tasks[key] = {"id": f"sim-local-{len(self.tasks) + 1}", **payload}
        return {**self.tasks[key], "created": created}

    def update_sop_send_task(self, task_id: str, **payload: Any) -> dict[str, Any]:
        task = next(value for value in self.tasks.values() if value["id"] == task_id)
        task.update(payload)
        return dict(task)

    def list_sop_events_by_statuses(
        self,
        statuses: list[str],
        *,
        limit: int,
        event_type: str = "",
    ) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self.events.values()
            if item["status"] in statuses and (not event_type or item["event_type"] == event_type)
        ][:limit]


class SimulationPlatform:
    def __init__(
        self,
        *,
        online_tasks: list[dict[str, Any]],
        store_tasks: list[dict[str, Any]],
        online_total: int | None = None,
        store_total: int | None = None,
    ) -> None:
        self.online_tasks = list(online_tasks)
        self.store_tasks = list(store_tasks)
        self.online_total = len(online_tasks) if online_total is None else online_total
        self.store_total = len(store_tasks) if store_total is None else store_total
        self.consume_calls: list[dict[str, Any]] = []

    async def pending(self, *, limit: int | None = None) -> dict[str, Any]:
        return {"items": self.online_tasks, "total": self.online_total, "limit": limit}

    async def store_visit_pending(self, *, limit: int | None = None) -> dict[str, Any]:
        return {
            "items": self.store_tasks,
            "total": self.store_total,
            "limit": limit,
            "biz_type": "store_visit",
        }

    async def consume(
        self,
        *,
        task_id: str | int,
        status: int,
        remark: str = "",
        content_exhausted: bool | None = None,
    ) -> dict[str, Any]:
        record = {
            "task_id": str(task_id),
            "status": status,
            "remark": remark,
            "content_exhausted": content_exhausted,
        }
        self.consume_calls.append(record)
        return {"code": 200, "data": record}


class SimulationSystem:
    def __init__(self, *, conversation: dict[str, Any], send_mode: str = "immediate") -> None:
        self.conversation_payload = {"code": 0, "data": conversation}
        self.send_mode = send_mode
        self.send_calls: list[dict[str, Any]] = []
        self.conversation_calls: list[dict[str, Any]] = []

    async def conversation_status(self, **_kwargs: Any) -> dict[str, Any]:
        data = self.conversation_payload.get("data") if isinstance(self.conversation_payload.get("data"), dict) else {}
        ai_auto_reply = data.get("ai_auto_reply", True)
        return {
            "code": 0,
            "data": {
                "takeover": {"ai_auto_reply": ai_auto_reply},
                "ai_auto_reply": ai_auto_reply,
            },
        }

    async def conversation(self, **kwargs: Any) -> dict[str, Any]:
        self.conversation_calls.append(kwargs)
        return self.conversation_payload

    async def send(self, **kwargs: Any) -> dict[str, Any]:
        self.send_calls.append(kwargs)
        if self.send_mode == "exception":
            raise RuntimeError("simulated_send_failure")
        if self.send_mode == "callback":
            return {
                "code": 0,
                "data": {"callback_required": True, "delivery_status": "platform_accepted"},
            }
        return {"code": 0, "data": {"send_status": "sent"}}


class SimulationCustomerContext:
    def load(self, **_kwargs: Any) -> dict[str, Any]:
        return {"source": "simulation", "orders": [], "appointment": {}}


class FixtureModel:
    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    async def chat_json(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if not self.outputs:
            raise RuntimeError("simulation_fixture_model_output_exhausted")
        return self.outputs.pop(0)


class CountingModel:
    def __init__(self, delegate: ModelClient) -> None:
        self.delegate = delegate
        self.calls: list[dict[str, Any]] = []

    async def chat_json(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return await self.delegate.chat_json(messages, **kwargs)


def settings(*, quiet: bool = False, shadow: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        sop_platform_token="simulation-only",
        sop_platform_base_url="https://test.api.customer.4ba.cn",
        sop_platform_timeout_seconds=2,
        sop_platform_lookback_seconds=300,
        sop_platform_window_seconds=60,
        sop_platform_batch_size=500,
        sop_platform_task_concurrency=2,
        sop_platform_queue_size=500,
        sop_platform_recovery_concurrency=2,
        sop_platform_recovery_batch_size=20,
        sop_platform_shadow_mode=shadow,
        sop_platform_model_timeout_seconds=10,
        sop_platform_max_task_age_seconds=21600,
        sop_platform_live_not_before="",
        sop_platform_quiet_hours_enabled=quiet,
        sop_platform_quiet_start_hour=0,
        sop_platform_quiet_end_hour=0 if quiet else 8,
        sop_platform_quiet_first_add_grace_minutes=0,
    )


def task(
    task_id: str,
    *,
    text: str,
    minute: int,
    trigger_event: str = "follow_up",
    use_ai_copy: bool = True,
    external_userid: str = "sim_customer_001",
) -> dict[str, Any]:
    scheduled = datetime.now(BEIJING).replace(second=0, microsecond=0) + timedelta(minutes=minute)
    return {
        "task_id": task_id,
        "ruleName": f"仿真任务{task_id}",
        "customerId": f"sim_{external_userid}",
        "customer_wechat_id": external_userid,
        "corp_id": "sim_corp",
        "user_wechat_id": "sim_staff",
        "user_wechat": "SIM001",
        "useAiCopy": use_ai_copy,
        "triggerEvent": trigger_event,
        "scheduledAt": scheduled.strftime("%Y-%m-%d %H:%M:%S"),
        "sortOrder": minute,
        "scene": {"name": f"场景{task_id}"},
        "message_content": [
            {"type": "text", "content": text},
            {"type": "image", "content": f"https://example.invalid/assets/{task_id}.jpg"},
        ],
    }


def conversation(
    *,
    ai: bool = True,
    opened: bool = True,
    deleted: bool = False,
    history: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {
            "direction": "customer",
            "content": "客户已经问过门店，现在想了解活动",
            "msgtime": int(datetime.now(BEIJING).timestamp() * 1000),
        }
    ]
    if not opened:
        messages = [{"direction": "customer", "content": "我已经添加了你，现在我们可以开始聊天了"}]
    elif history is not None:
        messages = [
            {
                "direction": direction,
                "content": content,
                "msgtime": int((datetime.now(BEIJING) + timedelta(seconds=index)).timestamp() * 1000),
            }
            for index, (direction, content) in enumerate(history)
        ]
    return {
        "ai_auto_reply": ai,
        "customer_relation": {"status": "deleted" if deleted else "active", "is_deleted": deleted},
        "messages": messages,
    }


def evaluation(task_id: str, decision: str, reason: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "decision": decision,
        "reason": reason,
        "evidence_refs": [f"task:{task_id}"],
    }


def decision(*items: dict[str, Any], selected: str = "", transition: str = "") -> dict[str, Any]:
    return {
        "evaluations": list(items),
        "selected_task_id": selected,
        "transition_text": transition,
    }


def scenarios() -> list[dict[str, Any]]:
    today = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    first_add = task("101", text="首次加微平台原文", minute=0, trigger_event="add_wecom")
    first_add["operateTime"] = today
    return [
        {
            "id": "same_day_unopened",
            "name": "加微当天未开口，最早任务原样发送",
            "online": [first_add, task("102", text="第二组暂不发送", minute=1)],
            "conversation": conversation(opened=False),
            "model": [],
            "expected_consume": [("101", 20), ("101", 30)],
            "expected_send_count": 1,
            "expected_selected": "101",
        },
        {
            "id": "skip_prefix_then_send",
            "name": "前两组跳过，第三组发送，第四组保留",
            "online": [
                task("201", text="重复门店询问", minute=0),
                task("202", text="重复标准报价", minute=1),
                task("203", text="新的效果唤醒内容", minute=2),
                task("204", text="后续付款提醒", minute=3),
            ],
            "conversation": conversation(
                history=[
                    ("customer", "荆州有门店吗"),
                    ("assistant", "有的，荆州万达店的门店卡已经发您了。"),
                    ("customer", "活动多少钱"),
                    ("assistant", "这次活动价格是268元，包含淡斑、皮肤检测、基础清洁和补水。"),
                    ("customer", "那效果到底怎么样"),
                ]
            ),
            "model": [
                decision(
                    evaluation("201", "skip", "门店已处理"),
                    evaluation("202", "skip", "标准报价已处理"),
                    evaluation("203", "send", "仍有新的效果触达目的"),
                    selected="203",
                    transition="前面门店已经给您看过了，我接着把效果参考发您。",
                ),
                {"status": "pass", "reason": "没有新增业务事实"},
            ],
            "expected_consume": [("203", 20), ("201", 70), ("202", 70), ("203", 30)],
            "expected_send_count": 1,
            "expected_selected": "203",
        },
        {
            "id": "all_filtered",
            "name": "所有到期任务均不适合发送",
            "online": [task("301", text="重复门店", minute=0), task("302", text="重复报价", minute=1)],
            "conversation": conversation(
                history=[
                    ("customer", "门店在哪里"),
                    ("assistant", "门店地址和门店卡已经发您。"),
                    ("customer", "多少钱"),
                    ("assistant", "活动价268元，活动内容也已经给您介绍完整。"),
                    ("customer", "不用了，别再发了"),
                ]
            ),
            "model": [
                decision(
                    evaluation("301", "skip", "已处理"),
                    evaluation("302", "skip", "已处理"),
                )
            ],
            "expected_consume": [("301", 70), ("302", 70)],
            "expected_send_count": 0,
            "expected_selected": "",
        },
        {
            "id": "human_takeover",
            "name": "人工接管，不调用模型，全部无需发送",
            "online": [task("401", text="人工状态任务一", minute=0), task("402", text="人工状态任务二", minute=1)],
            "conversation": conversation(ai=False),
            "model": [],
            "expected_consume": [("401", 70), ("402", 70)],
            "expected_send_count": 0,
            "expected_selected": "",
        },
        {
            "id": "transition_rejected",
            "name": "过渡句新增价格事实时丢弃过渡句，原文照发",
            "online": [task("501", text="平台268元活动原文", minute=0)],
            "conversation": conversation(),
            "model": [
                decision(evaluation("501", "send", "适合发送"), selected="501", transition="这次只要199元。"),
                {"status": "fail", "reason": "新增了平台原文没有的价格"},
            ],
            "expected_consume": [("501", 20), ("501", 30)],
            "expected_send_count": 1,
            "expected_selected": "501",
        },
        {
            "id": "callback_success",
            "name": "平台异步确认成功后才消费前缀",
            "online": [task("601", text="已处理任务", minute=0), task("602", text="待发任务", minute=1)],
            "conversation": conversation(),
            "model": [
                decision(
                    evaluation("601", "skip", "已处理"),
                    evaluation("602", "send", "适合发送"),
                    selected="602",
                )
            ],
            "send_mode": "callback",
            "callback": "send_succeeded",
            "expected_consume": [("602", 20), ("601", 70), ("602", 30)],
            "expected_send_count": 1,
            "expected_selected": "602",
        },
        {
            "id": "callback_failure",
            "name": "平台异步确认失败，跳过前缀不消费",
            "online": [task("701", text="已处理任务", minute=0), task("702", text="待发任务", minute=1)],
            "conversation": conversation(),
            "model": [
                decision(
                    evaluation("701", "skip", "已处理"),
                    evaluation("702", "send", "适合发送"),
                    selected="702",
                )
            ],
            "send_mode": "callback",
            "callback": "send_failed",
            "expected_consume": [("702", 20)],
            "expected_send_count": 1,
            "expected_selected": "702",
        },
        {
            "id": "send_exception",
            "name": "发送异常，跳过前缀不消费",
            "online": [task("801", text="已处理任务", minute=0), task("802", text="发送失败任务", minute=1)],
            "conversation": conversation(),
            "model": [
                decision(
                    evaluation("801", "skip", "已处理"),
                    evaluation("802", "send", "适合发送"),
                    selected="802",
                )
            ],
            "send_mode": "exception",
            "expected_consume": [("802", 20)],
            "expected_send_count": 1,
            "expected_selected": "802",
            "expected_error": "",
        },
        {
            "id": "incomplete_page",
            "name": "分页不完整，整轮不处理",
            "online": [task("901", text="分页中的第一组", minute=0)],
            "online_total": 2,
            "conversation": conversation(),
            "model": [],
            "expected_consume": [],
            "expected_send_count": 0,
            "expected_selected": "",
            "expected_poll_error": 1,
        },
        {
            "id": "quiet_hours",
            "name": "夜间只读取数量，不运行模型、发送或消费",
            "online": [task("1001", text="夜间到期任务", minute=0)],
            "conversation": conversation(),
            "model": [],
            "quiet": True,
            "expected_consume": [("1001", 70)],
            "expected_send_count": 0,
            "expected_selected": "",
        },
        {
            "id": "store_visit_queue",
            "name": "无正文时间任务从回访接口补齐全量消息组",
            "online": [
                {
                    **task("1100", text="", minute=0),
                    "message_content": [],
                }
            ],
            "store": [task("1101", text="门店回访原文", minute=0)],
            "conversation": conversation(),
            "model": [decision(evaluation("1101", "send", "适合回访"), selected="1101")],
            "expected_consume": [("1101", 20), ("1101", 30), ("1100", 70)],
            "expected_send_count": 1,
            "expected_selected": "1101",
        },
        {
            "id": "model_contract_repair",
            "name": "模型首次输出不合约，修复后继续",
            "online": [task("1201", text="需要判断的任务", minute=0)],
            "conversation": conversation(),
            "model": [
                {"evaluations": [], "selected_task_id": "1201", "transition_text": ""},
                decision(evaluation("1201", "send", "修复后适合发送"), selected="1201"),
            ],
            "expected_consume": [("1201", 20), ("1201", 30)],
            "expected_send_count": 1,
            "expected_selected": "1201",
            "expected_model_calls": 2,
        },
    ]


def canonical_hash(messages: list[dict[str, Any]]) -> str:
    canonical = [{"type": item.get("type"), "content": item.get("content")} for item in messages]
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def run_scenario(case: dict[str, Any], *, live_model: ModelClient | None = None) -> dict[str, Any]:
    online = list(case.get("online") or [])
    store = list(case.get("store") or [])
    repo = SimulationRepository()
    platform = SimulationPlatform(
        online_tasks=online,
        store_tasks=store,
        online_total=case.get("online_total"),
        store_total=case.get("store_total"),
    )
    system = SimulationSystem(
        conversation=case.get("conversation") or conversation(),
        send_mode=str(case.get("send_mode") or "immediate"),
    )
    model: FixtureModel | CountingModel
    model = CountingModel(live_model) if live_model is not None else FixtureModel(list(case.get("model") or []))
    service_settings = settings(quiet=bool(case.get("quiet")))
    if live_model is not None:
        service_settings.sop_platform_model_timeout_seconds = 45
    service = SopPlatformTaskService(
        settings=service_settings,
        repository=repo,
        platform_client=platform,
        system_client=system,
        model_client=model,
        customer_context_service=SimulationCustomerContext(),
        objection_material_service=None,
    )
    error = ""
    results: list[dict[str, Any]] = []
    poll_result: dict[str, Any] = {}
    try:
        poll_result = await service.poll_once()
        while not service._queue.empty():
            queued = service._queue.get_nowait()
            try:
                result = await service.process_customer_batch(queued)
                results.append(result)
                callback = str(case.get("callback") or "")
                if callback and system.send_calls:
                    source_context = system.send_calls[-1]["source_context"]
                    await service.finalize_message_delivery(
                        {
                            "status": callback,
                            "error_message": "simulated_callback_failure" if callback == "send_failed" else "",
                            "source_context": source_context,
                            "source_task_id": source_context["sop_send_task_id"],
                        }
                    )
            finally:
                service._queue.task_done()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    selected = ""
    for result in results:
        selected = str(result.get("task_id") or selected)
    if not selected and system.send_calls:
        source_context = system.send_calls[-1].get("source_context") or {}
        selected = str(source_context.get("platform_task_id") or "")
    expected_selected = str(case.get("expected_selected") or "")
    consumes = [(item["task_id"], item["status"]) for item in platform.consume_calls]
    checks = {
        "consume_sequence": consumes == list(case.get("expected_consume") or []),
        "send_count": len(system.send_calls) == int(case.get("expected_send_count") or 0),
        "selected_task": selected == expected_selected,
        "poll_error": int(poll_result.get("error_count") or 0) == int(case.get("expected_poll_error") or 0),
        "error": str(case.get("expected_error") or "") in error if case.get("expected_error") else not error,
        "model_calls": (
            True
            if live_model is not None
            else len(model.calls) == int(case.get("expected_model_calls", len(case.get("model") or [])))
        ),
    }

    original_hash = ""
    delivered_hash = ""
    original_unchanged = True
    if expected_selected and system.send_calls:
        selected_task = next((item for item in [*online, *store] if str(item["task_id"]) == expected_selected), None)
        if selected_task:
            original = _platform_messages(selected_task)
            delivered = system.send_calls[-1].get("reply_messages") or []
            delivered_original = delivered[-len(original) :] if original else []
            original_hash = canonical_hash(original)
            delivered_hash = canonical_hash(delivered_original)
            original_unchanged = original_hash == delivered_hash
    checks["original_messages_unchanged"] = original_unchanged

    decisions = []
    decision_keys: set[str] = set()
    for task_record in repo.tasks.values():
        audit = task_record.get("send_payload") if isinstance(task_record.get("send_payload"), dict) else {}
        if isinstance(audit.get("decision"), dict):
            key = json.dumps(audit["decision"], ensure_ascii=False, sort_keys=True)
            if key not in decision_keys:
                decision_keys.add(key)
                decisions.append(audit["decision"])

    return {
        "case_id": case["id"],
        "name": case["name"],
        "passed": all(checks.values()),
        "checks": checks,
        "poll_result": poll_result,
        "task_groups": [
            {
                "biz_type": "online_service" if item in online else "store_visit",
                "task_id": str(item["task_id"]),
                "scheduled_at": item.get("scheduledAt"),
                "message_content": item.get("message_content"),
            }
            for item in [*online, *store]
        ],
        "model_decisions": decisions,
        "model_call_count": len(model.calls),
        "selected_task_id": selected,
        "sent_messages": [call.get("reply_messages") for call in system.send_calls],
        "consume_calls": platform.consume_calls,
        "event_statuses": {key: value.get("status") for key, value in repo.events.items()},
        "original_hash": original_hash,
        "delivered_original_hash": delivered_hash,
        "error": error,
    }


def render_markdown(run: dict[str, Any]) -> str:
    lines = [
        "# SOP 待消费任务全链路仿真审核报告",
        "",
        f"- 运行时间：`{run['created_at']}`",
        f"- 运行模式：`{run['mode']}`",
        f"- 场景数量：`{run['summary']['total']}`",
        f"- 通过：`{run['summary']['passed']}`",
        f"- 失败：`{run['summary']['failed']}`",
        "- 外部写操作：`0`（平台、会话和发送均为本地仿真适配器）",
        "",
        "## 汇总",
        "",
        "| 场景 | 结果 | 选中任务 | 模型调用 | 发送次数 | 消费顺序 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in run["results"]:
        consumes = " → ".join(f"{row['task_id']}:{row['status']}" for row in item["consume_calls"]) or "-"
        lines.append(
            f"| {item['name']} | {'通过' if item['passed'] else '失败'} | "
            f"{item['selected_task_id'] or '-'} | {item['model_call_count']} | "
            f"{len(item['sent_messages'])} | {consumes} |"
        )

    lines.extend(["", "## 逐条审核", ""])
    for item in run["results"]:
        lines.extend(
            [
                f"### {item['case_id']} · {item['name']}",
                "",
                f"- 结论：**{'通过' if item['passed'] else '失败'}**",
                f"- 选中任务：`{item['selected_task_id'] or '-'}`",
                f"- 原文哈希一致：`{item['checks']['original_messages_unchanged']}`",
                f"- 事件状态：`{json.dumps(item['event_statuses'], ensure_ascii=False)}`",
                f"- 异常：`{item['error'] or '-'}`",
                "",
                "**模型判断**",
                "```json",
                json.dumps(item["model_decisions"], ensure_ascii=False, indent=2),
                "```",
                "",
                "**实际发送内容**",
                "```json",
                json.dumps(item["sent_messages"], ensure_ascii=False, indent=2),
                "```",
                "",
                "**消费回传**",
                "```json",
                json.dumps(item["consume_calls"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated SOP pending full-chain simulations.")
    parser.add_argument("--output", default="", help="Output directory. Defaults to .tmp_runtime/sop_pending_e2e/<run_id>.")
    parser.add_argument("--case", action="append", default=[], help="Run only selected case_id; repeatable.")
    parser.add_argument("--real-model", action="store_true", help="Use the configured real model with simulated platform adapters.")
    args = parser.parse_args()

    run_id = datetime.now(BEIJING).strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output) if args.output else ROOT / ".tmp_runtime" / "sop_pending_e2e" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = set(args.case)
    case_list = [item for item in scenarios() if not selected or item["id"] in selected]
    live_model: ModelClient | None = None
    if args.real_model:
        live_settings = Settings()
        live_model = ModelClient(live_settings)
        if not live_model.available:
            raise RuntimeError("real model mode requires a configured model API key")
    try:
        results = [await run_scenario(item, live_model=live_model) for item in case_list]
    finally:
        if live_model is not None:
            await live_model.aclose()
    counts = Counter("passed" if item["passed"] else "failed" for item in results)
    run = {
        "run_id": run_id,
        "created_at": datetime.now(BEIJING).isoformat(),
        "mode": "isolated_real_model_full_chain" if args.real_model else "isolated_deterministic_full_chain",
        "summary": {"total": len(results), "passed": counts["passed"], "failed": counts["failed"]},
        "results": results,
    }
    (output_dir / "result.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "report.md").write_text(render_markdown(run), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), **run["summary"]}, ensure_ascii=False))
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
