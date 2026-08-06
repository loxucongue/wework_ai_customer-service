from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.simulation.runner import render_markdown, run_suite


DEFAULT_RELAY_BASE_URL = "https://linkai.shop"


@dataclass(frozen=True)
class ModelProfile:
    name: str
    model: str
    api_key_env: str
    uses_claude_key_slot: bool = False


MODEL_PROFILES: dict[str, ModelProfile] = {
    "claude": ModelProfile(
        name="claude",
        model="claude-opus-4-7",
        api_key_env="REFACTOR_MODEL_CLAUDE_API_KEY",
        uses_claude_key_slot=True,
    ),
    "gemini": ModelProfile(
        name="gemini",
        model="gemini-3.5-flash",
        api_key_env="REFACTOR_MODEL_GEMINI_API_KEY",
    ),
    "openai": ModelProfile(
        name="openai",
        model="gpt-5.4",
        api_key_env="REFACTOR_MODEL_OPENAI_API_KEY",
    ),
}


def selected_profiles(value: str) -> list[ModelProfile]:
    names = [item.strip().lower() for item in str(value or "").split(",") if item.strip()]
    if not names:
        names = ["claude", "gemini", "openai"]
    unknown = [name for name in names if name not in MODEL_PROFILES]
    if unknown:
        raise ValueError(f"unknown model profiles: {','.join(unknown)}")
    return [MODEL_PROFILES[name] for name in names]


def relay_api_base_url(value: str) -> str:
    base_url = str(value or DEFAULT_RELAY_BASE_URL).strip().rstrip("/")
    if not base_url:
        base_url = DEFAULT_RELAY_BASE_URL
    if base_url == "https://linkai.shop":
        return f"{base_url}/v1"
    return base_url


def build_profile_settings(
    base: Settings,
    *,
    profile: ModelProfile,
    relay_base_url: str,
    api_key: str,
) -> Settings:
    key_update = {
        "model_relay_api_key": "" if profile.uses_claude_key_slot else api_key,
        "claude_relay_api_key": api_key if profile.uses_claude_key_slot else "",
        "anthropic_auth_token": "",
    }
    return base.model_copy(
        update={
            "model_provider": "relay",
            "model_relay_base_url": relay_base_url,
            "anthropic_base_url": "",
            "model_relay_protocol": "openai",
            "model_fast": profile.model,
            "model_planner": profile.model,
            "model_balanced": profile.model,
            "model_strong": profile.model,
            "model_reply": profile.model,
            "model_fast_fallbacks": "",
            "model_planner_fallbacks": "",
            "model_balanced_fallbacks": "",
            "model_strong_fallbacks": "",
            "model_reply_fallbacks": "",
            **key_update,
        }
    )


def public_profile_config(profile: ModelProfile, *, relay_base_url: str, api_key_present: bool) -> dict[str, Any]:
    return {
        "name": profile.name,
        "model": profile.model,
        "relay_base_url": relay_base_url,
        "api_key_env": profile.api_key_env,
        "api_key_present": api_key_present,
        "api_key_value_logged": False,
        "protocol": "openai-compatible relay",
    }


