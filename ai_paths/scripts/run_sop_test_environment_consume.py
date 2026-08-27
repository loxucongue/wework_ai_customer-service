from __future__ import annotations

import argparse
import asyncio
import json
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AI_PATHS = ROOT / "ai_paths"
if str(AI_PATHS) not in sys.path:
    sys.path.insert(0, str(AI_PATHS))

from app.config import Settings  # noqa: E402
from app.services.model_client import ModelClient  # noqa: E402
from app.services.sop_platform_client import SopPlatformClient  # noqa: E402
from app.services.sop_platform_task_service import (  # noqa: E402
    SopPlatformTaskService,
    _customer_batch_key,
    _task_batch_sort_key,
    _task_id,
)


TEST_BASE_URL = "https://test.api.customer.4ba.cn"
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    task_ids: tuple[str, ...]
    expected_statuses: dict[str, int | None]
    expected_send_task_id: str


SCENARIOS = (
    Scenario(
        scenario_id="human_takeover_consumes_all",
        title="人工接管：不发送，当前到期任务全部消费",
        task_ids=("1261", "1262"),
        expected_statuses={"1261": 70, "1262": 70},
        expected_send_task_id="",
    ),
    Scenario(
        scenario_id="ai_sends_first_keeps_second",
        title="AI 托管：最早一条适合发送，只消费第一条并保留第二条",
        task_ids=("1263", "1264"),
        expected_statuses={"1263": 30, "1264": None},
        expected_send_task_id="1263",
    ),
    Scenario(
        scenario_id="ai_skips_first_sends_second",
        title="AI 托管：第一条已处理，跳过第一条并发送第二条",
        task_ids=("1265", "1266"),
        expected_statuses={"1265": 70, "1266": 30},
        expected_send_task_id="1266",
    ),
    Scenario(
        scenario_id="ai_skips_all",
        title="AI 托管：所有待消费内容均已处理，全部消费但不发送",
        task_ids=("1267", "1268"),
        expected_statuses={"1267": 70, "1268": 70},
        expected_send_task_id="",
    ),
    Scenario(
        scenario_id="deleted_relation_consumes_all",
        title="客户关系失效：不调用模型、不发送，全部消费",
        task_ids=("1269", "1270"),
        expected_statuses={"1269": 70, "1270": 70},
        expected_send_task_id="",
    ),
    Scenario(
        scenario_id="same_day_unopened_direct_first",
        title="加微当天未开口：不调用筛选模型，原样直发最早一条",
        task_ids=("2654",),
        expected_statuses={"2654": 30},
        expected_send_task_id="2654",
    ),
)


class AuditRepository:
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
                "error": "",
                "raw_payload": dict(payload),
            }
        return {**self.events[event_id], "created": created}

    def get_sop_event(self, event_id: str) -> dict[str, Any]:
        return dict(self.events.get(event_id) or {})

    def update_sop_event_status(self, event_id: str, *, status: str, error: str = "") -> dict[str, Any]:
        self.events[event_id].update({"status": status, "error": error})
        return dict(self.events[event_id])

    def create_sop_send_task(self, **payload: Any) -> dict[str, Any]:
        key = str(payload["idempotency_key"])
        created = key not in self.tasks
        if created:
            self.tasks[key] = {"id": f"test-local-{len(self.tasks) + 1}", **payload}
        return {**self.tasks[key], "created": created}

    def get_sop_send_task_by_idempotency_key(self, key: str) -> dict[str, Any]:
        return dict(self.tasks.get(key) or {})

    def update_sop_send_task(self, task_id: str, **payload: Any) -> dict[str, Any]:
        task = next(item for item in self.tasks.values() if item["id"] == task_id)
        task.update(payload)
        return dict(task)

    def find_sop_send_task_delivery_duplicate(
        self,
        send_once_key: str,
        *,
        exclude_idempotency_key: str = "",
    ) -> dict[str, Any]:
        for task in self.tasks.values():
            if task.get("idempotency_key") == exclude_idempotency_key:
                continue
            if task.get("send_once_key") == send_once_key and task.get("status") in {"sent", "sending"}:
                return dict(task)
        return {}


