from __future__ import annotations

from app.config import Settings
from ai_paths.scripts.run_refactor_model_matrix import (
    DEFAULT_RELAY_BASE_URL,
    MODEL_PROFILES,
    build_profile_settings,
    matrix_ranking,
    missing_key_profile_result,
    profile_result_summary,
    public_profile_config,
    relay_api_base_url,
    selected_profiles,
    timed_out_profile_result,
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
    relay_base_url = relay_api_base_url(DEFAULT_RELAY_BASE_URL)
    settings = build_profile_settings(
        Settings(_env_file=None),
        profile=MODEL_PROFILES["gemini"],
        relay_base_url=relay_base_url,
        api_key="dummy-gemini-key",
    )

    assert settings.model_provider == "relay"
    assert settings.model_relay_protocol == "openai"
    assert settings.model_relay_base_url == "https://linkai.shop/v1"
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


def test_relay_api_base_url_accepts_linkai_root_url() -> None:
    assert relay_api_base_url("https://linkai.shop") == "https://linkai.shop/v1"
    assert relay_api_base_url("https://linkai.shop/") == "https://linkai.shop/v1"
    assert relay_api_base_url("https://linkai.shop/v1") == "https://linkai.shop/v1"


def test_public_profile_config_never_contains_key_value() -> None:
    profile = MODEL_PROFILES["openai"]

    public = public_profile_config(profile, relay_base_url=DEFAULT_RELAY_BASE_URL, api_key_present=True)

    assert public["api_key_env"] == "REFACTOR_MODEL_OPENAI_API_KEY"
    assert public["api_key_present"] is True
    assert public["api_key_value_logged"] is False
    assert "dummy" not in str(public)


def test_profile_result_summary_exposes_accuracy_and_speed_for_review() -> None:
    summary = profile_result_summary(
        {
            "hard_error_count": 0,
            "semantic_pass_rate": 0.94,
            "failed_critical_scenarios": [],
            "summary": {
                "hard_pass_rate": "100.0%",
                "evaluable_attempts": 15,
                "infrastructure_failures": 0,
                "p50_ms": 4200,
                "p90_ms": 7800,
                "acceptance": {
                    "hard_errors_zero": True,
                    "semantic_at_least_90": True,
                    "critical_all_pass": True,
                },
            },
            "effect_review": {
                "issue_count": 2,
                "low_score_count": 1,
                "hard_or_infra_count": 1,
            },
        }
    )

    assert summary["hard_error_count"] == 0
    assert summary["semantic_pass_rate"] == 0.94
    assert summary["p50_ms"] == 4200
    assert summary["p90_ms"] == 7800
    assert summary["effect_issue_count"] == 2
    assert summary["effect_low_score_count"] == 1
    assert summary["effect_hard_or_infra_count"] == 1
    assert summary["accepted_by_release_thresholds"] is True


def test_profile_result_summary_rejects_infrastructure_failures() -> None:
    summary = profile_result_summary(
        {
            "hard_error_count": 0,
            "semantic_pass_rate": 1.0,
            "failed_critical_scenarios": [],
            "summary": {
                "hard_pass_rate": "100.0%",
                "evaluable_attempts": 10,
                "infrastructure_failures": 1,
                "p50_ms": 2200,
                "p90_ms": 3100,
                "acceptance": {
                    "hard_errors_zero": True,
                    "semantic_at_least_90": True,
                    "critical_all_pass": True,
                },
            },
        }
    )

    assert summary["infrastructure_failures"] == 1
    assert summary["accepted_by_release_thresholds"] is False


def test_timed_out_profile_result_is_not_release_accepted_and_does_not_log_key() -> None:
    result = timed_out_profile_result(
        MODEL_PROFILES["openai"],
        relay_base_url="https://linkai.shop/v1",
        timeout_seconds=120,
    )

    assert result["status"] == "timed_out"
    assert result["model_profile"]["api_key_env"] == "REFACTOR_MODEL_OPENAI_API_KEY"
    assert result["model_profile"]["api_key_value_logged"] is False
    assert result["profile_summary"]["infrastructure_failures"] == 1
    assert result["profile_summary"]["timeout_seconds"] == 120
    assert result["profile_summary"]["accepted_by_release_thresholds"] is False
    assert "sk-" not in str(result)


def test_missing_key_profile_summary_is_not_counted_as_infrastructure_failure() -> None:
    result = missing_key_profile_result(
        MODEL_PROFILES["gemini"],
        relay_base_url="https://linkai.shop/v1",
    )

    summary = result["profile_summary"]
    assert result["status"] == "skipped_missing_api_key_env"
    assert result["model_profile"]["api_key_present"] is False
    assert result["model_profile"]["api_key_value_logged"] is False
    assert summary["infrastructure_failures"] == 0
    assert summary["accepted_by_release_thresholds"] is False
    assert "sk-" not in str(result)


def test_matrix_ranking_orders_by_accuracy_then_errors_then_speed() -> None:
    ranked = matrix_ranking(
        [
            {
                "status": "completed",
                "model_profile": {"name": "slow", "model": "slow-model"},
                "profile_summary": {"semantic_pass_rate": 0.9, "hard_error_count": 0, "p90_ms": 9000},
            },
            {
                "status": "completed",
                "model_profile": {"name": "better", "model": "better-model"},
                "profile_summary": {
                    "semantic_pass_rate": 0.95,
                    "hard_error_count": 0,
                    "p90_ms": 12000,
                    "effect_issue_count": 1,
                    "effect_low_score_count": 1,
                    "effect_hard_or_infra_count": 0,
                },
            },
            {
                "status": "completed",
                "model_profile": {"name": "fast", "model": "fast-model"},
                "profile_summary": {"semantic_pass_rate": 0.9, "hard_error_count": 0, "p90_ms": 3000},
            },
            {
                "status": "completed",
                "model_profile": {"name": "unstable", "model": "unstable-model"},
                "profile_summary": {
                    "semantic_pass_rate": 0.9,
                    "hard_error_count": 0,
                    "infrastructure_failures": 2,
                    "p90_ms": 1000,
                },
            },
            {
                "status": "skipped_missing_api_key_env",
                "model_profile": {"name": "skipped", "model": "skipped-model"},
            },
        ]
    )

    assert [item["name"] for item in ranked] == ["better", "fast", "slow", "unstable"]
    assert ranked[0]["effect_issue_count"] == 1
    assert ranked[0]["effect_low_score_count"] == 1
    assert ranked[0]["effect_hard_or_infra_count"] == 0
