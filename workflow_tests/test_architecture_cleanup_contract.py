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
