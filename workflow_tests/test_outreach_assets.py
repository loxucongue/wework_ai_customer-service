from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.outreach_assets import (
    asset_reply_message,
    build_outreach_asset_catalog,
    recent_outreach_media,
    resolve_case_asset,
    resolve_configured_asset,
)


def test_enabled_sop_media_builds_stable_asset_catalog() -> None:
    catalog = build_outreach_asset_catalog(
        {
            "packs": [
                {
                    "id": "effect_pack",
                    "enabled": True,
                    "name": "效果参考",
                    "purpose": "增强效果信任",
                    "sop_category": "effect_case",
                    "reply_messages": [
                        {"type": "text", "order": 1, "content": {"text": "参考"}},
                        {"type": "image", "order": 2, "content": {"url": "https://cdn.example/case.jpg"}},
                    ],
                },
                {
                    "id": "disabled_pack",
                    "enabled": False,
                    "reply_messages": [
                        {"type": "video", "order": 1, "content": {"url": "https://cdn.example/old.mp4"}}
                    ],
                },
            ]
        }
    )

    assert catalog == [
        {
            "asset_id": "effect_pack:2",
            "type": "image",
            "url": "https://cdn.example/case.jpg",
            "source": "sop_config",
            "source_pack_id": "effect_pack",
            "source_pack_name": "效果参考",
            "sop_category": "effect_case",
            "purpose": "增强效果信任",
        }
    ]


def test_recent_media_is_deduplicated_and_blocks_reuse() -> None:
    messages = [
        {
            "created_at": "2026-07-28T08:00:00+00:00",
            "reply_messages": [
                {
                    "type": "image",
                    "content": {"url": "https://cdn.example/case.jpg"},
                    "document_id": "case-1",
                }
            ],
        }
    ]
    delivered = recent_outreach_media(
        messages,
        now=datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc),
    )
    catalog = [
        {
            "asset_id": "effect_pack:2",
            "type": "image",
            "url": "https://cdn.example/case.jpg",
        }
    ]

    assert delivered == {"urls": ["https://cdn.example/case.jpg"], "document_ids": ["case-1"]}
    assert resolve_configured_asset(
        catalog,
        "effect_pack:2",
        sent_urls=set(delivered["urls"]),
        expected_type="image",
    ) == {}


def test_case_result_uses_only_real_image_and_document_id() -> None:
    result = SimpleNamespace(
        items=[
            SimpleNamespace(
                content='description: 同类改善参考 <img src="https://cdn.example/kb-case.jpg">',
                document_id="doc-9",
            )
        ]
    )

    asset = resolve_case_asset(result)

    assert asset["source"] == "case_studies"
    assert asset["document_id"] == "doc-9"
    assert asset["url"] == "https://cdn.example/kb-case.jpg"
    assert asset_reply_message(asset, order=2) == {
        "type": "image",
        "order": 2,
        "content": {"url": "https://cdn.example/kb-case.jpg"},
    }


def test_unknown_or_invalid_asset_is_rejected() -> None:
    catalog = [{"asset_id": "video:1", "type": "video", "url": "javascript:alert(1)"}]

    assert resolve_configured_asset(catalog, "missing:1") == {}
    assert resolve_configured_asset(catalog, "video:1", expected_type="video") == {}
