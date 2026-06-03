# -*- coding: utf-8 -*-
"""生成本地前端可见的测试会话种子文件。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests


API_URL = "http://127.0.0.1:8000/chat"
OUT_PATH = Path("projects/public/test-conversations.json")
REPORT_PATH = Path("logs/local_preview_three_conversations_report.json")


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "codex_test_preview_store",
        "title": "Codex测试-本地预览01 门店+项目",
        "turns": [
            {"content": "你好，我想了解一下面部斑点，也想知道上海有没有门店"},
            {"content": "徐汇店地址发我一下，顺便说说这类问题一般看什么方向"},
        ],
    },
    {
        "id": "codex_test_preview_price",
        "title": "Codex测试-本地预览02 价格+预算",
        "turns": [
            {"content": "我主要是点状斑，这种大概要多少钱"},
            {"content": "预算别太高，有没有新客能先参考的价格"},
        ],
    },
    {
        "id": "codex_test_preview_trust",
        "title": "Codex测试-本地预览03 信任顾虑",
        "turns": [
            {"content": "你们正规吗？会不会到店乱收费"},
            {"content": "我之前被别家坑过，所以比较担心这个"},
        ],
    },
]


def build_history(messages: list[dict[str, Any]]) -> list[str]:
    history: list[str] = []
    for message in messages[-10:]:
        role = "用户" if message["role"] == "user" else "小贝"
        text = message.get("content") or ("[图片]" if message.get("imageUrl") else "")
        history.append(f"{role}: {text}")
    return history


def make_message_id(prefix: str, turn_index: int, item_index: int) -> str:
    return f"{prefix}_{turn_index}_{item_index}_{int(time.time() * 1000)}"


def generate() -> None:
    timestamp_suffix = int(time.time())
    conversations: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []

    for scenario_index, scenario in enumerate(SCENARIOS, start=1):
        scenario_id = f"{scenario['id']}_{timestamp_suffix}"
        messages: list[dict[str, Any]] = []
        turn_reports: list[dict[str, Any]] = []
        created_at = int(time.time() * 1000) - scenario_index * 1000

        for turn_index, turn in enumerate(scenario["turns"], start=1):
            user_message = {
                "id": make_message_id(f"{scenario_id}_u", turn_index, 0),
                "role": "user",
                "content": turn["content"],
                "timestamp": int(time.time() * 1000),
            }
            payload = {
                "content": turn["content"],
                "customer_id": scenario_id,
                "corp_id": "ww916da62a08044243",
                "conversation_history": build_history(messages),
                "user_id": 7294,
                "wechat": "yzm-yibingwen",
                "external_userid": f"codex_preview_{scenario_index:02d}",
            }

            started = time.time()
            response = requests.post(API_URL, json=payload, timeout=180)
            elapsed_ms = int((time.time() - started) * 1000)
            response.raise_for_status()
            data = response.json()
            meta = data.get("meta") or {}
            replies = data.get("reply_messages") or []

            messages.append(user_message)

            for assistant_index, item in enumerate(replies, start=1):
                assistant_message: dict[str, Any] = {
                    "id": make_message_id(f"{scenario_id}_a", turn_index, assistant_index),
                    "role": "assistant",
                    "content": item.get("content", ""),
                    "contentType": item.get("type", "text"),
                    "timestamp": int(time.time() * 1000),
                    "duration": elapsed_ms,
                }
                if assistant_index == 1:
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
                        "raw": meta,
                    }
                messages.append(assistant_message)

            turn_reports.append(
                {
                    "turn": turn_index,
                    "user": turn["content"],
                    "request_id": data.get("request_id", ""),
                    "intent": data.get("intent", ""),
                    "subflow": data.get("subflow", ""),
                    "reply_count": len(replies),
                    "token_usage": meta.get("token_usage", {}),
                }
            )

        conversations.append(
            {
                "id": scenario_id,
                "title": scenario["title"],
                "messages": messages,
                "createdAt": created_at,
                "updatedAt": int(time.time() * 1000),
            }
        )
        report.append(
            {
                "id": scenario_id,
                "title": scenario["title"],
                "turns": turn_reports,
            }
        )

    OUT_PATH.write_text(
        json.dumps({"conversations": conversations}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    REPORT_PATH.write_text(
        json.dumps(
            {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "report": report},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    generate()
