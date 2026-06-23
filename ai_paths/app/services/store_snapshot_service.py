from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.services.platform_agent_client import PlatformAgentClient
from app.services.coze_oauth import CozeOAuthTokenProvider


class StoreSnapshotService:
    """Daily cache of platform store details used to hydrate customer-scoped stores."""

    def __init__(
        self,
        settings: Settings,
        platform_client: PlatformAgentClient | None,
        *,
        max_workers: int = 24,
    ) -> None:
        self._path = settings.store_snapshot_path
        self._ttl_seconds = max(1, int(settings.store_snapshot_ttl_hours)) * 3600
        self._platform_client = platform_client
        self._max_workers = max_workers
        self._settings = settings
        self._geocode_workflow_id = str(getattr(settings, "geocode_workflow_id", "") or "").strip()
        self._coze_oauth_provider = CozeOAuthTokenProvider(settings) if self._geocode_workflow_id else None

    def load_snapshot(self, *, request_context: dict[str, Any] | None = None) -> dict[str, Any]:
        snapshot = self._read_snapshot()
        if snapshot and not self._is_stale(snapshot):
            return snapshot
        refreshed = self.refresh_snapshot(request_context=request_context, allow_existing_on_error=True)
        return refreshed or snapshot or self._empty_snapshot(source="missing_snapshot")

    def refresh_snapshot(
        self,
        *,
        request_context: dict[str, Any] | None = None,
        allow_existing_on_error: bool = True,
    ) -> dict[str, Any]:
        existing = self._read_snapshot()
        if not self._platform_client or not self._platform_client.available:
            if allow_existing_on_error and existing:
                existing["refresh_error"] = "platform_agent_unavailable"
                return existing
            return self._empty_snapshot(source="platform_agent_unavailable", refresh_error="platform_agent_unavailable")

        try:
            rows = self._platform_client.list_store_options(request_context=request_context)
            stores = self._hydrate_rows(rows, request_context or {})
            snapshot = self._build_snapshot(stores)
            self._write_snapshot(snapshot)
            return snapshot
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if allow_existing_on_error and existing:
                existing["refresh_error"] = error
                return existing
            return self._empty_snapshot(source="refresh_error", refresh_error=error)

    def stores_for_scope(
        self,
        rows: list[dict[str, Any]],
        *,
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = self.load_snapshot(request_context=request_context)
        stores_by_id = snapshot.get("stores_by_id") if isinstance(snapshot.get("stores_by_id"), dict) else {}
        stores: list[dict[str, Any]] = []
        missing_ids: list[str] = []

        for row in rows:
            if not isinstance(row, dict):
                continue
            store_id = str(row.get("id") or row.get("store_id") or "").strip()
            if not store_id:
                continue
            cached = stores_by_id.get(store_id)
            if isinstance(cached, dict) and cached.get("store_name"):
                store = dict(cached)
                store["source"] = "customer_scope_snapshot"
                stores.append(store)
                continue
            missing_ids.append(store_id)
            stores.append(self._store_from_row(row, detail={}, detail_source="scope_row_fallback"))

        stores = sorted(
            [store for store in stores if store.get("store_id") and store.get("store_name")],
            key=lambda item: (
                str(item.get("province") or ""),
                str(item.get("city") or ""),
                str(item.get("district") or ""),
                str(item.get("store_name") or ""),
                str(item.get("store_id") or ""),
            ),
        )
        return {
            "stores": stores,
            "grouped_by_region": group_stores_by_region(stores),
            "missing_snapshot_store_ids": list(dict.fromkeys(missing_ids)),
            "snapshot_generated_at": snapshot.get("generated_at"),
            "snapshot_store_count": snapshot.get("store_count", 0),
            "snapshot_source": snapshot.get("source", "store_snapshot"),
            "snapshot_refresh_error": snapshot.get("refresh_error", ""),
        }

    def store_by_id(self, store_id: str, *, request_context: dict[str, Any] | None = None) -> dict[str, Any]:
        snapshot = self.load_snapshot(request_context=request_context)
        stores_by_id = snapshot.get("stores_by_id") if isinstance(snapshot.get("stores_by_id"), dict) else {}
        cached = stores_by_id.get(str(store_id or "").strip())
        return dict(cached) if isinstance(cached, dict) else {}

    def _hydrate_rows(self, rows: list[dict[str, Any]], request_context: dict[str, Any]) -> list[dict[str, Any]]:
        if not rows:
            return []
        output: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self._hydrate_row, row, request_context): row
                for row in rows
                if isinstance(row, dict) and (row.get("id") or row.get("store_id"))
            }
            for future in as_completed(futures):
                output.append(future.result())
        return [item for item in output if item.get("store_id") and item.get("store_name")]

    def _hydrate_row(self, row: dict[str, Any], request_context: dict[str, Any]) -> dict[str, Any]:
        store_id = str(row.get("id") or row.get("store_id") or "").strip()
        detail: dict[str, Any] = {}
        detail_source = "store_info"
        if store_id and self._platform_client:
            try:
                detail = self._platform_client.store_info(store_id, request_context=request_context)
            except Exception as exc:
                detail_source = f"store_info_error:{type(exc).__name__}: {exc}"
        return self._store_from_row(row, detail=detail, detail_source=detail_source)

    def _store_from_row(self, row: dict[str, Any], *, detail: dict[str, Any], detail_source: str) -> dict[str, Any]:
        store_id = str(row.get("id") or row.get("store_id") or "").strip()
        parking = detail.get("parking_info") if isinstance(detail.get("parking_info"), dict) else {}
        address = clean_text(detail.get("tencent_address") or row.get("tencent_address") or row.get("address") or "")
        parking_address = clean_text(parking.get("park_address") or "")
        province, city, district = parse_region(address)
        if not (province or city or district):
            province, city, district = parse_region(parking_address)
        geocode = self._geocode_store_address(address or parking_address)
        if geocode:
            province = clean_text(geocode.get("province") or province)
            city = clean_text(geocode.get("city") or city)
            district = clean_text(geocode.get("district") or geocode.get("township") or district)
            address = address or clean_text(geocode.get("formatted_address") or "")
        if not district:
            district = parse_township(address) or parse_township(parking_address)
        begin = str(row.get("business_hours_begin") or detail.get("business_hours_begin") or "").strip()
        end = str(row.get("business_hours_end") or detail.get("business_hours_end") or "").strip()
        return {
            "store_id": store_id,
            "store_name": clean_text(row.get("name") or detail.get("name") or ""),
            "province": province,
            "city": city,
            "district": district,
            "store_address": address,
            "business_hours": f"{begin}-{end}".strip("-"),
            "is_open": bool(begin and end),
            "map_url": clean_text(detail.get("tencent_map_store") or row.get("tencent_map_store") or row.get("map_store") or ""),
            "parking_name": clean_text(parking.get("park_name") or ""),
            "parking_address": parking_address,
            "parking_url": clean_text(parking.get("park_link") or ""),
            "guidance_video": detail.get("guidance_video") or row.get("guidance_video") or [],
            "location": clean_text(geocode.get("location") if geocode else ""),
            "geocode_formatted_address": clean_text(geocode.get("formatted_address") if geocode else ""),
            "geocode_level": clean_text(geocode.get("level") if geocode else ""),
            "geocode_township": clean_text(geocode.get("township") if geocode else ""),
            "geocode_source": "poi_to_geocode" if geocode else "",
            "source": "store_snapshot",
            "detail_source": detail_source,
        }

    def _geocode_store_address(self, address: str) -> dict[str, Any]:
        address = clean_text(address)
        if not address or not self._geocode_workflow_id or not self._coze_oauth_provider:
            return {}
        try:
            token = self._coze_oauth_provider.get_access_token()
            url = f"{self._settings.coze_api_base.rstrip('/')}/v1/workflow/run"
            payload = {
                "workflow_id": self._geocode_workflow_id,
                "parameters": {"address": address},
            }
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    url,
                    headers=headers,
                    content=json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8"),
                )
                response.raise_for_status()
                return parse_geocode_workflow_response(response.json())
        except Exception:
            return {}

    def _build_snapshot(self, stores: list[dict[str, Any]]) -> dict[str, Any]:
        stores_by_id = {str(store.get("store_id")): store for store in stores if store.get("store_id")}
        generated_at = datetime.now(timezone.utc).isoformat()
        return {
            "source": "platform_agent.option+store_info",
            "generated_at": generated_at,
            "generated_at_epoch": int(time.time()),
            "store_count": len(stores_by_id),
            "stores_by_id": stores_by_id,
            "grouped_by_region": group_stores_by_region(list(stores_by_id.values())),
        }

    def _read_snapshot(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        temp_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._path)

    def _is_stale(self, snapshot: dict[str, Any]) -> bool:
        try:
            generated_at = int(snapshot.get("generated_at_epoch") or 0)
        except (TypeError, ValueError):
            generated_at = 0
        return generated_at <= 0 or time.time() - generated_at > self._ttl_seconds

    @staticmethod
    def _empty_snapshot(*, source: str, refresh_error: str = "") -> dict[str, Any]:
        return {
            "source": source,
            "generated_at": "",
            "generated_at_epoch": 0,
            "store_count": 0,
            "stores_by_id": {},
            "grouped_by_region": {},
            "refresh_error": refresh_error,
        }


