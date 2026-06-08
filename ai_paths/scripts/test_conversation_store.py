from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PUBLIC_FILE = ROOT / "projects" / "public" / "test-conversations.json"


def load_test_conversation_payload(path: Path = DEFAULT_PUBLIC_FILE) -> dict[str, Any]:
    if not path.exists():
        return {"generatedAt": int(time.time() * 1000), "conversations": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = path.with_suffix(path.suffix + f".broken.{int(time.time())}.bak")
        path.replace(backup)
        return {"generatedAt": int(time.time() * 1000), "conversations": []}
    if not isinstance(payload, dict):
        return {"generatedAt": int(time.time() * 1000), "conversations": []}
    if not isinstance(payload.get("conversations"), list):
        payload["conversations"] = []
    return payload


def append_test_conversations(
    conversations: list[dict[str, Any]],
    *,
    path: Path = DEFAULT_PUBLIC_FILE,
) -> dict[str, Any]:
    """Append or replace test conversations by id without clearing older records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = load_test_conversation_payload(path)
    existing = [item for item in payload.get("conversations", []) if isinstance(item, dict)]
    incoming_ids = {item.get("id") for item in conversations if item.get("id")}
    kept = [item for item in existing if item.get("id") not in incoming_ids]
    payload["conversations"] = conversations + kept
    payload["generatedAt"] = int(time.time() * 1000)

    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
    return payload
