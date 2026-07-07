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
            }
            if self.settings.model_response_format_enabled and not model_selection.is_claude_model(model):
                payload["response_format"] = {"type": "json_object"}
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
        model = str(payload.get("model") or "")
        if self._uses_anthropic_messages_api(model):
            return await self._post_anthropic_messages(payload, tier=tier, fallback_index=fallback_index, errors=errors)
        url = f"{self._base_url(model).rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key(model)}",
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

    async def _post_anthropic_messages(
        self,
        payload: dict[str, Any],
        *,
        tier: ModelTier,
        fallback_index: int,
        errors: list[str],
    ) -> dict[str, Any]:
        model = str(payload.get("model") or "")
        url = self._anthropic_messages_url(model)
        headers = {
            "Authorization": f"Bearer {self._api_key(model)}",
            "anthropic-version": self.settings.anthropic_version,
            "Content-Type": "application/json; charset=utf-8",
        }
        anthropic_payload = self._anthropic_messages_payload(payload)
        body = json.dumps(anthropic_payload, ensure_ascii=False).encode("utf-8")
        client = self._http_client()
        response = await client.post(url, headers=headers, content=body)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:800]
            raise RuntimeError(f"Model HTTP {response.status_code}: {detail}") from exc
        raw = self._normalize_anthropic_response(response.json())
        self.last_usage = {
            "provider": self.settings.model_provider,
            "protocol": "anthropic",
            "model": payload.get("model"),
            "tier": tier,
            "fallback_index": fallback_index,
            "fallback_errors": list(errors),
            "usage": raw.get("usage") or {},
        }
        return raw

    def _uses_anthropic_messages_api(self, model: str | None = None) -> bool:
        if self.settings.model_provider.lower() not in {"relay", "openai_compatible", "openai-compatible"}:
            return False
        protocol = (self.settings.model_relay_protocol or "auto").strip().lower()
        if protocol == "anthropic":
            return True
        if protocol == "openai":
            return False
        if protocol == "mixed":
            return model_selection.is_claude_model(model) and bool(self.settings.anthropic_base_url)
        return bool(self.settings.anthropic_base_url and not self.settings.model_relay_base_url)

    def _anthropic_messages_url(self, model: str | None = None) -> str:
        base_url = self._anthropic_base_url(model).rstrip("/")
        if base_url.endswith("/v1"):
            return f"{base_url}/messages"
        return f"{base_url}/v1/messages"

    def _anthropic_messages_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        for item in payload.get("messages") or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user").strip().lower()
            content = item.get("content")
            if role == "system":
                text = self._anthropic_content_to_text(content)
                if text:
                    system_parts.append(text)
                continue
            anthropic_role = "assistant" if role == "assistant" else "user"
            text = self._anthropic_content_to_text(content)
            if not text:
                continue
            if messages and messages[-1].get("role") == anthropic_role:
                messages[-1]["content"] = f"{messages[-1].get('content', '')}\n{text}".strip()
            else:
                messages.append({"role": anthropic_role, "content": text})
        if not messages:
            messages.append({"role": "user", "content": "Return a brief response."})
        result: dict[str, Any] = {
            "model": payload.get("model"),
            "messages": messages,
            "max_tokens": int(self.settings.model_max_tokens),
            "temperature": payload.get("temperature", 0.1),
        }
        if system_parts:
            result["system"] = "\n\n".join(system_parts)
        return result

    @staticmethod
    def _anthropic_content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if text:
                        parts.append(str(text))
            return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
        return str(content or "").strip()

    @staticmethod
    def _normalize_anthropic_response(raw: dict[str, Any]) -> dict[str, Any]:
        parts: list[str] = []
        for item in raw.get("content") or []:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        return {
            "choices": [{"message": {"content": "\n".join(parts).strip()}}],
            "usage": raw.get("usage") or {},
            "raw_provider": "anthropic",
        }

    def _http_client(self) -> httpx.AsyncClient:
        timeout = int(self.settings.model_timeout_seconds)
        loop_id = id(asyncio.get_running_loop())
        if (
            self._client is None
            or self._client.is_closed
            or self._client_timeout != timeout
            or self._client_loop_id != loop_id
        ):
            connect_timeout = min(5.0, float(timeout))
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

    def _api_key(self, model: str | None = None) -> str:
        return model_selection.api_key(self.settings, model=model)

    def _base_url(self, model: str | None = None) -> str:
        return model_selection.base_url(self.settings)

    def _anthropic_base_url(self, model: str | None = None) -> str:
        return self.settings.anthropic_base_url or self._base_url(model)

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
