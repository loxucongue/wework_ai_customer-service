from __future__ import annotations

import re
from typing import Any


MIN_REQUIRED_SIMULATION_SCENARIOS = 100
MIN_REQUIRED_SIMULATION_ATTEMPTS = 3
MIN_REQUIRED_CRITICAL_SIMULATION_ATTEMPTS = 5
SECRET_LIKE_PATTERN = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9][A-Za-z0-9_-]{10,}")
REQUIRED_SIMULATION_COVERAGE_CATEGORIES = (
    "门店V2",
    "SOP主线",
    "效果案例",
    "精准问答",
    "项目范围",
    "健康风险",
    "门店匹配",
    "门店工具",
    "门店异议",
    "SOP Gate",
    "预约金",
    "已付登记",
    "客户异议",
    "明确拒绝",
    "SOP Event",
    "消息归一",
    "模型恢复",
)


def payload_isolation_report_blockers(report: dict[str, Any]) -> list[str]:
    if report.get("schema_version") != "reply_chain_payload_isolation_audit_v1":
        return ["missing_payload_isolation_audit"]
    blockers: list[str] = []
    blockers.extend(_secret_like_value_blockers("payload_isolation", report))
    commit = _string_value(report.get("git_commit"))
    if not commit:
        blockers.append("payload_isolation_missing_git_commit")
    commit_set = _list_strings(report.get("git_commit_set"))
    if not commit_set:
        blockers.append("payload_isolation_missing_git_commit_set")
    elif len(set(commit_set)) != 1:
        blockers.append(f"payload_isolation_multiple_git_commits:{','.join(commit_set)}")
    elif commit and commit_set[0] != commit:
        blockers.append(f"payload_isolation_git_commit_set_mismatch:{commit_set[0]}!={commit}")
    shadow_fields = _list_strings(report.get("shadow_only_fields"))
    if not shadow_fields:
        blockers.append("payload_isolation_missing_shadow_only_fields")
    payloads_checked = set(_list_strings(report.get("payloads_checked")))
    for payload_name in (
        "planner",
        "reply",
        "sop_chat_gate_selector",
        "sop_chat_gate_messages",
    ):
        if payload_name not in payloads_checked:
            blockers.append(f"payload_isolation_missing_payload_check:{payload_name}")
    leaks = _dict(report.get("leaked_fields_by_payload"))
    for payload_name, leaked in leaks.items():
        for field in _list_strings(leaked):
            blockers.append(f"payload_isolation_leaked_field:{payload_name}:{field}")
    if report.get("payload_isolation_passed") is not True:
        blockers.append("payload_isolation_not_passed")
    if report.get("active_model_payloads_checked") is not True:
        blockers.append("payload_isolation_missing_active_payload_check")
    safety = _dict(report.get("safety"))
    for field, blocker in (
        ("audit_only", "payload_isolation_missing_audit_only_safety"),
        ("does_not_change_runtime_behavior", "payload_isolation_missing_no_runtime_change_safety"),
        ("does_not_send_customer_messages", "payload_isolation_missing_no_send_safety"),
        ("does_not_write_database", "payload_isolation_missing_no_write_safety"),
        ("does_not_call_models", "payload_isolation_missing_no_model_call_safety"),
    ):
        if safety.get(field) is not True:
            blockers.append(blocker)
    return blockers


def business_wording_freeze_report_blockers(report: dict[str, Any]) -> list[str]:
    if report.get("schema_version") != "reply_chain_business_wording_freeze_audit_v1":
        return ["missing_business_wording_freeze_audit"]
    blockers: list[str] = []
    blockers.extend(_secret_like_value_blockers("business_wording_freeze", report))
    commit = _string_value(report.get("git_commit"))
    if not commit:
        blockers.append("business_wording_freeze_missing_git_commit")
    commit_set = _list_strings(report.get("git_commit_set"))
    if not commit_set:
        blockers.append("business_wording_freeze_missing_git_commit_set")
    elif len(set(commit_set)) != 1:
        blockers.append(f"business_wording_freeze_multiple_git_commits:{','.join(commit_set)}")
    elif commit and commit_set[0] != commit:
        blockers.append(f"business_wording_freeze_git_commit_set_mismatch:{commit_set[0]}!={commit}")
    changed = _list_strings(report.get("changed_protected_paths"))
    if changed:
        blockers.extend(f"business_wording_freeze_protected_path_changed:{item}" for item in changed)
    if report.get("customer_visible_business_assets_unchanged") is not True:
        blockers.append("business_wording_freeze_assets_not_unchanged")
    safety = _dict(report.get("safety"))
    for field, blocker in (
        ("audit_only", "business_wording_freeze_missing_audit_only_safety"),
        ("does_not_change_runtime_behavior", "business_wording_freeze_missing_no_runtime_change_safety"),
        ("does_not_send_customer_messages", "business_wording_freeze_missing_no_send_safety"),
        ("does_not_write_database", "business_wording_freeze_missing_no_write_safety"),
        ("does_not_call_models", "business_wording_freeze_missing_no_model_call_safety"),
    ):
        if safety.get(field) is not True:
            blockers.append(blocker)
    return blockers


