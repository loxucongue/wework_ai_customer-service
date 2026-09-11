import asyncio

from scripts.snapshot_follow_knowledge_materials import _catalog_media, _public_url


def test_catalog_snapshot_extracts_only_supported_media():
    rows = _catalog_media(
        [
            {
                "source_ref": "follow_script:synthetic",
                "checkpoint_code": "cp1",
                "media_messages": [
                    {"type": "text", "content": "ignored"},
                    {"type": "image", "url": "https://example.invalid/signed?a=secret", "file_id": 0},
                    {"type": "video", "url": "https://example.invalid/video", "file_id": 7},
                ],
            }
        ]
    )
    assert [row["type"] for row in rows] == ["image", "video"]
    assert {row["role"] for row in rows} == {"cp1"}


def test_catalog_snapshot_rejects_private_and_credentialed_urls():
    assert not asyncio.run(_public_url("http://127.0.0.1/media"))
    assert not asyncio.run(_public_url("http://user:password@example.com/media"))
    assert not asyncio.run(_public_url("file:///tmp/media"))
