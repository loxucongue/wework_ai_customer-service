from __future__ import annotations

from app.config import Settings
from ai_paths.scripts.run_refactor_model_matrix import (
    DEFAULT_RELAY_BASE_URL,
    MODEL_PROFILES,
    build_profile_settings,
    public_profile_config,
    selected_profiles,
)


def test_refactor_model_matrix_profiles_match_approved_candidates() -> None:
    assert MODEL_PROFILES["claude"].model == "claude-opus-4-7"
    assert MODEL_PROFILES["gemini"].model == "gemini-3.5-flash"
    assert MODEL_PROFILES["openai"].model == "gpt-5.4"
    assert MODEL_PROFILES["claude"].api_key_env == "REFACTOR_MODEL_CLAUDE_API_KEY"
    assert MODEL_PROFILES["gemini"].api_key_env == "REFACTOR_MODEL_GEMINI_API_KEY"
    assert MODEL_PROFILES["openai"].api_key_env == "REFACTOR_MODEL_OPENAI_API_KEY"


def test_selected_profiles_reject_unknown_profile() -> None:
    try:
        selected_profiles("openai,unknown")
    except ValueError as exc:
        assert "unknown model profiles: unknown" in str(exc)
    else:
        raise AssertionError("unknown profile should be rejected")


def test_build_profile_settings_uses_openai_compatible_relay_without_fallbacks() -> None:
    settings = build_profile_settings(
        Settings(_env_file=None),
        profile=MODEL_PROFILES["gemini"],
        relay_base_url=DEFAULT_RELAY_BASE_URL,
        api_key="dummy-gemini-key",
    )

    assert settings.model_provider == "relay"
    assert settings.model_relay_protocol == "openai"
    assert settings.model_relay_base_url == DEFAULT_RELAY_BASE_URL
    assert settings.model_planner == "gemini-3.5-flash"
    assert settings.model_reply == "gemini-3.5-flash"
    assert settings.model_planner_fallbacks == ""
    assert settings.model_reply_fallbacks == ""
    assert settings.model_relay_api_key == "dummy-gemini-key"
    assert settings.claude_relay_api_key == ""


def test_build_profile_settings_keeps_claude_key_in_claude_slot() -> None:
    settings = build_profile_settings(
        Settings(_env_file=None),
        profile=MODEL_PROFILES["claude"],
        relay_base_url=DEFAULT_RELAY_BASE_URL,
        api_key="dummy-claude-key",
    )

    assert settings.model_relay_protocol == "openai"
    assert settings.model_planner == "claude-opus-4-7"
    assert settings.model_relay_api_key == ""
    assert settings.claude_relay_api_key == "dummy-claude-key"


def test_public_profile_config_never_contains_key_value() -> None:
    profile = MODEL_PROFILES["openai"]

    public = public_profile_config(profile, relay_base_url=DEFAULT_RELAY_BASE_URL, api_key_present=True)

    assert public["api_key_env"] == "REFACTOR_MODEL_OPENAI_API_KEY"
    assert public["api_key_present"] is True
    assert public["api_key_value_logged"] is False
    assert "dummy" not in str(public)