def rollback_evidence_report_blockers(report: dict[str, Any]) -> list[str]:
    if report.get("schema_version") != "reply_chain_refactor_rollback_evidence_v1":
        return ["missing_refactor_rollback_evidence"]
    blockers: list[str] = []
    blockers.extend(_secret_like_value_blockers("rollback_evidence", report))
    commit = _string_value(report.get("git_commit"))
    if not commit:
        blockers.append("rollback_evidence_missing_git_commit")
    commit_set = _list_strings(report.get("git_commit_set"))
    if not commit_set:
        blockers.append("rollback_evidence_missing_git_commit_set")
    elif len(set(commit_set)) != 1:
        blockers.append(f"rollback_evidence_multiple_git_commits:{','.join(commit_set)}")
    elif commit and commit_set[0] != commit:
        blockers.append(f"rollback_evidence_git_commit_set_mismatch:{commit_set[0]}!={commit}")
    branch = _string_value(report.get("branch"))
    expected = _string_value(report.get("expected_branch")) or "codex/reply-chain-refactor"
    if branch != expected:
        blockers.append(f"rollback_evidence_wrong_branch:{branch or 'missing'}")
    if report.get("branch_is_refactor") is not True:
        blockers.append("rollback_evidence_branch_not_refactor")
    if report.get("main_branch_untouched") is not True:
        blockers.append("rollback_evidence_main_branch_not_untouched")
    changed = _list_strings(report.get("changed_deployment_sensitive_paths"))
    if changed:
        blockers.extend(f"rollback_evidence_deployment_sensitive_path_changed:{item}" for item in changed)
    if report.get("deployment_sensitive_paths_unchanged") is not True:
        blockers.append("rollback_evidence_deployment_sensitive_paths_not_unchanged")
    rollback_plan = _dict(report.get("rollback_plan"))
    if rollback_plan.get("schema_version") != "reply_chain_behavior_switch_rollback_plan_v1":
        blockers.append("rollback_evidence_missing_rollback_plan")
    else:
        for field, blocker in (
            ("restore_flags_to_shadow_or_disabled", "rollback_evidence_missing_flag_restore"),
            ("revert_stage_commit", "rollback_evidence_missing_revert_stage_commit"),
            ("rerun_diagnostics_before_reenable", "rollback_evidence_missing_rerun_diagnostics"),
            ("no_deployment_from_refactor_branch", "rollback_evidence_missing_no_refactor_deploy"),
        ):
            if rollback_plan.get(field) is not True:
                blockers.append(blocker)
        if not _list_strings(rollback_plan.get("rollback_steps")):
            blockers.append("rollback_evidence_missing_rollback_steps")
    safety = _dict(report.get("safety"))
    for field, blocker in (
        ("audit_only", "rollback_evidence_missing_audit_only_safety"),
        ("does_not_change_runtime_behavior", "rollback_evidence_missing_no_runtime_change_safety"),
        ("does_not_send_customer_messages", "rollback_evidence_missing_no_send_safety"),
        ("does_not_write_database", "rollback_evidence_missing_no_write_safety"),
        ("does_not_call_models", "rollback_evidence_missing_no_model_call_safety"),
        ("does_not_deploy", "rollback_evidence_missing_no_deploy_safety"),
    ):
        if safety.get(field) is not True:
            blockers.append(blocker)
    return blockers


