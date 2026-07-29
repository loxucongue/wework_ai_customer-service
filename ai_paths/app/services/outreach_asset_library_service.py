from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import Settings


_ALLOWED_TYPES = {"image", "video"}
_IDENTIFIER_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")


class OutreachAssetLibraryService:
    """Manage the static media library used only by personalized Outreach."""

    def __init__(self, settings: Settings) -> None:
        self.path = settings.outreach_asset_library_path
        self.default_path = Path(__file__).parents[3] / "config" / "outreach_assets.json"

    def load(self) -> dict[str, Any]:
        path = self.path if self.path.exists() else self.default_path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            normalized = self._normalize(payload)
            normalized["storage"] = {
                "source": "configured" if path == self.path else "bundled_default",
                "path": str(path),
            }
            return normalized
        except (OSError, json.JSONDecodeError, ValueError):
            normalized = self._normalize(
                {
                    "version": 1,
                    "purpose": "个性化主动唤醒独立素材库，仅供 Outreach 使用，不与 SOP 话术包共用。",
                    "assets": [],
                }
            )
            normalized["storage"] = {"source": "empty_default", "path": str(self.path)}
            return normalized

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize(payload)
        normalized["updated_at"] = datetime.now(UTC).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with tmp_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(normalized, file, ensure_ascii=False, indent=2)
            file.write("\n")
        tmp_path.replace(self.path)
        result = deepcopy(normalized)
        result["storage"] = {"source": "configured", "path": str(self.path)}
        return result

    def catalog(self) -> list[dict[str, Any]]:
        return [
            deepcopy(asset)
            for asset in self.load().get("assets", [])
            if isinstance(asset, dict) and bool(asset.get("enabled"))
        ]

    def _normalize(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            raise ValueError("assets must be a list")

        assets: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        for index, raw_asset in enumerate(raw_assets, start=1):
            if not isinstance(raw_asset, dict):
                raise ValueError(f"asset #{index} must be an object")
            asset = self._normalize_asset(raw_asset, index)
            asset_id = asset["id"]
            url = asset["url"]
            if asset_id in seen_ids:
                raise ValueError(f"duplicated asset id: {asset_id}")
            if url in seen_urls:
                raise ValueError(f"duplicated asset url: {url}")
            seen_ids.add(asset_id)
            seen_urls.add(url)
            assets.append(asset)

        return {
            "version": _positive_int(payload.get("version"), 1),
            "purpose": _text(payload.get("purpose"))
            or "个性化主动唤醒独立素材库，仅供 Outreach 使用，不与 SOP 话术包共用。",
            "updated_at": _text(payload.get("updated_at")),
            "assets": assets,
        }

    def _normalize_asset(self, item: dict[str, Any], index: int) -> dict[str, Any]:
        asset_id = _identifier(item.get("id"))
        if not asset_id:
            raise ValueError(f"asset #{index} id is required")
        asset_type = _text(item.get("type")).lower()
        if asset_type not in _ALLOWED_TYPES:
            raise ValueError(f"asset {asset_id} type must be image or video")
        name = _text(item.get("name"))
        annotation = _text(item.get("annotation"))
        url = _text(item.get("url"))
        if not name:
            raise ValueError(f"asset {asset_id} name is required")
        if not annotation:
            raise ValueError(f"asset {asset_id} annotation is required")
        if not _is_http_url(url):
            raise ValueError(f"asset {asset_id} url must be http or https")
        if _text(item.get("storage")).lower() != "oss":
            raise ValueError(f"asset {asset_id} must be transferred to OSS before saving")

        return {
            "id": asset_id,
            "enabled": bool(item.get("enabled", True)),
            "type": asset_type,
            "name": name,
            "url": url,
            "annotation": annotation,
            "use_cases": _text_list(item.get("use_cases")),
            "avoid_when": _text_list(item.get("avoid_when")),
            "tags": _text_list(item.get("tags")),
            "storage": "oss",
            "source_url": _text(item.get("source_url")),
            "created_at": _text(item.get("created_at")) or datetime.now(UTC).isoformat(),
            "updated_at": _text(item.get("updated_at")) or datetime.now(UTC).isoformat(),
        }


def _identifier(value: Any) -> str:
    return _IDENTIFIER_PATTERN.sub("-", _text(value)).strip("-_")[:120]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item)
        if text and text not in result:
            result.append(text[:200])
    return result


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
