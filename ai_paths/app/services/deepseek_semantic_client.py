from __future__ import annotations

import asyncio
import copy
import json
import time
from contextvars import ContextVar
from typing import Any
from urllib.parse import urljoin

import httpx

from app.config import Settings
from app.services.model_client import ModelClient


_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class DeepSeekSemanticClient:
    """V3-only semantic JSON client with the existing OpenAI relay as fallback."""

    _JSON_MARKER = "Return valid json only."

    def __init__(self, settings: Settings, fallback_client: ModelClient | None) -> None:
        self.settings = settings
        self.fallback_client = fallback_client
        self._api_key = str(settings.deepseek_api_key or "").strip()
        self._base_url = str(settings.deepseek_api_base_url or "").rstrip("/") + "/"
        self._model = str(settings.deepseek_semantic_model or "deepseek-v4-flash").strip()
        self._timeout = max(1.0, float(settings.deepseek_semantic_timeout_seconds or 10.0))
        self._max_tokens = max(100, int(settings.deepseek_semantic_max_tokens or 800))
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int | None = None
        self._last_usage: ContextVar[dict[str, Any] | None] = ContextVar(
            f"deepseek_semantic_last_usage_{id(self)}",
            default=None,
        )

    @property
    def available(self) -> bool:
        return bool(self._api_key or (self.fallback_client and self.fallback_client.available))

    @property
    def last_usage(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._last_usage.get())

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def chat_json(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        started = time.perf_counter()
        direct_error = ""
        if self._api_key:
            try:
                result = await self._direct_json(messages)
                usage = dict(self._last_usage.get() or {})
                usage.update({"fallback_used": False, "overall_duration_ms": int((time.perf_counter() - started) * 1000)})
                self._last_usage.set(usage)
                return result
            except Exception as exc:
                direct_error = f"{type(exc).__name__}: {exc}"[:500]

        if self.fallback_client is None or not self.fallback_client.available:
            raise RuntimeError(direct_error or "No semantic model provider configured")
        deadline = time.monotonic() + self._timeout
        result = await self.fallback_client.chat_json(
            _json_messages(messages, self._JSON_MARKER),
            tier="fast",
            temperature=0.0,
            deadline_monotonic=deadline,
            max_parallel_candidates=1,
        )
        usage = copy.deepcopy(self.fallback_client.last_usage or {})
        usage.update(
            {
                "semantic_provider": "openai_relay_fallback",
                "fallback_used": True,
                "direct_error": direct_error,
                "overall_duration_ms": int((time.perf_counter() - started) * 1000),
            }
        )
        self._last_usage.set(usage)
        return result

    async def _direct_json(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self._model,
            "messages": _json_messages(messages, self._JSON_MARKER),
            "temperature": 0,
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        errors: list[str] = []
        for attempt in range(2):
            try:
                response = await self._http_client().post(
                    urljoin(self._base_url, "chat/completions"),
                    headers=headers,
                    json=payload,
                )
                if response.status_code in _RETRYABLE_STATUS_CODES and attempt == 0:
                    errors.append(f"http_status:{response.status_code}")
                    await asyncio.sleep(0.2)
                    continue
                response.raise_for_status()
                body = response.json()
                text = _response_text(body)
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise ValueError("semantic JSON output must be an object")
                self._last_usage.set(
                    {
                        "semantic_provider": "deepseek_official",
                        "model": self._model,
                        "attempts": attempt + 1,
                        "retry_errors": errors,
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "usage": copy.deepcopy(body.get("usage") or {}),
                    }
                )
                return parsed
            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.ConnectError,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
                httpx.PoolTimeout,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                errors.append(f"{type(exc).__name__}: {exc}"[:300])
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                raise
        raise RuntimeError("DeepSeek semantic request failed")

    def _http_client(self) -> httpx.AsyncClient:
        loop_id = id(asyncio.get_running_loop())
        if self._client is None or self._client.is_closed or self._client_loop_id != loop_id:
            self._client = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
            self._client_loop_id = loop_id
        return self._client


def _json_messages(messages: list[dict[str, Any]], marker: str) -> list[dict[str, Any]]:
    output = copy.deepcopy(messages)
    if not any("json" in str(item.get("content") or "").lower() for item in output if isinstance(item, dict)):
        output.insert(0, {"role": "system", "content": marker})
    return output


def _response_text(body: dict[str, Any]) -> str:
    choices = body.get("choices") if isinstance(body.get("choices"), list) else []
    if not choices or not isinstance(choices[0], dict):
        raise ValueError("DeepSeek response has no choices")
    message = choices[0].get("message") if isinstance(choices[0].get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    raise ValueError("DeepSeek response content is empty")
