# -*- coding: utf-8 -*-
"""Run the appointment/payment dispute error case and publish it to local preview."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any


API_URL = os.getenv("AI_PATHS_CHAT_URL", "http://47.252.81.104/api/ai/chat")
OUT_PATH = Path("projects/public/test-conversations.json")
REPORT_PATH = Path("logs/appointment_dispute_error_case_report.json")

CUSTOMER_ID = f"codex_test_appointment_dispute_{int(time.time())}"
TITLE = "Codex测试-预约加价退款争议"

PRE_HISTORY = [
    "用户: 我和朋友今天下午5点想过去，你先帮我看看能不能接待",
    "小贝: 小贝先帮你确认门店和5点是否还有空位，确认好再跟你说。",
]

TURNS = [
    "不是说了5点吗",
    "约好吗？",
    "那我直接到店就可以是吧",
    "你们门店说要额外加钱 怎么说不一样",
    "你们把10块钱退给我",
]


def post_chat(content: str, history: list[str]) -> tuple[dict[str, Any], int]:
    payload = {
        "content": content,
        "customer_id": CUSTOMER_ID,
        "corp_id": "ww916da62a08044243",
        "user_id": 7294,
        "wechat": "yzm-yibingwen",
        "external_userid": "codex_appointment_dispute_external",
        "conversation_history": history[-10:],
        "confirmed_store_name": "厦门百星",
        "request_context": {
            "conversation_id": CUSTOMER_ID,
            "known_preferred_time": "今天下午5点",
            "known_people_count": "2",
            "scenario": "appointment_extra_charge_refund_dispute",
        },
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=240) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw), int((time.perf_counter() - start) * 1000)


def build_frontend_message(
    *,
    message_id: str,
    role: str,
    content: str,
    timestamp: int,
    duration: int | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "id": message_id,
        "role": role,
        "content": content,
        "contentType": "text",
        "timestamp": timestamp,
    }
    if duration is not None:
        message["duration"] = duration
    if data is not None:
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        message["meta"] = {
            "intent": data.get("intent", ""),
            "scene": data.get("scene", ""),
            "subflow": data.get("subflow", ""),
            "requestId": data.get("request_id", ""),
            "traceUrl": data.get("trace_url", ""),
            "toolResultKeys": meta.get("tool_result_keys", []),
            "toolCalls": meta.get("tool_calls", []),
            "profileUpdate": meta.get("profile_update"),
            "eventUpdates": meta.get("event_updates", []),
            "imageInfo": meta.get("image_info"),
            "raw": {
                "token_usage": meta.get("token_usage"),
                "model_usage": meta.get("model_usage"),
                "customer_context": meta.get("customer_context"),
            },
        }
    return message


def main() -> None:
    now = int(time.time() * 1000)
    messages: list[dict[str, Any]] = []
    history = list(PRE_HISTORY)
    report: list[dict[str, Any]] = []

    for index, content in enumerate(TURNS, start=1):
        turn_time = now + index * 60_000
        messages.append(
            build_frontend_message(
                message_id=f"{CUSTOMER_ID}_u_{index}",
                role="user",
                content=content,
                timestamp=turn_time,
            )
        )
        data, elapsed_ms = post_chat(content, history)
        replies = [item for item in data.get("reply_messages") or [] if isinstance(item, dict) and item.get("content")]
        reply_texts: list[str] = []
        for reply_index, item in enumerate(replies, start=1):
            text = str(item.get("content") or "")
            reply_texts.append(text)
            messages.append(
                build_frontend_message(
                    message_id=f"{CUSTOMER_ID}_a_{index}_{reply_index}",
                    role="assistant",
                    content=text,
                    timestamp=turn_time + reply_index * 1000,
                    duration=elapsed_ms,
                    data=data if reply_index == 1 else None,
                )
            )
        history.append(f"用户: {content}")
        for text in reply_texts:
            history.append(f"小贝: {text}")
        report.append(
            {
                "turn": index,
                "user": content,
                "reply": reply_texts,
                "elapsed_ms": elapsed_ms,
                "request_id": data.get("request_id"),
                "intent": data.get("intent"),
                "subflow": data.get("subflow"),
                "tool_result_keys": (data.get("meta") or {}).get("tool_result_keys", []),
                "token_usage": (data.get("meta") or {}).get("token_usage", {}),
            }
        )

    conversation = {
        "id": CUSTOMER_ID,
        "title": TITLE,
        "messages": messages,
        "createdAt": now,
        "updatedAt": now + len(TURNS) * 60_000,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({"conversations": [conversation]}, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps({"conversation_id": CUSTOMER_ID, "title": TITLE, "report": report}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"conversation_id": CUSTOMER_ID, "title": TITLE, "report": report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
