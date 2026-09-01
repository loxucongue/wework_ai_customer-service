from __future__ import annotations

from enum import Enum


class RuntimeRole(str, Enum):
    REPLY = "reply"
    CONTROL = "control"
    WORKER = "worker"


_LEGACY_ROLE_ALIASES = {
    "model_led_sales_brain_v3": RuntimeRole.REPLY,
    "primary": RuntimeRole.CONTROL,
    "workers": RuntimeRole.WORKER,
}


def normalize_runtime_role(value: str) -> RuntimeRole:
    candidate = str(value or "").strip().lower()
    try:
        return RuntimeRole(candidate)
    except ValueError:
        try:
            return _LEGACY_ROLE_ALIASES[candidate]
        except KeyError as exc:
            supported = ", ".join(role.value for role in RuntimeRole)
            raise ValueError(f"unsupported AI_PATHS_SERVICE_ROLE={value!r}; expected one of: {supported}") from exc
