from __future__ import annotations

import json
import asyncio
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Literal, TypeVar

import httpx

from app.config import Settings
from app.services import model_response, model_selection


ModelTier = Literal["fast", "planner", "balanced", "strong", "reply", "vision", "store_destination"]
T = TypeVar("T")


class ModelClient:
    _JSON_MODE_MARKER = "Return valid json only."

    def __init__(self, settings: Settings):
        self.settings = settings
        self._last_usage: ContextVar[dict[str, Any] | None] = ContextVar(
            f"model_client_last_usage_{id(self)}",
            default=None,
        )
        self._client: httpx.AsyncClient | None = None
        self._client_timeout: int | None = None
        self._client_loop_id: int | None = None

    @property
    def last_usage(self) -> dict[str, Any] | None:
        return self._last_usage.get()

    @last_usage.setter
    def last_usage(self, value: dict[str, Any] | None) -> None:
        self._last_usage.set(value)

    @property
    def available(self) -> bool:
        return bool(self._api_key())

    @property
    def secondary_available(self) -> bool:
        settings = self._secondary_settings()
        if settings is None:
            return False
        return bool(model_selection.api_key(settings, model=settings.model_fast))

    async def chat_json_secondary(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        """Run one JSON attempt through an independently configured provider."""

        settings = self._secondary_settings()
        if settings is None:
            raise RuntimeError("Secondary model provider is not configured")
        client = ModelClient(settings)
        try:
            result = await client.chat_json(
                messages,
                tier="fast",
                temperature=temperature,
                deadline_monotonic=deadline_monotonic,
                max_parallel_candidates=1,
            )
            usage = dict(client.last_usage or {})
            usage.update(
                {
                    "secondary_provider": settings.model_provider,
                    "secondary_model": settings.model_fast,
                    "transport_recovery": True,
                }
            )
            self.last_usage = usage
            return result
        finally:
            await client.aclose()

    async def chat_text(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: ModelTier = "balanced",
        temperature: float = 0.2,
        deadline_monotonic: float | None = None,
    ) -> str:
        if not self.available:
            raise RuntimeError("No model API key configured")
        models = self._model_names(tier)
        request_started_at = time.monotonic()
        deadline = self._resolve_deadline(tier, deadline_monotonic)

        def build_payload(model: str) -> dict[str, Any]:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            self._apply_max_tokens(payload, json_mode=False)
            self._apply_relay_reasoning(payload, json_mode=False)
            self._apply_provider_extensions(payload)
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
                deadline_monotonic=deadline,
            ),
            deadline_monotonic=deadline,
            deadline_seconds=max(0.0, deadline - request_started_at),
        )

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: ModelTier = "balanced",
        temperature: float = 0.0,
        deadline_monotonic: float | None = None,
        max_parallel_candidates: int | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("No model API key configured")
        models = self._model_names(tier)
        request_started_at = time.monotonic()
        deadline = self._resolve_deadline(tier, deadline_monotonic)

        def build_payload(model: str, *, include_response_format: bool = True) -> dict[str, Any]:
            payload = {
                "model": model,
                "messages": self._prepare_json_messages(messages),
                "temperature": temperature,
            }
            self._apply_max_tokens(payload, json_mode=True)
            if include_response_format and self.settings.model_response_format_enabled:
                payload["response_format"] = {"type": "json_object"}
            self._apply_relay_reasoning(payload, json_mode=True)
            if self.settings.model_provider.lower() == "aliyun":
                payload["enable_thinking"] = False
            self._apply_provider_extensions(payload)
            return payload

        async def consume(raw: dict[str, Any]) -> dict[str, Any]:
            return self._parse_json(self._extract_text(raw))

        deadline_seconds = max(0.0, deadline - request_started_at)
        try:
            return await self._run_with_transient_retries(
                lambda: self._run_model_candidates(
                    models,
                    tier=tier,
                    build_payload=lambda model: build_payload(model, include_response_format=True),
                    consume=consume,
                    failure_label="All JSON model candidates failed",
                    deadline_monotonic=deadline,
                    max_parallel_candidates=max_parallel_candidates,
                ),
                deadline_monotonic=deadline,
                deadline_seconds=deadline_seconds,
            )
        except Exception as exc:
            strict_error = f"{type(exc).__name__}: {exc}"
            if (
                not self.settings.model_response_format_enabled
                or not self._should_retry_json_without_response_format(exc)
                or (deadline_monotonic is not None and time.monotonic() >= deadline_monotonic)
            ):
                raise
            try:
                result = await self._run_with_transient_retries(
                    lambda: self._run_model_candidates(
                        models,
                        tier=tier,
                        build_payload=lambda model: build_payload(model, include_response_format=False),
                        consume=consume,
                        failure_label="All JSON no-response-format model candidates failed",
                        deadline_monotonic=deadline,
                        max_parallel_candidates=max_parallel_candidates,
                    ),
                    deadline_monotonic=deadline,
                    deadline_seconds=max(0.0, deadline - request_started_at),
                )
            except Exception:
                raise
            usage = self.last_usage if isinstance(self.last_usage, dict) else {}
            usage.update(
                {
                    "json_response_format_fallback": True,
                    "json_response_format_strict_error": strict_error[:800],
                }
            )
            self.last_usage = usage
            return result

    async def chat_json_model(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tier: ModelTier = "balanced",
        temperature: float = 0.0,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        """Run one explicitly selected model for SOP decision contracts."""
        if not self._api_key(model):
            raise RuntimeError("No model API key configured")
        request_started_at = time.monotonic()
        deadline = self._resolve_deadline(tier, deadline_monotonic)

        def build_payload(candidate: str, *, include_response_format: bool = True) -> dict[str, Any]:
            payload = {
                "model": candidate,
                "messages": self._prepare_json_messages(messages),
                "temperature": temperature,
            }
            self._apply_max_tokens(payload, json_mode=True)
            if include_response_format and self.settings.model_response_format_enabled:
                payload["response_format"] = {"type": "json_object"}
            self._apply_relay_reasoning(payload, json_mode=True)
            self._apply_provider_extensions(payload, candidate)
            return payload

        async def consume(raw: dict[str, Any]) -> dict[str, Any]:
            return self._parse_json(self._extract_text(raw))

        try:
            return await self._run_with_transient_retries(
                lambda: self._run_model_candidates(
                    [model],
                    tier=tier,
                    build_payload=lambda candidate: build_payload(candidate, include_response_format=True),
                    consume=consume,
                    failure_label="JSON model failed",
                    deadline_monotonic=deadline,
                    max_parallel_candidates=1,
                ),
                deadline_monotonic=deadline,
                deadline_seconds=max(0.0, deadline - request_started_at),
            )
        except Exception as exc:
            strict_error = f"{type(exc).__name__}: {exc}"
            if (
                not self.settings.model_response_format_enabled
                or not self._should_retry_json_without_response_format(exc)
                or (deadline_monotonic is not None and time.monotonic() >= deadline_monotonic)
            ):
                raise
            result = await self._run_with_transient_retries(
                lambda: self._run_model_candidates(
                    [model],
                    tier=tier,
                    build_payload=lambda candidate: build_payload(candidate, include_response_format=False),
                    consume=consume,
                    failure_label="JSON no-response-format model failed",
                    deadline_monotonic=deadline,
                    max_parallel_candidates=1,
                ),
                deadline_monotonic=deadline,
                deadline_seconds=max(0.0, deadline - request_started_at),
            )
            usage = self.last_usage if isinstance(self.last_usage, dict) else {}
            usage.update(
                {
                    "json_response_format_fallback": True,
                    "json_response_format_strict_error": strict_error[:800],
                }
            )
            self.last_usage = usage
            return result

    async def vision_json(
        self,
        *,
        prompt: str,
        image_url: str,
        tier: ModelTier = "vision",
        temperature: float = 0.0,
        deadline_monotonic: float | None = None,
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
        request_started_at = time.monotonic()
        deadline = self._resolve_deadline(tier, deadline_monotonic)

        def build_payload(model: str) -> dict[str, Any]:
            payload = {
                "model": model,
                "messages": self._prepare_json_messages(messages),
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
                deadline_monotonic=deadline,
            ),
            deadline_monotonic=deadline,
            deadline_seconds=max(0.0, deadline - request_started_at),
        )

    async def _run_with_transient_retries(
        self,
        invoke: Callable[[], Awaitable[T]],
        *,
        deadline_monotonic: float | None = None,
        deadline_seconds: float | None = None,
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
                        "attempts": attempt,
                        "request_retry_errors": list(retry_errors),
                        "deadline_seconds": deadline_seconds,
                        "remaining_budget_ms": self._remaining_budget_ms(deadline_monotonic),
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
                    "attempts": attempt,
                    "request_retry_errors": list(retry_errors),
                    "deadline_seconds": deadline_seconds,
                    "remaining_budget_ms": self._remaining_budget_ms(deadline_monotonic),
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
        max_parallel_candidates: int | None = None,
    ) -> T:
        if not models:
            raise RuntimeError(f"{failure_label}: no model candidates")
        self.last_usage = None
        errors: list[str] = []
        pending: dict[asyncio.Task[tuple[T, dict[str, Any]]], tuple[int, str]] = {}
        next_index = 0
        configured_parallel = (
            self.settings.model_hedge_max_parallel
            if max_parallel_candidates is None
            else max_parallel_candidates
        )
        max_parallel = max(1, min(3, int(configured_parallel or 1)))
        hedge_delay = self._hedge_delay_for_tier(tier)
        configured_total_timeout = self._total_timeout_for_tier(tier)
        started_at = time.perf_counter()
        started_at_monotonic = time.monotonic()
        deadline = started_at_monotonic + configured_total_timeout
        if deadline_monotonic is not None:
            deadline = min(deadline, deadline_monotonic)
        total_timeout = max(0.0, deadline - started_at_monotonic)
        candidate_models = list(models)
        started_models: list[str] = []
        cancelled_models: list[str] = []
        hedge_started = False

        def enrich_last_usage(winner_model: str = "") -> None:
            usage = self.last_usage if isinstance(self.last_usage, dict) else {}
            usage.update(
                {
                    "candidate_models": candidate_models,
                    "primary_model": candidate_models[0] if candidate_models else "",
                    "hedge_model": candidate_models[1] if len(candidate_models) > 1 else "",
                    "started_models": list(started_models),
                    "hedge_started": bool(hedge_started),
                    "winner_model": str(usage.get("model") or winner_model),
                    "cancelled_models": list(cancelled_models),
                    "total_timeout_seconds": total_timeout,
                    "configured_total_timeout_seconds": configured_total_timeout,
                    "deadline_limited": deadline_monotonic is not None and total_timeout < configured_total_timeout,
                    "overall_duration_ms": int((time.perf_counter() - started_at) * 1000),
                    "remaining_budget_ms": self._remaining_budget_ms(deadline),
                    "timeout_stage": str(usage.get("timeout_stage") or ""),
                }
            )
            self.last_usage = usage

        def record_cancelled_pending() -> None:
            for task, (_, model) in pending.items():
                if not task.done() and model not in cancelled_models:
                    cancelled_models.append(model)

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
                "primary_model": candidate_models[0] if candidate_models else "",
                "hedge_model": candidate_models[1] if len(candidate_models) > 1 else "",
                "started_models": list(started_models),
                "pending_models": pending_models,
                "hedge_started": bool(hedge_started),
                "winner_model": "",
                "cancelled_models": list(cancelled_models),
                "total_timeout_seconds": total_timeout,
                "configured_total_timeout_seconds": configured_total_timeout,
                "deadline_limited": deadline_monotonic is not None and total_timeout < configured_total_timeout,
                "remaining_budget_ms": self._remaining_budget_ms(deadline),
                "timeout_stage": "candidate_race" if "TimeoutError" in error else "",
                "error": error,
            }

        async def run_one(index: int, model: str) -> tuple[T, dict[str, Any]]:
            payload = build_payload(model)
            raw = await self._post_chat(payload, tier=tier, fallback_index=index, errors=list(errors))
            usage = dict(self.last_usage or {})
            return await consume(raw), usage

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
                remaining = deadline - time.monotonic()
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
                        result, winner_usage = await task
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
                        _, cancelled_model = pending[other]
                        cancelled_models.append(cancelled_model)
                        other.cancel()
                    if pending:
                        await asyncio.gather(*pending.keys(), return_exceptions=True)
                    self.last_usage = winner_usage
                    enrich_last_usage(model)
                    return result
            record_failure(f"{failure_label}: " + " | ".join(errors))
            raise RuntimeError(f"{failure_label}: " + " | ".join(errors))
        except BaseException:
            if not isinstance(self.last_usage, dict):
                record_failure(f"{failure_label}: " + " | ".join(errors))
            record_cancelled_pending()
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending.keys(), return_exceptions=True)
            if isinstance(self.last_usage, dict):
                self.last_usage["cancelled_models"] = list(cancelled_models)
            raise

    def _resolve_deadline(self, tier: ModelTier, deadline_monotonic: float | None) -> float:
        if deadline_monotonic is not None:
            return float(deadline_monotonic)
        return time.monotonic() + self._total_timeout_for_tier(tier)

    @staticmethod
    def _remaining_budget_ms(deadline_monotonic: float | None) -> int | None:
        if deadline_monotonic is None:
            return None
        return max(0, int((deadline_monotonic - time.monotonic()) * 1000))

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

    def _apply_provider_extensions(self, payload: dict[str, Any]) -> None:
        model = str(payload.get("model") or "").strip().lower()
        base_url = str(self._base_url(model) or "").strip().lower()
        if model.startswith("deepseek-") and "api.deepseek.com" in base_url:
            payload["thinking"] = {"type": "disabled"}

    def _total_timeout_for_tier(self, tier: ModelTier) -> float:
        if tier == "store_destination":
            return max(
                1.0,
                float(
                    self.settings.model_store_destination_total_timeout_seconds
                    or self.settings.model_timeout_seconds
                ),
            )
        if tier == "planner":
            return max(1.0, float(self.settings.model_planner_total_timeout_seconds or self.settings.model_timeout_seconds))
        if tier in {"reply", "strong"}:
            return max(1.0, float(self.settings.model_reply_total_timeout_seconds or self.settings.model_timeout_seconds))
        if tier == "vision":
            return max(1.0, float(self.settings.model_vision_total_timeout_seconds or self.settings.model_timeout_seconds))
        return max(1.0, float(self.settings.model_timeout_seconds))

    def _hedge_delay_for_tier(self, tier: ModelTier) -> float:
        if tier == "store_destination":
            return max(0.0, float(self.settings.model_store_destination_hedge_delay_seconds or 0.0))
        if tier == "planner":
            return max(0.0, float(self.settings.model_planner_hedge_delay_seconds or 0.0))
        if tier in {"reply", "strong"}:
            return max(0.0, float(self.settings.model_reply_hedge_delay_seconds or 0.0))
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
            # The relay occasionally needs more than five seconds to establish
            # concurrent TLS connections. Keep this inside the existing node
            # timeout while avoiding false provider failures during the real
            # Gate/Tool-Planner parallel fan-out.
            connect_timeout = min(10.0, float(timeout))
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=connect_timeout),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
                trust_env=bool(self.settings.model_http_trust_env),
            )
            self._client_timeout = timeout
            self._client_loop_id = loop_id
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _api_key(self, model: str | None = None) -> str:
        return model_selection.api_key(self.settings, model=model)

    def _secondary_settings(self) -> Settings | None:
        provider = str(self.settings.model_secondary_provider or "").strip().lower()
        model = str(self.settings.model_secondary or "").strip()
        if not provider or not model:
            return None
        timeout = max(1.0, float(self.settings.model_secondary_timeout_seconds or 20.0))
        return self.settings.model_copy(
            update={
                "model_provider": provider,
                "model_fast": model,
                "model_fast_fallbacks": "",
                "model_emergency_fallbacks": "",
                "model_hedge_max_parallel": 1,
                "model_request_retry_attempts": 1,
                "model_timeout_seconds": timeout,
            }
        )

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
    def _should_retry_json_without_response_format(exc: Exception) -> bool:
        message = str(exc).lower()
        protocol_markers = (
            "json_object",
            "text.format",
            "contain the word",
            "response input messages must contain",
        )
        if any(marker in message for marker in protocol_markers):
            return True
        if "model http 502" not in message:
            return False
        gateway_markers = ("bad gateway", "cloudflare", "linkai.shop")
        return any(marker in message for marker in gateway_markers)

    @staticmethod
    def _extract_text(raw: dict[str, Any]) -> str:
        return model_response.extract_text(raw)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        return model_response.parse_json(text)

    @staticmethod
    def _prepare_json_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared = ModelClient._ensure_json_marker(
            ModelClient._merge_adjacent_system_messages(ModelClient._ensure_json_marker(messages))
        )
        return ModelClient._ensure_json_user_marker(prepared)

    @staticmethod
    def _ensure_json_marker(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Some OpenAI-compatible gateways validate the lowercase literal in the
        # raw input before accepting response_format=json_object. Always keep an
        # explicit lowercase marker in the first system message; do not rely on
        # business prompts, which may say uppercase JSON or be transformed by a
        # relay before provider-side validation.
        marker_text = ModelClient._JSON_MODE_MARKER
        if messages and str(messages[0].get("role") or "").lower() == "system":
            content = messages[0].get("content")
            if isinstance(content, str) and marker_text in content:
                return messages
            item = dict(messages[0])
            item["content"] = f"{marker_text}\n\n{content or ''}".strip()
            return [item, *messages[1:]]
        if any(
            isinstance(item, dict)
            and str(item.get("role") or "").lower() == "system"
            and isinstance(item.get("content"), str)
            and marker_text in str(item.get("content"))
            for item in messages
        ):
            return messages
        marker = {"role": "system", "content": marker_text}
        return [marker, *messages]

    @staticmethod
    def _ensure_json_user_marker(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep the relay's lowercase marker after system-to-input conversion.

        Some OpenAI-compatible relays validate only converted user input when
        response_format=json_object is used.  The system marker remains the
        model contract; this duplicate marker only preserves protocol syntax.
        """

        marker_text = ModelClient._JSON_MODE_MARKER
        for index, message in enumerate(messages):
            if str(message.get("role") or "").lower() != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                if marker_text in content:
                    return messages
                updated = list(messages)
                item = dict(message)
                item["content"] = f"{marker_text}\n\n{content}".strip()
                updated[index] = item
                return updated
            if isinstance(content, list):
                if any(
                    isinstance(part, dict)
                    and str(part.get("type") or "") in {"text", "input_text"}
                    and marker_text in str(part.get("text") or "")
                    for part in content
                ):
                    return messages
                updated = list(messages)
                item = dict(message)
                item["content"] = [{"type": "text", "text": marker_text}, *content]
                updated[index] = item
                return updated
        return [*messages, {"role": "user", "content": marker_text}]

    @staticmethod
    def _merge_adjacent_system_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        pending_system: dict[str, Any] | None = None
        pending_parts: list[str] = []

        def flush_system() -> None:
            nonlocal pending_system, pending_parts
            if pending_system is None:
                return
            item = dict(pending_system)
            item["content"] = "\n\n".join(part for part in pending_parts if part)
            merged.append(item)
            pending_system = None
            pending_parts = []

        for message in messages:
            if not isinstance(message, dict):
                flush_system()
                merged.append(message)
                continue
            role = str(message.get("role") or "").strip().lower()
            if role != "system":
                flush_system()
                merged.append(message)
                continue
            if pending_system is None:
                pending_system = dict(message)
                pending_parts = []
            pending_parts.append(ModelClient._message_content_to_text(message.get("content")))
        flush_system()
        return merged

    @staticmethod
    def _message_content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if text is not None:
                        parts.append(str(text))
            return "\n".join(parts)
        return str(content or "")
