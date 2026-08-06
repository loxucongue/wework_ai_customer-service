from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "docs" / "parallel_gate_planner_refactor_plan.md"
MATRIX_PATH = ROOT / "docs" / "rule_ownership_matrix.md"


def test_refactor_plan_keeps_review_and_test_gates() -> None:
    text = PLAN_PATH.read_text(encoding="utf-8")

    for marker in [
        "Gate 不是新的 Planner",
        "Tool Planner 不输出客户话术",
        "Reply 是复杂场景最终业务大脑",
        "17.4 每阶段两轮 Review",
        "19.1 T0：合同与隔离测试",
        "19.5 T4：Join 确定性测试",
        "19.7 T6：并行组合测试",
        "19.8 T7：离线全链路仿真",
        "19.9 T8：Shadow 对比",
        "不自动部署或向真实客户发送消息",
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
        "business_wording_freeze_review",
        "model_semantics_ownership_review",
        "simulation_regression_review",
        "rollback_evidence_review",
        "workflow_tests/test_reply_chain_shadow_payload_isolation.py",
        "Offline simulation report covers SOP, precision QA, store, payment, paid registration, risk, and model-failure cases.",
        "must not be deployed from this branch",
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
