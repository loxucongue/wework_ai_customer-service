from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.outreach_asset_library_service import OutreachAssetLibraryService


def test_outreach_asset_library_round_trip(tmp_path) -> None:
    path = tmp_path / "outreach_assets.json"
    service = OutreachAssetLibraryService(SimpleNamespace(outreach_asset_library_path=path))

    saved = service.save(
        {
            "version": 1,
            "purpose": "主动唤醒独立素材",
            "assets": [
                {
                    "id": "case-reference",
                    "enabled": True,
                    "type": "image",
                    "name": "效果参考",
                    "url": "https://cdn.example/outreach/case.jpg",
                    "annotation": "用于效果顾虑后的真实参考，不用于已发过同类案例的客户。",
                    "use_cases": ["效果顾虑"],
                    "avoid_when": ["最近已发同类案例"],
                    "tags": ["案例"],
                    "storage": "oss",
                }
            ],
        }
    )

    assert saved["storage"]["source"] == "configured"
    assert saved["assets"][0]["annotation"].startswith("用于效果顾虑")
    assert service.catalog()[0]["id"] == "case-reference"
    assert json.loads(path.read_text(encoding="utf-8"))["assets"][0]["storage"] == "oss"


@pytest.mark.parametrize(
    ("patch", "error"),
    [
        ({"annotation": ""}, "annotation is required"),
        ({"storage": "local"}, "must be transferred to OSS"),
        ({"url": "javascript:alert(1)"}, "url must be http or https"),
        ({"type": "file"}, "type must be image or video"),
    ],
)
def test_outreach_asset_library_rejects_invalid_assets(tmp_path, patch, error) -> None:
    service = OutreachAssetLibraryService(
        SimpleNamespace(outreach_asset_library_path=tmp_path / "outreach_assets.json")
    )
    asset = {
        "id": "asset-1",
        "enabled": True,
        "type": "image",
        "name": "参考图",
        "url": "https://cdn.example/outreach/case.jpg",
        "annotation": "用于建立效果信任",
        "storage": "oss",
    }
    asset.update(patch)

    with pytest.raises(ValueError, match=error):
        service.save({"version": 1, "assets": [asset]})
