from __future__ import annotations

import argparse
from pathlib import Path

from app.config import Settings
from ai_paths.scripts.run_refactor_model_matrix import (
    DEFAULT_RELAY_BASE_URL,
    MODEL_PROFILES,
    build_profile_settings,
    git_commit_fields,
    load_refactor_env,
    matrix_ranking,
    matrix_run_options,
    missing_key_profile_result,
    evaluation_scope,
    profile_artifacts_summary,
    profile_result_summary,
    public_profile_config,
    refactor_env_value,
    relay_api_base_url,
    selected_profiles,
    suite_run_options,
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


def test_git_commit_fields_expose_single_commit_set(monkeypatch) -> None:
    import ai_paths.scripts.run_refactor_model_matrix as matrix

    monkeypatch.setattr(matrix, "git_commit", lambda repo_root: "abc123")

    assert git_commit_fields(Path(".")) == {
        "git_commit": "abc123",
        "git_commit_set": ["abc123"],
    }


def test_public_profile_config_never_contains_key_value() -> None:
    profile = MODEL_PROFILES["openai"]

    public = public_profile_config(profile, relay_base_url=DEFAULT_RELAY_BASE_URL, api_key_present=True)

    assert public["api_key_env"] == "REFACTOR_MODEL_OPENAI_API_KEY"
    assert public["api_key_present"] is True
    assert public["api_key_value_logged"] is False
    assert "dummy" not in str(public)


def test_load_refactor_env_reads_only_approved_ignored_keys(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "REFACTOR_MODEL_CLAUDE_API_KEY=dummy_claude_key",
                "REFACTOR_MODEL_GEMINI_API_KEY='dummy_gemini_key'",
                'REFACTOR_MODEL_OPENAI_API_KEY="dummy_openai_key"',
                "REFACTOR_MODEL_RELAY_BASE_URL=https://linkai.shop",
                "UNRELATED_SECRET=dummy_unrelated_secret",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("REFACTOR_MODEL_OPENAI_API_KEY", "dummy_env_openai_key")

    values = load_refactor_env(tmp_path)

    assert values["REFACTOR_MODEL_CLAUDE_API_KEY"] == "dummy_claude_key"
    assert values["REFACTOR_MODEL_GEMINI_API_KEY"] == "dummy_gemini_key"
    assert values["REFACTOR_MODEL_OPENAI_API_KEY"] == "dummy_env_openai_key"
    assert values["REFACTOR_MODEL_RELAY_BASE_URL"] == "https://linkai.shop"
    assert "UNRELATED_SECRET" not in values


def test_refactor_model_matrix_env_example_is_safe_and_not_loaded() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    example_path = repo_root / "workflow_tests" / "fixtures" / "refactor_model_matrix_env.example"

    text = example_path.read_text(encoding="utf-8")
    values = load_refactor_env(example_path.parent)

    for marker in [
        "claude-opus-4-7 through LinkAI OpenAI-compatible relay",
        "gemini-3.5-flash through LinkAI OpenAI-compatible relay",
        "gpt-5.4 through LinkAI OpenAI-compatible relay",
        "Reports must compare both reply accuracy and latency",
        "REFACTOR_MODEL_RELAY_BASE_URL=https://linkai.shop",
        "REFACTOR_MODEL_CLAUDE_API_KEY=<local-only-claude-key>",
        "REFACTOR_MODEL_GEMINI_API_KEY=<local-only-gemini-key>",
        "REFACTOR_MODEL_OPENAI_API_KEY=<local-only-openai-key>",
        "REFACTOR_MODEL_MATRIX_PROFILES=claude,gemini,openai",
        "REFACTOR_MODEL_MATRIX_PROFILE_TIMEOUT_SECONDS=120",
    ]:
        assert marker in text

    assert "sk-" not in text
    assert values == {}


def test_refactor_env_value_strips_values_without_logging_keys() -> None:
    values = {"REFACTOR_MODEL_OPENAI_API_KEY": "  dummy_openai_key  "}

    api_key = refactor_env_value(values, "REFACTOR_MODEL_OPENAI_API_KEY")
    public = public_profile_config(
        MODEL_PROFILES["openai"],
        relay_base_url=DEFAULT_RELAY_BASE_URL,
        api_key_present=bool(api_key),
    )

    assert api_key == "dummy_openai_key"
    assert public["api_key_present"] is True
    assert public["api_key_value_logged"] is False
    assert api_key not in str(public)


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


def test_suite_run_options_passes_scenario_filters_to_simulation() -> None:
    settings = Settings(_env_file=None)
    options = suite_run_options(
        argparse.Namespace(
            attempts=2,
            critical_attempts=5,
            concurrency=2,
            max_cases=0,
            scenario_id="store_v2_county_to_nearest_store",
            category="store",
            skip_review=False,
        ),
        settings,
    )

    assert options == {
        "attempts": 2,
        "critical_attempts": 5,
        "concurrency": 2,
        "max_cases": 0,
        "scenario_id": "store_v2_county_to_nearest_store",
        "category": "store",
        "skip_review": False,
        "base_settings": settings,
    }


def test_suite_run_options_normalizes_empty_filters_to_none() -> None:
    settings = Settings(_env_file=None)
    options = suite_run_options(
        argparse.Namespace(
            attempts=1,
            critical_attempts=1,
            concurrency=1,
            max_cases=3,
            scenario_id="",
            category="",
            skip_review=True,
        ),
        settings,
    )

    assert options["scenario_id"] is None
    assert options["category"] is None
    assert options["max_cases"] == 3
    assert options["skip_review"] is True


def test_evaluation_scope_marks_full_release_gate_candidate_only_without_filters() -> None:
    full = evaluation_scope(
        argparse.Namespace(
            scenario_id="",
            category="",
            max_cases=0,
        )
    )
    targeted = evaluation_scope(
        argparse.Namespace(
            scenario_id="store_v2_county_confirm",
            category="",
            max_cases=0,
        )
    )

    assert full == {
        "schema_version": "reply_chain_refactor_model_matrix_scope_v1",
        "scenario_id": "",
        "category": "",
        "max_cases": 0,
        "targeted_smoke": False,
        "full_release_gate_candidate": True,
    }
    assert targeted["targeted_smoke"] is True
    assert targeted["full_release_gate_candidate"] is False


def test_matrix_run_options_exposes_review_and_attempt_gate_inputs() -> None:
    options = matrix_run_options(
        argparse.Namespace(
            attempts=3,
            critical_attempts=5,
            concurrency=2,
            skip_review=False,
            profile_timeout_seconds=120,
        )
    )

    assert options == {
        "schema_version": "reply_chain_refactor_model_matrix_run_options_v1",
        "attempts": 3,
        "critical_attempts": 5,
        "concurrency": 2,
        "skip_review": False,
        "profile_timeout_seconds": 120,
    }


def test_profile_artifacts_summary_exposes_per_model_report_evidence(tmp_path) -> None:
    result_json_path = tmp_path / "result.json"
    report_md_path = tmp_path / "report.md"
    result_json_path.write_text("{}", encoding="utf-8")
    report_md_path.write_text("# report", encoding="utf-8")

    artifacts = profile_artifacts_summary(
        {
            "scenario_count": 100,
            "attempt_count": 300,
            "effect_review": {"result_count": 300},
            "review_artifacts": {"result_count": 300},
        },
        result_json_path=result_json_path,
        report_md_path=report_md_path,
    )

    assert artifacts == {
        "schema_version": "reply_chain_refactor_model_profile_artifacts_v1",
        "result_json_path": str(result_json_path),
        "report_md_path": str(report_md_path),
        "result_json_written": True,
        "report_md_written": True,
        "scenario_count": 100,
        "attempt_count": 300,
        "effect_review_result_count": 300,
        "review_artifacts_result_count": 300,
    }


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
