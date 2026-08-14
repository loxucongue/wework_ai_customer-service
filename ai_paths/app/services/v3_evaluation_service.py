from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class V3EvaluationService:
    """Read-only access to offline V3 evaluation artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def list_runs(self) -> dict[str, Any]:
        if not self.root.exists():
            return {"items": []}
        items = []
        for manifest_path in self.root.glob("*/manifest.json"):
            manifest = self._read_json(manifest_path)
            if manifest:
                items.append(manifest)
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {"items": items}

    def get_run(self, run_id: str) -> dict[str, Any]:
        safe_id = str(run_id or "").strip()
        if not safe_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in safe_id):
            raise ValueError("invalid run_id")
        run_dir = (self.root / safe_id).resolve()
        if run_dir.parent != self.root:
            raise ValueError("invalid run_id")
        manifest = self._read_json(run_dir / "manifest.json")
        if not manifest:
            raise FileNotFoundError(safe_id)
        return {
            "manifest": manifest,
            "evaluation": self._read_json(run_dir / "evaluation.json"),
            "results": self._read_json(run_dir / "results.json"),
            "report": self._read_text(run_dir / "report.md"),
        }

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _read_text(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.is_file() else ""

