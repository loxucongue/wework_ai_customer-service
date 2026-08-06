from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "docs" / "parallel_gate_planner_refactor_plan.md"
MATRIX_PATH = ROOT / "docs" / "rule_ownership_matrix.md"
CHECKLIST_PATH = ROOT / "docs" / "reply_chain_refactor_execution_checklist.md"


def test_refactor_plan_keeps_review_and_test_gates() -> None:
    text = PLAN_PATH.read_text(encoding="utf-8")

    for marker in [
        "Gate 不是新的 Planner",
        "Gate 不是最终表达大脑",
        "Tool Planner 不输出客户话术",
        "Reply 是复杂场景最终业务大脑",
        "每阶段两轮 Review",
        "T0：合同与隔离测试",
        "T4：Join 确定性测试",
        "T6：并行组合测试",
        "T7：离线全链路仿真",
        "T8：Shadow 对比",
        "不得提交到 `main`，不得部署，不得主动发送真实客户消息",
        "Gate 必须提供非空静态 `direct_reply_candidate`，否则交 Reply 恢复表达",
        "依赖缺失或重复时只能进入 shadow blocker，不能提前执行",
    ]:
        assert marker in text


def test_refactor_plan_keeps_gate_and_planner_from_becoming_brains() -> None:
    text = PLAN_PATH.read_text(encoding="utf-8")

    for marker in [
        "Gate 是内容匹配和第一层路由节点，不是复杂场景的最终业务大脑",
        "最终判断客户意向度、心理状态和成交阶段",
        "Tool Planner 只规划只读工具事实，不能输出客户话术，不能判断客户心理",
        "Join 是确定性合并层，不是第三个模型大脑",
        "Reply 负责最终客户可见回复、复杂历史理解、客户当前意向、单一主线动作和语气",
        "复杂软拒绝、时间反复不确定、客户信任异议等场景即使不需要工具，也应进入 Reply",
    ]:
        assert marker in text


def test_rule_matrix_active_rules_have_target_owner_and_tests() -> None:
    rows = _matrix_rows()
    assert rows, "rule ownership matrix must not be empty"

    for row in rows:
        status = row["migration status"].strip()
        if status in {"active", "merged", "hard_boundary"}:
            assert row["target owner"].strip(), f"{row['rule_id']} missing target owner"
            assert row["regression tests"].strip(), f"{row['rule_id']} missing regression tests"


def test_tool_planner_target_does_not_own_business_semantics() -> None:
    rows = _matrix_rows()
    forbidden = ("psychology", "sales rhythm", "closing", "成交", "心理", "主线")

    for row in rows:
        target_owner = row["target owner"].lower()
        business_meaning = row["business meaning"].lower()
        if target_owner.startswith("tool planner"):
            assert not any(term.lower() in business_meaning for term in forbidden), row["rule_id"]


def test_superseded_order_precondition_stays_superseded() -> None:
    rows = {row["rule_id"]: row for row in _matrix_rows()}
    row = rows["order_required_before_payment_card"]

    assert row["migration status"] == "superseded"
    assert row["target owner"] == "None"


def test_rule_matrix_covers_recent_high_risk_business_areas() -> None:
    rows = {row["rule_id"]: row for row in _matrix_rows()}
    required = {
        "gate_not_business_brain",
        "offer_activity_facts",
        "project_scope_boundary",
        "effect_case_image_evidence",
        "store_visible_scope_only",
        "store_candidate_count_rule",
        "payment_no_order_precondition",
        "payment_after_paid_registration",
        "unknown_message_transfer_paid",
        "health_risk_priority",
        "explicit_reject_no_payment",
        "human_wechat_style",
        "sop_mainline_progression",
        "precision_answer_then_mainline",
    }

    missing = sorted(required.difference(rows))
    assert not missing, f"missing rule ownership rows: {missing}"

    for rule_id in required:
        assert rows[rule_id]["migration status"] == "active", rule_id
        assert rows[rule_id]["target owner"], rule_id


