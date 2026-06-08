from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
REPORT_FILE = ROOT / "logs" / "focus_regression_report.json"


CASES = [
    ("广告开场", "我是看到你们祛斑活动广告加你的，想了解祛斑、价格、效果和到店安排这些。"),
    ("一次费用", "是一次的费用吗"),
    ("做几次", "要做多少次"),
    ("乱收费顾虑", "到店会乱收费吗"),
    ("做后没效果", "已做了2次，不见效果呢"),
]


def main() -> None:
    sys.path.insert(0, str(ROOT / "ai_paths"))
    from app.main import app

    client = TestClient(app)
    conversation_history: list[str] = []
    customer_id = f"codex_focus_{int(time.time() * 1000)}"
    rows = []
    for label, content in CASES:
        started = time.perf_counter()
        response = client.post(
            "/chat",
            json={
                "content": content,
                "customer_id": customer_id,
                "corp_id": "ww916da62a08044243",
                "user_id": 7294,
                "wechat": "yzm-yibingwen",
                "external_userid": customer_id,
                "conversation_history": conversation_history[-10:],
                "request_context": {"conversation_id": customer_id},
            },
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        data = response.json()
        replies: list[str] = []
        for item in data.get("reply_messages") or []:
            if not isinstance(item, dict):
                continue
            content_obj = item.get("content")
            if isinstance(content_obj, dict):
                text = str(content_obj.get("text") or content_obj.get("handoff_reason") or "")
            else:
                text = str(content_obj or "")
            if text:
                replies.append(text)
        rows.append(
            {
                "label": label,
                "user": content,
                "status": response.status_code,
                "elapsed_ms": elapsed_ms,
                "intent": data.get("intent") or "",
                "subflow": data.get("subflow") or "",
                "reply": replies,
            }
        )
        conversation_history.append(f"用户: {content}")
        for text in replies:
            conversation_history.append(f"助手: {text}")

    REPORT_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
