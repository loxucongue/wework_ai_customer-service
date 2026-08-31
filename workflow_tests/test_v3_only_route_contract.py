from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.background import BackgroundTasks

from app import main
from app.schemas import ChatRequest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("endpoint", [main.chat_info, main.reply_info])
def test_retired_reply_info_endpoints_are_gone(endpoint) -> None:
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(endpoint())
    assert exc_info.value.status_code == 410


@pytest.mark.parametrize(
    ("endpoint", "args"),
    [
        (main.chat, (ChatRequest(content="test", customer_id="test", corp_id="test"), None)),
        (main.reply, (ChatRequest(content="test", customer_id="test", corp_id="test"), BackgroundTasks(), None)),
        (main.chat_workflow_compatible, ({}, None)),
        (main.reply_workflow_compatible, ({}, BackgroundTasks(), None)),
        (main.reply_workflow_compatible_v2, ({},)),
        (main.chat_workflow_compatible_v2, ({},)),
    ],
)
def test_retired_reply_post_endpoints_are_gone(endpoint, args) -> None:
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(endpoint(*args))
    assert exc_info.value.status_code == 410


def test_deploy_config_never_proxies_retired_reply_routes() -> None:
    config = (ROOT / "deploy" / "ai-paths.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://127.0.0.1:8000/reply;" not in config
    assert "proxy_pass http://127.0.0.1:8000/reply/workflow-compatible;" not in config
    for path in (
        "/api/ai-paths/chat",
        "/api/ai/chat",
        "/api/ai/chat/workflow-compatible",
        "/api/ai/reply",
        "/api/ai/reply/workflow-compatible",
        "/api/ai/reply/workflow-compatible-v2",
    ):
        assert f"location = {path}" in config