class RecordingTestPlatform:
    def __init__(self, client: SopPlatformClient) -> None:
        self.client = client
        self.consume_calls: list[dict[str, Any]] = []

    async def consume(
        self,
        *,
        task_id: str | int,
        status: int,
        remark: str = "",
        content_exhausted: bool | None = None,
    ) -> dict[str, Any]:
        response = await self.client.consume(
            task_id=task_id,
            status=status,
            remark=remark,
            content_exhausted=content_exhausted,
        )
        self.consume_calls.append(
            {
                "task_id": str(task_id),
                "status": status,
                "remark": remark,
                "response": response,
            }
        )
        return response


class LocalTestSystem:
    def __init__(self, contexts: dict[str, dict[str, Any]]) -> None:
        self.contexts = contexts
        self.send_calls: list[dict[str, Any]] = []

    async def conversation(self, **identity: Any) -> dict[str, Any]:
        external_userid = str(identity.get("external_userid") or "")
        if external_userid not in self.contexts:
            raise RuntimeError(f"missing local test conversation: {external_userid}")
        return {"code": 0, "data": deepcopy(self.contexts[external_userid])}

    async def send(self, **payload: Any) -> dict[str, Any]:
        self.send_calls.append(deepcopy(payload))
        return {
            "code": 0,
            "msg": "test_local_send_succeeded",
            "data": {
                "send_status": "sent",
                "delivery_status": "sent",
                "test_local_only": True,
            },
        }


class EmptyCustomerContext:
    def load(self, **_kwargs: Any) -> dict[str, Any]:
        return {"source": "test_environment_harness", "orders": [], "appointment": {}}


def _message(direction: str, content: str, offset_seconds: int) -> dict[str, Any]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return {
        "direction": direction,
        "content": content,
        "msgtype": "text",
        "msgtime": now_ms + offset_seconds * 1000,
    }


