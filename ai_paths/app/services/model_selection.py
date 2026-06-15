from __future__ import annotations

from typing import Literal

import httpx

from app.config import Settings


ModelTier = Literal["fast", "balanced", "strong", "vision"]


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
    if tier == "vision":
        return settings.model_vision
    if tier == "strong":
        return settings.model_strong or settings.model_balanced or settings.model_fast
    if tier == "balanced":
        return settings.model_balanced or settings.model_fast
    return settings.model_fast


def model_names(settings: Settings, tier: ModelTier) -> list[str]:
    primary = model_name(settings, tier)
    fallback_text = {
        "fast": settings.model_fast_fallbacks,
        "balanced": settings.model_balanced_fallbacks,
        "strong": settings.model_strong_fallbacks,
        "vision": settings.model_vision_fallbacks,
    }.get(tier, "")
    models = [primary]
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
