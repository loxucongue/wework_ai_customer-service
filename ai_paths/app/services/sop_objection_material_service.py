from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class SopObjectionMaterialService:
    """Business-managed text slices used by third-party SOP AI-copy tasks."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "updated_at": "", "materials": []}
        with self.path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return self._normalize(payload)

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize(payload)
        normalized["updated_at"] = datetime.now(UTC).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(normalized, file, ensure_ascii=False, indent=2)
            file.write("\n")
        temporary.replace(self.path)
        return normalized

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        materials = payload.get("materials")
        if not isinstance(materials, list):
            raise ValueError("materials must be a list")
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(materials):
            if not isinstance(item, dict):
                raise ValueError(f"material #{index + 1} must be an object")
            material_id = str(item.get("material_id") or "").strip()
            if not material_id or material_id in seen:
                raise ValueError(f"material #{index + 1} requires a unique material_id")
            seen.add(material_id)
            output.append(
                {
                    "material_id": material_id,
                    "name": str(item.get("name") or item.get("objective") or material_id).strip(),
                    "category": str(item.get("category") or "").strip(),
                    "tags": _strings(item.get("tags")),
                    "applicable_scenes": _strings(item.get("applicable_scenes")),
                    "response_approach": str(
                        item.get("response_approach")
                        or item.get("business_note")
                        or item.get("objective")
                        or ""
                    ).strip(),
                    "example_contents": _strings(
                        item.get("example_contents")
                        if isinstance(item.get("example_contents"), list)
                        else item.get("reference_texts")
                    ),
                }
            )
        return {
            "version": int(payload.get("version") or 1),
            "updated_at": str(payload.get("updated_at") or ""),
            "materials": output,
        }


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]
