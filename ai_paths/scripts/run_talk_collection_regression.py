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
REPORT_FILE = ROOT / "logs" / "talk_collection_regression_report.json"

SCENARIOS = [
    {
        "title": "话术合集回归-初步咨询到广告价",
        "turns": [
            "我想了解一下祛斑",
            "我看广告上面说199元就有效果，是不是没有其他收费",
            "那有这个券吗",
        ],
    },
    {
        "title": "话术合集回归-门店推荐与地址",
        "turns": [
            "我人在厦门机场附近",
            "那你直接推荐一个近一点的吧",
            "可以，把这家店发给我",
        ],
    },
    {
        "title": "话术合集回归-项目方向别追问",
        "turns": [
            "主要是点状斑，还有点色沉",
            "别一直问我了，你先说我这种先看什么方向",
            "这种大概多少钱",
        ],
    },
    {
        "title": "话术合集回归-案例承接",
        "turns": [
            "客户做完之后的效果我想看一下",
            "我主要还是想看祛斑做完后的变化",
        ],
    },
    {
        "title": "话术合集回归-预约前准备",
        "turns": [
            "到店要带什么，要不要素颜",
            "那流程大概要多久",
        ],
    },
    {
        "title": "话术合集回归-效果保障与收费透明",
        "turns": [
            "效果有保障吗",
            "价格是199吗",
            "是不是没有其他收费",
        ],
    },
    {
        "title": "话术合集回归-预约报名与时间确认",
        "turns": [
            "报名",
            "明天上午来行吗",
            "那你们几点上班",
        ],
    },
    {
        "title": "话术合集回归-做完后的维持顾虑",
        "turns": [
            "做了之后会不会很快又回来",
            "那平时需要注意什么",
        ],
    },
]


def _message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or content.get("handoff_reason") or "")
    return str(content or "")


def _assistant_entries(
    *,
    replies: list[dict[str, Any]],
    turn_index: int,
    now_ms: int,
    elapsed_ms: int,
    result: dict[str, Any],
    status_code: int,
) -> list[dict[str, Any]]:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    entries: list[dict[str, Any]] = []
    for msg_index, item in enumerate(replies, start=1):
        text = _message_text(item)
        if not text:
            continue
        entry: dict[str, Any] = {
            "id": f"assistant-{turn_index}-{msg_index}",
            "role": "assistant",
            "content": text,
            "timestamp": now_ms + turn_index * 60_000 + msg_index * 1_000,
            "duration": elapsed_ms,
            "contentType": item.get("type") or "text",
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
                    "status_code": status_code,
                    "token_usage": meta.get("token_usage"),
                    "model_usage": meta.get("model_usage"),
                },
            }
        entries.append(entry)
    return entries


def run() -> list[dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "ai_paths"))
    from app.main import app

    client = TestClient(app)
    started_at = int(time.time() * 1_000)
    conversation_payloads: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []

    for scenario_index, scenario in enumerate(SCENARIOS, start=1):
        customer_id = f"codex_talk_collection_{started_at}_{scenario_index}"
        history: list[str] = []
        messages: list[dict[str, Any]] = []
        turns_report: list[dict[str, Any]] = []
        base_time = started_at + scenario_index * 3_600_000

        for turn_index, content in enumerate(scenario["turns"], start=1):
            messages.append(
                {
                    "id": f"user-{turn_index}",
                    "role": "user",
                    "content": content,
                    "timestamp": base_time + turn_index * 60_000,
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
            elapsed_ms = int((time.perf_counter() - start) * 1_000)
            result = response.json()
            replies = result.get("reply_messages") if isinstance(result.get("reply_messages"), list) else []
            assistant_entries = _assistant_entries(
                replies=[item for item in replies if isinstance(item, dict)],
                turn_index=turn_index,
                now_ms=base_time,
                elapsed_ms=elapsed_ms,
                result=result,
                status_code=response.status_code,
            )
            messages.extend(assistant_entries)

            history.append(f"用户: {content}")
            turn_reply_texts: list[str] = []
            for item in replies:
                if not isinstance(item, dict):
                    continue
                text = _message_text(item)
                if not text:
                    continue
                turn_reply_texts.append(text)
                history.append(f"助手: {text}")

            turns_report.append(
                {
                    "turn": turn_index,
                    "status": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "request_id": result.get("request_id") or "",
                    "intent": result.get("intent") or "",
                    "subflow": result.get("subflow") or "",
                    "user": content,
                    "reply": turn_reply_texts,
                }
            )

        conversation_payloads.append(
            {
                "id": customer_id,
                "title": scenario["title"],
                "messages": messages,
                "createdAt": base_time,
                "updatedAt": base_time + len(scenario["turns"]) * 60_000,
            }
        )
        report.append({"scenario": scenario["title"], "turns": turns_report})

    append_test_conversations(conversation_payloads, path=FRONTEND_FILE)
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