def model_semantics_ownership_report_blockers(report: dict[str, Any]) -> list[str]:
    if report.get("schema_version") != "reply_chain_model_semantics_ownership_audit_v1":
        return ["missing_model_semantics_ownership_audit"]
    blockers: list[str] = []
    blockers.extend(_secret_like_value_blockers("model_semantics_ownership", report))
    commit = _string_value(report.get("git_commit"))
    if not commit:
        blockers.append("model_semantics_ownership_missing_git_commit")
    commit_set = _list_strings(report.get("git_commit_set"))
    if not commit_set:
        blockers.append("model_semantics_ownership_missing_git_commit_set")
    elif len(set(commit_set)) != 1:
        blockers.append(f"model_semantics_ownership_multiple_git_commits:{','.join(commit_set)}")
    elif commit and commit_set[0] != commit:
        blockers.append(f"model_semantics_ownership_git_commit_set_mismatch:{commit_set[0]}!={commit}")
    if report.get("ownership_contract_checked") is not True:
        blockers.append("model_semantics_ownership_contract_not_checked")
    tool_must_not = set(_list_strings(report.get("tool_planner_must_not_own")))
    for item in ("customer_visible_text", "sales_psychology", "closing_move"):
        if item not in tool_must_not:
            blockers.append(f"model_semantics_ownership_tool_planner_missing_must_not_own:{item}")
    reply_owns = set(_list_strings(report.get("reply_owns")))
    for item in ("final_customer_visible_messages", "complex_turn_outcome", "single_mainline_action"):
        if item not in reply_owns:
            blockers.append(f"model_semantics_ownership_reply_missing_owns:{item}")
    code_must_not = set(_list_strings(report.get("code_must_not_own")))
    for item in ("normal_sales_intent", "objection_psychology", "sales_rhythm"):
        if item not in code_must_not:
            blockers.append(f"model_semantics_ownership_code_missing_must_not_own:{item}")
    if _int_value(report.get("tool_planner_legacy_residue_count")) != 0:
        blockers.append(f"model_semantics_ownership_tool_planner_residue:{report.get('tool_planner_legacy_residue_count')}")
    if report.get("tool_planner_only_ready") is not True:
        blockers.append("model_semantics_ownership_tool_planner_not_ready")
    if report.get("join_final_expression_boundary_schema") != "reply_final_expression_boundary_v1":
        blockers.append("model_semantics_ownership_missing_final_expression_boundary")
    if report.get("join_final_customer_message_owner") != "reply":
        blockers.append(f"model_semantics_ownership_join_owner_not_reply:{report.get('join_final_customer_message_owner') or 'missing'}")
    if report.get("join_generates_customer_visible_text") is not False:
        blockers.append("model_semantics_ownership_join_generates_text")
    if report.get("join_decides_sales_psychology") is not False:
        blockers.append("model_semantics_ownership_join_decides_sales_psychology")
    if report.get("direct_reply_scope") != "static_candidate_only_no_dynamic_facts":
        blockers.append(f"model_semantics_ownership_direct_reply_scope_invalid:{report.get('direct_reply_scope') or 'missing'}")
    if report.get("direct_reply_final_customer_message_owner") != "validated_static_gate_candidate":
        blockers.append("model_semantics_ownership_direct_reply_not_static_candidate")
    if report.get("direct_reply_requires_commit_validation") is not True:
        blockers.append("model_semantics_ownership_direct_reply_missing_commit_validation")
    if report.get("reply_handoff_schema") != "reply_final_brain_handoff_shadow_v1":
        blockers.append("model_semantics_ownership_missing_reply_handoff")
    if report.get("reply_handoff_ready") is not True:
        blockers.append("model_semantics_ownership_reply_handoff_not_ready")
    if report.get("legacy_business_field_mapping_schema") != "reply_legacy_field_mapping_audit_v1":
        blockers.append("model_semantics_ownership_missing_legacy_mapping")
    unmapped = _list_strings(report.get("unmapped_legacy_business_fields"))
    blockers.extend(f"model_semantics_ownership_unmapped_legacy_field:{item}" for item in unmapped)
    if report.get("parallel_shadow_schema") != "parallel_reply_chain_shadow_v1":
        blockers.append("model_semantics_ownership_missing_parallel_shadow")
    normalizer_audit = _dict(report.get("normalizer_boundary_audit"))
    if normalizer_audit.get("schema_version") != "planner_normalizer_boundary_audit_v1":
        blockers.append("model_semantics_ownership_missing_normalizer_boundary_audit")
    if normalizer_audit.get("normalizer_boundary_passed") is not True:
        blockers.append("model_semantics_ownership_normalizer_boundary_not_passed")
    normalizer_summary = _dict(normalizer_audit.get("summary"))
    if _int_value(normalizer_summary.get("semantic_overreach_count")) != 0:
        blockers.append(
            "model_semantics_ownership_normalizer_semantic_overreach:"
            f"{normalizer_summary.get('semantic_overreach_count')}"
        )
    blockers.extend(
        f"model_semantics_ownership_normalizer_boundary:{item}"
        for item in _list_strings(normalizer_audit.get("blockers"))
    )
    blockers.extend(f"model_semantics_ownership_report_blocker:{item}" for item in _list_strings(report.get("blockers")))
    if report.get("semantic_ownership_passed") is not True:
        blockers.append("model_semantics_ownership_not_passed")
    safety = _dict(report.get("safety"))
    for field, blocker in (
        ("audit_only", "model_semantics_ownership_missing_audit_only_safety"),
        ("does_not_change_runtime_behavior", "model_semantics_ownership_missing_no_runtime_change_safety"),
        ("does_not_send_customer_messages", "model_semantics_ownership_missing_no_send_safety"),
        ("does_not_write_database", "model_semantics_ownership_missing_no_write_safety"),
        ("does_not_call_models", "model_semantics_ownership_missing_no_model_call_safety"),
        ("does_not_call_external_tools", "model_semantics_ownership_missing_no_external_tool_safety"),
    ):
        if safety.get(field) is not True:
            blockers.append(blocker)
    return blockers