def test_rule_matrix_has_structural_refactor_review_gates() -> None:
    text = MATRIX_PATH.read_text(encoding="utf-8")

    for marker in [
        "Structural Refactor Review Gate",
        "rule_matrix_delta_review",
        "payload_isolation_review",
        "authority_snapshot_review",
        "reply_chain_authority_audit_v1",
        "reply_chain_timeline_window_audit_v1",
        "reply_chain_current_message_audit_v1",
        "reply_chain_fact_snapshot_audit_v1",
        "gate_commit_boundary_review",
        "branch_input_isolation_review",
        "final_expression_owner_review",
        "direct_reply_guard_review",
        "reply_chain_direct_reply_guard_audit_v1",
        "reply_handoff_readiness_review",
        "reply_final_brain_handoff_readiness_audit_v1",
        "reply_chain_release_review_checklist_v1",
        "reply_chain_behavior_switch_guard_v1",
        "commit_phase_shadow_review",
        "reply_chain_deferred_write_handoff_audit_v1",
        "business_wording_freeze_review",
        "model_semantics_ownership_review",
        "simulation_regression_review",
        "model_matrix_review",
        "rollback_evidence_review",
        "REPLY_FINAL_BRAIN_V2_ENABLED",
        "comparison diagnostics show no shadow replay diffs",
        "workflow_tests/test_reply_chain_shadow_payload_isolation.py",
        "workflow_tests/test_reply_chain_behavior_switch_guard.py",
        "workflow_tests/test_reply_chain_external_gate_evidence.py",
        "workflow_tests/test_reply_chain_shadow_context.py",
        "workflow_tests/test_reply_final_brain_handoff.py",
        "workflow_tests/test_parallel_reply_chain_shadow.py",
        "Offline simulation report covers SOP, precision QA, store, payment, paid registration, risk, and model-failure cases.",
        "reply_chain_refactor_model_matrix_v1",
        "must check all sixteen gates",
        "diagnostic evidence only",
        "must not be deployed from this branch",
    ]:
        assert marker in text


def test_execution_checklist_requires_safe_three_model_matrix_evidence() -> None:
    text = CHECKLIST_PATH.read_text(encoding="utf-8")

    for marker in [
        "schema_version=offline_reply_chain_simulation_report_v1",
        "summary.infrastructure_failures=0",
        "summary.acceptance.infrastructure_failures_zero=true",
        "safety.production_customer_messages_sent=false",
        "safety.production_writes_allowed=false",
        "safety.virtual_outbox_only=true",
        "safety.production_write_count=0",
        "review_artifacts.schema_version=offline_simulation_review_artifacts_v1",
        "request/event IDs, node trace names, tool call names",
        "run_refactor_model_matrix.py",
        "--profiles claude,gemini,openai",
        "--require-keys",
        "claude-opus-4-7",
        "gemini-3.5-flash",
        "gpt-5.4",
        "schema_version=reply_chain_refactor_model_matrix_v1",
        "profiles_requested",
        "profile_summary.semantic_pass_rate",
        "p50_ms",
        "p90_ms",
        "profile_summary.infrastructure_failures=0",
        "profile_summary.accepted_by_release_thresholds=true",
        "safety.api_keys_written_to_report=false",
        "safety.production_customer_messages_sent=false",
        "safety.production_writes_allowed=false",
        "reply_chain_behavior_switch_guard(model_matrix_report=...)",
        "reply_chain_shadow_bundle_audit(..., simulation_report=..., model_matrix_report=...)",
        "postcommit bundle and final behavior-switch guard aligned",
        "a valid matrix report",
        "authoritative evidence",
        "rg -n \"sk-[A-Za-z0-9]",
    ]:
        assert marker in text


def _matrix_rows() -> list[dict[str, str]]:
    lines = MATRIX_PATH.read_text(encoding="utf-8").splitlines()
    table_lines = [line for line in lines if line.startswith("| ") and " | " in line]
    header = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in table_lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells)))
    return rows