def group_stores_by_region(stores: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, Any] = {}
    for store in stores:
        province = str(store.get("province") or "未识别省份").strip()
        city = str(store.get("city") or "未识别城市").strip()
        district = str(store.get("district") or "未识别区域").strip()
        item = {
            key: store.get(key)
            for key in (
                "store_id",
                "store_name",
                "store_address",
                "business_hours",
                "is_open",
                "map_url",
                "parking_name",
                "parking_address",
                "parking_url",
                "guidance_video",
                "location",
                "geocode_formatted_address",
                "geocode_level",
                "geocode_township",
            )
        }
        grouped.setdefault(province, {}).setdefault(city, {}).setdefault(district, []).append(item)
    return grouped


def parse_region(address: str) -> tuple[str, str, str]:
    text = clean_text(address)
    if not text:
        return "", "", ""
    province = _first_match(text, r"^(.{2,12}?(?:省|自治区|特别行政区))")
    rest = text[len(province) :] if province else text
    city = _first_match(rest, r"^(.{2,12}?(?:市|自治州|地区|盟))")
    rest = rest[len(city) :] if city else rest
    district = _first_match(rest, r"^(.{1,12}?(?:区|县|旗|市))")
    if not district:
        district = parse_township(rest)
    if not province and not city:
        direct_city = re.match(r"^(.{2})(.{1,10}?(?:区|县))", text)
        if direct_city:
            city = f"{direct_city.group(1)}市"
            district = direct_city.group(2)
    return province, city, district


