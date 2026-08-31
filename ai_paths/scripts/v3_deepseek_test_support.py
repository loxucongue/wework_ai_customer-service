from __future__ import annotations

from typing import Any

from app.config import Settings


def deepseek_only_settings(
    settings: Settings,
    *,
    router_model: str = "deepseek-v4-flash",
    reply_model: str = "deepseek-v4-pro",
) -> Settings:
    assert_deepseek_models(router_model, reply_model)
    api_key = str(settings.deepseek_api_key or "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    return settings.model_copy(
        update={
            "model_provider": "openai_compatible",
            "model_relay_api_key": api_key,
            "model_relay_base_url": str(
                settings.deepseek_api_base_url or "https://api.deepseek.com"
            ).rstrip("/"),
            "model_relay_protocol": "openai",
            "model_http_trust_env": False,
            "model_relay_reasoning_control_enabled": False,
            "model_reasoning_enabled": False,
            "model_json_reasoning_enabled": False,
            "model_fast": reply_model,
            "model_planner": reply_model,
            "model_balanced": reply_model,
            "model_strong": reply_model,
            "model_reply": reply_model,
            "model_store_destination": router_model,
            "model_fast_fallbacks": "",
            "model_planner_fallbacks": "",
            "model_balanced_fallbacks": "",
            "model_strong_fallbacks": "",
            "model_reply_fallbacks": "",
            "model_store_destination_fallbacks": "",
            "model_emergency_fallbacks": "",
            "model_hedge_max_parallel": 1,
            "deepseek_semantic_model": router_model,
            "deepseek_semantic_timeout_seconds": 20.0,
            "service_rule_data_enabled": False,
        }
    )


def assert_deepseek_models(*models: str) -> None:
    invalid = [
        str(model or "")
        for model in models
        if not str(model or "").lower().startswith("deepseek-")
    ]
    if invalid:
        raise RuntimeError(f"DeepSeek-only test rejected model(s): {', '.join(invalid)}")


def collect_model_names(value: Any) -> list[str]:
    models: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if key in {"model", "primary_model", "selected_model", "fallback_model"}:
                    if isinstance(nested, str) and nested.strip():
                        models.add(nested.strip())
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return sorted(models)
