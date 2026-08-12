from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


_REVIEW_STATUSES = {"pending_review", "approved", "rejected"}
_ASSET_TYPES = {"image", "video", "image_reference", "video_reference", "media_reference"}


class ModelLedObjectionPlaybookService:
    """Load V2-only distilled sales guidance without exposing source replies."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "sales_principles": [], "evidence_strategies": [], "assets": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid V2 objection playbook: {exc}") from exc
        return self._normalize(payload)

    def sales_principles(self) -> list[dict[str, Any]]:
        return [
            {"id": item["id"], "reasoning": item["reasoning"]}
            for item in self.load()["sales_principles"]
        ]

    def gate_assets(self) -> list[dict[str, Any]]:
        payload = self.load()
        strategies = [
            {
                "id": item["id"],
                "content_type": "evidence_strategy",
                "name": item["name"],
                "purpose": item["customer_uncertainty"],
                "asset_role": "evidence_strategy",
                "customer_uncertainty": item["customer_uncertainty"],
                "useful_evidence": deepcopy(item["useful_evidence"]),
                "reasoning_moves": deepcopy(item["reasoning_moves"]),
                "expression_moves": deepcopy(item["expression_moves"]),
                "tone_reference": item["tone_reference"],
                "fact_authority_note": item["fact_authority_note"],
                "anti_patterns": deepcopy(item["anti_patterns"]),
                "requires_prior_asset_roles": [],
                "selection_constraints": {},
                "messages": [],
                "render_strategy": "guidance_only",
            }
            for item in payload["evidence_strategies"]
        ]
        approved_assets = []
        for item in payload["assets"]:
            if item["review_status"] != "approved" or not item["url"]:
                continue
            approved_assets.append(
                {
                    "id": item["asset_id"],
                    "content_type": "reviewed_media",
                    "name": item["asset_id"],
                    "purpose": item["evidence_purpose"],
                    "asset_role": "reviewed_evidence",
                    "requires_prior_asset_roles": [],
                    "selection_constraints": {},
                    "messages": [{"type": item["type"], "content": item["url"]}],
                    "render_strategy": "verbatim_required",
                }
            )
        return [*strategies, *approved_assets]

    def metadata_index(self) -> list[dict[str, Any]]:
        return [
            {
                "content_id": item["id"],
                "content_type": "evidence_strategy",
                "name": item["name"],
                "purpose": item["customer_uncertainty"],
                "asset_role": "evidence_strategy",
                "requires_prior_asset_roles": [],
                "category": "v2_distilled_guidance",
            }
            for item in self.load()["evidence_strategies"]
        ] + [
            {
                "content_id": item["asset_id"],
                "content_type": "reviewed_media",
                "name": item["asset_id"],
                "purpose": item["evidence_purpose"],
                "asset_role": "reviewed_evidence",
                "requires_prior_asset_roles": [],
                "category": "v2_reviewed_media",
            }
            for item in self.load()["assets"]
            if item["review_status"] == "approved" and item["url"]
        ]

    @staticmethod
    def _normalize(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("V2 objection playbook must be an object")
        principles = _unique_objects(payload.get("sales_principles"), "id", _normalize_principle)
        strategies = _unique_objects(payload.get("evidence_strategies"), "id", _normalize_strategy)
        assets = _unique_objects(payload.get("assets"), "asset_id", _normalize_asset)
        return {
            "version": max(1, _positive_int(payload.get("version"), 1)),
            "source": deepcopy(payload.get("source") or {}),
            "sales_principles": principles,
            "evidence_strategies": strategies,
            "assets": assets,
        }


def _unique_objects(value: Any, id_field: str, normalize) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{id_field} collection must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item = normalize(raw, index)
        identifier = item[id_field]
        if identifier in seen:
            raise ValueError(f"duplicate {id_field}: {identifier}")
        seen.add(identifier)
        result.append(item)
    return result


def _normalize_principle(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"sales principle #{index + 1} must be an object")
    identifier = _identifier(value.get("id"))
    reasoning = _text(value.get("reasoning"))
    if not identifier or not reasoning:
        raise ValueError(f"sales principle #{index + 1} requires id and reasoning")
    return {"id": identifier, "reasoning": reasoning, "source_ids": _source_ids(value.get("source_ids"))}


def _normalize_strategy(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"evidence strategy #{index + 1} must be an object")
    identifier = _identifier(value.get("id"))
    name = _text(value.get("name"))
    uncertainty = _text(value.get("customer_uncertainty"))
    if not identifier or not name or not uncertainty:
        raise ValueError(f"evidence strategy #{index + 1} requires id, name and customer_uncertainty")
    return {
        "id": identifier,
        "name": name,
        "customer_uncertainty": uncertainty,
        "useful_evidence": _strings(value.get("useful_evidence")),
        "reasoning_moves": _strings(value.get("reasoning_moves")),
        "expression_moves": _strings(value.get("expression_moves")),
        "tone_reference": _text(value.get("tone_reference")),
        "fact_authority_note": _text(value.get("fact_authority_note")),
        "anti_patterns": _strings(value.get("anti_patterns")),
        "source_ids": _source_ids(value.get("source_ids")),
    }


def _normalize_asset(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"asset #{index + 1} must be an object")
    identifier = _identifier(value.get("asset_id"))
    asset_type = _text(value.get("type"))
    status = _text(value.get("review_status"))
    if not identifier or asset_type not in _ASSET_TYPES or status not in _REVIEW_STATUSES:
        raise ValueError(f"asset #{index + 1} has invalid identity, type or review_status")
    url = _text(value.get("url"))
    if status == "approved" and not url.startswith(("http://", "https://")):
        raise ValueError(f"approved asset {identifier} requires an http(s) URL")
    return {
        "asset_id": identifier,
        "type": asset_type,
        "url": url,
        "source_reference": _text(value.get("source_reference")),
        "evidence_purpose": _text(value.get("evidence_purpose")),
        "review_status": status,
        "source_ids": _source_ids(value.get("source_ids")),
    }


def _source_ids(value: Any) -> list[str]:
    result = _strings(value)
    invalid = [item for item in result if not item.startswith("YYHF-")]
    if invalid:
        raise ValueError(f"invalid source ids: {invalid[:3]}")
    return result


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def _identifier(value: Any) -> str:
    return "".join(char for char in _text(value) if char.isascii() and (char.isalnum() or char in {"_", "-"}))


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
