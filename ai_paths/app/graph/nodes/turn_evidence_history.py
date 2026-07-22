from __future__ import annotations

from typing import Any


def build_history_evidence(
    *,
    is_short_message: bool,
    is_reference_message: bool,
    binding_source: str,
    last_assistant_action: str,
    last_assistant_text: str,
    history: list[Any],
) -> dict[str, Any]:
    return _drop_empty(
        {
            "is_short_message": is_short_message,
            "is_deictic_message": is_reference_message,
            "binding_source": binding_source,
            "recent_assistant_action": last_assistant_action if last_assistant_action != "none" else "",
            "recent_assistant_text": str(last_assistant_text or "")[:600],
            "history_window_size": len(history[-20:]) if isinstance(history, list) else 0,
        }
    )


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
