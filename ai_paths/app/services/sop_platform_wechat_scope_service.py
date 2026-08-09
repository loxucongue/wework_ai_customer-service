from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any


class SopPlatformWechatScopeService:
    """Persists the exact user_wechat values allowed into the SOP decision chain."""

    def __init__(self, settings: Any) -> None:
        configured_path = getattr(settings, "sop_platform_wechat_scope_path", None)
        db_path = Path(getattr(settings, "db_path", Path("data/ai_paths.db")))
        self.path = Path(configured_path) if configured_path else db_path.parent / "sop_platform_wechat_scope.json"
        self._lock = RLock()

    def is_enabled(self, user_wechat: str) -> bool:
        candidate = str(user_wechat or "").strip()
        if not candidate:
            return False
        return candidate in {
            item["user_wechat"]
            for item in self.load()["accounts"]
            if item.get("enabled") is True
        }

    def is_configured(self) -> bool:
        return self.load().get("configured") is True

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {
                    "version": 1,
                    "strict_mode": True,
                    "configured": False,
                    "accounts": [],
                    "updated_at": "",
                }
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"企微号范围配置读取失败: {exc}") from exc
            return self._normalize(payload)

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize(payload)
        normalized["configured"] = True
        normalized["updated_at"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
            temporary.write_text(
                json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        return normalized

    def view(self, repository: Any, *, days: int = 2) -> dict[str, Any]:
        config = self.load()
        observed = (
            repository.list_recent_platform_sop_wechats(days=days)
            if hasattr(repository, "list_recent_platform_sop_wechats")
            else []
        )
        configured = {item["user_wechat"]: item for item in config["accounts"]}
        observations = {str(item.get("user_wechat") or ""): item for item in observed}
        values = sorted(set(configured) | {value for value in observations if value})
        items: list[dict[str, Any]] = []
        for value in values:
            configured_item = configured.get(value, {})
            observed_item = observations.get(value, {})
            items.append(
                {
                    "user_wechat": value,
                    "enabled": configured_item.get("enabled") is True,
                    "source": str(configured_item.get("source") or ("observed" if observed_item else "manual")),
                    "task_count": int(observed_item.get("task_count") or 0),
                    "first_seen_at": str(observed_item.get("first_seen_at") or ""),
                    "last_seen_at": str(observed_item.get("last_seen_at") or ""),
                }
            )
        return {
            **config,
            "days": max(1, min(int(days or 2), 30)),
            "items": items,
            "enabled_count": sum(1 for item in items if item["enabled"]),
        }

    @staticmethod
    def _normalize(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("企微号范围配置必须是 JSON 对象")
        raw_accounts = payload.get("accounts", [])
        if not isinstance(raw_accounts, list):
            raise ValueError("accounts 必须是数组")
        accounts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_accounts:
            if not isinstance(raw, dict):
                raise ValueError("accounts 中每一项必须是对象")
            value = str(raw.get("user_wechat") or "").strip()
            if not value:
                raise ValueError("user_wechat 不能为空")
            if len(value) > 128:
                raise ValueError("user_wechat 最长 128 个字符")
            if value in seen:
                raise ValueError(f"user_wechat 重复: {value}")
            seen.add(value)
            accounts.append(
                {
                    "user_wechat": value,
                    "enabled": raw.get("enabled") is True,
                    "source": str(raw.get("source") or "manual").strip()[:32] or "manual",
                }
            )
        accounts.sort(key=lambda item: item["user_wechat"])
        return {
            "version": 1,
            "strict_mode": True,
            "configured": payload.get("configured") is True,
            "accounts": accounts,
            "updated_at": str(payload.get("updated_at") or ""),
        }