def simulation_report_blockers(simulation: dict[str, Any]) -> list[str]:
    if simulation.get("schema_version") != "offline_reply_chain_simulation_report_v1":
        return ["missing_offline_simulation_report"]
    blockers: list[str] = []
    blockers.extend(_secret_like_value_blockers("simulation", simulation))
    scenario_count = _int_value(simulation.get("scenario_count"))
    attempt_count = _int_value(simulation.get("attempt_count"))
    commit = _string_value(simulation.get("git_commit"))
    if scenario_count < MIN_REQUIRED_SIMULATION_SCENARIOS:
        blockers.append(f"simulation_scenario_count_below_100:{simulation.get('scenario_count')}")
    if attempt_count < scenario_count:
        blockers.append(
            f"simulation_attempt_count_below_scenario_count:{simulation.get('attempt_count')}<{simulation.get('scenario_count')}"
        )
    if not commit:
        blockers.append("simulation_missing_git_commit")
    commit_set = _list_strings(simulation.get("git_commit_set"))
    if not commit_set:
        blockers.append("simulation_missing_git_commit_set")
    elif len(set(commit_set)) != 1:
        blockers.append(f"simulation_multiple_git_commits:{','.join(commit_set)}")
    elif commit and commit_set[0] != commit:
        blockers.append(f"simulation_git_commit_set_mismatch:{commit_set[0]}!={commit}")
    scope = _dict(simulation.get("evaluation_scope"))
    if scope.get("schema_version") != "offline_simulation_scope_v1":
        blockers.append("simulation_missing_evaluation_scope")
    elif scope.get("full_release_gate_candidate") is not True:
        blockers.append("simulation_not_full_release_gate_candidate")
    run_options = _dict(simulation.get("run_options"))
    if run_options.get("schema_version") != "offline_simulation_run_options_v1":
        blockers.append("simulation_missing_run_options")
    else:
        if run_options.get("skip_review") is not False:
            blockers.append("simulation_skip_review_not_allowed")
        attempts = _int_value(run_options.get("attempts"))
        critical_attempts = _int_value(run_options.get("critical_attempts"))
        if attempts < MIN_REQUIRED_SIMULATION_ATTEMPTS:
            blockers.append(f"simulation_attempts_below_required:{attempts}<{MIN_REQUIRED_SIMULATION_ATTEMPTS}")
        if critical_attempts < MIN_REQUIRED_CRITICAL_SIMULATION_ATTEMPTS:
            blockers.append(
                "simulation_critical_attempts_below_required:"
                f"{critical_attempts}<{MIN_REQUIRED_CRITICAL_SIMULATION_ATTEMPTS}"
            )
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
    baseline_comparison = _dict(simulation.get("baseline_comparison"))
    if baseline_comparison.get("schema_version") != "offline_simulation_baseline_comparison_v1":
        blockers.append("simulation_missing_baseline_comparison")
    else:
        if baseline_comparison.get("available") is not True:
            blockers.append("simulation_baseline_comparison_unavailable")
        for item in _list_strings(baseline_comparison.get("regressed")):
            blockers.append(f"simulation_baseline_regressed:{item}")
    coverage = _dict(simulation.get("coverage"))
    if coverage.get("schema_version") != "offline_simulation_coverage_audit_v1":
        blockers.append("simulation_missing_coverage_audit")
    required_categories = _list_strings(coverage.get("required_categories"))
    if not required_categories:
        blockers.append("simulation_missing_required_category_manifest")
    else:
        required_set = set(required_categories)
        canonical_set = set(REQUIRED_SIMULATION_COVERAGE_CATEGORIES)
        for item in REQUIRED_SIMULATION_COVERAGE_CATEGORIES:
            if item not in required_set:
                blockers.append(f"simulation_required_category_manifest_missing:{item}")
        for item in sorted(required_set - canonical_set):
            blockers.append(f"simulation_required_category_manifest_unapproved:{item}")
    missing_categories = _list_strings(coverage.get("missing_required_categories"))
    if missing_categories:
        blockers.extend(f"simulation_missing_required_category:{item}" for item in missing_categories)
    missing_critical_categories = _list_strings(coverage.get("missing_critical_required_categories"))
    if missing_critical_categories:
        blockers.extend(f"simulation_missing_critical_required_category:{item}" for item in missing_critical_categories)
    summary = _dict(simulation.get("summary"))
    if _int_value(summary.get("infrastructure_failures")) != 0:
        blockers.append(f"simulation_infrastructure_failures:{summary.get('infrastructure_failures')}")
    evaluable_attempts = _int_value(summary.get("evaluable_attempts"))
    if evaluable_attempts < attempt_count:
        blockers.append(
            "simulation_evaluable_attempts_below_attempt_count:"
            f"{summary.get('evaluable_attempts')}<{simulation.get('attempt_count')}"
        )
    scenario_summary = _dict(simulation.get("scenario_summary"))
    if not scenario_summary:
        blockers.append("simulation_missing_scenario_summary")
    elif len(scenario_summary) < scenario_count:
        blockers.append(
            "simulation_scenario_summary_count_below_scenario_count:"
            f"{len(scenario_summary)}<{simulation.get('scenario_count')}"
        )
    for scenario_id, item in scenario_summary.items():
        if not isinstance(item, dict):
            blockers.append(f"simulation_scenario_summary_invalid:{scenario_id}")
            continue
        required_attempts = (
            MIN_REQUIRED_CRITICAL_SIMULATION_ATTEMPTS
            if item.get("critical") is True
            else MIN_REQUIRED_SIMULATION_ATTEMPTS
        )
        attempts = _int_value(item.get("attempts"))
        if attempts < required_attempts:
            blockers.append(f"simulation_scenario_attempts_below_required:{scenario_id}:{attempts}<{required_attempts}")
        hard_passes = _int_value(item.get("hard_passes"))
        if hard_passes < attempts:
            blockers.append(f"simulation_scenario_hard_passes_below_attempts:{scenario_id}:{hard_passes}<{attempts}")
        infrastructure_failures = _int_value(item.get("infrastructure_failures"))
        if infrastructure_failures != 0:
            blockers.append(f"simulation_scenario_infrastructure_failures:{scenario_id}:{infrastructure_failures}")
    acceptance = _dict(summary.get("acceptance"))
    for field, blocker in (
        ("hard_errors_zero", "simulation_hard_error_acceptance_missing_or_false"),
        ("semantic_review_complete", "simulation_semantic_review_incomplete"),
        ("semantic_at_least_90", "simulation_semantic_acceptance_missing_or_false"),
        ("critical_all_pass", "simulation_critical_acceptance_missing_or_false"),
    ):
        if acceptance.get(field) is not True:
            blockers.append(blocker)
    if acceptance.get("infrastructure_failures_zero") is not True:
        blockers.append("simulation_infrastructure_acceptance_missing_or_false")
    if acceptance.get("scenario_coverage_complete") is not True:
        blockers.append("simulation_scenario_coverage_incomplete")
    if acceptance.get("isolation_audit_passed") is not True:
        blockers.append("simulation_isolation_acceptance_missing_or_false")
    if acceptance.get("baseline_comparison_passed") is not True:
        blockers.append("simulation_baseline_acceptance_missing_or_false")
    if acceptance.get("semantic_ownership_passed") is not True:
        blockers.append("simulation_semantic_ownership_acceptance_missing_or_false")
    ownership = _dict(simulation.get("semantic_ownership_audit"))
    if ownership.get("schema_version") != "offline_simulation_semantic_ownership_audit_v1":
        blockers.append("simulation_missing_semantic_ownership_audit")
    else:
        if _int_value(ownership.get("result_count")) < attempt_count:
            blockers.append(
                "simulation_semantic_ownership_result_count_below_attempt_count:"
                f"{ownership.get('result_count')}<{simulation.get('attempt_count')}"
            )
        if _int_value(ownership.get("evidence_result_count")) < attempt_count:
            blockers.append(
                "simulation_semantic_ownership_evidence_below_attempt_count:"
                f"{ownership.get('evidence_result_count')}<{simulation.get('attempt_count')}"
            )
        if _int_value(ownership.get("missing_evidence_count")) != 0:
            blockers.append(f"simulation_semantic_ownership_missing_evidence:{ownership.get('missing_evidence_count')}")
        if _int_value(ownership.get("violation_count")) != 0:
            blockers.append(f"simulation_semantic_ownership_violations:{ownership.get('violation_count')}")
        if ownership.get("passed") is not True:
            blockers.append("simulation_semantic_ownership_not_passed")
        required = set(_list_strings(ownership.get("required_evidence")))
        for item in (
            "chat_gate_commit_boundary_v1",
            "tool_plan_preview_v2",
            "reply_chain_join_shadow_v1",
            "parallel_reply_chain_shadow_v1",
        ):
            if item not in required:
                blockers.append(f"simulation_semantic_ownership_missing_required_evidence:{item}")
    effect_review = _dict(simulation.get("effect_review"))
    if effect_review.get("schema_version") != "offline_simulation_effect_review_v1":
        blockers.append("simulation_missing_effect_review")
    else:
        if _int_value(effect_review.get("result_count")) < attempt_count:
            blockers.append(
                "simulation_effect_review_result_count_below_attempt_count:"
                f"{effect_review.get('result_count')}<{simulation.get('attempt_count')}"
            )
        if hard_error_count := _int_value(simulation.get("hard_error_count")):
            if _int_value(effect_review.get("hard_or_infra_count")) <= 0:
                blockers.append(f"simulation_effect_review_missing_hard_error_samples:{hard_error_count}")
        if pass_rate < 0.9 and _int_value(effect_review.get("low_score_count")) <= 0:
            blockers.append("simulation_effect_review_missing_low_score_samples")
        effect_items = effect_review.get("items")
        if not isinstance(effect_items, list):
            blockers.append("simulation_effect_review_missing_items")
        else:
            blockers.extend(_effect_review_item_blockers(effect_items))
    isolation = _dict(simulation.get("isolation_audit"))
    if isolation.get("schema_version") != "offline_simulation_isolation_summary_v1":
        blockers.append("simulation_missing_isolation_audit")
    else:
        if _int_value(isolation.get("result_count")) < scenario_count:
            blockers.append(
                "simulation_isolation_result_count_below_scenario_count:"
                f"{isolation.get('result_count')}<{simulation.get('scenario_count')}"
            )
        if _int_value(isolation.get("missing_result_count")) != 0:
            blockers.append(f"simulation_isolation_missing_results:{isolation.get('missing_result_count')}")
        if _int_value(isolation.get("failed_result_count")) != 0:
            blockers.append(f"simulation_isolation_failed_results:{isolation.get('failed_result_count')}")
        for field, blocker in (
            ("passed", "simulation_isolation_not_passed"),
            ("run_dirs_under_tmp_simulation", "simulation_isolation_run_dir_not_isolated"),
            ("paths_within_run_dir", "simulation_isolation_paths_escape_run_dir"),
            ("connector_urls_simulation_only", "simulation_isolation_connector_url_not_simulation"),
            ("adapters_simulation_only", "simulation_isolation_non_simulation_adapter"),
            ("identity_simulation_scoped", "simulation_isolation_identity_not_scoped"),
        ):
            if isolation.get(field) is not True:
                blockers.append(blocker)
        if isolation.get("real_connector_credentials_present") is not False:
            blockers.append("simulation_isolation_real_credentials_present")
    review_artifacts = _dict(simulation.get("review_artifacts"))
    if review_artifacts.get("schema_version") != "offline_simulation_review_artifacts_v1":
        blockers.append("simulation_missing_review_artifacts")
    else:
        if _int_value(review_artifacts.get("result_count")) < scenario_count:
            blockers.append(
                "simulation_review_artifacts_result_count_below_scenario_count:"
                f"{review_artifacts.get('result_count')}<{simulation.get('scenario_count')}"
            )
        if _int_value(review_artifacts.get("result_count")) < attempt_count:
            blockers.append(
                "simulation_review_artifacts_result_count_below_attempt_count:"
                f"{review_artifacts.get('result_count')}<{simulation.get('attempt_count')}"
            )
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
        else:
            if len(review_artifacts["results"]) < attempt_count:
                blockers.append(
                    "simulation_review_artifacts_results_length_below_attempt_count:"
                    f"{len(review_artifacts['results'])}<{simulation.get('attempt_count')}"
                )
            blockers.extend(_review_artifact_result_blockers(review_artifacts["results"]))
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


