from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.policies.sales_flow import configure_precision_qa_playbook_path, load_precision_qa_playbook
from app.services.precision_qa_playbook_service import PrecisionQaPlaybookService


def test_precision_qa_service_saves_utf8_and_refreshes_runtime_cache(tmp_path: Path) -> None:
    configured_path = tmp_path / "precision_qa_playbook.json"
    service = PrecisionQaPlaybookService(SimpleNamespace(precision_qa_playbook_path=configured_path))
    try:
        payload = service.load()
        assert payload["storage"]["source"] == "bundled_default"
        payload["purpose"] = "中文精准回复配置保存验证"
        payload["future_extension"] = {"kept": True}

        saved = service.save(payload)

        assert saved["storage"]["source"] == "configured"
        raw_text = configured_path.read_text(encoding="utf-8")
        assert "中文精准回复配置保存验证" in raw_text
        assert "\\u4e2d" not in raw_text
        stored = json.loads(raw_text)
        assert "audit" not in stored
        assert "storage" not in stored
        assert stored["future_extension"] == {"kept": True}
        assert load_precision_qa_playbook()["purpose"] == "中文精准回复配置保存验证"
    finally:
        configure_precision_qa_playbook_path(None)


def test_precision_qa_service_rejects_duplicate_question_ids(tmp_path: Path) -> None:
    service = PrecisionQaPlaybookService(
        SimpleNamespace(precision_qa_playbook_path=tmp_path / "precision_qa_playbook.json")
    )
    try:
        payload = service.load()
        payload["questions"].append(dict(payload["questions"][0]))

        with pytest.raises(ValueError, match="duplicated precision QA id"):
            service.save(payload)
    finally:
        configure_precision_qa_playbook_path(None)


def test_precision_qa_runtime_cache_refreshes_when_config_file_changes(tmp_path: Path) -> None:
    configured_path = tmp_path / "precision_qa_playbook.json"
    service = PrecisionQaPlaybookService(SimpleNamespace(precision_qa_playbook_path=configured_path))
    try:
        payload = service.load()
        payload["purpose"] = "第一版"
        service.save(payload)
        assert load_precision_qa_playbook()["purpose"] == "第一版"

        stored = json.loads(configured_path.read_text(encoding="utf-8"))
        stored["purpose"] = "第二版"
        configured_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        assert load_precision_qa_playbook()["purpose"] == "第二版"
    finally:
        configure_precision_qa_playbook_path(None)


def test_precision_qa_service_rejects_incomplete_question(tmp_path: Path) -> None:
    service = PrecisionQaPlaybookService(
        SimpleNamespace(precision_qa_playbook_path=tmp_path / "precision_qa_playbook.json")
    )
    try:
        payload = service.load()
        payload["questions"][0]["must_answer"] = []

        with pytest.raises(ValueError, match="必须回答至少需要一条"):
            service.save(payload)
    finally:
        configure_precision_qa_playbook_path(None)