def timed_out_profile_result(
    profile: ModelProfile,
    *,
    relay_base_url: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    return {
        "model_profile": public_profile_config(profile, relay_base_url=relay_base_url, api_key_present=True),
        "status": "timed_out",
        "profile_summary": {
            "hard_error_count": None,
            "semantic_pass_rate": None,
            "failed_critical_scenarios": [],
            "hard_pass_rate": None,
            "evaluable_attempts": 0,
            "infrastructure_failures": 1,
            "p50_ms": None,
            "p90_ms": None,
            "timeout_seconds": timeout_seconds,
            "accepted_by_release_thresholds": False,
        },
    }


def profile_result_summary(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    acceptance = summary.get("acceptance") if isinstance(summary.get("acceptance"), dict) else {}
    infrastructure_failures = int(summary.get("infrastructure_failures") or 0)
    return {
        "hard_error_count": report.get("hard_error_count"),
        "semantic_pass_rate": report.get("semantic_pass_rate"),
        "failed_critical_scenarios": report.get("failed_critical_scenarios") or [],
        "hard_pass_rate": summary.get("hard_pass_rate"),
        "evaluable_attempts": summary.get("evaluable_attempts"),
        "infrastructure_failures": infrastructure_failures,
        "p50_ms": summary.get("p50_ms"),
        "p90_ms": summary.get("p90_ms"),
        "accepted_by_release_thresholds": (
            acceptance.get("hard_errors_zero") is True
            and acceptance.get("semantic_at_least_90") is True
            and acceptance.get("critical_all_pass") is True
            and infrastructure_failures == 0
        ),
    }


def matrix_ranking(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed = [item for item in profiles if item.get("status") == "completed"]

    def score(item: dict[str, Any]) -> tuple[float, int, int, float]:
        summary = item.get("profile_summary") if isinstance(item.get("profile_summary"), dict) else {}
        semantic = float(summary.get("semantic_pass_rate") or 0.0)
        hard_errors = int(summary.get("hard_error_count") or 0)
        infrastructure_failures = int(summary.get("infrastructure_failures") or 0)
        p90 = float(summary.get("p90_ms") or 999999)
        return (-semantic, hard_errors, infrastructure_failures, p90)

    return [
        {
            "name": (item.get("model_profile") or {}).get("name"),
            "model": (item.get("model_profile") or {}).get("model"),
            "semantic_pass_rate": (item.get("profile_summary") or {}).get("semantic_pass_rate"),
            "hard_error_count": (item.get("profile_summary") or {}).get("hard_error_count"),
            "infrastructure_failures": (item.get("profile_summary") or {}).get("infrastructure_failures"),
            "p50_ms": (item.get("profile_summary") or {}).get("p50_ms"),
            "p90_ms": (item.get("profile_summary") or {}).get("p90_ms"),
        }
        for item in sorted(completed, key=score)
    ]


def git_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()
    except Exception:
        return ""


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run offline full-chain simulation across refactor candidate models. "
            "API keys are read only from environment variables and are never written to reports."
        )
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("workflow_tests/fixtures/full_chain_simulation_v1.json"),
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--profiles", default=os.getenv("REFACTOR_MODEL_MATRIX_PROFILES", "claude,gemini,openai"))
    parser.add_argument("--relay-base-url", default=os.getenv("REFACTOR_MODEL_RELAY_BASE_URL", DEFAULT_RELAY_BASE_URL))
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--critical-attempts", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--skip-review", action="store_true")
    parser.add_argument("--require-keys", action="store_true")
    parser.add_argument(
        "--profile-timeout-seconds",
        type=int,
        default=int(os.getenv("REFACTOR_MODEL_MATRIX_PROFILE_TIMEOUT_SECONDS", "0") or 0),
        help="Optional wall-clock timeout per model profile. 0 disables the timeout.",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _args()
    repo_root = Path(__file__).resolve().parents[2]
    fixture = (repo_root / args.fixture).resolve() if not args.fixture.is_absolute() else args.fixture
    output_dir = args.output_dir or (
        repo_root / ".tmp_runtime" / "simulation" / datetime.now().strftime("model-matrix-%Y%m%d-%H%M%S")
    )
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    relay_base_url = relay_api_base_url(str(args.relay_base_url or DEFAULT_RELAY_BASE_URL))
    base_settings = Settings()
    profiles = selected_profiles(args.profiles)
    matrix_results: list[dict[str, Any]] = []
    executed = 0

    for profile in profiles:
        api_key = os.getenv(profile.api_key_env, "").strip()
        public_config = public_profile_config(profile, relay_base_url=relay_base_url, api_key_present=bool(api_key))
        profile_dir = output_dir / profile.name
        if not api_key:
            skipped = {
                "model_profile": public_config,
                "status": "skipped_missing_api_key_env",
                "profile_summary": {
                    "hard_error_count": None,
                    "semantic_pass_rate": None,
                    "failed_critical_scenarios": [],
                    "p50_ms": None,
                    "p90_ms": None,
                    "accepted_by_release_thresholds": False,
                },
            }
            matrix_results.append(skipped)
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "result.json").write_text(json.dumps(skipped, ensure_ascii=False, indent=2), encoding="utf-8")
            continue

        settings = build_profile_settings(
            base_settings,
            profile=profile,
            relay_base_url=relay_base_url,
            api_key=api_key,
        )
        executed += 1
        suite_call = run_suite(
            repo_root=repo_root,
            fixture=fixture,
            output_dir=profile_dir,
            attempts=args.attempts,
            critical_attempts=args.critical_attempts,
            concurrency=args.concurrency,
            max_cases=args.max_cases,
            skip_review=args.skip_review,
            base_settings=settings,
        )
        try:
            if args.profile_timeout_seconds and args.profile_timeout_seconds > 0:
                report = await asyncio.wait_for(suite_call, timeout=args.profile_timeout_seconds)
            else:
                report = await suite_call
        except asyncio.TimeoutError:
            timed_out = timed_out_profile_result(
                profile,
                relay_base_url=relay_base_url,
                timeout_seconds=args.profile_timeout_seconds,
            )
            matrix_results.append(timed_out)
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "result.json").write_text(json.dumps(timed_out, ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        report["model_profile"] = public_config
        (profile_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (profile_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
        matrix_results.append(
            {
                "model_profile": public_config,
                "status": "completed",
                "profile_summary": profile_result_summary(report),
            }
        )

    matrix_report = {
        "schema_version": "reply_chain_refactor_model_matrix_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "git_commit": git_commit(repo_root),
        "fixture": str(fixture),
        "relay_base_url": relay_base_url,
        "profiles_requested": [profile.name for profile in profiles],
        "executed_profile_count": executed,
        "ranking": matrix_ranking(matrix_results),
        "profiles": matrix_results,
        "safety": {
            "api_keys_written_to_report": False,
            "production_customer_messages_sent": False,
            "production_writes_allowed": False,
        },
    }
    (output_dir / "matrix_result.json").write_text(json.dumps(matrix_report, ensure_ascii=False, indent=2), encoding="utf-8")
    print((output_dir / "matrix_result.json").resolve())
    print(json.dumps(matrix_report, ensure_ascii=False, indent=2))
    if args.require_keys and executed != len(profiles):
        return 2
    return 0 if executed > 0 else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
