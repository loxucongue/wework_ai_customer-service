from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_application_starts_and_stops_without_production_credentials(tmp_path: Path) -> None:
    probe = """
import json
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    response = client.get('/health')
    response.raise_for_status()
    print(json.dumps(response.json()))
"""
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT / "ai_paths"),
            "AI_PATHS_BACKGROUND_WORKERS_ENABLED": "false",
            "SOP_PLATFORM_PULL_ENABLED": "false",
            "SOP_QUIET_BACKLOG_FUSION_ENABLED": "false",
            "OUTREACH_FIRST_DAY_SILENCE_ENABLED": "false",
            "STORE_SNAPSHOT_REFRESH_ENABLED": "false",
            "AICS_DB_PATH": str(tmp_path / "isolated.sqlite3"),
            "V3_EVALUATION_DIR": str(tmp_path / "evaluations"),
            "AI_PATHS_API_KEY": "",
            "AI_EXTERNAL_API_KEY": "",
            "SOP_PLATFORM_TOKEN": "",
            "DEEPSEEK_API_KEY": "",
            "MODEL_RELAY_API_KEY": "",
            "ALIYUN_DASHSCOPE_API_KEY": "",
            "VOLCENGINE_ARK_API_KEY": "",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"
    assert payload["background_workers_enabled"] is False
