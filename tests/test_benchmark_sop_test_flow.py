from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths" / "scripts"))

from benchmark_sop_test_flow import benchmark


def test_benchmark_executes_terminal_flow_in_order() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        body = json.loads(request.content)
        if request.url.path.endswith("/pending"):
            return httpx.Response(
                200,
                json={"code": 200, "data": {"list": [{"taskId": 101, "userWechatId": "TEST001"}]}},
            )
        if request.url.path.endswith("/consume"):
            assert body == {
                "taskId": "101",
                "status": 70,
                "remark": "测试环境耗时基准：人工接管，任务消费不发送",
            }
            return httpx.Response(200, json={"code": 200, "data": {"status": 70}})
        assert body["sceneCode"] == "humantakeover"
        assert body["sendStatus"] == 20
        return httpx.Response(200, json={"code": 200, "data": {}})

    result = asyncio.run(
        benchmark(
            base_url="https://test.api.customer.example",
            token="test-token",
            limit=1,
            account="TEST001",
            timeout=5,
            execute=True,
            transport=httpx.MockTransport(handler),
        )
    )

    assert result["status"] == "completed_without_send"
    assert paths == [
        "/event/trigger/pending",
        "/event/trigger/consume",
        "/event/trigger/service-rule-data",
    ]
    assert set(result["timings_ms"]) == {"pending_ms", "consume_ms", "rule_data_ms", "total_ms"}


def test_benchmark_refuses_production_and_protected_accounts() -> None:
    with pytest.raises(RuntimeError, match="non-test"):
        asyncio.run(
            benchmark(
                base_url="https://api.customer.4ba.cn",
                token="token",
                limit=1,
                account="TEST001",
                timeout=5,
                execute=False,
            )
        )
    with pytest.raises(RuntimeError, match="protected"):
        asyncio.run(
            benchmark(
                base_url="https://test.api.customer.4ba.cn",
                token="token",
                limit=1,
                account="SL0906",
                timeout=5,
                execute=False,
            )
        )
