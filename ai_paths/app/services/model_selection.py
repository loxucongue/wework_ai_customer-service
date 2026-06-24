from __future__ import annotations

from typing import Literal

import httpx

from app.config import Settings


ModelTier = Literal["fast", "planner", "balanced", "strong", "reply", "vision"]


def api_key(settings: Settings) -> str:
    provider = settings.model_provider.lower()
    if provider == "volcengine":
        return settings.volcengine_ark_api_key
    return settings.aliyun_dashscope_api_key


def base_url(settings: Settings) -> str:
    provider = settings.model_provider.lower()
    if provider == "volcengine":
        return settings.volcengine_openai_base_url
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
        fallback_text = settings.model_planner_fallbacks or "qwen-turbo"
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
        if name and name not in models:
            models.append(name)
    return models


def split_models(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def should_try_next_model(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in {400, 401, 403, 404, 429, 500, 502, 503, 504}:
            return True
    return True
