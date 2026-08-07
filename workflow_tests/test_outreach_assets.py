from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.outreach_assets import (
    appointment_blocker_materials,
    asset_reply_message,
    build_appointment_blocker_asset_catalog,
    build_appointment_blocker_scene_index,
    recent_outreach_media,
    resolve_case_asset,
    resolve_configured_asset,
)


def test_appointment_blocker_media_builds_model_annotated_asset_catalog() -> None:
    playbook = {
        "items": [
            {
                "blocker_type": "距离远",
                "applicable_scene": "客户所在城市有门店但距离较远",
                "content_id": "YYHF-0001",
                "reply_messages": [
                    {"type": "text", "content": "参考表达"},
                    {"type": "image", "content": "https://cdn.example/case.jpg"},
                    {
                        "type": "image",
                        "content": "missing://source-image",
                        "source_missing": True,
                    },
                ],
            }
        ]
    }
    catalog = build_appointment_blocker_asset_catalog(playbook)

    assert catalog == [
        {
            "asset_id": "appointment-blocker:YYHF-0001:2",
            "type": "image",
            "url": "https://cdn.example/case.jpg",
            "source": "appointment_blocker_playbook",
            "name": "YYHF-0001",
            "annotation": "客户所在城市有门店但距离较远",
            "use_cases": ["客户所在城市有门店但距离较远"],
            "avoid_when": ["近期已经发送相同素材"],
            "tags": ["距离远", "YYHF-0001"],
            "content_id": "YYHF-0001",
        }
    ]
    scene_index = build_appointment_blocker_scene_index(playbook)
    assert len(scene_index) == 1
    assert scene_index[0]["source_ids"] == ["appointment-blocker:YYHF-0001"]
    assert scene_index[0]["asset_ids"] == ["appointment-blocker:YYHF-0001:2"]
    assert appointment_blocker_materials(playbook) == [
        {
            "source_id": "appointment-blocker:YYHF-0001",
            "content_id": "YYHF-0001",
            "blocker_type": "距离远",
            "applicable_scene": "客户所在城市有门店但距离较远",
            "reply_messages": [
                {"type": "text", "order": 1, "content": "参考表达"},
                {"type": "image", "order": 2, "asset_id": "appointment-blocker:YYHF-0001:2"},
            ],
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