def _effect_review_item_blockers(items: list[Any]) -> list[str]:
    blockers: list[str] = []
    required_fields = (
        "scenario_id",
        "attempt",
        "issue_types",
        "customer_input_excerpt",
        "assistant_reply_excerpt",
        "review_reasons",
    )
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            blockers.append(f"simulation_effect_review_invalid_item:{index}")
            continue
        scenario_id = _string_value(item.get("scenario_id")) or f"index_{index}"
        for field in required_fields:
            if field not in item:
                blockers.append(f"simulation_effect_review_item_missing_field:{scenario_id}:{field}")
        issue_types = item.get("issue_types")
        if not isinstance(issue_types, list):
            blockers.append(f"simulation_effect_review_item_field_not_list:{scenario_id}:issue_types")
            continue
        if "semantic_low_score" in {str(value) for value in issue_types}:
            scores = item.get("scores")
            if not isinstance(scores, dict) or not scores:
                blockers.append(f"simulation_effect_review_item_missing_scores:{scenario_id}")
    return blockers


def _review_artifact_result_blockers(items: list[Any]) -> list[str]:
    blockers: list[str] = []
    list_fields = ("request_ids", "event_ids", "node_trace_names", "tool_call_names")
    numeric_fields = ("sync_reply_message_count", "outbox_batch_count", "simulated_write_count")
    required_fields = ("scenario_id", "attempt", *list_fields, *numeric_fields)
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            blockers.append(f"simulation_review_artifacts_invalid_result:{index}")
            continue
        scenario_id = _string_value(item.get("scenario_id")) or f"index_{index}"
        for field in required_fields:
            if field not in item:
                blockers.append(f"simulation_review_artifacts_result_missing_field:{scenario_id}:{field}")
        for field in list_fields:
            if field in item and not isinstance(item.get(field), list):
                blockers.append(f"simulation_review_artifacts_result_field_not_list:{scenario_id}:{field}")
        for field in numeric_fields:
            if field in item and not _has_number(item.get(field)):
                blockers.append(f"simulation_review_artifacts_result_field_not_number:{scenario_id}:{field}")
    return blockers


