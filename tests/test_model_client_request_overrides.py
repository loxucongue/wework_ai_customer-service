from __future__ import annotations

import asyncio
from types import MethodType
from typing import Any

import pytest

from app.config import Settings
from app.services.model_client import ModelClient


def test_chat_json_honors_sop_model_and_transport_overrides() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="relay",
        model_relay_api_key="default-key",
        model_relay_base_url="https://default.invalid/v1",
        model_request_retry_attempts=1,
    )
    client = ModelClient(settings)
    captured: list[dict[str, Any]] = []

    async def fake_post_chat(
        _self: ModelClient,
        payload: dict[str, Any],
        **options: Any,
    ) -> dict[str, Any]:
        captured.append({"payload": payload, **options})
        return {
            "choices": [{"message": {"content": '{"decision":"send"}'}}],
            "usage": {},
        }

    client._post_chat = MethodType(fake_post_chat, client)  # type: ignore[method-assign]
    result = asyncio.run(
        client.chat_json(
            [{"role": "user", "content": "decide"}],
            model_names_override=["deepseek-v4-flash"],
            api_key_override="sop-key",
            base_url_override="https://api.deepseek.com",
            request_body_overrides={"thinking": {"type": "disabled"}},
            max_parallel_candidates=1,
        )
    )

    assert result == {"decision": "send"}
    assert len(captured) == 1
    assert captured[0]["payload"]["model"] == "deepseek-v4-flash"
    assert captured[0]["payload"]["thinking"] == {"type": "disabled"}
    assert captured[0]["api_key_override"] == "sop-key"
    assert captured[0]["base_url_override"] == "https://api.deepseek.com"


def test_chat_json_rejects_an_empty_model_override() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="relay",
        model_relay_api_key="default-key",
        model_request_retry_attempts=1,
    )
    client = ModelClient(settings)

    with pytest.raises(RuntimeError, match="no model candidates"):
        asyncio.run(client.chat_json([], model_names_override=[]))
