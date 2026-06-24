from __future__ import annotations

import json
import asyncio
from typing import Any, Literal

import httpx

from app.config import Settings
from app.services import model_response, model_selection


ModelTier = Literal["fast", "planner", "balanced", "strong", "reply", "vision"]


class ModelClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_usage: dict[str, Any] | None = None
        self._client: httpx.AsyncClient | None = None
        self._client_timeout: int | None = None
        self._client_loop_id: int | None = None

    @property
    def available(self) -> bool:
        return bool(self._api_key())

    async def chat_text(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: ModelTier = "balanced",
        temperature: float = 0.2,
    ) -> str:
        if not self.available:
            raise RuntimeError("No model API key configured")
        errors: list[str] = []
        for index, model in enumerate(self._model_names(tier)):
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            try:
                raw = await self._post_chat(payload, tier=tier, fallback_index=index, errors=errors)
                return self._extract_text(raw)
            except Exception as exc:
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
                if not self._should_try_next_model(exc):
                    break
        raise RuntimeError("All model candidates failed: " + " | ".join(errors))

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: ModelTier = "balanced",
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("No model API key configured")
        errors: list[str] = []
        for index, model in enumerate(self._model_names(tier)):
            payload = {
                "model": model,
                "messages": self._ensure_json_marker(messages),
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            }
            if self.settings.model_provider.lower() == "aliyun":
                payload["enable_thinking"] = False
            try:
                raw = await self._post_chat(payload, tier=tier, fallback_index=index, errors=errors)
                return self._parse_json(self._extract_text(raw))
            except Exception as exc:
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
                if isinstance(exc, json.JSONDecodeError):
                    continue
                if not self._should_try_next_model(exc):
                    break
        raise RuntimeError("All JSON model candidates failed: " + " | ".join(errors))

    async def vision_json(
        self,
        *,
        prompt: str,
        image_url: str,
        tier: ModelTier = "vision",
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("No model API key configured")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ]
        payload = {
            "model": self._model_names(tier)[0],
            "messages": messages,
            "temperature": temperature,
        }
        errors: list[str] = []
        for index, model in enumerate(self._model_names(tier)):
            payload["model"] = model
            try:
                raw = await self._post_chat(payload, tier=tier, fallback_index=index, errors=errors)
                return self._parse_json(self._extract_text(raw))
            except Exception as exc:
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
                if not self._should_try_next_model(exc):
                    break
        raise RuntimeError("All vision model candidates failed: " + " | ".join(errors))

    async def _post_chat(
        self,
        payload: dict[str, Any],
        *,
        tier: ModelTier,
        fallback_index: int,
        errors: list[str],
    ) -> dict[str, Any]:
        url = f"{self._base_url().rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        client = self._http_client()
        response = await client.post(url, headers=headers, content=body)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:800]
            raise RuntimeError(f"Model HTTP {response.status_code}: {detail}") from exc
        raw = response.json()
        self.last_usage = {
            "provider": self.settings.model_provider,
            "model": payload.get("model"),
            "tier": tier,
            "fallback_index": fallback_index,
            "fallback_errors": list(errors),
            "usage": raw.get("usage") or {},
        }
        return raw

    def _http_client(self) -> httpx.AsyncClient:
        timeout = int(self.settings.model_timeout_seconds)
        loop_id = id(asyncio.get_running_loop())
        if (
            self._client is None
            or self._client.is_closed
            or self._client_timeout != timeout
            or self._client_loop_id != loop_id
        ):
            connect_timeout = min(3.0, float(timeout))
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=connect_timeout),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
            )
            self._client_timeout = timeout
            self._client_loop_id = loop_id
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _api_key(self) -> str:
        return model_selection.api_key(self.settings)

    def _base_url(self) -> str:
        return model_selection.base_url(self.settings)

    def _model_name(self, tier: ModelTier) -> str:
        return model_selection.model_name(self.settings, tier)

    def _model_names(self, tier: ModelTier) -> list[str]:
        return model_selection.model_names(self.settings, tier)

    @staticmethod
    def _split_models(value: str) -> list[str]:
        return model_selection.split_models(value)

    @staticmethod
    def _should_try_next_model(exc: Exception) -> bool:
        return model_selection.should_try_next_model(exc)

    @staticmethod
    def _extract_text(raw: dict[str, Any]) -> str:
        return model_response.extract_text(raw)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        return model_response.parse_json(text)

    @staticmethod
    def _ensure_json_marker(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        combined = json.dumps(messages, ensure_ascii=False).lower()
        if "json" in combined:
            return messages
        marker = {"role": "system", "content": "Return valid json only."}
        return [marker, *messages]
