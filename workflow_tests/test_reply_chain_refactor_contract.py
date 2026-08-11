from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "docs" / "parallel_gate_planner_refactor_plan.md"
MODEL_LED_PLAN_PATH = ROOT / "docs" / "model_led_top_sales_brain_refactor_plan.md"
MATRIX_PATH = ROOT / "docs" / "rule_ownership_matrix.md"
GRAPH_BUILDER_PATH = ROOT / "ai_paths" / "app" / "graph" / "graph_builder.py"
PARALLEL_CHAIN_PATH = ROOT / "ai_paths" / "app" / "graph" / "nodes" / "parallel_reply_chain.py"


def test_refactor_plan_keeps_node_ownership_boundaries() -> None:
    text = PLAN_PATH.read_text(encoding="utf-8")

    for marker in [
        "Gate 只匹配可用话术和给出路由建议，不成为新的业务大脑",
        "Tool Planner 只决定需要哪些只读事实",
        "Reply 统一理解完整聊天",
        "Join 只合并证据",
        "不能在旧 Planner 完成后再把同一结果包装成两个分支",
        "不新增 Python 关键词分支",
    ]:
        assert marker in text


def test_refactor_plan_documents_real_parallel_and_review_stages() -> None:
    text = PLAN_PATH.read_text(encoding="utf-8")

    for marker in [
        "asyncio.gather()",
        "每个阶段两轮 review",
        "T1：Shared Context",
        "T6：真实并行",
        "T7：离线全链路仿真",
        "目标运行时已经落入真实 Graph",
        "真实模型全量仿真及人工回复审核仍是当前行为切换门禁",
        "本分支不部署",
    ]:
        assert marker in text


def test_refactor_documents_are_clean_utf8() -> None:
    for path in (PLAN_PATH, MODEL_LED_PLAN_PATH, MATRIX_PATH):
        text = path.read_text(encoding="utf-8")
        for marker in ("????", "锟", "�", "娑撳秴", "銆"):
            assert marker not in text, f"{path.name} contains encoding damage: {marker}"


def test_active_reply_graph_cannot_reach_legacy_planner_or_normalizer() -> None:
    graph = GRAPH_BUILDER_PATH.read_text(encoding="utf-8")

    expected_nodes = (
        '"shared_context"',
        '"parallel_evidence"',
        '"execute_readonly_actions"',
        '"evidence_join"',
        '"synthesize_reply"',
    )
    positions = [graph.index(node) for node in expected_nodes]
    assert positions == sorted(positions)
    assert "planner_nodes" not in graph
    assert "brain_v2_normalizer" not in graph
    assert "create_planner" not in graph


def test_parallel_chain_does_not_publish_legacy_sales_decisions() -> None:
    source = PARALLEL_CHAIN_PATH.read_text(encoding="utf-8")

    assert "asyncio.create_task(_run_content_gate" in source
    assert "asyncio.create_task(_run_tool_planner" in source
    assert "await asyncio.gather(" in source
    assert '"planner_decision":' not in source
    for excluded in (
        "signup_state",
        "next_slot",
        "deposit_ready_candidate",
        "customer_type",
        "main_blocker",
        "conversion_stage",
        "automatic_store_confirmation",
    ):
        assert f'"{excluded}"' in source


def test_model_led_plan_requires_post_completion_architecture_review() -> None:
    text = MODEL_LED_PLAN_PATH.read_text(encoding="utf-8")

    for marker in (
        "完成后的强制架构复审门禁",
        "semantic_intrusion",
        "over_protection",
        "legacy_unreachable",
        "代码在纠正结构时静默改变模型的业务决定",
        "任一 `semantic_intrusion` 或未解释的 `over_protection` 未清零",
    ):
        assert marker in text


def test_rule_matrix_active_rules_have_target_owner_and_tests() -> None:
    rows = _matrix_rows()
    assert rows

    for row in rows:
        if row["migration status"] == "active":
            assert row["target owner"], row["rule_id"]
            assert row["regression tests"], row["rule_id"]


def test_rule_matrix_keeps_high_risk_rules_and_superseded_order_precondition() -> None:
    rows = {row["rule_id"]: row for row in _matrix_rows()}
    required = {
        "gate_not_business_brain",
        "offer_activity_facts",
        "project_scope_boundary",
        "effect_case_image_evidence",
        "store_visible_scope_only",
        "store_candidate_count_rule",
        "payment_no_order_precondition",
        "payment_deposit_evidence_gate",
        "payment_after_paid_registration",
        "unknown_message_transfer_paid",
        "health_risk_priority",
        "explicit_reject_no_payment",
        "human_wechat_style",
        "sop_mainline_progression",
        "precision_answer_then_mainline",
        "appointment_blocker_reply_ownership",
    }

    assert not required.difference(rows)
    assert rows["order_required_before_payment_card"]["migration status"] == "superseded"
    assert rows["order_required_before_payment_card"]["target owner"] == "None"
    for rule_id in ("conversion_stage", "customer_type", "fixed_mainline_next_step"):
        assert rows[rule_id]["type"] == "deprecated"
        assert rows[rule_id]["migration status"] == "superseded"
        assert rows[rule_id]["target owner"] == "None"


def test_rule_matrix_uses_model_led_taxonomy_only() -> None:
    allowed = {"hard_law", "business_fact", "sales_principle", "content_asset", "deprecated"}
    for row in _matrix_rows():
        assert row["type"] in allowed, row["rule_id"]


def _matrix_rows() -> list[dict[str, str]]:
    lines = MATRIX_PATH.read_text(encoding="utf-8").splitlines()
    table_lines = [line for line in lines if line.startswith("| ") and " | " in line]
    header = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in table_lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells)))
    return rows
