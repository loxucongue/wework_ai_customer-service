from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.v3_deepseek_test_support import assert_deepseek_models, deepseek_only_settings


def test_deepseek_test_guard_rejects_non_deepseek_models() -> None:
    with pytest.raises(RuntimeError, match="DeepSeek-only"):
        assert_deepseek_models("deepseek-v4-flash", "gpt-5.4")


def test_deepseek_test_settings_disable_callbacks_and_external_fallbacks() -> None:
    settings = SimpleNamespace(
        deepseek_api_key="test-key",
        deepseek_api_base_url="https://api.deepseek.com",
        model_copy=lambda *, update: SimpleNamespace(**update),
    )

    result = deepseek_only_settings(
        settings,
        router_model="deepseek-v4-flash",
        reply_model="deepseek-v4-pro",
    )

    assert result.deepseek_semantic_model == "deepseek-v4-flash"
    assert result.model_reply == "deepseek-v4-pro"
    assert result.model_store_destination == "deepseek-v4-flash"
    assert result.model_reply_fallbacks == ""
    assert result.model_store_destination_fallbacks == ""
    assert result.model_emergency_fallbacks == ""
    assert result.service_rule_data_enabled is False
