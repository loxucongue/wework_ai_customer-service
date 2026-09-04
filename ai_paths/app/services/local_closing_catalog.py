from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from app.config import Settings


SCHEMA_VERSION = "ai_closing_catalog_v1"
ALLOWED_SOURCE_MODES = {"external", "local", "external_then_local"}


class LocalClosingCatalogProvider:
    """Load provisional closing knowledge without making a sales decision."""

    def __init__(self, settings: Settings) -> None:
        configured = Path(settings.closing_catalog_local_path)
        ai_paths_root = Path(__file__).resolve().parents[2]
        self.path = configured if configured.is_absolute() else ai_paths_root / configured
        self.mode = str(settings.closing_catalog_source or "external_then_local").strip().lower()
        if self.mode not in ALLOWED_SOURCE_MODES:
            raise ValueError(
                "AI_CLOSING_CATALOG_SOURCE must be external, local, or external_then_local"
            )
        self._cached: dict[str, Any] | None = None
        self._cached_mtime_ns: int | None = None

    @property
    def enabled(self) -> bool:
        return self.mode in {"local", "external_then_local"}

    def load(self) -> dict[str, Any]:
        if not self.enabled:
            return _unavailable("local_closing_catalog_disabled")
        try:
            mtime_ns = self.path.stat().st_mtime_ns
            if self._cached is not None and self._cached_mtime_ns == mtime_ns:
                result = copy.deepcopy(self._cached)
                result["cache_hit"] = True
                return result
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            result = _normalize_catalog(payload)
            self._cached = copy.deepcopy(result)
            self._cached_mtime_ns = mtime_ns
            return result
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return _unavailable(f"{type(exc).__name__}: {exc}")

    def query_scripts(self, *, checkpoint_type_id: int) -> dict[str, Any]:
        catalog = self.load()
        if catalog.get("status") != "ok":
            return {
                "schema_version": "follow_script_query_v1",
                "status": "error",
                "source": "local_closing_catalog",
                "reason": str(catalog.get("reason") or "local_closing_catalog_unavailable"),
                "total": 0,
                "items": [],
                "cache_hit_pages": 0,
                "duration_ms": 0,
            }
        items = [
            copy.deepcopy(item)
            for item in catalog.get("scripts") or []
            if int((item.get("checkpoint_type") or {}).get("id") or 0)
            == int(checkpoint_type_id or 0)
        ]
        return {
            "schema_version": "follow_script_query_v1",
            "status": "ok",
            "source": "local_closing_catalog",
            "total": len(items),
            "items": items,
            "cache_hit_pages": int(bool(catalog.get("cache_hit"))),
            "duration_ms": 0,
        }


def _normalize_catalog(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("local closing catalog must be an object")
    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    rules = payload.get("rules") if isinstance(payload.get("rules"), dict) else None
    sequences = payload.get("sequences") if isinstance(payload.get("sequences"), list) else None
    scripts = payload.get("scripts") if isinstance(payload.get("scripts"), list) else None
    if rules is None or sequences is None or scripts is None:
        raise ValueError("rules, sequences, and scripts are required")
    triggers = rules.get("triggers") if isinstance(rules.get("triggers"), list) else []
    if not triggers or not sequences or not scripts:
        raise ValueError("local closing rules, sequences, and scripts cannot be empty")

    rule_keys = _unique_keys(triggers, "rule_key", "rule")
    sequence_keys = _unique_keys(sequences, "sequence_key", "sequence")
    script_codes = _unique_keys(scripts, "script_code", "script")
    del rule_keys, sequence_keys, script_codes

    script_type_ids: set[int] = set()
    for script in scripts:
        if not isinstance(script, dict):
            raise ValueError("script items must be objects")
        checkpoint_type = script.get("checkpoint_type")
        if not isinstance(checkpoint_type, dict):
            raise ValueError(f"script {script.get('script_code')} needs checkpoint_type")
        type_id = int(checkpoint_type.get("id") or 0)
        if type_id <= 0:
            raise ValueError(f"script {script.get('script_code')} needs a positive type id")
        script_type_ids.add(type_id)
        if not str(script.get("body_text") or "").strip():
            raise ValueError(f"script {script.get('script_code')} needs body_text")

    node_keys: set[str] = set()
    for sequence in sequences:
        if not isinstance(sequence, dict):
            raise ValueError("sequence items must be objects")
        nodes = sequence.get("nodes") if isinstance(sequence.get("nodes"), list) else []
        if not nodes:
            raise ValueError(f"sequence {sequence.get('sequence_key')} needs nodes")
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError("node items must be objects")
            node_key = str(node.get("node_key") or "").strip()
            if not node_key or node_key in node_keys:
                raise ValueError(f"node key is missing or duplicated: {node_key}")
            node_keys.add(node_key)
            if str(node.get("timing") or "") not in {"immediate", "event_driven", "silent_after"}:
                raise ValueError(f"node {node_key} has invalid timing")
            action_type = node.get("action_type") if isinstance(node.get("action_type"), dict) else {}
            script_type = node.get("script_type") if isinstance(node.get("script_type"), dict) else {}
            if int(action_type.get("id") or 0) <= 0:
                raise ValueError(f"node {node_key} needs an action type")
            if int(script_type.get("id") or 0) not in script_type_ids:
                raise ValueError(f"node {node_key} references an unknown script type")

    checksum_payload = {"rules": rules, "sequences": sequences, "scripts": scripts}
    checksum = hashlib.sha256(
        json.dumps(checksum_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    result = {
        "schema_version": "closing_catalog_v1",
        "catalog_schema_version": SCHEMA_VERSION,
        "catalog_version": str(payload.get("catalog_version") or ""),
        "business_status": str(payload.get("business_status") or "provisional"),
        "status": "ok",
        "source": "local_closing_catalog",
        "source_mode": "local",
        "reason": "",
        "freshness_status": "local",
        "checksum": checksum,
        "rules": copy.deepcopy(rules),
        "sequences": copy.deepcopy(sequences),
        "scripts": copy.deepcopy(scripts),
        "trigger_count": len(triggers),
        "sequence_count": len(sequences),
        "node_count": sum(len(item.get("nodes") or []) for item in sequences if isinstance(item, dict)),
        "script_count": len(scripts),
        "eligibility_status": "configured",
        "quality_flags": ["provisional_business_catalog"]
        if str(payload.get("business_status") or "provisional") != "published"
        else [],
        "cache_hit": False,
        "duration_ms": 0,
    }
    return result


def _unique_keys(items: list[Any], field: str, label: str) -> set[str]:
    keys: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{label} items must be objects")
        key = str(item.get(field) or "").strip()
        if not key or key in keys:
            raise ValueError(f"{label} key is missing or duplicated: {key}")
        keys.add(key)
    return keys


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "closing_catalog_v1",
        "status": "unavailable",
        "source": "local_closing_catalog",
        "source_mode": "local",
        "reason": str(reason)[:500],
        "freshness_status": "unavailable",
        "checksum": "",
        "rules": {},
        "sequences": [],
        "scripts": [],
        "quality_flags": [],
        "cache_hit": False,
        "duration_ms": 0,
    }
