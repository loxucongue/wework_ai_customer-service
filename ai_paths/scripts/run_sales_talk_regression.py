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
REPORT_FILE = ROOT / "logs" / "sales_talk_regression_report.json"

SCENARIOS = [
    {
        "title": "话术回归-门店推荐与地址指引",
        "turns": [
            "我在厦门机场附近，想找近一点的门店",
            "那你直接推荐一家方便的吧",
            "把这家地址和停车发我",
        ],
    },
    {
        "title": "话术回归-广告价与收费口径",
        "turns": [
            "我想了解一下祛斑",
            "广告上说199就有效果，是不是没有其他收费",
            "那有这个券吗",
        ],
    },
    {
        "title": "话术回归-效果案例与口语化承接",
        "turns": [
            "主要是点状斑，还有点色沉",
            "别一直问我了，你先说我这种先看什么方向",
            "客户做完之后的效果我想看一下",
        ],
    },
    {
        "title": "话术回归-预约意向承接",
        "turns": [
            "我周六下午想过去看看",
            "我在厦门思明这边",
            "那就按你推荐的门店，下午五点左右有吗",
        ],
    },
]


def _message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or content.get("handoff_reason") or "")
    return str(content or "")


def _assistant_payload_messages(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = result.get("reply_messages")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _append_assistant_messages(
    *,
    messages: list[dict[str, Any]],
    replies: list[dict[str, Any]],
    turn_index: int,
    now_ms: int,
    elapsed_ms: int,
    result: dict[str, Any],
) -> None:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    for msg_index, item in enumerate(replies, start=1):
        text = _message_text(item)
        if not text:
            continue
        entry: dict[str, Any] = {
            "id": f"assistant-{turn_index}-{msg_index}",
            "role": "assistant",
            "content": text,
            "timestamp": now_ms + turn_index * 60_000 + msg_index * 1000,
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
                    "token_usage": meta.get("token_usage"),
                    "model_usage": meta.get("model_usage"),
                },
            }
        messages.append(entry)


def _judge_turn(user_text: str, result: dict[str, Any]) -> dict[str, Any]:
    replies = [_message_text(item) for item in _assistant_payload_messages(result)]
    joined = "\n".join(text for text in replies if text)
    problems: list[str] = []
    strengths: list[str] = []

    if len(replies) > 2:
        problems.append("拆句过多")
    if joined.count("具体") >= 2 or joined.count("方向") >= 3:
        problems.append("解释性词汇偏多")
    if "哪方面" in joined and any(term in user_text for term in ["点状斑", "色沉", "机场附近", "地址", "停车"]):
        problems.append("已知信息足够仍继续泛问")
    if any(term in user_text for term in ["机场附近", "近一点"]) and "推荐" not in joined and "优先" not in joined:
        problems.append("未直接推荐门店")
    if "199" in user_text and "199" not in joined:
        problems.append("广告价问题未承接原数字")
    if "效果" in user_text and "案例" in user_text and not any(term in joined for term in ["案例", "参考", "对比"]):
        problems.append("案例诉求承接不足")
    if "周六下午" in user_text and not any(term in joined for term in ["周六", "下午", "可约", "时间"]):
        problems.append("预约时间问题回答不聚焦")
    if joined and len(joined) <= 120:
        strengths.append("回复较短")
    if any(term in joined for term in ["按你这个情况", "你这个情况", "可以先看", "优先"]) or "推荐" in joined:
        strengths.append("有结论前置")

    return {
        "user": user_text,
        "intent": result.get("intent") or "",
        "subflow": result.get("subflow") or "",
        "reply": replies,
        "problems": problems,
        "strengths": strengths,
    }


def run() -> list[dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "ai_paths"))
    from app.main import app

    client = TestClient(app)
    started_at = int(time.time() * 1000)
    conversation_payloads: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []

    for scenario_index, scenario in enumerate(SCENARIOS, start=1):
        customer_id = f"codex_sales_talk_{started_at}_{scenario_index}"
        history: list[str] = []
        messages: list[dict[str, Any]] = []
        base_time = started_at + scenario_index * 3_600_000
        turns_report: list[dict[str, Any]] = []

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
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            result = response.json()
            replies = _assistant_payload_messages(result)
            _append_assistant_messages(
                messages=messages,
                replies=replies,
                turn_index=turn_index,
                now_ms=base_time,
                elapsed_ms=elapsed_ms,
                result=result,
            )
            history.append(f"用户: {content}")
            for item in replies:
                text = _message_text(item)
                if text:
                    history.append(f"助手: {text}")
            turns_report.append(
                {
                    "turn": turn_index,
                    "status": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "request_id": result.get("request_id") or "",
                    **_judge_turn(content, result),
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
