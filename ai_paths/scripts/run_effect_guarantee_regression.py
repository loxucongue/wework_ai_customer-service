from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

try:
    from .test_conversation_store import append_test_conversations
except ImportError:
    from test_conversation_store import append_test_conversations


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_FILE = ROOT / "projects" / "public" / "test-conversations.json"
REPORT_FILE = ROOT / "logs" / "effect_guarantee_regression_report.json"

TURNS = [
    "我脸上主要是点状斑，想先了解淡斑",
    "效果能保证吗？",
    "做了会不会反弹？",
    "如果我报名后到店不满意，10元能退吗？",
]


def _text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or content.get("handoff_reason") or "")
    return str(content or "")


def _assistant_messages(result: dict[str, Any]) -> list[str]:
    messages = result.get("reply_messages")
    if not isinstance(messages, list):
        return []
    return [_text(item) for item in messages if isinstance(item, dict) and _text(item)]


def run() -> list[dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "ai_paths"))
    from app.main import app

    client = TestClient(app)
    customer_id = f"codex_effect_guarantee_{int(time.time())}"
    history: list[str] = []
    messages: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    now = int(time.time() * 1000)

    for index, content in enumerate(TURNS, start=1):
        messages.append(
            {
                "id": f"user-{index}",
                "role": "user",
                "content": content,
                "timestamp": now + index * 60_000,
            }
        )
        start = time.perf_counter()
        response = client.post(
            "/chat",
            json={
                "content": content,
                "customer_id": customer_id,
                "corp_id": "ww916da62a08044243",
                "user_id": 7294,
                "wechat": "yzm-yibingwen",
                "external_userid": customer_id,
                "conversation_history": history[-10:],
                "request_context": {"conversation_id": customer_id},
            },
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        try:
            result = response.json()
        except ValueError:
            result = {"error": response.text}
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        replies = _assistant_messages(result)

        for msg_index, reply in enumerate(replies, start=1):
            entry: dict[str, Any] = {
                "id": f"assistant-{index}-{msg_index}",
                "role": "assistant",
                "content": reply,
                "timestamp": now + index * 60_000 + msg_index * 1000,
                "duration": elapsed_ms,
            }
            if msg_index == 1:
                entry["meta"] = {
                    "intent": result.get("intent") or "",
                    "scene": result.get("scene") or "",
                    "subflow": result.get("subflow") or "",
                    "requestId": result.get("request_id") or "",
                    "traceUrl": result.get("trace_url") or "",
                    "toolResultKeys": meta.get("tool_result_keys") if isinstance(meta.get("tool_result_keys"), list) else [],
                    "toolCalls": meta.get("tool_calls") if isinstance(meta.get("tool_calls"), list) else [],
                    "raw": {
                        "token_usage": meta.get("token_usage"),
                        "model_usage": meta.get("model_usage"),
                    },
                }
            messages.append(entry)

        history.append(f"用户: {content}")
        for reply in replies:
            history.append(f"助手: {reply}")

        report.append(
            {
                "turn": index,
                "user": content,
                "status": response.status_code,
                "elapsed_ms": elapsed_ms,
                "request_id": result.get("request_id"),
                "intent": result.get("intent"),
                "subflow": result.get("subflow"),
                "tool_result_keys": meta.get("tool_result_keys") if isinstance(meta.get("tool_result_keys"), list) else [],
                "reply": replies,
                "error": result.get("detail") or result.get("error") or "",
            }
        )

    append_test_conversations(
        [
            {
                "id": customer_id,
                "title": "定点回归-效果保障口径",
                "messages": messages,
                "createdAt": now,
                "updatedAt": now + len(TURNS) * 60_000,
            }
        ],
        path=FRONTEND_FILE,
    )
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
