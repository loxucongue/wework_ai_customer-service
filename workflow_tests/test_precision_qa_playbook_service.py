from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.policies.sales_flow import configure_precision_qa_playbook_path, load_precision_qa_playbook
from app.services.precision_qa_playbook_service import PrecisionQaPlaybookService


def test_appointment_blocker_service_saves_utf8_and_refreshes_runtime_cache(tmp_path: Path) -> None:
    configured_path = tmp_path / "precision_qa_playbook.json"
    service = PrecisionQaPlaybookService(SimpleNamespace(precision_qa_playbook_path=configured_path))
    try:
        payload = service.load()
        assert payload["storage"]["source"] == "bundled_default"
        payload["items"][0]["applicable_scene"] = "中文预约卡点场景"
        payload["future_extension"] = {"kept": True}

        saved = service.save(payload)

        assert saved["storage"]["source"] == "configured"
        raw_text = configured_path.read_text(encoding="utf-8")
        assert "中文预约卡点场景" in raw_text
        assert "\\u4e2d" not in raw_text
        stored = json.loads(raw_text)
        assert "audit" not in stored
        assert "storage" not in stored
        assert stored["future_extension"] == {"kept": True}
        assert load_precision_qa_playbook()["items"][0]["applicable_scene"] == "中文预约卡点场景"
    finally:
        configure_precision_qa_playbook_path(None)


def test_appointment_blocker_service_rejects_duplicate_content_ids(tmp_path: Path) -> None:
    service = PrecisionQaPlaybookService(SimpleNamespace(precision_qa_playbook_path=tmp_path / "precision_qa_playbook.json"))
    try:
        payload = service.load()
        payload["items"].append(dict(payload["items"][0]))
        with pytest.raises(ValueError, match="duplicated content id"):
            service.save(payload)
    finally:
        configure_precision_qa_playbook_path(None)


def test_runtime_cache_refreshes_when_config_file_changes(tmp_path: Path) -> None:
    configured_path = tmp_path / "precision_qa_playbook.json"
    service = PrecisionQaPlaybookService(SimpleNamespace(precision_qa_playbook_path=configured_path))
    try:
        payload = service.load()
        payload["items"][0]["blocker_type"] = "第一版"
        service.save(payload)
        assert load_precision_qa_playbook()["items"][0]["blocker_type"] == "第一版"

        stored = json.loads(configured_path.read_text(encoding="utf-8"))
        stored["items"][0]["blocker_type"] = "第二版"
        configured_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        assert load_precision_qa_playbook()["items"][0]["blocker_type"] == "第二版"
    finally:
        configure_precision_qa_playbook_path(None)


def test_service_rejects_empty_messages(tmp_path: Path) -> None:
    service = PrecisionQaPlaybookService(SimpleNamespace(precision_qa_playbook_path=tmp_path / "precision_qa_playbook.json"))
    try:
        payload = service.load()
        payload["items"][0]["reply_messages"] = []
        with pytest.raises(ValueError, match="non-empty list"):
            service.save(payload)
    finally:
        configure_precision_qa_playbook_path(None)


def test_bundled_appointment_blocker_dataset_contract() -> None:
    service = PrecisionQaPlaybookService(SimpleNamespace(precision_qa_playbook_path=Path("missing-config.json")))
    payload = service.load()
    messages = [message for item in payload["items"] for message in item["reply_messages"]]
    assert len(payload["items"]) == 104
    assert len({item["content_id"] for item in payload["items"]}) == 104
    assert sum(message["type"] == "image" for message in messages) == 64
    assert sum(bool(message.get("source_missing")) for message in messages) == 15
    assert payload["audit"]["warning_count"] == 15
