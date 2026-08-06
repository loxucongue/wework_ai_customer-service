from __future__ import annotations

from typing import Any


def simulation_report_blockers(simulation: dict[str, Any]) -> list[str]:
    if simulation.get("schema_version") != "offline_reply_chain_simulation_report_v1":
        return ["missing_offline_simulation_report"]
    blockers: list[str] = []
    if simulation.get("hard_error_count") not in (0, "0"):
        blockers.append(f"simulation_hard_errors:{simulation.get('hard_error_count')}")
    try:
        pass_rate = float(simulation.get("semantic_pass_rate") or 0.0)
    except (TypeError, ValueError):
        pass_rate = 0.0
    if pass_rate < 0.9:
        blockers.append(f"simulation_semantic_pass_rate_below_90:{pass_rate:.3f}")
    failed_critical = _list_strings(simulation.get("failed_critical_scenarios"))
    if failed_critical:
        blockers.extend(f"simulation_critical_failed:{item}" for item in failed_critical)
    coverage = _dict(simulation.get("coverage"))
    if coverage.get("schema_version") != "offline_simulation_coverage_audit_v1":
        blockers.append("simulation_missing_coverage_audit")
    missing_categories = _list_strings(coverage.get("missing_required_categories"))
    if missing_categories:
        blockers.extend(f"simulation_missing_required_category:{item}" for item in missing_categories)
    summary = _dict(simulation.get("summary"))
    if _int_value(summary.get("infrastructure_failures")) != 0:
        blockers.append(f"simulation_infrastructure_failures:{summary.get('infrastructure_failures')}")
    acceptance = _dict(summary.get("acceptance"))
    if acceptance.get("infrastructure_failures_zero") is not True:
        blockers.append("simulation_infrastructure_acceptance_missing_or_false")
    if acceptance.get("scenario_coverage_complete") is not True:
        blockers.append("simulation_scenario_coverage_incomplete")
    review_artifacts = _dict(simulation.get("review_artifacts"))
    if review_artifacts.get("schema_version") != "offline_simulation_review_artifacts_v1":
        blockers.append("simulation_missing_review_artifacts")
    else:
        for field in (
            "request_count",
            "event_count",
            "tool_call_count",
            "outbox_batch_count",
            "simulated_write_count",
        ):
            if field not in review_artifacts:
                blockers.append(f"simulation_review_artifacts_missing_field:{field}")
        if not isinstance(review_artifacts.get("results"), list):
            blockers.append("simulation_review_artifacts_missing_results")
    safety = _dict(simulation.get("safety"))
    if safety.get("production_customer_messages_sent") is not False:
        blockers.append("simulation_missing_no_customer_send_safety")
    if safety.get("production_writes_allowed") is not False:
        blockers.append("simulation_missing_no_production_write_safety")
    if safety.get("virtual_outbox_only") is not True:
        blockers.append("simulation_missing_virtual_outbox_safety")
    if _int_value(safety.get("production_write_count")) != 0:
        blockers.append(f"simulation_production_writes:{safety.get('production_write_count')}")
    return blockers


def model_matrix_report_blockers(model_matrix: dict[str, Any]) -> list[str]:
    if model_matrix.get("schema_version") != "reply_chain_refactor_model_matrix_v1":
        return ["missing_model_matrix_report"]
    blockers: list[str] = []
    requested = set(_list_strings(model_matrix.get("profiles_requested")))
    required = {"claude", "gemini", "openai"}
    missing = sorted(required - requested)
    if missing:
        blockers.extend(f"model_matrix_missing_requested_profile:{item}" for item in missing)
    profiles = model_matrix.get("profiles") if isinstance(model_matrix.get("profiles"), list) else []
    completed_names = {
        str((_dict(item.get("model_profile")).get("name") or "")).strip()
        for item in profiles
        if isinstance(item, dict) and item.get("status") == "completed"
    }
    missing_completed = sorted(required - completed_names)
    if missing_completed:
        blockers.extend(f"model_matrix_profile_not_completed:{item}" for item in missing_completed)
    accepted = False
    for item in profiles:
        if not isinstance(item, dict) or item.get("status") != "completed":
            continue
        summary = _dict(item.get("profile_summary"))
        if not _has_number(summary.get("semantic_pass_rate")):
            blockers.append(f"model_matrix_missing_semantic_pass_rate:{_profile_name(item)}")
        if not _has_number(summary.get("p50_ms")):
            blockers.append(f"model_matrix_missing_p50:{_profile_name(item)}")
        if not _has_number(summary.get("p90_ms")):
            blockers.append(f"model_matrix_missing_p90:{_profile_name(item)}")
        if summary.get("accepted_by_release_thresholds") is True:
            accepted = True
            if "infrastructure_failures" not in summary:
                blockers.append(f"model_matrix_accepted_profile_missing_infrastructure_failures:{_profile_name(item)}")
            elif _int_value(summary.get("infrastructure_failures")) != 0:
                blockers.append(
                    f"model_matrix_accepted_profile_has_infrastructure_failures:{_profile_name(item)}:"
                    f"{summary.get('infrastructure_failures')}"
                )
    if not accepted:
        blockers.append("model_matrix_no_candidate_meets_release_thresholds")
    safety = _dict(model_matrix.get("safety"))
    if safety.get("api_keys_written_to_report") is not False:
        blockers.append("model_matrix_missing_key_redaction_safety")
    if safety.get("production_customer_messages_sent") is not False:
        blockers.append("model_matrix_missing_no_send_safety")
    if safety.get("production_writes_allowed") is not False:
        blockers.append("model_matrix_missing_no_write_safety")
    return blockers


def _profile_name(item: dict[str, Any]) -> str:
    return str(_dict(item.get("model_profile")).get("name") or "unknown")


def _has_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
