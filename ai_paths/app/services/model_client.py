from __future__ import annotations

import json
import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar

import httpx

from app.config import Settings
from app.services import model_response, model_selection


ModelTier = Literal["fast", "planner", "balanced", "strong", "reply", "vision"]
T = TypeVar("T")


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
        models = self._model_names(tier)

        def build_payload(model: str) -> dict[str, Any]:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            self._apply_max_tokens(payload, json_mode=False)
            self._apply_relay_reasoning(payload, json_mode=False)
            return payload

        async def consume(raw: dict[str, Any]) -> str:
            return self._extract_text(raw)

        return await self._run_with_transient_retries(
            lambda: self._run_model_candidates(
                models,
                tier=tier,
                build_payload=build_payload,
                consume=consume,
                failure_label="All model candidates failed",
            )
        )

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: ModelTier = "balanced",
        temperature: float = 0.1,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("No model API key configured")
        models = self._model_names(tier)

        def build_payload(model: str) -> dict[str, Any]:
            payload = {
                "model": model,
                "messages": self._ensure_json_marker(messages),
                "temperature": temperature,
            }
            self._apply_max_tokens(payload, json_mode=True)
            if self.settings.model_response_format_enabled:
                payload["response_format"] = {"type": "json_object"}
            self._apply_relay_reasoning(payload, json_mode=True)
            if self.settings.model_provider.lower() == "aliyun":
                payload["enable_thinking"] = False
            return payload

        async def consume(raw: dict[str, Any]) -> dict[str, Any]:
            return self._parse_json(self._extract_text(raw))

        return await self._run_with_transient_retries(
            lambda: self._run_model_candidates(
                models,
                tier=tier,
                build_payload=build_payload,
                consume=consume,
                failure_label="All JSON model candidates failed",
                deadline_monotonic=deadline_monotonic,
            ),
            deadline_monotonic=deadline_monotonic,
        )

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
        models = self._model_names(tier)

        def build_payload(model: str) -> dict[str, Any]:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            self._apply_max_tokens(payload, json_mode=True)
            self._apply_relay_reasoning(payload, json_mode=True)
            return payload

        async def consume(raw: dict[str, Any]) -> dict[str, Any]:
            return self._parse_json(self._extract_text(raw))

        return await self._run_with_transient_retries(
            lambda: self._run_model_candidates(
                models,
                tier=tier,
                build_payload=build_payload,
                consume=consume,
                failure_label="All vision model candidates failed",
            )
        )

    async def _run_with_transient_retries(
        self,
        invoke: Callable[[], Awaitable[T]],
        *,
        deadline_monotonic: float | None = None,
    ) -> T:
        """Retry provider and format failures without changing business decisions."""
        attempts = max(1, int(self.settings.model_request_retry_attempts or 1))
        retry_delay = max(0.0, float(self.settings.model_request_retry_delay_seconds or 0.0))
        retry_errors: list[str] = []

        for attempt in range(1, attempts + 1):
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise TimeoutError("model request deadline exhausted")
            try:
                result = await invoke()
            except Exception as exc:
                retry_errors.append(f"{type(exc).__name__}: {exc}")
                usage = self.last_usage if isinstance(self.last_usage, dict) else {}
                usage.update(
                    {
                        "request_attempt": attempt,
                        "request_retry_errors": list(retry_errors),
                    }
                )
                self.last_usage = usage
                if (
                    attempt >= attempts
                    or not self._is_retryable_request_error(exc)
                    or (deadline_monotonic is not None and time.monotonic() >= deadline_monotonic)
                ):
                    raise
                if retry_delay:
                    delay = retry_delay
                    if deadline_monotonic is not None:
                        delay = min(delay, max(0.0, deadline_monotonic - time.monotonic()))
                    if delay:
                        await asyncio.sleep(delay)
                continue

            usage = self.last_usage if isinstance(self.last_usage, dict) else {}
            usage.update(
                {
                    "request_attempt": attempt,
                    "request_retry_errors": list(retry_errors),
                }
            )
            self.last_usage = usage
            return result

        raise RuntimeError("model retry loop exited unexpectedly")

    async def _run_model_candidates(
        self,
        models: list[str],
        *,
        tier: ModelTier,
        build_payload: Callable[[str], dict[str, Any]],
        consume: Callable[[dict[str, Any]], Awaitable[T]],
        failure_label: str,
        deadline_monotonic: float | None = None,
    ) -> T:
        if not models:
            raise RuntimeError(f"{failure_label}: no model candidates")
        self.last_usage = None
        errors: list[str] = []
        pending: dict[asyncio.Task[T], tuple[int, str]] = {}
        next_index = 0
        max_parallel = max(1, min(2, int(self.settings.model_hedge_max_parallel or 1)))
        hedge_delay = self._hedge_delay_for_tier(tier)
        configured_total_timeout = self._total_timeout_for_tier(tier)
        started_at = time.perf_counter()
        deadline = started_at + configured_total_timeout
        if deadline_monotonic is not None:
            deadline = min(deadline, deadline_monotonic)
        total_timeout = max(0.0, deadline - started_at)
        candidate_models = list(models)
        started_models: list[str] = []
        hedge_started = False

        def enrich_last_usage() -> None:
            usage = self.last_usage if isinstance(self.last_usage, dict) else {}
            usage.update(
                {
                    "candidate_models": candidate_models,
                    "started_models": list(started_models),
                    "hedge_started": bool(hedge_started),
                    "total_timeout_seconds": total_timeout,
                    "configured_total_timeout_seconds": configured_total_timeout,
                    "deadline_limited": deadline_monotonic is not None and total_timeout < configured_total_timeout,
                    "overall_duration_ms": int((time.perf_counter() - started_at) * 1000),
                }
            )
            self.last_usage = usage

        def record_failure(error: str) -> None:
            pending_models = [
                model
                for task, (_, model) in pending.items()
                if not task.done()
            ]
            self.last_usage = {
                "provider": self.settings.model_provider,
                "model": started_models[-1] if started_models else candidate_models[0],
                "tier": tier,
                "fallback_index": max(0, len(started_models) - 1),
                "fallback_errors": list(errors),
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "overall_duration_ms": int((time.perf_counter() - started_at) * 1000),
                "usage": {},
                "candidate_models": candidate_models,
                "started_models": list(started_models),
                "pending_models": pending_models,
                "hedge_started": bool(hedge_started),
                "total_timeout_seconds": total_timeout,
                "configured_total_timeout_seconds": configured_total_timeout,
                "deadline_limited": deadline_monotonic is not None and total_timeout < configured_total_timeout,
                "error": error,
            }

        async def run_one(index: int, model: str) -> T:
            payload = build_payload(model)
            raw = await self._post_chat(payload, tier=tier, fallback_index=index, errors=list(errors))
            return await consume(raw)

        def launch() -> None:
            nonlocal next_index, hedge_started
            if next_index >= len(models):
                return
            model = models[next_index]
            started_models.append(model)
            if len(started_models) > 1:
                hedge_started = True
            task = asyncio.create_task(run_one(next_index, model))
            pending[task] = (next_index, model)
            next_index += 1

        launch()
        last_launch_at = time.perf_counter()
        try:
            while pending:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    error = f"TimeoutError: total timeout {total_timeout:.1f}s"
                    record_failure(error)
                    raise TimeoutError(f"total timeout {total_timeout:.1f}s")
                should_hedge = next_index < len(models) and len(pending) < max_parallel
                wait_timeout = remaining
                if should_hedge:
                    elapsed = time.perf_counter() - last_launch_at
                    wait_timeout = min(wait_timeout, max(0.0, hedge_delay - elapsed))
                done, _ = await asyncio.wait(pending.keys(), timeout=wait_timeout, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    if should_hedge:
                        launch()
                        last_launch_at = time.perf_counter()
                    continue
                for task in done:
                    index, model = pending.pop(task)
                    try:
                        result = await task
                    except Exception as exc:
                        errors.append(f"{model}: {type(exc).__name__}: {exc}")
                        if not self._should_try_next_model(exc):
                            record_failure(f"{type(exc).__name__}: {exc}")
                            raise RuntimeError(f"{failure_label}: " + " | ".join(errors)) from exc
                        while next_index < len(models) and len(pending) < max_parallel:
                            launch()
                            last_launch_at = time.perf_counter()
                        continue
                    for other in pending:
                        other.cancel()
                    if pending:
                        await asyncio.gather(*pending.keys(), return_exceptions=True)
                    enrich_last_usage()
                    return result
            record_failure(f"{failure_label}: " + " | ".join(errors))
            raise RuntimeError(f"{failure_label}: " + " | ".join(errors))
        except Exception:
            if not isinstance(self.last_usage, dict):
                record_failure(f"{failure_label}: " + " | ".join(errors))
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending.keys(), return_exceptions=True)
            raise

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
        started = time.perf_counter()
        response = await client.post(url, headers=headers, content=body)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:800]
            raise RuntimeError(f"Model HTTP {response.status_code}: {detail}") from exc
        raw = response.json()
        duration_ms = int((time.perf_counter() - started) * 1000)
        self.last_usage = {
            "provider": self.settings.model_provider,
            "model": payload.get("model"),
            "tier": tier,
            "fallback_index": fallback_index,
            "fallback_errors": list(errors),
            "duration_ms": duration_ms,
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
        started = time.perf_counter()
        response = await client.post(url, headers=headers, content=body)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:800]
            raise RuntimeError(f"Model HTTP {response.status_code}: {detail}") from exc
        raw = self._normalize_anthropic_response(response.json())
        duration_ms = int((time.perf_counter() - started) * 1000)
        self.last_usage = {
            "provider": self.settings.model_provider,
            "protocol": "anthropic",
            "model": payload.get("model"),
            "tier": tier,
            "fallback_index": fallback_index,
            "fallback_errors": list(errors),
            "duration_ms": duration_ms,
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
        reasoning = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else {}
        if reasoning:
            result["reasoning"] = reasoning
        return result

    def _apply_relay_reasoning(self, payload: dict[str, Any], *, json_mode: bool) -> None:
        provider = self.settings.model_provider.lower()
        if provider not in {"relay", "openai_compatible", "openai-compatible"}:
            return
        if not self.settings.model_relay_reasoning_control_enabled:
            return
        if json_mode and not self.settings.model_json_reasoning_enabled:
            payload["reasoning"] = {"enabled": False}
            return
        if not json_mode and not self.settings.model_reasoning_enabled:
            payload["reasoning"] = {"enabled": False}
            return
        if self.settings.model_reasoning_enabled:
            reasoning: dict[str, Any] = {"enabled": True}
            effort = str(self.settings.model_reasoning_effort or "").strip()
            if effort:
                reasoning["effort"] = effort
            max_tokens = int(self.settings.model_reasoning_max_tokens or 0)
            if max_tokens > 0:
                reasoning["max_tokens"] = max_tokens
            payload["reasoning"] = reasoning

    def _apply_max_tokens(self, payload: dict[str, Any], *, json_mode: bool) -> None:
        if json_mode:
            max_tokens = int(self.settings.model_json_max_tokens or self.settings.model_max_tokens or 0)
        else:
            max_tokens = int(self.settings.model_text_max_tokens or self.settings.model_max_tokens or 0)
        if max_tokens > 0:
            payload["max_tokens"] = max_tokens

    def _total_timeout_for_tier(self, tier: ModelTier) -> float:
        if tier == "planner":
            return max(1.0, float(self.settings.model_planner_total_timeout_seconds or self.settings.model_timeout_seconds))
        if tier in {"reply", "strong"}:
            return max(1.0, float(self.settings.model_reply_total_timeout_seconds or self.settings.model_timeout_seconds))
        return max(1.0, float(self.settings.model_timeout_seconds))

    def _hedge_delay_for_tier(self, tier: ModelTier) -> float:
        if tier == "planner":
            return max(0.0, float(self.settings.model_planner_hedge_delay_seconds or 0.0))
        return max(0.0, float(self.settings.model_hedge_delay_seconds or 0.0))

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
    def _is_retryable_request_error(exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, json.JSONDecodeError, httpx.TimeoutException, httpx.NetworkError)):
            return True
        message = str(exc).lower()
        if "jsondecodeerror" in message or "total timeout" in message:
            return True
        return any(f"http {status}" in message for status in (429, 500, 502, 503, 504))

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
