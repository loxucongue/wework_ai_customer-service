from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(name: str, command: list[str], cwd: Path = ROOT) -> None:
    print(f"\n=== {name} ===", flush=True)
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repository quality gates; stop at the first failure.")
    parser.add_argument("--skip-frontend", action="store_true", help="Run backend-only gates (for environments without Node/pnpm).")
    args = parser.parse_args()
    run("Python compile", [sys.executable, "-m", "compileall", "-q", "ai_paths/app", "ai_paths/scripts", "scripts", "workflow_tests"])
    run("Static debt audit", [sys.executable, "scripts/quality_audit.py"])
    run("Deterministic backend tests", [sys.executable, "-m", "pytest", "-q", "workflow_tests", "-m", "not live and not model"])
    run("V3 route contract", [sys.executable, "-m", "pytest", "-q", "workflow_tests/test_v3_only_route_contract.py"])
    run("Isolated startup", [sys.executable, "-m", "pytest", "-q", "workflow_tests/test_isolated_startup_gate.py"])
    if not args.skip_frontend:
        pnpm = shutil.which("pnpm")
        if not pnpm:
            print("QUALITY GATE FAILURE: pnpm is required for frontend gates.")
            return 1
        run("Frontend dependency install", [pnpm, "install", "--frozen-lockfile"], ROOT / "projects")
        run("Frontend TypeScript and ESLint", [pnpm, "validate"], ROOT / "projects")
        run("Frontend production build", [pnpm, "build"], ROOT / "projects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

