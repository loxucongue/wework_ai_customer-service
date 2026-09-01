from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_runtime_entrypoint_and_outreach_facade_stay_small() -> None:
    main_lines = _source("ai_paths/app/main.py").splitlines()
    outreach_lines = _source("ai_paths/app/services/outreach_service.py").splitlines()

    assert len(main_lines) <= 200
    assert len(outreach_lines) <= 250


def test_outreach_has_one_explicit_implementation_per_stage() -> None:
    facade = _source("ai_paths/app/services/outreach_service.py")
    package = _source("ai_paths/app/services/outreach/planning.py")
    package += _source("ai_paths/app/services/outreach/first_day.py")
    package += _source("ai_paths/app/services/outreach/execution.py")
    package += _source("ai_paths/app/services/outreach/message.py")

    assert "_bind_legacy_dependencies" not in package
    assert "def __getattr__" not in facade + package
    assert "from app.services.outreach.plan_generation" not in facade + package
    assert "from app.services.outreach.task_execution" not in facade + package
    assert facade.count("PlanGenerator(") == 1
    assert facade.count("FirstDayWorkflow(") == 1
    assert facade.count("TaskExecutor(") == 1
    assert facade.count("MessageGenerator(") == 1


def test_v3_graph_has_explicit_fact_decision_material_reply_and_commit_modules() -> None:
    nodes = ROOT / "ai_paths/app/graph/nodes"
    assert not (nodes / "parallel_reply_chain.py").exists()
    for filename in (
        "authoritative_context.py",
        "sales_decision.py",
        "material_selection.py",
        "reply_generation.py",
        "fact_actions.py",
        "transaction_actions.py",
        "transaction_commit.py",
    ):
        assert (nodes / filename).is_file()

    builder = _source("ai_paths/app/graph/graph_builder.py")
    full_order = (
        '"authoritative_context"',
        '"sales_decision"',
        '"readonly_facts"',
        '"sales_decision_after_tools"',
        '"material_selection"',
        '"reply_generation"',
    )
    assert all(builder.index(left) < builder.index(right) for left, right in zip(full_order, full_order[1:]))


def test_v3_reply_has_one_repair_and_no_deterministic_business_fallback() -> None:
    generation = _source("ai_paths/app/graph/nodes/reply_generation.py")
    reply_nodes = _source("ai_paths/app/graph/nodes/reply_nodes.py")
    actions = _source("ai_paths/app/graph/nodes/action_nodes.py")

    assert "deterministic_neutral_final_fallback" not in generation + reply_nodes
    assert "deterministic_store_fact_recovery" not in generation + reply_nodes
    assert "final_targeted_repair_model" not in generation
    assert "def create_execute_actions_node" not in actions
