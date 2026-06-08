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
REPORT_FILE = ROOT / "logs" / "project_direction_followup_report.json"


def _message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or content.get("handoff_reason") or "")
    return str(content or "")


def main() -> list[dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "ai_paths"))
    from app.main import app

    client = TestClient(app)
    customer_id = f"codex_project_direction_{int(time.time() * 1000)}"
    turns = [
        "主要是点状斑，还有点色沉",
        "别一直问我了，你先说我这种先看什么方向",
        "客户做完之后的效果我想看一下",
    ]

    history: list[str] = []
    messages: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    base = int(time.time() * 1000)

    for idx, text in enumerate(turns, start=1):
        messages.append({"id": f"user-{idx}", "role": "user", "content": text, "timestamp": base + idx * 60_000})
        start = time.perf_counter()
        response = client.post(
            "/chat",
            json={
                "content": text,
                "customer_id": customer_id,
                "corp_id": "ww916da62a08044243",
                "user_id": 7294,
                "wechat": "yzm-yibingwen",
                "external_userid": customer_id,
                "conversation_history": history[-10:],
                "request_context": {"conversation_id": customer_id},
            },
        )
        elapsed = int((time.perf_counter() - start) * 1000)
        data = response.json()
        replies = data.get("reply_messages") if isinstance(data.get("reply_messages"), list) else []
        reply_texts: list[str] = []

        history.append(f"用户: {text}")
        for midx, item in enumerate(replies, start=1):
            if not isinstance(item, dict):
                continue
            msg = _message_text(item)
            if not msg:
                continue
            reply_texts.append(msg)
            messages.append(
                {
                    "id": f"assistant-{idx}-{midx}",
                    "role": "assistant",
                    "content": msg,
                    "timestamp": base + idx * 60_000 + midx * 1000,
                    "duration": elapsed,
                    "contentType": item.get("type") or "text",
                    "meta": {
                        "intent": data.get("intent") or "",
                        "scene": data.get("scene") or "",
                        "subflow": data.get("subflow") or "",
                        "requestId": data.get("request_id") or "",
                        "traceUrl": data.get("trace_url") or "",
                        "raw": {"status_code": response.status_code},
                    }
                    if midx == 1
                    else None,
                }
            )
            history.append(f"助手: {msg}")

        report.append(
            {
                "turn": idx,
                "status": response.status_code,
                "intent": data.get("intent") or "",
                "subflow": data.get("subflow") or "",
                "request_id": data.get("request_id") or "",
                "user": text,
                "reply": reply_texts,
                "elapsed_ms": elapsed,
            }
        )

    append_test_conversations(
        [
            {
                "id": customer_id,
                "title": "话术优化定点-先说方向",
                "messages": messages,
                "createdAt": base,
                "updatedAt": base + len(turns) * 60_000,
            }
        ],
        path=FRONTEND_FILE,
    )
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    main()
