from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings


SCHEMA_VERSION = "ai_sales_policy_v2"
DECISION_SCHEMA_VERSION = "v3_policy_decision_v3"
ALLOWED_RUNTIME_MODES = {"active", "shadow", "off"}
ALLOWED_POLICY_STATUSES = {"draft", "published"}
ALLOWED_SILENT_TASK_MODES = {"off", "shadow"}
ALLOWED_EMOTION_FLOW_ACTIONS = {
    "keep", "lower_pressure", "pause_marketing_turn", "handoff_by_system_rule",
}
EXPECTED_INTENT_KEYS = {
    "fact_inquiry", "blocker_expression", "transaction_progress",
    "information_submission", "defer", "explicit_exit", "normal_exchange",
}
EXPECTED_EMOTION_KEYS = {
    "enthusiastic", "curious", "neutral", "hesitant",
    "cold", "defensive", "impatient", "angry",
}
FORBIDDEN_CONFIG_FIELDS = {"raw_prompt", "system_prompt", "prompt_template", "developer_prompt"}


class AiSalesPolicyProvider(Protocol):
    def load_raw(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return one atomic policy payload and source metadata."""


class LocalJsonAiSalesPolicyProvider:
    def __init__(self, settings: Settings) -> None:
        configured = Path(settings.ai_sales_policy_path)
        ai_paths_root = Path(__file__).resolve().parents[2]
        self.default_path = Path(__file__).resolve().parents[1] / "policies" / "ai_sales_policy_v2.json"
        self.path = configured if configured.is_absolute() else ai_paths_root / configured

    def load_raw(self) -> tuple[dict[str, Any], dict[str, Any]]:
        path = self.path if self.path.exists() else self.default_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("AI sales policy payload must be an object")
        return payload, {
            "provider": "local_json",
            "source": "configured" if path == self.path else "bundled_default",
            "path": str(path),
        }


class AiSalesPolicyService:
    """Validate one published policy snapshot and expose a stable runtime contract.

    Business semantics remain model-owned. This service only handles configuration
    transport, schema validation, stable identifiers and last-known-good fallback.
    """

    def __init__(self, settings: Settings, provider: AiSalesPolicyProvider | None = None) -> None:
        self.settings = settings
        self.provider = provider or LocalJsonAiSalesPolicyProvider(settings)
        self._last_good: dict[str, Any] | None = None
        self._last_error = ""

    def load(self) -> dict[str, Any]:
        try:
            payload, storage = self.provider.load_raw()
            normalized = _normalize_policy(payload)
            audit = _audit_policy(normalized)
            if audit["error_count"]:
                messages = [
                    str(item.get("message") or item.get("code") or "")
                    for item in audit["issues"]
                    if item.get("severity") == "error"
                ]
                raise ValueError("; ".join(messages[:8]))
            result = deepcopy(normalized)
            result["checksum"] = _checksum(normalized)
            result["audit"] = audit
            result["storage"] = storage
            result["runtime_health"] = {
                "status": "ok",
                "using_last_known_good": False,
                "last_error": "",
            }
            self._last_good = deepcopy(result)
            self._last_error = ""
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
            raise ValueError(f"AI sales policy unavailable: {self._last_error}") from exc

    def runtime_snapshot(self) -> dict[str, Any]:
        policy = self.load()
        enabled = bool(self.settings.ai_sales_policy_enabled)
        configured_mode = str(policy.get("runtime_mode") or "off")
        runtime_mode = configured_mode if enabled else "off"
        return {
            "schema_version": policy.get("schema_version"),
            "policy_version": policy.get("policy_version"),
            "decision_schema_version": policy.get("decision_schema_version"),
            "checksum": policy.get("checksum"),
            "status": policy.get("status"),
            "runtime_mode": runtime_mode,
            "closing": deepcopy(policy.get("closing") or {}),
            "routing": deepcopy(policy.get("routing") or {}),
            "intent": deepcopy(policy.get("intent") or {}),
            "emotion": deepcopy(policy.get("emotion") or {}),
            "system_boundaries": deepcopy(policy.get("system_boundaries") or []),
            "source": deepcopy(policy.get("storage") or {}),
            "runtime_health": deepcopy(policy.get("runtime_health") or {}),
        }


def _normalize_policy(payload: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(payload)
    result["schema_version"] = _text(payload.get("schema_version"))
    result["decision_schema_version"] = _text(payload.get("decision_schema_version"))
    result["policy_version"] = _text(payload.get("policy_version"))
    result["status"] = _text(payload.get("status")) or "draft"
    result["runtime_mode"] = _text(payload.get("runtime_mode")) or "off"
    result["updated_at"] = _text(payload.get("updated_at"))
    result["purpose"] = _text(payload.get("purpose"))
    result["ownership"] = _mapping(payload.get("ownership"), "ownership")
    result["closing"] = _normalize_closing(_mapping(payload.get("closing"), "closing"))
    result["routing"] = _normalize_routing(_mapping(payload.get("routing"), "routing"))
    result["intent"] = _normalize_intent(_mapping(payload.get("intent"), "intent"))
    result["emotion"] = _normalize_emotion(_mapping(payload.get("emotion"), "emotion"))
    result["system_boundaries"] = _text_list(payload.get("system_boundaries"))
    return result


def _normalize_closing(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(raw.get("enabled", False)),
        "catalog_source": _identifier(raw.get("catalog_source")),
        "silent_tasks_mode": _text(raw.get("silent_tasks_mode")) or "off",
        "description": _text(raw.get("description")),
    }


def _normalize_routing(raw: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(raw)
    result["mode"] = _identifier(raw.get("mode"))
    result["description"] = _text(raw.get("description"))
    for field in ("fixed_priority", "business_tasks"):
        result[field] = [
            {key: _text(value) for key, value in item.items() if key in {"key", "name", "action", "goal"}}
            for item in _mapping_list(raw.get(field), f"routing.{field}")
        ]
        for item in result[field]:
            item["key"] = _identifier(item.get("key"))
    return result


def _normalize_intent(raw: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(raw)
    result["realtime_intents"] = [
        {
            "key": _identifier(item.get("key")),
            "name": _text(item.get("name")),
            "definition": _text(item.get("definition")),
            "usage": _text(item.get("usage")),
        }
        for item in _mapping_list(raw.get("realtime_intents"), "intent.realtime_intents")
    ]
    result["analytics_scoring"] = _mapping(raw.get("analytics_scoring"), "intent.analytics_scoring")
    return result


def _normalize_emotion(raw: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(raw)
    result["weak_evidence"] = _text_list(raw.get("weak_evidence"))
    result["labels"] = [
        {
            "key": _identifier(item.get("key")),
            "name": _text(item.get("name")),
            "definition": _text(item.get("definition")),
            "minimum_evidence": _text(item.get("minimum_evidence")),
            "not_enough_evidence": _text(item.get("not_enough_evidence")),
            "reply_effect": _text(item.get("reply_effect")),
            "flow_action": _identifier(item.get("flow_action")),
            "minimum_flow_confidence": _identifier(item.get("minimum_flow_confidence")),
            "low_confidence_flow_action": _identifier(item.get("low_confidence_flow_action")),
        }
        for item in _mapping_list(raw.get("labels"), "emotion.labels")
    ]
    return result


def _audit_policy(policy: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if policy.get("schema_version") != SCHEMA_VERSION:
        issues.append(_issue("error", "schema_version", f"schema_version must be {SCHEMA_VERSION}"))
    if policy.get("decision_schema_version") != DECISION_SCHEMA_VERSION:
        issues.append(
            _issue(
                "error", "decision_schema_version",
                f"decision_schema_version must be {DECISION_SCHEMA_VERSION}",
            )
        )
    if not policy.get("policy_version"):
        issues.append(_issue("error", "policy_version", "policy_version is required"))
    if policy.get("status") not in ALLOWED_POLICY_STATUSES:
        issues.append(_issue("error", "status", "status must be draft or published"))
    if policy.get("runtime_mode") not in ALLOWED_RUNTIME_MODES:
        issues.append(_issue("error", "runtime_mode", "runtime_mode must be active, shadow or off"))
    for path in _find_forbidden_fields(policy):
        issues.append(_issue("error", "raw_prompt_forbidden", f"raw prompt field is forbidden: {path}"))

    closing = policy.get("closing") if isinstance(policy.get("closing"), dict) else {}
    if closing.get("silent_tasks_mode") not in ALLOWED_SILENT_TASK_MODES:
        issues.append(
            _issue("error", "silent_tasks_mode", "silent_tasks_mode must be off or shadow")
        )
    if closing.get("catalog_source") != "configured_closing_catalog":
        issues.append(
            _issue(
                "error",
                "closing_catalog_source",
                "closing catalog_source must be configured_closing_catalog",
            )
        )

    routing = policy.get("routing") if isinstance(policy.get("routing"), dict) else {}
    if routing.get("mode") != "collect_all_choose_one_primary":
        issues.append(_issue("error", "routing_mode", "routing mode must collect all signals and choose one primary task"))
    _audit_unique_keys(issues, routing.get("fixed_priority"), "routing.fixed_priority", "key")
    _audit_unique_keys(issues, routing.get("business_tasks"), "routing.business_tasks", "key")

    intent = policy.get("intent") if isinstance(policy.get("intent"), dict) else {}
    _audit_unique_keys(issues, intent.get("realtime_intents"), "intent.realtime_intents", "key")
    intent_keys = {
        _text(item.get("key"))
        for item in intent.get("realtime_intents") or []
        if isinstance(item, dict)
    }
    if intent_keys != EXPECTED_INTENT_KEYS:
        issues.append(_issue("error", "intent_catalog", "intent catalog must contain the versioned 7 intents"))
    scoring = intent.get("analytics_scoring") if isinstance(intent.get("analytics_scoring"), dict) else {}
    if bool(scoring.get("controls_reply")) or bool(scoring.get("controls_closing")):
        issues.append(_issue("error", "analytics_controls_runtime", "analytics scoring cannot control reply or closing"))

    emotion = policy.get("emotion") if isinstance(policy.get("emotion"), dict) else {}
    _audit_unique_keys(issues, emotion.get("labels"), "emotion.labels", "key")
    emotion_keys = {
        _text(item.get("key"))
        for item in emotion.get("labels") or []
        if isinstance(item, dict)
    }
    if emotion_keys != EXPECTED_EMOTION_KEYS:
        issues.append(_issue("error", "emotion_catalog", "emotion catalog must contain the versioned 8 emotions"))
    for item in emotion.get("labels") or []:
        if isinstance(item, dict) and item.get("flow_action") not in ALLOWED_EMOTION_FLOW_ACTIONS:
            issues.append(
                _issue(
                    "error", "emotion_flow_action",
                    f"emotion {item.get('key') or '?'} has invalid flow_action",
                )
            )
        if isinstance(item, dict) and item.get("minimum_flow_confidence") not in {None, "", "low", "medium", "high"}:
            issues.append(
                _issue(
                    "error", "emotion_flow_confidence",
                    f"emotion {item.get('key') or '?'} has invalid minimum_flow_confidence",
                )
            )
        if isinstance(item, dict) and item.get("low_confidence_flow_action") not in {None, "", *ALLOWED_EMOTION_FLOW_ACTIONS}:
            issues.append(
                _issue(
                    "error", "emotion_low_confidence_flow_action",
                    f"emotion {item.get('key') or '?'} has invalid low_confidence_flow_action",
                )
            )
    if not policy.get("system_boundaries"):
        issues.append(_issue("warning", "system_boundaries_empty", "system boundary descriptions are empty"))
    errors = sum(1 for item in issues if item["severity"] == "error")
    warnings = sum(1 for item in issues if item["severity"] == "warning")
    return {
        "status": "error" if errors else ("warning" if warnings else "ok"),
        "error_count": errors,
        "warning_count": warnings,
        "issues": issues,
    }


def _audit_unique_keys(
    issues: list[dict[str, str]],
    values: Any,
    path: str,
    key_field: str,
) -> None:
    seen: set[str] = set()
    for index, item in enumerate(values if isinstance(values, list) else []):
        key = _identifier(item.get(key_field)) if isinstance(item, dict) else ""
        if not key:
            issues.append(_issue("error", "stable_key_required", f"{path}[{index}] needs {key_field}"))
        elif key in seen:
            issues.append(_issue("error", "stable_key_duplicated", f"duplicated key {path}.{key}"))
        seen.add(key)


def _find_forbidden_fields(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if str(key).strip().lower() in FORBIDDEN_CONFIG_FIELDS:
                found.append(path)
            found.extend(_find_forbidden_fields(nested, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_find_forbidden_fields(nested, f"{prefix}[{index}]"))
    return found


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return deepcopy(value)


def _mapping_list(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    if any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field} items must be objects")
    return [deepcopy(item) for item in value]


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _identifier(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    if not all(char.islower() or char.isdigit() or char == "_" for char in text):
        return ""
    return text


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("value must be a list")
    return [_text(item) for item in value if _text(item)]


def _checksum(policy: dict[str, Any]) -> str:
    payload = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _issue(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}
