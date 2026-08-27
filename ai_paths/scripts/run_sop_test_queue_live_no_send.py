from __future__ import annotations

import argparse
import asyncio
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AI_PATHS = ROOT / "ai_paths"
SCRIPTS = AI_PATHS / "scripts"
for value in (AI_PATHS, SCRIPTS):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from app.config import Settings  # noqa: E402
from app.services.customer_context import CustomerContextService  # noqa: E402
from app.services.model_client import ModelClient  # noqa: E402
from app.services.outreach_system_client import OutreachSystemClient  # noqa: E402
from app.services.platform_agent_client import PlatformAgentClient  # noqa: E402
from app.services.sop_platform_client import SopPlatformClient  # noqa: E402
from app.services.sop_platform_task_service import (  # noqa: E402
    SopPlatformTaskService,
    _batch_compat_trigger_tasks,
    _conversation_ai_auto_reply,
    _customer_batch_key,
    _platform_messages,
    _resolve_compatible_pending_tasks,
    _task_batch_sort_key,
    _task_id,
    _task_identity,
)
from run_sop_test_environment_consume import AuditRepository, RecordingTestPlatform  # noqa: E402


TEST_BASE_URL = "https://test.api.customer.4ba.cn"


class RealConversationLocalSendSystem:
    """Read real conversations, but never call the external send endpoint."""

    def __init__(self, client: OutreachSystemClient) -> None:
        self.client = client
        self.conversation_calls: list[dict[str, Any]] = []
        self.send_calls: list[dict[str, Any]] = []

    async def conversation(self, **identity: Any) -> dict[str, Any]:
        response = await self.client.conversation(**identity)
        self.conversation_calls.append(
            {
                "identity": _public_identity(identity),
                "response_summary": _conversation_response_summary(response),
            }
        )
        return response

    async def send(self, **payload: Any) -> dict[str, Any]:
        recorded = deepcopy(payload)
        recorded["external_send_suppressed"] = True
        self.send_calls.append(recorded)
        return {
            "code": 0,
            "msg": "local_no_send_success",
            "data": {
                "send_status": "sent",
                "delivery_status": "sent",
                "callback_required": False,
                "external_send_suppressed": True,
            },
        }


def _public_identity(identity: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(identity.get(key) or "")
        for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat")
    }


