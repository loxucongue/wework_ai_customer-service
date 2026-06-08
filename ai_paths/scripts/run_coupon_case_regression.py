from __future__ import annotations

import base64
import json
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

try:
    from .test_conversation_store import append_test_conversations
except ImportError:
    from test_conversation_store import append_test_conversations


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_FILE = ROOT / "projects" / "public" / "test-conversations.json"
REPORT_FILE = ROOT / "logs" / "coupon_case_regression_report.json"


def _make_coupon_data_url() -> str:
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((80, 140, 820, 500), radius=24, fill=(206, 238, 240), outline=(90, 150, 150), width=4)
    draw.rounded_rectangle((120, 620, 780, 980), radius=24, fill=(241, 231, 168), outline=(150, 130, 70), width=4)
    draw.text((120, 180), "BEIFACE", fill="black")
    draw.text((270, 300), "GAME", fill="black")
    draw.text((300, 385), "COUPON", fill="black")
    draw.text((160, 660), "BEIFACE", fill="black")
    draw.text((295, 790), "SNACK", fill="black")
    draw.text((285, 875), "COUPON", fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or content.get("handoff_reason") or "")
    return str(content or "")


def run() -> list[dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "ai_paths"))
    from app.main import app

    client = TestClient(app)
    conversation_id = f"codex_coupon_case_{int(time.time() * 1000)}"
    coupon_data_url = _make_coupon_data_url()
    turns = [
        {"content": "我想了解一下祛斑"},
        {"content": "我看广告上面说199元就有效果，是不是没有其他收费"},
        {"content": "有这个券吗"},
        {"content": "这个是吗", "file_image": coupon_data_url},
        {"content": "客户做完之后的效果我想看一下"},
        {"content": "我主要还是想看祛斑做完后的变化"},
    ]

    history: list[str] = []
    messages: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    base_time = int(time.time() * 1000)

    for turn_index, turn in enumerate(turns, start=1):
        content = turn["content"]
        messages.append(
            {
                "id": f"user-{turn_index}",
                "role": "user",
                "content": content,
                "timestamp": base_time + turn_index * 60_000,
                "contentType": "image" if turn.get("file_image") else "text",
                "imageUrl": turn.get("file_image"),
            }
        )
        started = time.perf_counter()
        response = client.post(
            "/chat",
            json={
                "content": content,
                "file_image": turn.get("file_image"),
                "customer_id": conversation_id,
                "corp_id": "ww916da62a08044243",
                "user_id": 7294,
                "wechat": "yzm-yibingwen",
                "external_userid": conversation_id,
                "conversation_history": history[-10:],
                "request_context": {"conversation_id": conversation_id},
            },
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        result = response.json()
        replies = result.get("reply_messages") if isinstance(result.get("reply_messages"), list) else []
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        tool_calls = meta.get("tool_calls") if isinstance(meta.get("tool_calls"), list) else []
        reply_texts: list[str] = []
        for msg_index, item in enumerate(replies, start=1):
            if not isinstance(item, dict):
                continue
            text = _message_text(item)
            if not text:
                continue
            reply_texts.append(text)
            message: dict[str, Any] = {
                "id": f"assistant-{turn_index}-{msg_index}",
                "role": "assistant",
                "content": text,
                "timestamp": base_time + turn_index * 60_000 + msg_index * 1000,
                "duration": elapsed_ms,
                "contentType": item.get("type") or "text",
            }
            if msg_index == 1:
                message["meta"] = {
                    "intent": result.get("intent") or "",
                    "scene": result.get("scene") or "",
                    "subflow": result.get("subflow") or "",
                    "requestId": result.get("request_id") or "",
                    "traceUrl": result.get("trace_url") or "",
                    "toolResultKeys": meta.get("tool_result_keys") if isinstance(meta.get("tool_result_keys"), list) else [],
                    "toolCalls": tool_calls,
                    "imageInfo": meta.get("image_info"),
                    "raw": {
                        "status_code": response.status_code,
                        "token_usage": meta.get("token_usage"),
                        "model_usage": meta.get("model_usage"),
                    },
                }
            messages.append(message)

        history.append(f"用户: {content}")
        for reply in reply_texts:
            history.append(f"助手: {reply}")

        report.append(
            {
                "turn": turn_index,
                "status": response.status_code,
                "elapsed_ms": elapsed_ms,
                "request_id": result.get("request_id") or "",
                "intent": result.get("intent") or "",
                "subflow": result.get("subflow") or "",
                "user": content,
                "has_image": bool(turn.get("file_image")),
                "reply": reply_texts,
            }
        )

    append_test_conversations(
        [
            {
                "id": conversation_id,
                "title": "定点回归-祛斑广告价券图效果",
                "messages": messages,
                "createdAt": base_time,
                "updatedAt": base_time + len(turns) * 60_000,
            }
        ],
        path=FRONTEND_FILE,
    )
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
