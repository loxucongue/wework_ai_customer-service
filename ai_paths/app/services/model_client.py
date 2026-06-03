from __future__ import annotations

import json
import re
from typing import Any, Literal

import httpx

from app.config import Settings


ModelTier = Literal["fast", "balanced", "strong", "vision"]


class ModelClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_usage: dict[str, Any] | None = None

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
        text = await self.chat_text(messages, tier=tier, temperature=temperature)
        return self._parse_json(text)

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
        async with httpx.AsyncClient(timeout=self.settings.model_timeout_seconds) as client:
            response = await client.post(url, headers=headers, content=body)
            response.raise_for_status()
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

    def _api_key(self) -> str:
        provider = self.settings.model_provider.lower()
        if provider == "volcengine":
            return self.settings.volcengine_ark_api_key
        return self.settings.aliyun_dashscope_api_key

    def _base_url(self) -> str:
        provider = self.settings.model_provider.lower()
        if provider == "volcengine":
            return self.settings.volcengine_openai_base_url
        return self.settings.aliyun_openai_base_url

    def _model_name(self, tier: ModelTier) -> str:
        if tier == "fast":
            return self.settings.model_fast
        if tier == "strong":
            return self.settings.model_strong
        if tier == "vision":
            return self.settings.model_vision
        return self.settings.model_balanced

    def _model_names(self, tier: ModelTier) -> list[str]:
        primary = self._model_name(tier)
        if tier == "fast":
            fallback_text = self.settings.model_fast_fallbacks
        elif tier == "strong":
            fallback_text = self.settings.model_strong_fallbacks
        elif tier == "vision":
            fallback_text = self.settings.model_vision_fallbacks
        else:
            fallback_text = self.settings.model_balanced_fallbacks
        models = [primary]
        for name in self._split_models(fallback_text):
            if name and name not in models:
                models.append(name)
        return models

    @staticmethod
    def _split_models(value: str) -> list[str]:
        return [item.strip() for item in (value or "").split(",") if item.strip()]

    @staticmethod
    def _should_try_next_model(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            if exc.response.status_code in {400, 401, 403, 404, 429, 500, 502, 503, 504}:
                return True
        return True

    @staticmethod
    def _extract_text(raw: dict[str, Any]) -> str:
        choices = raw.get("choices") or []
        if not choices:
            raise RuntimeError("Model response has no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if text:
                        parts.append(str(text))
            return "\n".join(parts).strip()
        return str(content or "").strip()

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
            stripped = re.sub(r"```$", "", stripped).strip()
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else {"output": parsed}
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", stripped, re.S)
            if not match:
                raise
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {"output": parsed}