def _conversation_response_summary(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    if not isinstance(data, dict):
        return {"valid": False, "response_type": type(data).__name__}
    messages = data.get("messages") if isinstance(data.get("messages"), list) else []
    relation = data.get("customer_relation") if isinstance(data.get("customer_relation"), dict) else {}
    return {
        "valid": True,
        "message_count": len(messages),
        "ai_auto_reply": _conversation_ai_auto_reply(data),
        "relation": {
            "status": relation.get("status"),
            "is_deleted": relation.get("is_deleted"),
        },
        "available_fields": sorted(str(key) for key in data),
    }


def _task_row(task: dict[str, Any]) -> dict[str, Any]:
    identity = _task_identity(task)
    return {
        "task_id": _task_id(task),
        "biz_type": str(task.get("_aics_biz_type") or ""),
        "scheduled_at": task.get("scheduledAt") or task.get("scheduled_at"),
        "sort_order": task.get("sortOrder") or task.get("sort_order"),
        "rule_name": task.get("ruleName") or task.get("rule_name"),
        "trigger_event": task.get("triggerEvent") or task.get("trigger_event"),
        "identity": _public_identity(identity),
        "message_content": task.get("message_content") or [],
    }


def _preflight_error_label(exc: Exception) -> str:
    text = str(exc)
    if "missing identity" in text:
        return "identity_missing"
    if "http_404" in text:
        return "conversation_not_found"
    if "http_409" in text:
        return "account_mapping_incomplete"
    return f"conversation_lookup_failed:{type(exc).__name__}"


async def _preflight_batch(
    tasks: list[dict[str, Any]],
    *,
    conversation_client: OutreachSystemClient,
) -> dict[str, Any]:
    identity = _task_identity(tasks[0])
    missing = [key for key, value in identity.items() if key in {"corp_id", "customer_id", "external_userid", "user_id", "wechat"} and not value]
    result: dict[str, Any] = {
        "batch_key": _customer_batch_key(tasks[0]),
        "biz_type": str(tasks[0].get("_aics_biz_type") or ""),
        "task_ids": [_task_id(task) for task in tasks],
        "identity": _public_identity(identity),
        "nodes": [
            {
                "node": "pending_queue_fetch",
                "status": "passed",
                "conclusion": "测试环境待消费任务已读取，未改变平台状态",
            },
            {
                "node": "customer_batch_ordering",
                "status": "passed",
                "conclusion": "任务已按 scheduledAt、sortOrder、task_id 排序",
            },
        ],
        "runnable": False,
        "block_reason": "",
    }
    if missing:
        result["block_reason"] = "identity_missing"
        result["nodes"].append(
            {
                "node": "identity_normalization",
                "status": "blocked",
                "conclusion": f"缺少字段：{', '.join(missing)}",
            }
        )
        return result

    result["nodes"].append(
        {
            "node": "identity_normalization",
            "status": "passed",
            "conclusion": "客户与接待账号身份字段完整",
        }
    )
    try:
        response = await conversation_client.conversation(**identity, limit=50)
    except Exception as exc:
        label = _preflight_error_label(exc)
        result["block_reason"] = label
        result["nodes"].append(
            {
                "node": "conversation_lookup",
                "status": "blocked",
                "conclusion": label,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return result

    summary = _conversation_response_summary(response)
    result["conversation_summary"] = summary
    result["nodes"].append(
        {
            "node": "conversation_lookup",
            "status": "passed" if summary.get("valid") else "blocked",
            "conclusion": f"拉取到 {summary.get('message_count', 0)} 条聊天记录" if summary.get("valid") else "会话响应结构无效",
        }
    )
    if not summary.get("valid"):
        result["block_reason"] = "conversation_response_invalid"
        return result
    if summary.get("ai_auto_reply") is None:
        result["block_reason"] = "management_state_missing"
        result["nodes"].append(
            {
                "node": "management_state",
                "status": "blocked",
                "conclusion": "会话接口未返回 ai_auto_reply，不能猜测当前是人工还是 AI",
            }
        )
        return result
    result["nodes"].append(
        {
            "node": "management_state",
            "status": "passed",
            "conclusion": "AI托管" if summary["ai_auto_reply"] else "人工接管",
        }
    )
    result["runnable"] = True
    return result


async def _fetch_pending_pages(platform_client: SopPlatformClient) -> tuple[dict[str, Any], dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            online_page = await platform_client.pending(limit=500)
            online_items = online_page.get("items") if isinstance(online_page, dict) else online_page
            needs_content_lookup = any(
                isinstance(item, dict) and not _platform_messages(item)
                for item in (online_items if isinstance(online_items, list) else [])
            )
            store_page = (
                await platform_client.store_visit_pending(limit=500)
                if needs_content_lookup
                else {"items": [], "total": 0, "complete": True, "biz_type": "store_visit"}
            )
            return online_page, store_page
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                await asyncio.sleep(float(attempt))
    assert last_error is not None
    raise last_error


def _append_runtime_nodes(
    preflight: dict[str, Any],
    *,
    batch_result: dict[str, Any] | None,
    consume_calls: list[dict[str, Any]],
    send_calls: list[dict[str, Any]],
    error: str,
) -> None:
    if error:
        preflight["nodes"].append(
            {
                "node": "batch_runtime",
                "status": "failed",
                "conclusion": error,
            }
        )
        preflight["final_status"] = "runtime_failed_no_further_consume"
        return
    result = batch_result or {}
    decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
    evaluations = decision.get("evaluations") if isinstance(decision.get("evaluations"), list) else []
    mode = str((preflight.get("conversation_summary") or {}).get("ai_auto_reply"))
    if mode == "False":
        preflight["nodes"].append(
            {
                "node": "human_takeover",
                "status": "passed",
                "conclusion": "不运行模型、不发送，消费当前到期任务",
            }
        )
    elif str(decision.get("decision_source") or "") == "same_day_unopened_direct":
        preflight["nodes"].append(
            {
                "node": "same_day_unopened",
                "status": "passed",
                "conclusion": "加微当天未开口，最早一组原样直发，无模型改写",
            }
        )
    elif evaluations:
        preflight["nodes"].append(
            {
                "node": "deepseek_sequence_decision",
                "status": "passed",
                "conclusion": "严格从最早任务开始，遇到第一条 send 后停止",
                "evaluations": evaluations,
                "selected_task_id": decision.get("selected_task_id") or "",
                "transition_text": decision.get("transition_text") or "",
            }
        )
    else:
        preflight["nodes"].append(
            {
                "node": "sequence_decision",
                "status": "passed",
                "conclusion": str(result.get("status") or "completed"),
            }
        )
    if send_calls:
        preflight["nodes"].append(
            {
                "node": "local_send_interceptor",
                "status": "passed",
                "conclusion": "客户消息未发送；仅在本地记录本应发送的内容",
                "would_send": [item.get("reply_messages") or [] for item in send_calls],
            }
        )
    preflight["nodes"].append(
        {
            "node": "consume_callbacks",
            "status": "passed",
            "conclusion": "平台消费回传已逐任务执行" if consume_calls else "没有产生消费回传",
            "calls": [
                {
                    "task_id": item.get("task_id"),
                    "status": item.get("status"),
                    "remark": item.get("remark"),
                }
                for item in consume_calls
            ],
        }
    )
    preflight["batch_result"] = result
    preflight["final_status"] = str(result.get("status") or "completed")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings()
    if settings.sop_platform_base_url.rstrip("/") != TEST_BASE_URL:
        raise RuntimeError(f"refusing non-test SOP platform: {settings.sop_platform_base_url}")
    if not settings.sop_platform_token:
        raise RuntimeError("SOP_PLATFORM_TOKEN is required")
    if not settings.outreach_system_token:
        raise RuntimeError("OUTREACH_SYSTEM_TOKEN is required")
    settings.sop_platform_shadow_mode = not args.execute_consume
    settings.sop_platform_quiet_hours_enabled = False

    platform_client = SopPlatformClient(settings)
    conversation_client = OutreachSystemClient(settings)
    model_client = ModelClient(settings)
    platform_agent_client = PlatformAgentClient(settings)
    customer_context_service = CustomerContextService(platform_agent_client)
    try:
        online_page, store_page = await _fetch_pending_pages(platform_client)
        pages = [online_page, store_page]
        if any(not page.get("complete") for page in pages):
            raise RuntimeError("pending_page_incomplete")
        tasks, unresolved_content_triggers = _resolve_compatible_pending_tasks(
            online_page.get("items") if isinstance(online_page.get("items"), list) else [],
            store_page.get("items") if isinstance(store_page.get("items"), list) else [],
        )
        tasks.sort(key=_task_batch_sort_key)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            grouped.setdefault(_customer_batch_key(task), []).append(task)

        batch_reports: list[dict[str, Any]] = []
        runnable: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
        for batch_tasks in grouped.values():
            batch_tasks.sort(key=_task_batch_sort_key)
            report = await _preflight_batch(batch_tasks, conversation_client=conversation_client)
            batch_reports.append(report)
            if report.get("runnable"):
                runnable.append((batch_tasks, report))

        terminal_count = 0
        for batch_tasks, report in runnable:
            if terminal_count >= max(1, args.max_terminal_tasks):
                report["final_status"] = "not_selected_after_limit"
                report["nodes"].append(
                    {
                        "node": "test_selection",
                        "status": "skipped",
                        "conclusion": "本次已达到目标消费数量，整个客户批次保留待消费",
                    }
                )
                continue
            repository = AuditRepository()
            recording_platform = RecordingTestPlatform(platform_client)
            local_system = RealConversationLocalSendSystem(conversation_client)
            service = SopPlatformTaskService(
                settings=settings,
                repository=repository,
                platform_client=recording_platform,
                system_client=local_system,
                model_client=model_client,
                customer_context_service=customer_context_service,
            )
            batch_result: dict[str, Any] | None = None
            error = ""
            try:
                batch_result = await service.process_customer_batch(
                    {
                        "_aics_customer_batch": True,
                        "batch_key": report["batch_key"],
                        "biz_type": report["biz_type"],
                        "tasks": batch_tasks,
                        "compat_trigger_tasks": _batch_compat_trigger_tasks({"tasks": batch_tasks}),
                    }
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            _append_runtime_nodes(
                report,
                batch_result=batch_result,
                consume_calls=recording_platform.consume_calls,
                send_calls=local_system.send_calls,
                error=error,
            )
            terminal_count += len((batch_result or {}).get("terminal_task_ids") or [])

        task_reports = []
        batch_by_task = {
            task_id: report
            for report in batch_reports
            for task_id in report.get("task_ids") or []
        }
        for task in tasks:
            task_id = _task_id(task)
            batch = batch_by_task.get(task_id) or {}
            task_reports.append(
                {
                    **_task_row(task),
                    "batch_key": batch.get("batch_key"),
                    "runnable": batch.get("runnable"),
                    "block_reason": batch.get("block_reason"),
                    "final_status": batch.get("final_status") or "blocked_before_consume",
                    "nodes": batch.get("nodes") or [],
                }
            )
        return {
            "schema_version": "sop_test_live_no_send_v1",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "mode": "execute_consume_local_send" if args.execute_consume else "preview_no_consume",
            "platform_base_url": TEST_BASE_URL,
            "external_send_mode": "disabled_local_interceptor",
            "queue": {
                "online_service": online_page.get("total"),
                "store_visit": store_page.get("total"),
                "unresolved_content_triggers": len(unresolved_content_triggers),
                "total": len(tasks),
            },
            "summary": {
                "batch_count": len(batch_reports),
                "runnable_batch_count": sum(1 for item in batch_reports if item.get("runnable")),
                "blocked_batch_count": sum(1 for item in batch_reports if not item.get("runnable")),
                "terminal_consumed_task_count": terminal_count,
                "external_send_count": 0,
                "local_would_send_count": sum(
                    1
                    for item in batch_reports
                    for node in item.get("nodes") or []
                    if node.get("node") == "local_send_interceptor"
                ),
            },
            "block_reason_counts": _count_values(
                str(item.get("block_reason") or "")
                for item in batch_reports
                if item.get("block_reason")
            ),
            "batches": batch_reports,
            "tasks": task_reports,
        }
    finally:
        await platform_client.aclose()
        await conversation_client.aclose()
        await model_client.aclose()


def _count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def write_report(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = result["summary"]
    lines = [
        "# SOP 测试队列真实会话联调报告",
        "",
        f"- 模式：`{result['mode']}`",
        f"- 测试平台：`{result['platform_base_url']}`",
        "- 客户消息发送：已禁用，所有 send 均由本地拦截器接管",
        f"- 队列任务：`{result['queue']['total']}`（线上客服 {result['queue']['online_service']}，门店回访 {result['queue']['store_visit']}）",
        f"- 可执行客户批次：`{summary['runnable_batch_count']}`",
        f"- 被阻断客户批次：`{summary['blocked_batch_count']}`",
        f"- 实际消费任务：`{summary['terminal_consumed_task_count']}`",
        f"- 实际外发客户消息：`{summary['external_send_count']}`",
        "",
        "## 阻断统计",
        "",
        "```json",
        json.dumps(result["block_reason_counts"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 逐任务结果",
        "",
        "| 队列 | task_id | 客户ID | 客服账号 | 时间 | 规则 | 最终状态 | 阻断原因 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for task in result["tasks"]:
        identity = task.get("identity") or {}
        lines.append(
            f"| {task.get('biz_type') or '-'} | {task.get('task_id') or '-'} | "
            f"{identity.get('customer_id') or '-'} | {identity.get('wechat') or '-'} | "
            f"{task.get('scheduled_at') or '-'} | {str(task.get('rule_name') or '-').replace('|', '/')} | "
            f"{task.get('final_status') or '-'} | {task.get('block_reason') or '-'} |"
        )
    lines.extend(["", "## 逐客户批次节点判断", ""])
    for index, batch in enumerate(result["batches"], start=1):
        lines.extend(
            [
                f"### {index}. {batch.get('biz_type')} / {', '.join(batch.get('task_ids') or [])}",
                "",
                f"- 客户：`{(batch.get('identity') or {}).get('customer_id') or '-'}`",
                f"- 客服账号：`{(batch.get('identity') or {}).get('wechat') or '-'}`",
                f"- 是否可执行：`{bool(batch.get('runnable'))}`",
                f"- 最终状态：`{batch.get('final_status') or 'blocked_before_consume'}`",
                "",
            ]
        )
        for node in batch.get("nodes") or []:
            lines.append(
                f"- **{node.get('node')}** [{node.get('status')}]: {node.get('conclusion') or ''}"
            )
            if node.get("evaluations"):
                lines.extend(
                    [
                        "",
                        "```json",
                        json.dumps(node["evaluations"], ensure_ascii=False, indent=2),
                        "```",
                    ]
                )
        lines.append("")
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run real test pending queues with real conversation lookup and local-only send interception."
    )
    parser.add_argument("--execute-consume", action="store_true")
    parser.add_argument("--max-terminal-tasks", type=int, default=10)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    write_report(result, Path(args.output))
    print(
        json.dumps(
            {
                "output": args.output,
                "mode": result["mode"],
                "queue_total": result["queue"]["total"],
                **result["summary"],
                "block_reason_counts": result["block_reason_counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
