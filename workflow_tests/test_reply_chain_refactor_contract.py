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
