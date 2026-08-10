from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.services.storage.repositories import AppRepository


def _store_event_facts(store: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    parking = str(store.get("parking") or store.get("parking_name") or store.get("parking_address") or "").strip()
    return {
        "store_id": str(store.get("store_id") or store.get("id") or "").strip(),
        "store_name": str(store.get("store_name") or store.get("name") or "").strip(),
        "province": str(store.get("province") or "").strip(),
        "city": str(store.get("city") or "").strip(),
        "district": str(store.get("district") or "").strip(),
        "address": str(store.get("store_address") or store.get("address") or "").strip(),
        "business_hours": str(store.get("business_hours") or "").strip(),
        "parking": parking,
        "map_url": str(store.get("map_url") or "").strip(),
        "request_id": str(request_id or "").strip(),
    }


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
        if not profile_update and not event_updates:
            return self.load(customer_id)
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
                    "event_time": now,
                    "facts": {
                        "document_ids": clean_ids,
                        "image_urls": image_urls or [],
                        "request_id": request_id,
                    },
                    "source": "reply_delivery",
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

    def record_activity_intro_image_sent(
        self,
        customer_id: str,
        *,
        image_url: str,
        request_id: str = "",
        send_mode: str = "",
    ) -> dict[str, Any]:
        clean_url = str(image_url or "").strip()
        if not clean_url:
            return {"status": "skipped", "reason": "empty_image_url"}
        data = self.load(customer_id)
        now = self._now()
        data["customer_id"] = customer_id
        data["updated_at"] = now
        events = data.setdefault("history_events", [])
        if isinstance(events, list):
            event_id = f"activity_intro_image_sent_{request_id or uuid4()}"
            if not any(isinstance(item, dict) and item.get("event_id") == event_id for item in events):
                events.append(
                    {
                        "event_id": event_id,
                        "event_type": "activity_intro_image_sent",
                        "event_time": now,
                        "facts": {
                            "image_url": clean_url,
                            "request_id": request_id,
                            "send_mode": send_mode,
                        },
                        "source": "sop_delivery",
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
        return {"status": "recorded", "image_url": clean_url}

    def record_sop_pack_sent(
        self,
        customer_id: str,
        *,
        sop_pack_id: str,
        sop_category: str,
        source_event_id: str,
        message_types: list[str],
        sent_at: str = "",
        task_id: str = "",
    ) -> dict[str, Any]:
        """Record a successfully sent SOP pack without storing message bodies or identity data."""
        clean_pack_id = str(sop_pack_id or "").strip()
        if not clean_pack_id:
            return {"status": "skipped", "reason": "empty_sop_pack_id"}

        clean_types: list[str] = []
        for message_type in message_types:
            value = str(message_type or "").strip()
            if value and value not in clean_types:
                clean_types.append(value)
        created_at = str(sent_at or "").strip() or self._now()
        event_id = f"sop_pack_sent_{task_id or uuid4()}"
        data = self.load(customer_id)
        events = data.setdefault("history_events", [])
        if not isinstance(events, list):
            events = []
            data["history_events"] = events
        if any(isinstance(item, dict) and item.get("event_id") == event_id for item in events):
            return {"status": "skipped", "reason": "duplicate_event", "event_id": event_id}

        events.append(
            {
                "event_id": event_id,
                "event_type": "sop_pack_sent",
                "event_time": created_at,
                "facts": {
                    "sop_pack_id": clean_pack_id,
                    "sop_category": str(sop_category or "").strip(),
                    "message_types": clean_types,
                    "source_event_id": str(source_event_id or "").strip(),
                    "task_id": str(task_id or "").strip(),
                },
                "source": "sop_delivery",
            }
        )
        data["customer_id"] = customer_id
        data["updated_at"] = created_at
        data["history_events"] = events[-100:]
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._path(customer_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.repository:
            try:
                self.repository.save_memory(customer_id, data)
            except Exception:
                pass
        return {"status": "recorded", "event_id": event_id, "sop_pack_id": clean_pack_id}

    def record_stop_contact(
        self,
        customer_id: str,
        *,
        request_id: str,
        evidence_refs: list[str],
        reason: str = "",
    ) -> dict[str, Any]:
        """Persist a model-confirmed stop-contact fact without storing message bodies."""
        clean_customer_id = str(customer_id or "").strip()
        clean_request_id = str(request_id or "").strip()
        if not clean_customer_id:
            return {"status": "skipped", "reason": "missing_customer_scope"}

        data = self.load(clean_customer_id)
        events = data.setdefault("history_events", [])
        if not isinstance(events, list):
            events = []
            data["history_events"] = events
        event_id = f"stop_contact_{clean_request_id or uuid4()}"
        if any(isinstance(item, dict) and item.get("event_id") == event_id for item in events):
            return {"status": "skipped", "reason": "duplicate_event", "event_id": event_id}

        now = self._now()
        events.append(
            {
                "event_id": event_id,
                "event_type": "stop_contact_confirmed",
                "event_time": now,
                "facts": {
                    "evidence_refs": [str(item) for item in evidence_refs if str(item or "").strip()],
                    "request_id": clean_request_id,
                    "reason": str(reason or "")[:500],
                },
                "source": "safety_gate_model",
            }
        )
        data["customer_id"] = clean_customer_id
        data["updated_at"] = now
        data["history_events"] = events[-100:]
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._path(clean_customer_id).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if self.repository:
            try:
                self.repository.save_memory(clean_customer_id, data)
            except Exception:
                pass
        return {"status": "recorded", "event_id": event_id}

    def record_store_fact(
        self,
        customer_id: str,
        *,
        store: dict[str, Any],
        event_type: str,
        request_id: str = "",
    ) -> dict[str, Any]:
        store_id = str(store.get("store_id") or store.get("id") or "").strip()
        store_name = str(store.get("store_name") or store.get("name") or "").strip()
        if not (store_id or store_name):
            return {"status": "skipped", "reason": "missing_store_identity"}
        if event_type not in {"store_matched", "store_address_sent"}:
            event_type = "store_matched"

        facts = _store_event_facts(store, request_id=request_id)
        data = self.load(customer_id)
        basic_info = data.setdefault("basic_info", {})
        if not isinstance(basic_info, dict):
            basic_info = {}
            data["basic_info"] = basic_info
        if facts.get("city"):
            basic_info["city"] = facts["city"]
        area_or_landmark = facts.get("district") or facts.get("address")
        if area_or_landmark:
            basic_info["area_or_landmark"] = area_or_landmark
        if facts.get("store_id"):
            basic_info["preferred_store_id"] = facts["store_id"]
        if facts.get("store_name"):
            basic_info["preferred_store_name"] = facts["store_name"]

        now = self._now()
        data["customer_id"] = customer_id
        data["updated_at"] = now
        events = data.setdefault("history_events", [])
        if isinstance(events, list):
            events.append(
                {
                    "event_id": f"{event_type}_{facts.get('store_id') or store_name}_{request_id or uuid4()}",
                    "event_type": event_type,
                    "event_time": now,
                    "facts": facts,
                    "source": "store_tool" if event_type == "store_matched" else "reply_delivery",
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
        return {
            "status": "recorded",
            "event_type": event_type,
            "store_id": facts.get("store_id", ""),
            "store_name": facts.get("store_name", ""),
            "city": facts.get("city", ""),
        }

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
        if isinstance(portrait.get("summary"), str) and portrait.get("summary"):
            return
        needs = portrait.get("needs") if isinstance(portrait.get("needs"), list) else []
        pain_points = portrait.get("pain_points") if isinstance(portrait.get("pain_points"), list) else []
        projects = portrait.get("projects") if isinstance(portrait.get("projects"), list) else []
        concerns = portrait.get("concerns") if isinstance(portrait.get("concerns"), list) else []
        customer_type_tags = (
            portrait.get("customer_type_tags")
            if isinstance(portrait.get("customer_type_tags"), list)
            else []
        )
        main_objection = str(portrait.get("main_objection") or "").strip()
        parts: list[str] = []
        if customer_type_tags:
            parts.append("类型：" + "、".join(str(item) for item in customer_type_tags[:3]))
        if pain_points:
            parts.append("关注：" + "、".join(str(item) for item in pain_points[:4]))
        if needs:
            parts.append("希望：" + "、".join(str(item) for item in needs[:4]))
        if projects:
            parts.append("提到：" + "、".join(str(item) for item in projects[:3]))
        if concerns:
            parts.append("顾虑：" + "、".join(str(item) for item in concerns[:3]))
        if main_objection:
            parts.append("主要阻力：" + main_objection)
        if parts:
            portrait["summary"] = "；".join(parts) + "。"
