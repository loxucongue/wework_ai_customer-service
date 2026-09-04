from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx


PROTECTED_ACCOUNTS = {"SL0906", "DY8808", "SL1580", "SL2478", "SL8004"}
TEST_HOST_MARKERS = ("test", "staging", "stage", "dev", "localhost", "127.0.0.1")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark one test-environment SOP terminal flow.")
    parser.add_argument("--base-url", default=os.getenv("SOP_PLATFORM_BASE_URL", ""))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--account", default="", help="Only select a task for this non-protected account.")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--execute", action="store_true", help="Actually consume and report the selected task.")
    return parser.parse_args()


def _require_test_environment(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    host = (urlparse(normalized).hostname or "").lower()
    if not normalized or not any(marker in host for marker in TEST_HOST_MARKERS):
        raise RuntimeError(f"refusing non-test SOP host: {host or '<empty>'}")
    return normalized


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("list", "items", "records", "tasks"):
        values = data.get(key)
        if isinstance(values, list):
            return [item for item in values if isinstance(item, dict)]
    return []


def _task_id(task: dict[str, Any]) -> str:
    return str(task.get("taskId") or task.get("task_id") or task.get("id") or "").strip()


def _account(task: dict[str, Any]) -> str:
    return str(
        task.get("userWechatId")
        or task.get("user_wechat_id")
        or task.get("userWechat")
        or task.get("user_wechat")
        or task.get("wechat")
        or ""
    ).strip()


def _ok(payload: dict[str, Any]) -> bool:
    return payload.get("code") in (None, 0, "0", 200, "200")


async def benchmark(
    *,
    base_url: str,
    token: str,
    limit: int,
    account: str,
    timeout: float,
    execute: bool,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    base_url = _require_test_environment(base_url)
    if not token:
        raise RuntimeError("SOP_PLATFORM_TOKEN is required")
    requested_account = account.strip().upper()
    if requested_account in PROTECTED_ACCOUNTS:
        raise RuntimeError(f"refusing protected account: {requested_account}")
    headers = {"x-event-token": token, "Content-Type": "application/json; charset=utf-8"}
    timings: dict[str, float] = {}
    total_started = time.perf_counter()
    client_started = time.perf_counter()
    async with httpx.AsyncClient(timeout=max(1.0, timeout), transport=transport) as client:
        timings["client_setup_ms"] = (time.perf_counter() - client_started) * 1000
        started = time.perf_counter()
        pending_response = await client.post(
            f"{base_url}/event/trigger/pending",
            headers=headers,
            json={"corp_id": "", "wechat": requested_account, "limit": max(1, min(limit, 500))},
        )
        timings["pending_ms"] = (time.perf_counter() - started) * 1000
        pending_response.raise_for_status()
        pending_payload = pending_response.json()
        if not isinstance(pending_payload, dict) or not _ok(pending_payload):
            raise RuntimeError(f"pending failed: {pending_payload}")
        candidates = [
            task
            for task in _items(pending_payload)
            if _task_id(task)
            and _account(task).upper() not in PROTECTED_ACCOUNTS
            and (not requested_account or _account(task).upper() == requested_account)
        ]
        if not candidates:
            raise RuntimeError("test pending queue has no eligible non-protected task")
        task = candidates[0]
        task_id = _task_id(task)
        selected_account = _account(task)
        result: dict[str, Any] = {
            "environment": base_url,
            "task_id": task_id,
            "account": selected_account,
            "execute": execute,
            "timings_ms": timings,
        }
        if not execute:
            timings["total_ms"] = (time.perf_counter() - total_started) * 1000
            result["status"] = "dry_run_selected"
            return result

        started = time.perf_counter()
        consume_response = await client.post(
            f"{base_url}/event/trigger/consume",
            headers=headers,
            json={"taskId": task_id, "status": 70, "remark": "测试环境耗时基准：人工接管，任务消费不发送"},
        )
        timings["consume_ms"] = (time.perf_counter() - started) * 1000
        consume_response.raise_for_status()
        consume_payload = consume_response.json()
        if not isinstance(consume_payload, dict) or not _ok(consume_payload):
            raise RuntimeError(f"consume failed: {consume_payload}")

        started = time.perf_counter()
        rule_response = await client.post(
            f"{base_url}/event/trigger/service-rule-data",
            headers=headers,
            json={
                "taskId": task_id,
                "sceneName": "人工接管",
                "sceneCode": "humantakeover",
                "sendStatus": 20,
                "remark": "当前会话由人工接待",
                "sendContent": "",
            },
        )
        timings["rule_data_ms"] = (time.perf_counter() - started) * 1000
        rule_response.raise_for_status()
        rule_payload = rule_response.json()
        if not isinstance(rule_payload, dict) or not _ok(rule_payload):
            raise RuntimeError(f"service-rule-data failed: {rule_payload}")
        timings["total_ms"] = (time.perf_counter() - total_started) * 1000
        timings["local_overhead_ms"] = max(
            0.0,
            timings["total_ms"]
            - timings["pending_ms"]
            - timings["consume_ms"]
            - timings["rule_data_ms"],
        )
        result.update(
            {
                "status": "completed_without_send",
                "consume_code": consume_payload.get("code"),
                "rule_data_code": rule_payload.get("code"),
            }
        )
        return result


def main() -> None:
    args = _args()
    result = asyncio.run(
        benchmark(
            base_url=args.base_url,
            token=os.getenv("SOP_PLATFORM_TOKEN", ""),
            limit=args.limit,
            account=args.account,
            timeout=args.timeout,
            execute=args.execute,
        )
    )
    result["timings_ms"] = {
        key: round(value, 1) for key, value in result.get("timings_ms", {}).items()
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
