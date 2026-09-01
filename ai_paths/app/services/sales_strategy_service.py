from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from app.config import Settings


SCHEMA_VERSION = "sales_strategy_catalog_v1"
ALLOWED_RUNTIME_MODES = {"active", "shadow", "off"}
ALLOWED_TIME_BASES = {
    "customer_reply",
    "contact_added",
    "previous_step",
    "same_day_18_00",
    "same_day_20_00",
    "appointment_previous_day_20_00",
}
HARD_BLOCK_RISKS = {"prohibited_absolute_or_medical_claim"}


class SalesStrategyProvider(Protocol):
    def load_raw(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return one provider payload and non-secret source metadata."""


class LocalJsonSalesStrategyProvider:
    def __init__(self, settings: Settings) -> None:
        configured = Path(settings.sales_strategy_catalog_path)
        ai_paths_root = Path(__file__).resolve().parents[2]
        self.default_path = Path(__file__).resolve().parents[1] / "policies" / "sales_strategy_catalog_v1.json"
        self.path = configured if configured.is_absolute() else ai_paths_root / configured

    def load_raw(self) -> tuple[dict[str, Any], dict[str, Any]]:
        path = self.path if self.path.exists() else self.default_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("sales strategy catalog must be an object")
        return payload, {
            "provider": "local_json",
            "source": "configured" if path == self.path else "bundled_default",
            "path": str(path),
        }


class SalesStrategyService:
    """Transport, validate and search strategy data without deciding business intent."""

    def __init__(self, settings: Settings, provider: SalesStrategyProvider | None = None) -> None:
        self.settings = settings
        self.provider = provider or LocalJsonSalesStrategyProvider(settings)
        self._last_good: dict[str, Any] | None = None
        self._last_error = ""
        self._index_checksum = ""
        self._content_terms: dict[str, Counter[str]] = {}
        self._document_frequency: Counter[str] = Counter()

    def load(self) -> dict[str, Any]:
        try:
            raw, storage = self.provider.load_raw()
            catalog = _normalize_catalog(raw)
            audit = _audit_catalog(catalog)
            if audit["error_count"]:
                messages = [item["message"] for item in audit["issues"] if item["severity"] == "error"]
                raise ValueError("; ".join(messages[:10]))
            result = deepcopy(catalog)
            result["checksum"] = str(raw.get("checksum") or _checksum(catalog))
            result["audit"] = audit
            result["storage"] = storage
            result["runtime_health"] = {
                "status": "ok",
                "using_last_known_good": False,
                "last_error": "",
            }
            self._last_good = deepcopy(result)
            self._last_error = ""
            self._ensure_index(result)
            return result
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            if self._last_good is not None:
                result = deepcopy(self._last_good)
                result["runtime_health"] = {
                    "status": "degraded",
                    "using_last_known_good": True,
                    "last_error": self._last_error,
                }
                return result
            raise ValueError(f"sales strategy catalog unavailable: {self._last_error}") from exc

    def runtime_summary(self) -> dict[str, Any]:
        catalog = self.load()
        enabled = bool(self.settings.sales_strategy_catalog_enabled)
        runtime_mode = str(catalog.get("runtime_mode") or "off") if enabled else "off"
        return {
            "schema_version": catalog.get("schema_version"),
            "catalog_version": catalog.get("catalog_version"),
            "checksum": catalog.get("checksum"),
            "runtime_mode": runtime_mode,
            "counts": _counts(catalog),
            "categories": [
                {"category_key": item.get("category_key"), "name": item.get("name")}
                for item in catalog.get("categories") or []
                if item.get("enabled", True)
            ],
            "tactic_tags": sorted(
                {
                    str(item.get("tactic_tag") or "").strip()
                    for item in catalog.get("contents") or []
                    if str(item.get("tactic_tag") or "").strip()
                }
            ),
            "runtime_health": deepcopy(catalog.get("runtime_health") or {}),
        }

    def admin_view(self) -> dict[str, Any]:
        catalog = self.load()
        risky = [
            {
                "content_id": item.get("content_id"),
                "category_key": item.get("category_key"),
                "scenario_name": item.get("scenario_name"),
                "required_facts": item.get("required_facts") or [],
                "risk_flags": item.get("risk_flags") or [],
                "source": item.get("source") or {},
            }
            for item in catalog.get("contents") or []
            if item.get("required_facts") or item.get("risk_flags")
        ]
        return {
            **catalog,
            "counts": _counts(catalog),
            "high_risk_contents": risky,
        }

    def retrieve(
        self,
        *,
        category_key: str,
        scenario_query: str,
        tactic_tags: list[str] | None = None,
        fact_context: dict[str, Any] | None = None,
        recent_asset_ids: set[str] | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        catalog = self.load()
        self._ensure_index(catalog)
        if str(catalog.get("runtime_mode") or "off") == "off" or not bool(self.settings.sales_strategy_catalog_enabled):
            return {"candidates": [], "filtered": [], "catalog": self.runtime_summary()}
        category = str(category_key or "").strip()
        tags = {str(item).strip() for item in tactic_tags or [] if str(item).strip()}
        available_facts = _available_fact_keys(fact_context or {})
        recent_assets = recent_asset_ids or set()
        query_terms = Counter(_ngrams(f"{scenario_query} {' '.join(sorted(tags))}"))
        total_documents = max(1, len(self._content_terms))
        scored: list[tuple[float, dict[str, Any]]] = []
        filtered: list[dict[str, Any]] = []
        for item in catalog.get("contents") or []:
            if item.get("status") != "active" or item.get("category_key") != category:
                continue
            reason = _filter_reason(item, available_facts=available_facts, recent_assets=recent_assets)
            if reason:
                filtered.append({"content_id": item.get("content_id"), "reason": reason})
                continue
            terms = self._content_terms.get(str(item.get("content_id") or ""), Counter())
            score = _bm25_score(query_terms, terms, self._document_frequency, total_documents)
            if tags and str(item.get("tactic_tag") or "") in tags:
                score += 2.0
            scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("content_id") or "")))
        candidates = [_runtime_content(item, score=score) for score, item in scored[: max(1, min(limit, 5))]]
        strategy_candidates = self.retrieve_strategies(
            category_key=category,
            scenario_query=scenario_query,
            tactic_tags=list(tags),
            limit=3,
        )
        return {
            "candidates": candidates,
            "strategy_candidates": strategy_candidates,
            "filtered": filtered,
            "catalog": self.runtime_summary(),
        }

    def retrieve_strategies(
        self,
        *,
        category_key: str,
        scenario_query: str,
        tactic_tags: list[str] | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        catalog = self.load()
        tags = {str(item).strip() for item in tactic_tags or [] if str(item).strip()}
        query_terms = set(_ngrams(scenario_query))
        results: list[tuple[float, dict[str, Any]]] = []
        for strategy in catalog.get("strategies") or []:
            if not strategy.get("enabled") or strategy.get("category_key") != category_key:
                continue
            strategy_tags = {
                str(tag).strip()
                for step in strategy.get("steps") or []
                for tag in step.get("tactic_tags") or []
                if str(tag).strip()
            }
            name_terms = set(_ngrams(str(strategy.get("name") or "")))
            overlap = len(query_terms & name_terms) / max(1, len(query_terms | name_terms))
            tag_bonus = len(tags & strategy_tags) * 0.2
            results.append((overlap + tag_bonus, strategy))
        results.sort(key=lambda pair: (-pair[0], str(pair[1].get("strategy_key") or "")))
        return [
            {
                "strategy_key": item.get("strategy_key"),
                "category_key": item.get("category_key"),
                "scenario_keys": item.get("scenario_keys") or [],
                "name": item.get("name"),
                "version": item.get("version"),
                "score": round(score, 6),
                "steps": deepcopy(item.get("steps") or []),
            }
            for score, item in results[: max(1, min(limit, 5))]
        ]

    def retrieve_content_pool(
        self,
        *,
        query: str,
        fact_context: dict[str, Any] | None = None,
        recent_asset_ids: set[str] | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Recall cross-category references without deciding the customer's intent.

        The active V3 semantic router owns the meaning of the current turn. This
        method only ranks the configured catalog against that model-produced
        summary and applies factual/media safety filters.
        """

        catalog = self.load()
        self._ensure_index(catalog)
        if (
            str(catalog.get("runtime_mode") or "off") == "off"
            or not bool(self.settings.sales_strategy_catalog_enabled)
        ):
            return {"candidates": [], "filtered": [], "catalog": self.runtime_summary()}
        query_terms = Counter(_ngrams(query))
        if not query_terms:
            return {"candidates": [], "filtered": [], "catalog": self.runtime_summary()}
        available_facts = _available_fact_keys(fact_context or {})
        recent_assets = recent_asset_ids or set()
        total_documents = max(1, len(self._content_terms))
        scored: list[tuple[float, dict[str, Any]]] = []
        filtered: list[dict[str, Any]] = []
        for item in catalog.get("contents") or []:
            if item.get("status") != "active":
                continue
            reason = _filter_reason(item, available_facts=available_facts, recent_assets=recent_assets)
            if reason:
                filtered.append({"content_id": item.get("content_id"), "reason": reason})
                continue
            terms = self._content_terms.get(str(item.get("content_id") or ""), Counter())
            score = _bm25_score(query_terms, terms, self._document_frequency, total_documents)
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("content_id") or "")))
        candidates = [
            _runtime_content(item, score=score)
            for score, item in scored[: max(1, min(limit, 5))]
        ]
        return {
            "candidates": candidates,
            "filtered": filtered,
            "catalog": self.runtime_summary(),
        }

    def retrieve_strategy_pool(self, *, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Return cross-category candidates; the model still chooses the actual strategy."""
        catalog = self.load()
        query_terms = set(_ngrams(query))
        results: list[tuple[float, dict[str, Any]]] = []
        for strategy in catalog.get("strategies") or []:
            if not strategy.get("enabled"):
                continue
            searchable = " ".join(
                [
                    str(strategy.get("name") or ""),
                    *[
                        " ".join(
                            [
                                str(step.get("node_goal") or ""),
                                " ".join(str(tag) for tag in step.get("tactic_tags") or []),
                            ]
                        )
                        for step in strategy.get("steps") or []
                        if isinstance(step, dict)
                    ],
                ]
            )
            terms = set(_ngrams(searchable))
            score = len(query_terms & terms) / max(1, len(query_terms | terms))
            results.append((score, strategy))
        results.sort(key=lambda pair: (-pair[0], str(pair[1].get("strategy_key") or "")))
        return [
            {
                "strategy_key": item.get("strategy_key"),
                "category_key": item.get("category_key"),
                "scenario_keys": item.get("scenario_keys") or [],
                "name": item.get("name"),
                "version": item.get("version"),
                "score": round(score, 6),
                "steps": deepcopy(item.get("steps") or []),
            }
            for score, item in results[: max(1, min(limit, 12))]
        ]

    def _ensure_index(self, catalog: dict[str, Any]) -> None:
        checksum = str(catalog.get("checksum") or _checksum(catalog))
        if checksum == self._index_checksum:
            return
        terms: dict[str, Counter[str]] = {}
        document_frequency: Counter[str] = Counter()
        for item in catalog.get("contents") or []:
            content_id = str(item.get("content_id") or "")
            text = " ".join(
                str(item.get(key) or "")
                for key in ("scenario_name", "tactic_tag", "solution_idea", "reference_text")
            )
            item_terms = Counter(_ngrams(text))
            terms[content_id] = item_terms
            document_frequency.update(item_terms.keys())
        self._content_terms = terms
        self._document_frequency = document_frequency
        self._index_checksum = checksum


def _normalize_catalog(value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)
    for key in ("schema_version", "catalog_version", "status", "runtime_mode", "generated_at"):
        result[key] = str(value.get(key) or "").strip()
    for key in ("categories", "scenarios", "strategies", "contents"):
        raw = value.get(key)
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise ValueError(f"{key} must be a list of objects")
        result[key] = deepcopy(raw)
    return result


def _audit_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if catalog.get("schema_version") != SCHEMA_VERSION:
        issues.append(_issue("error", "schema_version", f"schema_version must be {SCHEMA_VERSION}"))
    if not catalog.get("catalog_version"):
        issues.append(_issue("error", "catalog_version", "catalog_version is required"))
    if catalog.get("runtime_mode") not in ALLOWED_RUNTIME_MODES:
        issues.append(_issue("error", "runtime_mode", "runtime_mode must be active, shadow or off"))
    _unique_required(issues, catalog.get("categories"), "category_key", "categories")
    _unique_required(issues, catalog.get("scenarios"), "scenario_key", "scenarios")
    _unique_required(issues, catalog.get("strategies"), "strategy_key", "strategies")
    _unique_required(issues, catalog.get("contents"), "content_id", "contents")
    category_keys = {str(item.get("category_key") or "") for item in catalog.get("categories") or []}
    scenario_keys = {str(item.get("scenario_key") or "") for item in catalog.get("scenarios") or []}
    for item in catalog.get("strategies") or []:
        key = str(item.get("strategy_key") or "")
        if item.get("category_key") not in category_keys:
            issues.append(_issue("error", "unknown_category", f"strategy {key} has unknown category"))
        for scenario_key in item.get("scenario_keys") or []:
            if scenario_key not in scenario_keys:
                issues.append(_issue("error", "unknown_scenario", f"strategy {key} has unknown scenario"))
        if len(item.get("steps") or []) > 5:
            issues.append(_issue("error", "too_many_steps", f"strategy {key} has more than five steps"))
        for step in item.get("steps") or []:
            if step.get("trigger_base") not in ALLOWED_TIME_BASES:
                issues.append(_issue("error", "invalid_trigger_base", f"strategy {key} has invalid trigger base"))
    for item in catalog.get("contents") or []:
        content_id = str(item.get("content_id") or "")
        if item.get("category_key") not in category_keys:
            issues.append(_issue("error", "unknown_category", f"content {content_id} has unknown category"))
        if not item.get("content_types"):
            issues.append(_issue("error", "content_type_required", f"content {content_id} needs content_types"))
        for field in ("image_urls", "video_urls"):
            values = item.get(field) if isinstance(item.get(field), list) else []
            for url in values:
                if str(url or "") and not _valid_oss_url(str(url)):
                    issues.append(_issue("error", "invalid_oss_url", f"content {content_id} has invalid {field}"))
    errors = sum(1 for item in issues if item["severity"] == "error")
    warnings = sum(1 for item in issues if item["severity"] == "warning")
    return {
        "status": "error" if errors else ("warning" if warnings else "ok"),
        "error_count": errors,
        "warning_count": warnings,
        "issues": issues,
    }


def _available_fact_keys(value: Any) -> set[str]:
    available: set[str] = set()
    if not isinstance(value, dict):
        return available
    for key, nested in value.items():
        if nested not in (None, "", [], {}, False):
            available.add(str(key))
        if isinstance(nested, dict):
            available.update(_available_fact_keys(nested))
    return available


def _filter_reason(item: dict[str, Any], *, available_facts: set[str], recent_assets: set[str]) -> str:
    risks = {str(value) for value in item.get("risk_flags") or []}
    if risks & HARD_BLOCK_RISKS:
        return "hard_risk"
    missing = [str(value) for value in item.get("required_facts") or [] if str(value) not in available_facts]
    if missing:
        return "missing_facts:" + ",".join(sorted(missing))
    asset_ids = {str(value) for value in item.get("asset_ids") or [] if str(value or "")}
    if not asset_ids and str(item.get("asset_id") or ""):
        asset_ids.add(str(item.get("asset_id")))
    if asset_ids & recent_assets:
        return "recent_asset_duplicate"
    for field in ("image_urls", "video_urls"):
        for url in item.get(field) or []:
            if str(url or "") and not _valid_oss_url(str(url)):
                return f"invalid_{field}"
    return ""


def _valid_oss_url(value: str) -> bool:
    parsed = urlparse(value)
    trusted_storage = "oss" in parsed.netloc.lower() or "aliyuncs.com" in parsed.netloc.lower()
    trusted_media_proxy = parsed.path.startswith("/ai-paths/cardpoint-media/")
    return parsed.scheme == "https" and bool(parsed.netloc) and (trusted_storage or trusted_media_proxy)


def _ngrams(value: str) -> list[str]:
    text = re.sub(r"\s+", "", str(value or "").lower())
    if not text:
        return []
    chars = [char for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff"]
    joined = "".join(chars)
    grams = [joined[index : index + 2] for index in range(max(0, len(joined) - 1))]
    return grams or [joined]


def _bm25_score(query: Counter[str], document: Counter[str], df: Counter[str], total_documents: int) -> float:
    if not query or not document:
        return 0.0
    length = sum(document.values())
    average_length = 40.0
    score = 0.0
    for term, query_count in query.items():
        frequency = document.get(term, 0)
        if not frequency:
            continue
        inverse = math.log(1 + (total_documents - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
        denominator = frequency + 1.2 * (0.25 + 0.75 * length / average_length)
        score += inverse * (frequency * 2.2 / denominator) * min(query_count, 2)
    return score


def _runtime_content(item: dict[str, Any], *, score: float) -> dict[str, Any]:
    return {
        "content_id": item.get("content_id"),
        "category_key": item.get("category_key"),
        "scenario_keys": item.get("scenario_keys") or [],
        "scenario_name": item.get("scenario_name"),
        "tactic_tag": item.get("tactic_tag"),
        "solution_idea": item.get("solution_idea"),
        "reference_text": item.get("reference_text"),
        "image_url": item.get("image_url"),
        "video_url": item.get("video_url"),
        "image_urls": item.get("image_urls") or ([item.get("image_url")] if item.get("image_url") else []),
        "video_urls": item.get("video_urls") or ([item.get("video_url")] if item.get("video_url") else []),
        "content_types": item.get("content_types") or [],
        "asset_id": item.get("asset_id"),
        "asset_ids": item.get("asset_ids") or ([item.get("asset_id")] if item.get("asset_id") else []),
        "required_facts": item.get("required_facts") or [],
        "risk_flags": item.get("risk_flags") or [],
        "score": round(score, 6),
        "usage": "reference_only_rephrase_do_not_copy",
    }


def _counts(catalog: dict[str, Any]) -> dict[str, int]:
    return {
        "categories": len(catalog.get("categories") or []),
        "scenarios": len(catalog.get("scenarios") or []),
        "strategies": len(catalog.get("strategies") or []),
        "contents": len(catalog.get("contents") or []),
        "images": sum(1 for item in catalog.get("contents") or [] if item.get("image_url")),
        "videos": sum(1 for item in catalog.get("contents") or [] if item.get("video_url")),
    }


def _checksum(value: dict[str, Any]) -> str:
    payload = {
        key: item
        for key, item in value.items()
        if key not in {"checksum", "generated_at", "audit", "storage", "runtime_health"}
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _issue(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _unique_required(issues: list[dict[str, str]], values: Any, key: str, path: str) -> None:
    seen: set[str] = set()
    for index, item in enumerate(values if isinstance(values, list) else []):
        value = str(item.get(key) or "").strip() if isinstance(item, dict) else ""
        if not value:
            issues.append(_issue("error", "stable_id_required", f"{path}[{index}] needs {key}"))
        elif value in seen:
            issues.append(_issue("error", "stable_id_duplicated", f"{path} duplicated {value}"))
        seen.add(value)
