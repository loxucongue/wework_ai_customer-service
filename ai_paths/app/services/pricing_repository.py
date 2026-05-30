from __future__ import annotations

from pathlib import Path
from typing import Any

import openpyxl

from app.config import Settings


class LocalPricingRepository:
    def __init__(self, settings: Settings):
        self.path: Path = settings.pricing_xlsx_path
        self._rows: list[dict[str, Any]] | None = None

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._load_rows()
        terms = self._query_terms(query)
        if not terms:
            return rows[:limit]

        scored: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            name = str(row.get("project_name") or "")
            note = str(row.get("price_note") or "")
            haystack = f"{name} {note}"
            score = 0
            for term in terms:
                if term and term in haystack:
                    score += 10 if term in name else 4
            if score:
                scored.append((score, row))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in scored[:limit]]

    def _load_rows(self) -> list[dict[str, Any]]:
        if self._rows is not None:
            return self._rows
        if not self.path.exists():
            self._rows = []
            return self._rows

        workbook = openpyxl.load_workbook(self.path, read_only=True, data_only=True)
        sheet = workbook.active
        raw_rows = list(sheet.iter_rows(values_only=True))
        if not raw_rows:
            self._rows = []
            return self._rows
        headers = [str(cell or "") for cell in raw_rows[0]]
        rows: list[dict[str, Any]] = []
        for raw in raw_rows[1:]:
            row = {headers[index]: raw[index] if index < len(raw) else "" for index in range(len(headers))}
            if str(row.get("status", "")).lower() not in {"true", "1", "yes"}:
                continue
            rows.append({key: "" if value is None else str(value) for key, value in row.items()})
        self._rows = rows
        return rows

    def _query_terms(self, query: str) -> list[str]:
        query = query or ""
        terms: list[str] = []
        aliases = {
            "皮秒": ["皮秒", "超皮秒", "祛斑", "淡斑"],
            "超皮秒": ["超皮秒", "皮秒", "祛斑", "淡斑"],
            "祛斑": ["祛斑", "淡斑"],
            "淡斑": ["淡斑", "祛斑"],
            "水光": ["水光", "补水"],
            "热玛吉": ["热玛吉", "紧致", "抗老"],
        }
        for key, values in aliases.items():
            if key in query:
                terms.extend(values)
        if not terms:
            for key in aliases:
                if key in query:
                    terms.append(key)
        return self._dedupe(terms)

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return result
