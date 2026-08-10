from __future__ import annotations

from typing import Any


def reply_governance_flags(settings: Any | None) -> dict[str, Any]:
    """Expose rollout flags to model nodes without coupling them to Settings."""
    shadow_mode = bool(getattr(settings, "reply_governance_shadow_mode", True))
    configured = {
        "model_semantic_routing_enabled": bool(
            getattr(settings, "reply_model_semantic_routing_enabled", False)
        ),
        "semantic_contract_enabled": bool(
            getattr(settings, "reply_semantic_contract_enabled", False)
        ),
        "model_payment_sequencing_enabled": bool(
            getattr(settings, "reply_model_payment_sequencing_enabled", False)
        ),
        "event_schema_only_normalizer_enabled": bool(
            getattr(settings, "sop_event_schema_only_normalizer_enabled", False)
        ),
    }
    return {
        **{key: value and not shadow_mode for key, value in configured.items()},
        "shadow_mode": shadow_mode,
        "configured": configured,
    }


def governance_enabled(state: dict[str, Any] | None, flag: str) -> bool:
    if not isinstance(state, dict):
        return False
    governance = state.get("reply_governance")
    return bool(isinstance(governance, dict) and governance.get(flag))


def governance_configured(state: dict[str, Any] | None, flag: str) -> bool:
    if not isinstance(state, dict):
        return False
    governance = state.get("reply_governance")
    configured = governance.get("configured") if isinstance(governance, dict) else {}
    return bool(isinstance(configured, dict) and configured.get(flag))