def _first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return clean_text(match.group(1)) if match else ""


def parse_township(address: str) -> str:
    text = clean_text(address)
    if not text:
        return ""
    return _first_match(text, r"(.{1,12}?(?:街道|镇|乡))")


def clean_text(value: Any) -> str:
    return re.sub(r"[\u200b-\u200f\ufeff]", "", str(value or "")).strip()


def parse_geocode_workflow_response(raw: dict[str, Any]) -> dict[str, Any]:
    parsed = _parse_geocode_payload(raw)
    if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
        return _first_geocode_item(parsed.get("data"))
    if isinstance(parsed, dict) and isinstance(parsed.get("output"), list):
        return _first_geocode_item(parsed.get("output"))
    if isinstance(parsed, dict) and isinstance(parsed.get("output"), dict):
        return parsed.get("output") or {}
    if isinstance(parsed, list):
        return _first_geocode_item(parsed)
    if isinstance(parsed, dict):
        return _geocode_item_from_dict(parsed)
    return {}


def _parse_geocode_payload(value: Any) -> Any:
    if isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, str) and data:
            try:
                return _parse_geocode_payload(json.loads(data))
            except json.JSONDecodeError:
                return value
        if isinstance(data, (dict, list)):
            return _parse_geocode_payload(data)
        output = value.get("output")
        if isinstance(output, str) and output:
            try:
                return _parse_geocode_payload(json.loads(output))
            except json.JSONDecodeError:
                return value
        return value
    return value


def _first_geocode_item(items: Any) -> dict[str, Any]:
    if not isinstance(items, list):
        return {}
    for item in items:
        output = _geocode_item_from_dict(item)
        if output:
            return output
    return {}


def _geocode_item_from_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keys = ("province", "city", "district", "township", "formatted_address", "location", "level", "street", "number")
    if any(value.get(key) for key in keys):
        return {key: value.get(key) for key in keys if value.get(key) not in (None, "")}
    return {}
