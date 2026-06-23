from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.services.storage.repositories import AppRepository


class CustomerMemoryStore:
    def __init__(self, settings: Settings, repository: AppRepository | None = None):
        self.memory_dir: Path = settings.memory_dir
        self.repository = repository

    def load(self, customer_id: str) -> dict[str, Any]:
        if self.repository:
            memory = self.repository.load_memory(customer_id)
            if memory:
                return memory
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
        if self.repository:
            try:
                self.repository.save_memory(customer_id, data)
            except Exception:
                pass
        return data

    def clear(self, customer_id: str) -> None:
        if self.repository:
            self.repository.clear_memory(customer_id)
        path = self._path(customer_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def record_case_images_sent(
        self,
        customer_id: str,
        *,
        document_ids: list[str],
        request_id: str = "",
        image_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        clean_ids = [str(item).strip() for item in document_ids if str(item).strip()]
        if not clean_ids:
            return {"status": "skipped", "reason": "empty_document_ids", "document_ids": []}
        data = self.load(customer_id)
        portrait = data.setdefault("portrait", {})
        if not isinstance(portrait, dict):
            portrait = {}
            data["portrait"] = portrait
        existing = [str(item).strip() for item in portrait.get("sent_case_document_ids", []) if str(item).strip()] if isinstance(portrait.get("sent_case_document_ids"), list) else []
        merged: list[str] = []
        for doc_id in [*existing, *clean_ids]:
            if doc_id not in merged:
                merged.append(doc_id)
        portrait["sent_case_document_ids"] = merged[-200:]
        now = self._now()
        data["customer_id"] = customer_id
        data["updated_at"] = now
        events = data.setdefault("history_events", [])
        if isinstance(events, list):
            events.append(
                {
                    "event_id": f"case_image_sent_{request_id or uuid4()}",
                    "event_type": "case_image_sent",
                    "created_at": now,
                    "summary": "已向客户发送效果案例图片",
                    "facts": {
                        "document_ids": clean_ids,
                        "image_urls": image_urls or [],
                        "request_id": request_id,
                    },
                }
            )
            data["history_events"] = events[-100:]
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._path(customer_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.repository:
            try:
                self.repository.save_memory(customer_id, data)
            except Exception:
                pass
        return {"status": "recorded", "document_ids": clean_ids, "total_sent_case_document_ids": len(portrait["sent_case_document_ids"])}

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
                self._refresh_portrait_summary(portrait)
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

    @staticmethod
    def _refresh_portrait_summary(portrait: dict[str, Any]) -> None:
        needs = portrait.get("needs") if isinstance(portrait.get("needs"), list) else []
        pain_points = portrait.get("pain_points") if isinstance(portrait.get("pain_points"), list) else []
        projects = portrait.get("projects") if isinstance(portrait.get("projects"), list) else []
        concerns = portrait.get("concerns") if isinstance(portrait.get("concerns"), list) else []
        parts: list[str] = []
        if pain_points:
            parts.append("关注" + "、".join(str(item) for item in pain_points[:4]))
        if needs:
            parts.append("希望" + "、".join(str(item) for item in needs[:4]))
        if projects:
            parts.append("提到" + "、".join(str(item) for item in projects[:3]))
        if concerns:
            parts.append("顾虑" + "、".join(str(item) for item in concerns[:3]))
        if parts:
            portrait["summary"] = "，".join(parts) + "。"