def _conversation(
    *,
    ai: bool = True,
    deleted: bool = False,
    history: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    messages = [
        _message(direction, content, index - len(history or []))
        for index, (direction, content) in enumerate(history or [])
    ]
    return {
        "ai_auto_reply": ai,
        "customer_relation": {"status": "deleted" if deleted else "active", "is_deleted": deleted},
        "messages": messages,
    }


def _first_text(task: dict[str, Any]) -> str:
    for message in task.get("message_content") or []:
        if isinstance(message, dict) and str(message.get("type") or "") == "text":
            return str(message.get("content") or "")
    return ""


def _scenario_contexts(tasks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id = {_task_id(task): task for task in tasks}

    def external(task_id: str) -> str:
        return str(by_id[task_id].get("customer_wechat_id") or "")

    return {
        external("1261"): _conversation(
            ai=False,
            history=[("customer", "我已经转人工接待了，后续由人工老师和我沟通。")],
        ),
        external("1263"): _conversation(history=[]),
        external("1265"): _conversation(
            history=[
                ("assistant", _first_text(by_id["1265"])),
                ("customer", "第一条到店提醒我已经收到了，主任今天在店的消息可以提醒我。"),
            ]
        ),
        external("1267"): _conversation(
            history=[
                ("assistant", _first_text(by_id["1267"])),
                ("assistant", _first_text(by_id["1268"])),
                ("customer", "这两条提醒我都收到了，不需要重复发。"),
            ]
        ),
        external("1269"): _conversation(
            deleted=True,
            history=[("customer", "客户关系已解除。")],
        ),
        external("2654"): _conversation(ai=True, history=[]),
    }


def _simulate_current_timing(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(BEIJING_TZ).replace(microsecond=0)
    output = deepcopy(tasks)
    scenario_by_task = {
        task_id: scenario
        for scenario in SCENARIOS
        for task_id in scenario.task_ids
    }
    position_by_task = {
        task_id: index
        for scenario in SCENARIOS
        for index, task_id in enumerate(scenario.task_ids)
    }
    for task in output:
        task_id = _task_id(task)
        scenario = scenario_by_task[task_id]
        position = position_by_task[task_id]
        task["_test_original_scheduled_at"] = task.get("scheduledAt") or task.get("scheduled_at")
        task["scheduledAt"] = (now - timedelta(minutes=2 - position)).strftime("%Y-%m-%d %H:%M:%S")
        if scenario.scenario_id == "same_day_unopened_direct_first":
            task["triggerEvent"] = "add_wecom"
            task["operateTime"] = (now - timedelta(minutes=32)).strftime("%Y-%m-%d %H:%M:%S")
    return output


def _selected_task_ids() -> set[str]:
    return {task_id for scenario in SCENARIOS for task_id in scenario.task_ids}


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": _task_id(task),
        "trigger_event": task.get("triggerEvent"),
        "rule_name": task.get("ruleName"),
        "customer": task.get("customer_wechat_id"),
        "original_scheduled_at": task.get("_test_original_scheduled_at"),
        "simulated_scheduled_at": task.get("scheduledAt"),
        "message_content": task.get("message_content") or [],
    }


def _send_task_id(payload: dict[str, Any]) -> str:
    raw = str(payload.get("task_id") or "")
    return raw.removeprefix("platform-sop-send-")


def _final_statuses(
    *,
    consume_calls: list[dict[str, Any]],
    send_calls: list[dict[str, Any]],
    shadow: bool,
    batch_results: list[dict[str, Any]],
) -> tuple[dict[str, int | None], set[str]]:
    statuses: dict[str, int | None] = {task_id: None for task_id in _selected_task_ids()}
    sent_ids = {_send_task_id(item) for item in send_calls if _send_task_id(item)}
    if shadow:
        for result in batch_results:
            selected = str((result.get("decision") or {}).get("selected_task_id") or result.get("task_id") or "")
            evaluated = (result.get("decision") or {}).get("evaluations") or []
            for item in evaluated:
                task_id = str(item.get("task_id") or "")
                if not task_id:
                    continue
                statuses[task_id] = 30 if task_id == selected else 70
            if selected:
                sent_ids.add(selected)
            for task_id in result.get("task_ids") or []:
                if result.get("status") == "shadow_no_send":
                    statuses[str(task_id)] = 70
            if selected:
                selected_index = next(
                    (index for index, item in enumerate(evaluated) if str(item.get("task_id") or "") == selected),
                    -1,
                )
                if selected_index >= 0:
                    for item in evaluated[:selected_index]:
                        statuses[str(item.get("task_id") or "")] = 70
                statuses[selected] = 30
    else:
        for item in consume_calls:
            status = int(item["status"])
            if status in {30, 70}:
                statuses[str(item["task_id"])] = status
    return statuses, sent_ids


def _scenario_results(statuses: dict[str, int | None], sent_ids: set[str]) -> list[dict[str, Any]]:
    results = []
    for scenario in SCENARIOS:
        actual = {task_id: statuses.get(task_id) for task_id in scenario.task_ids}
        actual_send = next((task_id for task_id in scenario.task_ids if task_id in sent_ids), "")
        passed = actual == scenario.expected_statuses and actual_send == scenario.expected_send_task_id
        results.append(
            {
                "scenario_id": scenario.scenario_id,
                "title": scenario.title,
                "task_ids": list(scenario.task_ids),
                "expected_statuses": scenario.expected_statuses,
                "actual_statuses": actual,
                "expected_send_task_id": scenario.expected_send_task_id,
                "actual_send_task_id": actual_send,
                "passed": passed,
            }
        )
    return results


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings()
    if settings.sop_platform_base_url.rstrip("/") != TEST_BASE_URL:
        raise RuntimeError(f"refusing non-test SOP platform: {settings.sop_platform_base_url}")
    if not settings.sop_platform_token:
        raise RuntimeError("SOP_PLATFORM_TOKEN is required")
    if not settings.model_relay_api_key:
        raise RuntimeError("MODEL_RELAY_API_KEY is required")
    settings.sop_platform_shadow_mode = not args.execute_consume
    settings.sop_platform_quiet_hours_enabled = False

    platform_client = SopPlatformClient(settings)
    model_client = ModelClient(settings)
    try:
        online_page = await platform_client.pending(limit=500)
        if not online_page.get("complete"):
            raise RuntimeError("pending_page_incomplete")
        selected_ids = _selected_task_ids()
        source_tasks = [task for task in online_page.get("items") or [] if _task_id(task) in selected_ids]
        found = {_task_id(task) for task in source_tasks}
        missing = sorted(selected_ids - found)
        if missing:
            raise RuntimeError(f"test tasks are no longer pending: {','.join(missing)}")

        tasks = _simulate_current_timing(source_tasks)
        repository = AuditRepository()
        platform = RecordingTestPlatform(platform_client)
        system = LocalTestSystem(_scenario_contexts(tasks))
        service = SopPlatformTaskService(
            settings=settings,
            repository=repository,
            platform_client=platform,
            system_client=system,
            model_client=model_client,
            customer_context_service=EmptyCustomerContext(),
        )

        grouped: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            task["_aics_biz_type"] = "online_service"
            grouped.setdefault(_customer_batch_key(task), []).append(task)
        batch_results: list[dict[str, Any]] = []
        for batch_tasks in grouped.values():
            batch_tasks.sort(key=_task_batch_sort_key)
            batch_results.append(
                await service.process_customer_batch(
                    {
                        "_aics_customer_batch": True,
                        "batch_key": _customer_batch_key(batch_tasks[0]),
                        "biz_type": "online_service",
                        "tasks": batch_tasks,
                    }
                )
            )

        statuses, sent_ids = _final_statuses(
            consume_calls=platform.consume_calls,
            send_calls=system.send_calls,
            shadow=not args.execute_consume,
            batch_results=batch_results,
        )
        scenario_results = _scenario_results(statuses, sent_ids)
        remaining_page = await platform_client.pending(limit=500)
        remaining_ids = sorted(
            _task_id(task)
            for task in remaining_page.get("items") or []
            if _task_id(task) in selected_ids
        )
        return {
            "schema_version": "sop_test_environment_behavior_matrix_v2",
            "mode": "execute_consume" if args.execute_consume else "preview_shadow",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "platform_base_url": TEST_BASE_URL,
            "external_send_mode": "local_recording_only",
            "selected_task_count": len(tasks),
            "expected_terminal_task_count": 10,
            "selected_tasks": [_task_summary(task) for task in sorted(tasks, key=_task_batch_sort_key)],
            "scenario_results": scenario_results,
            "all_scenarios_passed": all(item["passed"] for item in scenario_results),
            "batch_results": batch_results,
            "consume_calls": platform.consume_calls,
            "local_send_calls": system.send_calls,
            "final_statuses": statuses,
            "remaining_selected_task_ids": remaining_ids,
        }
    finally:
        await platform_client.aclose()
        await model_client.aclose()


def write_report(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# SOP 测试环境行为矩阵报告",
        "",
        f"- 模式：`{result['mode']}`",
        f"- 测试平台：`{result['platform_base_url']}`",
        "- 客户消息发送：仅本地记录，不调用生产聚合平台",
        f"- 读取任务：`{result['selected_task_count']}` 条",
        f"- 目标消费：`{result['expected_terminal_task_count']}` 条",
        f"- 保留待消费：`{', '.join(result['remaining_selected_task_ids']) or '无'}`",
        "",
        "| 场景 | 任务 | 预期状态 | 实际状态 | 发送任务 | 结果 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in result["scenario_results"]:
        lines.append(
            f"| {item['title']} | {', '.join(item['task_ids'])} | "
            f"{json.dumps(item['expected_statuses'], ensure_ascii=False)} | "
            f"{json.dumps(item['actual_statuses'], ensure_ascii=False)} | "
            f"{item['actual_send_task_id'] or '-'} | {'通过' if item['passed'] else '失败'} |"
        )
    lines.extend(
        [
            "",
            "## 客户可见发送内容（本地模拟）",
            "",
            "```json",
            json.dumps(
                [
                    {
                        "task_id": _send_task_id(item),
                        "reply_messages": item.get("reply_messages") or [],
                    }
                    for item in result["local_send_calls"]
                ],
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
            "## 模型和流程判断",
            "",
            "```json",
            json.dumps(result["batch_results"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## 测试平台消费回传",
            "",
            "```json",
            json.dumps(result["consume_calls"], ensure_ascii=False, indent=2),
            "```",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview or execute the SOP behavioral matrix against test pending/consume APIs."
    )
    parser.add_argument("--execute-consume", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    write_report(result, Path(args.output))
    print(
        json.dumps(
            {
                "output": args.output,
                "mode": result["mode"],
                "scenario_passed": sum(1 for item in result["scenario_results"] if item["passed"]),
                "scenario_total": len(result["scenario_results"]),
                "remaining": result["remaining_selected_task_ids"],
            },
            ensure_ascii=False,
        )
    )
    if args.execute_consume and not result["all_scenarios_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
