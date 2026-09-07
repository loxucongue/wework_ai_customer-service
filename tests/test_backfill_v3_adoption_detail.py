from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from scripts.backfill_v3_adoption_detail import _adoption_detail, _enabled


def test_adoption_detail_reads_only_explicit_run_observation() -> None:
    assert _adoption_detail({}) is None
    assert _adoption_detail("not-json") is None
    assert _adoption_detail({"observability_v3": {"knowledge_match": {"adopted": {}}}}) == (0, 0)
    assert _adoption_detail({
        "observability_v3": {
            "knowledge_match": {
                "adopted": {"sequence_id": "14", "script_ids": ["125"]},
            },
        },
    }) == (1, 1)


def test_enabled_defaults_to_secure_mysql_connection() -> None:
    assert _enabled(None, default=True) is True
    assert _enabled("false", default=True) is False
    assert _enabled("YES") is True