def model_matrix_report_blockers(model_matrix: dict[str, Any]) -> list[str]:
    if model_matrix.get("schema_version") != "reply_chain_refactor_model_matrix_v1":
        return ["missing_model_matrix_report"]
    blockers: list[str] = []
    blockers.extend(_secret_like_value_blockers("model_matrix", model_matrix))
    commit = _string_value(model_matrix.get("git_commit"))
    if not commit:
        blockers.append("model_matrix_missing_git_commit")
    commit_set = _list_strings(model_matrix.get("git_commit_set"))
    if not commit_set:
        blockers.append("model_matrix_missing_git_commit_set")
    elif len(set(commit_set)) != 1:
        blockers.append(f"model_matrix_multiple_git_commits:{','.join(commit_set)}")
    elif commit and commit_set[0] != commit:
        blockers.append(f"model_matrix_git_commit_set_mismatch:{commit_set[0]}!={commit}")
    relay_base_url = _string_value(model_matrix.get("relay_base_url")).rstrip("/")
    if relay_base_url != "https://linkai.shop/v1":
        blockers.append(f"model_matrix_unapproved_relay_base_url:{relay_base_url or 'missing'}")
    scope = _dict(model_matrix.get("evaluation_scope"))
    if scope.get("schema_version") != "reply_chain_refactor_model_matrix_scope_v1":
        blockers.append("model_matrix_missing_evaluation_scope")
    elif scope.get("full_release_gate_candidate") is not True:
        blockers.append("model_matrix_not_full_release_gate_candidate")
    run_options = _dict(model_matrix.get("run_options"))
    if run_options.get("schema_version") != "reply_chain_refactor_model_matrix_run_options_v1":
        blockers.append("model_matrix_missing_run_options")
    else:
        if run_options.get("skip_review") is not False:
            blockers.append("model_matrix_skip_review_not_allowed")
        attempts = _int_value(run_options.get("attempts"))
        critical_attempts = _int_value(run_options.get("critical_attempts"))
        if attempts < MIN_REQUIRED_SIMULATION_ATTEMPTS:
            blockers.append(f"model_matrix_attempts_below_required:{attempts}<{MIN_REQUIRED_SIMULATION_ATTEMPTS}")
        if critical_attempts < MIN_REQUIRED_CRITICAL_SIMULATION_ATTEMPTS:
            blockers.append(
                "model_matrix_critical_attempts_below_required:"
                f"{critical_attempts}<{MIN_REQUIRED_CRITICAL_SIMULATION_ATTEMPTS}"
            )
        if run_options.get("baseline_path_present") is not True:
            blockers.append("model_matrix_missing_baseline_path")
    requested = set(_list_strings(model_matrix.get("profiles_requested")))
    required_models = {
        "claude": "claude-opus-4-7",
        "gemini": "gemini-3.5-flash",
        "openai": "gpt-5.4",
    }
    required = set(required_models)
    missing = sorted(required - requested)
    if missing:
        blockers.extend(f"model_matrix_missing_requested_profile:{item}" for item in missing)
    if _int_value(model_matrix.get("executed_profile_count")) != len(required):
        blockers.append(f"model_matrix_executed_profile_count_mismatch:{model_matrix.get('executed_profile_count')}")
    profiles = model_matrix.get("profiles") if isinstance(model_matrix.get("profiles"), list) else []
    ranking = model_matrix.get("ranking") if isinstance(model_matrix.get("ranking"), list) else []
    completed_names = {
        str((_dict(item.get("model_profile")).get("name") or "")).strip()
        for item in profiles
        if isinstance(item, dict) and item.get("status") == "completed"
    }
    missing_completed = sorted(required - completed_names)
    if missing_completed:
        blockers.extend(f"model_matrix_profile_not_completed:{item}" for item in missing_completed)
    ranking_names = {str(_dict(item).get("name") or "").strip() for item in ranking if isinstance(item, dict)}
    missing_ranked = sorted(completed_names - ranking_names)
    if missing_ranked:
        blockers.extend(f"model_matrix_ranking_missing_completed_profile:{item}" for item in missing_ranked)
    for item in ranking:
        if not isinstance(item, dict):
            continue
        ranking_name = str(item.get("name") or "unknown").strip() or "unknown"
        for field in (
            "semantic_pass_rate",
            "hard_error_count",
            "p90_ms",
            "effect_issue_count",
            "effect_low_score_count",
            "effect_hard_or_infra_count",
        ):
            if not _has_number(item.get(field)):
                blockers.append(f"model_matrix_ranking_missing_{field}:{ranking_name}")
    for item in profiles:
        if not isinstance(item, dict) or item.get("status") != "timed_out":
            continue
        summary = _dict(item.get("profile_summary"))
        timeout = summary.get("timeout_seconds")
        suffix = f":{timeout}" if timeout not in (None, "") else ""
        blockers.append(f"model_matrix_profile_timed_out:{_profile_name(item)}{suffix}")
    accepted = False
    for item in profiles:
        if not isinstance(item, dict) or item.get("status") != "completed":
            continue
        profile = _dict(item.get("model_profile"))
        profile_name = str(profile.get("name") or "").strip()
        expected_model = required_models.get(profile_name)
        observed_model = str(profile.get("model") or "").strip()
        if str(profile.get("protocol") or "").strip() != "openai-compatible relay":
            blockers.append(f"model_matrix_profile_protocol_mismatch:{profile_name or _profile_name(item)}")
        if profile.get("api_key_value_logged") is not False:
            blockers.append(f"model_matrix_profile_key_redaction_missing:{profile_name or _profile_name(item)}")
        if expected_model and observed_model != expected_model:
            blockers.append(
                f"model_matrix_profile_model_mismatch:{profile_name}:{observed_model or 'missing'}"
            )
        summary = _dict(item.get("profile_summary"))
        if not _has_number(summary.get("semantic_pass_rate")):
            blockers.append(f"model_matrix_missing_semantic_pass_rate:{_profile_name(item)}")
        if not _has_number(summary.get("hard_error_count")):
            blockers.append(f"model_matrix_missing_hard_error_count:{_profile_name(item)}")
        if not _has_number(summary.get("p50_ms")):
            blockers.append(f"model_matrix_missing_p50:{_profile_name(item)}")
        if not _has_number(summary.get("p90_ms")):
            blockers.append(f"model_matrix_missing_p90:{_profile_name(item)}")
        failed_critical = summary.get("failed_critical_scenarios")
        if not isinstance(failed_critical, list):
            blockers.append(f"model_matrix_missing_failed_critical_scenarios:{_profile_name(item)}")
        if not _has_number(summary.get("effect_issue_count")):
            blockers.append(f"model_matrix_missing_effect_issue_count:{_profile_name(item)}")
        if not _has_number(summary.get("effect_low_score_count")):
            blockers.append(f"model_matrix_missing_effect_low_score_count:{_profile_name(item)}")
        if not _has_number(summary.get("effect_hard_or_infra_count")):
            blockers.append(f"model_matrix_missing_effect_hard_or_infra_count:{_profile_name(item)}")
        if summary.get("baseline_comparison_available") is not True:
            blockers.append(f"model_matrix_profile_missing_baseline_comparison:{_profile_name(item)}")
        if not _has_number(summary.get("baseline_regression_count")):
            blockers.append(f"model_matrix_missing_baseline_regression_count:{_profile_name(item)}")
        blockers.extend(_model_matrix_profile_artifact_blockers(item))
        if summary.get("accepted_by_release_thresholds") is True:
            accepted = True
            if _int_value(summary.get("hard_error_count")) != 0:
                blockers.append(
                    f"model_matrix_accepted_profile_has_hard_errors:{_profile_name(item)}:"
                    f"{summary.get('hard_error_count')}"
                )
            accepted_failed_critical = _list_strings(summary.get("failed_critical_scenarios"))
            if accepted_failed_critical:
                blockers.extend(
                    f"model_matrix_accepted_profile_failed_critical:{_profile_name(item)}:{scenario}"
                    for scenario in accepted_failed_critical
                )
            try:
                profile_semantic = float(summary.get("semantic_pass_rate") or 0.0)
            except (TypeError, ValueError):
                profile_semantic = 0.0
            if profile_semantic < 0.9:
                blockers.append(
                    f"model_matrix_accepted_profile_semantic_below_90:{_profile_name(item)}:{profile_semantic:.3f}"
                )
            if "infrastructure_failures" not in summary:
                blockers.append(f"model_matrix_accepted_profile_missing_infrastructure_failures:{_profile_name(item)}")
            elif _int_value(summary.get("infrastructure_failures")) != 0:
                blockers.append(
                    f"model_matrix_accepted_profile_has_infrastructure_failures:{_profile_name(item)}:"
                    f"{summary.get('infrastructure_failures')}"
                )
            if summary.get("baseline_comparison_available") is not True:
                blockers.append(f"model_matrix_accepted_profile_missing_baseline:{_profile_name(item)}")
            baseline_regressions = _int_value(summary.get("baseline_regression_count"))
            if baseline_regressions != 0:
                blockers.append(
                    f"model_matrix_accepted_profile_has_baseline_regressions:{_profile_name(item)}:"
                    f"{summary.get('baseline_regression_count')}"
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


def _model_matrix_profile_artifact_blockers(item: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    profile_name = _profile_name(item)
    artifacts = _dict(item.get("profile_artifacts"))
    if artifacts.get("schema_version") != "reply_chain_refactor_model_profile_artifacts_v1":
        return [f"model_matrix_profile_missing_artifacts:{profile_name}"]
    for field in ("result_json_path", "report_md_path"):
        if not _string_value(artifacts.get(field)):
            blockers.append(f"model_matrix_profile_artifact_missing_{field}:{profile_name}")
    for field, blocker in (
        ("result_json_written", "model_matrix_profile_result_json_not_written"),
        ("report_md_written", "model_matrix_profile_report_md_not_written"),
    ):
        if artifacts.get(field) is not True:
            blockers.append(f"{blocker}:{profile_name}")
    scenario_count = _int_value(artifacts.get("scenario_count"))
    attempt_count = _int_value(artifacts.get("attempt_count"))
    effect_count = _int_value(artifacts.get("effect_review_result_count"))
    review_artifact_count = _int_value(artifacts.get("review_artifacts_result_count"))
    if scenario_count < MIN_REQUIRED_SIMULATION_SCENARIOS:
        blockers.append(
            "model_matrix_profile_artifact_scenario_count_below_100:"
            f"{profile_name}:{scenario_count}"
        )
    if attempt_count <= 0:
        blockers.append(f"model_matrix_profile_artifact_missing_attempt_count:{profile_name}")
    elif attempt_count < scenario_count:
        blockers.append(
            "model_matrix_profile_artifact_attempt_count_below_scenario_count:"
            f"{profile_name}:{attempt_count}<{scenario_count}"
        )
    if effect_count < attempt_count:
        blockers.append(
            "model_matrix_profile_effect_review_below_attempt_count:"
            f"{profile_name}:{effect_count}<{attempt_count}"
        )
    if review_artifact_count < attempt_count:
        blockers.append(
            "model_matrix_profile_review_artifacts_below_attempt_count:"
            f"{profile_name}:{review_artifact_count}<{attempt_count}"
        )
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


def _contains_secret_like_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(SECRET_LIKE_PATTERN.search(value))
    if isinstance(value, dict):
        return any(_contains_secret_like_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_secret_like_value(item) for item in value)
    return False


def _secret_like_value_blockers(label: str, value: Any) -> list[str]:
    if _contains_secret_like_value(value):
        return [f"{label}_contains_secret_like_value"]
    return []


def _list_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _string_value(value: Any) -> str:
    return str(value or "").strip() if isinstance(value, (str, int)) else ""
