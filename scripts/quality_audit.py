from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "quality" / "baseline.json"


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    symbol: str = ""

    def as_dict(self) -> dict[str, str | int]:
        value: dict[str, str | int] = {"path": self.path, "line": self.line}
        if self.symbol:
            value["symbol"] = self.symbol
        return value


def _python_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            yield path
        elif path.is_dir():
            yield from sorted(path.rglob("*.py"))


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def private_test_imports() -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted((ROOT / "workflow_tests").glob("test_*.py")):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name.startswith("_") and not alias.name.startswith("__"):
                        findings.append(Finding(_relative(path), node.lineno, alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.rsplit(".", 1)[-1]
                    if name.startswith("_") and not name.startswith("__"):
                        findings.append(Finding(_relative(path), node.lineno, alias.name))
    return findings


def _is_exception_type(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Name) and node.id == "Exception"


def _is_silent(handler: ast.ExceptHandler) -> bool:
    return len(handler.body) == 1 and (
        isinstance(handler.body[0], ast.Pass)
        or (
            isinstance(handler.body[0], ast.Return)
            and (
                handler.body[0].value is None
                or (isinstance(handler.body[0].value, ast.Constant) and handler.body[0].value.value is None)
            )
        )
    )


def _is_fail_open(handler: ast.ExceptHandler) -> bool:
    if len(handler.body) != 1 or not isinstance(handler.body[0], ast.Return):
        return False
    value = handler.body[0].value
    if isinstance(value, ast.Constant):
        return value.value is True or (isinstance(value.value, str) and value.value.lower() in {"ok", "success", "allow"})
    if isinstance(value, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
        return not value.elts if hasattr(value, "elts") else not value.keys
    return False


def exception_findings() -> dict[str, list[Finding]]:
    result = {"broad_exception_handlers": [], "bare_exception_handlers": [], "silent_exception_handlers": [], "pass_only_exception_handlers": [], "fail_open_exception_handlers": []}
    for path in _python_files((ROOT / "ai_paths", ROOT / "workflow_tests")):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.ExceptHandler):
                continue
            finding = Finding(_relative(path), node.lineno)
            if node.type is None:
                result["bare_exception_handlers"].append(finding)
            if _is_exception_type(node.type):
                result["broad_exception_handlers"].append(finding)
            if _is_silent(node):
                result["silent_exception_handlers"].append(finding)
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                result["pass_only_exception_handlers"].append(finding)
            if _is_fail_open(node):
                result["fail_open_exception_handlers"].append(finding)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit test encapsulation and exception-handling debt.")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    groups = {"private_test_imports": private_test_imports(), **exception_findings()}
    counts = {name: len(items) for name, items in groups.items()}
    failures = [f"{name}: {count} > baseline {baseline[name]}" for name, count in counts.items() if count > baseline[name]]
    report: dict[str, object] = {"counts": counts, "baseline": baseline, "status": "failed" if failures else "passed"}
    if args.details:
        report["findings"] = {name: [item.as_dict() for item in items] for name, items in groups.items()}
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if failures:
        for failure in failures:
            print(f"QUALITY AUDIT FAILURE: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
