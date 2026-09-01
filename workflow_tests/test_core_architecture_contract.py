from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function_lines(path: str, class_name: str, function_name: str) -> int:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    owner = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    function = next(
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    )
    return function.end_lineno - function.lineno + 1


def test_runtime_entry_and_public_facades_stay_small() -> None:
    assert len((ROOT / "ai_paths/app/main.py").read_text(encoding="utf-8").splitlines()) <= 200
    assert len(
        (ROOT / "ai_paths/app/services/outreach_service.py")
        .read_text(encoding="utf-8")
        .splitlines()
    ) <= 250
    assert _function_lines(
        "ai_paths/app/services/outreach/planning.py", "PlanGenerator", "_build_plan"
    ) <= 60
    assert _function_lines(
        "ai_paths/app/services/outreach/execution.py", "TaskExecutor", "execute"
    ) <= 180
    assert _function_lines(
        "ai_paths/app/services/sop_platform_task_service.py",
        "SopPlatformTaskService",
        "_process_locked",
    ) <= 60


def test_removed_dynamic_composition_does_not_return() -> None:
    service_source = (ROOT / "ai_paths/app/services/outreach_service.py").read_text(encoding="utf-8")
    app_source = (ROOT / "ai_paths/app/main.py").read_text(encoding="utf-8")
    assert "_bind_legacy_dependencies" not in service_source
    assert "def __getattr__" not in service_source
    assert "apply_runtime_route_policy" not in app_source


def test_reply_graph_has_one_customer_visible_decision_stage() -> None:
    graph_source = (ROOT / "ai_paths/app/graph/graph_builder.py").read_text(encoding="utf-8")
    assert '"semantic_evidence"' in graph_source
    assert '"semantic_evidence_after_facts"' in graph_source
    assert '"reply_decision"' in graph_source
    assert '"sales_decision"' not in graph_source
    assert '"reply_generation"' not in graph_source
