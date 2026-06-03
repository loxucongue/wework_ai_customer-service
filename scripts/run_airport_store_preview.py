# -*- coding: utf-8 -*-
"""Run the airport-nearby store scenario and append it to local preview conversations."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests


API_URL = "http://127.0.0.1:8000/chat"
OUT_PATH = Path("projects/public/test-conversations.json")
REPORT_PATH = Path("logs/store_scenarios/airport_store_preview_report.json")


TURNS = [
    "我想去机场附近",
    "我在厦门",
    "可以，把这家店发给我",
    "停车信息也发我一下",
]


def message_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index}_{int(time.time() * 1000)}"


def conversation_history(messages: list[dict[str, Any]]) -> list[str]:
    history: list[str] = []
    for message in messages[-10:]:
        role = "用户" if message["role"] == "user" else "小贝"
        text = message.get("content") or ""
        history.append(f"{role}: {text}")
    return history


def load_existing_conversations() -> list[dict[str, Any]]:
    if not OUT_PATH.exists():
        return []
    try:
        payload = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    conversations = payload.get("conversations")
    return conversations if isinstance(conversations, list) else []


def main() -> None:
    stamp = time.strftime("%Y%m%d%H%M%S")
    conversation_id = f"codex_test_airport_store_{stamp}"
    title = f"Codex测试-机场附近门店-{stamp[-4:]}"
    messages: list[dict[str, Any]] = []
    turn_report: list[dict[str, Any]] = []

    for turn_index, content in enumerate(TURNS, start=1):
        user_message = {
            "id": message_id(f"{conversation_id}_u", turn_index),
            "role": "user",
            "content": content,
            "contentType": "text",
            "timestamp": int(time.time() * 1000),
        }
        payload: dict[str, Any] = {
            "content": content,
            "customer_id": conversation_id,
            "corp_id": "ww916da62a08044243",
            "conversation_history": conversation_history(messages),
            "user_id": 7294,
            "wechat": "yzm-yibingwen",
            "external_userid": f"codex_airport_store_{stamp}",
        }

        started = time.time()
        data: dict[str, Any] | None = None
        error_text = ""
        for attempt in range(1, 3):
            try:
                response = requests.post(API_URL, json=payload, timeout=180)
                response.raise_for_status()
                data = response.json()
                error_text = ""
                break
            except Exception as exc:
                error_text = f"attempt {attempt}: {type(exc).__name__}: {exc}"
                if attempt == 1:
                    time.sleep(2)
        elapsed_ms = int((time.time() - started) * 1000)
        if data is None:
            data = {
                "request_id": "",
                "intent": "",
                "subflow": "",
                "meta": {},
                "reply_messages": [
                    {
                        "type": "text",
                        "order": 1,
                        "content": f"测试调用失败：{error_text}",
                    }
                ],
            }
        meta = data.get("meta") or {}
        replies = data.get("reply_messages") or data.get("output") or []

        messages.append(user_message)
        reply_texts: list[str] = []
        for reply_index, item in enumerate(replies, start=1):
            content_text = str(item.get("content") or "")
            reply_texts.append(content_text)
            assistant_message: dict[str, Any] = {
                "id": message_id(f"{conversation_id}_a_{turn_index}", reply_index),
                "role": "assistant",
                "content": content_text,
                "contentType": item.get("type", "text"),
                "timestamp": int(time.time() * 1000),
                "duration": elapsed_ms,
            }
            if reply_index == 1:
                assistant_message["meta"] = {
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
                    "memoryLoaded": meta.get("memory_loaded"),
                    "raw": meta,
                }
            messages.append(assistant_message)

        turn_report.append(
            {
                "turn": turn_index,
                "user": content,
                "replies": reply_texts,
                "elapsed_ms": elapsed_ms,
                "request_id": data.get("request_id", ""),
                "intent": data.get("intent", ""),
                "subflow": data.get("subflow", ""),
                "tool_result_keys": meta.get("tool_result_keys", []),
                "token_usage": meta.get("token_usage", {}),
                "error": error_text,
            }
        )

    conversation = {
        "id": conversation_id,
        "title": title,
        "messages": messages,
        "createdAt": int(time.time() * 1000),
        "updatedAt": int(time.time() * 1000),
    }

    conversations = load_existing_conversations()
    conversations = [item for item in conversations if item.get("id") != conversation_id]
    conversations.insert(0, conversation)
    OUT_PATH.write_text(
        json.dumps({"conversations": conversations}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "conversation_id": conversation_id,
                "title": title,
                "turns": turn_report,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "conversation_id": conversation_id,
                "title": title,
                "turns": len(turn_report),
                "preview_file": str(OUT_PATH),
                "report_file": str(REPORT_PATH),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
