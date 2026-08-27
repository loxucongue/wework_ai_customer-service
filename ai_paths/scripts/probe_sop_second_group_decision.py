from __future__ import annotations

import argparse
import asyncio
import json
import sys
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
    _task_batch_sort_key,
    _task_id,
)


TEST_BASE_URL = "https://test.api.customer.4ba.cn"
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


class UnusedDependency:
    pass


def _first_text(task: dict[str, Any]) -> str:
    for message in task.get("message_content") or []:
        if isinstance(message, dict) and str(message.get("type") or "") == "text":
            return str(message.get("content") or "")
    return ""


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings()
    if settings.sop_platform_base_url.rstrip("/") != TEST_BASE_URL:
        raise RuntimeError("probe only supports the SOP test environment")
    platform = SopPlatformClient(settings)
    model = ModelClient(settings)
    try:
        page = await platform.pending(limit=500)
        requested = {item.strip() for item in args.task_ids.split(",") if item.strip()}
        if len(requested) != 2:
            raise RuntimeError("exactly two task IDs are required")
        tasks = [dict(item) for item in page.get("items") or [] if _task_id(item) in requested]
        if len(tasks) != 2:
            raise RuntimeError("probe tasks are no longer pending")
        tasks.sort(key=_task_batch_sort_key)
        now = datetime.now(BEIJING_TZ)
        for index, task in enumerate(tasks):
            task["scheduledAt"] = (now - timedelta(minutes=2 - index)).strftime("%Y-%m-%d %H:%M:%S")
        context = {
            "conversation_timeline": [
                {
                    "message_ref": "msg_001",
                    "role": "assistant",
                    "message_type": "text",
                    "content": _first_text(tasks[0]),
                },
                {
                    "message_ref": "msg_002",
                    "role": "customer",
                    "message_type": "text",
                    "content": "第一条到店提醒我已经收到了，第二条关于主任今天到店的消息请发给我。",
                },
            ],
            "timeline_structure": {
                "customer_message_count": 1,
                "assistant_message_count": 1,
                "last_message_role": "customer",
            },
            "customer_relation": {"status": "active", "is_deleted": False},
            "business_state": {},
        }
        service = SopPlatformTaskService(
            settings=settings,
            repository=UnusedDependency(),
            platform_client=platform,
            system_client=UnusedDependency(),
            model_client=model,
            customer_context_service=UnusedDependency(),
        )
        results = []
        for _ in range(max(1, args.repeats)):
            results.append(await service._decide_customer_batch(tasks, context=context))
        expected = _task_id(tasks[1])
        return {
            "task_ids": [_task_id(task) for task in tasks],
            "expected_selected_task_id": expected,
            "results": results,
            "passed": all(str(item.get("selected_task_id") or "") == expected for item in results),
        }
    finally:
        await platform.aclose()
        await model.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only repeated model probe for selecting the second SOP group.")
    parser.add_argument("--task-ids", default="1271,1272")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "passed": result["passed"]}, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
