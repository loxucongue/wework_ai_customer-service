from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings


class CustomerMemoryStore:
    def __init__(self, settings: Settings):
        self.memory_dir: Path = settings.memory_dir

    def load(self, customer_id: str) -> dict[str, Any]:
        path = self._path(customer_id)
        if not path.exists():
            return self._empty(customer_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else self._empty(customer_id)
        except (OSError, json.JSONDecodeError):
            return self._empty(customer_id)

    def save_update(
        self,
        customer_id: str,
        *,
        profile_update: dict[str, Any],
        event_updates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        data = self.load(customer_id)
        data["customer_id"] = customer_id
        data["updated_at"] = self._now()
        if profile_update:
            self._merge_profile(data, profile_update)
        if event_updates:
            events = data.setdefault("history_events", [])
            if isinstance(events, list):
                seen_ids = {str(item.get("event_id")) for item in events if isinstance(item, dict)}
                for event in event_updates:
                    if event.get("event_id") and str(event.get("event_id")) in seen_ids:
                        continue
                    events.append(event)
                data["history_events"] = events[-100:]
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._path(customer_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def _path(self, customer_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", customer_id or "unknown")
        return self.memory_dir / f"{safe}.json"

    @staticmethod
    def _empty(customer_id: str) -> dict[str, Any]:
        return {
            "customer_id": customer_id,
            "portrait": {},
            "basic_info": {},
            "lifecycle_stage": "",
            "history_events": [],
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _merge_profile(self, data: dict[str, Any], profile_update: dict[str, Any]) -> None:
        portrait_update = profile_update.get("portrait")
        if isinstance(portrait_update, dict):
            portrait = data.setdefault("portrait", {})
            if isinstance(portrait, dict):
                self._merge_dict(portrait, portrait_update)
        basic_update = profile_update.get("basic_info")
        if isinstance(basic_update, dict):
            basic = data.setdefault("basic_info", {})
            if isinstance(basic, dict):
                self._merge_dict(basic, basic_update)
        lifecycle = profile_update.get("lifecycle_stage")
        if lifecycle:
            data["lifecycle_stage"] = lifecycle

    def _merge_dict(self, target: dict[str, Any], update: dict[str, Any]) -> None:
        for key, value in update.items():
            if value in ("", None, [], {}):
                continue
            if isinstance(value, list):
                target[key] = self._merge_list(target.get(key), value)
            elif isinstance(value, dict):
                nested = target.setdefault(key, {})
                if isinstance(nested, dict):
                    self._merge_dict(nested, value)
                else:
                    target[key] = value
            else:
                target[key] = value

    @staticmethod
    def _merge_list(existing: Any, incoming: list[Any]) -> list[Any]:
        result: list[Any] = []
        for value in existing if isinstance(existing, list) else []:
            if value not in result:
                result.append(value)
        for value in incoming:
            if value not in result:
                result.append(value)
        return result

