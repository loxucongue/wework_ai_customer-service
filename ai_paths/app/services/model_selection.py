from __future__ import annotations

from typing import Literal

import httpx

from app.config import Settings


ModelTier = Literal["fast", "planner", "balanced", "strong", "reply", "vision"]


def api_key(settings: Settings, model: str | None = None) -> str:
    provider = settings.model_provider.lower()
    if provider == "volcengine":
        return settings.volcengine_ark_api_key
    if provider in {"relay", "openai_compatible", "openai-compatible"}:
        if is_claude_model(model):
            return settings.claude_relay_api_key or settings.anthropic_auth_token or settings.model_relay_api_key
        return settings.model_relay_api_key or settings.claude_relay_api_key or settings.anthropic_auth_token
    return settings.aliyun_dashscope_api_key


def base_url(settings: Settings) -> str:
    provider = settings.model_provider.lower()
    if provider == "volcengine":
        return settings.volcengine_openai_base_url
    if provider in {"relay", "openai_compatible", "openai-compatible"}:
        return settings.model_relay_base_url or settings.anthropic_base_url
    return settings.aliyun_openai_base_url


def model_name(settings: Settings, tier: ModelTier) -> str:
    if tier == "fast":
        return settings.model_fast
    if tier == "planner":
        return settings.model_planner
    if tier == "strong":
        return settings.model_strong
    if tier == "reply":
        return settings.model_reply or settings.model_fast
    if tier == "vision":
        return settings.model_vision
    return settings.model_balanced


def model_names(settings: Settings, tier: ModelTier) -> list[str]:
    primary = model_name(settings, tier)
    if tier == "fast":
        fallback_text = settings.model_fast_fallbacks
    elif tier == "planner":
        fallback_text = settings.model_planner_fallbacks
    elif tier == "strong":
        fallback_text = settings.model_strong_fallbacks
    elif tier == "reply":
        fallback_text = settings.model_reply_fallbacks
    elif tier == "vision":
        fallback_text = settings.model_vision_fallbacks
    else:
        fallback_text = settings.model_balanced_fallbacks
    models = [primary, primary] if tier == "planner" else [primary]
    for name in split_models(fallback_text):
        if name:
            models.append(name)
    if tier == "planner" and settings.model_provider.lower() == "aliyun" and "qwen-turbo" not in models:
        models.append("qwen-turbo")
    return models


def split_models(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def is_claude_model(model: str | None) -> bool:
    value = str(model or "").strip().lower()
    if not value:
        return False
    return value.startswith("claude-") or "/claude-" in value or value.startswith("anthropic/claude-")


def should_try_next_model(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in {400, 401, 403, 404, 429, 500, 502, 503, 504}:
            return True
    return True
