from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "quality" / "baseline.json"


def _run_audit(baseline: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/quality_audit.py", "--baseline", str(baseline)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_quality_debt_matches_versioned_baseline() -> None:
    completed = _run_audit(BASELINE)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout)
    assert report["counts"]["private_test_imports"] == 157
    assert report["counts"] == report["baseline"]


def test_quality_debt_growth_returns_nonzero(tmp_path: Path) -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    baseline["private_test_imports"] -= 1
    lower_baseline = tmp_path / "lower-baseline.json"
    lower_baseline.write_text(json.dumps(baseline), encoding="utf-8")
    completed = _run_audit(lower_baseline)
    assert completed.returncode != 0
    assert "private_test_imports: 157 > baseline 156" in completed.stdout


def test_generated_strategy_candidates_are_not_human_gold() -> None:
    path = ROOT / "workflow_tests" / "fixtures" / "sales_strategy_gold_candidates_20260831.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["coverage"]["cases"] == 400
    assert payload["gold_status"] == "pending_human_review"
    assert "Human review is required" in payload["notice"]

